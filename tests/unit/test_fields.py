"""Unit tests for the field/relationship knowledge base."""

import pytest

from pyfsr import fields


def test_kb_has_core_modules():
    mods = fields.known_modules()
    assert "alerts" in mods
    assert "incidents" in mods


def test_module_fields_and_relationships_for_alerts():
    assert "name" in fields.module_fields("alerts")
    rels = fields.module_relationships("alerts")
    # severity is a picklist relationship on alerts
    assert "severity" in rels


def test_unknown_module_returns_empty():
    assert fields.module_fields("not_a_real_module") == []
    assert fields.module_relationships("not_a_real_module") == {}


# ------------------------------------------------------------------- normalization


def test_normalize_field_path_converts_double_underscore():
    assert fields.normalize_field_path("severity__itemValue") == "severity.itemValue"


def test_normalize_field_path_rejects_empty():
    with pytest.raises(ValueError, match="non-empty"):
        fields.normalize_field_path("   ")


def test_split_field_path_rejects_empty_segment():
    with pytest.raises(ValueError, match="empty segment"):
        fields.split_field_path("severity..itemValue")


# ------------------------------------------------------------------- validation


def test_validate_known_base_field_passes():
    fields.validate_field_path("alerts", "name")


def test_validate_unknown_base_field_raises():
    with pytest.raises(ValueError, match="no field"):
        fields.validate_field_path("alerts", "definitely_not_a_field")


def test_validate_dot_walk_through_relationship_passes():
    # severity -> picklists; itemValue is a picklist field
    fields.validate_field_path("alerts", "severity.itemValue")


def test_validate_dot_walk_into_scalar_raises():
    with pytest.raises(ValueError, match="scalar, not a relationship"):
        fields.validate_field_path("alerts", "name.itemValue")


def test_validate_unknown_module_is_lenient():
    # unknown module: cannot validate, must not raise
    fields.validate_field_path("custom_module_xyz", "whatever.path")


def test_validate_system_fields_accepted():
    # framework fields aren't in per-module schema attributes but are always valid
    for f in ("createDate", "modifyDate", "uuid", "id", "deletedAt"):
        fields.validate_field_path("alerts", f)


def test_validate_system_relationship_dot_walk():
    # audit relationships resolve to people (createUser/modifyUser -> people)
    fields.validate_field_path("alerts", "createUser.firstname")
    fields.validate_field_path("alerts", "modifyUser.firstname")
