"""Global search + persisted queries (``/api/search``, ``/api/query/{collection}/{queryId}``).

Cross-module Elasticsearch text search and execution of saved queries. Accessed
as ``client.search``. (Ad-hoc per-module queries are built with
:class:`~pyfsr.query.Query` and run via ``client.query``/record sets.)

Example:
    >>> client = demo_client()
    >>> results = client.search.search("8.8.8.8", index=["alerts", "incidents"])
    >>> results["hits"]["total"]
    1
    >>> qid = "6f1c9e2a-6b7a-4b0a-9a1e-2f6a5c9b3d10"
    >>> client.search.run_persisted("alerts", qid, limit=50)["hydra:totalItems"]
    1
"""

from __future__ import annotations

from typing import Any

from .base import BaseAPI


class SearchAPI(BaseAPI):
    """Global ES search and persisted-query execution."""

    def search(
        self,
        q: str,
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

        ``q`` is the query string (**min 3 chars**, enforced server-side);
        ``index`` is the list of module api names to search. Results are RBAC/team
        scoped automatically. Optional: ``size``/``offset`` paging, ``sort``,
        ``search_type``, and ``modify_date_gte``/``modify_date_lte`` (epoch ms).

        Example:
            >>> client = demo_client()
            >>> results = client.search.search("8.8.8.8", index=["alerts"])
            >>> results["hits"]["hits"][0]["_source"]["severity"]
            'Low'
        """
        if not isinstance(q, str) or len(q.strip()) < 3:
            raise ValueError("search() requires a query string of at least 3 characters")
        body: dict[str, Any] = {"q": q, "index": list(index)}
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
        """Execute a saved query (``POST /api/query/{collection}/{query_id}``).

        Runs a Query previously saved via ``POST /api/3/user_queries`` (or a
        system query under ``/api/3/system_queries``). ``collection`` is the
        module the query targets. Override paging with ``limit``/``page`` and
        ordering with ``orderby`` (e.g. ``"+name"``).

        Example:
            >>> client = demo_client()
            >>> qid = "6f1c9e2a-6b7a-4b0a-9a1e-2f6a5c9b3d10"
            >>> client.search.run_persisted("alerts", qid, limit=50)["hydra:totalItems"]
            1
        """
        body: dict[str, Any] = {}
        if limit is not None:
            body["$limit"] = limit
        if page is not None:
            body["$page"] = page
        if orderby is not None:
            body["$orderby"] = orderby
        return self.client.post(f"/api/query/{collection}/{query_id}", data=body)
