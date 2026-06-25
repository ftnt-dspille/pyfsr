"""Harvest a draft archetype from a real FortiSOAR solution pack.

A solution pack (whether the git source tree under
``corpus_builder/repos/fortisoar/solution-pack-*`` or the ``.zip`` returned by
:meth:`pyfsr.api.solution_packs.SolutionPackAPI.export_pack`) is a directory of JSON:
``info.json`` (pack metadata), ``modules/<mod>/mmd.json`` (module field/relationship/picklist
schema), ``playbooks/<collection>/*.json`` (playbook step graphs), and ``picklists/*.json``.

The harvester turns that into a draft :class:`~pyfsr.archetypes.record.Archetype` -- a honest
extraction of the module fields, the connector/operation pairs the playbooks use, and a step
skeleton per playbook. It does **not** parameterize (no ``{{param}}`` slots), assign connector
*roles*, or write a ``when_to_use`` -- that curation is step 3. Call ``store.put(draft)`` to
persist a draft for later curation.

Pure stdlib (``zipfile`` / ``json`` / ``pathlib``); the only network I/O is the optional
``export_pack`` call inside :func:`harvest_archetype_from_pack`.

Example::

    from pyfsr.archetypes import harvest_from_dir, ArchetypeStore

    draft = harvest_from_dir("path/to/solution-pack-servicenow-...", name="snow-sir-draft")
    ArchetypeStore().put(draft)
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .record import (
    Archetype,
    ConnectorUse,
    ModuleField,
    PlaybookSkeleton,
    StepSkeleton,
)

# Playbook-tree JSON files that are not playbooks themselves.
_NON_PLAYBOOK_FILES = {"collection.metadata.json", "globalVariables.json", "tags.json"}


def _now_iso() -> str:
    """UTC now as an ISO-8601 string with a ``Z`` suffix (sortable, unambiguous)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _uuid_tail(iri: Any) -> str | None:
    """Return the trailing segment of an ``/api/3/.../<uuid>`` IRI, or ``None``."""
    if not iri or not isinstance(iri, str):
        return None
    return iri.rstrip("/").split("/")[-1] or None


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _find_info(root: Path) -> Path | None:
    direct = root / "info.json"
    if direct.exists():
        return direct
    hits = sorted(root.rglob("info.json"))
    return hits[0] if hits else None


def _find_mmds(root: Path) -> list[Path]:
    return sorted(root.rglob("mmd.json"))


def _find_playbooks(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*.json")):
        if "playbooks" not in p.parts:
            continue
        if p.name in _NON_PLAYBOOK_FILES:
            continue
        out.append(p)
    return out


def _picklist_name(attr: dict[str, Any]) -> str | None:
    """Resolve a picklist field's list name from its ``dataSource.query.filters``.

    A picklist attribute carries ``dataSource.model == "picklists"`` and a filter on
    ``listName__name`` whose ``value`` is the list name (e.g. ``"IncidentStatus"``).
    """
    ds = attr.get("dataSource") or {}
    for f in (ds.get("query") or {}).get("filters") or []:
        if f.get("field") == "listName__name" and f.get("value"):
            return str(f["value"])
    return None


def _field_from_attr(module: str, attr: dict[str, Any]) -> ModuleField:
    type_ = attr.get("type", "")
    ds = attr.get("dataSource") or {}
    model = ds.get("model")
    picklist: str | None = None
    relationship: str | None = None
    if type_ == "picklists":
        picklist = _picklist_name(attr)
    elif model and model != "picklists":
        relationship = model
    return ModuleField(
        module=module,
        name=attr.get("name", ""),
        type=type_,
        form_type=attr.get("formType"),
        required=bool((attr.get("validation") or {}).get("required", False)),
        display_name=(attr.get("descriptions") or {}).get("singular"),
        picklist=picklist,
        relationship=relationship,
    )


def _extract_module_fields(root: Path) -> list[ModuleField]:
    fields: list[ModuleField] = []
    for mmd_path in _find_mmds(root):
        mmd = _read_json(mmd_path)
        if not isinstance(mmd, dict):
            continue
        module = str(mmd.get("module") or mmd.get("type") or mmd_path.parent.name)
        for attr in mmd.get("attributes") or []:
            if isinstance(attr, dict):
                fields.append(_field_from_attr(module, attr))
    return fields


def _skeleton_from_playbook(pb: dict[str, Any]) -> tuple[PlaybookSkeleton, list[ConnectorUse]]:
    steps_out: list[StepSkeleton] = []
    uses: list[ConnectorUse] = []
    for step in pb.get("steps") or []:
        if not isinstance(step, dict):
            continue
        args = step.get("arguments") or {}
        connector = args.get("connector")
        operation = args.get("operation")
        steps_out.append(
            StepSkeleton(
                name=step.get("name", ""),
                step_type=_uuid_tail(step.get("stepType")),
                connector=connector,
                operation=operation,
            )
        )
        if connector and operation:
            uses.append(
                ConnectorUse(
                    connector=str(connector),
                    operation=str(operation),
                    step_name=step.get("name", ""),
                )
            )
    return (
        PlaybookSkeleton(
            name=pb.get("name", ""),
            description=pb.get("description"),
            steps=steps_out,
        ),
        uses,
    )


def _dedupe_manifest(uses: list[ConnectorUse]) -> list[ConnectorUse]:
    """Dedup by ``(connector, operation)``, keeping the first step seen for each pair."""
    seen: set[tuple[str, str]] = set()
    out: list[ConnectorUse] = []
    for u in uses:
        key = (u.connector, u.operation)
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


def harvest_from_dir(pack_dir: str | Path, *, name: str | None = None) -> Archetype:
    """Harvest a draft archetype from an unpacked solution-pack directory.

    Works on both the git source tree and an unpacked export ``.zip`` (discovery uses ``rglob``,
    so a top-level prefix directory in the export is tolerated). Parses ``info.json``,
    ``modules/*/mmd.json``, and ``playbooks/**/*.json`` into a draft :class:`~pyfsr.archetypes.record.Archetype`.

    Args:
        pack_dir: path to the unpacked pack.
        name: the archetype name (key). Defaults to the pack's ``info.json`` ``name``, falling
            back to the directory name.

    Returns:
        A draft :class:`~pyfsr.archetypes.record.Archetype` (``when_to_use`` empty, ``parameters`` empty).
    """
    root = Path(pack_dir)
    info_path = _find_info(root)
    info = _read_json(info_path) if info_path else {}
    if not isinstance(info, dict):
        info = {}

    module_schema = _extract_module_fields(root)

    skeletons: list[PlaybookSkeleton] = []
    manifest: list[ConnectorUse] = []
    for pb_path in _find_playbooks(root):
        pb = _read_json(pb_path)
        if not isinstance(pb, dict):
            continue
        skeleton, uses = _skeleton_from_playbook(pb)
        skeletons.append(skeleton)
        manifest.extend(uses)

    pack_name = info.get("name") or root.name
    return Archetype(
        name=name or pack_name,
        when_to_use="",
        description=str(info.get("description", "")),
        module_schema=module_schema,
        connector_manifest=_dedupe_manifest(manifest),
        playbook_skeletons=skeletons,
        parameters=[],
        source={
            "pack_name": pack_name,
            "pack_version": info.get("version"),
            "pack_label": info.get("label"),
            "harvested_at": _now_iso(),
        },
    )


def harvest_from_zip(zip_path: str | Path, *, name: str | None = None) -> Archetype:
    """Harvest a draft archetype from a solution-pack export ``.zip``.

    Extracts the archive to a temporary directory and delegates to :func:`harvest_from_dir`.
    Use this with the path returned by
    :meth:`pyfsr.api.solution_packs.SolutionPackAPI.export_pack`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        return harvest_from_dir(tmp, name=name)


def harvest_archetype_from_pack(client: Any, pack_identifier: str, archetype_name: str) -> Archetype:
    """Harvest a draft archetype from a live appliance's solution pack.

    Wraps :meth:`pyfsr.api.solution_packs.SolutionPackAPI.export_pack` (which finds the
    installed pack, triggers the export, and downloads the ``.zip``) and parses it with
    :func:`harvest_from_zip`. Returns the draft named ``archetype_name`` -- curate it, then
    ``ArchetypeStore().put(draft)`` to persist.
    """
    zip_path = client.solution_packs.export_pack(pack_identifier)
    return harvest_from_zip(zip_path, name=archetype_name)
