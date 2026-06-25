"""Unit tests for the generic pyfsr MCP server (over the tool registry)."""

import json

import pytest

from pyfsr import mcp as mcp_mod
from pyfsr import tools


# -- client_from_env (delegates to pyfsr.config.EnvConfig) ------------------
def test_client_from_env_delegates(monkeypatch):
    captured = {}

    class FakeCfg:
        @staticmethod
        def from_env(env):
            captured["env"] = env
            return FakeCfg()

        def client(self):
            return "CLIENT"

    monkeypatch.setattr(mcp_mod, "EnvConfig", FakeCfg)
    result = mcp_mod.client_from_env({"FSR_BASE_URL": "h", "FSR_API_KEY": "k"})
    assert result == "CLIENT"
    assert captured["env"] == {"FSR_BASE_URL": "h", "FSR_API_KEY": "k"}


def test_client_from_env_missing_config_raises():
    with pytest.raises(ValueError, match="FSR_BASE_URL"):
        mcp_mod.client_from_env({})


# -- tool mapping -----------------------------------------------------------
def test_mcp_tools_mirror_registry():
    mcp_tools = mcp_mod._mcp_tools()
    assert len(mcp_tools) == len(tools.list_tools())
    names = {t.name for t in mcp_tools}
    assert "get_record" in names and "run_connector_operation" in names
    sample = next(t for t in mcp_tools if t.name == "get_record")
    assert sample.inputSchema["type"] == "object"
    assert sample.description


# -- call dispatch ----------------------------------------------------------
class FakeClient:
    def list_modules(self, refresh=False):
        return [{"type": "alerts", "label": "Alerts", "plural": "alerts"}]


def test_call_wraps_result_as_text_json():
    content = mcp_mod._call(FakeClient(), "list_modules", {})
    assert len(content) == 1
    assert content[0].type == "text"
    payload = json.loads(content[0].text)
    assert payload["modules"][0]["type"] == "alerts"


def test_call_dispatches_new_admin_tool_as_text_json():
    # The new registry tools (module admin / connector config / run-debug / upsert)
    # flow through the same _call -> dispatch path as the originals.
    class FakeConnectors:
        def default_config(self, connector, version=None):
            return {"server": "", "verify_ssl": True}

    class FakeModulesAdmin:
        pass

    class Client:
        def __init__(self):
            self.connectors = FakeConnectors()
            self.modules_admin = FakeModulesAdmin()

    content = mcp_mod._call(Client(), "default_connector_config", {"connector": "code-snippet"})
    assert len(content) == 1
    assert content[0].type == "text"
    payload = json.loads(content[0].text)
    assert payload == {"server": "", "verify_ssl": True}


def test_call_unknown_tool_returns_structured_error():
    content = mcp_mod._call(FakeClient(), "does_not_exist", {})
    payload = json.loads(content[0].text)
    assert payload["error"]["type"] == "UnknownTool"


# -- server wiring ----------------------------------------------------------
def test_build_server_registers_handlers():
    server = mcp_mod.build_server(FakeClient())
    assert server.name == "pyfsr"
    # low-level Server records handlers keyed by their MCP request type.
    handler_names = {req.__name__ for req in server.request_handlers}
    assert "ListToolsRequest" in handler_names
    assert "CallToolRequest" in handler_names
