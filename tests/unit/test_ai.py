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
