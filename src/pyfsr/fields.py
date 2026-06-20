"""Field & relationship knowledge base for query field paths.

FortiSOAR query ``field`` paths can **dot-walk** named relationships, e.g.
``severity.itemValue`` (the picklist behind ``severity``) or
``alerts.source.itemValue`` (two hops). On the wire a dot (``.``) and a double
underscore (``__``) are interchangeable in a path — ``severity.itemValue`` and
``severity__itemValue`` resolve identically — and adding a relationship hop to
the path changes which rows match (an inner join), so it can change result
counts versus filtering on the base module alone.

This module ships a compact map of every module's attributes and relationship
targets (generated from the appliance module schema) so a field path can be
*softly* validated: the first segment is checked against the module's known
fields, and relationship hops are followed where the target module is known.
Validation is advisory — unknown modules and custom fields pass — because the
schema is per-appliance and customizable.
"""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

_RESOURCE = "query_fields.json.gz"

#: Framework fields present on every module but absent from per-module schema
#: attributes (the JSON-LD envelope + audit/soft-delete columns).
_SYSTEM_FIELDS = frozenset(
    {
        "uuid",
        "id",
        "@id",
        "@type",
        "createDate",
        "modifyDate",
        "lastModifyDate",
        "deletedAt",
        "recordTags",
        "importedBy",
    }
)

#: System relationship fields → their target module (so audit dot-walks like
#: ``createUser.name`` or ``owners.name`` validate).
_SYSTEM_RELATIONSHIPS = {
    "createUser": "people",
    "modifyUser": "people",
    "owners": "teams",
}


@lru_cache(maxsize=1)
def _kb() -> dict[str, Any]:
    """Load and cache the shipped module → {fields, relationships} map."""
    try:
        blob = (files("pyfsr.resources") / _RESOURCE).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError):  # pragma: no cover - packaging guard
        return {}
    return json.loads(gzip.decompress(blob))


def known_modules() -> list[str]:
    """All modules present in the field knowledge base."""
    return sorted(_kb())


def module_fields(module: str) -> list[str]:
    """Known attribute names for ``module`` (empty if the module is unknown)."""
    return list(_kb().get(module, {}).get("fields", []))


def module_relationships(module: str) -> dict[str, str]:
    """Map of relationship field name → target module for ``module``."""
    return dict(_kb().get(module, {}).get("relationships", {}))


def normalize_field_path(path: str) -> str:
    """Canonicalize a field path to dot form (``__`` → ``.``), trimmed."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("field path must be a non-empty string")
    return path.strip().replace("__", ".")


def split_field_path(path: str) -> list[str]:
    """Split a (normalized) field path into its segments."""
    segs = [s for s in normalize_field_path(path).split(".")]
    if any(not s for s in segs):
        raise ValueError(f"field path {path!r} has an empty segment")
    return segs


def validate_field_path(module: str, path: str) -> None:
    """Advisory validation of a query ``field`` path against the KB.

    Raises ``ValueError`` only when the module IS known and a segment is
    provably wrong (an unknown base field, or a dot-walk through a non-existent
    relationship). Unknown modules/fields pass silently — the schema is
    per-appliance and extensible.
    """
    segs = split_field_path(path)
    kb = _kb()
    current = module
    for i, seg in enumerate(segs):
        entry = kb.get(current)
        if entry is None:
            return  # unknown module: can't validate further, accept
        is_last = i == len(segs) - 1
        rels = {**_SYSTEM_RELATIONSHIPS, **entry.get("relationships", {})}
        fields = set(entry.get("fields", [])) | _SYSTEM_FIELDS
        if is_last:
            if seg not in fields and seg not in rels:
                raise ValueError(
                    f"{current!r} has no field {seg!r} "
                    f"(in path {path!r}); did you mean one of: "
                    f"{', '.join(sorted(fields)[:8])}…"
                )
            return
        # intermediate segment must be a relationship we can hop through
        if seg in rels:
            current = rels[seg]
        elif seg in fields:
            raise ValueError(
                f"{current!r} field {seg!r} is a scalar, not a relationship; cannot dot-walk into it in path {path!r}"
            )
        else:
            raise ValueError(f"{current!r} has no relationship {seg!r} in path {path!r}")
