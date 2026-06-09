"""Fluent builder for the FortiSOAR query DSL.

FortiSOAR's ad-hoc query endpoint (``POST /api/query/{module}``) takes a body of
the shape::

    {
      "logic": "AND",
      "filters": [
        {"field": "severity.itemValue", "operator": "eq", "value": "Critical"},
        {"logic": "OR", "filters": [ ... ]}        # nested group
      ],
      "sort": [{"field": "createDate", "direction": "DESC"}],
      "__selectFields": ["uuid", "name", "severity"],
      "limit": 30
    }

``Query`` builds that dict so callers don't hand-assemble it. Every mutator
returns ``self`` for chaining::

    Query().eq("status.itemValue", "Open").gt("createDate", ts).sort("createDate").limit(50)
"""

from __future__ import annotations

from typing import Any

# Authoritative leaf operators (FortiSOAR OpenAPI QueryBody schema).
OPERATORS = frozenset(
    {
        "eq",
        "neq",
        "lt",
        "lte",
        "gt",
        "gte",
        "in",
        "nin",
        "like",
        "notlike",
        "contains",
        "exists",
        "isnull",
        # Trigger-condition operators (used by playbook start filters / visual
        # editor wire shapes). ``changed`` is value-less; ``in_all`` takes a list.
        "changed",
        "in_all",
    }
)


class Query:
    """Fluent builder for a FortiSOAR query body.

    Args:
        logic: Top-level logical join for this group's filters (``"AND"`` or ``"OR"``).
    """

    def __init__(self, logic: str = "AND") -> None:
        logic = logic.upper()
        if logic not in ("AND", "OR"):
            raise ValueError(f"logic must be 'AND' or 'OR', got {logic!r}")
        self._logic = logic
        self._filters: list[dict[str, Any]] = []
        self._sort: list[dict[str, str]] = []
        self._select: list[str] | None = None
        self._ignore: list[str] | None = None
        self._limit: int | None = None
        self._page: int | None = None
        self._search: str | None = None

    # -- leaf filters -------------------------------------------------------
    def where(self, field: str, operator: str, value: Any = None) -> Query:
        """Add a raw leaf filter. Prefer the named helpers (``eq``, ``in_`` …)."""
        if operator not in OPERATORS:
            raise ValueError(
                f"unknown operator {operator!r}; valid: {', '.join(sorted(OPERATORS))}"
            )
        leaf: dict[str, Any] = {"field": field, "operator": operator}
        if value is not None:
            leaf["value"] = value
        self._filters.append(leaf)
        return self

    def eq(self, field: str, value: Any) -> Query:
        return self.where(field, "eq", value)

    def neq(self, field: str, value: Any) -> Query:
        return self.where(field, "neq", value)

    def lt(self, field: str, value: Any) -> Query:
        return self.where(field, "lt", value)

    def lte(self, field: str, value: Any) -> Query:
        return self.where(field, "lte", value)

    def gt(self, field: str, value: Any) -> Query:
        return self.where(field, "gt", value)

    def gte(self, field: str, value: Any) -> Query:
        return self.where(field, "gte", value)

    def in_(self, field: str, values: list[Any]) -> Query:
        return self.where(field, "in", list(values))

    def nin(self, field: str, values: list[Any]) -> Query:
        return self.where(field, "nin", list(values))

    def like(self, field: str, value: str) -> Query:
        return self.where(field, "like", value)

    def notlike(self, field: str, value: str) -> Query:
        return self.where(field, "notlike", value)

    def contains(self, field: str, value: Any) -> Query:
        return self.where(field, "contains", value)

    def exists(self, field: str, value: bool = True) -> Query:
        return self.where(field, "exists", value)

    def isnull(self, field: str, value: bool = True) -> Query:
        return self.where(field, "isnull", value)

    def changed(self, field: str) -> Query:
        """Match records whose ``field`` changed (trigger-condition operator).

        Value-less — only meaningful inside a playbook start/update trigger filter.
        """
        return self.where(field, "changed")

    def in_all(self, field: str, values: list[Any]) -> Query:
        """Match records whose ``field`` contains *all* of ``values``.

        Trigger-condition operator (distinct from ``in``, which is any-of).
        """
        return self.where(field, "in_all", list(values))

    def group(self, query: Query) -> Query:
        """Nest another ``Query`` as a sub-group (its own logic + filters)."""
        self._filters.append({"logic": query._logic, "filters": query._build_filters()})
        return self

    # -- shaping ------------------------------------------------------------
    def sort(self, field: str, direction: str = "DESC") -> Query:
        direction = direction.upper()
        if direction not in ("ASC", "DESC"):
            raise ValueError(f"direction must be 'ASC' or 'DESC', got {direction!r}")
        self._sort.append({"field": field, "direction": direction})
        return self

    def select(self, *fields: str) -> Query:
        """Allowlist the fields returned per record (``__selectFields``)."""
        if self._ignore is not None:
            raise ValueError("select() and ignore() are mutually exclusive")
        self._select = list(fields)
        return self

    def ignore(self, *fields: str) -> Query:
        """Denylist fields stripped from each record (``__ignoreFields``)."""
        if self._select is not None:
            raise ValueError("select() and ignore() are mutually exclusive")
        self._ignore = list(fields)
        return self

    def limit(self, n: int) -> Query:
        self._limit = n
        return self

    def page(self, n: int) -> Query:
        self._page = n
        return self

    def search(self, term: str) -> Query:
        self._search = term
        return self

    # -- output -------------------------------------------------------------
    def _build_filters(self) -> list[dict[str, Any]]:
        return list(self._filters)

    def to_body(self) -> dict[str, Any]:
        """Render the query as the dict POSTed to ``/api/query/{module}``."""
        body: dict[str, Any] = {"logic": self._logic, "filters": self._build_filters()}
        if self._sort:
            body["sort"] = self._sort
        if self._select is not None:
            body["__selectFields"] = self._select
        if self._ignore is not None:
            body["__ignoreFields"] = self._ignore
        if self._limit is not None:
            body["limit"] = self._limit
        if self._search is not None:
            body["search"] = self._search
        return body

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Query(logic={self._logic!r}, filters={len(self._filters)})"
