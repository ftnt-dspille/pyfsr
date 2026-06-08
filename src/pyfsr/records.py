"""Generic CRUD for any FortiSOAR module.

``client.records("incidents")`` returns a :class:`RecordSet` bound to that
module, so callers don't hand-build ``/api/3/<module>`` URLs or unwrap Hydra
envelopes. This is the generic counterpart to the typed ``client.alerts`` API
and works for every module on the appliance.

    incidents = client.records("incidents")
    inc = incidents.get("0d2c…")                     # by uuid
    page = incidents.query(Query().eq("status.itemValue", "Open").limit(50))
    for rec in incidents.iterate(Query().gt("createDate", ts)):
        ...
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from .pagination import HydraPage, paginate
from .query import Query

if TYPE_CHECKING:
    from .client import FortiSOAR


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


class RecordSet:
    """CRUD operations scoped to a single FortiSOAR module."""

    def __init__(self, client: FortiSOAR, module: str) -> None:
        self.client = client
        self.module = module

    # -- reads --------------------------------------------------------------
    def get(
        self,
        ref: str,
        *,
        relationships: bool = False,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch one record by uuid, ``module:uuid`` shorthand, or IRI."""
        path = resolve_record_path(self.module, ref)
        query = dict(params or {})
        if relationships:
            query["$relationships"] = "true"
        return self.client.get(path, params=query or None)

    def list(
        self,
        *,
        limit: int = 30,
        page: int = 1,
        params: dict[str, Any] | None = None,
    ) -> HydraPage:
        """List records via ``GET /api/3/<module>`` (one page)."""
        query = dict(params or {})
        query["$limit"] = limit
        query["$page"] = page
        resp = self.client.get(f"/api/3/{self.module}", params=query)
        return HydraPage.from_response(resp, page=page, limit=limit)

    def search(
        self,
        term: str = "",
        *,
        limit: int = 30,
        page: int = 1,
        params: dict[str, Any] | None = None,
    ) -> HydraPage:
        """Free-text search via ``GET /api/3/<module>?$search=<term>``."""
        query = dict(params or {})
        if term:
            query["$search"] = term
        return self.list(limit=limit, page=page, params=query)

    def query(self, query: Query | dict[str, Any], *, page: int = 1) -> HydraPage:
        """Run a structured query via ``POST /api/query/<module>``.

        FortiSOAR paginates this endpoint with the ``$limit``/``$page``/``$search``
        *query params* — the ``limit``/``search`` keys in the body are ignored — so
        they are lifted out of the body and sent as params.
        """
        body, params = self._split_query(query, page=page)
        resp = self.client.post(f"/api/query/{self.module}", data=body, params=params)
        return HydraPage.from_response(resp, page=page, limit=params.get("$limit"))

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

    def iterate(
        self,
        query: Query | dict[str, Any] | None = None,
        *,
        page_size: int = 100,
        max_records: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Lazily yield every matching record across all pages.

        Uses the structured query endpoint when ``query`` is given, otherwise a
        plain list. The page size overrides any ``limit`` on the query.
        """
        if query is None:

            def fetch(page: int) -> Any:
                return self.client.get(
                    f"/api/3/{self.module}",
                    params={"$limit": page_size, "$page": page},
                )
        else:

            def fetch(page: int) -> Any:
                body, params = self._split_query(query, page=page, page_size=page_size)
                return self.client.post(f"/api/query/{self.module}", data=body, params=params)

        yield from paginate(fetch, page_size=page_size, max_records=max_records)

    # -- writes -------------------------------------------------------------
    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a record via ``POST /api/3/<module>``."""
        return self.client.post(f"/api/3/{self.module}", data=data)

    def update(self, ref: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update a record via ``PUT /api/3/<module>/<uuid>``."""
        return self.client.put(resolve_record_path(self.module, ref), data=data)

    def delete(self, ref: str) -> None:
        """Soft-delete a record via ``DELETE /api/3/<module>/<uuid>``.

        Hard-delete / recycle-bin restore land in a later phase (safe-delete).
        """
        self.client.delete(resolve_record_path(self.module, ref))

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RecordSet(module={self.module!r})"
