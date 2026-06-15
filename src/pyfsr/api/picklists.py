"""Picklist resolution: name discovery, value lookup, IRI mapping.

Ported from the fsr-playbook-framework runner, de-coupled from its local sqlite
reference DB. All discovery is live against the FortiSOAR REST API and cached
in-process for the lifetime of the client.

The hard part FortiSOAR makes you do is turn a *friendly* picklist value (e.g.
``"High"``) into the IRI (``/api/3/picklists/<uuid>``) the API actually stores.
This API resolves that, and can auto-discover which picklist a ``(module, field)``
pair binds to from the module's field metadata.

Example:
    >>> client.picklists.list()                       # all picklist names
    >>> client.picklists.values("AlertStatus")        # its items
    >>> client.picklists.for_field("alerts", "status") # -> "AlertStatus"
    >>> client.picklists.resolve("High", picklist="Severity")
    '/api/3/picklists/...'
    >>> client.picklists.resolve_record_fields(
    ...     "alerts", {"name": "x", "severity": "High", "status": "Open"})
    {'name': 'x', 'severity': '/api/3/picklists/...', 'status': '/api/3/picklists/...'}
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from .base import BaseAPI

# staging_model_metadatas needs $relationships=true to expose each attribute's
# dataSource (where the bound picklist listName lives).
_META_PATH = "/api/3/staging_model_metadatas?$limit=2147483647&$orderby=type&$relationships=true"


def _picklist_name_from_attr(attr: dict) -> str | None:
    """The picklist listName an attribute binds to (e.g. 'AlertStatus')."""
    ds = attr.get("dataSource") or {}
    if not isinstance(ds, dict):
        return None
    for f in (ds.get("query") or {}).get("filters", []) or []:
        if isinstance(f, dict) and f.get("field") == "listName__name":
            v = f.get("value")
            if isinstance(v, str):
                return v
    return None


class PicklistsAPI(BaseAPI):
    """Live picklist lookups and friendly-value → IRI resolution.

    Accessed as ``client.picklists``. All lookups are cached in-process;
    construct a new client (or call :meth:`clear_cache`) to refresh.
    """

    def __init__(self, client):
        super().__init__(client)
        self._names: list[str] | None = None
        self._values: dict[str, list[dict]] = {}  # listName -> items
        self._iri: dict[str, str] = {}  # "ListName:value" -> IRI
        # module -> {field_name: picklist_name (or None)}
        self._module_fields: dict[str, dict[str, str | None]] = {}

    # ------------------------------------------------------------------ caches
    def clear_cache(self) -> None:
        """Drop all in-process picklist caches."""
        self._names = None
        self._values.clear()
        self._iri.clear()
        self._module_fields.clear()

    # -------------------------------------------------------------- names/values
    def list(self) -> list[str]:
        """Return every picklist name on the server (sorted, cached)."""
        if self._names is not None:
            return self._names
        data = self.client.get("/api/3/picklist_names", params={"$limit": 500})
        members = (data or {}).get("hydra:member") or []
        self._names = sorted({m.get("name") for m in members if m.get("name")})
        return self._names

    def values(self, picklist_name: str) -> list[dict[str, Any]]:
        """List a picklist's items as ``[{itemValue, uuid, iri, ordinal}, ...]``."""
        if picklist_name in self._values:
            return self._values[picklist_name]
        qs = urllib.parse.urlencode({"listName.name": picklist_name, "$limit": 200})
        data = self.client.get(f"/api/3/picklists?{qs}")
        out: list[dict[str, Any]] = []
        for m in (data or {}).get("hydra:member") or []:
            u = m.get("uuid")
            out.append(
                {
                    "itemValue": m.get("itemValue"),
                    "uuid": u,
                    "iri": f"/api/3/picklists/{u}" if u else None,
                    "ordinal": m.get("ordinal"),
                }
            )
        self._values[picklist_name] = out
        return out

    # ---------------------------------------------------------- (module,field)
    def _field_map(self, module: str) -> dict[str, str | None]:
        """``{field_name: picklist_name|None}`` for one module (live + cached)."""
        want = module.strip().lower()
        if want in self._module_fields:
            return self._module_fields[want]
        data = self.client.get(_META_PATH)
        members = (data or {}).get("hydra:member") or []
        hit = next(
            (
                m
                for m in members
                if str(m.get("type", "")).lower() == want
                or str(m.get("module", "")).lower() == want
            ),
            None,
        )
        fmap: dict[str, str | None] = {}
        if hit is not None:
            for a in hit.get("attributes") or []:
                if isinstance(a, dict) and a.get("name"):
                    fmap[a["name"]] = _picklist_name_from_attr(a)
        self._module_fields[want] = fmap
        return fmap

    def for_field(self, module: str, field: str) -> str | None:
        """Return the picklist name a ``(module, field)`` binds to, or ``None``."""
        return self._field_map(module).get(field)

    # ----------------------------------------------------------------- resolve
    def resolve(
        self,
        value: str,
        *,
        picklist: str | None = None,
        module: str | None = None,
        field: str | None = None,
    ) -> str | None:
        """Resolve a friendly value (e.g. ``"High"``) to its picklist IRI.

        Provide either an explicit ``picklist`` name, or ``(module, field)`` to
        auto-discover it. Already-IRI strings pass through unchanged. Returns
        ``None`` if the value isn't found in the resolved picklist.
        """
        if not isinstance(value, str):
            return None
        if value.startswith("/api/"):
            return value
        if picklist is None:
            if not (module and field):
                return None
            picklist = self.for_field(module, field)
            if not picklist:
                return None
        cache_key = f"{picklist}:{value.lower()}"
        if cache_key in self._iri:
            return self._iri[cache_key]
        for it in self.values(picklist):
            if (it.get("itemValue") or "").lower() == value.lower():
                iri = it.get("iri")
                if iri:
                    self._iri[cache_key] = iri
                return iri
        return None

    def options(self, picklist_name: str) -> list[str]:
        """The valid friendly values (itemValues) of a picklist — what an AI
        should choose from. Cached via :meth:`values`."""
        return [it.get("itemValue") for it in self.values(picklist_name) if it.get("itemValue")]

    def resolve_record_fields(
        self,
        module: str,
        fields: dict[str, Any],
        *,
        strict: bool = False,
        report: list | None = None,
    ) -> dict[str, Any]:
        """Return a copy of ``fields`` with picklist-typed values mapped to IRIs.

        Only fields the module flags as picklist-backed are touched, and only
        when their value is a friendly string (not already an IRI).

        Friendly feedback on a miss (a value not in the picklist):
          - ``strict=True`` raises :class:`~pyfsr.exceptions.PicklistResolutionError`
            naming the field, bad value, and the valid options.
          - pass a list as ``report`` to collect misses as
            ``{field, value, picklist, valid_values}`` without raising.
          - by default the original value is left in place (back-compatible).
        """
        from ..exceptions import PicklistResolutionError

        fmap = self._field_map(module)
        out: dict[str, Any] = {}
        for k, v in fields.items():
            picklist = fmap.get(k)
            if picklist and isinstance(v, str) and not v.startswith("/api/"):
                iri = self.resolve(v, picklist=picklist)
                if iri:
                    out[k] = iri
                    continue
                valid = self.options(picklist)
                if report is not None:
                    report.append({"field": k, "value": v, "picklist": picklist,
                                   "valid_values": valid})
                if strict:
                    raise PicklistResolutionError(k, v, picklist, valid)
            out[k] = v
        return out
