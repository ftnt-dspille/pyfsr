"""Unit tests for the MCP server's multi-instance routing layer."""

import json

from pyfsr.agent import mcp as mcp_mod
from pyfsr.agent import tools


class FakeClient:
    def __init__(self, tag):
        self.tag = tag
        self.base_url = f"https://{tag}"

    def list_modules(self, refresh=False):
        return [{"type": self.tag}]


class FakeRegistry:
    """Minimal InstanceRegistry stand-in with two named clients."""

    def __init__(self):
        self.default = "a"
        self._clients = {"a": FakeClient("a"), "b": FakeClient("b")}

    def names(self):
        return ["a", "b"]

    def describe(self):
        return [{"instance": n, "base_url": f"https://{n}", "default": n == self.default} for n in self.names()]

    def client(self, alias=None):
        name = alias or self.default
        if name not in self._clients:
            raise ValueError(f"unknown instance {name!r}; known instances: {self.names()}")
        return self._clients[name]


# -- schema injection -------------------------------------------------------
def test_registry_tools_gain_instance_arg_and_meta_tool():
    reg = FakeRegistry()
    mcp_tools = mcp_mod._mcp_tools(reg)
    names = {t.name for t in mcp_tools}
    # One extra tool vs the bare registry: the list_instances meta-tool.
    assert names == {t["name"] for t in tools.tool_schemas()} | {mcp_mod.LIST_INSTANCES}

    sample = next(t for t in mcp_tools if t.name == "get_record")
    inst = sample.inputSchema["properties"]["instance"]
    assert inst["type"] == "string"
    assert inst["enum"] == ["a", "b"]
    # Original required fields are untouched by the injection.
    assert set(sample.inputSchema["required"]) == {"module", "ref"}


def test_no_registry_means_no_instance_arg():
    mcp_tools = mcp_mod._mcp_tools()  # single-instance / back-compat
    names = {t.name for t in mcp_tools}
    assert mcp_mod.LIST_INSTANCES not in names
    sample = next(t for t in mcp_tools if t.name == "get_record")
    assert "instance" not in sample.inputSchema["properties"]


# -- routing ----------------------------------------------------------------
def test_route_call_targets_selected_instance():
    reg = FakeRegistry()
    content = mcp_mod._route_call(reg, "list_modules", {"instance": "b"})
    payload = json.loads(content[0].text)
    assert payload["modules"][0]["type"] == "b"


def test_route_call_uses_default_when_instance_omitted():
    reg = FakeRegistry()
    content = mcp_mod._route_call(reg, "list_modules", {})
    payload = json.loads(content[0].text)
    assert payload["modules"][0]["type"] == "a"


def test_route_call_list_instances_meta_tool():
    reg = FakeRegistry()
    content = mcp_mod._route_call(reg, mcp_mod.LIST_INSTANCES, {})
    payload = json.loads(content[0].text)
    assert [i["instance"] for i in payload["instances"]] == ["a", "b"]


def test_route_call_unknown_instance_is_structured_error():
    reg = FakeRegistry()
    content = mcp_mod._route_call(reg, "list_modules", {"instance": "zzz"})
    payload = json.loads(content[0].text)
    assert payload["error"]["type"] == "ValueError"
    assert payload["error"]["instance"] == "zzz"


def test_build_server_accepts_registry():
    server = mcp_mod.build_server(FakeRegistry())
    assert server.name == "pyfsr"
    handler_names = {req.__name__ for req in server.request_handlers}
    assert {"ListToolsRequest", "CallToolRequest"} <= handler_names
