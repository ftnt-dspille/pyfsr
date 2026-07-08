"""Unit tests for the feeds / TAXII / audit / api-users / system / search wrappers."""

import pytest

from pyfsr.api.api_keys import ApiKeysAPI, _api_key_plaintext
from pyfsr.api.api_users import ApiKeyUsersAPI
from pyfsr.api.audit import AuditAPI
from pyfsr.api.feeds import FeedIngestResult, IngestFeedsAPI
from pyfsr.api.search import SearchAPI
from pyfsr.api.system import SystemAPI
from pyfsr.api.taxii import TaxiiAPI
from pyfsr.models import ApiKeyUser


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
def test_feeds_indicators_wraps_rows_in_data_envelope():
    c = FakeClient(post_resp={"status": "success", "uuids": ["u1"]})
    result = IngestFeedsAPI(c).indicators([{"value": "8.8.8.8"}])
    assert c.calls[-1][:3] == ("POST", "/api/ingest-feeds/indicators", {"data": [{"value": "8.8.8.8"}]})
    assert isinstance(result, FeedIngestResult)
    assert result.ok is True
    assert result.uuids == ["u1"]


def test_feeds_stix_bundle_posts_dict_unwrapped():
    c = FakeClient(post_resp={"status": "success"})
    IngestFeedsAPI(c).stix_bundle({"type": "bundle", "objects": []})
    assert c.calls[-1][1] == "/api/ingest-feeds/stix-bundle"
    assert c.calls[-1][2] == {"type": "bundle", "objects": []}


def test_feeds_insert_generic_record_type_wraps_data_envelope():
    c = FakeClient(post_resp={"status": "success", "uuids": []})
    result = IngestFeedsAPI(c).insert("events", [{"a": 1}])
    assert c.calls[-1][:3] == ("POST", "/api/insert-feeds/events", {"data": [{"a": 1}]})
    assert isinstance(result, FeedIngestResult)


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
    assert data == {"uuid": "u-1", "key_type": "API_KEY", "operation": "REVOKE"}


def test_api_users_reset_validity_includes_days():
    c = FakeClient()
    ApiKeyUsersAPI(c).reset_validity("u-1", 90)
    assert c.calls[-1][2]["operation"] == "RESET_VALIDITY"
    assert c.calls[-1][2]["api_key_validity"] == 90


def test_api_users_regenerate_sends_uppercase_key_type_and_validity():
    # Server requires key_type "API_KEY" (uppercase) AND api_key_validity for a
    # REGENERATE — both verified live on 7.6.5; a missing validity errors.
    c = FakeClient()
    ApiKeyUsersAPI(c).regenerate("u-1", api_key_validity=1)
    method, endpoint, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/auth/users"
    assert data == {
        "uuid": "u-1",
        "key_type": "API_KEY",
        "operation": "REGENERATE",
        "api_key_validity": 1,
    }


def test_api_users_bad_operation_raises():
    with pytest.raises(ValueError):
        ApiKeyUsersAPI(FakeClient()).lifecycle("u-1", "BOGUS")


# ------------------------------------------------------------------ api_keys
class _FakeUsers:
    """Stand-in for ``client.roles`` / ``client.teams`` — resolves known names
    to fake IRIs and passes IRIs through (mirrors the real
    :class:`~pyfsr.api.roles.RolesAPI` / :class:`~pyfsr.api.teams.TeamsAPI`)."""

    _TEAMS = {"TeamB": "/api/3/teams/t-1"}
    _ROLES = {"Admin": "/api/3/roles/r-1"}

    def _resolve_roles(self, roles):
        return [self._ROLES.get(r, r) for r in roles]

    def _resolve_teams(self, teams):
        return [self._TEAMS.get(t, t) for t in teams]


def _api_keys_client(*, get_resp=None, post_resp=None, put_resp=None):
    c = FakeClient(get_resp=get_resp, post_resp=post_resp, put_resp=put_resp)
    # api_keys delegates role/team resolution to client.roles / client.teams.
    c.roles = _FakeUsers()
    c.teams = _FakeUsers()
    return c


def test_api_keys_create_resolves_team_names_to_iris():
    c = _api_keys_client(post_resp={"uuid": "k-1"})
    out = ApiKeysAPI(c).create(name="repro-teamb", user_uuid="u-1", teams=["TeamB"])
    assert out["uuid"] == "k-1"
    assert c.calls[-1][:3] == (
        "POST",
        "/api/3/api_keys",
        {"name": "repro-teamb", "userId": "u-1", "teams": ["/api/3/teams/t-1"]},
    )


def test_api_keys_create_passes_iris_through():
    c = _api_keys_client(post_resp={"uuid": "k-1"})
    ApiKeysAPI(c).create(
        name="k",
        user_uuid="u-1",
        roles=["/api/3/roles/r-1"],
        teams=["/api/3/teams/t-1"],
    )
    body = c.calls[-1][2]
    assert body["roles"] == ["/api/3/roles/r-1"]
    assert body["teams"] == ["/api/3/teams/t-1"]


def test_api_keys_create_omits_empty_roles_teams():
    c = _api_keys_client(post_resp={"uuid": "k-1"})
    ApiKeysAPI(c).create(name="k", user_uuid="u-1")
    assert c.calls[-1][2] == {"name": "k", "userId": "u-1"}


def test_api_keys_list_unwraps_hydra_members():
    c = _api_keys_client(get_resp={"hydra:member": [{"name": "k1"}, {"name": "k2"}]})
    assert [k["name"] for k in ApiKeysAPI(c).list()] == ["k1", "k2"]


def test_api_keys_get_or_create_reuses_existing_by_name():
    c = _api_keys_client(get_resp={"hydra:member": [{"name": "repro-teamb", "uuid": "k-9"}]})
    binding, created = ApiKeysAPI(c).get_or_create(name="repro-teamb", user_uuid="u-1")
    assert created is False and binding["uuid"] == "k-9"
    assert not any(call[0] == "POST" for call in c.calls)


def test_api_keys_get_or_create_creates_when_absent():
    c = _api_keys_client(
        get_resp={"hydra:member": [{"name": "other"}]},
        post_resp={"uuid": "k-new"},
    )
    binding, created = ApiKeysAPI(c).get_or_create(
        name="repro-teamb",
        user_uuid="u-1",
        teams=["TeamB"],
    )
    assert created is True and binding["uuid"] == "k-new"
    assert c.calls[-1][0] == "POST"


def test_api_keys_update_resolves_and_puts():
    c = _api_keys_client(put_resp={"uuid": "k-1"})
    ApiKeysAPI(c).update("k-1", teams=["TeamB"])
    method, endpoint, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/3/api_keys/k-1"
    assert data == {"teams": ["/api/3/teams/t-1"]}


# ------------------------------------------------------------- api_keys.ensure_usable
class _FakeApiUsers:
    """Stand-in for ``client.api_users`` — records lifecycle calls and serves
    configurable plaintext on ``get(show_api_key=True)``. Returns the
    *unwrapped* user dict (the real :meth:`ApiKeyUsersAPI.get` unwraps the
    ``usersresp`` envelope), with ``api_key.retrievable`` set per case."""

    def __init__(self, create_resp=None, get_resps=None, regen_resp=None):
        self._create = create_resp or {"uuid": "u-1", "api_key": {"key": "plain"}}
        # Sequence of responses returned by successive get() calls.
        self._gets = list(get_resps or [{"api_key": {"key": "plain", "retrievable": True}}])
        # regenerate echoes the fresh plaintext in its response body.
        self._regen = regen_resp if regen_resp is not None else {"uuid": "u-1", "api_key": {"key": "fresh"}}
        self.calls = []  # (op, uuid-ish)

    def create(self, *, api_key_validity):
        self.calls.append(("create", api_key_validity))
        return self._create

    def get(self, uuid, *, show_api_key=False):
        self.calls.append(("get", uuid, show_api_key))
        return self._gets.pop(0) if self._gets else {}

    def regenerate(self, uuid, *, api_key_validity=365, key_type="API_KEY"):
        self.calls.append(("regenerate", uuid, api_key_validity))
        return self._regen


class _FakeAuthConfig:
    def __init__(self, retrievable=True):
        self._retrievable = retrievable
        self.toggled = False

    def is_api_key_retrievable(self):
        return self._retrievable

    def set_api_key_retrievable(self, enabled):
        self.toggled = True
        self._retrievable = bool(enabled)
        return {}


def _ensure_client(*, list_members=None, post_resp=None, put_resp=None, api_users=None, auth_config=None):
    c = _api_keys_client(
        get_resp={"hydra:member": list_members or []},
        post_resp=post_resp or {"uuid": "k-1", "name": "k"},
        put_resp=put_resp or {"uuid": "k-1"},
    )
    c.api_users = api_users or _FakeApiUsers()
    c.auth_config = auth_config or _FakeAuthConfig()
    return c


def test_ensure_usable_creates_user_and_binding_and_reads_plaintext_from_response():
    au = _FakeApiUsers()
    c = _ensure_client(list_members=[], api_users=au)  # no existing binding
    binding, plaintext = ApiKeysAPI(c).ensure_usable(name="k", teams=["TeamB"])
    assert plaintext == "plain"  # straight from the create response
    assert binding["uuid"] == "k-1"
    # Created the api-key user, then bound it with the resolved team IRI.
    assert ("create", 365) in au.calls
    assert c.calls[-1][:3] == (
        "POST",
        "/api/3/api_keys",
        {"name": "k", "userId": "u-1", "teams": ["/api/3/teams/t-1"]},
    )
    # Plaintext came from the create response — no show_api_key GET, no regenerate.
    ops = [op for op, *_ in au.calls]
    assert "get" not in ops
    assert "regenerate" not in ops


def test_ensure_usable_reuse_regenerates_and_reads_plaintext_from_response():
    # A reused binding can't recover its original plaintext, so it regenerates
    # and reads the fresh key straight from the regenerate response body.
    au = _FakeApiUsers(regen_resp={"uuid": "u-1", "api_key": {"key": "fresh"}})
    c = _ensure_client(
        list_members=[{"name": "k", "uuid": "k-9", "userId": "u-1"}],
        api_users=au,
    )
    binding, plaintext = ApiKeysAPI(c).ensure_usable(name="k")
    assert plaintext == "fresh"
    ops = [op for op, *_ in au.calls]
    assert ops == ["regenerate"]  # no show_api_key GET round-trips


def test_ensure_usable_reuses_existing_and_reconciles_teams():
    au = _FakeApiUsers(regen_resp={"uuid": "u-1", "api_key": {"key": "fresh"}})
    c = _ensure_client(
        list_members=[{"name": "k", "uuid": "k-9", "userId": "u-1"}],
        api_users=au,
    )
    binding, plaintext = ApiKeysAPI(c).ensure_usable(name="k", teams=["TeamB"])
    assert plaintext == "fresh" and binding["uuid"] == "k-9"
    # Reused binding -> reconcile teams via PUT (no POST create).
    assert not any(call[0] == "POST" for call in c.calls)
    method, endpoint, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/3/api_keys/k-9"
    assert data == {"teams": ["/api/3/teams/t-1"]}


def test_ensure_usable_raises_when_regenerate_response_has_no_plaintext():
    au = _FakeApiUsers(regen_resp={"uuid": "u-1", "api_key": {"key": ""}})
    c = _ensure_client(
        list_members=[{"name": "k", "uuid": "k-9", "userId": "u-1"}],
        api_users=au,
    )
    with pytest.raises(RuntimeError, match="plaintext"):
        ApiKeysAPI(c).ensure_usable(name="k")


def test_ensure_usable_never_toggles_retrievable_mode():
    # The broken-branch trigger on 7.6.5/8.0.0 — ensure_usable must not touch it.
    ac = _FakeAuthConfig(retrievable=False)
    c = _ensure_client(list_members=[], auth_config=ac)
    ApiKeysAPI(c).ensure_usable(name="k")
    assert ac.toggled is False


# -- wire shapes (real ApiKeyUsersAPI.get + _api_key_plaintext via FakeClient) --
def test_api_users_get_unwraps_usersresp_envelope():
    c = FakeClient(get_resp={"usersresp": [{"uuid": "u-1", "user_type": 9}]})
    c.api_users = ApiKeyUsersAPI(c)
    u = c.api_users.get("u-1", show_api_key=True)
    # Unwrapped + parsed into an ApiKeyUser (not the {"usersresp": [...]} envelope).
    assert isinstance(u, ApiKeyUser)
    assert u["uuid"] == "u-1" and u.user_type == 9
    assert "usersresp" not in u


def test_api_key_plaintext_returns_key_when_retrievable():
    c = FakeClient(get_resp={"usersresp": [{"api_key": {"key": "plainkey", "retrievable": True}}]})
    c.api_users = ApiKeyUsersAPI(c)
    assert _api_key_plaintext(c, "u-1") == "plainkey"


def test_api_key_plaintext_none_when_masked_even_though_key_nonempty():
    # A masked key is non-empty ("xxxx…d517") but retrievable=False → unrecoverable.
    c = FakeClient(get_resp={"usersresp": [{"api_key": {"key": "xxxxxxxxd517", "retrievable": False}}]})
    c.api_users = ApiKeyUsersAPI(c)
    assert _api_key_plaintext(c, "u-1") is None


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
