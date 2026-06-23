"""Unit tests for RecordModuleAPI helpers: resolve_bulk and validate parameter."""

import pytest

from pyfsr.api._record_module import RecordModuleAPI


class _NoopPicklists:
    """Identity picklist resolver for unit tests (no HTTP)."""

    def __init__(self, fail_on_field=None, fail_strict=False):
        """Initialize the resolver.

        Args:
            fail_on_field: Field name that should trigger a resolution failure.
            fail_strict: Whether to raise on resolution miss (when strict=True).
        """
        self.fail_on_field = fail_on_field
        self.fail_strict = fail_strict
        self.resolve_calls = []

    def resolve_record_fields(self, module, fields, strict=False, report=None):
        """Mock resolver that tracks calls and optionally fails on a field."""
        self.resolve_calls.append((module, dict(fields), strict))
        if report is None:
            report = []

        out = {}
        for k, v in fields.items():
            if self.fail_on_field and k == self.fail_on_field:
                report.append({"field": k, "value": v, "picklist": "TestList", "valid_values": ["A", "B"]})
                if strict and self.fail_strict:
                    from pyfsr.exceptions import PicklistResolutionError

                    raise PicklistResolutionError(k, v, "TestList", ["A", "B"])
                # Otherwise, leave the original value
                out[k] = v
            else:
                out[k] = v
        return out


class FakeClient:
    """Minimal mock client for testing RecordModuleAPI."""

    def __init__(self, responses=None, picklists=None):
        self.calls = []
        self.responses = responses or {}
        self.picklists = picklists or _NoopPicklists()

    def get(self, endpoint, params=None, **kwargs):
        self.calls.append(("GET", endpoint, params, None))
        return self.responses.get(endpoint, {})

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.calls.append(("POST", endpoint, params, data))
        return self.responses.get(endpoint, {"uuid": "new-id"})

    def put(self, endpoint, data=None, params=None, **kwargs):
        self.calls.append(("PUT", endpoint, params, data))
        return self.responses.get(endpoint, data or {})

    def delete(self, endpoint, params=None, **kwargs):
        self.calls.append(("DELETE", endpoint, params, None))
        return None


class MockRecordAPI(RecordModuleAPI):
    """Concrete RecordModuleAPI for testing."""

    module = "test_records"


# -- resolve_bulk tests ---------------------------------------------------------


def test_resolve_bulk_single_record():
    """resolve_bulk processes a single record correctly."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists)
    api = MockRecordAPI(client)

    records = [{"name": "test1", "status": "Open"}]
    result = api.resolve_bulk(records)

    assert len(result) == 1
    assert result[0] == {"name": "test1", "status": "Open"}
    assert len(picklists.resolve_calls) == 1
    assert picklists.resolve_calls[0] == ("test_records", {"name": "test1", "status": "Open"}, False)


def test_resolve_bulk_multiple_records():
    """resolve_bulk processes multiple records in order."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists)
    api = MockRecordAPI(client)

    records = [
        {"name": "task1", "priority": "High"},
        {"name": "task2", "priority": "Low"},
        {"name": "task3", "priority": "Medium"},
    ]
    result = api.resolve_bulk(records)

    assert len(result) == 3
    assert result[0]["name"] == "task1"
    assert result[1]["name"] == "task2"
    assert result[2]["name"] == "task3"
    assert len(picklists.resolve_calls) == 3


def test_resolve_bulk_empty_iterable():
    """resolve_bulk handles empty input gracefully."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists)
    api = MockRecordAPI(client)

    result = api.resolve_bulk([])
    assert result == []
    assert len(picklists.resolve_calls) == 0


def test_resolve_bulk_strict_mode():
    """resolve_bulk passes strict=True to the resolver."""
    picklists = _NoopPicklists(fail_on_field="status", fail_strict=True)
    client = FakeClient(picklists=picklists)
    api = MockRecordAPI(client)

    records = [{"name": "task", "status": "Invalid"}]
    with pytest.raises(Exception):  # PicklistResolutionError
        api.resolve_bulk(records, strict=True)

    assert picklists.resolve_calls[0][2] is True  # strict param was passed


def test_resolve_bulk_preserves_order():
    """resolve_bulk maintains record order through processing."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists)
    api = MockRecordAPI(client)

    records = [{"id": i, "name": f"rec_{i}"} for i in range(10)]
    result = api.resolve_bulk(records)

    assert len(result) == 10
    for i, rec in enumerate(result):
        assert rec["id"] == i
        assert rec["name"] == f"rec_{i}"


def test_resolve_bulk_generator_input():
    """resolve_bulk accepts generator/iterable input."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists)
    api = MockRecordAPI(client)

    def record_gen():
        yield {"name": "task1"}
        yield {"name": "task2"}

    result = api.resolve_bulk(record_gen())
    assert len(result) == 2
    assert result[0]["name"] == "task1"
    assert result[1]["name"] == "task2"


# -- validate parameter tests (create) ------------------------------------------


def test_create_with_validate_false():
    """create with validate=False (default) skips validation."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists, responses={"/api/3/test_records": {"uuid": "new"}})
    api = MockRecordAPI(client)

    result = api.create(name="test", validate=False, resolve_picklists=False)
    assert result["uuid"] == "new"
    # Should make a POST call
    assert len(client.calls) == 1
    assert client.calls[0][0] == "POST"


def test_create_with_validate_true():
    """create with validate=True runs validation before POST."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists, responses={"/api/3/test_records": {"uuid": "new"}})
    api = MockRecordAPI(client)

    # With validate=True, it calls _validate_record before posting
    result = api.create(name="test", validate=True, resolve_picklists=False)
    assert result["uuid"] == "new"
    # Should still make a POST call
    assert len(client.calls) == 1
    assert client.calls[0][0] == "POST"


def test_create_validate_order():
    """create validates before picklist resolution."""
    call_order = []

    class TrackingPicklists(_NoopPicklists):
        def resolve_record_fields(self, module, fields, strict=False, report=None):
            call_order.append("resolve")
            return fields

    client = FakeClient(picklists=TrackingPicklists(), responses={"/api/3/test_records": {"uuid": "new"}})

    # Patch _validate_record to track when it's called
    api = MockRecordAPI(client)
    original_validate = api._validate_record

    def tracking_validate(data):
        call_order.append("validate")
        return original_validate(data)

    api._validate_record = tracking_validate

    api.create(name="test", validate=True, resolve_picklists=True)

    # Validation should come before picklist resolution
    assert call_order == ["validate", "resolve"]


# -- validate parameter tests (update) ------------------------------------------


def test_update_with_validate_false():
    """update with validate=False (default) skips validation."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists, responses={"/api/3/test_records/r1": {"uuid": "r1"}})
    api = MockRecordAPI(client)

    result = api.update("r1", {"status": "Closed"}, validate=False, resolve_picklists=False)
    assert result["uuid"] == "r1"
    # Should make a PUT call
    assert len(client.calls) == 1
    assert client.calls[0][0] == "PUT"


def test_update_with_validate_true():
    """update with validate=True runs validation before PUT."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists, responses={"/api/3/test_records/r1": {"uuid": "r1"}})
    api = MockRecordAPI(client)

    result = api.update("r1", {"status": "Closed"}, validate=True, resolve_picklists=False)
    assert result["uuid"] == "r1"
    # Should still make a PUT call
    assert len(client.calls) == 1
    assert client.calls[0][0] == "PUT"


def test_update_validate_order():
    """update validates before picklist resolution."""
    call_order = []

    class TrackingPicklists(_NoopPicklists):
        def resolve_record_fields(self, module, fields, strict=False, report=None):
            call_order.append("resolve")
            return fields

    client = FakeClient(picklists=TrackingPicklists(), responses={"/api/3/test_records/r1": {"uuid": "r1"}})

    api = MockRecordAPI(client)
    original_validate = api._validate_record

    def tracking_validate(data):
        call_order.append("validate")
        return original_validate(data)

    api._validate_record = tracking_validate

    api.update("r1", {"status": "Closed"}, validate=True, resolve_picklists=True)

    # Validation should come before picklist resolution
    assert call_order == ["validate", "resolve"]


# -- _validate_record tests (placeholder) -----------------------------------------------


def test_validate_record_is_placeholder():
    """_validate_record is a placeholder that doesn't raise by default."""
    client = FakeClient()
    api = MockRecordAPI(client)

    # Should not raise
    api._validate_record({})
    api._validate_record({"name": "test", "status": "Open"})


def test_validate_record_accepts_any_dict():
    """_validate_record accepts any dict without validation logic."""
    client = FakeClient()
    api = MockRecordAPI(client)

    # Even with invalid/weird values, the placeholder should not raise
    api._validate_record({"field_1": None, "field_2": 123, "field_3": []})
    api._validate_record({})  # Empty dict


# -- integration: create + resolve_picklists + validate --------------------------------


def test_create_all_features():
    """create works with resolve_picklists and validate together."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists, responses={"/api/3/test_records": {"uuid": "new"}})
    api = MockRecordAPI(client)

    result = api.create(
        name="test",
        status="Open",
        resolve_picklists=True,
        validate=True,
    )
    assert result["uuid"] == "new"
    assert len(client.calls) == 1
    assert client.calls[0][0] == "POST"


def test_update_all_features():
    """update works with resolve_picklists and validate together."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists, responses={"/api/3/test_records/r1": {"uuid": "r1"}})
    api = MockRecordAPI(client)

    result = api.update(
        "r1",
        {"status": "Closed", "priority": "Low"},
        resolve_picklists=True,
        validate=True,
    )
    assert result["uuid"] == "r1"
    assert len(client.calls) == 1
    assert client.calls[0][0] == "PUT"


def test_create_with_record_link_and_validate():
    """create accepts record link with validation enabled."""
    picklists = _NoopPicklists()
    client = FakeClient(picklists=picklists, responses={"/api/3/test_records": {"uuid": "new"}})
    api = MockRecordAPI(client)

    result = api.create(
        name="test",
        record="/api/3/alerts/alert-1",
        resolve_picklists=False,
        validate=True,
    )
    assert result["uuid"] == "new"
    # The record link adds an "alerts" field to data before validation
    assert client.calls[0][3]["alerts"] == ["/api/3/alerts/alert-1"]
