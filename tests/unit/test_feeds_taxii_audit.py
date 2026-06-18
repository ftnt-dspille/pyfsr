"""Unit tests for the feeds / TAXII / audit / api-users / system / search wrappers."""

import pytest

from pyfsr.api.api_users import ApiKeyUsersAPI
from pyfsr.api.audit import AuditAPI
from pyfsr.api.feeds import IngestFeedsAPI
from pyfsr.api.search import SearchAPI
from pyfsr.api.system import SystemAPI
from pyfsr.api.taxii import TaxiiAPI


class FakeClient:
    def __init__(self, *, get_resp=None, post_resp=None, put_resp=None):
        self.calls = []
        self._get = get_resp
        self._post = post_resp
        self._put = put_resp

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        return {} if self._get is None else self._get

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data, params))
        return {} if self._post is None else self._post

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return {} if self._put is None else self._put

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, kw.get("data"), params))


# --------------------------------------------------------------------- feeds
def test_feeds_indicators_posts_list():
    c = FakeClient()
    IngestFeedsAPI(c).indicators([{"value": "8.8.8.8"}])
    assert c.calls[-1][:3] == ("POST", "/api/ingest-feeds/indicators", [{"value": "8.8.8.8"}])


def test_feeds_stix_bundle_posts_dict():
    c = FakeClient()
    IngestFeedsAPI(c).stix_bundle({"type": "bundle", "objects": []})
    assert c.calls[-1][1] == "/api/ingest-feeds/stix-bundle"


def test_feeds_insert_generic_record_type():
    c = FakeClient()
    IngestFeedsAPI(c).insert("events", [{"a": 1}])
    assert c.calls[-1][1] == "/api/insert-feeds/events"


def test_feeds_insert_rejects_blank():
    with pytest.raises(ValueError):
        IngestFeedsAPI(FakeClient()).insert("  ", [])


# --------------------------------------------------------------------- taxii
def test_taxii_collections_unwraps():
    c = FakeClient(get_resp={"collections": [{"id": "c1"}]})
    assert TaxiiAPI(c).collections() == [{"id": "c1"}]
    assert c.calls[-1][1] == "/api/taxii/1/collections"


def test_taxii_objects_passes_paging():
    c = FakeClient(get_resp={"totalItems": 0, "objects": []})
    TaxiiAPI(c).objects("c1", limit=50, added_after="2026-01-01T00:00:00Z")
    method, endpoint, params = c.calls[-1]
    assert endpoint == "/api/taxii/1/collections/c1/objects"
    assert params == {"limit": 50, "added_after": "2026-01-01T00:00:00Z"}


def test_taxii_discovery_trailing_slash():
    c = FakeClient()
    TaxiiAPI(c).discovery()
    assert c.calls[-1][1] == "/api/taxii/1/"


# --------------------------------------------------------------------- audit
def test_audit_activities_builds_filter_body():
    c = FakeClient(post_resp={"content": []})
    AuditAPI(c).activities("S", "E", operation="login", limit=100, user_id="u1")
    method, endpoint, data, params = c.calls[-1]
    assert endpoint == "/api/gateway/audit/activities"
    assert data == {
        "startDate": "S",
        "endDate": "E",
        "limit": 100,
        "operation": "login",
        "userId": "u1",
    }


def test_audit_count_omits_paging():
    c = FakeClient()
    AuditAPI(c).count("S", "E")
    assert c.calls[-1][2] == {"startDate": "S", "endDate": "E"}
    assert c.calls[-1][1] == "/api/gateway/audit/activities/count"


def test_audit_disable_ttl_and_operations():
    c = FakeClient()
    AuditAPI(c).disable_ttl()
    assert c.calls[-1][:2] == ("DELETE", "/api/gateway/audit/activities/ttl")
    AuditAPI(c).operations()
    assert c.calls[-1][1] == "/api/gateway/audit/operations"


# ----------------------------------------------------------------- api_users
def test_api_users_create_defaults():
    c = FakeClient(post_resp={"uuid": "u-1"})
    out = ApiKeyUsersAPI(c).create(api_key_validity=365)
    assert out["uuid"] == "u-1"
    assert c.calls[-1][2] == {"type": 9, "status": 1, "api_key_validity": 365}


def test_api_users_get_show_key():
    c = FakeClient(get_resp={"uuid": "u-1"})
    ApiKeyUsersAPI(c).get("u-1", show_api_key=True)
    assert c.calls[-1] == ("GET", "/api/auth/users", {"uuid": "u-1", "show_api_key": "true"})


def test_api_users_revoke_uses_put_operation():
    c = FakeClient()
    ApiKeyUsersAPI(c).revoke("u-1")
    method, endpoint, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/auth/users"
    assert data == {"uuid": "u-1", "key_type": "api_key", "operation": "REVOKE"}


def test_api_users_reset_validity_includes_days():
    c = FakeClient()
    ApiKeyUsersAPI(c).reset_validity("u-1", 90)
    assert c.calls[-1][2]["operation"] == "RESET_VALIDITY"
    assert c.calls[-1][2]["api_key_validity"] == 90


def test_api_users_bad_operation_raises():
    with pytest.raises(ValueError):
        ApiKeyUsersAPI(FakeClient()).lifecycle("u-1", "BOGUS")


# -------------------------------------------------------------------- system
def test_system_simple_gets():
    c = FakeClient(get_resp={"ok": True})
    s = SystemAPI(c)
    s.version()
    assert c.calls[-1][1] == "/api/version"
    s.permissions()
    assert c.calls[-1][1] == "/api/permissions/current"
    s.feature_access()
    assert c.calls[-1][1] == "/api/product/feature-access"
    s.cluster_health()
    assert c.calls[-1][1] == "/api/auth/cluster/health"


def test_system_deploy_license_public_action():
    c = FakeClient()
    SystemAPI(c).deploy_license_public("KEY-123", node_id="n1")
    assert c.calls[-1][1] == "/api/public/license"
    assert c.calls[-1][2] == {"action": "deploy_license", "license_key": "KEY-123", "nodeId": "n1"}


# -------------------------------------------------------------------- search
def test_search_builds_body():
    c = FakeClient(post_resp={"hits": []})
    SearchAPI(c).search("8.8.8.8", index=["alerts"], size=10)
    method, endpoint, data, params = c.calls[-1]
    assert endpoint == "/api/search"
    assert data == {"q": "8.8.8.8", "index": ["alerts"], "size": 10}


def test_search_min_length_enforced():
    with pytest.raises(ValueError):
        SearchAPI(FakeClient()).search("ab", index=["alerts"])


def test_search_run_persisted():
    c = FakeClient()
    SearchAPI(c).run_persisted("alerts", "q-1", limit=50, orderby="+name")
    assert c.calls[-1][1] == "/api/query/alerts/q-1"
    assert c.calls[-1][2] == {"$limit": 50, "$orderby": "+name"}
