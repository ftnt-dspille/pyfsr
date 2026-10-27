"""Global search, saved queries and related-record queries (``/api/search``, ``/api/query``).

Cross-module Elasticsearch text search and execution of saved queries. Accessed
as ``client.search`` -- the instance is callable, so ``client.search("term",
index=["alerts"])`` works directly. Hits are hydrated into typed records by
default; pass ``hydrate=False`` for the raw Elasticsearch hits. (Ad-hoc
per-module queries are built with :class:`~pyfsr.query.Query` and run via
``client.query``/record sets.)

Example:
    >>> client = demo_client()
    >>> results = client.search(["8.8.8.8"], index=["alerts", "incidents"])
    >>> results.total
    1
    >>> results[0].name
    'Response Capture Test Alert'
    >>> qid = "7d245801-38d7-4400-9453-7bf7c42b7353"
    >>> client.search.run_persisted("threat_intel_feeds", qid, limit=50)["hydra:totalItems"]
    1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..exceptions import ResourceNotFoundError
from .base import BaseAPI

__all__ = ["GlobalSearchResult", "SearchAPI"]


@dataclass
class GlobalSearchResult:
    """Hydrated global-search response.

    Built by :meth:`SearchAPI.search` (the default). Iteration, ``len()`` and
    indexing go over the hydrated :attr:`records`; the raw Elasticsearch hits
    stay available on :attr:`hits` (same order as the wire response), and
    :attr:`total` is the match count across ALL pages, not just this one.

    >>> result = GlobalSearchResult.from_response(
    ...     {"totalItems": 1, "results": [{"_index": "cyops_alerts"}], "context_code": 200},
    ...     records=["<Alert>"],
    ...     skipped=0,
    ... )
    >>> (result.total, len(result), result[0])
    (1, 1, '<Alert>')
    """

    #: Total matches across all pages (the wire ``totalItems``).
    total: int
    #: Full records hydrated from each hit's ``@id``, in hit order. Hits whose
    #: record no longer resolves (deleted since indexing, or not
    #: permission-visible) are omitted -- see :attr:`skipped`.
    records: list[Any]
    #: The raw Elasticsearch hits, exactly as the wire response carried them
    #: (``_index``, ``_id``, ``_score``, ``_source``, ``highlight``).
    hits: list[dict[str, Any]]
    #: How many hits failed to hydrate (still visible in :attr:`hits`).
    skipped: int = 0
    #: The full wire response, untouched.
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(
        cls,
        raw: dict[str, Any],
        records: list[Any],
        skipped: int,
    ) -> GlobalSearchResult:
        return cls(
            total=raw.get("totalItems", len(records)),
            records=list(records),
            hits=list(raw.get("results") or []),
            skipped=skipped,
            raw=raw,
        )

    def __iter__(self) -> Any:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, position: int) -> Any:
        return self.records[position]

    def __repr__(self) -> str:
        return f"<GlobalSearchResult total={self.total} records={len(self.records)} skipped={self.skipped}>"


class SearchAPI(BaseAPI):
    """Global ES search, saved-query execution and related-record queries.

    The instance is callable: ``client.search("malware", index=["alerts"])``
    is shorthand for ``client.search.search(...)``.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> GlobalSearchResult | dict[str, Any]:
        """Alias for :meth:`search` -- ``client.search(...)``.

        Example:
            >>> client = demo_client()
            >>> client.search("8.8.8.8", index=["alerts"]).total
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
        hydrate: bool = True,
    ) -> GlobalSearchResult | dict[str, Any]:
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

        By default the hits are HYDRATED into typed records: each hit's
        ``_source['@id']`` (e.g. ``/api/3/alerts/<uuid>`` -- the one reliable
        handle, since the ``_source`` projection otherwise varies between
        calls) is fetched concurrently via :meth:`~pyfsr.records.RecordSet.get_many`,
        and the response becomes a :class:`GlobalSearchResult` -- iterate it
        for the records, ``.total`` for the match count across ALL pages,
        ``.hits`` for the raw hits. A hit whose record no longer resolves
        (deleted since indexing, or not permission-visible) is skipped from
        ``.records`` (``.skipped`` counts it) rather than failing the search.
        Hydration costs one GET per hit (the server's default page size is
        30) and reuses :meth:`~pyfsr.records.RecordSet.get_many`'s bounded
        thread pool -- a warm page of 6 hydrates in well under a second
        (measured 0.55s vs 1.58s serial). Note the FIRST picklist-resolving
        read in a process (hydration included) warms pyfsr's picklist
        catalog once (~5s on the 8.0.0 probe box); later reads are warm.
        Pass ``hydrate=False`` to skip hydration and get the raw measured
        wire dict ``{totalItems, results, context_code}`` where each
        ``results`` entry is the raw ES hit (``_index``, ``_id``, ``_score``,
        ``_source``, ``highlight``).

        Example:
            >>> client = demo_client()
            >>> results = client.search("8.8.8.8", index=["alerts"])
            >>> results.total
            1
            >>> results[0].name
            'Response Capture Test Alert'
            >>> raw = client.search("8.8.8.8", index=["alerts"], hydrate=False)
            >>> (raw["totalItems"], raw["results"][0]["_source"]["severity"])
            (1, 'Low')
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
        raw = self.client.post("/api/search", data=body)
        if not hydrate:
            return raw
        records, skipped = self._hydrate_hits(raw.get("results") or [])
        return GlobalSearchResult.from_response(raw, records, skipped)

    def _hydrate_hits(self, hits: list[dict[str, Any]]) -> tuple[list[Any], int]:
        """Fetch the full record behind each ES hit, grouped per module.

        The handle is ``_source['@id']`` (``/api/3/<module>/<uuid>``); hits
        without one fall back to ``_index`` (stripped of its ``cyops_``
        prefix) plus ``_id``. Fetches run through
        :meth:`~pyfsr.records.RecordSet.get_many` -- a bounded thread pool,
        ordered results, and ``on_error="none"`` so one stale hit cannot fail
        the whole search.
        """
        slots: dict[int, Any] = {}
        per_module: dict[str, list[tuple[int, str]]] = {}
        for position, hit in enumerate(hits):
            module, ref = self._hit_ref(hit)
            if module and ref:
                per_module.setdefault(module, []).append((position, ref))
        for module, wanted in per_module.items():
            fetched = self.client.records(module).get_many([ref for _, ref in wanted], on_error="none")
            for (position, _ref), record in zip(wanted, fetched, strict=True):
                if record is not None:
                    slots[position] = record
        records = [slots[i] for i in range(len(hits)) if i in slots]
        return records, len(hits) - len(records)

    @staticmethod
    def _hit_ref(hit: dict[str, Any]) -> tuple[str | None, str | None]:
        """Derive ``(module, record ref)`` from one ES hit (either handle)."""
        source = hit.get("_source") or {}
        iri = source.get("@id")
        if isinstance(iri, str) and iri.startswith("/api/3/"):
            parts = iri.strip("/").split("/")
            if len(parts) >= 4:
                return parts[2], iri
        index, record_id = hit.get("_index"), source.get("uuid") or hit.get("_id")
        if isinstance(index, str) and index and isinstance(record_id, str) and record_id:
            module = index[len("cyops_") :] if index.startswith("cyops_") else index
            return module, f"/api/3/{module}/{record_id}"
        return None, None

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
