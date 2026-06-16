"""Unit tests for the generic RecordSet CRUD layer."""

import pytest

from pyfsr import Query, RecordSet
from pyfsr.records import resolve_record_path


class _NoopPicklists:
    """Identity picklist resolver, no HTTP call.

    create/update/upsert resolve picklists by default now; these tests exercise
    write *mechanics*, so resolution is a passthrough that doesn't perturb the
    recorded call sequence.
    """

    def resolve_record_fields(self, module, fields, **kwargs):
        return fields


class FakeClient:
    """Records get/post/put/delete calls and returns scripted responses."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        self.picklists = _NoopPicklists()

    def get(self, endpoint, params=None, **kwargs):
        self.calls.append(("GET", endpoint, params, None))
        return self._resp(endpoint)

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.calls.append(("POST", endpoint, params, data))
        return self._resp(endpoint)

    def put(self, endpoint, data=None, params=None, **kwargs):
        self.calls.append(("PUT", endpoint, params, data))
        return data if data is not None else self._resp(endpoint)

    def delete(self, endpoint, params=None, **kwargs):
        self.calls.append(("DELETE", endpoint, params, None))
        return None

    def _resp(self, endpoint):
        if callable(self.responses):
            return self.responses(endpoint)
        return self.responses.get(endpoint, {"hydra:member": []})


# -- path resolution --------------------------------------------------------
def test_resolve_record_path_uuid():
    assert resolve_record_path("alerts", "abc-123") == "/api/3/alerts/abc-123"


def test_resolve_record_path_iri_passthrough():
    assert resolve_record_path("alerts", "/api/3/alerts/abc") == "/api/3/alerts/abc"


def test_resolve_record_path_module_colon_shorthand():
    assert resolve_record_path("alerts", "incidents:xyz") == "/api/3/incidents/xyz"


# -- reads ------------------------------------------------------------------
def test_get_by_uuid():
    client = FakeClient({"/api/3/incidents/u1": {"uuid": "u1"}})
    rec = RecordSet(client, "incidents").get("u1")
    assert rec["uuid"] == "u1"  # dict-compatible access on the typed model
    assert rec.uuid == "u1"
    assert client.calls[0] == ("GET", "/api/3/incidents/u1", None, None)
    # raw=True returns the plain dict
    assert RecordSet(client, "incidents").get("u1", raw=True) == {"uuid": "u1"}


def test_get_with_relationships():
    client = FakeClient()
    RecordSet(client, "incidents").get("u1", relationships=True)
    assert client.calls[0] == ("GET", "/api/3/incidents/u1", {"$relationships": "true"}, None)


def test_list_sets_limit_and_page():
    client = FakeClient({"/api/3/alerts": {"hydra:member": [1, 2], "hydra:totalItems": 2}})
    page = RecordSet(client, "alerts").list(limit=10, page=2)
    assert page.members == [1, 2]
    assert page.total == 2
    assert client.calls[0] == ("GET", "/api/3/alerts", {"$limit": 10, "$page": 2}, None)


def test_search_adds_search_param():
    client = FakeClient()
    RecordSet(client, "alerts").search("malware", limit=5)
    method, endpoint, params, _ = client.calls[0]
    assert params["$search"] == "malware"
    assert params["$limit"] == 5


def test_query_posts_to_query_endpoint():
    client = FakeClient(
        {"/api/query/incidents": {"hydra:member": [{"uuid": "x"}], "hydra:totalItems": 1}}
    )
    q = Query().eq("status.itemValue", "Open").limit(50)
    page = RecordSet(client, "incidents").query(q)
    method, endpoint, params, data = client.calls[0]
    assert (method, endpoint) == ("POST", "/api/query/incidents")
    assert data["filters"][0]["operator"] == "eq"
    # limit/page travel as $-params, NOT in the body (FSR ignores body limit).
    assert "limit" not in data
    assert params == {"$page": 1, "$limit": 50}
    assert page.members[0]["uuid"] == "x"


def test_iterate_walks_pages_via_query():
    pages = {
        1: {"hydra:member": [{"i": 1}, {"i": 2}]},
        2: {"hydra:member": [{"i": 3}]},
    }
    seen_pages = []

    def responder(endpoint):
        return None  # unused; overridden below

    client = FakeClient()

    def post(endpoint, data=None, params=None, **kwargs):
        page = params["$page"]
        seen_pages.append(page)
        assert params["$limit"] == 2  # page_size drives $limit
        return pages.get(page, {"hydra:member": []})

    client.post = post
    out = list(RecordSet(client, "incidents").iterate(Query().eq("x", 1), page_size=2))
    assert [r["i"] for r in out] == [1, 2, 3]
    assert seen_pages == [1, 2]


def test_iterate_without_query_uses_list_endpoint():
    pages = {1: {"hydra:member": [{"i": 1}]}, 2: {"hydra:member": []}}
    client = FakeClient()

    def get(endpoint, params=None, **kwargs):
        return pages.get(params["$page"], {"hydra:member": []})

    client.get = get
    out = list(RecordSet(client, "alerts").iterate(page_size=1))
    assert [r["i"] for r in out] == [1]


# -- writes -----------------------------------------------------------------
def test_create():
    client = FakeClient({"/api/3/alerts": {"uuid": "new"}})
    rec = RecordSet(client, "alerts").create({"name": "x"})
    assert client.calls[0] == ("POST", "/api/3/alerts", None, {"name": "x"})
    assert rec["uuid"] == "new"


def test_update_uses_put():
    client = FakeClient()
    RecordSet(client, "alerts").update("u1", {"severity": "High"})
    assert client.calls[0] == ("PUT", "/api/3/alerts/u1", None, {"severity": "High"})


def test_delete():
    client = FakeClient()
    RecordSet(client, "alerts").delete("u1")
    assert client.calls[0] == ("DELETE", "/api/3/alerts/u1", None, None)


# -- P4: safe deletes + recycle ---------------------------------------------
def test_hard_delete_sets_param():
    client = FakeClient()
    RecordSet(client, "alerts").delete("u1", hard=True)
    assert client.calls[0] == ("DELETE", "/api/3/alerts/u1", {"$hardDelete": "true"}, None)


def test_delete_module_colon_and_iri():
    client = FakeClient()
    RecordSet(client, "alerts").delete("incidents:x")
    RecordSet(client, "alerts").delete("/api/3/alerts/y", hard=True)
    assert client.calls[0] == ("DELETE", "/api/3/incidents/x", None, None)
    assert client.calls[1] == ("DELETE", "/api/3/alerts/y", {"$hardDelete": "true"}, None)


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_delete_rejects_blank_ref(bad):
    client = FakeClient()
    with pytest.raises(ValueError):
        RecordSet(client, "alerts").delete(bad)
    assert client.calls == []  # never reaches the wire


def test_restore_clears_deleted_at_and_puts():
    deleted = {"@id": "/api/3/alerts/u1", "uuid": "u1", "name": "x", "deletedAt": 1234.5}
    client = FakeClient({"/api/3/alerts/u1": deleted})
    rec = RecordSet(client, "alerts", typed=False).restore("u1")
    get_call, put_call = client.calls
    assert get_call == ("GET", "/api/3/alerts/u1", {"$showDeleted": "true"}, None)
    method, endpoint, params, data = put_call
    assert (method, endpoint, params) == ("PUT", "/api/3/alerts/u1", {"$showDeleted": "true"})
    assert data["deletedAt"] is None
    assert data["uuid"] == "u1"  # full prior body preserved
    assert rec["deletedAt"] is None


def test_restore_rejects_blank_ref():
    client = FakeClient()
    with pytest.raises(ValueError):
        RecordSet(client, "alerts").restore("")


# -- P4: show_deleted plumbing ----------------------------------------------
def test_get_show_deleted_param():
    client = FakeClient({"/api/3/alerts/u1": {"uuid": "u1"}})
    RecordSet(client, "alerts").get("u1", show_deleted=True)
    assert client.calls[0] == ("GET", "/api/3/alerts/u1", {"$showDeleted": "true"}, None)


def test_list_show_deleted_param():
    client = FakeClient()
    RecordSet(client, "alerts").list(show_deleted=True)
    _, endpoint, params, _ = client.calls[0]
    assert endpoint == "/api/3/alerts"
    assert params["$showDeleted"] == "true"


def test_query_show_deleted_param_and_body():
    client = FakeClient()
    RecordSet(client, "alerts").query(Query().limit(5), show_deleted=True)
    method, endpoint, params, data = client.calls[0]
    assert method == "POST"
    assert params["$showDeleted"] == "true"
    assert data["showDeleted"] is True


def test_records_accessor_on_client(mock_client):
    rs = mock_client.records("alerts")
    assert isinstance(rs, RecordSet)
    assert rs.module == "alerts"


# -- upsert / bulk_upsert ---------------------------------------------------
def test_upsert_posts_to_upsert_path():
    client = FakeClient({"/api/3/upsert/alerts": {"uuid": "u9", "name": "x"}})
    rec = RecordSet(client, "alerts").upsert({"name": "x"})
    assert client.calls[0] == ("POST", "/api/3/upsert/alerts", None, {"name": "x"})
    assert rec["uuid"] == "u9"


def test_upsert_raw_returns_plain_dict():
    client = FakeClient({"/api/3/upsert/alerts": {"uuid": "u9"}})
    assert RecordSet(client, "alerts").upsert({"name": "x"}, raw=True) == {"uuid": "u9"}


def test_bulk_upsert_posts_list_and_returns_raw():
    client = FakeClient({"/api/3/bulkupsert/workflow_collections": {"hydra:member": [1, 2]}})
    rows = [{"name": "a"}, {"name": "b"}]
    out = RecordSet(client, "workflow_collections").bulk_upsert(rows)
    method, endpoint, _params, data = client.calls[0]
    assert (method, endpoint) == ("POST", "/api/3/bulkupsert/workflow_collections")
    assert data == rows
    assert out == {"hydra:member": [1, 2]}
