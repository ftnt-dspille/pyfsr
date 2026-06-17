"""Unit tests for workflow-collection CRUD."""

import pytest

from pyfsr.api.workflow_collections import WorkflowCollectionsAPI


class RecordingClient:
    """Records calls and returns canned responses."""

    def __init__(self):
        self.calls = []

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        return {"hydra:member": [{"uuid": "c-1", "name": "Pack"}], "hydra:totalItems": 1}

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"ok": True}

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return {"ok": True, **(data or {})}

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))


def api():
    c = RecordingClient()
    return WorkflowCollectionsAPI(c), c


def test_list_returns_members_and_sets_limit():
    a, c = api()
    out = a.list()
    assert out == [{"uuid": "c-1", "name": "Pack"}]
    method, endpoint, params = c.calls[-1]
    assert method == "GET" and endpoint == "/api/3/workflow_collections"
    assert params["$limit"] == 2147483647 and "$relationships" not in params


def test_list_relationships_flag():
    a, c = api()
    a.list(relationships=True)
    assert c.calls[-1][2]["$relationships"] == "true"


def test_get_inlines_relationships_by_default():
    a, c = api()
    a.get("c-1")
    method, endpoint, params = c.calls[-1]
    assert endpoint == "/api/3/workflow_collections/c-1"
    assert params == {"$relationships": "true"}
    a.get("c-1", relationships=False)
    assert c.calls[-1][2] is None


def test_create_builds_import_envelope():
    a, c = api()
    wfs = [{"@type": "Workflow", "name": "wf"}]
    a.create("My Pack", description="d", workflows=wfs, uuid="fixed-uuid")
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/3/workflow_collections"
    assert data["type"] == "workflow_collections"
    assert data["macros"] == [] and data["exported_tags"] == []
    (col,) = data["data"]
    assert col["@type"] == "WorkflowCollection"
    assert col["name"] == "My Pack" and col["description"] == "d"
    assert col["uuid"] == "fixed-uuid" and col["visible"] is True
    assert col["workflows"] == wfs


def test_create_generates_uuid_when_omitted():
    a, c = api()
    a.create("Pack")
    (col,) = c.calls[-1][2]["data"]
    assert isinstance(col["uuid"], str) and len(col["uuid"]) == 36


def test_create_rejects_empty_name():
    a, _ = api()
    with pytest.raises(ValueError):
        a.create("  ")


def test_update_puts_partial_fields():
    a, c = api()
    a.update("c-1", name="Renamed", visible=False)
    method, endpoint, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/3/workflow_collections/c-1"
    assert data == {"name": "Renamed", "visible": False}


def test_update_requires_a_field():
    a, _ = api()
    with pytest.raises(ValueError):
        a.update("c-1")


def test_delete_hard_sends_no_body_with_flags():
    a, c = api()
    a.delete("c-1")
    method, endpoint, params = c.calls[-1]
    assert method == "DELETE" and endpoint == "/api/3/workflow_collections/c-1"
    assert params == {"$hardDelete": "true", "$showDeleted": "true"}


def test_delete_soft():
    a, c = api()
    a.delete("c-1", hard=False)
    assert c.calls[-1][2] is None


def test_uuid_validation():
    a, _ = api()
    for op in (lambda: a.get(""), lambda: a.update("", name="x"), lambda: a.delete("  ")):
        with pytest.raises(ValueError):
            op()
