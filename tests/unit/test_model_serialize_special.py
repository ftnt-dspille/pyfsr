"""Unit tests for BaseRecord.to_dict(serialize_special=...) (AUDIT gap 17).

Tests that object/array fields (typed as list[Any], dict[str, Any]) are
correctly serialized as JSON strings when serialize_special=True, and remain
as native Python objects when serialize_special=False (the default).
"""

import json

from pyfsr import Alert, BaseRecord, Incident, Task


class TestToDict_SerializeSpecialFalse:
    """Default behavior: serialize_special=False keeps native Python objects."""

    def test_default_list_any_field_stays_list(self):
        """list[Any] fields should remain as Python lists by default."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [{"id": "ind1"}, {"id": "ind2"}],
            }
        )
        result = inc.to_dict()
        assert isinstance(result["indicators"], list)
        assert result["indicators"] == [{"id": "ind1"}, {"id": "ind2"}]

    def test_multiple_special_fields_stay_native(self):
        """Multiple list[Any] fields should all remain native Python lists."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [{"indicator": "1"}],
                "comments": [{"comment": "a"}, {"comment": "b"}],
                "warrooms": [{"room": "x"}],
            }
        )
        result = inc.to_dict()
        assert isinstance(result["indicators"], list)
        assert isinstance(result["comments"], list)
        assert isinstance(result["warrooms"], list)

    def test_exclude_none_parameter_still_works(self):
        """exclude_none=True should work with serialize_special=False."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [{"id": "1"}],
            }
        )
        result = inc.to_dict(exclude_none=True)
        assert "indicators" in result
        assert result["indicators"] == [{"id": "1"}]
        # Fields without values should be excluded
        assert "comments" not in result

    def test_by_alias_parameter_still_works(self):
        """by_alias parameter should work with serialize_special=False."""
        rec = BaseRecord.model_validate({"@id": "/api/3/test/1", "uuid": "test-1"})
        result = rec.to_dict(by_alias=True)
        assert "@id" in result
        assert result["@id"] == "/api/3/test/1"


class TestToDict_SerializeSpecialTrue:
    """With serialize_special=True, special fields are JSON-encoded strings."""

    def test_list_any_field_becomes_json_string(self):
        """list[Any] fields should become JSON-encoded strings."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [{"id": "ind1"}, {"id": "ind2"}],
            }
        )
        result = inc.to_dict(serialize_special=True)
        assert isinstance(result["indicators"], str)
        # Should be valid JSON
        decoded = json.loads(result["indicators"])
        assert decoded == [{"id": "ind1"}, {"id": "ind2"}]

    def test_multiple_special_fields_all_encoded(self):
        """All special fields should be JSON-encoded when serialize_special=True."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [{"indicator": "1"}],
                "comments": [{"comment": "a"}, {"comment": "b"}],
                "warrooms": [{"room": "x"}],
            }
        )
        result = inc.to_dict(serialize_special=True)
        assert isinstance(result["indicators"], str)
        assert isinstance(result["comments"], str)
        assert isinstance(result["warrooms"], str)
        # All should be valid JSON
        assert json.loads(result["indicators"]) == [{"indicator": "1"}]
        assert json.loads(result["comments"]) == [{"comment": "a"}, {"comment": "b"}]
        assert json.loads(result["warrooms"]) == [{"room": "x"}]

    def test_empty_list_field_becomes_empty_json_array(self):
        """Empty list[Any] fields should become '[]'."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [],
            }
        )
        result = inc.to_dict(serialize_special=True)
        assert result["indicators"] == "[]"
        assert json.loads(result["indicators"]) == []

    def test_null_special_field_stays_null(self):
        """Special fields with null/None should stay null, not become 'null'."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
            }
        )
        # Don't set indicators, so it's None
        result = inc.to_dict(serialize_special=True, exclude_none=False)
        # With exclude_none=False, None fields are present in the dict
        assert result["indicators"] is None

    def test_exclude_none_with_serialize_special(self):
        """exclude_none=True should exclude None fields, even with serialize_special=True."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [{"id": "1"}],
            }
        )
        result = inc.to_dict(serialize_special=True, exclude_none=True)
        assert "indicators" in result
        assert result["indicators"] == '[{"id": "1"}]'
        # Fields that are None should not be in result
        assert "comments" not in result

    def test_by_alias_with_serialize_special(self):
        """by_alias parameter should still work with serialize_special=True."""
        rec = BaseRecord.model_validate({"@id": "/api/3/test/1", "uuid": "test-1"})
        result = rec.to_dict(by_alias=True, serialize_special=True)
        assert "@id" in result
        assert result["@id"] == "/api/3/test/1"

    def test_round_trip_serialization(self):
        """Serialized data should deserialize back to original value."""
        original = [
            {"key": "value", "nested": {"deep": 42}},
            {"key": "value2", "list": [1, 2, 3]},
        ]
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": original,
            }
        )
        result = inc.to_dict(serialize_special=True)
        deserialized = json.loads(result["indicators"])
        assert deserialized == original

    def test_complex_nested_objects(self):
        """Complex nested structures should serialize correctly."""
        complex_obj = {"nested": {"deep": {"value": "test", "list": [1, 2, {"x": "y"}]}}}
        # Create a task with a special field (using a list containing the dict)
        task = Task.model_validate(
            {
                "uuid": "t1",
                "name": "Task",
                "attachments": [complex_obj],
            }
        )
        result = task.to_dict(serialize_special=True)
        assert isinstance(result["attachments"], str)
        deserialized = json.loads(result["attachments"])
        assert deserialized == [complex_obj]


class TestToDict_NonSpecialFields:
    """Non-special fields should not be affected by serialize_special."""

    def test_string_fields_unchanged(self):
        """String fields should remain unchanged."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test Incident",
                "description": "A description",
            }
        )
        result_default = inc.to_dict(serialize_special=False)
        result_special = inc.to_dict(serialize_special=True)
        assert result_default["name"] == result_special["name"] == "Test Incident"
        assert result_default["description"] == result_special["description"]

    def test_numeric_fields_unchanged(self):
        """Numeric fields should remain unchanged."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "impactROI": 42,
                "recoveryTime": 3600,
            }
        )
        result_special = inc.to_dict(serialize_special=True)
        assert result_special["impactROI"] == 42
        assert result_special["recoveryTime"] == 3600

    def test_picklist_iri_fields_unchanged(self):
        """Picklist IRI fields should remain as strings."""
        alert = Alert.model_validate(
            {
                "uuid": "a1",
                "name": "Alert",
                "severity": "/api/3/picklists/sev-1",
            }
        )
        result_special = alert.to_dict(serialize_special=True)
        assert result_special["severity"] == "/api/3/picklists/sev-1"
        assert isinstance(result_special["severity"], str)

    def test_extra_fields_unchanged(self):
        """Extra (unknown) fields should not be affected."""
        rec = BaseRecord.model_validate(
            {
                "uuid": "r1",
                "customField": "custom_value",
                "customList": [1, 2, 3],  # A list, but untyped
            }
        )
        result_special = rec.to_dict(serialize_special=True)
        # Extra fields that aren't list[Any] or dict[str, Any] aren't encoded
        assert result_special["customField"] == "custom_value"
        # customList is preserved but not JSON-encoded (it's not a typed list[Any])
        assert result_special["customList"] == [1, 2, 3]


class TestToDict_BackwardCompatibility:
    """Ensure backward compatibility: default behavior unchanged."""

    def test_default_is_false(self):
        """Default behavior should be serialize_special=False."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [{"id": "1"}],
            }
        )
        # Call without specifying serialize_special
        result = inc.to_dict()
        # Should be same as explicit False
        assert result == inc.to_dict(serialize_special=False)

    def test_existing_code_unaffected(self):
        """Existing code using to_dict() should continue to work."""
        inc = Incident.model_validate(
            {
                "uuid": "i1",
                "name": "Test",
                "indicators": [{"id": "1"}],
            }
        )
        # This is how existing code uses to_dict
        result = inc.to_dict(by_alias=True, exclude_none=True)
        # Should still get native Python objects
        assert isinstance(result["indicators"], list)


class TestToDict_ModelVariants:
    """Test serialize_special across different model types."""

    def test_alert_with_createuser_dict_field(self):
        """Alert.createUser is dict | str; dict should not be JSON-encoded."""
        alert = Alert.model_validate(
            {
                "uuid": "a1",
                "name": "Alert",
                "createUser": {"@id": "/api/3/people/u1", "name": "User"},
            }
        )
        result = alert.to_dict(serialize_special=True)
        # createUser is not list[Any] or dict[str, Any], it's dict | str
        # So it should NOT be JSON-encoded
        assert isinstance(result["createUser"], dict)

    def test_task_with_multiple_special_fields(self):
        """Task should handle multiple special fields correctly."""
        task = Task.model_validate(
            {
                "uuid": "t1",
                "name": "Task",
                "attachments": [{"file": "doc.pdf"}],
                "comments": [{"text": "comment 1"}],
            }
        )
        result = task.to_dict(serialize_special=True)
        assert isinstance(result["attachments"], str)
        assert isinstance(result["comments"], str)
        assert json.loads(result["attachments"]) == [{"file": "doc.pdf"}]
        assert json.loads(result["comments"]) == [{"text": "comment 1"}]

    def test_baserecord_with_extra_list_field(self):
        """BaseRecord with extra fields should only encode typed list[Any]."""
        rec = BaseRecord.model_validate(
            {
                "uuid": "r1",
                "extraListField": [1, 2, 3],  # untyped, shouldn't be encoded
            }
        )
        result = rec.to_dict(serialize_special=True)
        # untyped extra fields are NOT encoded
        assert result["extraListField"] == [1, 2, 3]
