"""The terse predicate parser lowers to the FortiSOAR query filter structure."""

from __future__ import annotations

import pytest

from pyfsr.predicate import PredicateError, parse_predicate, parse_sort

PICKLISTS = {"status", "severity", "type"}


def test_empty_predicate_is_an_empty_and():
    assert parse_predicate("") == ([], "AND")
    assert parse_predicate("   ") == ([], "AND")


def test_single_equality_on_a_picklist_gets_itemvalue():
    filters, logic = parse_predicate("status=Open", PICKLISTS)
    assert logic == "AND"
    assert filters == [{"field": "status.itemValue", "operator": "eq", "value": "Open"}]


def test_a_plain_text_field_is_not_rewritten():
    filters, _ = parse_predicate("name=web-server-01", PICKLISTS)
    assert filters == [{"field": "name", "operator": "eq", "value": "web-server-01"}]


def test_contains_adds_sql_wildcards():
    filters, _ = parse_predicate('name~"Agent Lab"', PICKLISTS)
    assert filters == [{"field": "name", "operator": "like", "value": "%Agent Lab%"}]


def test_and_combines_with_and_logic():
    filters, logic = parse_predicate("status=Open and severity=High", PICKLISTS)
    assert logic == "AND"
    assert filters == [
        {"field": "status.itemValue", "operator": "eq", "value": "Open"},
        {"field": "severity.itemValue", "operator": "eq", "value": "High"},
    ]


def test_or_combines_with_or_logic():
    filters, logic = parse_predicate("severity=High or severity=Critical", PICKLISTS)
    assert logic == "OR"
    assert len(filters) == 2


def test_comparison_operators_map_to_fortisoar_operators():
    assert parse_predicate("count>=5")[0] == [{"field": "count", "operator": "gte", "value": "5"}]
    assert parse_predicate("count<10")[0] == [{"field": "count", "operator": "lt", "value": "10"}]
    assert parse_predicate("severity!=Low", PICKLISTS)[0] == [
        {"field": "severity.itemValue", "operator": "neq", "value": "Low"}
    ]


def test_in_builds_a_value_list_and_itemvalue_for_a_picklist():
    filters, _ = parse_predicate("status in Open,Investigating", PICKLISTS)
    assert filters == [{"field": "status.itemValue", "operator": "in", "value": ["Open", "Investigating"]}]


def test_quoted_value_may_contain_a_connective_or_operator():
    filters, logic = parse_predicate('name~"locked and loaded"', PICKLISTS)
    assert logic == "AND"
    assert filters == [{"field": "name", "operator": "like", "value": "%locked and loaded%"}]


def test_an_explicit_itemvalue_is_left_alone():
    filters, _ = parse_predicate("status.itemValue=Open", PICKLISTS)
    assert filters == [{"field": "status.itemValue", "operator": "eq", "value": "Open"}]


def test_without_picklist_hints_nothing_is_rewritten():
    filters, _ = parse_predicate("status=Open")
    assert filters == [{"field": "status", "operator": "eq", "value": "Open"}]


def test_mixing_and_or_is_refused():
    with pytest.raises(PredicateError, match="either 'and' or 'or'"):
        parse_predicate("a=1 and b=2 or c=3")


def test_a_clause_without_an_operator_is_refused():
    with pytest.raises(PredicateError, match="no operator"):
        parse_predicate("status Open")


def test_a_clause_without_a_field_is_refused():
    with pytest.raises(PredicateError, match="missing field"):
        parse_predicate("=Open")


def test_sort_parses_direction_per_field_defaulting_to_asc():
    assert parse_sort("createDate desc, name") == [
        {"field": "createDate", "direction": "DESC"},
        {"field": "name", "direction": "ASC"},
    ]


def test_sort_rejects_a_bad_direction():
    with pytest.raises(PredicateError, match="asc or desc"):
        parse_sort("createDate sideways")
