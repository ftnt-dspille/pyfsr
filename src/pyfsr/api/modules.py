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
    >>> client.list_modules()["modules"][:3]
    [{'type': 'alerts', 'label': 'Alerts', 'plural': 'alerts'}, ...]
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
                "label": m.get("displayName") or m.get("module") or m.get("type"),
                "plural": m.get("module"),
            }
            for m in members
            if m.get("type")
        ]
        mods.sort(key=lambda m: str(m["type"]))
        self._modules = mods
        return mods

    def describe(self, module: str, *, refresh: bool = False) -> dict[str, Any]:
        """Describe one module's fields.

        Returns ``{module, label, plural, field_count, fields}`` where each field
        is ``{name, title, type, required, picklist_name}``. If the module isn't
        found, returns ``{error, available}`` listing the known module types.
        Cached per module.
        """
        want = module.strip().lower()
        if not refresh and want in self._described:
            return self._described[want]
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
                    "title": a.get("title") or a.get("displayName") or a.get("name"),
                    "type": a.get("type") or a.get("formType"),
                    "required": bool(
                        isinstance(validation, dict) and validation.get("required") is True
                    ),
                    "picklist_name": _picklist_name_from_attr(a),
                }
            )
        out = {
            "module": hit.get("type"),
            "label": hit.get("displayName") or hit.get("module") or hit.get("type"),
            "plural": hit.get("module"),
            "field_count": len(fields),
            "fields": fields,
        }
        self._described[want] = out
        return out
