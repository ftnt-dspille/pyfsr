"""Unit tests for the pydantic query-body models + operator knowledge base."""

import pytest

from pyfsr.query_models import (
    OPERATOR_SPECS,
    OPERATORS,
    Arity,
    FilterGroup,
    FilterLeaf,
    QueryBody,
    SortSpec,
    operator_error,
    validate_leaf_value,
)

# ------------------------------------------------------------------- operator KB


def test_operator_specs_cover_operators_set():
    assert set(OPERATOR_SPECS) == set(OPERATORS)


def test_operator_categories_and_arity():
    assert OPERATOR_SPECS["changed"].arity is Arity.NONE
    assert OPERATOR_SPECS["changed"].category == "trigger"
    assert OPERATOR_SPECS["in"].arity is Arity.LIST
    assert OPERATOR_SPECS["eq"].arity is Arity.SCALAR
    assert OPERATOR_SPECS["exists"].arity is Arity.BOOL


@pytest.mark.parametrize(
    "operator,value",
    [("in", "x"), ("changed", "v"), ("eq", None), ("exists", "yes"), ("nin", 3)],
)
def test_validate_leaf_value_rejects_arity_mismatch(operator, value):
    with pytest.raises(ValueError):
        validate_leaf_value(operator, value)


@pytest.mark.parametrize(
    "operator,value",
    [("in", ["a"]), ("changed", None), ("eq", "v"), ("exists", True), ("isnull", False)],
)
def test_validate_leaf_value_accepts_correct_arity(operator, value):
    validate_leaf_value(operator, value)  # no raise


def test_operator_error_suggests_fix_for_known_mistakes():
    assert "isnull" in operator_error("isnotnull")
    assert "nin" in operator_error("notin")
    assert "valid:" in operator_error("totally_made_up")


# ------------------------------------------------------------------- FilterLeaf


def test_filter_leaf_rejects_unknown_operator():
    with pytest.raises(ValueError, match="unknown operator"):
        FilterLeaf(field="x", operator="bogus", value=1)


def test_filter_leaf_enforces_arity():
    with pytest.raises(ValueError, match="needs a list"):
        FilterLeaf(field="tags", operator="in", value="not-a-list")


def test_filter_leaf_forbids_extra_keys():
    with pytest.raises(ValueError):
        FilterLeaf(field="x", operator="eq", value=1, junk="nope")


# ------------------------------------------------------------------- QueryBody


def test_query_body_to_body_aliases_and_drops_empty_sort():
    body = QueryBody(
        logic="OR",
        filters=[FilterLeaf(field="x", operator="eq", value=1)],
        select_fields=["uuid", "name"],
    ).to_body()
    assert body["logic"] == "OR"
    assert body["__selectFields"] == ["uuid", "name"]
    assert "sort" not in body  # empty sort dropped
    assert body["filters"] == [{"field": "x", "operator": "eq", "value": 1}]


def test_query_body_parses_nested_group_from_dicts():
    qb = QueryBody(
        filters=[
            {"field": "a", "operator": "eq", "value": 1},
            {"logic": "OR", "filters": [{"field": "b", "operator": "eq", "value": 2}]},
        ]
    )
    assert isinstance(qb.filters[0], FilterLeaf)
    assert isinstance(qb.filters[1], FilterGroup)
    body = qb.to_body()
    assert body["filters"][1] == {
        "logic": "OR",
        "filters": [{"field": "b", "operator": "eq", "value": 2}],
    }


def test_query_body_select_ignore_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        QueryBody(select_fields=["a"], ignore_fields=["b"])


def test_query_body_drops_valueless_leaf_value():
    body = QueryBody(filters=[FilterLeaf(field="status", operator="changed")]).to_body()
    assert body["filters"][0] == {"field": "status", "operator": "changed"}


def test_sort_spec_defaults_desc():
    assert SortSpec(field="createDate").direction == "DESC"
