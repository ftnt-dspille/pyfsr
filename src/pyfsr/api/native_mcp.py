"""Client for the appliance's own native MCP tool gateway (FortiSOAR 8.0+).

FortiSOAR 8.0 ships an on-appliance ``mcp-server`` service (nginx-fronted at
``/mcp/*``, real Streamable-HTTP MCP transport) that auto-exposes FortiSOAR's
own modules/playbooks/SOC tools *and* every installed connector as MCP tools:

- ``/mcp/modules/`` — record CRUD (``fetch_record``, ``get_alert``, ...)
- ``/mcp/playbooks/`` — ``list_playbooks``, ``trigger_playbook``, ...
- ``/mcp/soc/`` — bundled SOC-investigator tools (``get_alert``,
  ``enrich_indicator``, ``block_indicator``, ``hunt_ioc_siem``, ...)
- ``/mcp/utility/`` — ``get_current_datetime``, ...
- ``/mcp/connector/NAME/`` — one auto-generated server per *installed* connector

This is the thing an agentic AI stack (fsr-ai, or your own agent) calls into —
**not** the same as:

- :class:`pyfsr.api.ai.AIApi` (``client.ai``) — manages *external* MCP
  servers (e.g. FortiSIEM's own MCP) that FortiSOAR's agents are allowed to
  call. That's registration/allowlisting; this module is the appliance
  answering as an MCP server itself.
- :mod:`pyfsr.agent.mcp` — the reverse direction: makes *pyfsr* act as an MCP
  server, exposing its own CRUD/schema tool registry to an external agent.

Auth passes through the caller's own FortiSOAR credential: the gateway
validates the ``Authorization`` header against ``/api/3`` and every tool call
then runs with that identity's real RBAC — no service account, no separate
credential to manage. This module reuses the client's ``auth`` object for
that header, including its 401/403 refresh-and-retry behavior (see
:meth:`pyfsr.auth.base.BaseAuth.refresh`) so a long-lived client survives a
stale session the same way its REST calls already do.

Requires the optional dependency: ``pip install 'pyfsr[mcp]'``.

Accessed as ``client.mcp``.

Example:
    >>> client = FortiSOAR("soar.example.com", token=api_key)
    >>> [t["name"] for t in client.mcp.list_tools("soc")]
    ['get_alert', 'enrich_indicator', 'block_indicator', ...]
    >>> client.mcp.call_tool("soc", "get_alert", {"uuid": ["<alert-uuid>"]})
    {'status': 'success', 'result': {...}}

Calling a connector's own auto-generated server (e.g. a SIEM connector
installed on the appliance) uses the same ``server`` argument with a
``connector:`` prefix:

    >>> client.mcp.list_tools("connector:fortisiem")

Note the per-connector servers are feature-gated on the appliance: a connector
that isn't exposed as an MCP server answers ``/mcp/connector/<name>/`` with
``404 "Connector not found"``, which surfaces here as an ``McpError("Session
terminated")`` during ``initialize`` (the stream closes before the handshake
completes). The four fixed servers (``modules``, ``playbooks``, ``soc``,
``utility``) are always present. ``<name>`` is the connector's install name
(e.g. ``virustotal``), not its display label.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from typing import Any

from ..models._ai import MCPTool, MCPToolResult
from .base import BaseAPI

_CONNECTOR_PREFIX = "connector:"


def _require_mcp_sdk() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised via missing-dep test
        raise ImportError(
            "client.mcp requires the optional 'mcp' dependency. Install it with: pip install 'pyfsr[mcp]'"
        ) from exc


def _server_path(server: str) -> str:
    """Map a friendly ``server`` name to its ``/mcp/<path>/`` segment."""
    server = server.strip("/")
    if server.startswith(_CONNECTOR_PREFIX):
        name = server[len(_CONNECTOR_PREFIX) :].strip("/")
        if not name:
            raise ValueError("connector server name is empty (expected 'connector:<name>')")
        return f"connector/{name}"
    if not server:
        raise ValueError("server must be a non-empty path, e.g. 'soc' or 'connector:fortisiem'")
    return server


def _tools_to_models(tools: Any) -> list[MCPTool]:
    return [MCPTool(name=t.name, description=t.description, inputSchema=t.inputSchema) for t in tools]


def _new_httpx_client(verify: Any, **kw: Any) -> Any:
    """Build the ``httpx.AsyncClient`` every MCP session uses.

    ``follow_redirects=True`` is required: some registered servers 307-redirect a
    trailing-slash path (e.g. the bridge answers ``/mcp/fortisiem/`` with a
    redirect to ``/mcp/fortisiem``); without it the MCP handshake dies with a bare
    ``ExceptionGroup`` wrapping the 307 (live-verified against a real server).
    """
    import httpx

    return httpx.AsyncClient(verify=verify, follow_redirects=True, **kw)


def _bearer_headers(auth: dict[str, Any]) -> dict[str, str]:
    prefix = auth.get("prefix", "Bearer")
    return {auth.get("header_name", "Authorization"): f"{prefix} {auth['value']}"}


def _basic_headers(auth: dict[str, Any]) -> dict[str, str]:
    import base64

    raw = f"{auth['username']}:{auth['password']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


_MCP_AUTH_HEADER_BUILDERS = {
    "BEARER": _bearer_headers,
    "FSR": _bearer_headers,
    "API_KEY": lambda a: {a["header_name"]: a["value"]},
    "BASIC": _basic_headers,
    "NONE": lambda a: {},
}


def build_mcp_auth_headers(auth: dict[str, Any] | None) -> dict[str, str]:
    """Map a registered MCP server's ``authentication`` dict to request headers.

    Mirrors FortiSOAR fsr-ai's own ``mcp_auth.py`` builders so a registered
    server is reached the same way the product's agent reaches it: ``BEARER``/
    ``FSR`` → ``Authorization: <prefix> <value>`` (``prefix``/``header_name``
    overridable), ``API_KEY`` → ``<header_name>: <value>``, ``BASIC`` →
    base64, ``NONE`` → no header.
    """
    auth = auth or {"type": "NONE"}
    builder = _MCP_AUTH_HEADER_BUILDERS.get((auth.get("type") or "NONE").upper())
    if builder is None:
        raise ValueError(f"unsupported MCP auth type: {auth.get('type')!r}")
    return builder(auth)


def _to_tool_result(payload: Any) -> MCPToolResult:
    """Wrap a decoded ``call_tool`` payload into :class:`~pyfsr.models.MCPToolResult`.

    A dict is validated as the envelope (keeping any extra keys); any other
    payload (bare string, list, ``None``) lands under ``result`` with
    ``status=None`` so the return type is stable.
    """
    if isinstance(payload, dict):
        return MCPToolResult.model_validate(payload)
    return MCPToolResult(result=payload)


def _content_to_result(content: Any) -> Any:
    text = "".join(getattr(block, "text", "") for block in content)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class MCPSession:
    """One open MCP session, for batching several calls without re-handshaking.

    ``NativeMCPApi.list_tools``/``call_tool`` each open a fresh MCP session
    (connect, ``initialize``, one request, disconnect) — simple and safe as a
    one-off, but a script calling several tools in a row pays a full
    handshake every time. Get one from :meth:`NativeMCPApi.session` instead::

        with client.mcp.session("soc") as s:
            alert = s.call_tool("get_alert", {"uuid": [alert_uuid]})
            s.call_tool("enrich_indicator", {"indicator": ip})

    Unlike the one-off calls, a session does **not** retry on a stale
    (401/403) auth token mid-batch — it authenticates once on entry. For a
    short-lived batch of calls right after opening (the intended use) that's
    not a real constraint; for a long-running session, catch the error and
    open a new one.

    Implementation note: the MCP SDK's session/transport use ``anyio`` cancel
    scopes internally, which must be entered *and exited in the same asyncio
    Task* — so ``__enter__``/each call/``__exit__`` can't each be their own
    ``loop.run_until_complete(...)`` (that puts every one in a fresh Task and
    anyio raises "Attempted to exit cancel scope in a different task than it
    was entered in", caught live against a real appliance 2026-07-05). A
    background thread runs one continuous coroutine that opens the
    connection, then pulls call requests off a queue until told to stop —
    the whole MCP session lives in that single task for its entire life;
    sync calls just hand off work to it and block for the answer.
    """

    def __init__(self, api: NativeMCPApi, server: str) -> None:
        self._api = api
        self._server = server
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Any = None
        self._queue: asyncio.Queue | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None

    def __enter__(self) -> MCPSession:
        _require_mcp_sdk()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="pyfsr-mcp-session", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise self._startup_error
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._loop is not None and self._queue is not None:
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop).result()
        if self._thread is not None:
            self._thread.join(timeout=30)
        if self._loop is not None:
            self._loop.close()
        self._loop = None

    def _run(self) -> None:
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._worker())
        except BaseException as exc:  # noqa: BLE001 - surfaced to __enter__ via _startup_error
            if not self._ready.is_set():
                self._startup_error = exc
                self._ready.set()

    async def _worker(self) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        client = self._api.client
        url = self._api._url_for(self._server)
        headers = client.auth.get_auth_headers()
        verify = client.verify_ssl

        async with streamablehttp_client(
            url,
            headers=headers,
            httpx_client_factory=lambda **kw: _new_httpx_client(verify, **kw),
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._queue = asyncio.Queue()
                self._ready.set()
                while True:
                    item = await self._queue.get()
                    if item is None:  # shutdown sentinel from __exit__
                        break
                    method, args, future = item
                    try:
                        if method == "list_tools":
                            result = await session.list_tools()
                            future.set_result(_tools_to_models(result.tools))
                        else:  # "call_tool"
                            name, arguments = args
                            result = await session.call_tool(name, arguments or {})
                            future.set_result(_content_to_result(result.content))
                    except Exception as exc:  # noqa: BLE001 - propagate to the caller's thread
                        future.set_exception(exc)

    def _dispatch(self, method: str, args: Any) -> Any:
        if self._loop is None or self._queue is None:
            raise RuntimeError("MCPSession is not open — use it inside a 'with' block")
        future: concurrent.futures.Future = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(self._queue.put((method, args, future)), self._loop)
        return future.result()

    def list_tools(self) -> list[MCPTool]:
        """Same shape as :meth:`NativeMCPApi.list_tools`, on the open session."""
        return self._dispatch("list_tools", None)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Same shape as :meth:`NativeMCPApi.call_tool`, on the open session."""
        return self._dispatch("call_tool", (name, arguments))

    def call_tool_result(self, name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        """Same as :meth:`NativeMCPApi.call_tool_result`, on the open session."""
        return _to_tool_result(self._dispatch("call_tool", (name, arguments)))


class NativeMCPApi(BaseAPI):
    """Call tools on FortiSOAR's own native MCP gateway (``/mcp/*``).

    See the module docstring for the full path table and how this differs
    from :class:`~pyfsr.api.ai.AIApi`'s MCP-server *registration* surface.
    """

    def _url_for(self, server: str) -> str:
        return f"{self.client.base_url.rstrip('/')}/mcp/{_server_path(server)}/"

    async def _run(self, server: str, coro_factory: Any) -> Any:
        """Open one MCP session against ``server`` and run ``coro_factory(session)``.

        Re-authenticates once (via :attr:`FortiSOAR.auth`'s refresh, if it
        supports one) on a 401/403 raised while opening the connection or
        during the call, mirroring the retry-on-stale-session behavior the
        client's own REST calls already get.
        """
        auth = self.client.auth
        url = self._url_for(server)
        verify = self.client.verify_ssl

        try:
            return await self._run_at(url, auth.get_auth_headers(), verify, coro_factory)
        except Exception as exc:  # noqa: BLE001 - inspect for an auth failure to retry
            if _looks_like_auth_error(exc) and getattr(auth, "can_refresh", False):
                auth.refresh()
                return await self._run_at(url, auth.get_auth_headers(), verify, coro_factory)
            raise

    async def _run_at(self, url: str, headers: dict[str, str], verify: Any, coro_factory: Any) -> Any:
        """Open one MCP session at an arbitrary ``url``/``headers`` and run ``coro_factory(session)``.

        The transport-level core shared by the native gateway (:meth:`_run`, which
        adds the client's auth + 401/403 refresh) and the *registered-server* path
        (:meth:`~pyfsr.api.ai.AIApi.call_registered_tool`, which supplies the
        server's own url + auth header). No auth-refresh here — a registered
        server owns its credential.
        """
        _require_mcp_sdk()
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            url,
            headers=headers,
            httpx_client_factory=lambda **kw: _new_httpx_client(verify, **kw),
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await coro_factory(session)

    def list_tools_at(self, url: str, headers: dict[str, str], *, verify: Any = None) -> list[MCPTool]:
        """List tools of *any* MCP server at ``url`` (auth already in ``headers``).

        Lower-level than :meth:`list_tools` (which targets the appliance's own
        ``/mcp/*`` gateway with the client's credential). Used by
        :meth:`~pyfsr.api.ai.AIApi.call_registered_tool` to reach a registered
        external server; ``verify`` defaults to the client's ``verify_ssl``.
        """

        async def _list(session: Any) -> list[MCPTool]:
            return _tools_to_models((await session.list_tools()).tools)

        v = self.client.verify_ssl if verify is None else verify
        return asyncio.run(self._run_at(url, headers, v, _list))

    def call_tool_at(
        self,
        url: str,
        headers: dict[str, str],
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        verify: Any = None,
    ) -> Any:
        """Call ``name`` on *any* MCP server at ``url`` (auth already in ``headers``).

        Lower-level companion to :meth:`call_tool`; see :meth:`list_tools_at`.
        Returns the raw decoded payload; use :meth:`call_tool_result` for a typed
        :class:`~pyfsr.models.MCPToolResult`.
        """

        async def _call(session: Any) -> Any:
            return _content_to_result((await session.call_tool(name, arguments or {})).content)

        v = self.client.verify_ssl if verify is None else verify
        return asyncio.run(self._run_at(url, headers, v, _call))

    def list_tools(self, server: str = "soc") -> list[MCPTool]:
        """Return ``[{"name", "description", "input_schema"}, ...]`` for ``server``.

        ``server`` is one of the fixed paths (``"modules"``, ``"playbooks"``,
        ``"soc"``, ``"utility"``) or ``"connector:<name>"`` for an installed
        connector's auto-generated server.
        """

        async def _list(session: Any) -> list[MCPTool]:
            result = await session.list_tools()
            return _tools_to_models(result.tools)

        return asyncio.run(self._run(server, _list))

    def call_tool(self, server: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call tool ``name`` on ``server`` and return its result.

        Each MCP content block is text (FortiSOAR's native tools return
        JSON-encoded text). Concatenates them and JSON-decodes the result;
        falls back to the raw string if it isn't valid JSON, and returns
        ``None`` for an empty response.
        """

        async def _call(session: Any) -> Any:
            result = await session.call_tool(name, arguments or {})
            return _content_to_result(result.content)

        return asyncio.run(self._run(server, _call))

    def call_tool_result(self, server: str, name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        """Like :meth:`call_tool`, but always return a typed :class:`~pyfsr.models.MCPToolResult`.

        Native tools reply with a ``{"status", "result", "error"}`` envelope on
        success; this wraps it into the model (``r.ok``, ``r.result``, ``r.error``,
        plus dict-style ``r["result"]``). An in-band failure that comes back as a
        bare string (or any non-dict payload) is wrapped as
        ``MCPToolResult(status=None, result=<raw>)`` so callers get one stable
        type regardless of outcome. Use :meth:`call_tool` when you want the raw
        decoded payload untouched.
        """
        return _to_tool_result(self.call_tool(server, name, arguments))

    def session(self, server: str = "soc") -> MCPSession:
        """Open a reusable :class:`MCPSession` against ``server`` for a batch
        of calls without re-handshaking per call::

            with client.mcp.session("soc") as s:
                s.list_tools()
                s.call_tool("get_alert", {"uuid": [alert_uuid]})
        """
        return MCPSession(self, server)


def _iter_leaf_exceptions(exc: BaseException) -> Any:
    """Yield ``exc`` and every exception nested under it — through
    ``ExceptionGroup.exceptions`` (anyio task groups wrap failures in one of
    these) and ``__cause__``/``__context__`` chains.

    Checks ``exceptions`` by attribute rather than ``isinstance(...,
    BaseExceptionGroup)`` — that type is 3.11+ only (PEP 654) and pyfsr
    supports 3.10, where ``anyio``'s task groups still raise the
    ``exceptiongroup`` backport's equivalent, which carries the same
    ``.exceptions`` tuple.
    """
    seen: set[int] = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        nested = getattr(current, "exceptions", None)
        if nested:
            stack.extend(nested)
        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None:
            stack.append(current.__context__)


def _looks_like_auth_error(exc: BaseException) -> bool:
    """Best-effort sniff for a 401/403 surfaced through the MCP/httpx stack.

    Live-verified 2026-07-05: a bad bearer token doesn't raise a flat
    exception with a ``status_code`` — anyio's task group wraps the real
    ``httpx.HTTPStatusError`` in an ``ExceptionGroup`` whose own message is
    just "unhandled errors in a TaskGroup (1 sub-exception)", which mentions
    no status code at all. A naive top-level check (or a string match on the
    group's own ``str()``) misses this entirely, silently skipping the
    retry. Walk every nested exception (group members + cause/context
    chains) and check each one — same fail-open posture as the rest of
    pyfsr's auth-retry paths (skip the retry, not crash the caller, on
    anything ambiguous).
    """
    for e in _iter_leaf_exceptions(exc):
        status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
        if status in (401, 403):
            return True
        if any(code in str(e) for code in ("401", "403")):
            return True
    return False
