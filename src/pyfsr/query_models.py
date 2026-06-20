"""Pydantic models + operator knowledge base for the FortiSOAR query DSL.

This is the validated, typed backing for :class:`pyfsr.query.Query`. The fluent
builder assembles a :class:`QueryBody`, which round-trips to the exact dict the
``POST /api/query/{module}`` endpoint expects.

The **operator knowledge base** (:data:`OPERATOR_SPECS`) records each leaf
operator's *arity* (does it take no value, a scalar, a list, or a bool?) and
*category* (a normal leaf filter vs a trigger-condition operator only valid in
playbook start/update filters). :data:`DEPRECATED_OPERATORS` maps common
mistakes to the operator that actually works (e.g. ``isnotnull`` 400s on the
appliance — use ``isnull`` with ``value=False``).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Arity(str, Enum):
    """How many/what kind of value an operator's leaf carries."""

    NONE = "none"  # value-less (e.g. changed)
    SCALAR = "scalar"  # a single value (eq, gt, like, …)
    LIST = "list"  # a list value (in, nin, in_all)
    BOOL = "bool"  # a boolean flag (exists, isnull)


class OperatorSpec(BaseModel):
    """One entry in the operator knowledge base."""

    model_config = ConfigDict(frozen=True)

    name: str
    arity: Arity
    category: Literal["leaf", "trigger"]
    summary: str


def _spec(name: str, arity: Arity, category: str, summary: str) -> OperatorSpec:
    return OperatorSpec(name=name, arity=arity, category=category, summary=summary)  # type: ignore[arg-type]


#: Authoritative operator knowledge base (FortiSOAR OpenAPI QueryBody schema +
#: trigger-condition operators used by playbook start filters).
OPERATOR_SPECS: dict[str, OperatorSpec] = {
    o.name: o
    for o in (
        _spec("eq", Arity.SCALAR, "leaf", "equals"),
        _spec("neq", Arity.SCALAR, "leaf", "not equals"),
        _spec("lt", Arity.SCALAR, "leaf", "less than"),
        _spec("lte", Arity.SCALAR, "leaf", "less than or equal"),
        _spec("gt", Arity.SCALAR, "leaf", "greater than"),
        _spec("gte", Arity.SCALAR, "leaf", "greater than or equal"),
        _spec("in", Arity.LIST, "leaf", "value is any of the list"),
        _spec("nin", Arity.LIST, "leaf", "value is none of the list"),
        _spec("like", Arity.SCALAR, "leaf", "case-insensitive substring match"),
        _spec("notlike", Arity.SCALAR, "leaf", "negated substring match"),
        _spec("contains", Arity.SCALAR, "leaf", "collection/relationship contains value"),
        _spec("exists", Arity.BOOL, "leaf", "field is present (bool)"),
        _spec("isnull", Arity.BOOL, "leaf", "field is null (bool; use value=False for not-null)"),
        _spec("changed", Arity.NONE, "trigger", "field changed (playbook trigger only)"),
        _spec("in_all", Arity.LIST, "trigger", "collection contains all of the list"),
    )
}

#: Back-compat: the flat set of valid operator names.
OPERATORS = frozenset(OPERATOR_SPECS)

#: Common wrong/unsupported operators → the one that actually works.
DEPRECATED_OPERATORS: dict[str, str] = {
    "isnotnull": "use 'isnull' with value=False (isnotnull returns HTTP 400)",
    "notin": "use 'nin'",
    "not_in": "use 'nin'",
    "ne": "use 'neq'",
    "equals": "use 'eq'",
    "gte_lte": "use two leaves: gte and lte",
    "ilike": "use 'like' (already case-insensitive)",
    "startswith": "use 'like' with the prefix",
}


def operator_error(operator: str) -> str:
    """Build a helpful error message for an unknown/unsupported operator."""
    hint = DEPRECATED_OPERATORS.get(operator)
    if hint:
        return f"unsupported operator {operator!r}: {hint}"
    return f"unknown operator {operator!r}; valid: {', '.join(sorted(OPERATORS))}"


def validate_leaf_value(operator: str, value: Any) -> None:
    """Raise ``ValueError`` if ``value`` doesn't match ``operator``'s arity."""
    spec = OPERATOR_SPECS.get(operator)
    if spec is None:
        raise ValueError(operator_error(operator))
    if spec.arity is Arity.NONE:
        if value is not None:
            raise ValueError(f"operator {operator!r} is value-less; drop the value")
    elif spec.arity is Arity.LIST:
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"operator {operator!r} needs a list value, got {type(value).__name__}")
    elif spec.arity is Arity.BOOL:
        if not isinstance(value, bool):
            raise ValueError(f"operator {operator!r} needs a bool value, got {type(value).__name__}")
    else:  # SCALAR
        if value is None:
            raise ValueError(f"operator {operator!r} requires a value")


# --------------------------------------------------------------------------- models


class SortSpec(BaseModel):
    """A single ``sort`` entry."""

    field: str
    direction: Literal["ASC", "DESC"] = "DESC"


class FilterLeaf(BaseModel):
    """A leaf condition: ``{field, operator, value?}``."""

    model_config = ConfigDict(extra="forbid")

    field: str
    operator: str
    value: Any = None

    @field_validator("operator")
    @classmethod
    def _known_operator(cls, v: str) -> str:
        if v not in OPERATORS:
            raise ValueError(operator_error(v))
        return v

    @model_validator(mode="after")
    def _check_arity(self) -> FilterLeaf:
        validate_leaf_value(self.operator, self.value)
        return self


class FilterGroup(BaseModel):
    """A nested group: ``{logic, filters[]}`` of leaves and/or sub-groups."""

    model_config = ConfigDict(extra="forbid")

    logic: Literal["AND", "OR"] = "AND"
    filters: list[FilterLeaf | FilterGroup] = Field(default_factory=list)


class QueryBody(BaseModel):
    """The full body POSTed to ``/api/query/{module}``.

    ``to_body()`` renders the appliance-shaped dict (aliased ``__selectFields`` /
    ``__ignoreFields``, empty ``sort`` dropped).
    """

    model_config = ConfigDict(populate_by_name=True)

    logic: Literal["AND", "OR"] = "AND"
    filters: list[FilterLeaf | FilterGroup] = Field(default_factory=list)
    sort: list[SortSpec] = Field(default_factory=list)
    select_fields: list[str] | None = Field(default=None, alias="__selectFields")
    ignore_fields: list[str] | None = Field(default=None, alias="__ignoreFields")
    limit: int | None = None
    search: str | None = None
    show_deleted: bool | None = Field(default=None, alias="showDeleted")

    @model_validator(mode="after")
    def _select_xor_ignore(self) -> QueryBody:
        if self.select_fields is not None and self.ignore_fields is not None:
            raise ValueError("__selectFields and __ignoreFields are mutually exclusive")
        return self

    def to_body(self) -> dict[str, Any]:
        """Render the appliance-shaped query dict."""
        data = self.model_dump(by_alias=True, exclude_none=True)
        if not data.get("sort"):
            data.pop("sort", None)
        return data
