"""Saved datasets — ``/api/3/system_queries``.

A *system query* is a named, module-scoped filter — what the UI calls a
**dataset**. Two reasons to care:

1. Saved views: run one with :meth:`~pyfsr.api.search.SearchAPI.run_persisted`.
2. **A dataset on ``threat_intel_feeds`` is a TAXII collection.** The id served
   at ``/api/taxii/1/collections/<id>/objects`` *is* the dataset uuid, so
   creating one here is how you publish an outgoing threat feed that a FortiGate
   (or any TAXII client) can pull. See :class:`~pyfsr.api.taxii.TaxiiAPI` and
   ``examples/taxii_threat_feed_to_fortigate.py``.

.. warning::
   **A filter without ``type``, or a query body without ``logic``, is silently
   ignored** — FortiSOAR returns *every* record instead of raising. Build filters
   with :meth:`SystemQueriesAPI.filter` and bodies with :meth:`SystemQueriesAPI.query`
   (or pass :class:`~pyfsr.models.QueryDefinition`), which always emit both.

Accessed as ``client.system_queries``.

Example:
    >>> client = demo_client()
    >>> datasets = client.system_queries.list()
    >>> datasets[0]["name"]
    'Block List (IP Address)'
    >>> datasets[0].module
    'threat_intel_feeds'
"""

from __future__ import annotations

from typing import Any

from ..models import QueryDefinition, QueryFilter, SystemQuery
from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/system_queries"


class SystemQueriesAPI(BaseAPI):
    """Create, read, and run saved datasets (``/api/3/system_queries``)."""

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def filter(field: str, operator: str, value: Any, *, type: str | None = None) -> QueryFilter:
        """Build one filter condition, inferring ``type`` when you don't pass it.

        ``type`` is required by the appliance in practice — a filter without it
        is dropped silently. Inference: IRI strings (``/api/3/...``) → ``object``
        (picklists, lookups), everything else → ``primitive``. Pass ``type``
        explicitly for dates (``datetime``).

        Example:
            >>> SystemQueriesAPI.filter("confidence", "gte", 70).type
            'primitive'
            >>> SystemQueriesAPI.filter(
            ...     "reputation", "eq", "/api/3/picklists/7074e547"
            ... ).type
            'object'
        """
        if type is None:
            type = "object" if isinstance(value, str) and value.startswith("/api/") else "primitive"
        return QueryFilter(field=field, operator=operator, value=value, type=type)

    @staticmethod
    def query(
        filters: list[QueryFilter | dict[str, Any]] | None = None,
        *,
        logic: str = "AND",
        limit: int | None = 30,
        **extra: Any,
    ) -> QueryDefinition:
        """Build a query body with ``logic`` always set.

        Example:
            >>> q = SystemQueriesAPI.query([SystemQueriesAPI.filter("confidence", "gte", 70)])
            >>> q.logic, len(q.filters)
            ('AND', 1)
        """
        norm: list[QueryFilter] = [
            f if isinstance(f, QueryFilter) else QueryFilter.model_validate(f) for f in (filters or [])
        ]
        return QueryDefinition(logic=logic, filters=norm, limit=limit, **extra)

    def model_iri(self, module: str) -> str:
        """Resolve a module slug to its ``model_metadatas`` IRI (needed by :meth:`create`).

        Example:
            >>> client = demo_client()
            >>> client.system_queries.model_iri("threat_intel_feeds")
            '/api/3/model_metadatas/acbac353-3593-41d2-af46-67951cfab083'
        """
        r = self.client.get("/api/3/model_metadatas", params={"$limit": 300})
        for m in extract_members(r):
            if m.get("type") == module:
                return m["@id"]
        raise ValueError(f"no model_metadata for module {module!r}")

    # ------------------------------------------------------------------- CRUD
    def list(self, *, module: str | None = None, limit: int = 200) -> list[SystemQuery]:
        """List datasets, optionally only those targeting ``module``.

        Example:
            >>> client = demo_client()
            >>> [d["name"] for d in client.system_queries.list(module="threat_intel_feeds")]
            ['Block List (IP Address)']
        """
        r = self.client.get(_BASE, params={"$limit": limit})
        out = [SystemQuery.model_validate(q) for q in extract_members(r)]
        if module is not None:
            out = [q for q in out if q.module == module]
        return out

    def get(self, uuid: str) -> SystemQuery:
        """Fetch one dataset by uuid.

        Example:
            >>> client = demo_client()
            >>> client.system_queries.get("7d245801-38d7-4400-9453-7bf7c42b7353")["name"]
            'Block List (IP Address)'
        """
        return SystemQuery.model_validate(self.client.get(f"{_BASE}/{uuid}", params={"$relationships": "true"}))

    def find_by_name(self, name: str, *, module: str | None = None) -> SystemQuery | None:
        """Return the dataset called ``name`` (optionally scoped to ``module``), else None.

        Example:
            >>> client = demo_client()
            >>> client.system_queries.find_by_name("Block List (IP Address)")["uuid"]
            '7d245801-38d7-4400-9453-7bf7c42b7353'
        """
        for q in self.list(module=module):
            if q.name == name:
                return q
        return None

    def create(
        self,
        *,
        name: str,
        module: str,
        query: QueryDefinition | dict[str, Any] | None = None,
        filters: list[QueryFilter | dict[str, Any]] | None = None,
        **fields: Any,
    ) -> SystemQuery:
        """Create a dataset targeting ``module``.

        Pass either a built ``query`` or bare ``filters`` (wrapped via
        :meth:`query`, so ``logic`` is always set). ``module`` is a slug — the
        ``model_metadatas`` IRI is resolved for you.

        Example:
            >>> client = demo_client()
            >>> ds = client.system_queries.create(
            ...     name="Block List (IP Address)",
            ...     module="threat_intel_feeds",
            ...     filters=[SystemQueriesAPI.filter("confidence", "gte", 70)],
            ... )
            >>> ds["name"]
            'Block List (IP Address)'
        """
        if query is None:
            query = self.query(filters)
        elif isinstance(query, dict):
            query = QueryDefinition.model_validate(query)
        body: dict[str, Any] = {
            "name": name,
            "models": self.model_iri(module),
            "query": query.model_dump(exclude_none=True),
            **fields,
        }
        return SystemQuery.model_validate(self.client.post(_BASE, data=body))

    def ensure(
        self,
        *,
        name: str,
        module: str,
        query: QueryDefinition | dict[str, Any] | None = None,
        filters: list[QueryFilter | dict[str, Any]] | None = None,
        **fields: Any,
    ) -> SystemQuery:
        """Idempotent :meth:`create` — reuse the dataset named ``name`` if present.

        Example:
            >>> client = demo_client()
            >>> client.system_queries.ensure(
            ...     name="Block List (IP Address)", module="threat_intel_feeds"
            ... )["uuid"]
            '7d245801-38d7-4400-9453-7bf7c42b7353'
        """
        found = self.find_by_name(name, module=module)
        if found is not None:
            return found
        return self.create(name=name, module=module, query=query, filters=filters, **fields)

    def update(self, uuid: str, **fields: Any) -> SystemQuery:
        """Partially update a dataset (``PUT``). Pass only the keys to change."""
        if isinstance(fields.get("query"), QueryDefinition):
            fields["query"] = fields["query"].model_dump(exclude_none=True)
        return SystemQuery.model_validate(self.client.put(f"{_BASE}/{uuid}", data=fields))

    def delete(self, uuid: str) -> None:
        """Delete a dataset."""
        self.client.delete(f"{_BASE}/{uuid}")

    # -------------------------------------------------------------------- run
    def run(self, uuid: str, *, limit: int | None = None, page: int | None = None) -> dict[str, Any]:
        """Execute a saved dataset and return the matching records.

        Delegates to :meth:`~pyfsr.api.search.SearchAPI.run_persisted`, resolving
        the dataset's own module for you.
        """
        ds = self.get(uuid)
        module = ds.module
        if not module:
            raise ValueError(f"dataset {uuid} has no resolvable module")
        return self.client.search.run_persisted(module, uuid, limit=limit, page=page)
