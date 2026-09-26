"""pyfsr.api.ai: adding a connector LLM profile and switching agents between profiles."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyfsr.api.ai import AIApi, LLMSetupError

LOW = {
    "uuid": "low",
    "name": "Low Reasoning",
    "isdefault": True,
    "provider": "fortisoar",
    "config": {"connector_name": "fortinet-fortiai-proxy", "connector_config_id": "c1"},
}
OAI = {
    "uuid": "oai",
    "name": "OpenAI GPT-4.1",
    "provider": "fortisoar",
    "config": {"connector_name": "openai", "connector_config_id": "c2"},
}
AGENTS = [
    {"uuid": "a1", "name": "siem", "version": "1.0.0"},
    {"uuid": "a2", "name": "investigation-verdict", "version": "1.0.0"},
]


class FakeClient:
    def __init__(self, allowed=("fortinet-fortiai-proxy", "openai"), installed=True):
        self.posts: list[tuple[str, object]] = []
        self.configs = {
            "siem": {"config_type": "default", "llm_provider": "low"},
            "investigation-verdict": {"config_type": "custom", "llm_provider": "high"},
        }
        self.allowed = allowed
        hub_item = SimpleNamespace(name="openai", version="4.1.0")
        self.content_hub = SimpleNamespace(
            search_installed_connectors=lambda term: [hub_item] if installed else [],
            search_available_connectors=lambda term: [hub_item],
        )
        self.installs: list[tuple[str, str]] = []
        self.saved_configs: list[dict] = []

        def install(name, version, **kw):
            self.installs.append((name, version))
            return SimpleNamespace(status="Import Complete")

        def upsert_configuration(connector, config, *, name, version=None):
            self.saved_configs.append({"connector": connector, "name": name, **config})
            return SimpleNamespace(config_id="c2")

        self.connectors = SimpleNamespace(
            install=install,
            upsert_configuration=upsert_configuration,
            healthcheck=lambda connector, config=None: SimpleNamespace(status="Available", message=""),
            execute=lambda connector, operation, config=None, params=None: SimpleNamespace(
                status=self.completion_status, message="You have no credits remaining"
            ),
        )
        self.completion_status = "Success"

    def get(self, endpoint, params=None, **kw):
        if endpoint == "/api/ai/llm/config":
            return [LOW, OAI]
        if endpoint == "/api/ai/llm/config/oai":
            return OAI
        if endpoint == "/api/ai/llm/allowed-providers":
            return [{"name": n} for n in self.allowed]
        if endpoint == "/api/ai/agent/":
            return AGENTS
        if endpoint == "/api/ai/agent/config/default":
            return {"config": {"config_type": "default", "llm_provider": "low", "mcp_server": []}}
        if endpoint.startswith("/api/ai/agent/config/"):
            name = endpoint.split("/")[5]
            cfg = self.configs[name]
            return {
                "agent_name": name,
                "name": "Custom Configuration" if cfg["config_type"] == "custom" else "Default",
                "config_id": "x" if cfg["config_type"] == "custom" else None,
                "config": dict(cfg),
            }
        if endpoint.startswith("/api/ai/agent/"):
            return next(a for a in AGENTS if a["name"] == endpoint.split("/")[4])
        raise AssertionError(endpoint)

    def post(self, endpoint, data=None, **kw):
        self.posts.append((endpoint, data))
        if endpoint == "/api/ai/agent/config":
            self.configs[data["agent_name"]] = {
                "config_type": "custom",
                "llm_provider": data["config"].get("llm_provider"),
            }
        return data


def test_assign_writes_the_record_and_every_config_and_returns_the_old_state():
    c = FakeClient()
    before = AIApi(c).assign_llm("OpenAI GPT-4.1")
    assert before["investigation-verdict"]["llm_provider"] == "high"
    bulk = [d for e, d in c.posts if e == "/api/ai/agent/llm/config"]
    assert bulk == [[{"uuid": "a1", "llm": "oai"}, {"uuid": "a2", "llm": "oai"}]]
    assert {n: cfg["llm_provider"] for n, cfg in c.configs.items()} == {"siem": "oai", "investigation-verdict": "oai"}
    # the agent on the default config got its own row, with a client-minted id
    siem_write = next(d for e, d in c.posts if e == "/api/ai/agent/config" and d["agent_name"] == "siem")
    assert siem_write["config_id"] and siem_write["name"] == "Custom Configuration"


def test_restore_puts_each_agent_back():
    c = FakeClient()
    api = AIApi(c)
    before = api.assign_llm("OpenAI GPT-4.1")
    api.restore_llm_assignments(before)
    assert {n: cfg["llm_provider"] for n, cfg in c.configs.items()} == {"siem": "low", "investigation-verdict": "high"}


def test_assign_rejects_unknown_profile_or_agent():
    with pytest.raises(ValueError, match="no LLM profile"):
        AIApi(FakeClient()).assign_llm("nope")
    with pytest.raises(ValueError, match="unknown agent"):
        AIApi(FakeClient()).assign_llm("OpenAI GPT-4.1", ["ghost"])


def test_setup_installs_configures_and_creates_the_profile():
    c = FakeClient(installed=False)
    AIApi(c).setup_connector_llm("OpenAI GPT-4.1", model="GPT-4.1", api_key="k")
    assert c.installs == [("openai", "4.1.0")]
    assert c.saved_configs == [{"connector": "openai", "name": "OpenAI GPT-4.1", "apiKey": "k", "model": "GPT-4.1"}]
    body = next(d for e, d in c.posts if e == "/api/ai/llm/config")[0]
    assert body["provider"] == "fortisoar" and body["uuid"] == "oai"
    assert body["config"] == {"connector_name": "openai", "connector_config_id": "c2"}


def test_setup_stops_when_the_test_completion_fails():
    # a valid key passes the health check even with no credits left
    c = FakeClient()
    c.completion_status = "Failed"
    with pytest.raises(LLMSetupError, match="test completion"):
        AIApi(c).setup_connector_llm("OpenAI GPT-4.1", model="GPT-4.1", api_key="k")
    assert not [e for e, _ in c.posts if e == "/api/ai/llm/config"]  # no dead profile created
