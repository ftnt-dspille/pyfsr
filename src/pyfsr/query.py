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

The builder validates operators and value-arity against the operator knowledge
base (:mod:`pyfsr.query_models`). Pass ``module=`` to additionally validate field
paths against the shipped field/relationship KB (:mod:`pyfsr.fields`)::

    Query(module="alerts").eq("severity.itemValue", "Critical")  # field path checked

The validated, typed form is available via :meth:`Query.model` (a
:class:`~pyfsr.query_models.QueryBody`); :meth:`Query.to_body` renders the dict.
"""

from __future__ import annotations

from typing import Any

from .fields import validate_field_path
from .query_models import (
    OPERATOR_SPECS,
    OPERATORS,
    QueryBody,
    operator_error,
    validate_leaf_value,
)

__all__ = ["Query", "OPERATORS", "OPERATOR_SPECS"]


class Query:
    """Fluent builder for a FortiSOAR query body.

    Args:
        logic: Top-level logical join for this group's filters (``"AND"`` or ``"OR"``).
        module: Optional module name; when set, field paths are validated against
            the shipped field/relationship knowledge base.
    """

    def __init__(self, logic: str = "AND", *, module: str | None = None) -> None:
        logic = logic.upper()
        if logic not in ("AND", "OR"):
            raise ValueError(f"logic must be 'AND' or 'OR', got {logic!r}")
        self._logic = logic
        self._module = module
        self._filters: list[dict[str, Any]] = []
        self._sort: list[dict[str, str]] = []
        self._select: list[str] | None = None
        self._ignore: list[str] | None = None
        self._limit: int | None = None
        self._page: int | None = None
        self._search: str | None = None

    def _check_field(self, field: str) -> None:
        if self._module is not None:
            validate_field_path(self._module, field)

    # -- leaf filters -------------------------------------------------------
    def where(self, field: str, operator: str, value: Any = None) -> Query:
        """Add a raw leaf filter. Prefer the named helpers (``eq``, ``in_`` …)."""
        if operator not in OPERATORS:
            raise ValueError(operator_error(operator))
        validate_leaf_value(operator, value)
        self._check_field(field)
        leaf: dict[str, Any] = {"field": field, "operator": operator}
        if value is not None:
            leaf["value"] = value
        self._filters.append(leaf)
        return self

    def eq(self, field: str, value: Any) -> Query:
        """Match records where ``field`` equals ``value``.

        For picklist fields use the ``.itemValue`` sub-path to compare by display
        name, or the bare field name to compare by IRI::

            Query().eq("severity.itemValue", "High")   # friendly name
            Query().eq("status.itemValue", "Open")
            Query().eq("uuid", "3f2a...")               # exact UUID
        """
        return self.where(field, "eq", value)

    def neq(self, field: str, value: Any) -> Query:
        """Match records where ``field`` does not equal ``value``.

        ::

            Query().neq("status.itemValue", "Closed")  # exclude closed alerts
        """
        return self.where(field, "neq", value)

    def lt(self, field: str, value: Any) -> Query:
        """Match records where ``field`` is less than ``value``.

        Typically used with epoch timestamps::

            import time
            Query().lt("createDate", time.time() - 86400)  # older than 24 h
        """
        return self.where(field, "lt", value)

    def lte(self, field: str, value: Any) -> Query:
        """Match records where ``field`` is less than or equal to ``value``."""
        return self.where(field, "lte", value)

    def gt(self, field: str, value: Any) -> Query:
        """Match records where ``field`` is greater than ``value``.

        Typically used with epoch timestamps::

            import time
            Query().gt("createDate", time.time() - 3600)  # last hour
        """
        return self.where(field, "gt", value)

    def gte(self, field: str, value: Any) -> Query:
        """Match records where ``field`` is greater than or equal to ``value``."""
        return self.where(field, "gte", value)

    def in_(self, field: str, values: list[Any]) -> Query:
        """Match records where ``field`` equals any value in ``values``.

        ::

            Query().in_("severity.itemValue", ["Critical", "High"])
            Query().in_("uuid", ["3f2a...", "7b1c..."])
        """
        return self.where(field, "in", list(values))

    def nin(self, field: str, values: list[Any]) -> Query:
        """Match records where ``field`` equals none of ``values``.

        ::

            Query().nin("status.itemValue", ["Closed", "Resolved"])
        """
        return self.where(field, "nin", list(values))

    def like(self, field: str, value: str) -> Query:
        """Case-insensitive substring match on ``field``.

        The server interprets ``value`` as a SQL ``LIKE`` pattern (``%`` is
        wildcard), but a plain string also works as a contains check::

            Query().like("name", "phishing")        # name contains "phishing"
            Query().like("sourceId", "SIEM-%")      # starts with "SIEM-"
        """
        return self.where(field, "like", value)

    def notlike(self, field: str, value: str) -> Query:
        """Case-insensitive substring non-match on ``field``.

        Inverse of :meth:`like`::

            Query().notlike("name", "test")  # exclude records named "test*"
        """
        return self.where(field, "notlike", value)

    def contains(self, field: str, value: Any) -> Query:
        """Match records where a collection ``field`` contains ``value``.

        Used for to-many relationship fields (tags, indicators, owners) — checks
        that at least one related item matches::

            Query().contains("recordTags", "malware")
        """
        return self.where(field, "contains", value)

    def exists(self, field: str, value: bool = True) -> Query:
        """Match records where ``field`` is present (``True``) or absent (``False``).

        ::

            Query().exists("assignedTo")           # record has an assignee
            Query().exists("assignedTo", False)    # record is unassigned
        """
        return self.where(field, "exists", value)

    def isnull(self, field: str, value: bool = True) -> Query:
        """Match records where ``field`` is null (``True``) or non-null (``False``).

        ::

            Query().isnull("resolvedDate")          # not yet resolved
            Query().isnull("resolvedDate", False)   # has a resolved date
        """
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
        """Add a sort clause. Call multiple times for multi-field sort.

        Args:
            field: The field path to sort on (e.g. ``"createDate"``, ``"name"``).
            direction: ``"DESC"`` (newest/highest first, default) or ``"ASC"``.

        ::

            Query().sort("createDate", "DESC").sort("name", "ASC")
        """
        direction = direction.upper()
        if direction not in ("ASC", "DESC"):
            raise ValueError(f"direction must be 'ASC' or 'DESC', got {direction!r}")
        self._check_field(field)
        self._sort.append({"field": field, "direction": direction})
        return self

    def select(self, *fields: str) -> Query:
        """Allowlist the fields returned per record (``__selectFields``).

        Reduces payload size when you only need a few fields. Mutually exclusive
        with :meth:`ignore`::

            Query().select("uuid", "name", "severity", "status")
        """
        if self._ignore is not None:
            raise ValueError("select() and ignore() are mutually exclusive")
        self._select = list(fields)
        return self

    def ignore(self, *fields: str) -> Query:
        """Denylist fields stripped from each record (``__ignoreFields``).

        Useful when the module has large text fields you don't need. Mutually
        exclusive with :meth:`select`::

            Query().ignore("description", "sourcedata")
        """
        if self._select is not None:
            raise ValueError("select() and ignore() are mutually exclusive")
        self._ignore = list(fields)
        return self

    def limit(self, n: int) -> Query:
        """Set the page size (number of records per page).

        The default server page size is 30. Use with :meth:`page` for manual
        pagination, or leave it to :meth:`~pyfsr.records.RecordSet.iterate` which
        handles paging automatically::

            Query().limit(100)          # 100 per page
        """
        self._limit = n
        return self

    def page(self, n: int) -> Query:
        """Set the 1-based page number for manual pagination.

        Usually not needed — prefer :meth:`~pyfsr.records.RecordSet.iterate` which
        walks pages automatically::

            Query().limit(50).page(3)   # third page of 50
        """
        self._page = n
        return self

    def search(self, term: str) -> Query:
        """Add a free-text search term alongside structured filters.

        The server applies full-text search across indexed fields in addition to
        any leaf filters already added::

            Query().eq("status.itemValue", "Open").search("ransomware")
        """
        self._search = term
        return self

    # -- output -------------------------------------------------------------
    def _build_filters(self) -> list[dict[str, Any]]:
        return list(self._filters)

    def model(self) -> QueryBody:
        """Return the validated, typed :class:`QueryBody` for this query."""
        return QueryBody(
            logic=self._logic,
            filters=self._filters,
            sort=self._sort,
            select_fields=self._select,
            ignore_fields=self._ignore,
            limit=self._limit,
            search=self._search,
        )

    def to_body(self) -> dict[str, Any]:
        """Render the query as the dict POSTed to ``/api/query/{module}``."""
        return self.model().to_body()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Query(logic={self._logic!r}, filters={len(self._filters)})"
