"""Admin backend for the Content Hub mirror — GUI + JSON API to manage content.

One Flask app, two faces:
  * ``GET  /``                      — HTML console (list + add form + upload)
  * ``GET  /api/content``           — list local entries (JSON)
  * ``POST /api/content``           — add an entry: either an uploaded artifact
                                      (multipart ``artifact``) or manual JSON fields
  * ``DELETE /api/content/<type>/<name>`` — remove an entry (+ its artifact)
  * ``POST /api/rebuild``           — re-merge + rewrite the served tree

Every mutation writes to ``LOCAL_CONTENT_DIR`` (entry JSON) and, for uploads,
``ARTIFACTS_DIR`` (the downloadable file), then calls ``build_catalog`` so nginx
serves the change immediately (shared filesystem, no reload needed).

Optional bearer auth: set ``ADMIN_TOKEN`` and send ``Authorization: Bearer <t>``
(the CLI does this automatically). With no token set, the API is open — only do
that on a trusted network.
"""

from __future__ import annotations

import os
import re
import sys

from flask import Flask, jsonify, request

# build_catalog.py sits one dir up; make it importable before the local import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from build_catalog import build_catalog, load_local_entries  # noqa: E402
from connector_publish import publish_connector  # noqa: E402

from pyfsr.content_catalog import entry_from_artifact, validate_entry  # noqa: E402

app = Flask(__name__)

LOCAL_DIR = os.environ.get("LOCAL_CONTENT_DIR", "/local-content")
ARTIFACTS_DIR = os.environ.get("ARTIFACTS_DIR", "/artifacts")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/srv")
CONNECTORS_LOCAL_DIR = os.environ.get("CONNECTORS_LOCAL_DIR", "/connectors-local")
CONTENT_HUB_DIR = os.environ.get("CONTENT_HUB_DIR", "/srv/content-hub")
CONNECTORS_CINFO = os.environ.get("CONNECTORS_CINFO", "/srv/local-cinfo/connectors-all.json")
CONNECTOR_SPEC_IN = os.environ.get(
    "CONNECTOR_SPEC_IN",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "connector-build", "cyops-connector.spec.in"
    ),
)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(name: str) -> str:
    """Sanitize a name for use in a filename (no path traversal)."""
    return _SAFE.sub("_", name or "")


def _authed() -> bool:
    if not ADMIN_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and auth[7:] == ADMIN_TOKEN


def _rebuild():
    """Re-merge + rewrite the served tree with the current config."""
    return build_catalog(
        out_root=OUTPUT_DIR,
        local_dir=LOCAL_DIR,
        artifacts_dir=ARTIFACTS_DIR,
        upstream_snapshot=os.environ.get("UPSTREAM_SNAPSHOT", "").strip(),
        upstream_host=os.environ.get("UPSTREAM_HOST", "").strip(),
        verify=os.environ.get("TLS_VERIFY", "1") != "0",
    )


def _entry_file(entry: dict) -> str:
    return os.path.join(LOCAL_DIR, f"{_safe(entry['type'])}__{_safe(entry['name'])}.json")


@app.before_request
def _guard():
    if request.path.startswith("/api/") and not _authed():
        return jsonify(error="unauthorized"), 401


@app.get("/api/content")
def list_content():
    entries = load_local_entries(LOCAL_DIR)
    return jsonify(count=len(entries), entries=entries)


@app.post("/api/content")
def add_content():
    import json

    os.makedirs(LOCAL_DIR, exist_ok=True)
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    # Path 1: an uploaded artifact -> derive the entry + stage the file.
    if "artifact" in request.files and request.files["artifact"].filename:
        up = request.files["artifact"]
        fname = _safe(os.path.basename(up.filename))
        art_path = os.path.join(ARTIFACTS_DIR, fname)
        up.save(art_path)
        overrides = {}
        if request.form.get("type"):
            overrides["type"] = request.form["type"]
        if request.form.get("buildNumber"):
            overrides["buildNumber"] = int(request.form["buildNumber"])
        try:
            entry = entry_from_artifact(art_path, **overrides)
        except ValueError as exc:
            os.remove(art_path)
            return jsonify(error=str(exc)), 400
        # Validate before the derived name/version touch any path (they become a
        # filename below and path segments in the served tree).
        problems = validate_entry(entry)
        if problems:
            os.remove(art_path)
            return jsonify(error="artifact produced an invalid entry", problems=problems), 400
        # rename the staged artifact to the {name}-{version} convention the tree wants
        want = os.path.join(ARTIFACTS_DIR, f"{entry['name']}-{entry['version']}{os.path.splitext(fname)[1]}")
        if want != art_path:
            os.replace(art_path, want)

    # Path 2: manual JSON body -> validate the entry as given.
    else:
        entry = request.get_json(silent=True) or {}
        problems = validate_entry(entry)
        if problems:
            return jsonify(error="invalid entry", problems=problems), 400

    with open(_entry_file(entry), "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2)

    try:
        cat = _rebuild()
    except ValueError as exc:
        return jsonify(error=f"rebuild failed: {exc}"), 400
    return jsonify(
        added={"type": entry["type"], "name": entry["name"], "version": entry.get("version")}, total=len(cat)
    ), 201


@app.post("/api/connector")
def add_connector():
    """Publish a connector from an uploaded source tgz — the mirror builds the RPM.

    Multipart form: ``tgz`` = the connector tarball (top-level ``<name>/`` with
    ``info.json``); optional ``release`` (RPM release number, bump to force a
    re-pull of the same version). Builds the RPM, drops it in the local yum repo
    (+ ``createrepo``), merges ``connectors-all.json``, and stages the
    Content-Hub metadata zip — everything ``install(name, version)`` needs.
    """
    up = request.files.get("tgz")
    if not up or not up.filename:
        return jsonify(error="missing 'tgz' file upload"), 400
    release = (request.form.get("release") or "1").strip()
    os.makedirs("/tmp/uploads", exist_ok=True)
    tmp_tgz = os.path.join("/tmp/uploads", _safe(os.path.basename(up.filename)))
    up.save(tmp_tgz)
    try:
        summary = publish_connector(
            tmp_tgz,
            connectors_local_dir=CONNECTORS_LOCAL_DIR,
            content_hub_dir=CONTENT_HUB_DIR,
            cinfo_path=CONNECTORS_CINFO,
            spec_in=CONNECTOR_SPEC_IN,
            release=release,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:  # rpmbuild/createrepo failures surface as 500 w/ detail
        return jsonify(error=f"connector publish failed: {exc}"), 500
    finally:
        if os.path.isfile(tmp_tgz):
            os.remove(tmp_tgz)
    # also add a catalog entry so the connector shows in content-hub.json
    entry = {
        "type": "connector",
        "name": summary["name"],
        "version": summary["version"],
        "buildNumber": summary["buildNumber"],
    }
    for k in ("label", "publisher", "category"):
        if summary["info"].get(k):
            entry[k] = summary["info"][k]
    if not validate_entry(entry):
        import json as _json

        os.makedirs(LOCAL_DIR, exist_ok=True)
        with open(_entry_file(entry), "w", encoding="utf-8") as fh:
            _json.dump(entry, fh, indent=2)
        try:
            _rebuild()
        except ValueError:
            pass
    return jsonify(published=summary), 201


@app.delete("/api/content/<etype>/<name>")
def remove_content(etype, name):
    path = os.path.join(LOCAL_DIR, f"{_safe(etype)}__{_safe(name)}.json")
    if not os.path.isfile(path):
        return jsonify(error="not found"), 404
    # drop matching artifacts too
    import json

    with open(path, encoding="utf-8") as fh:
        entry = json.load(fh)
    os.remove(path)
    for ext in (".zip", ".tgz"):
        art = os.path.join(ARTIFACTS_DIR, f"{entry.get('name')}-{entry.get('version')}{ext}")
        if os.path.isfile(art):
            os.remove(art)
    cat = _rebuild()
    return jsonify(removed={"type": etype, "name": name}, total=len(cat))


@app.post("/api/rebuild")
def rebuild():
    cat = _rebuild()
    return jsonify(total=len(cat), counts=cat.counts())


@app.get("/healthz")
def healthz():
    return "ok\n", 200


# ---- HTML console ----------------------------------------------------------

_CONSOLE_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console.html")


@app.get("/")
def index():
    with open(_CONSOLE_HTML, encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    if not ADMIN_TOKEN:
        print(
            "[admin] WARNING: ADMIN_TOKEN not set — the add/remove API is OPEN. "
            "Set ADMIN_TOKEN (and bind to a trusted network) before exposing this off localhost.",
            flush=True,
        )
    app.run(host="0.0.0.0", port=int(os.environ.get("ADMIN_PORT", "9000")))
