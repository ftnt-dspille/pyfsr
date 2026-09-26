"""Unit tests for ``client.playbooks.set_step_timeout`` / ``clear_step_timeout`` (8.0.1)."""

import pytest

from pyfsr.api.playbooks import PlaybooksAPI

STEP = "11111111-2222-3333-4444-555555555555"


class FakeClient:
    def __init__(self, arguments=None):
        self.calls = []
        default = {"connector": "cyops_utilities", "operation": "api_call"}
        self.arguments = arguments if arguments is not None else default

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, None))
        return {"@id": endpoint, "arguments": dict(self.arguments)}

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return data


def test_sets_timeout_block_and_keeps_other_arguments():
    c = FakeClient()
    PlaybooksAPI(c).set_step_timeout(STEP, operation_timeout=5, retry=2)
    method, endpoint, body = c.calls[-1]
    assert (method, endpoint) == ("PUT", f"/api/3/workflow_steps/{STEP}")
    assert body["arguments"]["timeout"] == {"operation_timeout": 5, "retry": 2}
    assert body["arguments"]["operation"] == "api_call"


def test_accepts_step_iri():
    c = FakeClient()
    PlaybooksAPI(c).set_step_timeout(f"/api/3/workflow_steps/{STEP}", operation_timeout=10)
    assert c.calls[0][1] == f"/api/3/workflow_steps/{STEP}"


def test_clear_removes_only_timeout():
    c = FakeClient({"operation": "api_call", "timeout": {"operation_timeout": 5, "retry": 2}})
    PlaybooksAPI(c).clear_step_timeout(STEP)
    assert c.calls[-1][2]["arguments"] == {"operation": "api_call"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"operation_timeout": 0},
        {"operation_timeout": -5},
        {"operation_timeout": 5.5},
        {"operation_timeout": "5"},
        {"operation_timeout": True},
        {"operation_timeout": 5, "retry": -1},
        {"operation_timeout": 5, "retry": 1.0},
        {"operation_timeout": 600, "retry": 2},  # 1800s total == limit, must be under
        {"operation_timeout": 1000, "retry": 2},
    ],
)
def test_rejects_values_the_designer_would_reject(kwargs):
    c = FakeClient()
    with pytest.raises(ValueError):
        PlaybooksAPI(c).set_step_timeout(STEP, **kwargs)
    assert c.calls == []  # validated before any request


def test_just_under_the_limit_is_allowed():
    c = FakeClient()
    PlaybooksAPI(c).set_step_timeout(STEP, operation_timeout=599, retry=2)  # 1797s
    assert c.calls[-1][0] == "PUT"
