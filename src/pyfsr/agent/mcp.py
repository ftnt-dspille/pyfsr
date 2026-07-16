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

**Multi-instance.** With a ``~/.pyfsr/instances.toml`` (or ``$PYFSR_INSTANCES``)
the server targets several appliances at once: every tool gains an optional
``instance`` argument (e.g. ``instance="206"``) and a ``list_instances`` meta-tool
lists the configured aliases. See :class:`pyfsr.instances.InstanceRegistry` for the
config shape. Without that file it falls back to a single instance from the
environment, exactly as before:

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
from ..instances import InstanceRegistry
from .tools import dispatch, tool_schemas

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.lowlevel import Server

SERVER_NAME = "pyfsr"

#: Meta-tool (no client needed) that lists the configured FortiSOAR instances.
LIST_INSTANCES = "list_instances"

#: Cap on a single tool result's serialized size. A raw connector/record dump can
#: be tens of KB — enough to blow an agent's context in one call — so a larger
#: result is replaced by a valid-JSON envelope that keeps a head preview and tells
#: the agent to narrow its query. Small results (the common case) pass through
#: verbatim and stay directly parseable.
MAX_TOOL_OUTPUT_CHARS = 4000


def _cap_output(text: str) -> str:
    """Return ``text`` unchanged, or a JSON truncation envelope if it's oversized.

    The envelope is itself valid JSON (``truncated``/``total_chars``/``preview``)
    so a consuming agent can still parse the result and act on the hint rather
    than choke on a multi-KB blob or a blindly-sliced, invalid-JSON fragment.
    """
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text

    def envelope(preview: str) -> str:
        return json.dumps(
            {
                "truncated": True,
                "total_chars": len(text),
                "shown_chars": len(preview),
                "note": (
                    "Result exceeded the MCP output cap. Narrow the query — add filters, "
                    "request fewer fields, or lower the limit — to retrieve it in full."
                ),
                "preview": preview,
            },
            indent=2,
        )

    # Start with headroom for the wrapper, then shrink until the *serialized*
    # envelope fits — JSON-escaping the preview (quotes/backslashes) can inflate
    # it non-linearly, so a single fixed slice isn't enough.
    budget = MAX_TOOL_OUTPUT_CHARS - 320
    out = envelope(text[:budget])
    while len(out) > MAX_TOOL_OUTPUT_CHARS and budget > 0:
        budget -= len(out) - MAX_TOOL_OUTPUT_CHARS + 16
        out = envelope(text[: max(budget, 0)])
    return out


def client_from_env(env: dict[str, str] | None = None) -> FortiSOAR:
    """Build a :class:`~pyfsr.client.FortiSOAR` client from ``FSR_*`` env vars.

    Thin wrapper over :meth:`pyfsr.config.EnvConfig.from_env` →
    :meth:`~pyfsr.config.EnvConfig.client`; see :mod:`pyfsr.config` for the full
    list of recognized variables. Raises ``ValueError`` when host/auth is missing.
    """
    return EnvConfig.from_env(env).client()


class _SingleClientRegistry:
    """Adapter presenting one already-built client as an :class:`InstanceRegistry`.

    Keeps :func:`build_server` / :func:`serve` back-compatible with callers that
    pass a bare :class:`~pyfsr.client.FortiSOAR` (and the single-box tests), while
    the multi-instance path uses a real :class:`InstanceRegistry`.
    """

    def __init__(self, client: Any, name: str = "default") -> None:
        self._client = client
        self.default = name

    def names(self) -> list[str]:
        return [self.default]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "instance": self.default,
                "base_url": getattr(self._client, "base_url", None),
                "default": True,
            }
        ]

    def client(self, alias: str | None = None) -> Any:
        return self._client


def _as_registry(target: Any) -> Any:
    """Coerce a client-or-registry into something with ``.client()``/``.describe()``."""
    if isinstance(target, (InstanceRegistry, _SingleClientRegistry)):
        return target
    return _SingleClientRegistry(target)


def _instance_property(registry: Any) -> dict[str, Any]:
    """JSON-Schema fragment for the optional ``instance`` argument on every tool."""
    names = registry.names()
    default = getattr(registry, "default", None)
    hint = f" Default: {default!r}." if default else " No default configured; specify one."
    prop: dict[str, Any] = {
        "type": "string",
        "description": f"Which configured FortiSOAR instance to target.{hint}",
    }
    if names:
        prop["enum"] = names
    return prop


def _mcp_tools(registry: Any | None = None) -> list[Any]:
    """Render the tool registry as MCP ``Tool`` objects.

    When ``registry`` is given, every tool gains an optional ``instance`` argument
    (enumerated over the configured instances) and a ``list_instances`` meta-tool
    is appended so an agent can discover the available targets.
    """
    import mcp.types as types

    instance_prop = _instance_property(registry) if registry is not None else None
    tools: list[Any] = []
    for t in tool_schemas():
        schema = t["input_schema"]
        if instance_prop is not None:
            schema = dict(schema)
            props = dict(schema.get("properties") or {})
            props["instance"] = instance_prop
            schema["properties"] = props
        tools.append(types.Tool(name=t["name"], description=t["description"], inputSchema=schema))
    if registry is not None:
        tools.append(
            types.Tool(
                name=LIST_INSTANCES,
                description=(
                    "List the FortiSOAR instances this server can target (alias, base_url, "
                    "which is default). Pass the chosen alias as the 'instance' argument of any "
                    "other tool."
                ),
                inputSchema={"type": "object", "properties": {}},
            )
        )
    return tools


def _text_content(obj: Any) -> list[Any]:
    """Wrap a JSON-able object as size-capped MCP text content."""
    import mcp.types as types

    text = _cap_output(json.dumps(obj, indent=2, default=str))
    return [types.TextContent(type="text", text=text)]


def _call(client: FortiSOAR, name: str, arguments: dict[str, Any] | None) -> list[Any]:
    """Dispatch one tool call against ``client`` and wrap the JSON result."""
    return _text_content(dispatch(client, name, arguments or {}))


def _route_call(registry: Any, name: str, arguments: dict[str, Any] | None) -> list[Any]:
    """Resolve the ``instance`` argument to a client, then dispatch ``name``.

    ``list_instances`` is handled here (no client needed). An unknown/unreachable
    instance is returned as a structured ``{"error": {...}}`` — never raised — so a
    bad target can't crash the session.
    """
    if name == LIST_INSTANCES:
        return _text_content({"instances": registry.describe()})

    args = dict(arguments or {})
    instance = args.pop("instance", None)
    try:
        client = registry.client(instance)
    except Exception as exc:  # noqa: BLE001 - surface bad target as data, not a crash
        return _text_content({"error": {"type": type(exc).__name__, "message": str(exc), "instance": instance}})
    return _call(client, name, args)


def build_server(target: Any) -> Server:
    """Create an MCP :class:`~mcp.server.lowlevel.Server` bound to ``target``.

    ``target`` is an :class:`~pyfsr.instances.InstanceRegistry` (multi-instance) or
    a single :class:`~pyfsr.client.FortiSOAR` (back-compat). Registers ``list_tools``
    and ``call_tool`` handlers; errors are surfaced as structured JSON content by
    :func:`pyfsr.agent.tools.dispatch`, so a tool call never crashes the session.
    """
    from mcp.server.lowlevel import Server

    registry = _as_registry(target)
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return _mcp_tools(registry)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        return _route_call(registry, name, arguments)

    return server


async def serve(registry: InstanceRegistry | None = None, *, client: FortiSOAR | None = None) -> None:
    """Run the MCP server over stdio until the client disconnects.

    With no arguments, loads an :class:`~pyfsr.instances.InstanceRegistry` from
    ``$PYFSR_INSTANCES``/``~/.pyfsr/instances.toml`` (multi-instance), falling back
    to a single instance from the ``FSR_*`` environment. Pass ``client=`` to bind
    one pre-built client (back-compat).
    """
    from mcp.server.stdio import stdio_server

    if registry is None:
        registry = InstanceRegistry.load() if client is None else None
    target: Any = registry if registry is not None else client
    server = build_server(target)
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    """Console entry point: ``python -m pyfsr.agent.mcp`` / ``pyfsr-mcp``."""
    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m pyfsr.agent.mcp`
    main()
