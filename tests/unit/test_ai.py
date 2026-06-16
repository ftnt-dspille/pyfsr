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
    def __init__(self, records=None):
        self.records = records or {}
        self.updates = []

    def get(self, uuid):
        return self.records.get(
            uuid, {"uuid": uuid, "name": "alert", "@id": f"/api/3/alerts/{uuid}"}
        )

    def update(self, uuid, data):
        self.updates.append((uuid, data))
        return {"uuid": uuid, **data}


class RecordingClient:
    """Records HTTP calls and returns scripted responses keyed by endpoint."""

    def __init__(self, responses=None, alerts=None):
        self.calls = []
        self.responses = responses or {}
        self.system_settings = FakeSystemSettings()
        self.alerts = alerts or FakeAlerts()

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


def test_start_investigation_links_task_id_back_to_alert():
    c = RecordingClient()
    ai = AIApi(c)
    ai.start_alert_investigation("alerts:abc-123")
    # the returned task_id is written back to the alert's triagetaskid field
    assert c.alerts.updates == [("abc-123", {"triagetaskid": "t-1"})]


def test_start_investigation_link_false_skips_writeback():
    c = RecordingClient()
    ai = AIApi(c)
    ai.start_alert_investigation("alerts:abc-123", link=False)
    assert c.alerts.updates == []


def test_start_investigation_link_skipped_when_uuid_unknown():
    c = RecordingClient()
    ai = AIApi(c)
    # an alert dict with no uuid/@id/id can't be linked — best-effort, no error
    ai.start_alert_investigation({"name": "no-id alert"})
    assert c.alerts.updates == []


def test_get_investigation_for_alert_reads_field():
    alerts = FakeAlerts({"abc-123": {"uuid": "abc-123", "triagetaskid": "t-9"}})
    c = RecordingClient(alerts=alerts)
    ai = AIApi(c)
    assert ai.get_investigation_for_alert("alerts:abc-123") == "t-9"
    # accepts a record dict directly too
    assert ai.get_investigation_for_alert({"triagetaskid": "t-9"}) == "t-9"


def test_get_investigation_for_alert_none_when_unset():
    alerts = FakeAlerts({"abc-123": {"uuid": "abc-123"}})
    c = RecordingClient(alerts=alerts)
    ai = AIApi(c)
    assert ai.get_investigation_for_alert("abc-123") is None


def test_get_alert_investigation_status_combines_field_and_status():
    alerts = FakeAlerts({"abc-123": {"uuid": "abc-123", "triagetaskid": "t-9"}})
    c = RecordingClient(
        responses={("GET", "/api/ai/agents/t-9/status"): {"status": "inprogress"}},
        alerts=alerts,
    )
    ai = AIApi(c)
    assert ai.get_alert_investigation_status("abc-123") == {
        "task_id": "t-9",
        "status": "inprogress",
    }


def test_get_alert_investigation_status_none_when_no_investigation():
    alerts = FakeAlerts({"abc-123": {"uuid": "abc-123"}})
    c = RecordingClient(alerts=alerts)
    ai = AIApi(c)
    assert ai.get_alert_investigation_status("abc-123") is None


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


# -- MCP tool surface -------------------------------------------------------
def test_list_mcp_tools_extracts_names_from_validate():
    c = RecordingClient(
        responses={
            ("POST", "/api/ai/mcp/validate"): {
                "valid": True,
                "tools": [{"name": "hunt_ioc_siem"}, {"name": "get_incidents_by_entity"}],
            }
        }
    )
    ai = AIApi(c)
    assert ai.list_mcp_tools({"url": "x"}) == ["hunt_ioc_siem", "get_incidents_by_entity"]


def test_mcp_tool_catalog_maps_tools_to_servers():
    # validate returns a different tool set per server (keyed off posted url),
    # and the stored authentication is a JSON *string* that must be decoded.
    class CatalogClient(RecordingClient):
        def post(self, endpoint, data=None, params=None, **kw):
            self.calls.append(("POST", endpoint, data))
            if endpoint == "/api/ai/mcp/validate":
                assert isinstance(data.get("authentication"), dict)  # decoded
                tools = {
                    "https://siem": [{"name": "get_incident_by_id"}],
                    "https://soc": [{"name": "hunt_ioc_siem", "description": "hunt"}],
                }.get(data.get("url"), [])
                return {"valid": True, "tools": tools}
            return {}

    c = CatalogClient(
        responses={
            ("GET", "/api/3/mcp_configurations"): {
                "hydra:member": [
                    {
                        "uuid": "s1",
                        "name": "FortiSIEM",
                        "url": "https://siem",
                        "authentication": '{"type":"BEARER","value":"t"}',
                    },
                    {
                        "uuid": "s2",
                        "name": "SOC Framework",
                        "url": "https://soc",
                        "authentication": '{"type":"FSR"}',
                    },
                ]
            }
        }
    )
    cat = AIApi(c).mcp_tool_catalog()
    assert cat["get_incident_by_id"]["server"] == "FortiSIEM"
    assert cat["hunt_ioc_siem"] == {
        "server": "SOC Framework",
        "server_uuid": "s2",
        "description": "hunt",
    }


def test_mcp_tool_catalog_skips_unreachable_server():
    class FlakyClient(RecordingClient):
        def post(self, endpoint, data=None, params=None, **kw):
            if endpoint == "/api/ai/mcp/validate" and data.get("url") == "https://dead":
                raise RuntimeError("connection refused")
            if endpoint == "/api/ai/mcp/validate":
                return {"valid": True, "tools": [{"name": "get_weather"}]}
            return {}

    c = FlakyClient(
        responses={
            ("GET", "/api/3/mcp_configurations"): {
                "hydra:member": [
                    {"uuid": "d", "name": "Dead", "url": "https://dead", "authentication": "{}"},
                    {"uuid": "w", "name": "Weather", "url": "https://ok", "authentication": "{}"},
                ]
            }
        }
    )
    cat = AIApi(c).mcp_tool_catalog()
    assert set(cat) == {"get_weather"}  # dead server skipped, not fatal


def test_attribute_tool_calls_tags_each_call_with_server():
    c = RecordingClient(responses={("GET", "/api/3/llm_activity_logs"): _LOGS})
    ai = AIApi(c)
    catalog = {"hunt_ioc_siem": {"server": "SOC Framework", "server_uuid": "s2"}}
    out = ai.attribute_tool_calls("task-1", catalog=catalog)
    assert out[0]["tool_name"] == "hunt_ioc_siem"
    assert out[0]["server"] == "SOC Framework"
    # a tool with no registered owner reports server=None (e.g. built-in action)
    assert out[1]["tool_name"] == "get_reputation_by_entity"
    assert out[1]["server"] is None


# -- investigation questions / weighting ------------------------------------
_RESULT = {
    "summary": {"classification": "Malicious", "key_findings": [{"id": "F1"}]},
    "hypotheses": [
        {"id": 1, "name": "Routine activity", "intentStatus": "RULED_OUT", "attentionNeeded": "No"},
        {"id": 4, "name": "Brute force", "intentStatus": "CONFIRMED", "attentionNeeded": "Yes"},
    ],
    "logs": [
        {
            "index": 2,
            "question": "IP reputation?",
            "agent_label": "Threat Intelligence Provider",
            "agent_hint": "TI",
            "params": {"ioc": [{"value": "1.2.3.4"}]},
            "result": "No",
            "evidence": "No reputation available.",
            "status": "success",
            "supports": [4],
            "weakens": ["1"],
            "primary_information_type": ["Threat Intelligence"],
        },
        {
            "index": 3,
            "question": "Asset critical?",
            "agent_label": "Asset Context Provider",
            "params": {"asset": "host1"},
            "result": "Normal",
            "evidence": "Not critical.",
            "status": "success",
            "supports": ["1"],
            "weakens": [4],
        },
    ],
}


def test_investigation_questions_shapes_logs():
    c = RecordingClient(responses={("GET", "/api/ai/agents/t-1/result"): _RESULT})
    qs = AIApi(c).investigation_questions("t-1")
    assert [q["index"] for q in qs] == [2, 3]
    q0 = qs[0]
    assert q0["agent"] == "Threat Intelligence Provider"
    assert q0["input"] == {"ioc": [{"value": "1.2.3.4"}]}
    assert q0["response"] == "No"
    assert q0["evidence"] == "No reputation available."
    # hypothesis ids normalized to strings
    assert q0["supports"] == ["4"] and q0["weakens"] == ["1"]


def test_hypothesis_evidence_links_questions_to_verdict():
    c = RecordingClient(responses={("GET", "/api/ai/agents/t-1/result"): _RESULT})
    ev = AIApi(c).hypothesis_evidence("t-1")
    assert ev["classification"] == "Malicious"
    by_id = {h["id"]: h for h in ev["hypotheses"]}
    # H1 ruled out: supported by Q3, weakened by Q2
    assert by_id["1"]["status"] == "RULED_OUT"
    assert by_id["1"]["support_count"] == 1 and by_id["1"]["weaken_count"] == 1
    assert by_id["1"]["supported_by"][0]["index"] == 3
    assert by_id["1"]["weakened_by"][0]["index"] == 2
    # H4 confirmed: supported by Q2 (the TI evidence), weakened by Q3
    assert by_id["4"]["status"] == "CONFIRMED"
    assert by_id["4"]["supported_by"][0]["agent"] == "Threat Intelligence Provider"
    assert by_id["4"]["supported_by"][0]["evidence"] == "No reputation available."


# -- tool-usage evidence ----------------------------------------------------
_LOGS = {
    "hydra:member": [
        {
            "correlationID": "task-1",
            "title": "ioc-enrichment",
            "modelName": "gpt-4o-mini",
            "response": {
                "content": "calling",
                "tool_name": "hunt_ioc_siem",
                "tool_args": {"ip": "1.1.1.1"},
            },
        },
        # response delivered as a JSON string is parsed too
        {
            "correlationID": "task-1",
            "title": "siem",
            "response": '{"content": "x", "tool_name": "get_reputation_by_entity"}',
        },
        # no tool selected -> skipped
        {
            "correlationID": "task-1",
            "title": "normalization",
            "response": {"content": "no tool", "tool_name": None},
        },
    ]
}


def test_tool_usage_returns_only_tool_selecting_records():
    c = RecordingClient(responses={("GET", "/api/3/llm_activity_logs"): _LOGS})
    ai = AIApi(c)
    calls = ai.tool_usage(correlation_id="task-1")
    assert [x["tool_name"] for x in calls] == ["hunt_ioc_siem", "get_reputation_by_entity"]
    assert calls[0]["tool_args"] == {"ip": "1.1.1.1"}
    assert calls[0]["correlation_id"] == "task-1"


def test_investigation_tool_calls_delegates_to_tool_usage():
    c = RecordingClient(responses={("GET", "/api/3/llm_activity_logs"): _LOGS})
    ai = AIApi(c)
    assert len(ai.investigation_tool_calls("task-1")) == 2


def test_find_investigations_groups_distinct_correlation_ids():
    logs = {
        "hydra:member": [
            {"correlationID": "task-a"},
            {"correlationID": "task-a"},
            {"correlationID": "task-b"},
            {"correlationID": None},
        ]
    }
    c = RecordingClient(responses={("GET", "/api/3/llm_activity_logs"): logs})
    ai = AIApi(c)
    found = ai.find_investigations("alerts:740a751c")
    assert {f["task_id"] for f in found} == {"task-a", "task-b"}
    assert next(f for f in found if f["task_id"] == "task-a")["log_count"] == 2
