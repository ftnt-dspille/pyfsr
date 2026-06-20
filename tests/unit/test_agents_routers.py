"""Unit tests for the expanded agents lifecycle/installer + routers wrappers."""

import pytest

from pyfsr.api.agents import AgentsAPI
from pyfsr.api.routers import RoutersAPI


class _Resp:
    def __init__(self, content=b"", payload=None):
        self.content = content
        self._payload = payload

    def json(self):
        return self._payload


class RecordingClient:
    def __init__(self, *, members=None, post_payload=None, get_payload=None, raw=b""):
        self.calls = []
        self._members = members or []
        self._post_payload = post_payload
        self._get_payload = get_payload
        self._raw = raw

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        if self._get_payload is not None:
            return self._get_payload
        return {"hydra:member": self._members}

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        if self._post_payload is not None:
            return self._post_payload
        return {"uuid": "ag-1", **(data or {})}

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        if self._post_payload is not None:
            return self._post_payload
        return {**(data or {})}

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))

    def request(self, method, endpoint, data=None, **kw):
        self.calls.append((method, endpoint, data))
        return _Resp(content=self._raw, payload=self._post_payload)


# --------------------------------------------------------------------- agents
def test_agents_get_by_uuid():
    c = RecordingClient(get_payload={"uuid": "ag-1", "agentId": "a1"})
    out = AgentsAPI(c).get("ag-1")
    assert out["agentId"] == "a1"
    assert c.calls[-1] == ("GET", "/api/3/agents/ag-1", None)


def test_agents_list_passes_limit():
    c = RecordingClient(members=[{"agentId": "a1", "active": True}])
    AgentsAPI(c).list()
    assert c.calls[-1][2] == {"$limit": 2147483647}


def test_agents_create_normalizes_router_and_installer():
    c = RecordingClient()
    AgentsAPI(c).create("edge-1", router="r-uuid", installer_type="docker", description="x")
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/3/agents"
    assert data["name"] == "edge-1" and data["description"] == "x"
    assert data["router"] == "/api/3/routers/r-uuid"
    assert data["installerType"] == "/api/3/picklists/d9f874be-3068-4282-9aed-100eba51e61b"


def test_agents_create_accepts_router_record_and_iri():
    c = RecordingClient()
    api = AgentsAPI(c)
    api.create("a", router={"@id": "/api/3/routers/abc", "uuid": "abc"})
    assert c.calls[-1][2]["router"] == "/api/3/routers/abc"
    api.create("b", router="/api/3/routers/xyz", installer_type="bash")
    data = c.calls[-1][2]
    assert data["router"] == "/api/3/routers/xyz"
    assert data["installerType"] == "/api/3/picklists/a8181039-30a0-4807-b470-50de69d37561"


def test_agents_create_requires_name_and_router():
    c = RecordingClient()
    with pytest.raises(ValueError):
        AgentsAPI(c).create("", router="r")
    with pytest.raises(ValueError):
        AgentsAPI(c).create("ok", router="")


def test_agents_delete_uses_no_body():
    c = RecordingClient()
    AgentsAPI(c).delete("ag-1")
    assert c.calls[-1] == ("DELETE", "/api/3/agents/ag-1", None)


def test_agents_installer_returns_bytes():
    c = RecordingClient(raw=b"MZ\x00binary-bundle")
    out = AgentsAPI(c).installer("a1", connectors=["cyops_utilities"])
    assert out == b"MZ\x00binary-bundle"
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/integration/agent-installer/?format=json"
    assert data == {
        "agent": "a1",
        "connectors": ["cyops_utilities"],
        "include_last_known_configurations": False,
    }


def test_agents_install_connector_builds_body():
    c = RecordingClient(post_payload={"ok": True})
    AgentsAPI(c).install_connector("a1", name="cyops_utilities", version="3.7.1")
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/integration/install-connector/?format=json"
    assert data["agent"] == ["a1"]
    assert data["name"] == "cyops_utilities" and data["version"] == "3.7.1"
    assert data["label"] == "cyops_utilities" and data["publisher"] == "Fortinet"


def test_connector_install_status_filters_by_agent():
    rows = [
        {"agent": "a1", "status": "Completed"},
        {"agent": "a2", "status": "awaiting"},
    ]
    c = RecordingClient(post_payload=rows)
    out = AgentsAPI(c).connector_install_status("cyops_utilities", "3.7.1", agent_id="a1")
    assert len(out) == 1
    assert out[0].agent == "a1"
    assert out[0].status == "Completed"
    endpoint = c.calls[-1][1]
    assert endpoint == ("/api/integration/connectors/agents/cyops_utilities/3.7.1/?format=json&active=true")


def test_connector_install_status_unwraps_dict_payload():
    c = RecordingClient(post_payload={"data": [{"agent": "a1"}]})
    out = AgentsAPI(c).connector_install_status("c", "1.0", active=False)
    assert len(out) == 1
    assert out[0].agent == "a1"
    assert c.calls[-1][1].endswith("/c/1.0/?format=json")


# -------------------------------------------------------------------- routers
def test_routers_list():
    c = RecordingClient(members=[{"uuid": "r1", "name": "broker"}])
    out = RoutersAPI(c).list()
    assert out == [{"uuid": "r1", "name": "broker"}]
    assert c.calls[-1] == ("GET", "/api/3/routers", {"$limit": 2147483647})


def test_routers_first_orders_by_name():
    c = RecordingClient(members=[{"uuid": "r1", "name": "broker"}])
    out = RoutersAPI(c).first()
    assert out["uuid"] == "r1"
    assert c.calls[-1][2] == {"$limit": 1, "$orderby": "+name"}


def test_routers_first_none_when_empty():
    c = RecordingClient(members=[])
    assert RoutersAPI(c).first() is None


# ------------------------------------------- connector upgrade/uninstall/heartbeat
def test_upgrade_connector_puts_spec_body():
    c = RecordingClient(post_payload={"status": "ok"})
    AgentsAPI(c).upgrade_connector("hash-1", name="hello-world", version="1.0.4")
    assert c.calls[-1] == (
        "PUT",
        "/api/integration/install-connector/?format=json",
        {"name": "hello-world", "version": "1.0.4", "agent_id": "hash-1"},
    )


def test_uninstall_connector_deletes_with_body():
    c = RecordingClient(post_payload={"status": "ok"})
    AgentsAPI(c).uninstall_connector("hash-1", name="hello-world", version="1.0.4")
    assert c.calls[-1] == (
        "DELETE",
        "/api/integration/install-connector/?format=json",
        {"name": "hello-world", "version": "1.0.4", "agent_id": "hash-1"},
    )


def test_heartbeat_hits_agent_path():
    c = RecordingClient(get_payload={"status": "alive"})
    out = AgentsAPI(c).heartbeat("hash-1")
    assert out == {"status": "alive"}
    assert c.calls[-1] == ("GET", "/api/integration/agent-heartbeat/hash-1/", None)


def test_connector_lifecycle_requires_agent_id():
    c = RecordingClient()
    for fn in ("upgrade_connector", "uninstall_connector", "heartbeat"):
        with pytest.raises(ValueError):
            if fn == "heartbeat":
                getattr(AgentsAPI(c), fn)("")
            else:
                getattr(AgentsAPI(c), fn)("", name="x", version="1")
