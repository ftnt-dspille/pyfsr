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
    """Global ES search, saved-query execution and related-record queries.

    The instance is callable: ``client.search("malware", index=["alerts"])``
    is shorthand for ``client.search.search(...)``.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Alias for :meth:`search` -- ``client.search(...)``.

        Example:
            >>> client = demo_client()
            >>> client.search("8.8.8.8", index=["alerts"])["totalItems"]
            1
        """
        return self.search(*args, **kwargs)

    def search(
        self,
        q: str | list[str],
        *,
        index: list[str] | None = None,
        size: int | None = None,
        offset: int | None = None,
        sort: str | None = None,
        search_type: str | None = None,
        modify_date_gte: int | None = None,
        modify_date_lte: int | None = None,
    ) -> dict[str, Any]:
        """Cross-module text search (``POST /api/search``), Elasticsearch-backed.

        ``q`` is one term or a list of terms -- the server requires an ARRAY
        (a bare string in the body crashes the search controller with
        ``400 TypeError``, so this method always sends ``["<term>", ...]``).
        There is NO min-length rule despite the server's 400 message: 2-char
        terms answer 200.

        ``index`` is a non-empty list of module *api names* to search across
        -- the same names ``client.records`` takes (``["alerts"]``,
        ``["incidents", "workflows"]``; list yours with
        ``client.modules.list()``). An index list that filters to empty
        (unknown module or no permission) answers ``400 "You must indicate
        which index to search on."`` Results are RBAC/team scoped
        automatically.

        ``search_type`` picks the match strategy -- the values are the UI's
        Search-type dropdown, semantics live-verified on 8.0.0-6034 and
        8.0.1-6087:

        - ``"_default"`` (UI "Broad Search") -- each term is tokenized and the
          tokens OR together ("Malware detected" matches MORE than "malware"
          alone: 3749 vs 2151 on the probe box); separate ``q`` entries AND
          together (``["malware", "ransomware"]`` narrows to 2036).
        - ``"phrase_prefix"`` (UI "Exact Text Search") -- the whole term must
          appear as a phrase, with the last word also matched as a prefix
          ("Malware detected" narrows 3749 -> 81; a bare "malw" still
          prefix-matches).
        - ``"query_string"`` (UI "Query Based Search") -- each term is a
          Lucene query string against the ES field names: ``name:malware*``
          works, ``displayName:...`` matches nothing; ``AND``/``OR``/``NOT``
          are honored (``NOT malware`` returns everything, capped at 10000
          rows); a MALFORMED query is not an error -- it answers 200 with
          ``totalItems: 0``.

        Any other value is silently treated as ``"_default"`` (verified with a
        bogus type on both builds). Optional: ``size``/``offset`` paging,
        ``sort`` (default ``_score``), and ``modify_date_gte``/
        ``modify_date_lte`` (epoch).

        Returns the measured ``{totalItems, results, context_code}`` shape
        where each ``results`` entry is a raw ES hit (``_index``, ``_id``,
        ``_score``, ``_source``, ``highlight``). ``_source`` is a VARIABLE
        projection: which fields it carries differs between calls (one
        capture had ``name``/``severity``, the next only ``@id``/``@type``/
        ``uuid``/``displayName`` and the dates), so treat ``@id`` (e.g.
        ``/api/3/alerts/<uuid>``) as the only reliable handle and re-fetch
        the full record from it.

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
        if index is None:
            raise ValueError(
                'search() needs index=[<module api names>], e.g. index=["alerts"] '
                'or index=["incidents", "workflows"] -- the same names '
                "client.records() takes (list yours with client.modules.list())"
            )
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
