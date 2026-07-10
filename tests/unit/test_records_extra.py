"""Additional RecordSet coverage: first/count/exists query variants, BaseRecord
inputs to write methods, upsert-with-key update path, projection, and insert
response normalization."""

import pytest

from pyfsr import Query, RecordSet
from pyfsr.models.base import BaseRecord


class _NoopPicklists:
    def resolve_record_fields(self, module, fields, **kwargs):
        return fields


class FakeClient:
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

    def _resp(self, endpoint):
        if callable(self.responses):
            return self.responses(endpoint)
        return self.responses.get(endpoint, {"hydra:member": []})


class SampleRecord(BaseRecord):
    name: str | None = None
    severity: str | None = None


def _page(members, total=None):
    d = {"hydra:member": members}
    if total is not None:
        d["hydra:totalItems"] = total
    return d


# -- first -------------------------------------------------------------------
def test_first_no_query_uses_list_limit_1():
    c = FakeClient({"/api/3/alerts": _page([{"uuid": "u1"}])})
    rec = RecordSet(c, "alerts").first()
    assert rec["uuid"] == "u1"
    assert c.calls[-1][0] == "GET" and c.calls[-1][2]["$limit"] == 1


def test_first_with_query_object_limits_to_1():
    c = FakeClient({"/api/query/alerts": _page([{"uuid": "q1"}])})
    rec = RecordSet(c, "alerts").first(Query().eq("status", "Open"))
    assert rec["uuid"] == "q1"
    assert c.calls[-1][0] == "POST" and c.calls[-1][1] == "/api/query/alerts"


def test_first_with_dict_query_limits_to_1():
    c = FakeClient({"/api/query/alerts": _page([{"uuid": "d1"}])})
    rec = RecordSet(c, "alerts").first({"logic": "AND", "filters": []})
    assert rec["uuid"] == "d1"
    # limit lifted from body into $limit param
    assert c.calls[-1][2]["$limit"] == 1


def test_first_returns_none_when_empty():
    c = FakeClient({"/api/3/alerts": _page([])})
    assert RecordSet(c, "alerts").first() is None


# -- count -------------------------------------------------------------------
def test_count_no_query_returns_total():
    c = FakeClient({"/api/3/alerts": _page([{"uuid": "u1"}], total=42)})
    assert RecordSet(c, "alerts").count() == 42


def test_count_with_query_object():
    c = FakeClient({"/api/query/alerts": _page([{"uuid": "u1"}], total=7)})
    assert RecordSet(c, "alerts").count(Query().eq("status", "Open")) == 7


def test_count_with_dict_query():
    c = FakeClient({"/api/query/alerts": _page([{"uuid": "u1"}], total=3)})
    assert RecordSet(c, "alerts").count({"filters": []}) == 3


# -- exists ------------------------------------------------------------------
def test_exists_no_query_true_when_members():
    c = FakeClient({"/api/3/alerts": _page([{"uuid": "u1"}])})
    assert RecordSet(c, "alerts").exists() is True


def test_exists_with_query_object_false_when_empty():
    c = FakeClient({"/api/query/alerts": _page([])})
    assert RecordSet(c, "alerts").exists(Query().eq("sourceId", "x")) is False


def test_exists_with_dict_query():
    c = FakeClient({"/api/query/alerts": _page([{"uuid": "u1"}])})
    assert RecordSet(c, "alerts").exists({"filters": []}) is True


# -- BaseRecord inputs to write methods --------------------------------------
def test_update_accepts_base_record():
    c = FakeClient()
    RecordSet(c, "alerts").update("u1", SampleRecord(severity="High"))
    method, endpoint, _, data = c.calls[-1]
    assert method == "PUT" and data == {"severity": "High"}


def test_upsert_natural_key_accepts_base_record():
    c = FakeClient({"/api/3/upsert/alerts": {"uuid": "up1"}})
    rec = RecordSet(c, "alerts").upsert(SampleRecord(name="x"))
    assert rec["uuid"] == "up1"
    assert c.calls[-1][1] == "/api/3/upsert/alerts"
    assert c.calls[-1][3] == {"name": "x"}


# -- upsert with custom key --------------------------------------------------
def test_upsert_with_key_updates_when_exists():
    # get_or_create finds an existing record -> update path
    def responses(endpoint):
        if endpoint == "/api/query/alerts":
            return _page([{"@id": "/api/3/alerts/u9", "uuid": "u9", "alias": "a"}])
        return {"uuid": "u9", "alias": "a", "name": "updated"}

    c = FakeClient(responses)
    rec = RecordSet(c, "alerts").upsert({"alias": "a", "name": "updated"}, key="alias")
    # last call is a PUT to update the existing record
    assert c.calls[-1][0] == "PUT" and "/api/3/alerts/u9" in c.calls[-1][1]
    assert rec is not None


def test_upsert_with_key_creates_when_absent():
    def responses(endpoint):
        if endpoint == "/api/query/alerts":
            return _page([])  # not found -> create
        return {"@id": "/api/3/alerts/new", "uuid": "new", "alias": "b"}

    c = FakeClient(responses)
    rec = RecordSet(c, "alerts").upsert({"alias": "b", "name": "n"}, key="alias")
    assert rec["uuid"] == "new"


# -- insert response normalization -------------------------------------------
def test_bulk_insert_normalizes_all_succeeded_bare_collection():
    # all-succeeded comes back as a bare hydra:Collection with no success/failure
    c = FakeClient({"/api/3/insert/alerts": {"hydra:member": [{"uuid": "i1"}, {"uuid": "i2"}]}})
    result = RecordSet(c, "alerts").bulk_insert([{"name": "a"}, {"name": "b"}], parse=True)
    assert len(result.succeeded) == 2
    assert result.failed == []


def test_bulk_insert_accepts_base_records_and_parse_false():
    c = FakeClient({"/api/3/insert/alerts": {"success": [{"uuid": "i1"}], "failure": []}})
    raw = RecordSet(c, "alerts").bulk_insert([SampleRecord(name="a")], parse=False)
    # parse=False returns the raw response dict
    assert raw == {"success": [{"uuid": "i1"}], "failure": []}
    assert c.calls[-1][3] == {"data": [{"name": "a"}]}


# -- projection on get -------------------------------------------------------
def test_get_with_fields_projects():
    c = FakeClient({"/api/3/alerts/u1": {"uuid": "u1", "name": "n", "severity": "High"}})
    out = RecordSet(c, "alerts").get("u1", fields=["uuid", "name"])
    assert "severity" not in out
    assert out["name"] == "n"


# -- query projection --------------------------------------------------------
def test_query_with_fields_returns_trimmed_dict():
    c = FakeClient({"/api/query/alerts": _page([{"uuid": "u1", "name": "n", "severity": "High"}])})
    out = RecordSet(c, "alerts").query({"filters": []}, fields=["uuid", "name"])
    assert all("severity" not in m for m in out["members"])


# -- iterate with query + show_deleted --------------------------------------
def test_iterate_with_query_and_show_deleted():
    c = FakeClient({"/api/query/alerts": _page([{"uuid": "u1"}])})
    out = list(RecordSet(c, "alerts").iterate(Query().eq("x", 1), page_size=50, show_deleted=True))
    assert out[0]["uuid"] == "u1"
    post = c.calls[-1]
    assert post[0] == "POST" and post[2]["$showDeleted"] == "true"
    assert post[3]["showDeleted"] is True


# -- get_or_create validation ------------------------------------------------
def test_get_or_create_rejects_empty_key():
    c = FakeClient()
    with pytest.raises(ValueError, match="non-empty field name"):
        RecordSet(c, "alerts").get_or_create({"name": "x"}, key=[])


def test_get_or_create_requires_key_field_in_data():
    c = FakeClient()
    with pytest.raises(ValueError, match="key field"):
        RecordSet(c, "alerts").get_or_create({"name": "x"}, key="alias")


# -- upsert-with-key missing ref ---------------------------------------------
def test_upsert_with_key_raises_when_existing_has_no_ref():
    def responses(endpoint):
        if endpoint == "/api/query/alerts":
            return _page([{"alias": "a", "name": "old"}])  # no @id/uuid/id
        return {}

    c = FakeClient(responses)
    with pytest.raises(ValueError, match="could not determine record reference"):
        RecordSet(c, "alerts").upsert({"alias": "a", "name": "new"}, key="alias")


# -- bulk_upsert BaseRecord input --------------------------------------------
def test_bulk_upsert_accepts_base_records():
    c = FakeClient({"/api/3/bulkupsert/alerts": {"hydra:member": []}})
    RecordSet(c, "alerts").bulk_upsert([SampleRecord(name="a")])
    assert c.calls[-1][1] == "/api/3/bulkupsert/alerts"
    assert c.calls[-1][3] == [{"name": "a"}]


# -- bulk_insert partial failure ---------------------------------------------
def test_bulk_insert_reports_failures():
    c = FakeClient({"/api/3/insert/alerts": {"success": [{"uuid": "i1"}], "failure": ["dup key on row 2"]}})
    result = RecordSet(c, "alerts").bulk_insert([{"name": "a"}, {"name": "b"}], parse=True)
    assert len(result.succeeded) == 1
    assert len(result.failed) == 1


# -- filter forwards to query ------------------------------------------------
def test_filter_forwards_to_query():
    c = FakeClient({"/api/query/alerts": _page([{"uuid": "u1"}])})
    page = RecordSet(c, "alerts").filter(Query().eq("status", "Open"))
    assert page.members[0]["uuid"] == "u1"
    assert c.calls[-1][1] == "/api/query/alerts"
