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

from pyfsr.content_catalog import entry_from_artifact, validate_entry  # noqa: E402

app = Flask(__name__)

LOCAL_DIR = os.environ.get("LOCAL_CONTENT_DIR", "/local-content")
ARTIFACTS_DIR = os.environ.get("ARTIFACTS_DIR", "/artifacts")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/srv")
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
