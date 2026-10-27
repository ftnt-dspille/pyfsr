"""Global search, saved queries and related-record queries (``/api/search``, ``/api/query``).

Cross-module Elasticsearch text search and execution of saved queries. Accessed
as ``client.search``. (Ad-hoc per-module queries are built with
:class:`~pyfsr.query.Query` and run via ``client.query``/record sets.)

Example:
    >>> client = demo_client()
    >>> results = client.search.search(["8.8.8.8"], index=["alerts", "incidents"])
    >>> results["totalItems"]
    1
    >>> qid = "7d245801-38d7-4400-9453-7bf7c42b7353"
    >>> client.search.run_persisted("threat_intel_feeds", qid, limit=50)["hydra:totalItems"]
    1
"""

from __future__ import annotations

from typing import Any

from ..exceptions import ResourceNotFoundError
from .base import BaseAPI


class SearchAPI(BaseAPI):
    """Global ES search, saved-query execution and related-record queries."""

    def search(
        self,
        q: str | list[str],
        *,
        index: list[str],
        size: int | None = None,
        offset: int | None = None,
        sort: str | None = None,
        search_type: str | None = None,
        modify_date_gte: int | None = None,
        modify_date_lte: int | None = None,
    ) -> dict[str, Any]:
        """Cross-module text search (``POST /api/search``), Elasticsearch-backed.

        ``q`` is one term or a list of terms -- the server requires an ARRAY
        (a bare string in the body crashes the PHP controller with
        ``400 TypeError``, so this method always sends ``["<term>", ...]``).
        There is NO min-length rule despite the server's 400 message: 2-char
        terms answer 200. ``index`` is the list of module api names to search;
        an index list that filters to empty (unknown module or no permission)
        answers ``400 "You must indicate which index to search on."``.
        Results are RBAC/team scoped automatically. Optional: ``size``/
        ``offset`` paging, ``sort``, ``search_type``, and
        ``modify_date_gte``/``modify_date_lte`` (epoch).

        Returns the measured ``{totalItems, results, context_code}`` shape
        where each ``results`` entry is a raw ES hit (``_index``, ``_id``,
        ``_score``, ``_source``).

        Example:
            >>> client = demo_client()
            >>> results = client.search.search("8.8.8.8", index=["alerts"])
            >>> results["totalItems"]
            1
            >>> results["results"][0]["_source"]["severity"]
            'Low'
        """
        terms = [q] if isinstance(q, str) else list(q)
        if not terms or not all(isinstance(t, str) and t.strip() for t in terms):
            raise ValueError("search() requires at least one non-empty search term (the server 400s an empty q list)")
        body: dict[str, Any] = {"q": terms, "index": list(index)}
        for key, val in (
            ("size", size),
            ("offset", offset),
            ("sort", sort),
            ("searchType", search_type),
            ("modifyDateGte", modify_date_gte),
            ("modifyDateLte", modify_date_lte),
        ):
            if val is not None:
                body[key] = val
        return self.client.post("/api/search", data=body)

    def run_persisted(
        self,
        collection: str,
        query_id: str,
        *,
        limit: int | None = None,
        page: int | None = None,
        orderby: str | None = None,
    ) -> dict[str, Any]:
        """Execute a saved query against ``collection``.

        FortiSOAR has no "run saved query" route (``POST
        /api/query/{collection}/{query_id}`` 404s on 8.0.0 and 8.0.1). The UI
        loads the saved query and posts its body to ``POST
        /api/query/{collection}``; this does the same. ``query_id`` is a
        ``/api/3/user_queries`` uuid or, failing that, a
        ``/api/3/system_queries`` (dataset) uuid.

        ``limit``/``page`` go out as ``$limit``/``$page`` (the stored body's
        ``limit``/``page`` are ignored by the server). ``orderby`` (``"+name"``
        or ``"-name"``) replaces the stored ``sort``.

        Example:
            >>> client = demo_client()
            >>> qid = "7d245801-38d7-4400-9453-7bf7c42b7353"
            >>> client.search.run_persisted("threat_intel_feeds", qid, limit=50)["hydra:totalItems"]
            1
        """
        body = dict(self._saved_query(query_id).get("query") or {})
        if orderby is not None:
            direction = "DESC" if orderby.startswith("-") else "ASC"
            body["sort"] = [{"field": orderby.lstrip("+-"), "direction": direction}]
        return self.client.post(f"/api/query/{collection}", data=body, params=_paging(limit, page))

    def query_related(
        self,
        collection: str,
        record_id: str,
        field: str,
        *,
        filters: list[dict[str, Any]] | None = None,
        logic: str = "AND",
        limit: int | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        """Query the records linked to one record (``POST /api/query/{collection}/{id}/{field}``).

        ``field`` is a to-many relationship on ``collection`` (e.g. an alert's
        ``indicators``); ``filters``/``logic`` narrow the linked records with
        the usual Query grammar. The server needs both keys in the body, so an
        empty ``filters`` is sent rather than omitted.

        Example:
            >>> client = demo_client()
            >>> aid = "9f0eb603-ac1e-41c3-b47b-444589beed39"
            >>> client.search.query_related("alerts", aid, "indicators")["hydra:totalItems"]
            1
        """
        body = {"logic": logic, "filters": list(filters or [])}
        return self.client.post(f"/api/query/{collection}/{record_id}/{field}", data=body, params=_paging(limit, page))

    def _saved_query(self, query_id: str) -> dict[str, Any]:
        try:
            return self.client.get(f"/api/3/user_queries/{query_id}")
        except ResourceNotFoundError:
            return self.client.get(f"/api/3/system_queries/{query_id}")


def _paging(limit: int | None, page: int | None) -> dict[str, int] | None:
    params = {k: v for k, v in (("$limit", limit), ("$page", page)) if v is not None}
    return params or None
