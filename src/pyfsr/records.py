"""Generic CRUD for any FortiSOAR module.

``client.records("incidents")`` returns a :class:`RecordSet` bound to that
module, so callers don't hand-build ``/api/3/<module>`` URLs or unwrap Hydra
envelopes. This is the generic counterpart to the typed ``client.alerts`` API
and works for every module on the appliance::

    incidents = client.records("incidents")
    inc = incidents.get("0d2c...")                   # by uuid
    page = incidents.query(Query().eq("status.itemValue", "Open").limit(50))
    for rec in incidents.iterate(Query().gt("createDate", ts)):
        ...
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, overload

from .models import BaseRecord, model_for
from .pagination import HydraPage, paginate
from .projection import project
from .query import Query

if TYPE_CHECKING:
    from .client import FortiSOAR

T = TypeVar("T", bound=BaseRecord)


def resolve_record_path(module: str, ref: str) -> str:
    """Build the ``/api/3/<module>/<uuid>`` path for a record reference.

    Accepts a full IRI (``/api/3/alerts/<uuid>`` — returned as-is), the
    ``module:uuid`` shorthand, or a bare uuid (combined with ``module``).
    """
    if ref.startswith("/api/"):
        return ref
    if ":" in ref and "/" not in ref:
        mod, _, uuid = ref.partition(":")
        return f"/api/3/{mod}/{uuid}"
    return f"/api/3/{module}/{ref}"


class RecordSet(Generic[T]):
    """CRUD operations scoped to a single FortiSOAR module.

    ``T`` is the bound model type (e.g. ``Alert``, ``Incident``).  Reads parse
    responses into typed :class:`~pyfsr.models.BaseRecord` subclasses when one
    is registered for the module, falling back to a bare ``BaseRecord``
    otherwise.  ``BaseRecord`` is dict-compatible (``rec["field"]`` /
    ``rec.get(...)`` / ``"field" in rec``), so typing is additive.

    Reads always come back as the bound model; pass ``model=...`` to force a
    specific one, or ``raw=True`` on an individual call for a one-off plain dict.

    The ``raw=True`` overloads narrow the return type to ``dict[str, Any]``
    so type checkers know exactly which shape they're getting.
    """

    def __init__(
        self,
        client: FortiSOAR,
        module: str,
        *,
        model: type[T] | None = None,
    ) -> None:
        self.client = client
        self.module = module
        self.model = model or model_for(module)  # type: ignore[assignment]

    # -- parsing ------------------------------------------------------------
    def _parse(self, obj: Any, *, raw: bool) -> Any:
        """Coerce a record dict into the bound model (unless ``raw``)."""
        if raw or self.model is None or not isinstance(obj, dict):
            return obj
        return self.model.model_validate(obj)

    def _parse_page(self, page: HydraPage[Any], *, raw: bool) -> HydraPage[Any]:
        if raw or self.model is None:
            return page
        page.members = [self._parse(m, raw=False) for m in page.members]
        return page

    # -- reads --------------------------------------------------------------
    @overload
    def get(
        self,
        ref: str,
        *,
        relationships: bool = ...,
        show_deleted: bool = ...,
        params: dict[str, Any] | None = ...,
        raw: Literal[True],
        fields: list[str] | tuple[str, ...] | None = ...,
        summary: bool = ...,
    ) -> dict[str, Any]: ...

    @overload
    def get(
        self,
        ref: str,
        *,
        relationships: bool = ...,
        show_deleted: bool = ...,
        params: dict[str, Any] | None = ...,
        raw: Literal[False] = ...,
        fields: list[str] | tuple[str, ...] | None = ...,
        summary: bool = ...,
    ) -> T: ...

    def get(
        self,
        ref: str,
        *,
        relationships: bool = False,
        show_deleted: bool = False,
        params: dict[str, Any] | None = None,
        raw: bool = False,
        fields: list[str] | tuple[str, ...] | None = None,
        summary: bool = False,
    ) -> Any:
        """Fetch one record by uuid, ``module:uuid`` shorthand, or IRI.

        Returns the bound model (or ``BaseRecord``); pass ``raw=True`` for the
        plain decoded dict. Pass ``show_deleted=True`` to read a soft-deleted
        record from the recycle bin (a plain ``get`` 404s on those).

        ``fields=[...]`` / ``summary=True`` trim the result to a token-efficient
        plain dict (handy for agents); see :mod:`pyfsr.projection`.
        """
        path = resolve_record_path(self.module, ref)
        query = dict(params or {})
        if relationships:
            query["$relationships"] = "true"
        if show_deleted:
            query["$showDeleted"] = "true"
        rec = self._parse(self.client.get(path, params=query or None), raw=raw)
        if fields or summary:
            return project(rec, fields=fields, summary=summary)
        return rec

    def get_many(
        self,
        refs: list[str] | tuple[str, ...],
        *,
        relationships: bool = False,
        show_deleted: bool = False,
        raw: bool = False,
        fields: list[str] | tuple[str, ...] | None = None,
        summary: bool = False,
        max_workers: int = 8,
        on_error: str = "none",
    ) -> list[Any]:
        """Fetch many records by id **concurrently**, results ordered like ``refs``.

        Each ref is fetched with the same semantics as :meth:`get` (a uuid,
        ``module:uuid`` shorthand, or IRI), in a bounded thread pool — N
        independent ``GET``s collapse from N round-trips to roughly one. Use this
        when you need *full* per-record reads (relationships, soft-deleted rows,
        or :mod:`pyfsr.projection` trimming) for a known id list; for a plain
        field read across ids, a single ``list(params={"uuid__in": ...})`` filter
        is cheaper.

        ``on_error="none"`` (default) puts ``None`` in the slot of any ref whose
        fetch fails (e.g. a 404) and keeps the rest; ``on_error="raise"`` lets the
        first failure propagate. See ``pyfsr._concurrency.map_threaded``.
        """
        from ._concurrency import map_threaded

        def _one(ref: str) -> Any:
            return self.get(
                ref,
                relationships=relationships,
                show_deleted=show_deleted,
                raw=raw,
                fields=fields,
                summary=summary,
            )

        return map_threaded(_one, list(refs), max_workers=max_workers, on_error=on_error)

    def comments(
        self,
        ref: str,
        *,
        limit: int = 30,
        page: int = 1,
        orderby: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """List comments on a record (``GET /api/3/<module>/<uuid>/comments``).

        The server scopes the comment query to the parent record for you — cheaper
        than ``GET /api/3/comments?<module>.uuid=<uuid>``. ``ref`` is a uuid,
        ``module:uuid`` shorthand, or IRI. Returns the ``hydra:member`` array.

        Note: comments are read-only on this path (create 405s); to add one,
        ``client.records("comments").create({...})`` against ``/api/3/comments``.
        """
        from .pagination import extract_members

        path = resolve_record_path(self.module, ref).rstrip("/") + "/comments"
        query = dict(params or {})
        query["$limit"] = limit
        query["$page"] = page
        if orderby is not None:
            query["$orderby"] = orderby
        return extract_members(self.client.get(path, params=query))

    @overload
    def list(
        self,
        *,
        limit: int = ...,
        page: int = ...,
        show_deleted: bool = ...,
        params: dict[str, Any] | None = ...,
        raw: Literal[True],
    ) -> HydraPage[dict[str, Any]]: ...

    @overload
    def list(
        self,
        *,
        limit: int = ...,
        page: int = ...,
        show_deleted: bool = ...,
        params: dict[str, Any] | None = ...,
        raw: Literal[False] = ...,
    ) -> HydraPage[T]: ...

    def list(
        self,
        *,
        limit: int = 30,
        page: int = 1,
        show_deleted: bool = False,
        params: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> HydraPage[Any]:
        """List records via ``GET /api/3/<module>`` (one page).

        For structured filtering use :meth:`filter` (or :meth:`query` with a
        :class:`~pyfsr.query.Query`); for free-text search use :meth:`search`;
        to page through all results lazily use :meth:`iterate`.
        Pass ``show_deleted=True`` to include recycle-bin records.
        """
        query = dict(params or {})
        query["$limit"] = limit
        query["$page"] = page
        if show_deleted:
            query["$showDeleted"] = "true"
        resp = self.client.get(f"/api/3/{self.module}", params=query)
        return self._parse_page(HydraPage.from_response(resp, page=page, limit=limit), raw=raw)

    def search(
        self,
        term: str = "",
        *,
        limit: int = 30,
        page: int = 1,
        show_deleted: bool = False,
        params: dict[str, Any] | None = None,
        raw: bool = False,
        fields: list[str] | tuple[str, ...] | None = None,
        summary: bool = False,
    ) -> Any:
        """Free-text search via ``GET /api/3/<module>?$search=<term>``.

        With ``fields=``/``summary=`` the page members are trimmed to a plain
        ``{members, total, page, has_next}`` dict (see :mod:`pyfsr.projection`).
        """
        query = dict(params or {})
        if term:
            query["$search"] = term
        page_obj = self.list(limit=limit, page=page, show_deleted=show_deleted, params=query, raw=raw)
        if fields or summary:
            return project(page_obj, fields=fields, summary=summary)
        return page_obj

    def aggregate(
        self,
        *,
        group_by: str | list[str] | None = None,
        metrics: list[tuple[str, str, str]] | None = None,
        count: bool = False,
        filters: list[dict[str, Any]] | None = None,
        logic: str = "AND",
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Server-side aggregation via ``POST /api/query/<module>`` ``aggregates[]``.

        Pushes grouping/counting to the database instead of pulling records and
        tallying client-side. Each result row is a dict keyed by the aliases.

        Args:
            group_by: field path(s) to ``GROUP BY`` (e.g. ``"severity.itemValue"``,
                or ``"triggerStep.stepType.name"`` on ``workflows``). Each becomes a
                ``groupby`` aggregate aliased to the field's last path segment.
            metrics: explicit ``(operator, field, alias)`` triples — ``operator`` is
                an ``AggregateOperators`` name (``count``/``countdistinct``/``sum``/
                ``avg``/``min``/``max``/``median``). ``field="*"`` counts rows.
            count: shorthand to append ``("countdistinct", "*", "total")``.
            filters: leaf/group filter dicts applied before aggregation (two-phase:
                filtered to a uuid subquery, then aggregated). Same grammar as
                :meth:`query`.
            logic: ``"AND"``/``"OR"`` for ``filters``.
            search: optional ``$search`` term (AND-combined with filters).
            limit: max aggregate rows (``$limit``).

        Returns:
            The aggregate rows (``hydra:member``), e.g.
            ``[{"itemValue": "high", "total": 42}, …]``.

        Note:
            Grouping by an association *from the child side* (e.g. grouping
            ``workflow_steps`` by ``workflow.uuid``) is rejected server-side, and
            there is no ``HAVING`` — post-aggregate count thresholds must be applied
            client-side. For per-playbook step quantities use
            :meth:`~pyfsr.api.playbooks.PlaybooksAPI.match`.

        Example:
            >>> client.records("workflows").aggregate(
            ...     group_by="triggerStep.stepType.name", count=True)  # doctest: +SKIP
            [{'name': 'cybersponse.action', 'total': 390}, ...]
        """
        aggregates: list[dict[str, Any]] = []
        for field in [group_by] if isinstance(group_by, str) else (group_by or []):
            aggregates.append({"operator": "groupby", "field": field, "alias": field.split(".")[-1]})
        for operator, field, alias in metrics or []:
            aggregates.append({"operator": operator, "field": field, "alias": alias})
        if count:
            aggregates.append({"operator": "countdistinct", "field": "*", "alias": "total"})
        if not aggregates:
            raise ValueError("aggregate() needs group_by, metrics, or count=True")

        body: dict[str, Any] = {"logic": logic, "filters": filters or [], "aggregates": aggregates}
        params: dict[str, Any] = {}
        if search is not None:
            params["$search"] = search
        if limit is not None:
            params["$limit"] = limit
        resp = self.client.post(f"/api/query/{self.module}", data=body, params=params or None)
        if isinstance(resp, dict):
            members = resp.get("hydra:member")
            if isinstance(members, list):
                return members
        return resp if isinstance(resp, list) else []

    def query(
        self,
        query: Query | dict[str, Any],
        *,
        page: int = 1,
        show_deleted: bool = False,
        raw: bool = False,
        fields: list[str] | tuple[str, ...] | None = None,
        summary: bool = False,
    ) -> Any:
        """Run a structured query via ``POST /api/query/<module>``.

        FortiSOAR paginates this endpoint with the ``$limit``/``$page``/``$search``
        *query params* — the ``limit``/``search`` keys in the body are ignored — so
        they are lifted out of the body and sent as params. Pass
        ``show_deleted=True`` to include recycle-bin records (sent both as the
        ``$showDeleted`` param and the ``showDeleted`` body flag the endpoint wants).

        Returns a :class:`~pyfsr.pagination.HydraPage`; with ``fields=``/``summary=``
        the members are trimmed and a plain ``{members, total, page, has_next}``
        dict is returned instead (see :mod:`pyfsr.projection`).
        """
        body, params = self._split_query(query, page=page)
        if show_deleted:
            params["$showDeleted"] = "true"
            body["showDeleted"] = True
        resp = self.client.post(f"/api/query/{self.module}", data=body, params=params)
        page_obj = HydraPage.from_response(resp, page=page, limit=params.get("$limit"))
        parsed = self._parse_page(page_obj, raw=raw)
        if fields or summary:
            return project(parsed, fields=fields, summary=summary)
        return parsed

    @staticmethod
    def _split_query(
        query: Query | dict[str, Any], *, page: int, page_size: int | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Split a query into (POST body, $-prefixed query params).

        ``$limit``/``$page``/``$search`` drive pagination as params; everything
        else (logic/filters/sort/__selectFields…) stays in the body.
        """
        body = query.to_body() if isinstance(query, Query) else dict(query)
        params: dict[str, Any] = {"$page": page}
        limit = page_size if page_size is not None else body.pop("limit", None)
        if page_size is not None:
            body.pop("limit", None)
        if limit is not None:
            params["$limit"] = limit
        search = body.pop("search", None)
        if search is not None:
            params["$search"] = search
        return body, params

    @overload
    def iterate(
        self,
        query: Query | dict[str, Any] | None = ...,
        *,
        page_size: int = ...,
        max_records: int | None = ...,
        show_deleted: bool = ...,
        raw: Literal[True],
    ) -> Iterator[dict[str, Any]]: ...

    @overload
    def iterate(
        self,
        query: Query | dict[str, Any] | None = ...,
        *,
        page_size: int = ...,
        max_records: int | None = ...,
        show_deleted: bool = ...,
        raw: Literal[False] = ...,
    ) -> Iterator[T]: ...

    def iterate(
        self,
        query: Query | dict[str, Any] | None = None,
        *,
        page_size: int = 100,
        max_records: int | None = None,
        show_deleted: bool = False,
        raw: bool = False,
    ) -> Iterator[Any]:
        """Lazily yield every matching record across all pages.

        Uses the structured query endpoint when ``query`` is given, otherwise a
        plain list. The page size overrides any ``limit`` on the query. Yields
        typed models unless ``raw=True``. Pass ``show_deleted=True`` to include
        recycle-bin records.
        """
        if query is None:

            def fetch(page: int) -> Any:
                params = {"$limit": page_size, "$page": page}
                if show_deleted:
                    params["$showDeleted"] = "true"
                return self.client.get(f"/api/3/{self.module}", params=params)
        else:

            def fetch(page: int) -> Any:
                body, params = self._split_query(query, page=page, page_size=page_size)
                if show_deleted:
                    params["$showDeleted"] = "true"
                    body["showDeleted"] = True
                return self.client.post(f"/api/query/{self.module}", data=body, params=params)

        for record in paginate(fetch, page_size=page_size, max_records=max_records):
            yield self._parse(record, raw=raw)

    # -- writes -------------------------------------------------------------
    @overload
    def create(
        self,
        data: dict[str, Any],
        *,
        raw: Literal[True],
        resolve_picklists: bool = ...,
    ) -> dict[str, Any]: ...

    @overload
    def create(
        self,
        data: dict[str, Any],
        *,
        raw: Literal[False] = ...,
        resolve_picklists: bool = ...,
    ) -> T: ...

    def create(
        self,
        data: dict[str, Any],
        *,
        raw: bool = False,
        resolve_picklists: bool = True,
    ) -> Any:
        """Create a record via ``POST /api/3/<module>``.

        ``data`` may be a dict or a model instance; the created record is
        returned parsed (or raw, with ``raw=True``). Friendly picklist values
        (e.g. ``"High"``) are mapped to their IRIs via ``client.picklists``
        before sending — pass ``resolve_picklists=False`` to skip that (and the
        metadata lookup it needs) when every value is already an IRI.
        """
        if isinstance(data, BaseRecord):
            data = data.to_dict(exclude_none=True)
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        return self._parse(self.client.post(f"/api/3/{self.module}", data=data), raw=raw)

    @overload
    def update(
        self,
        ref: str,
        data: dict[str, Any],
        *,
        raw: Literal[True],
        resolve_picklists: bool = ...,
    ) -> dict[str, Any]: ...

    @overload
    def update(
        self,
        ref: str,
        data: dict[str, Any],
        *,
        raw: Literal[False] = ...,
        resolve_picklists: bool = ...,
    ) -> T: ...

    def update(
        self,
        ref: str,
        data: dict[str, Any],
        *,
        raw: bool = False,
        resolve_picklists: bool = True,
    ) -> Any:
        """Update a record via ``PUT /api/3/<module>/<uuid>``.

        Friendly picklist values are mapped to IRIs before sending; pass
        ``resolve_picklists=False`` to skip that (see :meth:`create`).
        """
        if isinstance(data, BaseRecord):
            data = data.to_dict(exclude_none=True)
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        path = resolve_record_path(self.module, ref)
        return self._parse(self.client.put(path, data=data), raw=raw)

    @overload
    def get_or_create(
        self,
        data: dict[str, Any],
        *,
        key: str | list[str] = ...,
        raw: Literal[True],
        resolve_picklists: bool = ...,
    ) -> tuple[dict[str, Any], bool]: ...

    @overload
    def get_or_create(
        self,
        data: dict[str, Any],
        *,
        key: str | list[str] = ...,
        raw: Literal[False] = ...,
        resolve_picklists: bool = ...,
    ) -> tuple[T, bool]: ...

    def get_or_create(
        self,
        data: dict[str, Any],
        *,
        key: str | list[str] = "uuid",
        raw: bool = False,
        resolve_picklists: bool = True,
    ) -> tuple[Any, bool]:
        """Look up an existing record by key field(s), or create if absent.

        Queries for an existing record matching the given key field(s). If found,
        returns it with ``created=False``. Otherwise creates the record and
        returns ``created=True``.

        Args:
            data: dict or model instance with the record to create/match.
            key: field name (str) or list of field names to match against
                (default ``"uuid"`` — the natural key). Multiple keys are AND'ed.
            raw: if ``True``, returns a plain dict; otherwise a typed model.
            resolve_picklists: if ``True``, friendly picklist values are mapped to
                IRIs before posting (see :meth:`create`).

        Returns:
            A tuple of ``(record, created)`` where ``created`` is ``True`` if the
            record was newly created, ``False`` if it already existed.

        Raises:
            ValueError: if ``key`` field(s) are missing from ``data``.

        Example:
            >>> alert, created = client.records("alerts").get_or_create(
            ...     {"name": "Malware Alert", "severity": "High"},
            ...     key="name"
            ... )
            >>> if created:
            ...     print(f"Created new alert {alert.uuid}")
            ... else:
            ...     print(f"Alert already exists: {alert.uuid}")
        """
        if isinstance(data, BaseRecord):
            data = data.to_dict(exclude_none=True)

        # Normalize key to a list
        keys = [key] if isinstance(key, str) else list(key)
        if not keys:
            raise ValueError("key must be a non-empty field name or list of field names")

        # Verify all key fields are present in data
        for k in keys:
            if k not in data:
                raise ValueError(f"get_or_create() requires the key field {k!r} to be present in data")

        # Build a query to find existing record by key field(s)
        q = Query()
        for k in keys:
            q = q.eq(k, data[k])
        q = q.limit(1)

        existing = self.first(q, raw=raw)
        if existing is not None:
            return (existing, False)

        # Create if not found
        created_rec = self.create(data, raw=raw, resolve_picklists=resolve_picklists)
        return (created_rec, True)

    @overload
    def upsert(
        self,
        data: dict[str, Any],
        *,
        key: str | list[str] | None = ...,
        raw: Literal[True],
        resolve_picklists: bool = ...,
    ) -> dict[str, Any]: ...

    @overload
    def upsert(
        self,
        data: dict[str, Any],
        *,
        key: str | list[str] | None = ...,
        raw: Literal[False] = ...,
        resolve_picklists: bool = ...,
    ) -> T: ...

    def upsert(
        self,
        data: dict[str, Any],
        *,
        key: str | list[str] | None = None,
        raw: bool = False,
        resolve_picklists: bool = True,
    ) -> Any:
        """Insert-or-update one record via ``POST /api/3/upsert/<module>``.

        When ``key`` is ``None`` (default), FortiSOAR matches an existing row by
        the record's natural key (its ``uuid`` / ``@id`` when present, else the
        module's unique field) and updates it, otherwise creates a new one.

        When ``key`` is specified (a field name or list of field names),
        uses :meth:`get_or_create` to find an existing record by those fields;
        if found, updates it with the provided ``data``; otherwise creates it.

        ``data`` may be a dict or a model instance; friendly picklist values
        are mapped to IRIs first — pass ``resolve_picklists=False`` to skip that
        (see :meth:`create`).

        Args:
            data: dict or model instance with the record to create/update.
            key: optional field name(s) to match on for the lookup. If ``None``
                (default), uses FortiSOAR's natural key (``uuid`` or the module's
                unique field). If a str or list of strs, finds-by-key then updates
                if present, else creates.
            raw: if ``True``, returns a plain dict; otherwise a typed model.
            resolve_picklists: if ``True``, friendly picklist values are mapped to
                IRIs before posting.

        Returns:
            The upserted record (newly created or updated), parsed as the bound
            model (or raw dict if ``raw=True``).
        """
        if isinstance(data, BaseRecord):
            data = data.to_dict(exclude_none=True)

        # When no custom key is specified, use the FortiSOAR natural-key upsert endpoint
        if key is None:
            if resolve_picklists:
                data = self.client.picklists.resolve_record_fields(self.module, data)
            return self._parse(self.client.post(f"/api/3/upsert/{self.module}", data=data), raw=raw)

        # When a custom key is specified, use get_or_create + update pattern
        existing, created = self.get_or_create(
            data,
            key=key,
            raw=False,  # Always get the typed record internally
            resolve_picklists=resolve_picklists,
        )
        if created:
            # Record was just created, return it
            return self._parse(existing, raw=raw)

        # Record exists; update it
        ref = existing.get("@id") or existing.get("uuid") or existing.get("id")
        if not ref:
            raise ValueError("could not determine record reference for update (no @id, uuid, or id)")
        updated = self.update(ref, data, raw=False, resolve_picklists=False)
        return self._parse(updated, raw=raw)

    def bulk_upsert(
        self,
        rows: list[dict[str, Any]],
        *,
        resolve_picklists: bool = True,
    ) -> dict[str, Any]:
        """Insert-or-update many records via ``POST /api/3/bulkupsert/<module>``.

        ``rows`` is a list of dicts or model instances. Each row is matched the
        same way as :meth:`upsert`. The raw server response is returned
        unparsed — bulk endpoints reply with a multi-status (``207``) envelope
        whose per-row results the caller usually wants to inspect directly.
        Friendly picklist values are resolved on every row; pass
        ``resolve_picklists=False`` to skip that.
        """
        payload: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, BaseRecord):
                row = row.to_dict(exclude_none=True)
            if resolve_picklists:
                row = self.client.picklists.resolve_record_fields(self.module, row)
            payload.append(row)
        return self.client.post(f"/api/3/bulkupsert/{self.module}", data=payload)

    def _single_record_path(self, ref: str, *, action: str) -> str:
        """Resolve ``ref`` to a single-record path, refusing collection-wide refs.

        Guards against an empty/blank ``ref`` (or one that resolves to the bare
        ``/api/3/<module>`` collection) ever reaching a destructive endpoint —
        FortiSOAR has no safe bulk delete here, and an empty body has bitten
        before by acting collection-wide.
        """
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError(f"{action}() requires a non-empty record reference")
        path = resolve_record_path(self.module, ref.strip())
        if path.rstrip("/") in (f"/api/3/{self.module}", "/api/3"):
            raise ValueError(f"refusing to {action}: {ref!r} does not identify a single record")
        return path

    def delete(self, ref: str, *, hard: bool = False) -> None:
        """Delete one record by uuid, ``module:uuid`` shorthand, or IRI.

        Soft-delete (default) moves the record to the recycle bin via
        ``DELETE /api/3/<module>/<uuid>``. A soft-deleted row keeps reserving
        *both* its uuid and any unique name, so re-creating with the same name
        will collide until it's purged or restored; reverse it with
        :meth:`restore`.

        Pass ``hard=True`` to permanently delete via ``?$hardDelete=true``. This
        is a single-row, URL-scoped delete; on relationship-parent modules
        (e.g. ``workflow_collections``) the server cascades to children. An
        empty/blank ``ref`` raises — to delete many rows by filter use
        :meth:`delete_by_query`.
        """
        path = self._single_record_path(ref, action="delete")
        params = {"$hardDelete": "true"} if hard else None
        self.client.delete(path, params=params)

    def delete_by_query(self, query: Query | dict[str, Any], *, hard: bool = False) -> dict[str, Any] | None:
        """Bulk-delete every record matching ``query`` in one call.

        Sends the filter as the body of ``DELETE /api/3/delete-with-query/<module>``
        (the route is DELETE-only and module-scoped). ``query`` is the same
        structured filter :meth:`query` accepts — a :class:`~pyfsr.query.Query`
        or a raw ``{"logic": ..., "filters": [...]}`` dict; only the filter part
        is used (pagination/sort keys are irrelevant to a delete).

        Soft-deletes by default (rows go to the recycle bin); pass ``hard=True``
        to purge permanently via ``?$hardDelete=true``.

        ⚠️ This deletes **all** matching rows server-side in one shot — there is
        no per-row confirmation. An empty/missing filter would match the whole
        module, so a query with no ``filters`` is rejected.

        Returns:
            The decoded JSON response when the API returns a body (typically a
            count/summary), otherwise ``None``.
        """
        body = query.to_body() if isinstance(query, Query) else dict(query)
        if not body.get("filters"):
            raise ValueError("delete_by_query requires a non-empty 'filters' — refusing to delete the whole module.")
        params = {"$hardDelete": "true"} if hard else None
        resp = self.client.request("DELETE", f"/api/3/delete-with-query/{self.module}", data=body, params=params)
        if resp.content and "application/json" in resp.headers.get("Content-Type", ""):
            result = resp.json()
            assert isinstance(result, dict)
            return result
        return None

    @overload
    def restore(self, ref: str, *, raw: Literal[True]) -> dict[str, Any]: ...

    @overload
    def restore(self, ref: str, *, raw: Literal[False] = ...) -> T: ...

    def restore(self, ref: str, *, raw: bool = False) -> Any:
        """Restore a soft-deleted record from the recycle bin.

        Loads the deleted record (``$showDeleted=true``), clears its
        ``deletedAt``, and PUTs it back. Returns the restored record. Note: via
        Doctrine cascade-persist this also re-attaches the record's *original*
        children, even if your current state has since diverged.
        """
        path = self._single_record_path(ref, action="restore")
        current = self.client.get(path, params={"$showDeleted": "true"})
        if not isinstance(current, dict):
            raise ValueError(f"could not load deleted record {ref!r} to restore")
        body = dict(current)
        body["deletedAt"] = None
        restored = self.client.put(path, data=body, params={"$showDeleted": "true"})
        return self._parse(restored, raw=raw)

    # -- convenience shortcuts ---------------------------------------------

    def filter(
        self,
        query: Query | dict[str, Any],
        *,
        page: int = 1,
        show_deleted: bool = False,
        raw: bool = False,
        fields: list[str] | tuple[str, ...] | None = None,
        summary: bool = False,
    ) -> Any:
        """Alias for :meth:`query` — filter records with a structured query.

        Example::

            from pyfsr import Query
            open_alerts = client.records("alerts").filter(
                Query().eq("status.itemValue", "Open").sort("createDate", "DESC").limit(50)
            )
            for alert in open_alerts:
                print(alert.name, alert.severity)
        """
        return self.query(query, page=page, show_deleted=show_deleted, raw=raw, fields=fields, summary=summary)

    def first(
        self,
        query: Query | dict[str, Any] | None = None,
        *,
        show_deleted: bool = False,
        raw: bool = False,
    ) -> T | dict[str, Any] | None:
        """Return the first matching record, or ``None`` if there are none.

        With no ``query``, returns the first record in the default server order.
        Pass a :class:`~pyfsr.query.Query` to filter and/or sort first::

            latest = client.records("alerts").first(
                Query().eq("status.itemValue", "Open").sort("createDate", "DESC")
            )
        """
        if query is None:
            page = self.list(limit=1, show_deleted=show_deleted, raw=raw)
        else:
            if isinstance(query, Query):
                query = query.limit(1)
            else:
                query = dict(query)
                query["limit"] = 1
            page = self.query(query, show_deleted=show_deleted, raw=raw)
        return page.members[0] if page.members else None

    def count(
        self,
        query: Query | dict[str, Any] | None = None,
        *,
        show_deleted: bool = False,
    ) -> int | None:
        """Return ``hydra:totalItems`` for the module (or a filtered subset).

        Fetches a single-record page (``limit=1``) — cheap, just the envelope
        metadata. Returns ``None`` when the server omits ``hydra:totalItems``.

        Example::

            n = client.records("alerts").count(Query().eq("status.itemValue", "Open"))
        """
        if query is None:
            page = self.list(limit=1, show_deleted=show_deleted, raw=True)
        else:
            if isinstance(query, Query):
                query = query.limit(1)
            else:
                query = dict(query)
                query["limit"] = 1
            page = self.query(query, show_deleted=show_deleted, raw=True)
        return page.total

    def exists(
        self,
        query: Query | dict[str, Any] | None = None,
        *,
        show_deleted: bool = False,
    ) -> bool:
        """Return ``True`` if at least one matching record exists.

        Uses a ``limit=1`` fetch — avoids pulling unnecessary data::

            if client.records("alerts").exists(Query().eq("sourceId", sid)):
                ...
        """
        if query is None:
            page = self.list(limit=1, show_deleted=show_deleted, raw=True)
        else:
            if isinstance(query, Query):
                query = query.limit(1)
            else:
                query = dict(query)
                query["limit"] = 1
            page = self.query(query, show_deleted=show_deleted, raw=True)
        return bool(page.members)

    def create_and_wait(
        self,
        data: dict[str, Any],
        *,
        playbook: str | None = None,
        playbook_uuid: str | None = None,
        timeout: float = 120,
        poll_interval: float = 3,
        resolve_picklists: bool = True,
    ) -> Any:
        """Create a record and wait for its on-create playbook to complete.

        Posts the record to ``/api/3/<module>``, then uses the playbooks API
        to poll until the triggered on-create playbook reaches a terminal state
        (finished/failed/error/cancelled/aborted).

        Useful when a record's creation triggers a downstream playbook workflow
        and you need to confirm the full pipeline completes before proceeding.

        Args:
            data: dict or model instance with the record to create.
            playbook: the playbook name to wait for. This is the on-create
                playbook that should be auto-triggered when the record is posted.
            playbook_uuid: the playbook uuid (use instead of ``playbook`` when
                you already have it).
            timeout: seconds to wait before raising :exc:`TimeoutError`
                (default 120).
            poll_interval: seconds between polls (default 3).
            resolve_picklists: if ``True`` (default), friendly picklist values
                are mapped to IRIs before posting (see :meth:`create`).

        Returns:
            A tuple of ``(record, run)`` where:
            - ``record`` is the created record (typed model or dict,
              matching the bound model type).
            - ``run`` is the shaped run dict from
              :meth:`~pyfsr.api.playbooks.PlaybooksAPI.wait_for_run`
              (``{task_id, name, status, error_message, modified, uuid, pk, source}``).

        Raises:
            TimeoutError: if the playbook run does not complete within ``timeout``.
            ValueError: if the playbook does not exist.

        Note:
            The playbook lookup is done via :meth:`~pyfsr.api.playbooks.PlaybooksAPI`
            on the same client. If the client does not have a playbooks API (unlikely
            for FortiSOAR), this method may not work; check `client.playbooks` before
            calling this method.

        Example:
            >>> # Create a record and wait for its on-create playbook to finish
            >>> record, run = client.records("alerts").create_and_wait(
            ...     {"name": "Suspicious Activity", "severity": "High"},
            ...     playbook="Auto Investigate",
            ...     timeout=120
            ... )
            >>> print(f"Alert created: {record.uuid}")
            >>> print(f"Playbook run {run['pk']}: {run['status']}")
            >>> if run["error_message"]:
            ...     print(f"Error: {run['error_message']}")
        """
        # Create the record first
        record = self.create(data, raw=False, resolve_picklists=resolve_picklists)

        # Wait for the on-create playbook
        # Use 'since' to only poll runs created after this record was posted
        import time as _time_module

        post_time = _time_module.time()
        try:
            run = self.client.playbooks.wait_for_run(
                playbook=playbook,
                playbook_uuid=playbook_uuid,
                since=post_time,
                timeout=timeout,
                poll_interval=poll_interval,
            )
        except AttributeError as e:
            raise RuntimeError(f"create_and_wait() requires the client to have a playbooks API: {e}") from e

        return (record, run)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RecordSet(module={self.module!r})"
