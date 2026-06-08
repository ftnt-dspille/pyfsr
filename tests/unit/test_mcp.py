"""Unit tests for the generic pyfsr MCP server (over the tool registry)."""

import json

import pytest

from pyfsr import mcp as mcp_mod
from pyfsr import tools


class RecordingFortiSOAR:
    """Stand-in for FortiSOAR that records constructor args (no network)."""

    last = None

    def __init__(self, base_url, auth, **kwargs):
        RecordingFortiSOAR.last = {"base_url": base_url, "auth": auth, **kwargs}


@pytest.fixture
def recorder(monkeypatch):
    RecordingFortiSOAR.last = None
    monkeypatch.setattr(mcp_mod, "FortiSOAR", RecordingFortiSOAR)
    return RecordingFortiSOAR


# -- client_from_env --------------------------------------------------------
def test_client_from_env_api_key(recorder):
    mcp_mod.client_from_env({"FSR_BASE_URL": "soar.example.com", "FSR_API_KEY": "k"})
    assert recorder.last["base_url"] == "soar.example.com"
    assert recorder.last["auth"] == "k"
    assert recorder.last["verify_ssl"] is True
    assert recorder.last["port"] is None


def test_client_from_env_userpass(recorder):
    mcp_mod.client_from_env(
        {"FSR_BASE_URL": "h", "FSR_USERNAME": "u", "FSR_PASSWORD": "p", "FSR_PORT": "8443"}
    )
    assert recorder.last["auth"] == ("u", "p")
    assert recorder.last["port"] == 8443


def test_client_from_env_verify_ssl_disabled(recorder):
    mcp_mod.client_from_env({"FSR_BASE_URL": "h", "FSR_API_KEY": "k", "FSR_VERIFY_SSL": "false"})
    assert recorder.last["verify_ssl"] is False


def test_client_from_env_host_alias(recorder):
    mcp_mod.client_from_env({"FSR_HOST": "h", "FSR_API_KEY": "k"})
    assert recorder.last["base_url"] == "h"


def test_client_from_env_missing_base_url_raises(recorder):
    with pytest.raises(ValueError, match="FSR_BASE_URL"):
        mcp_mod.client_from_env({"FSR_API_KEY": "k"})


def test_client_from_env_missing_auth_raises(recorder):
    with pytest.raises(ValueError, match="FSR_API_KEY"):
        mcp_mod.client_from_env({"FSR_BASE_URL": "h"})


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
