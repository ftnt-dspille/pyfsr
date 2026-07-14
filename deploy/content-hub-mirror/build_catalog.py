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
    # Map upstream entries by (type, name) so we can warn when a local entry
    # shadows a Fortinet one but would lose the sync's "is this newer?" test.
    upstream_entries = cat.to_list()
    upstream_by_key = {(str(e.get("type", "")), str(e.get("name", ""))): e for e in upstream_entries}

    def _categories(entry):
        c = entry.get("category")
        if isinstance(c, str):
            return [c] if c.strip() else []
        return [x for x in (c or []) if isinstance(x, str) and x.strip()]

    # The appliance sync rejects an entry outright (FSR_CH_0000001) if its category
    # isn't in the "Solution Pack Category" picklist. Every category the upstream
    # catalog already uses IS valid by definition; fall back to the shipped set when
    # there is no upstream to learn from. This is a warning only — never fatal.
    from pyfsr.content_catalog import SOLUTION_PACK_CATEGORIES

    known_categories = {c for e in upstream_entries for c in _categories(e)}
    known_categories |= set(SOLUTION_PACK_CATEGORIES)

    local = load_local_entries(local_dir)
    for e in local:
        for c in _categories(e):
            if c not in known_categories:
                log(
                    f"[build] WARNING: local entry {e.get('type')}/{e.get('name')} "
                    f"category {c!r} is not a known 'Solution Pack Category'; the "
                    f"appliance sync will REJECT this entry. Use a category the "
                    f"upstream catalog uses, or leave it empty."
                )
        key = (str(e.get("type", "")), str(e.get("name", "")))
        up = upstream_by_key.get(key)
        if not up:
            continue  # brand-new (type, name) — always inserts
        # The appliance sync only overwrites an existing record when the override's
        # publishedDate is STRICTLY greater than the synced one (a --force sync
        # bypasses this, but scheduled syncs do not). buildNumber/version do NOT
        # gate this; publishedDate does. cat.merge(local) below auto-bumps an
        # override's publishedDate past Fortinet's, so scheduled syncs apply it —
        # just note when that bump is happening for visibility.
        ours = e.get("publishedDate") or 0
        theirs = up.get("publishedDate") or 0
        if ours <= theirs:
            log(
                f"[build] note: local override {key[0]}/{key[1]} publishedDate "
                f"{ours} <= Fortinet's {theirs}; auto-bumping to {theirs + 1} so "
                f"scheduled syncs apply your version."
            )
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
