"""A terse predicate surface that compiles DOWN to the FortiSOAR query filter
structure the pyfsr query API already consumes.

A caller (often an agent) should filter records without knowing the Hydra
schema. It writes ``status=Open and severity=High`` and this
module lowers it to the ``{logic, filters:[{field, operator, value}]}`` body
``records().query`` takes -- owning the quirks in one place:

- ``~`` (contains) wraps the value in SQL wildcards (``%value%``), because a bare
  FortiSOAR ``like`` silently matches nothing without them (live: ``name like
  "Agent Lab"`` -> 0 rows, ``"%Agent Lab%"`` -> 20).
- a picklist field compared by ``=``/``!=``/``in`` gets ``.itemValue`` appended,
  because a bare picklist comparison silently matches nothing (live: ``status eq
  "Open"`` -> 0 rows, ``status.itemValue eq "Open"`` -> 19).

Use it through :meth:`pyfsr.records.RecordSet.where_body`, which supplies the
module's picklist fields.

This is a thin translator, not a query engine: it emits the exact filter JSON the
existing API consumes, and the caller passes the result straight to
``records().query``.

Grammar (one level, no parentheses):

    predicate := clause ((" and " | " or ") clause)*
    clause    := field OP value | field " in " value ("," value)*
    OP        := "!=" | ">=" | "<=" | "~" | "=" | ">" | "<"

``and``/``or`` must not be mixed in one predicate (a FortiSOAR query body carries
a single ``logic``). Values may be single- or double-quoted to include spaces or
an operator character.
"""

from __future__ import annotations

import re
from typing import Any

from .exceptions import ValidationError

# Two-character operators are listed before the one-character ones they contain
# (``!=`` before ``=``, ``>=`` before ``>``) so the scan claims the longer match.
_OPS: tuple[tuple[str, str], ...] = (
    ("!=", "neq"),
    (">=", "gte"),
    ("<=", "lte"),
    ("~", "like"),
    ("=", "eq"),
    (">", "gt"),
    ("<", "lt"),
)
_ITEMVALUE_OPS = {"eq", "neq", "in"}
_QUOTED_RE = re.compile(r'"[^"]*"|\'[^\']*\'')
_CONNECTIVE_RE = re.compile(r"\s+(and|or)\s+", re.IGNORECASE)
_SPLIT_RE = re.compile(r"\s+(?:and|or)\s+", re.IGNORECASE)
_IN_RE = re.compile(r"^(?P<field>[\w.]+)\s+in\s+(?P<values>.+)$", re.IGNORECASE)


class PredicateError(ValidationError, ValueError):
    """A predicate string that could not be parsed. The message names the fix."""


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _picklist_field(field: str, picklist_fields: set[str]) -> str:
    """Append ``.itemValue`` when ``field`` is a picklist filtered by display value."""
    base = field.split(".", 1)[0]
    if base in picklist_fields and not field.endswith(".itemValue"):
        return f"{field}.itemValue"
    return field


def _parse_clause(clause: str, picklist_fields: set[str]) -> dict[str, Any]:
    in_match = _IN_RE.match(clause)
    if in_match:
        field = in_match.group("field")
        values = [_strip_quotes(v) for v in in_match.group("values").split(",") if v.strip()]
        if not values:
            raise PredicateError(f"'in' needs at least one value: {clause!r}")
        return {"field": _picklist_field(field, picklist_fields), "operator": "in", "value": values}

    for symbol, operator in _OPS:
        idx = clause.find(symbol)
        if idx < 0:  # operator not present; try the next
            continue
        field = clause[:idx].strip()
        value = _strip_quotes(clause[idx + len(symbol) :])
        if not field:
            raise PredicateError(f"missing field before '{symbol}' in: {clause!r}")
        if operator == "like":
            return {"field": field, "operator": "like", "value": f"%{value}%"}
        if operator in _ITEMVALUE_OPS:
            field = _picklist_field(field, picklist_fields)
        return {"field": field, "operator": operator, "value": value}

    raise PredicateError(f"no operator in clause: {clause!r} (use =, !=, >, >=, <, <=, ~ for contains, or 'in')")


def parse_predicate(where: str, picklist_fields: set[str] | None = None) -> tuple[list[dict[str, Any]], str]:
    """Compile ``where`` to ``(filters, logic)`` for ``records().query``.

    ``picklist_fields`` is the set of picklist field names on the module, so a
    bare ``status=Open`` lowers to ``status.itemValue=Open``. Omit it and no
    ``.itemValue`` rewriting happens (callers that already pass ``field.itemValue``
    need nothing). Raises :class:`PredicateError` on a malformed predicate.
    """
    picklist_fields = picklist_fields or set()
    where = where.strip()
    if not where:
        return [], "AND"

    # Mask quoted segments so a connective or operator inside a value is not parsed
    # as syntax, then restore each value in its clause.
    quoted: list[str] = []

    def _mask(match: re.Match[str]) -> str:
        quoted.append(match.group(0))
        return f"\x00{len(quoted) - 1}\x00"

    masked = _QUOTED_RE.sub(_mask, where)

    has_and = re.search(r"\s+and\s+", masked, re.IGNORECASE)
    has_or = re.search(r"\s+or\s+", masked, re.IGNORECASE)
    if has_and and has_or:
        raise PredicateError(
            "a predicate uses either 'and' or 'or', not both (a query has one logic); split it into two queries"
        )
    logic = "OR" if has_or else "AND"

    def _unmask(match: re.Match[str]) -> str:
        return quoted[int(match.group(1))]

    filters: list[dict[str, Any]] = []
    for masked_clause in _SPLIT_RE.split(masked):
        clause = re.sub(r"\x00(\d+)\x00", _unmask, masked_clause).strip()
        if clause:
            filters.append(_parse_clause(clause, picklist_fields))
    return filters, logic


def parse_sort(sort: str) -> list[dict[str, str]]:
    """Compile a terse sort (``"createDate desc, severity asc"``) to the query
    ``sort`` array. A bare field sorts ascending."""
    out: list[dict[str, str]] = []
    for part in sort.split(","):
        tokens = part.split()
        if not tokens:
            continue
        field = tokens[0]
        direction = tokens[1].upper() if len(tokens) > 1 else "ASC"
        if direction not in ("ASC", "DESC"):
            raise PredicateError(f"sort direction must be asc or desc, got {tokens[1]!r}")
        out.append({"field": field, "direction": direction})
    return out
