"""Unit tests for the FortiAI investigation / LLM / MCP API (``client.ai``)."""

from pyfsr.api.ai import TERMINAL_STATUSES, AIApi


class FakeSystemSettings:
    def __init__(self):
        self.public = {"ai_feature": {"enable": False}}
        self.patches = []

    def get_public_values(self):
        return self.public

    def update(self, patch):
        self.patches.append(patch)
        # mimic deep-merge of the ai_feature flag
        self.public.setdefault("ai_feature", {}).update(patch.get("ai_feature", {}))
        return {"publicValues": self.public}


class FakeAlerts:
    def get(self, uuid):
        return {"uuid": uuid, "name": "alert", "@id": f"/api/3/alerts/{uuid}"}


class RecordingClient:
    """Records HTTP calls and returns scripted responses keyed by endpoint."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        self.system_settings = FakeSystemSettings()
        self.alerts = FakeAlerts()

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint))
        return self.responses.get(("GET", endpoint), {})

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return self.responses.get(("POST", endpoint), {"task_id": "t-1", "status": "pending"})

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return self.responses.get(("PUT", endpoint), data)

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint))


def test_enable_features_writes_ai_feature_flag():
    c = RecordingClient()
    ai = AIApi(c)
    assert ai.features_enabled() is False
    ai.enable_features(modified_by="CS Admin")
    assert c.system_settings.patches[0]["ai_feature"]["enable"] is True
    assert c.system_settings.patches[0]["ai_feature"]["lastModifiedBy"] == "CS Admin"
    assert ai.features_enabled() is True


def test_start_investigation_from_ref_fetches_alert_then_posts():
    c = RecordingClient()
    ai = AIApi(c)
    started = ai.start_alert_investigation("alerts:abc-123")
    # posted the resolved full alert JSON, not the bare ref
    post = [call for call in c.calls if call[0] == "POST"][0]
    assert post[1] == "/api/ai/triage/alert"
    assert post[2]["uuid"] == "abc-123"
    assert started["task_id"] == "t-1"


def test_get_status_and_result_use_agents_prefix():
    c = RecordingClient(
        responses={
            ("GET", "/api/ai/agents/t-1/status"): {"task_id": "t-1", "status": "completed"},
            ("GET", "/api/ai/agents/t-1/result"): {"phases": [], "status": "completed"},
        }
    )
    ai = AIApi(c)
    assert ai.get_status("t-1") == "completed"
    assert ai.get_result("t-1")["status"] == "completed"


def test_wait_for_result_returns_immediately_on_terminal_status():
    c = RecordingClient(
        responses={
            ("GET", "/api/ai/agents/t-1/status"): {"status": "failed"},
            ("GET", "/api/ai/agents/t-1/result"): {"error": "boom"},
        }
    )
    ai = AIApi(c)
    result = ai.wait_for_result("t-1", interval=0, timeout=1)
    assert result["status"] == "failed"
    assert "failed" in TERMINAL_STATUSES


def test_list_helpers_coerce_bare_list_and_hydra():
    c = RecordingClient(
        responses={
            ("GET", "/api/ai/mcp"): [{"id": "1", "name": "SOC"}],
            ("GET", "/api/ai/llm/config"): [{"name": "Low Reasoning"}],
            ("GET", "/api/ai/llm/allowed-providers"): {"hydra:member": [{"label": "FortiAI"}]},
        }
    )
    ai = AIApi(c)
    assert ai.list_mcp_servers()[0]["name"] == "SOC"
    assert ai.list_llm_configs()[0]["name"] == "Low Reasoning"
    assert ai.list_providers()[0]["label"] == "FortiAI"


def test_register_and_delete_mcp_server():
    c = RecordingClient(responses={("POST", "/api/3/mcp_configurations"): {"uuid": "m-1"}})
    ai = AIApi(c)
    assert ai.register_mcp_server({"name": "FortiManager"})["uuid"] == "m-1"
    ai.delete_mcp_server("m-1")
    assert ("DELETE", "/api/3/mcp_configurations/m-1") in c.calls


def test_register_mcp_server_json_encodes_dict_authentication():
    # The persistence layer stores authentication as a JSON *string*; passing a
    # dict makes the backend store the literal "Array" and breaks mcp/status.
    c = RecordingClient(responses={("POST", "/api/3/mcp_configurations"): {"uuid": "m-2"}})
    ai = AIApi(c)
    ai.register_mcp_server({"name": "W", "authentication": {"type": "none"}})
    sent = [call for call in c.calls if call[0] == "POST"][0][2]
    assert sent["authentication"] == '{"type": "none"}'


def test_update_mcp_server_puts_and_json_encodes_auth():
    c = RecordingClient()
    ai = AIApi(c)
    ai.update_mcp_server(
        "m-9", {"name": "FortiSIEM", "authentication": {"type": "bearer", "value": "tok"}}
    )
    put = [call for call in c.calls if call[0] == "PUT"][0]
    assert put[1] == "/api/3/mcp_configurations/m-9"
    assert put[2]["authentication"] == '{"type": "bearer", "value": "tok"}'


def test_update_mcp_server_strips_uuid_from_body():
    # The UI puts uuid in the URL and deletes it from the body on PUT.
    c = RecordingClient()
    ai = AIApi(c)
    ai.update_mcp_server("m-9", {"uuid": "m-9", "name": "FortiSIEM"})
    put = [call for call in c.calls if call[0] == "PUT"][0]
    assert "uuid" not in put[2]


def test_save_mcp_server_validates_then_creates_when_new():
    c = RecordingClient(
        responses={
            ("POST", "/api/ai/mcp/validate"): {"valid": True, "tools": [{"name": "x"}]},
            ("POST", "/api/3/mcp_configurations"): {"uuid": "new-1"},
        }
    )
    ai = AIApi(c)
    out = ai.save_mcp_server(
        {"name": "FortiSIEM", "authentication": {"type": "bearer", "value": "t"}}
    )
    assert out["uuid"] == "new-1"
    methods = [call[0:2] for call in c.calls]
    # validated first, then POSTed to create
    assert methods[0] == ("POST", "/api/ai/mcp/validate")
    assert ("POST", "/api/3/mcp_configurations") in methods


def test_save_mcp_server_updates_when_uuid_present():
    c = RecordingClient(
        responses={("POST", "/api/ai/mcp/validate"): {"valid": True}},
    )
    ai = AIApi(c)
    ai.save_mcp_server({"uuid": "u-7", "name": "FortiSIEM"})
    assert any(call[0] == "PUT" and call[1] == "/api/3/mcp_configurations/u-7" for call in c.calls)


def test_save_mcp_server_refuses_on_invalid():
    c = RecordingClient(
        responses={("POST", "/api/ai/mcp/validate"): {"valid": False, "message": "no"}}
    )
    ai = AIApi(c)
    try:
        ai.save_mcp_server({"name": "bad"})
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "did not validate" in str(e)
    # nothing was persisted
    assert not any(call[1] == "/api/3/mcp_configurations" for call in c.calls if len(call) > 1)


def test_list_agent_mcp_servers_reads_allowlist():
    c = RecordingClient(
        responses={
            ("GET", "/api/ai/agent/config/siem/1_0_0"): {
                "agent_name": "siem",
                "config": {"config_type": "siem", "mcp_server": ["a", "b"]},
                "config_id": "cfg-1",
            }
        }
    )
    ai = AIApi(c)
    assert ai.list_agent_mcp_servers("siem", "1_0_0") == ["a", "b"]


def test_list_agent_mcp_servers_friendly_resolves_names():
    c = RecordingClient(
        responses={
            ("GET", "/api/ai/agent/config/siem/1_0_0"): {
                "config": {"mcp_server": ["a", "b", "ghost"]},
            },
            ("GET", "/api/ai/mcp"): [
                {"id": "a", "name": "SOC Framework"},
                {"id": "b", "name": "FortiSIEM"},
            ],
        }
    )
    ai = AIApi(c)
    # unknown uuid ("ghost") falls back to the raw uuid
    assert ai.list_agent_mcp_servers("siem", "1_0_0", friendly=True) == [
        "SOC Framework",
        "FortiSIEM",
        "ghost",
    ]
    assert ai.describe_agent_mcp_servers("siem", "1_0_0")[1] == {"uuid": "b", "name": "FortiSIEM"}


def test_allow_mcp_server_appends_and_dedupes():
    c = RecordingClient(
        responses={
            ("GET", "/api/ai/agent/config/siem/1_0_0"): {
                "agent_name": "siem",
                "name": "siem cfg",
                "config": {"config_type": "siem", "mcp_server": ["a"]},
                "config_id": "cfg-1",
            }
        }
    )
    ai = AIApi(c)
    ai.allow_mcp_server_for_agent("siem", "1_0_0", "fsiem")
    # the gateway authorizes POST (not PUT) for ^agent/config$
    post = [call for call in c.calls if call[0] == "POST" and call[1] == "/api/ai/agent/config"][0]
    assert post[2]["config"]["mcp_server"] == ["a", "fsiem"]
    assert post[2]["agent_name"] == "siem"
    assert post[2]["config_id"] == "cfg-1"
    # idempotent: re-adding does not duplicate
    c.calls.clear()
    ai.allow_mcp_server_for_agent("siem", "1_0_0", "a")
    post = [call for call in c.calls if call[0] == "POST" and call[1] == "/api/ai/agent/config"][0]
    assert post[2]["config"]["mcp_server"] == ["a"]


def test_allow_mcp_server_forks_default_config():
    c = RecordingClient(
        responses={
            ("GET", "/api/ai/agent/config/ioc-enrichment/1_0_0"): {
                "agent_name": "ioc-enrichment",
                "config": {"config_type": "default"},
                "config_id": None,
            },
            ("GET", "/api/ai/agent/config/default"): {
                "config": {"config_type": "default", "llm_provider": "p1", "mcp_server": ["x"]}
            },
        }
    )
    ai = AIApi(c)
    ai.allow_mcp_server_for_agent("ioc-enrichment", "1_0_0", "fsiem")
    post = [call for call in c.calls if call[0] == "POST" and call[1] == "/api/ai/agent/config"][0]
    # seeded from default (llm_provider carried over, config_type dropped) + new uuid
    assert post[2]["config"]["mcp_server"] == ["x", "fsiem"]
    assert post[2]["config"]["llm_provider"] == "p1"
    assert "config_type" not in post[2]["config"]


def test_disallow_mcp_server_removes_uuid():
    c = RecordingClient(
        responses={
            ("GET", "/api/ai/agent/config/siem/1_0_0"): {
                "agent_name": "siem",
                "config": {"config_type": "siem", "mcp_server": ["a", "fsiem"]},
                "config_id": "cfg-1",
            }
        }
    )
    ai = AIApi(c)
    ai.disallow_mcp_server_for_agent("siem", "1_0_0", "fsiem")
    post = [call for call in c.calls if call[0] == "POST" and call[1] == "/api/ai/agent/config"][0]
    assert post[2]["config"]["mcp_server"] == ["a"]


def test_activate_agent_posts_uuids_with_active_param():
    c = RecordingClient()
    ai = AIApi(c)
    ai.activate_agent(["u1", "u2"], active=True)
    assert ("POST", "/api/ai/agent/activate", {"uuids": ["u1", "u2"]}) in c.calls
