"""Foundational playbook library index -- the worked-examples retrieval layer.

A curated library of whole, compiling playbooks an agent retrieves by intent and
adapts. The playbooks live under ``examples/playbooks/library/`` (repo-only; not
packaged); this module indexes them into retrieval facets (stage, goal, step
types, connectors, jinja filters, triggers, compile status).

Unlike :mod:`pyfsr.playbook_catalog` (which serves the *compiler's* step-type
reference data and belongs downstream in ``fsr_playbooks``), this index is
pyfsr-repo-specific: it walks the in-repo example corpus and compiles each entry
through the :mod:`pyfsr.authoring` bridge. The ``pyfsr playbook examples`` CLI
prints it; :func:`library_manifest` serializes the NL->playbook retrieval payload.
"""

from __future__ import annotations

import json as _json
import re as _re
import shutil as _shutil
import sqlite3 as _sqlite3
import tempfile as _tempfile
from pathlib import Path as _Path

from pydantic import BaseModel

_LIBRARY_DEFAULT = _Path(__file__).resolve().parents[2] / "examples" / "playbooks" / "library"
_FIXTURE_CONNECTORS_DEFAULT = _LIBRARY_DEFAULT / "fixture_connectors.json"


def _build_fixture_catalog_db(fixture_spec: str | _Path | None = None) -> _Path | None:
    """Build a throwaway reference catalog seeded with fake connectors/operations.

    The packaged ``fsr_playbooks`` slim catalog ships with **0 connectors** by
    design (per-install data, not shippable). Every library playbook that calls
    a real connector (``openai``, ``fortigate-firewall``, ...) therefore fails
    offline compilation with ``unknown_connector`` -- not a real defect, just
    catalog data the library corpus doesn't have without a live box.

    This copies the packaged slim catalog to a temp file and inserts
    ``connector``/``operation`` rows from ``fixture_connectors.json`` (a
    ``{connector: [operation, ...]}`` map, auto-derived from the connector
    steps actually used across the library -- see that file's own comment).
    Params are intentionally NOT seeded: an unmodeled param schema is only a
    ``unknown_param`` *warning* (params pass through unvalidated), so
    connector+operation identity is all offline compilation needs to go green.

    Returns None if the compiler extra isn't installed or the packaged
    catalog can't be found -- callers should fall back to the default
    (uncatalogued, ``cold*``) compile path in that case.
    """
    try:
        from fsr_playbooks._db import default_db_path
    except ImportError:
        return None

    spec_path = _Path(fixture_spec) if fixture_spec else _FIXTURE_CONNECTORS_DEFAULT
    try:
        spec = _json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    packaged = default_db_path()
    if not _Path(packaged).is_file():
        return None

    fd, tmp_name = _tempfile.mkstemp(prefix="pyfsr_library_fixture_", suffix=".db")
    import os as _os

    _os.close(fd)
    tmp_path = _Path(tmp_name)
    _shutil.copyfile(packaged, tmp_path)

    con = _sqlite3.connect(tmp_path)
    try:
        for connector, operations in spec.items():
            con.execute(
                "INSERT OR IGNORE INTO connectors(name, version, label, source) VALUES (?, '1.0.0', ?, 'fixture')",
                (connector, connector),
            )
            for op in operations:
                con.execute(
                    "INSERT OR IGNORE INTO operations(connector_name, op_name, title) VALUES (?, ?, ?)",
                    (connector, op, op),
                )
        con.commit()
    finally:
        con.close()
    return tmp_path


class LibraryEntry(BaseModel):
    """One playbook in the foundational library, with retrieval facets."""

    slug: str
    stage: str
    path: str  # repo-relative, e.g. examples/playbooks/library/triggers/<slug>.yaml
    name: str  # the playbook name (first playbook in the file)
    goal: str  # from front-matter, else the playbook description
    step_types: list[str]  # distinct friendly types used
    connectors: list[str]  # distinct connector names used (may be empty)
    jinja_filters: list[str]  # distinct | filters used
    triggers: list[str]  # trigger step types (start/start_on_create/.../api_endpoint)
    compiles_ok: bool  # .ok from compile_playbook_yaml
    source: str  # "authored" or "tutorial-corpus:<name>" (from front-matter)
    summary: str  # the goal/summary line


def _parse_front_matter(text: str) -> dict[str, str]:
    """Pull the leading ``# key: value`` comment block into a dict."""
    fm: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        m = _re.match(r"#\s*([A-Za-z_ ]+):\s*(.*)", line)
        if m:
            fm[m.group(1).strip().lower().replace(" ", "_")] = m.group(2).strip()
    return fm


def _facets(text: str) -> tuple[list[str], list[str], list[str], list[str], str]:
    """Extract step_types / connectors / jinja_filters / triggers / first name from YAML."""
    try:
        import yaml

        doc = yaml.safe_load(text)
    except Exception:
        return [], [], [], [], ""
    playbooks = (doc or {}).get("playbooks", []) if isinstance(doc, dict) else []
    name = playbooks[0].get("name", "") if playbooks else ""
    step_types: list[str] = []
    connectors: list[str] = []
    triggers: list[str] = []
    for pb in playbooks:
        for s in pb.get("steps", []) or []:
            t = s.get("type")
            if t and t not in step_types:
                step_types.append(t)
            if t in ("start", "start_on_create", "start_on_update", "start_on_delete", "api_endpoint"):
                if t not in triggers:
                    triggers.append(t)
            args = s.get("arguments", {}) or {}
            c = args.get("connector") if isinstance(args, dict) else None
            if c and c not in connectors:
                connectors.append(c)
    # jinja filters: distinct | name tokens in the raw text
    filters = sorted(set(_re.findall(r"\|\s*([a-z_]+)", text)))
    return step_types, connectors, filters, triggers, name


def list_library(library_dir: str | _Path | None = None, *, use_fixture_catalog: bool = True) -> list[LibraryEntry]:
    """Index every playbook in the foundational library.

    Walks ``library_dir`` (default: the repo's ``examples/playbooks/library/``),
    compiles each ``.yaml``, and returns one :class:`LibraryEntry` per file with the
    facets an agent retrieves on: stage, goal, step types, connectors, jinja filters,
    triggers, and compile status. The manifest generator
    (:func:`library_manifest`) serializes this; the ``pyfsr playbook examples`` CLI
    prints it.

    With ``use_fixture_catalog=True`` (default), compilation runs against a
    throwaway catalog seeded from ``fixture_connectors.json`` (see
    :func:`_build_fixture_catalog_db`) so playbooks using real connectors
    (``fortigate-firewall``, ``openai``, ...) compile clean offline instead of
    failing ``unknown_connector`` against the packaged catalog's 0 connectors.
    Pass ``False`` to compile against the packaged slim catalog as-is (the
    honest "what ships in the wheel" count).
    """
    from .authoring import compile_playbook_yaml

    root = _Path(library_dir) if library_dir else _LIBRARY_DEFAULT
    if not root.is_dir():
        return []
    repo_root = root.parents[2]
    fixture_db = _build_fixture_catalog_db() if use_fixture_catalog else None
    entries: list[LibraryEntry] = []
    try:
        for f in sorted(root.rglob("*.yaml")):
            text = f.read_text(encoding="utf-8")
            fm = _parse_front_matter(text)
            step_types, connectors, filters, triggers, name = _facets(text)
            try:
                res = compile_playbook_yaml(text, db_path=fixture_db)
                ok = res.ok
            except Exception:
                ok = False
            stage = f.relative_to(root).parts[0] if len(f.relative_to(root).parts) > 1 else "misc"
            slug = f.stem
            src = fm.get("source", "authored")
            entries.append(
                LibraryEntry(
                    slug=slug,
                    stage=stage,
                    path=str(f.relative_to(repo_root)),
                    name=name or slug,
                    goal=fm.get("goal", "") or slug,
                    step_types=step_types,
                    connectors=connectors,
                    jinja_filters=filters,
                    triggers=triggers,
                    compiles_ok=ok,
                    source=src,
                    summary=fm.get("goal", "") or fm.get("summary", "") or slug,
                )
            )
    finally:
        if fixture_db is not None:
            fixture_db.unlink(missing_ok=True)
    return entries


def library_manifest(library_dir: str | _Path | None = None, *, use_fixture_catalog: bool = True) -> dict:
    """Build the retrieval manifest for the library (the NL->playbook payload).

    Returns a JSON-serializable dict: ``{library_dir, count, playbooks: [<entry>]}``.
    Each entry carries the intent + facets an agent uses to find the closest worked
    example and adapt it. Generate it with ``pyfsr playbook examples --manifest``.
    """
    entries = list_library(library_dir, use_fixture_catalog=use_fixture_catalog)
    root = _Path(library_dir) if library_dir else _LIBRARY_DEFAULT
    return {
        "library_dir": str(root),
        "count": len(entries),
        # model_dump() gives every field; subset to drop `summary` (the manifest
        # contract has never exposed it -- pre-pydantic the dict was hand-rolled
        # field-by-field and simply omitted it).
        "playbooks": [{k: v for k, v in e.model_dump().items() if k != "summary"} for e in entries],
    }


def library_show(slug: str, library_dir: str | _Path | None = None) -> LibraryEntry | None:
    """Find one library playbook by slug (exact match). Returns None if absent."""
    for e in list_library(library_dir):
        if e.slug == slug:
            return e
    return None
