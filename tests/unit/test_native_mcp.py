"""Unit tests for client.mcp — the native FortiSOAR MCP gateway client."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import pytest

from pyfsr.api.native_mcp import MCPSession, NativeMCPApi, _server_path

try:
    ExceptionGroup  # noqa: B018 - builtin on 3.11+
except NameError:  # pragma: no cover - exercised only on Python 3.10
    from exceptiongroup import ExceptionGroup  # type: ignore[no-redef]


# -- server-path mapping ------------------------------------------------------
@pytest.mark.parametrize(
    "server,expected",
    [
        ("soc", "soc"),
        ("/soc/", "soc"),
        ("modules", "modules"),
        ("connector:fortisiem", "connector/fortisiem"),
        ("connector:/fortisiem/", "connector/fortisiem"),
    ],
)
def test_server_path_mapping(server, expected):
    assert _server_path(server) == expected


@pytest.mark.parametrize("server", ["", "connector:"])
def test_server_path_rejects_empty(server):
    with pytest.raises(ValueError):
        _server_path(server)


# -- fakes ---------------------------------------------------------------------
class FakeAuth:
    def __init__(self, can_refresh=False):
        self.headers_calls = 0
        self.refresh_calls = 0
        self.can_refresh = can_refresh

    def get_auth_headers(self):
        self.headers_calls += 1
        return {"Authorization": "Bearer tok"}

    def refresh(self):
        self.refresh_calls += 1
        return self.get_auth_headers()


class FakeClient:
    def __init__(self, auth=None):
        self.base_url = "https://soar.example.com:13000"
        self.verify_ssl = False
        self.auth = auth or FakeAuth()


class FakeTool:
    def __init__(self, name, description="", input_schema=None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object"}


class FakeToolsResult:
    def __init__(self, tools):
        self.tools = tools


class FakeContentBlock:
    def __init__(self, text):
        self.text = text


class FakeCallResult:
    def __init__(self, text):
        self.content = [FakeContentBlock(text)] if text is not None else []


class FakeSession:
    """Stands in for ``mcp.ClientSession`` — also its own async context manager."""

    def __init__(self, tools=None, call_result_text='{"ok": true}', raise_on=None):
        self._tools = tools or [FakeTool("get_alert", "desc")]
        self._call_result_text = call_result_text
        self._raise_on = raise_on or {}
        self.initialized = False
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        if "list_tools" in self._raise_on:
            raise self._raise_on["list_tools"]
        return FakeToolsResult(self._tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if "call_tool" in self._raise_on:
            raise self._raise_on["call_tool"]
        return FakeCallResult(self._call_result_text)


def _fake_transport_factory(session_factory):
    """Build a fake ``streamablehttp_client`` returning ``session_factory()``'s session."""

    @asynccontextmanager
    async def _factory(url, headers=None, httpx_client_factory=None):
        yield (object(), object(), None)

    return _factory


def _patch_mcp(monkeypatch, session):
    """Patch the two lazily-imported symbols native_mcp._run reaches for."""
    import mcp
    import mcp.client.streamable_http as streamable_http

    monkeypatch.setattr(mcp, "ClientSession", lambda read, write: session)
    monkeypatch.setattr(streamable_http, "streamablehttp_client", _fake_transport_factory(None))


# -- list_tools / call_tool happy paths ----------------------------------------
def test_list_tools_returns_name_description_schema(monkeypatch):
    session = FakeSession(tools=[FakeTool("get_alert", "fetch an alert", {"type": "object"})])
    _patch_mcp(monkeypatch, session)
    api = NativeMCPApi(FakeClient())

    result = api.list_tools("soc")

    assert result == [{"name": "get_alert", "description": "fetch an alert", "input_schema": {"type": "object"}}]
    assert session.initialized


def test_call_tool_parses_json_result(monkeypatch):
    session = FakeSession(call_result_text='{"status": "success", "result": {"a": 1}}')
    _patch_mcp(monkeypatch, session)
    api = NativeMCPApi(FakeClient())

    result = api.call_tool("soc", "get_alert", {"uuid": ["x"]})

    assert result == {"status": "success", "result": {"a": 1}}
    assert session.calls == [("get_alert", {"uuid": ["x"]})]


def test_call_tool_falls_back_to_raw_text_on_bad_json(monkeypatch):
    session = FakeSession(call_result_text="not json")
    _patch_mcp(monkeypatch, session)
    api = NativeMCPApi(FakeClient())

    assert api.call_tool("soc", "get_alert") == "not json"


def test_call_tool_returns_none_on_empty_content(monkeypatch):
    session = FakeSession(call_result_text=None)
    _patch_mcp(monkeypatch, session)
    api = NativeMCPApi(FakeClient())

    assert api.call_tool("soc", "get_alert") is None


def test_call_tool_defaults_arguments_to_empty_dict(monkeypatch):
    session = FakeSession()
    _patch_mcp(monkeypatch, session)
    api = NativeMCPApi(FakeClient())

    api.call_tool("soc", "get_current_datetime")

    assert session.calls == [("get_current_datetime", {})]


# -- reauth-on-401/403 ----------------------------------------------------------
class _AuthError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_retries_once_on_auth_error_when_auth_can_refresh(monkeypatch):
    """First session raises 401; a fresh session (post-refresh) succeeds."""
    good_session = FakeSession(call_result_text='{"ok": true}')
    bad_session = FakeSession(raise_on={"call_tool": _AuthError(401)})

    sessions = iter([bad_session, good_session])
    import mcp
    import mcp.client.streamable_http as streamable_http

    monkeypatch.setattr(mcp, "ClientSession", lambda read, write: next(sessions))
    monkeypatch.setattr(streamable_http, "streamablehttp_client", _fake_transport_factory(None))

    auth = FakeAuth(can_refresh=True)
    api = NativeMCPApi(FakeClient(auth=auth))

    result = api.call_tool("soc", "get_alert")

    assert result == {"ok": True}
    assert auth.refresh_calls == 1


def test_retries_on_exceptiongroup_wrapped_httpx_status_error(monkeypatch):
    """The real shape observed live: anyio wraps the actual httpx.HTTPStatusError
    (which carries the 401) in an ExceptionGroup whose own str() says nothing
    about a status code at all — a naive top-level/string check misses it."""

    class FakeResponse:
        status_code = 401

    class FakeHTTPStatusError(Exception):
        def __init__(self):
            super().__init__("Client error '401 Unauthorized' for url '...'")
            self.response = FakeResponse()

    wrapped = ExceptionGroup("unhandled errors in a TaskGroup", [FakeHTTPStatusError()])
    good_session = FakeSession(call_result_text='{"ok": true}')
    bad_session = FakeSession(raise_on={"call_tool": wrapped})

    sessions = iter([bad_session, good_session])
    import mcp
    import mcp.client.streamable_http as streamable_http

    monkeypatch.setattr(mcp, "ClientSession", lambda read, write: next(sessions))
    monkeypatch.setattr(streamable_http, "streamablehttp_client", _fake_transport_factory(None))

    auth = FakeAuth(can_refresh=True)
    api = NativeMCPApi(FakeClient(auth=auth))

    result = api.call_tool("soc", "get_alert")

    assert result == {"ok": True}
    assert auth.refresh_calls == 1


def test_does_not_retry_when_auth_cannot_refresh(monkeypatch):
    session = FakeSession(raise_on={"call_tool": _AuthError(401)})
    _patch_mcp(monkeypatch, session)
    auth = FakeAuth(can_refresh=False)
    api = NativeMCPApi(FakeClient(auth=auth))

    with pytest.raises(_AuthError):
        api.call_tool("soc", "get_alert")

    assert auth.refresh_calls == 0


def test_non_auth_error_propagates_without_retry(monkeypatch):
    session = FakeSession(raise_on={"call_tool": RuntimeError("boom")})
    _patch_mcp(monkeypatch, session)
    auth = FakeAuth(can_refresh=True)
    api = NativeMCPApi(FakeClient(auth=auth))

    with pytest.raises(RuntimeError, match="boom"):
        api.call_tool("soc", "get_alert")

    assert auth.refresh_calls == 0


# -- missing optional dependency -----------------------------------------------
def test_missing_mcp_dependency_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "mcp", None)
    api = NativeMCPApi(FakeClient())

    with pytest.raises(ImportError, match=r"pyfsr\[mcp\]"):
        api.list_tools("soc")


# -- MCPSession (batched calls, one handshake) ---------------------------------
def test_session_reuses_one_handshake_across_calls(monkeypatch):
    session_obj = FakeSession(
        tools=[FakeTool("get_alert")],
        call_result_text='{"ok": true}',
    )
    transport_calls = {"n": 0}
    import mcp
    import mcp.client.streamable_http as streamable_http

    @asynccontextmanager
    async def counting_transport(url, headers=None, httpx_client_factory=None):
        transport_calls["n"] += 1
        yield (object(), object(), None)

    monkeypatch.setattr(mcp, "ClientSession", lambda read, write: session_obj)
    monkeypatch.setattr(streamable_http, "streamablehttp_client", counting_transport)

    api = NativeMCPApi(FakeClient())
    with api.session("soc") as s:
        assert isinstance(s, MCPSession)
        names = [t["name"] for t in s.list_tools()]
        result = s.call_tool("get_alert", {"uuid": ["x"]})
        result2 = s.call_tool("get_alert", {"uuid": ["y"]})

    assert names == ["get_alert"]
    assert result == {"ok": True}
    assert result2 == {"ok": True}
    assert session_obj.calls == [("get_alert", {"uuid": ["x"]}), ("get_alert", {"uuid": ["y"]})]
    # One handshake for 3 calls (list_tools + 2x call_tool), not 3.
    assert transport_calls["n"] == 1
    assert session_obj.initialized


def test_session_closes_transport_and_session_on_exit(monkeypatch):
    closed = {"session": False, "transport": False}

    class TrackedFakeSession(FakeSession):
        async def __aexit__(self, *exc):
            closed["session"] = True
            return False

    @asynccontextmanager
    async def tracked_transport(url, headers=None, httpx_client_factory=None):
        try:
            yield (object(), object(), None)
        finally:
            closed["transport"] = True

    import mcp
    import mcp.client.streamable_http as streamable_http

    session_obj = TrackedFakeSession()
    monkeypatch.setattr(mcp, "ClientSession", lambda read, write: session_obj)
    monkeypatch.setattr(streamable_http, "streamablehttp_client", tracked_transport)

    api = NativeMCPApi(FakeClient())
    with api.session("soc"):
        pass

    assert closed == {"session": True, "transport": True}
