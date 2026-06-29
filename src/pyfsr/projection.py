"""Record projection / summarization for token-efficient agent reads.

FortiSOAR records hydrate into large, deeply-nested dicts (expanded picklists,
relationship objects, audit metadata). Handing one straight to an LLM burns
context fast. These helpers trim a record down to what an agent actually needs:

- :func:`to_jsonable` — normalize a model / page / mixed structure to plain JSON.
- :func:`project_record` — keep an explicit ``fields`` allow-list, or a compact
  ``summary`` of the identity + triage fields, collapsing expanded picklist and
  relationship objects to their readable value.

This is the generic, dependency-light port of the fsrpb ``get_record``
projection (which trimmed ~96% of the payload). It is opt-in everywhere:
``RecordSet`` reads return full typed models unless you pass ``fields=`` /
``summary=``, and the tool registry applies it on the agent's behalf.
"""

from __future__ import annotations

from typing import Any

from .models import ApiResult, BaseRecord
from .pagination import HydraPage

#: Fields kept by ``summary=True`` when present on a record — identity plus the
#: triage fields a human/agent scans first. Order here is the output order.
SUMMARY_FIELDS: tuple[str, ...] = (
    "@id",
    "uuid",
    "name",
    "label",
    "title",
    "displayName",
    "status",
    "severity",
    "state",
    "phase",
    "type",
    "description",
    "assignedTo",
    "owner",
    "source",
    "createDate",
    "modifyDate",
)

#: Reference fields that always survive projection so a result stays addressable.
_REF_FIELDS: tuple[str, ...] = ("@id", "uuid")


def to_jsonable(obj: Any) -> Any:
    """Recursively coerce models / pages into plain JSON-serializable values.

    ``BaseRecord`` → its aliased dict (``@id``/``@type`` form), ``HydraPage`` →
    ``{members, total, page, has_next}``, lists/dicts are walked; everything else
    is returned as-is.
    """
    if isinstance(obj, BaseRecord):
        return obj.to_dict(by_alias=True)
    if isinstance(obj, ApiResult):
        return to_jsonable(obj.to_dict(by_alias=True))
    if isinstance(obj, HydraPage):
        return {
            "members": [to_jsonable(m) for m in obj.members],
            "total": obj.total,
            "page": obj.page,
            "has_next": obj.has_next,
        }
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def iri_to_uuid(value: Any) -> str | None:
    """Extract the uuid from a FortiSOAR IRI, record, or model.

    FortiSOAR identifies records by IRI (``@id``, e.g. ``/api/3/alerts/<uuid>``);
    the uuid is the last path segment. This accepts whatever you have on hand and
    returns the uuid, or ``None`` if none can be found:

    - a **string** IRI (``"/api/3/alerts/abc-123"`` → ``"abc-123"``) or a bare uuid
      (returned unchanged),
    - a **record dict** — preferring ``@id``, then a literal ``uuid`` / ``id`` key,
    - a **model** (``BaseRecord``/``ApiResult``) — normalized then read the same way.

    Replaces the recurring ``rec["@id"].split("/")[-1] if "@id" in rec else rec["id"]``
    idiom.
    """
    if value is None:
        return None
    if isinstance(value, (BaseRecord, ApiResult)):
        value = value.to_dict(by_alias=True)
    if isinstance(value, dict):
        iri = value.get("@id")
        if isinstance(iri, str) and iri:
            return iri.rstrip("/").rsplit("/", 1)[-1]
        for key in ("uuid", "id"):
            v = value.get(key)
            if isinstance(v, str) and v:
                return v.rstrip("/").rsplit("/", 1)[-1] if "/" in v else v
        return None
    if isinstance(value, str):
        return value.rstrip("/").rsplit("/", 1)[-1] if value else None
    return None


def _collapse_value(value: Any) -> Any:
    """Reduce an expanded picklist / relationship object to a readable scalar.

    Expanded picklists arrive as ``{itemValue, @id, ...}`` and relationships as
    ``{@id, name, ...}``; for a summary we want the human-readable label, falling
    back to the IRI. Lists are collapsed element-wise. Plain scalars pass through.
    """
    if isinstance(value, dict):
        for key in ("itemValue", "name", "title", "displayName"):
            if value.get(key):
                return value[key]
        if value.get("@id"):
            return value["@id"]
        return value
    if isinstance(value, list):
        return [_collapse_value(v) for v in value]
    return value


def project_record(
    record: Any,
    *,
    fields: list[str] | tuple[str, ...] | None = None,
    summary: bool = False,
) -> Any:
    """Trim a single record (model or dict) for token-efficient consumption.

    With ``fields`` given, keep exactly those keys (plus ``@id``/``uuid`` so the
    result stays addressable). With ``summary=True`` and no ``fields``, keep the
    :data:`SUMMARY_FIELDS` that are present and collapse expanded picklist /
    relationship objects to their label. With neither, the record is returned
    unchanged (as a plain dict). Non-dict inputs are returned untouched.
    """
    data = to_jsonable(record)
    if not isinstance(data, dict):
        return data

    if fields:
        keep = list(dict.fromkeys((*fields, *(f for f in _REF_FIELDS if f in data))))
        return {k: data[k] for k in keep if k in data}

    if summary:
        out: dict[str, Any] = {}
        for key in SUMMARY_FIELDS:
            if key in data and data[key] is not None:
                out[key] = _collapse_value(data[key])
        # Guarantee at least a reference if none of the summary fields matched.
        for ref in _REF_FIELDS:
            if ref not in out and data.get(ref) is not None:
                out[ref] = data[ref]
        return out

    return data


def project(
    obj: Any,
    *,
    fields: list[str] | tuple[str, ...] | None = None,
    summary: bool = False,
) -> Any:
    """Project a record, a list of records, or a :class:`HydraPage`.

    Dispatches :func:`project_record` over the members of a page / list and
    preserves the page envelope (``total``/``page``/``has_next``). A bare record
    is projected directly. With no ``fields``/``summary`` this is just
    :func:`to_jsonable`.
    """
    if not fields and not summary:
        return to_jsonable(obj)

    if isinstance(obj, HydraPage):
        return {
            "members": [project_record(m, fields=fields, summary=summary) for m in obj.members],
            "total": obj.total,
            "page": obj.page,
            "has_next": obj.has_next,
        }
    if isinstance(obj, list):
        return [project_record(m, fields=fields, summary=summary) for m in obj]
    return project_record(obj, fields=fields, summary=summary)
