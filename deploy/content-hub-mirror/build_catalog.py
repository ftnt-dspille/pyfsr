#!/usr/bin/env python3
"""Build the served content-hub tree for the mirror container.

Runs at container start (before nginx) and again whenever content is added via
the admin GUI/CLI. It produces ``$OUTPUT_DIR/content-hub/`` — a merged,
spec-valid catalog + directory tree that nginx serves, following Option B of
``CONTENT_HUB_SELF_HOSTED_REPO_PLAN.md``:

    merged content-hub.json  =  (optional) upstream Fortinet catalog
                             +  our own local entries  (local wins on collisions)

Upstream is obtained one of two ways (in priority order):
  1. ``UPSTREAM_SNAPSHOT`` — a path to a previously-saved ``content-hub.json``.
  2. ``UPSTREAM_HOST`` (+ ``FDN_CERT``/``FDN_KEY``) — crawl the live host over mTLS.
If neither is set, the mirror serves ONLY our local content (Option A).

Local content lives in ``LOCAL_CONTENT_DIR`` as ``*.json`` entry files, and the
matching downloadable artifacts (``{name}-{version}.zip`` / ``.tgz``) in
``ARTIFACTS_DIR`` — they get copied into the served tree so the appliance can
actually download them (not just see them listed).

Importable: ``build_catalog(...)`` returns the merged :class:`ContentCatalog` and
writes the tree; the ``__main__`` path just reads config from the environment.
"""

from __future__ import annotations

import glob
import json
import os
import sys

from pyfsr.content_catalog import ContentCatalog, validate_entry


def load_local_entries(dir_path: str) -> list[dict]:
    """Read every ``*.json`` entry file under ``dir_path`` (object or list each)."""
    entries: list[dict] = []
    for path in sorted(glob.glob(os.path.join(dir_path, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        items = data if isinstance(data, list) else [data]
        for item in items:
            problems = validate_entry(item)
            if problems:
                raise ValueError(f"invalid local entry in {path}: {problems}")
            entries.append(item)
    return entries


def _artifact_map(entries: list[dict], artifacts_dir: str) -> dict[tuple[str, str], str]:
    """Map ``(type, name) -> artifact path`` for entries with a file on disk.

    Looks for ``{name}-{version}.zip`` then ``.tgz`` in ``artifacts_dir``.
    """
    out: dict[tuple[str, str], str] = {}
    if not artifacts_dir or not os.path.isdir(artifacts_dir):
        return out
    for e in entries:
        name, ver, etype = e.get("name"), e.get("version"), e.get("type")
        for ext in (".zip", ".tgz"):
            cand = os.path.join(artifacts_dir, f"{name}-{ver}{ext}")
            if os.path.isfile(cand):
                out[(str(etype), str(name))] = cand
                break
    return out


def build_catalog(
    *,
    out_root: str,
    local_dir: str,
    artifacts_dir: str = "",
    upstream_snapshot: str = "",
    upstream_host: str = "",
    verify: bool = True,
    log=print,
) -> ContentCatalog:
    """Merge upstream + local content, copy artifacts, write the served tree.

    Returns the merged catalog. Raises ``ValueError`` if any entry (local or
    merged) is invalid — nothing is written in that case.
    """
    if upstream_snapshot:
        log(f"[build] upstream from snapshot: {upstream_snapshot}")
        cat = ContentCatalog.from_file(upstream_snapshot)
    elif upstream_host:
        cert_path = os.environ.get("FDN_CERT", "").strip()
        key_path = os.environ.get("FDN_KEY", "").strip()
        cert = (cert_path, key_path) if cert_path and key_path else (cert_path or None)
        log(f"[build] crawling upstream: {upstream_host} (cert={'yes' if cert else 'no'})")
        cat = ContentCatalog.from_url(upstream_host, verify=verify, cert=cert)
    else:
        log("[build] no upstream configured -> local content only (Option A)")
        cat = ContentCatalog()

    upstream_n = len(cat)
    local = load_local_entries(local_dir)
    cat.merge(local)
    log(f"[build] upstream={upstream_n} + local={len(local)} -> merged={len(cat)} {cat.counts()}")

    artifacts = _artifact_map(cat.to_list(), artifacts_dir)
    if artifacts:
        log(f"[build] wiring {len(artifacts)} downloadable artifact(s)")

    manifest = cat.write_tree(out_root, artifacts=artifacts)
    log(f"[build] wrote {manifest}")
    return cat


def main() -> int:
    try:
        build_catalog(
            out_root=os.environ.get("OUTPUT_DIR", "/srv"),
            local_dir=os.environ.get("LOCAL_CONTENT_DIR", "/local-content"),
            artifacts_dir=os.environ.get("ARTIFACTS_DIR", "/artifacts"),
            upstream_snapshot=os.environ.get("UPSTREAM_SNAPSHOT", "").strip(),
            upstream_host=os.environ.get("UPSTREAM_HOST", "").strip(),
            verify=os.environ.get("TLS_VERIFY", "1") != "0",
        )
    except ValueError as exc:
        sys.exit(f"[build] {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
