"""Unit tests for the ``pyfsr mcp`` CLI verbs (list-tools, call).

Mocks ``client.mcp`` — no live network calls; verifies the CLI wires
arguments through to ``client.mcp.list_tools``/``call_tool`` correctly and
handles bad ``--args`` JSON.
"""

from __future__ import annotations

import argparse
from io import StringIO
from unittest.mock import patch

from pyfsr.cli.__main__ import (
    build_parser,
    cmd_mcp_call,
    cmd_mcp_call_registered,
    cmd_mcp_list_registered,
    cmd_mcp_list_tools,
)


def suppress_output(func):
    def wrapper(*args, **kwargs):
        import sys

        stdout_backup, stderr_backup = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = StringIO(), StringIO()
            return func(*args, **kwargs)
        finally:
            sys.stdout, sys.stderr = stdout_backup, stderr_backup

    return wrapper


class MockMCP:
    def __init__(self, tools=None, call_result=None):
        self._tools = tools or [{"name": "get_alert", "description": "fetch an alert\nmore detail", "input_schema": {}}]
        self._call_result = call_result if call_result is not None else {"status": "success"}
        self.list_tools_calls = []
        self.call_tool_calls = []

    def list_tools(self, server):
        self.list_tools_calls.append(server)
        return self._tools

    def call_tool(self, server, name, arguments):
        self.call_tool_calls.append((server, name, arguments))
        return self._call_result


class MockClient:
    def __init__(self, mcp=None):
        self.mcp = mcp or MockMCP()
        self.http_trace = False


# --- parser wiring ------------------------------------------------------------
def test_parser_wires_mcp_list_tools():
    parser = build_parser()
    args = parser.parse_args(["mcp", "list-tools", "--mcp-server", "connector:virustotal"])
    assert args.mcp_server == "connector:virustotal"
    assert args.func is cmd_mcp_list_tools


def test_parser_wires_mcp_call():
    parser = build_parser()
    args = parser.parse_args(["mcp", "call", "get_alert", "--args", '{"uuid": ["x"]}'])
    assert args.tool == "get_alert"
    assert args.args == '{"uuid": ["x"]}'
    assert args.mcp_server == "soc"  # default
    assert args.func is cmd_mcp_call


def test_parser_server_flag_is_connection_override_not_mcp_path():
    """--server is playbook_cmds' FSR_BASE_URL override; --mcp-server is ours.
    A prior draft of this CLI clashed on --server — pin the distinction."""
    parser = build_parser()
    args = parser.parse_args(["mcp", "list-tools", "--server", "https://soar.example.com"])
    assert args.server == "https://soar.example.com"
    assert args.mcp_server == "soc"  # untouched, still the default


# --- cmd_mcp_list_tools --------------------------------------------------------
@suppress_output
def test_list_tools_table_format():
    args = argparse.Namespace(mcp_server="soc", fmt="table")
    mcp = MockMCP()
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClient(mcp)):
        result = cmd_mcp_list_tools(args)
    assert result == 0
    assert mcp.list_tools_calls == ["soc"]


def test_list_tools_json_format_prints_full_payload(capsys):
    args = argparse.Namespace(mcp_server="connector:virustotal", fmt="json")
    tools = [{"name": "get_reputation", "description": "d", "input_schema": {"type": "object"}}]
    mcp = MockMCP(tools=tools)
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClient(mcp)):
        result = cmd_mcp_list_tools(args)
    assert result == 0
    assert mcp.list_tools_calls == ["connector:virustotal"]
    out = capsys.readouterr().out
    assert '"get_reputation"' in out
    assert '"input_schema"' in out


# --- cmd_mcp_call --------------------------------------------------------------
def test_call_dispatches_parsed_json_args(capsys):
    args = argparse.Namespace(mcp_server="soc", tool="get_alert", args='{"uuid": ["a-1"]}')
    mcp = MockMCP(call_result={"status": "success", "result": {"ok": True}})
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClient(mcp)):
        result = cmd_mcp_call(args)
    assert result == 0
    assert mcp.call_tool_calls == [("soc", "get_alert", {"uuid": ["a-1"]})]
    out = capsys.readouterr().out
    assert '"status": "success"' in out


def test_call_defaults_args_to_empty_object():
    args = argparse.Namespace(mcp_server="soc", tool="get_current_datetime", args="{}")
    mcp = MockMCP()
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClient(mcp)):
        result = cmd_mcp_call(args)
    assert result == 0
    assert mcp.call_tool_calls == [("soc", "get_current_datetime", {})]


@suppress_output
def test_call_rejects_malformed_json_args():
    args = argparse.Namespace(mcp_server="soc", tool="get_alert", args="not json")
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClient()):
        result = cmd_mcp_call(args)
    assert result == 1


@suppress_output
def test_call_rejects_non_object_json_args():
    args = argparse.Namespace(mcp_server="soc", tool="get_alert", args="[1, 2, 3]")
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClient()):
        result = cmd_mcp_call(args)
    assert result == 1


# --- main() error handling -----------------------------------------------------
def test_missing_mcp_dependency_surfaces_as_clean_error(capsys):
    """client.mcp raises ImportError when the optional 'mcp' dep is absent;
    main()'s exception guard already catches ImportError for every command —
    pin that the mcp group benefits from it too, not just appliance/records."""
    from pyfsr.cli.__main__ import main

    class RaisingMCP:
        def list_tools(self, server):
            raise ImportError(
                "client.mcp requires the optional 'mcp' dependency. Install it with: pip install 'pyfsr[mcp]'"
            )

    with patch(
        "pyfsr.cli.__main__.playbook_cmds._make_client",
        return_value=MockClient(RaisingMCP()),
    ):
        exit_code = main(["mcp", "list-tools"])
    assert exit_code == 1
    assert "pyfsr[mcp]" in capsys.readouterr().err


# --- registered-server verbs (client.ai) --------------------------------------
class MockAI:
    def __init__(self, tools=None, call_result=None):
        from pyfsr.models import MCPTool, MCPToolResult

        self._tools = tools or [MCPTool(name="get_incident", description="d")]
        self._call_result = call_result or MCPToolResult(status="success", result={"incidentId": 1})
        self.list_calls = []
        self.call_calls = []

    def list_registered_tools(self, server, *, token=None):
        self.list_calls.append((server, token))
        return self._tools

    def call_registered_tool(self, server, tool, arguments=None, *, token=None):
        self.call_calls.append((server, tool, arguments, token))
        return self._call_result


class MockClientAI(MockClient):
    def __init__(self, ai=None):
        super().__init__()
        self.ai = ai or MockAI()


def test_parser_wires_call_registered():
    parser = build_parser()
    args = parser.parse_args(
        ["mcp", "call-registered", "Bridge: FortiSIEM", "get_incident", "--args", "{}", "--mcp-token", "t"]
    )
    assert args.registered_server == "Bridge: FortiSIEM"
    assert args.tool == "get_incident"
    assert args.mcp_token == "t"
    assert args.func is cmd_mcp_call_registered


def test_call_registered_dispatches_and_prints_result(capsys):
    ai = MockAI()
    args = argparse.Namespace(
        registered_server="Bridge: FortiSIEM", tool="get_incident", args='{"incident_id": "1"}', mcp_token=None
    )
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClientAI(ai)):
        assert cmd_mcp_call_registered(args) == 0
    assert ai.call_calls == [("Bridge: FortiSIEM", "get_incident", {"incident_id": "1"}, None)]
    assert '"incidentId": 1' in capsys.readouterr().out


def test_list_registered_table():
    ai = MockAI()
    args = argparse.Namespace(registered_server="Bridge: FortiSIEM", fmt="table", mcp_token=None)
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClientAI(ai)):
        assert suppress_output(cmd_mcp_list_registered)(args) == 0
    assert ai.list_calls == [("Bridge: FortiSIEM", None)]


def test_call_registered_rejects_malformed_args():
    args = argparse.Namespace(registered_server="X", tool="t", args="not-json", mcp_token=None)
    with patch("pyfsr.cli.__main__.playbook_cmds._make_client", return_value=MockClientAI()):
        assert suppress_output(cmd_mcp_call_registered)(args) == 1
