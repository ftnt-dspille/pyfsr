"""Typed (pydantic-backed) returns for the access-management API surface.

Covers the ``Access management`` endpoints: roles, teams, api_keys, api_users,
and auth_config. Reads come back as typed ``BaseRecord`` subclasses
(``Role``/``Team``/``ApiKey``/``ApiKeyUser``/``ModulePermission``) that stay
dict-compatible (``rec["x"]`` / ``rec.get("x")`` keep working). ``client.teams``
is a first-class ``TeamsAPI``; ``client.users`` team/role helpers delegate to it.
"""

from __future__ import annotations

from typing import Any

import pytest

from pyfsr.api.api_keys import ApiKeysAPI, _api_key_plaintext
from pyfsr.api.api_users import ApiKeyUsersAPI
from pyfsr.api.auth_config import AuthConfigAPI, AuthConfigRow
from pyfsr.api.roles import RolesAPI
from pyfsr.api.teams import TeamsAPI
from pyfsr.api.users import UsersAPI
from pyfsr.models import ApiKey, ApiKeyMaterial, ApiKeyUser, ModulePermission, Role, Team

# Stable, well-formed uuids so the uuid-or-name resolvers accept them directly.
_R_UID = "550e8400-e29b-41d4-a716-446655440000"  # role
_T_UID = "660e8400-e29b-41d4-a716-446655440000"  # team
_K_UID = "770e8400-e29b-41d4-a716-446655440000"  # api_key binding
_U_UID = "880e8400-e29b-41d4-a716-446655440000"  # api-key user


class _FakeClient:
    """Records calls and serves canned GET/POST/PUT responses by path."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._routes: dict[tuple[str, str], Any] = {}

    def route(self, method: str, path: str, resp: Any) -> _FakeClient:
        self._routes[(method, path)] = resp
        return self

    def _lookup(self, method: str, path: str):
        # longest matching route prefix
        match = None
        for (m, p), r in self._routes.items():
            if m == method and (path == p or path.startswith(p.rstrip("/") + "/")):
                if match is None or len(p) > len(match[0]):
                    match = (p, r)
        return match[1] if match else {}

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        return self._lookup("GET", endpoint)

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data, params))
        return self._lookup("POST", endpoint)

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return self._lookup("PUT", endpoint)

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))
        return self._lookup("DELETE", endpoint)


def _roles_client() -> _FakeClient:
    c = _FakeClient()
    c.route(
        "GET",
        "/api/3/roles",
        {
            "hydra:member": [
                {
                    "@id": f"/api/3/roles/{_R_UID}",
                    "@type": "Role",
                    "name": "Analyst",
                    "uuid": _R_UID,
                    "description": "d",
                },
            ]
        },
    )
    c.route(
        "GET",
        f"/api/3/roles/{_R_UID}",
        {
            "@id": f"/api/3/roles/{_R_UID}",
            "@type": "Role",
            "name": "Analyst",
            "uuid": _R_UID,
            "modulePermissions": [
                {
                    "@type": "ModulePermission",
                    "module": {"@id": "/api/3/modules/m1"},
                    "canRead": True,
                    "canCreate": False,
                }
            ],
        },
    )
    c.route("GET", "/api/3/modules", {"hydra:member": [{"type": "alerts", "@id": "/api/3/modules/m1"}]})
    c.route(
        "POST",
        "/api/3/roles",
        {"@id": "/api/3/roles/new", "@type": "Role", "name": "New", "uuid": "new", "modulePermissions": []},
    )
    c.route(
        "PUT",
        f"/api/3/roles/{_R_UID}",
        {"@id": f"/api/3/roles/{_R_UID}", "@type": "Role", "name": "Analyst", "uuid": _R_UID},
    )
    return c


# ----------------------------------------------------------------- roles
def test_roles_list_returns_typed_role_records():
    api = RolesAPI(_roles_client())
    rs = api.list()
    assert rs and isinstance(rs[0], Role)
    assert rs[0].name == "Analyst"
    # dict-compatible access still works
    assert rs[0]["uuid"] == _R_UID and rs[0].get("name") == "Analyst"


def test_roles_get_and_module_permissions_are_typed():
    api = RolesAPI(_roles_client())
    role = api.get("Analyst", relationships=True)
    assert isinstance(role, Role)
    perms = api.module_permissions("Analyst")
    assert perms and isinstance(perms[0], ModulePermission)
    assert perms[0].canRead is True and perms[0].canCreate is False


def test_roles_create_posts_name_and_description():
    c = _roles_client()
    role = RolesAPI(c).create("New", description="x")
    assert isinstance(role, Role) and role.name == "New"
    method, endpoint, data, _ = c.calls[-1]
    assert (method, endpoint) == ("POST", "/api/3/roles")
    assert data == {"name": "New", "description": "x"}


def test_roles_create_omits_description_when_absent():
    c = _roles_client()
    RolesAPI(c).create("New")
    assert c.calls[-1][2] == {"name": "New"}


def test_roles_grant_returns_role_and_merges_permissions():
    c = _roles_client()
    role = RolesAPI(c).grant_module_permissions("Analyst", module="alerts", can_read=True)
    assert isinstance(role, Role)
    # PUT to the role, with the existing perm replaced (module IRI preserved)
    method, endpoint, data = c.calls[-1]
    assert (method, endpoint) == ("PUT", f"/api/3/roles/{_R_UID}")
    mp = data["modulePermissions"][0]
    assert mp["module"] == "/api/3/modules/m1" and mp["canRead"] is True


def test_roles_resolution_helpers():
    api = RolesAPI(_roles_client())
    assert api.role_uuid_by_name("Analyst") == _R_UID
    assert api.role_uuid_by_name("nope") is None
    assert api._resolve_roles(["Analyst"]) == [_R_UID]
    assert api._resolve_roles([_R_UID]) == [_R_UID]  # bare uuid passes through
    with pytest.raises(ValueError):
        api._resolve_roles(["Ghost"])


# ----------------------------------------------------------------- teams
def _teams_client() -> _FakeClient:
    c = _FakeClient()
    c.route(
        "GET",
        "/api/3/teams",
        {
            "hydra:member": [
                {
                    "@id": f"/api/3/teams/{_T_UID}",
                    "@type": "Team",
                    "name": "Tier 1",
                    "uuid": _T_UID,
                    "description": "triage",
                },
            ]
        },
    )
    c.route(
        "GET",
        f"/api/3/teams/{_T_UID}",
        {"@id": f"/api/3/teams/{_T_UID}", "@type": "Team", "name": "Tier 1", "uuid": _T_UID},
    )
    c.route(
        "POST",
        "/api/3/teams",
        {
            "@id": "/api/3/teams/new",
            "@type": "Team",
            "name": "Tier 2",
            "uuid": "new",
            "actors": [],
            "children": [],
            "parents": [],
            "siblings": [],
        },
    )
    return c


def test_teams_list_get_create_typed():
    c = _teams_client()
    api = TeamsAPI(c)
    ts = api.list()
    assert ts and isinstance(ts[0], Team) and ts[0]["name"] == "Tier 1"
    assert isinstance(api.get("Tier 1"), Team)
    nt = api.create("Tier 2", description="escalation")
    assert isinstance(nt, Team) and nt.name == "Tier 2"
    assert c.calls[-1][2] == {"name": "Tier 2", "description": "escalation"}


def test_teams_resolution_helpers():
    api = TeamsAPI(_teams_client())
    assert api.team_uuid_by_name("Tier 1") == _T_UID
    assert api._resolve_teams(["Tier 1"]) == [_T_UID]
    assert api._resolve_teams([_T_UID]) == [_T_UID]
    with pytest.raises(ValueError):
        api._resolve_teams(["Ghost"])


# ----------------------------------------------------------------- api_users
def _api_users_client() -> _FakeClient:
    c = _FakeClient()
    c.route(
        "GET",
        "/api/auth/users",
        {
            "usersresp": [
                {
                    "uuid": _U_UID,
                    "user_type": 9,
                    "status": 1,
                    "access_type": "Concurrent",
                    "api_key": {"key": "PLAIN", "retrievable": True, "status": "Active"},
                }
            ]
        },
    )
    c.route("POST", "/api/auth/users", {"uuid": _U_UID, "api_key": {"key": "PLAIN", "retrievable": True}})
    c.route(
        "POST",
        "/api/auth/query/users",
        {
            "usersresp": [
                {"uuid": _U_UID, "api_key": {"key": "PLAIN", "retrievable": True}},
            ]
        },
    )
    c.route(
        "PUT", "/api/auth/users", {"uuid": _U_UID, "api_key": {"key": "REGEN", "retrievable": True, "status": "Active"}}
    )
    c.api_users = ApiKeyUsersAPI(c)  # type: ignore[attr-defined]
    return c


def test_api_users_get_returns_typed_apikeyuser_with_nested_material():
    c = _api_users_client()
    u = ApiKeyUsersAPI(c).get(_U_UID, show_api_key=True)
    assert isinstance(u, ApiKeyUser)
    assert u.user_type == 9 and u.status == 1
    # nested api_key parsed into ApiKeyMaterial, and dict-compat chains through
    assert isinstance(u.api_key, ApiKeyMaterial)
    assert u.api_key.retrievable is True
    assert u["api_key"]["key"] == "PLAIN"
    assert u.get("api_key").get("retrievable") is True


def test_api_users_query_returns_list_of_typed():
    c = _api_users_client()
    users = ApiKeyUsersAPI(c).query([_U_UID], show_api_key=True)
    assert isinstance(users, list) and isinstance(users[0], ApiKeyUser)
    assert users[0]["uuid"] == _U_UID


def test_api_users_create_and_lifecycle_typed():
    c = _api_users_client()
    api = ApiKeyUsersAPI(c)
    assert isinstance(api.create(api_key_validity=365), ApiKeyUser)
    out = api.regenerate(_U_UID)
    assert isinstance(out, ApiKeyUser) and out["api_key"]["key"] == "REGEN"


def test_api_key_plaintext_helper_round_trip():
    c = _api_users_client()
    assert _api_key_plaintext(c, _U_UID) == "PLAIN"


def test_api_key_plaintext_none_when_masked():
    c = _api_users_client()
    c.route(
        "GET",
        "/api/auth/users",
        {"usersresp": [{"uuid": _U_UID, "api_key": {"key": "xxxxxxxxd517", "retrievable": False}}]},
    )
    assert _api_key_plaintext(c, _U_UID) is None


def test_api_keys_get_plaintext_public_method_round_trip():
    # T3.3: public wrapper over the private _api_key_plaintext recovery flow.
    c = _api_users_client()
    assert ApiKeysAPI(c).get_plaintext(_U_UID) == "PLAIN"


def test_api_keys_get_plaintext_none_when_masked():
    c = _api_users_client()
    c.route(
        "GET",
        "/api/auth/users",
        {"usersresp": [{"uuid": _U_UID, "api_key": {"key": "xxxxxxxxd517", "retrievable": False}}]},
    )
    assert ApiKeysAPI(c).get_plaintext(_U_UID) is None


# ----------------------------------------------------------------- api_keys
def _api_keys_client() -> _FakeClient:
    c = _FakeClient()
    c.route(
        "GET",
        "/api/3/api_keys",
        {
            "hydra:member": [
                {
                    "@id": f"/api/3/api_keys/{_K_UID}",
                    "@type": "ApiKey",
                    "name": "repro-teamb",
                    "userId": _U_UID,
                    "uuid": _K_UID,
                    "roles": [],
                    "teams": [],
                },
            ]
        },
    )
    c.route(
        "GET",
        f"/api/3/api_keys/{_K_UID}",
        {
            "@id": f"/api/3/api_keys/{_K_UID}",
            "@type": "ApiKey",
            "name": "repro-teamb",
            "userId": _U_UID,
            "uuid": _K_UID,
            "roles": [],
            "teams": [],
        },
    )
    c.route(
        "POST",
        "/api/3/api_keys",
        {
            "@id": f"/api/3/api_keys/{_K_UID}",
            "@type": "ApiKey",
            "name": "repro-teamb",
            "userId": _U_UID,
            "uuid": _K_UID,
            "roles": [],
            "teams": [],
        },
    )
    c.route(
        "PUT",
        f"/api/3/api_keys/{_K_UID}",
        {
            "@id": f"/api/3/api_keys/{_K_UID}",
            "@type": "ApiKey",
            "name": "repro-teamb",
            "userId": _U_UID,
            "uuid": _K_UID,
            "teams": [],
        },
    )
    # api-key user plaintext recovery (GET) + regenerate (PUT)
    c.route(
        "GET",
        "/api/auth/users",
        {"usersresp": [{"uuid": _U_UID, "api_key": {"key": "PLAIN", "retrievable": True, "status": "Active"}}]},
    )
    c.route(
        "PUT", "/api/auth/users", {"uuid": _U_UID, "api_key": {"key": "PLAIN", "retrievable": True, "status": "Active"}}
    )
    # resolution targets (client.roles / client.teams) — name → uuid
    c.roles = RolesAPI(c)  # type: ignore[attr-defined]
    c.teams = TeamsAPI(c)  # type: ignore[attr-defined]
    # re-route roles/teams GETs after wiring (the API instances share `c`)
    c.route(
        "GET",
        "/api/3/roles",
        {"hydra:member": [{"@id": f"/api/3/roles/{_R_UID}", "@type": "Role", "name": "Analyst", "uuid": _R_UID}]},
    )
    c.route(
        "GET",
        "/api/3/teams",
        {"hydra:member": [{"@id": f"/api/3/teams/{_T_UID}", "@type": "Team", "name": "Tier 1", "uuid": _T_UID}]},
    )
    c.api_users = ApiKeyUsersAPI(c)  # type: ignore[attr-defined]
    c.auth_config = type(  # type: ignore[attr-defined]
        "FC", (), {"is_api_key_retrievable": lambda self: True, "set_api_key_retrievable": lambda self, e: {}}
    )()
    return c


def test_api_keys_list_get_create_update_typed():
    c = _api_keys_client()
    api = ApiKeysAPI(c)
    assert isinstance(api.list()[0], ApiKey)
    assert isinstance(api.get(_K_UID), ApiKey)
    cr = api.create(name="repro-teamb", user_uuid=_U_UID, teams=["Tier 1"], roles=["Analyst"])
    assert isinstance(cr, ApiKey)
    body = c.calls[-1][2]
    # teams/roles resolved to uuids via client.roles/client.teams
    assert body["teams"] == [_T_UID] and body["roles"] == [_R_UID]
    up = api.update(_K_UID, teams=["Tier 1"])
    assert isinstance(up, ApiKey)
    assert c.calls[-1][2] == {"teams": [_T_UID]}


def test_api_keys_delete_issues_delete():
    c = _api_keys_client()
    ApiKeysAPI(c).delete(_K_UID)
    method, endpoint, *_ = c.calls[-1]
    assert method == "DELETE" and endpoint == f"/api/3/api_keys/{_K_UID}"


def test_api_keys_get_or_create_reuses_existing():
    c = _api_keys_client()
    binding, created = ApiKeysAPI(c).get_or_create(name="repro-teamb", user_uuid=_U_UID)
    assert created is False and isinstance(binding, ApiKey) and binding["uuid"] == _K_UID


def test_api_keys_ensure_usable_returns_typed_binding_and_plaintext():
    c = _api_keys_client()
    binding, plaintext = ApiKeysAPI(c).ensure_usable(name="repro-teamb", teams=["Tier 1"])
    assert isinstance(binding, ApiKey) and plaintext == "PLAIN"


# ----------------------------------------------------------------- auth_config
def _auth_config_client() -> _FakeClient:
    c = _FakeClient()
    c.route(
        "GET",
        "/api/auth/config",
        {
            "hydra:member": [
                {"id": 1, "section": "TOKEN", "key": "idle_time", "dataType": "int", "value": 30},
                {"id": 2, "section": "TOKEN", "key": "max_session", "dataType": "int", "value": 1440},
            ]
        },
    )
    return c


def test_auth_config_get_raw_returns_typed_rows():
    rows = AuthConfigAPI(_auth_config_client()).get_raw("TOKEN")
    assert rows and isinstance(rows[0], AuthConfigRow)
    assert rows[0].key == "idle_time" and rows[0].value == 30


def test_auth_config_get_returns_plain_dict_subscriptable():
    cfg = AuthConfigAPI(_auth_config_client()).get("TOKEN")
    # plain {key: value} dict (not a model) — subscript stays the documented UX
    assert isinstance(cfg, dict)
    assert cfg["idle_time"] == 30 and cfg["max_session"] == 1440


# ----------------------------------------------------------------- users shims
def test_users_shims_delegate_to_roles_and_teams():
    c = _api_keys_client()
    users = UsersAPI(c)
    assert isinstance(users.list_roles()[0], Role)
    assert isinstance(users.list_teams()[0], Team)
    assert users.role_uuid_by_name("Analyst") == _R_UID
    assert users.team_uuid_by_name("Tier 1") == _T_UID
    assert users._resolve_roles(["Analyst"]) == [_R_UID]
    assert users._resolve_teams(["Tier 1"]) == [_T_UID]


# ----------------------------------------------------------------- records() accessor
def test_records_api_keys_registered_as_typed_recordset():
    """client.records('api_keys') binds to the ApiKey model."""
    from pyfsr.models import model_for
    from pyfsr.records import RecordSet

    assert model_for("api_keys") is ApiKey
    # RecordSet resolves the model from the registry at construction time.
    rs = RecordSet(client=object(), module="api_keys")  # type: ignore[arg-type]
    assert rs.model is ApiKey
    assert isinstance(rs, RecordSet)
