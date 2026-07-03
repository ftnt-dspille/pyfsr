"""Generic Model Context Protocol server over the pyfsr tool registry.

Point an MCP-capable agent at *any* FortiSOAR with one command::

    FSR_BASE_URL=soar.example.com FSR_API_KEY=... python -m pyfsr.agent.mcp

This is a deliberately thin, generic consumer of :mod:`pyfsr.agent.tools`: it exposes
the same 16 core operations (record CRUD, schema discovery, picklists,
connectors, playbook runs) as MCP tools, listing them from
:func:`pyfsr.agent.tools.tool_schemas` and executing them through
:func:`pyfsr.agent.tools.dispatch`. It is intentionally distinct from fsrpb's
authoring/domain MCP and the connector's on-platform MCP — no YAML compiler, no
agent UX, just raw FortiSOAR access.

Requires the optional dependency: ``pip install 'pyfsr[mcp]'``.

Configuration is read from the environment (see :func:`client_from_env`):

- ``FSR_BASE_URL`` — appliance host or URL (required; ``FSR_HOST`` also accepted).
- ``FSR_API_KEY`` — API-key auth, or use ``FSR_USERNAME`` + ``FSR_PASSWORD``.
- ``FSR_PORT`` — optional port override.
- ``FSR_VERIFY_SSL`` — ``false``/``0``/``no`` to disable TLS verification.
- ``FSR_SUPPRESS_INSECURE_WARNINGS`` — silence urllib3 warnings when SSL is off.

The ``appliance_*`` tools reach the box over SSH (not the REST API), so they
read a separate set of vars via :func:`pyfsr.cli.appliance.transport.transport_from_env`:
``PYFSR_APPLIANCE_HOST``/``USER``/``PASSWORD``/``PORT``/``KEY_PATH``/
``SUDO_PASSWORD``/``INSECURE_SKIP_HOST_KEY_CHECK``. On-box (``/opt/cyops``
present) the local transport is used and only the sudo password is needed.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from ..client import FortiSOAR
from ..config import EnvConfig
from .tools import dispatch, tool_schemas

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.lowlevel import Server

SERVER_NAME = "pyfsr"


def client_from_env(env: dict[str, str] | None = None) -> FortiSOAR:
    """Build a :class:`~pyfsr.client.FortiSOAR` client from ``FSR_*`` env vars.

    Thin wrapper over :meth:`pyfsr.config.EnvConfig.from_env` →
    :meth:`~pyfsr.config.EnvConfig.client`; see :mod:`pyfsr.config` for the full
    list of recognized variables. Raises ``ValueError`` when host/auth is missing.
    """
    return EnvConfig.from_env(env).client()


def _mcp_tools() -> list[Any]:
    """Render the tool registry as MCP ``Tool`` objects."""
    import mcp.types as types

    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["input_schema"],
        )
        for t in tool_schemas()
    ]


def _call(client: FortiSOAR, name: str, arguments: dict[str, Any] | None) -> list[Any]:
    """Dispatch one tool call and wrap the JSON result as MCP text content."""
    import mcp.types as types

    result = dispatch(client, name, arguments or {})
    text = json.dumps(result, indent=2, default=str)
    return [types.TextContent(type="text", text=text)]


def build_server(client: FortiSOAR) -> Server:
    """Create an MCP :class:`~mcp.server.lowlevel.Server` bound to ``client``.

    Registers ``list_tools`` (from the registry) and ``call_tool`` (through
    :func:`pyfsr.agent.tools.dispatch`) handlers. Errors are surfaced as structured
    JSON content by ``dispatch``, so a tool call never crashes the session.
    """
    from mcp.server.lowlevel import Server

    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return _mcp_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        return _call(client, name, arguments)

    return server


async def serve(client: FortiSOAR | None = None) -> None:
    """Run the MCP server over stdio until the client disconnects."""
    from mcp.server.stdio import stdio_server

    client = client or client_from_env()
    server = build_server(client)
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    """Console entry point: ``python -m pyfsr.agent.mcp``."""
    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m pyfsr.agent.mcp`
    main()
