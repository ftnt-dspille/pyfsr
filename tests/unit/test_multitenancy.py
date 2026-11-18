"""Unit tests for the dedicated-tenant API (``client.multitenancy``), fully mocked over HTTP."""

from __future__ import annotations

import copy

import pytest

from pyfsr._testing import demo_client
from pyfsr.api import multitenancy as mt
from pyfsr.api.multitenancy import ExportedConfig, MultitenancyAPI, MultitenancyError
from pyfsr.exceptions import APIError, ResourceNotFoundError

TENANT_ID = "0123456789abcdef0123456789abcdef"
SELF_TENANT_ID = "fedcba9876543210fedcba9876543210"
SOC = "/api/3/teams/soc"
OWN = "/api/3/teams/own"
SME = "/api/3/routers/sme-1"
FSR_NODE = "/api/3/picklists/t-fsr-node"
BASIC = "/api/3/picklists/a-basic"
CONF = "/api/3/picklists/c-configured"
NOTCONF = "/api/3/picklists/c-notconfigured"
VERIFIED = "/api/3/picklists/c-verified"
HEALTH_CONNECTED = "/api/3/picklists/h-connected"
HEALTH_WAIT = "/api/3/picklists/h-awaiting"
PW = "pw-placeholder"
KEY = "key-placeholder"
CERT = "-----BEGIN CERTIFICATE----- placeholder"
MASTER_AGENT_ID = "aabbccddeeff00112233445566778899"


def _resp(status: int) -> object:
    class _R:
        status_code = status

    return _R()


def _api_error(status: int, message: str) -> APIError:
    return APIError(message, _resp(status))


def _not_found() -> ResourceNotFoundError:
    return ResourceNotFoundError("not found", _resp(404))


def _members(*records):
    return {"hydra:member": list(records)}


class _FakeTeam:
    def __init__(self, name):
        self.name, self.description = name, "Management team"


class FakeTeams:
    known = {"SOC Team": SOC, "Own": OWN}

    def get(self, uuid):
        return _FakeTeam({"soc": "SOC Team", "own": "Own"}.get(uuid, uuid))

    def _resolve_teams(self, teams):
        out = []
        for t in teams:
            if t in self.known:
                out.append(self.known[t])
            elif str(t).startswith("/api/3/teams/"):
                out.append(t)
            else:
                raise ValueError(f"team {t!r} not found")
        return out


class FakeRouters:
    def __init__(self, sme=True):
        self._sme = {"@id": SME, "uuid": "sme-1"} if sme else None

    def first(self):
        return self._sme


class FakePicklists:
    FIELDS = {
        ("agents", "agentType"): "AgentType",
        ("agents", "authType"): "AuthType",
        ("agents", "configstatus"): "ConfigStatus",
        ("routers", "configstatus"): "ConfigStatus",
        ("agents", "configurationHealth"): "Health",
    }
    VALUES = {
        "AgentType": {"FSR Node": FSR_NODE},
        "AuthType": {"Basic Auth": BASIC},
        "ConfigStatus": {"Configured": CONF, "Not Configured": NOTCONF, "Connection Verified": VERIFIED},
        "Health": {"Remote Node Connected": HEALTH_CONNECTED, "Awaiting Remote Node Connection": HEALTH_WAIT},
    }

    def for_field(self, module, field):
        return self.FIELDS.get((module, field))

    def resolve(self, value, *, picklist=None, **_):
        return self.VALUES.get(picklist, {}).get(value)

    def options(self, picklist):
        return list(self.VALUES.get(picklist, {}))

    def reverse_resolve(self, iri):
        for picklist, values in self.VALUES.items():
            for item, value_iri in values.items():
                if value_iri == iri:
                    return {"picklist": picklist, "itemValue": item, "iri": iri}
        return None


class FakeClient:
    """Routes (METHOD, path) to a canned value, a callable(payload), or an exception to raise.

    Every call is recorded as ``(method, endpoint, payload)`` in ``calls``; the endpoint keeps
    its query string so tests can assert on it.
    """

    def __init__(self, routes=None, *, sme=True, version="8.0.1-6034"):
        # The query API is the fallback for records a tenant's list endpoint hides; empty unless a test says otherwise.
        self.routes = {
            ("POST", "/api/query/agents"): _members(),
            ("POST", "/api/query/routers"): _members(),
            ("POST", "/api/query/tenants"): _members(),
        }
        self.routes.update(routes or {})
        self.calls: list[tuple[str, str, object]] = []
        self.teams = FakeTeams()
        self.routers = FakeRouters(sme=sme)
        self.picklists = FakePicklists()
        self._version = version

    def version(self, *, refresh=False):
        return self._version

    def _route(self, method, endpoint, payload):
        self.calls.append((method, endpoint, copy.deepcopy(payload)))
        path = endpoint.split("?")[0]
        if (method, path) not in self.routes:
            raise AssertionError(f"unexpected {method} {endpoint}")
        handler = self.routes[(method, path)]
        if isinstance(handler, BaseException):
            raise handler
        if callable(handler):
            return handler(payload)
        return copy.deepcopy(handler)

    def get(self, endpoint, params=None, **_):
        return self._route("GET", endpoint, params)

    def post(self, endpoint, data=None, params=None, **_):
        return self._route("POST", endpoint, data)

    def put(self, endpoint, data=None, params=None, **_):
        return self._route("PUT", endpoint, data)

    def delete(self, endpoint, params=None, **_):
        return self._route("DELETE", endpoint, params)

    def request(self, method, endpoint, data=None, **_):
        return self._route(method.upper(), endpoint, data)

    def methods(self):
        return [m for m, *_ in self.calls]

    def mutations(self):
        return [(m, e, p) for m, e, p in self.calls if m != "GET" and not e.startswith("/api/query/")]


def _tenant_list(*extra):
    return _members({"name": "Self", "role": "self", "tenantId": SELF_TENANT_ID, "@id": "/api/3/tenants/self"}, *extra)


# ----------------------------------------------------------------- secrets
def test_exported_config_masks_secrets_in_repr_and_str_but_stays_a_dict():
    cfg = ExportedConfig({"agentId": MASTER_AGENT_ID, "password": PW, "certificate": CERT, "clientKey": KEY})
    text = repr(cfg) + str(cfg)
    assert PW not in text and KEY not in text and CERT not in text
    assert "agentId" in text and MASTER_AGENT_ID in text
    assert cfg["password"] == PW and isinstance(cfg, dict)


def test_client_exposes_multitenancy_api():
    client = demo_client()
    assert isinstance(client.multitenancy, MultitenancyAPI)


# ------------------------------------------------------- add_dedicated_tenant
def _add_routes(*, agent_post=None, tenant_post=None, tenants=None):
    tenant_rec = {"@id": "/api/3/tenants/t-1", "uuid": "t-1", "name": "branch-7", "isDedicated": True}
    agent_rec = {
        "uuid": "ag-1",
        "name": "branch-7",
        "agentId": TENANT_ID,
        "encryptionKey": KEY,
        "password": PW,
        "configurationHealth": HEALTH_WAIT,
    }
    return {
        ("GET", "/api/3/tenants"): tenants if tenants is not None else _tenant_list(),
        ("POST", "/api/3/tenants"): tenant_post if tenant_post is not None else tenant_rec,
        ("POST", "/api/3/agents"): agent_post if agent_post is not None else agent_rec,
    }


def test_add_dedicated_tenant_posts_tenant_then_agent_with_resolved_picklists():
    client = FakeClient(_add_routes())
    tenant, agent = MultitenancyAPI(client).add_dedicated_tenant("branch-7", TENANT_ID, version=None)

    assert tenant.name == "branch-7" and tenant.isDedicated is True
    tenant_post = next(c for c in client.calls if c[:2] == ("POST", "/api/3/tenants"))[2]
    assert tenant_post == {"name": "branch-7", "isDedicated": True, "tenantId": TENANT_ID, "owners": [SOC]}

    agent_post = next(c for c in client.calls if c[:2] == ("POST", "/api/3/agents"))[2]
    assert agent_post["agentId"] == TENANT_ID
    assert agent_post["version"] == "8.0.1"  # build number stripped
    assert agent_post["agentType"] == FSR_NODE and agent_post["authType"] == BASIC
    assert agent_post["router"] == SME
    assert agent_post["tenants"] == ["/api/3/tenants/t-1"]
    assert agent_post["owners"] == [SOC]
    # the agent's secrets never come back out of the returned model
    assert "encryptionKey" not in agent.model_dump(by_alias=True)
    assert PW not in repr(agent) and KEY not in repr(agent)


def test_add_dedicated_tenant_requires_the_tenant_nodes_id():
    """The tenantId is the TENANT node's own Self id; the master cannot supply it."""
    import pytest

    from pyfsr.api.multitenancy import MultitenancyAPI
    from pyfsr.client import FortiSOAR  # noqa: F401

    api = MultitenancyAPI.__new__(MultitenancyAPI)
    with pytest.raises(TypeError):
        api.add_dedicated_tenant("region-x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        api.add_dedicated_tenant("region-x", "not-hex")


def test_add_dedicated_tenant_rejects_a_malformed_tenant_id_before_any_write():
    client = FakeClient(_add_routes())
    with pytest.raises(ValueError):
        MultitenancyAPI(client).add_dedicated_tenant("branch-7", "not-hex")
    assert client.mutations() == []


def test_add_dedicated_tenant_refuses_an_existing_name():
    client = FakeClient(_add_routes(tenants=_tenant_list({"name": "branch-7", "uuid": "t-0"})))
    with pytest.raises(MultitenancyError, match="already exists"):
        MultitenancyAPI(client).add_dedicated_tenant("branch-7", TENANT_ID)
    assert client.mutations() == []


def test_add_dedicated_tenant_without_an_sme_router_fails_clearly():
    client = FakeClient(_add_routes(), sme=False)
    with pytest.raises(MultitenancyError, match="secure-message-exchange"):
        MultitenancyAPI(client).add_dedicated_tenant("branch-7", TENANT_ID)


def test_add_dedicated_tenant_tenant_conflict_is_a_multitenancy_error():
    client = FakeClient(_add_routes(tenant_post=_api_error(409, "UniqueConstraint")))
    with pytest.raises(MultitenancyError, match="already exists"):
        MultitenancyAPI(client).add_dedicated_tenant("branch-7", TENANT_ID)


def test_add_dedicated_tenant_agent_failure_points_at_delete_tenant():
    client = FakeClient(_add_routes(agent_post=_api_error(400, "bad agent")))
    with pytest.raises(MultitenancyError, match="delete_tenant"):
        MultitenancyAPI(client).add_dedicated_tenant("branch-7", TENANT_ID)


# ------------------------------------------------------------ wait_for_health
def test_wait_for_health_polls_until_the_state_is_reached(monkeypatch):
    states = [HEALTH_WAIT, HEALTH_WAIT, HEALTH_CONNECTED]
    sleeps = []
    monkeypatch.setattr(mt.time, "sleep", lambda s: sleeps.append(s))

    def agents(_):
        return _members({"name": "branch-7", "uuid": "ag-1", "configurationHealth": states.pop(0)})

    client = FakeClient({("GET", "/api/3/agents"): agents})
    assert MultitenancyAPI(client).wait_for_health("branch-7", "Remote Node Connected", interval=2) == (
        "Remote Node Connected"
    )
    assert len(sleeps) == 2 and all(s <= 2 for s in sleeps)


def test_wait_for_health_times_out_naming_the_last_state():
    client = FakeClient(
        {("GET", "/api/3/agents"): _members({"name": "branch-7", "uuid": "ag-1", "configurationHealth": HEALTH_WAIT})}
    )
    with pytest.raises(MultitenancyError, match="Awaiting Remote Node Connection"):
        MultitenancyAPI(client).wait_for_health("branch-7", "Remote Node Connected", timeout=0)


def test_wait_for_health_unknown_agent_is_an_error():
    client = FakeClient({("GET", "/api/3/agents"): _members()})
    with pytest.raises(MultitenancyError, match="no agent named"):
        MultitenancyAPI(client).wait_for_health("branch-7", "Remote Node Connected", timeout=0)


# -------------------------------------------------------------- export_config
def test_export_config_posts_the_agent_id_and_returns_a_masked_config():
    client = FakeClient(
        {
            ("GET", "/api/3/agents"): _members({"name": "branch-7", "agentId": MASTER_AGENT_ID, "uuid": "ag-1"}),
            ("POST", "/api/integration/agent-installer/"): {
                "address": "master.example.com",
                "agentId": MASTER_AGENT_ID,
                "password": PW,
                "certificate": CERT,
                "tcpPort": 443,
                "sni": "master.example.com",
                "routerUsername": "router",
                "username": "agent",
                "vhost": "/",
                "authType": "Basic Auth",
                "isDedicated": True,
            },
        }
    )
    cfg = MultitenancyAPI(client).export_config("branch-7")
    method, endpoint, payload = client.mutations()[0]
    assert (method, endpoint) == ("POST", "/api/integration/agent-installer/?format=json")
    assert payload == {"agent": MASTER_AGENT_ID}
    assert cfg["agentId"] == MASTER_AGENT_ID and cfg["tcpPort"] == 443
    assert PW not in repr(cfg)


def test_export_config_non_object_response_is_an_error():
    client = FakeClient(
        {
            ("GET", "/api/3/agents"): _members({"name": "branch-7", "agentId": MASTER_AGENT_ID}),
            ("POST", "/api/integration/agent-installer/"): ["unexpected"],
        }
    )
    with pytest.raises(MultitenancyError):
        MultitenancyAPI(client).export_config("branch-7")


def test_export_config_rejects_an_empty_reference():
    with pytest.raises(ValueError):
        MultitenancyAPI(FakeClient()).export_config("  ")


# ------------------------------------------------------------ encryption_key
def test_encryption_key_reads_the_master_record():
    client = FakeClient(
        {("GET", "/api/3/agents"): _members({"name": "branch-7", "uuid": "ag-1", "encryptionKey": KEY})}
    )
    assert MultitenancyAPI(client).encryption_key("branch-7") == KEY


def test_encryption_key_falls_back_to_the_record_by_uuid():
    client = FakeClient(
        {
            ("GET", "/api/3/agents"): _members({"name": "branch-7", "uuid": "ag-1"}),
            ("GET", "/api/3/agents/ag-1"): {"uuid": "ag-1", "encryptionKey": KEY},
        }
    )
    assert MultitenancyAPI(client).encryption_key("branch-7") == KEY


def test_encryption_key_missing_is_an_error():
    client = FakeClient(
        {
            ("GET", "/api/3/agents"): _members({"name": "branch-7", "uuid": "ag-1"}),
            ("GET", "/api/3/agents/ag-1"): {"uuid": "ag-1"},
        }
    )
    with pytest.raises(MultitenancyError, match="no encryptionKey"):
        MultitenancyAPI(client).encryption_key("branch-7")


# ------------------------------------------------------------- delete_tenant
def test_delete_tenant_deletes_the_tenant_then_its_leftover_agent():
    client = FakeClient(
        {
            ("GET", "/api/3/tenants"): _members({"name": "branch-7", "uuid": "t-1"}),
            ("GET", "/api/3/agents"): _members({"name": "branch-7", "uuid": "ag-1"}),
            ("DELETE", "/api/3/tenants/t-1"): None,
            ("DELETE", "/api/3/agents/ag-1"): None,
        }
    )
    MultitenancyAPI(client).delete_tenant("branch-7")
    assert client.mutations() == [
        ("DELETE", "/api/3/tenants/t-1", None),
        ("DELETE", "/api/3/agents/ag-1", None),
    ]


def test_delete_tenant_skips_agent_cleanup_when_the_tenant_delete_removed_it():
    client = FakeClient(
        {
            ("GET", "/api/3/tenants"): _members({"name": "branch-7", "uuid": "t-1"}),
            ("GET", "/api/3/agents"): _members(),
            ("DELETE", "/api/3/tenants/t-1"): None,
        }
    )
    MultitenancyAPI(client).delete_tenant("branch-7")
    assert client.mutations() == [("DELETE", "/api/3/tenants/t-1", None)]


def test_delete_tenant_unknown_name_sends_nothing():
    client = FakeClient({("GET", "/api/3/tenants"): _members()})
    with pytest.raises(MultitenancyError, match="no tenant named"):
        MultitenancyAPI(client).delete_tenant("branch-7")
    assert client.mutations() == []


# ------------------------------------------------------------- configure_master
def _config(**overrides):
    cfg = {
        "agentId": MASTER_AGENT_ID,
        "address": "master.example.com",
        "tcpPort": 443,
        "sni": "master.example.com",
        "routerUsername": "router",
        "password": PW,
        "certificate": CERT,
        "username": "agent",
        "vhost": "/",
        "authType": "Basic Auth",
        "isDedicated": True,
        "selfId": "self-1",
    }
    cfg.update(overrides)
    return cfg


def _configure_routes(*, router_post=None, agent_post=None, leftover_agent=None, leftover_router=None):
    router_uuid = mt._master_uuid(MASTER_AGENT_ID, "router")
    agent_uuid = mt._master_uuid(MASTER_AGENT_ID, "agent")
    return {
        ("GET", f"/api/3/agents/{agent_uuid}"): leftover_agent or _not_found(),
        ("GET", f"/api/3/routers/{router_uuid}"): leftover_router or _not_found(),
        ("POST", "/api/3/routers"): router_post
        or (lambda data: {"@id": f"/api/3/routers/{data['uuid']}", "uuid": data["uuid"]}),
        ("POST", "/api/3/agents"): agent_post
        or (lambda data: {"uuid": agent_uuid, **data, "encryptionKey": KEY, "password": PW}),
        ("DELETE", f"/api/3/agents/{agent_uuid}"): None,
        ("DELETE", f"/api/3/routers/{router_uuid}"): None,
    }


def test_configure_master_creates_router_then_agent_with_the_key_and_master_iris():
    client = FakeClient(_configure_routes())
    agent = MultitenancyAPI(client).configure_master(_config(), KEY)

    router_uuid = mt._master_uuid(MASTER_AGENT_ID, "router")
    router_post = next(c for c in client.calls if c[:2] == ("POST", "/api/3/routers"))[2]
    assert router_post["uuid"] == router_uuid and router_post["name"] == "Master"
    assert router_post["apiPort"] == 15671 and router_post["tcpPort"] == 443
    assert router_post["username"] == "router" and router_post["encryptionKey"] == KEY
    assert router_post["configstatus"] == NOTCONF

    agent_call = next(c for c in client.calls if c[0] == "POST" and c[1].startswith("/api/3/agents"))
    assert agent_call[1] == "/api/3/agents?$relationships=true&$asJson=true"
    body = agent_call[2]
    assert body["agentId"] == MASTER_AGENT_ID and body["name"] == "Master"
    assert body["router"] == router_uuid  # the bare uuid, as the UI sends it
    assert [o["name"] for o in body["owners"]] == ["SOC Team"]  # full team objects, as the UI sends
    assert body["authType"] == BASIC and body["agentType"] == FSR_NODE
    assert body["encryptionKey"] == KEY and body["active"] is True and body["isLocal"] is False
    assert body["role"] == "master" and body["tenants"][0]["name"] == "Master"  # the UI body, by default

    # the returned record has its secrets stripped
    assert "encryptionKey" not in agent.model_dump(by_alias=True)
    assert PW not in repr(agent)


def test_configure_master_sends_role_and_tenants_when_given():
    client = FakeClient(_configure_routes())
    MultitenancyAPI(client).configure_master(_config(), KEY, role="master-x", tenants=["/api/3/tenants/t-9"])
    body = next(c for c in client.calls if c[0] == "POST" and c[1].startswith("/api/3/agents"))[2]
    assert body["tenants"] == ["/api/3/tenants/t-9"]
    assert body["role"] == "master-x"


def test_configure_master_defaults_to_the_ui_agent_body_and_activates_the_master_tenant():
    """Live 8.0.1: only this shape connects -- role master, the Master tenant inline, then activated."""
    routes = _configure_routes()
    created = {"agent": False}  # the Master tenant only exists once the agent POST has made it
    agent_post = routes[("POST", "/api/3/agents")]
    routes[("POST", "/api/3/agents")] = lambda data: (created.update(agent=True), agent_post(data))[1]
    routes[("POST", "/api/query/tenants")] = lambda _: _members(
        *([{"name": "Master", "role": "master", "uuid": "t-master"}] if created["agent"] else [])
    )
    routes[("PUT", "/api/3/tenants/t-master")] = {}
    client = FakeClient(routes)
    MultitenancyAPI(client).configure_master(_config(), KEY)
    body = next(c for c in client.calls if c[0] == "POST" and c[1].startswith("/api/3/agents"))[2]
    assert body["role"] == "master"
    (tenant,) = body["tenants"]
    assert tenant["tenantId"] == MASTER_AGENT_ID and tenant["name"] == "Master" and tenant["role"] == "master"
    assert tenant["active"] is False and tenant["isDedicated"] is True
    assert [o["name"] for o in tenant["owners"]] == ["SOC Team"] and tenant["owners"][0]["uuid"] == "soc"
    assert client.calls[-1][:2] == ("PUT", "/api/3/tenants/t-master")
    assert client.calls[-1][2] == {"active": True}


def test_configure_master_missing_config_keys_fail_before_any_call():
    client = FakeClient(_configure_routes())
    bad = _config()
    del bad["address"]
    with pytest.raises(ValueError, match="address"):
        MultitenancyAPI(client).configure_master(bad, KEY)
    assert client.calls == []


def test_configure_master_empty_key_is_rejected():
    client = FakeClient(_configure_routes())
    with pytest.raises(ValueError):
        MultitenancyAPI(client).configure_master(_config(), "  ")


def test_configure_master_unknown_owner_fails_before_any_write():
    client = FakeClient(_configure_routes())
    with pytest.raises(ValueError, match="not found"):
        MultitenancyAPI(client).configure_master(_config(), KEY, owners=["Nobody"])
    assert client.mutations() == []


def test_configure_master_decrypt_failure_leaves_router_and_says_so():
    client = FakeClient(
        _configure_routes(agent_post=_api_error(400, "DB Password decrypt failed for the agent record"))
    )
    with pytest.raises(MultitenancyError) as info:
        MultitenancyAPI(client).configure_master(_config(), KEY)
    err = info.value
    assert "does not decrypt" in str(err) and "replace_leftover=True" in str(err)
    assert err.leftover_router == mt._master_uuid(MASTER_AGENT_ID, "router")
    assert PW not in str(err) and KEY not in str(err)


def test_configure_master_agent_uuid_conflict_is_a_leftover_error():
    client = FakeClient(_configure_routes(agent_post=_api_error(409, "UniqueConstraint(uuid)")))
    with pytest.raises(MultitenancyError, match="409") as info:
        MultitenancyAPI(client).configure_master(_config(), KEY)
    assert info.value.leftover_router == mt._master_uuid(MASTER_AGENT_ID, "router")


def test_configure_master_router_conflict_is_a_leftover_error():
    client = FakeClient(_configure_routes(router_post=_api_error(409, "UniqueConstraint(uuid)")))
    with pytest.raises(MultitenancyError, match="replace_leftover=True") as info:
        MultitenancyAPI(client).configure_master(_config(), KEY)
    assert info.value.leftover_router == mt._master_uuid(MASTER_AGENT_ID, "router")
    assert not any(m == "POST" and e.startswith("/api/3/agents") for m, e, _ in client.calls)


def test_configure_master_finds_a_leftover_router_and_refuses_without_replace():
    router_uuid = mt._master_uuid(MASTER_AGENT_ID, "router")
    client = FakeClient(_configure_routes(leftover_router={"uuid": router_uuid, "name": "Master"}))
    with pytest.raises(MultitenancyError, match="replace_leftover=True") as info:
        MultitenancyAPI(client).configure_master(_config(), KEY)
    assert info.value.leftover_router == router_uuid
    assert client.mutations() == []


def test_configure_master_replace_leftover_deletes_then_recreates():
    router_uuid = mt._master_uuid(MASTER_AGENT_ID, "router")
    agent_uuid = mt._master_uuid(MASTER_AGENT_ID, "agent")
    client = FakeClient(
        _configure_routes(
            leftover_router={"uuid": router_uuid, "name": "Master"},
            leftover_agent={"uuid": agent_uuid, "name": "Master"},
        )
    )
    MultitenancyAPI(client).configure_master(_config(), KEY, replace_leftover=True)
    mutations = [(m, e) for m, e, _ in client.mutations()]
    assert mutations == [
        ("DELETE", f"/api/3/agents/{agent_uuid}"),
        ("DELETE", f"/api/3/routers/{router_uuid}"),
        ("POST", "/api/3/routers"),
        ("POST", "/api/3/agents?$relationships=true&$asJson=true"),
    ]


def test_configure_master_unknown_picklist_value_fails_before_any_write():
    client = FakeClient(_configure_routes())
    with pytest.raises(MultitenancyError, match="authType"):
        MultitenancyAPI(client).configure_master(_config(authType="Kerberos"), KEY)
    assert client.mutations() == []


# ---------------------------------------------------------------------- status
def test_status_maps_picklist_iris_to_friendly_values():
    client = FakeClient(
        {
            ("GET", "/api/3/agents"): _members(
                {
                    "name": "Master",
                    "uuid": "ag-2",
                    "agentId": MASTER_AGENT_ID,
                    "active": True,
                    "configurationHealth": {"itemValue": "Remote Node Connected", "@id": HEALTH_CONNECTED},
                    "configstatus": VERIFIED,
                }
            )
        }
    )
    st = MultitenancyAPI(client).status("Master")
    assert st.configurationHealth == "Remote Node Connected"
    assert st.configstatus == "Connection Verified"
    assert st.agentId == MASTER_AGENT_ID and st.active is True


def test_status_unknown_agent_is_an_error():
    with pytest.raises(MultitenancyError, match="no agent named"):
        MultitenancyAPI(FakeClient({("GET", "/api/3/agents"): _members()})).status("Master")


# ---------------------------------------------------- master -> tenant playbooks
COLL_UUID = "fb64dd06-d402-5744-bc3f-017968127efb"
ALIAS_UUID = "5191eb0f-dd91-459a-bfdf-e4e3aa93f374"


def test_push_playbooks_resolves_names_and_posts_the_ui_body():
    client = FakeClient(
        {
            ("GET", "/api/3/workflow_collections"): _members({"name": "MT Health - Tenant", "uuid": COLL_UUID}),
            ("GET", "/api/3/agents"): _members({"name": "branch-7", "uuid": "ag-1", "agentId": TENANT_ID}),
            ("POST", "/api/postman/playbook-push/"): {"message": "Playbook publish to remote is successful"},
        }
    )
    MultitenancyAPI(client).push_playbooks(["MT Health - Tenant"], ["branch-7", SELF_TENANT_ID.upper()])
    assert client.calls[-1] == (
        "POST",
        "/api/postman/playbook-push/",
        {"collectionIds": [COLL_UUID], "tenants": [TENANT_ID, SELF_TENANT_ID], "replace": True},
    )


def test_push_playbooks_needs_a_collection_and_a_tenant():
    with pytest.raises(ValueError):
        MultitenancyAPI(FakeClient()).push_playbooks([], ["branch-7"])


def test_playbook_alias_returns_uuid_and_mapped_tenants():
    client = FakeClient(
        {
            ("GET", "/api/postman/wf-alias/"): {
                "count": 1,
                "results": [
                    {
                        "id": 1,
                        "name": "MT Health Marker",
                        "uuid": ALIAS_UUID,
                        "remote_wfs": [{"tenant_id": TENANT_ID}, {"tenant_id": SELF_TENANT_ID}],
                    }
                ],
            }
        }
    )
    assert MultitenancyAPI(client).playbook_alias("MT Health Marker") == {
        "uuid": ALIAS_UUID,
        "name": "MT Health Marker",
        "tenants": [TENANT_ID, SELF_TENANT_ID],
    }


def test_playbook_alias_missing_says_to_push_first():
    client = FakeClient({("GET", "/api/postman/wf-alias/"): {"count": 0, "results": []}})
    with pytest.raises(MultitenancyError, match="push its collection first"):
        MultitenancyAPI(client).playbook_alias("MT Health Marker")


def test_status_finds_a_master_agent_the_list_endpoint_hides():
    """Live 8.0.1 tenant: GET /api/3/agents is empty, /api/query/agents has the Master record."""
    client = FakeClient(
        {
            ("GET", "/api/3/agents"): _members(),
            ("POST", "/api/query/agents"): _members(
                {"name": "Self", "uuid": "u-self"},
                {"name": "Master", "uuid": "u-master", "agentId": MASTER_AGENT_ID, "configstatus": {"@id": VERIFIED}},
            ),
        }
    )
    status = MultitenancyAPI(client).status("Master")
    assert status.uuid == "u-master" and status.agentId == MASTER_AGENT_ID


def test_configure_master_replaces_master_records_made_through_the_ui():
    """UI-made Master records have random uuids, so only a by-name lookup finds them; agent goes first."""
    routes = _configure_routes()
    routes[("POST", "/api/query/routers")] = _members({"name": "Master", "uuid": "ui-router"})
    routes[("POST", "/api/query/agents")] = _members({"name": "Master", "uuid": "ui-agent"})
    routes[("DELETE", "/api/3/agents/ui-agent")] = None
    routes[("DELETE", "/api/3/routers/ui-router")] = None
    client = FakeClient(routes)
    api = MultitenancyAPI(client)

    with pytest.raises(MultitenancyError, match="replace_leftover=True") as info:
        api.configure_master(_config(), KEY)
    assert info.value.leftover_router == "ui-router"
    assert not [c for c in client.calls if c[0] == "DELETE"]

    api.configure_master(_config(), KEY, replace_leftover=True)
    deletes = [c[1] for c in client.calls if c[0] == "DELETE"]
    assert deletes == ["/api/3/agents/ui-agent", "/api/3/routers/ui-router"]


def test_remove_master_deletes_agents_then_routers_found_by_name():
    client = FakeClient(
        {
            ("POST", "/api/query/agents"): _members(
                {"name": "Self", "uuid": "u-self"}, {"name": "Master", "uuid": "a1"}
            ),
            ("POST", "/api/query/routers"): _members({"name": "Master", "uuid": "r1"}),
            ("DELETE", "/api/3/agents/a1"): None,
            ("DELETE", "/api/3/routers/r1"): None,
        }
    )
    assert MultitenancyAPI(client).remove_master() == ["a1", "r1"]
    assert client.mutations() == [("DELETE", "/api/3/agents/a1", None), ("DELETE", "/api/3/routers/r1", None)]
    assert MultitenancyAPI(FakeClient()).remove_master() == []


def test_delete_tenant_tolerates_the_agent_vanishing_before_its_own_delete():
    seen = {"agents": 0}

    def agents(_):
        seen["agents"] += 1  # present for the lookup, gone once the master has cleaned it up itself
        return _members({"name": "branch-7", "uuid": "a-1"}) if seen["agents"] == 1 else _members()

    client = FakeClient(
        {
            ("GET", "/api/3/tenants"): _members({"name": "branch-7", "uuid": "t-1"}),
            ("GET", "/api/3/agents"): agents,
            ("DELETE", "/api/3/tenants/t-1"): None,
            ("DELETE", "/api/3/agents/a-1"): _api_error(500, "Internal Server Error"),
        }
    )
    MultitenancyAPI(client).delete_tenant("branch-7")


def test_delete_tenant_fails_if_the_agent_is_still_there_after_a_failed_delete():
    client = FakeClient(
        {
            ("GET", "/api/3/tenants"): _members({"name": "branch-7", "uuid": "t-1"}),
            ("GET", "/api/3/agents"): _members({"name": "branch-7", "uuid": "a-1"}),
            ("DELETE", "/api/3/tenants/t-1"): None,
            ("DELETE", "/api/3/agents/a-1"): _api_error(500, "boom"),
        }
    )
    with pytest.raises(APIError):
        MultitenancyAPI(client).delete_tenant("branch-7")
