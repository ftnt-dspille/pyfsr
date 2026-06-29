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

from typing import Any

from ..models._system import PicklistItem, PicklistName
from .base import BaseAPI

# staging_model_metadatas needs $relationships=true to expose each attribute's
# dataSource (where the bound picklist listName lives).
_META_PATH = "/api/3/staging_model_metadatas?$limit=2147483647&$orderby=type&$relationships=true"

# Wire endpoints (live-verified on 8.0.0):
#   POST   /api/3/picklist_names  {name, system}          -> 201 PicklistName
#   POST   /api/3/picklists       {itemValue, listName}   -> 201 Picklist (item)
#   DELETE /api/3/picklists/<uuid>                         -> 204
#   DELETE /api/3/picklist_names/<uuid>                    -> 204 (cascades to items)
# A duplicate list NAME 409s (UniqueConstraintViolationException on `name`);
# duplicate itemValue within a list is allowed (no unique constraint on it).
_PICKLIST_NAMES = "/api/3/picklist_names"
_PICKLISTS = "/api/3/picklists"


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
        self._items: dict[str, list[PicklistItem]] | None = None  # name -> typed items
        self._iri: dict[str, str] = {}  # "ListName:value" -> IRI
        # module -> {field_name: picklist_name (or None)}
        self._module_fields: dict[str, dict[str, str | None]] = {}

    # ------------------------------------------------------------------ caches
    def clear_cache(self) -> None:
        """Drop all in-process picklist caches."""
        self._names = None
        self._items = None
        self._iri.clear()
        self._module_fields.clear()

    # ----------------------------------------------------------------- bulk load
    def _load_bulk(self, *, refresh: bool = False) -> None:
        """Warm every picklist + its items in **two calls**, not ``1+N``.

        ``GET /api/3/picklist_names`` gives the full name set and the listName-IRI
        → name map; ``GET /api/3/picklists`` returns every item across every
        picklist in one page (each carries its own ``listName`` IRI). We group the
        items under their picklist name from the first call's map. Both responses
        are cached for the client's lifetime; pass ``refresh=True`` to re-pull.
        """
        if self._items is not None and self._names is not None and not refresh:
            return
        names_resp = self.client.get("/api/3/picklist_names", params={"$limit": 2147483647})
        iri_to_name: dict[str, str] = {}
        names: set[str] = set()
        for m in (names_resp or {}).get("hydra:member") or []:
            nm, iri = m.get("name"), m.get("@id")
            if nm:
                names.add(nm)
                if iri:
                    iri_to_name[iri] = nm
        items_resp = self.client.get("/api/3/picklists", params={"$limit": 2147483647})
        grouped: dict[str, list[PicklistItem]] = {}
        for m in (items_resp or {}).get("hydra:member") or []:
            item = PicklistItem.model_validate(m)
            ln_iri = item.list_name_iri
            name = iri_to_name.get(ln_iri) if ln_iri else None
            if name is None:
                continue
            grouped.setdefault(name, []).append(item)
        self._items = grouped
        self._names = sorted(names)

    # -------------------------------------------------------------- names/values
    def list(self) -> list[str]:
        """Return every picklist name on the server (sorted, cached)."""
        self._load_bulk()
        return self._names or []

    def all(self, *, refresh: bool = False) -> dict[str, list[PicklistItem]]:
        """Every picklist's items, keyed by picklist name (typed, bulk-warmed).

        One bulk fetch backs this and :meth:`list`/:meth:`values`/:meth:`resolve`,
        so warming a whole catalog costs two HTTP calls rather than ``1+N``.
        """
        self._load_bulk(refresh=refresh)
        return self._items or {}

    def values(self, picklist_name: str) -> list[dict[str, Any]]:
        """List a picklist's items as ``[{itemValue, uuid, iri, ordinal}, ...]``."""
        self._load_bulk()
        return [
            {"itemValue": it.itemValue, "uuid": it.uuid, "iri": it.iri, "ordinal": it.order_index}
            for it in (self._items or {}).get(picklist_name, [])
        ]

    # ---------------------------------------------------------- (module,field)
    def _field_map(self, module: str) -> dict[str, str | None]:
        """``{field_name: picklist_name|None}`` for one module (live + cached)."""
        want = module.strip().lower()
        if want in self._module_fields:
            return self._module_fields[want]
        data = self.client.get(_META_PATH)
        members = (data or {}).get("hydra:member") or []
        hit = next(
            (m for m in members if str(m.get("type", "")).lower() == want or str(m.get("module", "")).lower() == want),
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
                    report.append({"field": k, "value": v, "picklist": picklist, "valid_values": valid})
                if strict:
                    raise PicklistResolutionError(k, v, picklist, valid)
            out[k] = v
        return out

    def validate_record_fields(self, module: str, fields: dict[str, Any]) -> list[dict[str, Any]]:
        """Dry-run picklist resolution: return the misses without mapping/writing.

        Runs the same resolution :meth:`resolve_record_fields` would, but discards
        the resolved dict and returns only the unresolved picklist values as
        ``[{field, value, picklist, valid_values}, ...]`` — empty list means every
        picklist field resolves cleanly. Lets a caller validate its mappings before
        committing any write.

        Example:
            >>> misses = client.picklists.validate_record_fields(
            ...     "alerts", {"severity": "Critical", "status": "Bogus"})
            >>> [m["field"] for m in misses]
            ['status']
        """
        report: list[dict[str, Any]] = []
        self.resolve_record_fields(module, fields, report=report)
        return report

    # --------------------------------------------------------------- write ops
    # Live-verified on 8.0.0. These are NOT
    # cached-read operations — each performs a real write, then invalidates the
    # read cache so the next :meth:`values`/`resolve` reflects the change.
    #
    # FortiSOAR has no batch "create list + options" endpoint, so the list and
    # each option are individual POSTs (sequential, ordered). A list NAME is
    # unique instance-wide (a duplicate POST 409s); option itemValue is NOT
    # unique within a list, so callers dedup explicitly or use :meth:`ensure_picklist`.

    def create_picklist(
        self,
        name: str,
        *,
        system: bool = False,
        options: list[str] | list[dict[str, Any]] | None = None,
    ) -> PicklistName:
        """Create a picklist *list* (optionally with initial options).

        Args:
            name: the list's friendly name (unique instance-wide — a duplicate
                409s; use :meth:`get_or_create_picklist` for idempotency).
            system: the ``system`` flag (custom lists are ``False``).
            options: optional initial option labels. A bare string becomes an item
                with auto orderIndex; a dict can carry ``{"value", "color",
                "order"}`` (``order`` overrides the position). Created in order.

        Returns:
            The created :class:`~pyfsr.models.PicklistName` (with its options
            embedded when ``options`` was given).

        Raises:
            APIError: on a 409 duplicate name (``UniqueConstraintViolationException``).
        """

        created = self.client.post(_PICKLIST_NAMES, data={"name": name, "system": system})
        pn = PicklistName.model_validate(created)
        if options:
            for idx, opt in enumerate(options):
                if isinstance(opt, dict):
                    value = opt.get("value")
                    color = opt.get("color")
                    order = opt.get("order", idx)
                else:
                    value, color, order = opt, None, idx
                if value:
                    self.add_option(pn.iri or pn.uuid, value, color=color, order=order)
            # re-read with relationships to embed the created options
            pn = self.get_picklist(pn.uuid)
        self.clear_cache()
        return pn

    def get_or_create_picklist(
        self,
        name: str,
        *,
        system: bool = False,
        options: list[str] | list[dict[str, Any]] | None = None,
    ) -> tuple[PicklistName, bool]:
        """Idempotently ensure picklist ``name`` exists; return ``(list, created)``.

        If the list already exists, its options are **not** modified (only the list
        is ensured) — use :meth:`add_option` to append. Returns ``created=True``
        only when the list was newly created.
        """
        existing = self.get_picklist(name)
        if existing is not None:
            return existing, False
        return self.create_picklist(name, system=system, options=options), True

    def add_option(
        self,
        picklist: str,
        value: str,
        *,
        color: str | None = None,
        order: int | None = None,
    ) -> PicklistItem:
        """Add an option (item) to an existing picklist.

        Args:
            picklist: the target list — a name, a list IRI
                (``/api/3/picklist_names/<uuid>``), or a bare uuid.
            value: the option's friendly label (``itemValue``).
            color: optional hex color (e.g. ``"#FF0000"``).
            order: the ``orderIndex``; defaults to appending after the list's
                current highest index.

        Returns:
            The created :class:`~pyfsr.models.PicklistItem` (its ``iri`` is what a
            record stores for this value).

        Raises:
            ValueError: if ``picklist`` can't be resolved to a list IRI.
        """
        list_iri = self._resolve_list_iri(picklist)
        if list_iri is None:
            raise ValueError(f"picklist {picklist!r} not found — create it first with create_picklist()")
        payload: dict[str, Any] = {"itemValue": value, "listName": list_iri}
        if color is not None:
            payload["color"] = color
        if order is not None:
            payload["orderIndex"] = order
        created = self.client.post(_PICKLISTS, data=payload)
        item = PicklistItem.model_validate(created)
        self.clear_cache()
        return item

    def remove_option(
        self,
        picklist: str | None = None,
        *,
        value: str | None = None,
        item: str | None = None,
        missing_ok: bool = True,
    ) -> bool:
        """Remove an option from a picklist.

        Identify the item either by its ``value`` within ``picklist`` (resolved to
        an IRI) or directly by its ``item`` IRI/uuid. Returns ``True`` if an item
        was deleted; with ``missing_ok=True`` (default) an absent item returns
        ``False`` rather than raising.

        Raises:
            ValueError: if neither ``item`` nor ``value`` is given, or ``value``
                is given without ``picklist``.
            ResourceNotFoundError: if the item is absent and ``missing_ok=False``.
        """
        from ..exceptions import ResourceNotFoundError

        if item is None and value is None:
            raise ValueError("remove_option() requires `item=` or `value=`")
        if item is None:
            if not picklist:
                raise ValueError("remove_option(value=...) requires picklist=")
            item_iri = self.resolve(value, picklist=picklist)
            if item_iri is None:
                if missing_ok:
                    return False
                raise ResourceNotFoundError(f"option {value!r} not in picklist {picklist!r}")
        else:
            item_iri = item if str(item).startswith("/api/") else f"/api/3/picklists/{item}"
        self.client.delete(item_iri)
        self.clear_cache()
        return True

    def remove_picklist(
        self,
        picklist: str,
        *,
        missing_ok: bool = True,
    ) -> bool:
        """Delete a picklist *list* and (per the platform) cascade-delete its items.

        Args:
            picklist: the list — a name, IRI, or bare uuid.
            missing_ok: when ``True`` (default), an absent list returns ``False``
                instead of raising.

        Returns:
            ``True`` if a list was deleted.

        Raises:
            ResourceNotFoundError: if the list is absent and ``missing_ok=False``.
        """
        from ..exceptions import ResourceNotFoundError

        list_iri = self._resolve_list_iri(picklist)
        if list_iri is None:
            if missing_ok:
                return False
            raise ResourceNotFoundError(f"picklist {picklist!r} not found")
        self.client.delete(list_iri)
        self.clear_cache()
        return True

    def get_picklist(self, picklist: str, *, relationships: bool = True) -> PicklistName | None:
        """Fetch one picklist list by name, IRI, or uuid (None if absent).

        With ``relationships=True`` (default) the options are embedded under
        ``picklists`` (so ``.items`` is populated); without it the list is returned
        bare. Resolves a name to its IRI via the bulk name index first.
        """
        from ..exceptions import ResourceNotFoundError

        list_iri = self._resolve_list_iri(picklist)
        if list_iri is None:
            return None
        params: dict[str, Any] = {"$relationships": "true"} if relationships else None
        try:
            data = self.client.get(list_iri, params=params)
        except ResourceNotFoundError:
            return None
        return PicklistName.model_validate(data) if data else None

    # ------------------------------------------------------------- write helpers
    def _list_name_to_iri(self, name: str) -> str | None:
        """Resolve a picklist NAME to its list IRI via the bulk name index."""
        self._load_bulk()
        # The bulk load groups items by name but drops the listName IRI→name inverse
        # mapping after use; fetch names once to build it.
        names_resp = self.client.get(_PICKLIST_NAMES, params={"$limit": 2147483647})
        for m in (names_resp or {}).get("hydra:member") or []:
            if m.get("name") == name:
                return m.get("@id")
        return None

    def _resolve_list_iri(self, picklist: str) -> str | None:
        """Coerce a picklist identifier (name | IRI | uuid) to its list IRI, or None."""
        if not picklist:
            return None
        if isinstance(picklist, str) and picklist.startswith("/api/3/picklist_names/"):
            return picklist
        if isinstance(picklist, str) and "/" not in picklist and len(picklist) == 36:
            return f"/api/3/picklist_names/{picklist}"
        return self._list_name_to_iri(picklist)
