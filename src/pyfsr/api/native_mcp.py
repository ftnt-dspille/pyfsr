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
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

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
        _require_mcp_sdk()
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        auth = self.client.auth
        url = self._url_for(server)
        verify = self.client.verify_ssl

        async def _attempt() -> Any:
            headers = auth.get_auth_headers()
            async with streamablehttp_client(
                url,
                headers=headers,
                httpx_client_factory=lambda **kw: httpx.AsyncClient(verify=verify, **kw),
            ) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await coro_factory(session)

        try:
            return await _attempt()
        except Exception as exc:  # noqa: BLE001 - inspect for an auth failure to retry
            if _looks_like_auth_error(exc) and getattr(auth, "can_refresh", False):
                auth.refresh()
                return await _attempt()
            raise

    def list_tools(self, server: str = "soc") -> list[dict[str, Any]]:
        """Return ``[{"name", "description", "input_schema"}, ...]`` for ``server``.

        ``server`` is one of the fixed paths (``"modules"``, ``"playbooks"``,
        ``"soc"``, ``"utility"``) or ``"connector:<name>"`` for an installed
        connector's auto-generated server.
        """

        async def _list(session: Any) -> list[dict[str, Any]]:
            result = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                }
                for t in result.tools
            ]

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
            text = "".join(getattr(block, "text", "") for block in result.content)
            if not text:
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text

        return asyncio.run(self._run(server, _call))


def _looks_like_auth_error(exc: Exception) -> bool:
    """Best-effort sniff for a 401/403 surfaced through the MCP/httpx stack.

    The MCP SDK and httpx don't share one exception type for this across
    transports, so this matches on status-code attributes/text rather than a
    single class — same fail-open posture as the rest of pyfsr's auth-retry
    paths (skip the retry, not crash the caller, if unsure).
    """
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return True
    return any(code in str(exc) for code in ("401", "403"))
