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

    Pass ``model=...`` to force a specific model, ``typed=False`` to get raw
    dicts back, or ``raw=True`` on an individual call for one-off dicts.

    The ``raw=True`` overloads narrow the return type to ``dict[str, Any]``
    so type checkers know exactly which shape they're getting.
    """

    def __init__(
        self,
        client: FortiSOAR,
        module: str,
        *,
        model: type[T] | None = None,
        typed: bool = True,
    ) -> None:
        self.client = client
        self.module = module
        if not typed:
            self.model: type[T] | None = None
        else:
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
        page_obj = self.list(
            limit=limit, page=page, show_deleted=show_deleted, params=query, raw=raw
        )
        if fields or summary:
            return project(page_obj, fields=fields, summary=summary)
        return page_obj

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
    def upsert(
        self,
        data: dict[str, Any],
        *,
        raw: Literal[True],
        resolve_picklists: bool = ...,
    ) -> dict[str, Any]: ...

    @overload
    def upsert(
        self,
        data: dict[str, Any],
        *,
        raw: Literal[False] = ...,
        resolve_picklists: bool = ...,
    ) -> T: ...

    def upsert(
        self,
        data: dict[str, Any],
        *,
        raw: bool = False,
        resolve_picklists: bool = True,
    ) -> Any:
        """Insert-or-update one record via ``POST /api/3/upsert/<module>``.

        FortiSOAR matches an existing row by the record's natural key (its
        ``uuid`` / ``@id`` when present, else the module's unique field) and
        updates it, otherwise creates a new one. ``data`` may be a dict or a
        model instance; friendly picklist values are mapped to IRIs first —
        pass ``resolve_picklists=False`` to skip that (see :meth:`create`).
        """
        if isinstance(data, BaseRecord):
            data = data.to_dict(exclude_none=True)
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        return self._parse(self.client.post(f"/api/3/upsert/{self.module}", data=data), raw=raw)

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
        (e.g. ``workflow_collections``) the server cascades to children. There is
        deliberately no bulk path — an empty/blank ``ref`` raises.
        """
        path = self._single_record_path(ref, action="delete")
        params = {"$hardDelete": "true"} if hard else None
        self.client.delete(path, params=params)

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

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RecordSet(module={self.module!r})"
