"""Unit tests for structured exception metadata from FSR error bodies."""

import pytest

from pyfsr.exceptions import (
    APIError,
    PermissionError,
    ResourceNotFoundError,
    ValidationError,
    handle_api_error,
)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_validation_error_carries_type_and_status():
    resp = FakeResponse(400, {"type": "ValidationException", "message": "bad field"})
    with pytest.raises(ValidationError) as exc:
        handle_api_error(resp)
    assert exc.value.status_code == 400
    assert exc.value.error_type == "ValidationException"
    assert exc.value.message == "bad field"


def test_not_found_carries_status():
    resp = FakeResponse(404, {"type": "NotFound", "message": "gone"})
    with pytest.raises(ResourceNotFoundError) as exc:
        handle_api_error(resp)
    assert exc.value.status_code == 404
    assert exc.value.error_type == "NotFound"


def test_generic_400_without_validation_type():
    resp = FakeResponse(400, {"message": "nope"})
    with pytest.raises(APIError) as exc:
        handle_api_error(resp)
    assert exc.value.status_code == 400
    assert exc.value.error_type == ""


def test_teamless_actor_message_becomes_permission_error_with_hint():
    """FSR's 'must be on at least one team' 403 is translated to an actionable hint."""
    resp = FakeResponse(
        403,
        {"type": "AccessDenied", "message": "A user must be on at least one team in order to create records."},
    )
    with pytest.raises(PermissionError) as exc:
        handle_api_error(resp)
    assert exc.value.status_code == 403
    text = str(exc.value)
    assert "must be on at least one team" in text.lower()  # original message preserved
    assert "team" in text.lower() and "API key" in text  # hint names the fix


def test_teamless_hint_fires_regardless_of_type_field():
    """The team hint keys off the message, not the error type (FSR omits a useful type here)."""
    resp = FakeResponse(403, {"message": "Access Denied: a user must be on at least one team."})
    with pytest.raises(PermissionError) as exc:
        handle_api_error(resp)
    assert "username/password" in str(exc.value) or "JWT" in str(exc.value)


def test_non_json_body_degrades_gracefully():
    class Bad(FakeResponse):
        def json(self):
            raise ValueError("not json")

    with pytest.raises(APIError) as exc:
        handle_api_error(Bad(500, "boom"))
    assert exc.value.status_code == 500
