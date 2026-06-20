"""Unit tests for workflow-collection CRUD."""

import pytest

from pyfsr.api.workflow_collections import WorkflowCollectionsAPI


class RecordingClient:
    """Records calls and returns canned responses."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        if endpoint in self.responses:
            return self.responses[endpoint]
        return {"hydra:member": [{"uuid": "c-1", "name": "Pack"}], "hydra:totalItems": 1}

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"ok": True}

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, params, data))
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
    a.create_collection("My Pack", description="d", workflows=wfs, uuid="fixed-uuid")
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/3/workflow_collections"
    assert data["@type"] == "WorkflowCollection"
    assert data["name"] == "My Pack" and data["description"] == "d"
    assert data["uuid"] == "fixed-uuid" and data["visible"] is True
    assert data["workflows"] == wfs
    assert data["recordTags"] == []


def test_create_generates_uuid_when_omitted():
    a, c = api()
    a.create_collection("Pack")
    col = c.calls[-1][2]
    assert isinstance(col["uuid"], str) and len(col["uuid"]) == 36


def test_create_rejects_empty_name():
    a, _ = api()
    with pytest.raises(ValueError):
        a.create_collection("  ")


def test_update_puts_partial_fields():
    a, c = api()
    a.update("c-1", name="Renamed", visible=False)
    method, endpoint, params, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/3/workflow_collections/c-1"
    assert params is None
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


def test_upsert_posts_to_upsert_path():
    a, c = api()
    a.upsert({"uuid": "c-1", "name": "Pack"})
    assert c.calls[-1] == (
        "POST",
        "/api/3/upsert/workflow_collections",
        {"uuid": "c-1", "name": "Pack"},
    )


def test_create_collections_posts_to_bulk_upsert_path():
    a, c = api()
    a.create_collections([{"uuid": "c-1", "name": "Pack"}])
    assert c.calls[-1] == (
        "POST",
        "/api/3/bulkupsert/workflow_collections",
        [{"uuid": "c-1", "name": "Pack"}],
    )


def test_import_export_strips_export_metadata():
    a, c = api()
    a.import_export({"type": "workflow_collections", "data": [{"@context": "x", "uuid": "c-1", "name": "Pack"}]})
    assert c.calls[-1] == ("POST", "/api/3/workflow_collections", {"uuid": "c-1", "name": "Pack"})


def test_import_export_replace_hard_deletes_existing():
    uuid = "46a177c6-200c-425a-b16d-c52ebb915d6b"
    responses = {f"/api/3/workflow_collections/{uuid}": {"uuid": uuid, "name": "Pack"}}
    a = WorkflowCollectionsAPI(RecordingClient(responses=responses))
    a.import_export({"type": "workflow_collections", "data": [{"uuid": uuid, "name": "Pack"}]}, replace=True)
    assert a.client.calls[0] == ("GET", f"/api/3/workflow_collections/{uuid}", None)
    assert a.client.calls[1] == (
        "DELETE",
        f"/api/3/workflow_collections/{uuid}",
        {"$hardDelete": "true", "$showDeleted": "true"},
    )


def test_restore_uses_recordset_restore():
    uuid = "46a177c6-200c-425a-b16d-c52ebb915d6b"
    responses = {f"/api/3/workflow_collections/{uuid}": {"uuid": uuid, "deletedAt": 1}}
    a = WorkflowCollectionsAPI(RecordingClient(responses=responses))
    a.restore(uuid)
    assert a.client.calls[0] == (
        "GET",
        f"/api/3/workflow_collections/{uuid}",
        {"$showDeleted": "true"},
    )
    assert a.client.calls[1] == (
        "PUT",
        f"/api/3/workflow_collections/{uuid}",
        {"$showDeleted": "true"},
        {"uuid": uuid, "deletedAt": None},
    )


def test_uuid_validation():
    a, _ = api()
    for op in (lambda: a.get(""), lambda: a.update("", name="x"), lambda: a.delete("  ")):
        with pytest.raises(ValueError):
            op()
