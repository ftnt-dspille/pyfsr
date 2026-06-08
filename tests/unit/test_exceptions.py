"""Unit tests for structured exception metadata from FSR error bodies."""

import pytest

from pyfsr.exceptions import (
    APIError,
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


def test_non_json_body_degrades_gracefully():
    class Bad(FakeResponse):
        def json(self):
            raise ValueError("not json")

    with pytest.raises(APIError) as exc:
        handle_api_error(Bad(500, "boom"))
    assert exc.value.status_code == 500
