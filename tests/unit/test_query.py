"""Unit tests for the Query DSL builder."""

import pytest

from pyfsr import Query


def test_simple_eq_body():
    body = Query().eq("severity.itemValue", "Critical").to_body()
    assert body == {
        "logic": "AND",
        "filters": [{"field": "severity.itemValue", "operator": "eq", "value": "Critical"}],
    }


def test_chained_filters_and_shaping():
    q = (
        Query()
        .eq("status.itemValue", "Open")
        .gt("createDate", 1700000000)
        .sort("createDate", "desc")
        .select("uuid", "name", "severity")
        .limit(50)
        .search("ransomware")
    )
    body = q.to_body()
    assert body["logic"] == "AND"
    assert len(body["filters"]) == 2
    assert body["sort"] == [{"field": "createDate", "direction": "DESC"}]
    assert body["__selectFields"] == ["uuid", "name", "severity"]
    assert body["limit"] == 50
    assert body["search"] == "ransomware"


def test_in_and_nin_coerce_to_list():
    body = Query().in_("type", ("a", "b")).nin("state", ["x"]).to_body()
    assert body["filters"][0] == {"field": "type", "operator": "in", "value": ["a", "b"]}
    assert body["filters"][1] == {"field": "state", "operator": "nin", "value": ["x"]}


def test_exists_and_isnull_default_true():
    body = Query().exists("owner").isnull("deletedAt").to_body()
    assert body["filters"][0] == {"field": "owner", "operator": "exists", "value": True}
    assert body["filters"][1] == {"field": "deletedAt", "operator": "isnull", "value": True}


def test_nested_or_group():
    sub = Query("OR").eq("a", 1).eq("b", 2)
    body = Query().eq("top", "x").group(sub).to_body()
    assert body["filters"][0] == {"field": "top", "operator": "eq", "value": "x"}
    assert body["filters"][1] == {
        "logic": "OR",
        "filters": [
            {"field": "a", "operator": "eq", "value": 1},
            {"field": "b", "operator": "eq", "value": 2},
        ],
    }


def test_ignore_fields():
    body = Query().eq("x", 1).ignore("createDate", "createUser").to_body()
    assert body["__ignoreFields"] == ["createDate", "createUser"]
    assert "__selectFields" not in body


def test_select_and_ignore_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        Query().select("a").ignore("b")
    with pytest.raises(ValueError, match="mutually exclusive"):
        Query().ignore("b").select("a")


def test_unknown_operator_rejected():
    with pytest.raises(ValueError, match="unknown operator"):
        Query().where("f", "bogus", 1)


def test_invalid_logic_and_direction():
    with pytest.raises(ValueError, match="logic must be"):
        Query("XOR")
    with pytest.raises(ValueError, match="direction must be"):
        Query().sort("f", "sideways")


def test_changed_operator_is_valueless():
    body = Query().changed("status").to_body()
    assert body["filters"] == [{"field": "status", "operator": "changed"}]


def test_in_all_operator_takes_list():
    body = Query().in_all("tags", ["a", "b"]).to_body()
    assert body["filters"] == [{"field": "tags", "operator": "in_all", "value": ["a", "b"]}]
