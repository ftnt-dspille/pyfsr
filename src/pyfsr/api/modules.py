"""Module (schema) discovery: list modules and describe their fields.

A runtime discovery surface so a caller — or an agent — can learn the appliance's
schema before reading/writing records: which modules exist (and the
plural-vs-singular name that bites on FortiCloud), and each module's fields with
type, required-ness, and the picklist a field binds to.

Both read FortiSOAR's ``staging_model_metadatas`` (the same source
:class:`~pyfsr.api.picklists.PicklistsAPI` uses); results are cached in-process.
Accessed as ``client.modules`` (or the ``client.list_modules()`` /
``client.describe_module()`` shortcuts).

Example:
    >>> client.list_modules()[:3]
    [{'type': 'agents', 'label': 'Agent', 'plural': 'agents'}, ...]
    >>> client.describe_module("incidents")["fields"][0]
    {'name': 'name', 'title': 'Name', 'type': 'text', 'required': True,
     'picklist_name': None}
"""

from __future__ import annotations

from typing import Any

from .base import BaseAPI
from .picklists import _picklist_name_from_attr

# No $relationships → small payload, enough for the module list.
_META_BASE = "/api/3/staging_model_metadatas?$limit=2147483647&$orderby=type"
# $relationships=true exposes each attribute's dataSource (picklist binding).
_META_FULL = _META_BASE + "&$relationships=true"


def _friendly(*candidates: Any) -> str:
    """Return the first non-empty candidate that isn't a Jinja template.

    Module/field ``displayName`` on a live appliance is often a template like
    ``{{ name }}`` rather than a human label; skip those in favour of a real
    ``descriptions.singular`` so search and printing read nicely.
    """
    for c in candidates:
        if c and isinstance(c, str) and "{{" not in c:
            return c
    # fall back to the first truthy value even if templated
    for c in candidates:
        if c:
            return str(c)
    return ""


class ModulesAPI(BaseAPI):
    """Live module + field schema discovery (cached in-process)."""

    def __init__(self, client):
        super().__init__(client)
        self._modules: list[dict[str, Any]] | None = None
        self._described: dict[str, dict[str, Any]] = {}

    def clear_cache(self) -> None:
        """Drop the cached module list and per-module descriptions."""
        self._modules = None
        self._described.clear()

    def list(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Return every module as ``[{type, label, plural}, ...]`` (sorted).

        ``type`` is the singular module type used in record IRIs; ``plural`` is
        the collection name (they differ on some appliances). Cached after the
        first call; pass ``refresh=True`` to re-fetch.
        """
        if self._modules is not None and not refresh:
            return self._modules
        data = self.client.get(_META_BASE)
        members = (data or {}).get("hydra:member") or []
        mods = [
            {
                "type": m.get("type"),
                "label": _friendly(
                    m.get("displayName"),
                    (m.get("descriptions") or {}).get("singular"),
                    m.get("module"),
                    m.get("type"),
                ),
                "plural": m.get("module"),
            }
            for m in members
            if m.get("type")
        ]
        mods.sort(key=lambda m: str(m["type"]))
        self._modules = mods
        return mods

    def describe(
        self, module: str, *, refresh: bool = False, with_values: bool = False
    ) -> dict[str, Any]:
        """Describe one module's fields.

        Returns ``{module, label, plural, field_count, fields}`` where each field
        is ``{name, title, type, required, picklist_name}``. If the module isn't
        found, returns ``{error, available}`` listing the known module types.
        Cached per module.

        ``with_values=True`` additionally resolves each picklist-backed field's
        valid friendly values into ``picklist_values`` — so an AI authoring a
        record knows exactly which friendly strings are accepted (e.g. the
        ``AlertType`` options) before it submits.
        """
        want = module.strip().lower()
        if not refresh and want in self._described:
            base = self._described[want]
            return self._with_picklist_values(base) if with_values else base
        data = self.client.get(_META_FULL)
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
        if hit is None:
            types = sorted({m.get("type") for m in members if m.get("type")})
            return {"error": f"module {module!r} not found", "available": types}
        fields = []
        for a in hit.get("attributes") or []:
            if not isinstance(a, dict) or not a.get("name"):
                continue
            validation = a.get("validation")
            fields.append(
                {
                    "name": a.get("name"),
                    "title": _friendly(
                        a.get("title"),
                        (a.get("descriptions") or {}).get("singular"),
                        a.get("displayName"),
                        a.get("name"),
                    ),
                    "type": a.get("type") or a.get("formType"),
                    "form_type": a.get("formType"),
                    "required": bool(
                        isinstance(validation, dict) and validation.get("required") is True
                    ),
                    "picklist_name": _picklist_name_from_attr(a),
                }
            )
        out = {
            "module": hit.get("type"),
            "label": _friendly(
                hit.get("displayName"),
                (hit.get("descriptions") or {}).get("singular"),
                hit.get("module"),
                hit.get("type"),
            ),
            "plural": hit.get("module"),
            "field_count": len(fields),
            "fields": fields,
        }
        self._described[want] = out
        return self._with_picklist_values(out) if with_values else out

    def _with_picklist_values(self, described: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of a describe() result with ``picklist_values`` added to
        each picklist-backed field (friendly itemValues)."""
        if "fields" not in described:
            return described
        enriched = []
        for f in described["fields"]:
            f = dict(f)
            name = f.get("picklist_name")
            if name:
                try:
                    f["picklist_values"] = self.client.picklists.options(name)
                except Exception:
                    f["picklist_values"] = []
            enriched.append(f)
        out = dict(described)
        out["fields"] = enriched
        return out

    # ------------------------------------------------------------- search
    def search(self, query: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Return modules whose type, label, or plural contains ``query`` (case-insensitive).

        >>> client.modules.search("incid")
        [{'type': 'incidents', 'label': 'Incident', 'plural': 'incidents'}]
        """
        q = query.strip().lower()
        return [
            m
            for m in self.list(refresh=refresh)
            if q in str(m["type"]).lower()
            or q in str(m["label"]).lower()
            or q in str(m["plural"]).lower()
        ]

    def fields(self, module: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Shortcut for ``describe(module)['fields']`` (empty list if not found)."""
        return self.describe(module, refresh=refresh).get("fields", [])

    def find_field(
        self,
        name: str | None = None,
        *,
        type: str | None = None,
        required: bool | None = None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Search fields across **all** modules.

        Filters (all optional, AND-combined): ``name`` substring (case-insensitive),
        exact field ``type``, and ``required``. Returns ``[{module, field}, ...]``.

        >>> client.modules.find_field(type="json")          # every JSON field, any module
        >>> client.modules.find_field(name="severity")      # where does 'severity' live
        """
        nq = name.strip().lower() if name else None
        hits: list[dict[str, Any]] = []
        for mod in self.list(refresh=refresh):
            for f in self.fields(mod["type"]):
                if nq and nq not in str(f["name"]).lower() and nq not in str(f["title"]).lower():
                    continue
                if type is not None and f.get("type") != type:
                    continue
                if required is not None and bool(f.get("required")) != required:
                    continue
                hits.append({"module": mod["type"], "field": f})
        return hits

    def format_module(self, module: str, *, refresh: bool = False, with_values: bool = True) -> str:
        """Return a human-readable description of a module and its fields.

        Handy for a quick "print one module as a sample" — e.g.
        ``print(client.modules.format_module("alerts"))``. By default it also
        lists the valid friendly values of each picklist field so an AI can see
        what to use.
        """
        d = self.describe(module, refresh=refresh, with_values=with_values)
        if "error" in d:
            return f"{d['error']}\navailable: {', '.join(d.get('available', []))}"
        lines = [
            f"Module: {d['label']}  (type={d['module']}, plural={d['plural']})",
            f"Fields: {d['field_count']}",
            f"  {'NAME':<28} {'TYPE':<18} {'REQ':<4} {'TITLE'}",
            f"  {'-' * 28} {'-' * 18} {'-' * 4} {'-' * 20}",
        ]
        for f in d["fields"]:
            req = "yes" if f.get("required") else ""
            pick = f"  [picklist: {f['picklist_name']}]" if f.get("picklist_name") else ""
            lines.append(
                f"  {str(f['name']):<28} {str(f.get('type')):<18} {req:<4} "
                f"{f.get('title', '')}{pick}"
            )
            vals = f.get("picklist_values")
            if vals:
                shown = ", ".join(vals[:25]) + ("  …" if len(vals) > 25 else "")
                lines.append(f"  {'':<28} {'':<18} {'':<4} valid: {shown}")
        return "\n".join(lines)

    def print_module(self, module: str, *, refresh: bool = False) -> None:
        """Print :meth:`format_module` to stdout."""
        print(self.format_module(module, refresh=refresh))
