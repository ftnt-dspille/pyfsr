"""Call FortiSOAR's own native MCP gateway (``client.mcp``) — all pyfsr.

FortiSOAR 8.0 ships an on-appliance ``mcp-server`` service (nginx-fronted at
``/mcp/*``, real Streamable-HTTP MCP transport) that exposes the appliance's own
tools *as an MCP server*. Unlike an external server you *register* with
``client.ai`` (see ``register_and_call_public_mcp_server.py`` /
``fortisiem_mcp_setup_and_test.py``, which can only be exercised inside an agent
investigation), the native gateway is **directly callable** — you drive it with
your own FortiSOAR credential and every tool runs under your RBAC. This is the
example the ``pyfsr.api.native_mcp`` module docstring promises ("see examples/
for that surface once written").

Four fixed servers are always present:

  modules    -- query_records, get_modules, get_module_schema
  playbooks  -- get_available_playbooks, execute_playbook, get_playbook_execution_result
  soc        -- get_alert, get_indicators, get_asset, enrich_indicator,
                block_indicator, hunt_ioc_siem, update_alert_ai_analysis, ...
  utility    -- get_current_datetime, global_search

...plus one auto-generated ``connector:<name>`` server per connector the
appliance exposes for MCP (feature-gated — see ``tour_connector`` below).

What this shows:
  * ``client.supports_native_mcp()`` — is the gateway present at all
  * ``client.mcp.list_tools(server)`` — returns typed :class:`~pyfsr.models.MCPTool`
    objects (dict-compatible: ``t["name"]`` / ``t.get("input_schema")`` still work)
  * ``client.mcp.call_tool(server, name, args)`` — raw decoded payload
  * ``client.mcp.call_tool_result(server, name, args)`` — the same call wrapped
    in a typed :class:`~pyfsr.models.MCPToolResult` (``r.ok`` / ``r.result`` / ``r.error``)
  * ``client.mcp.session(server)`` — one handshake for a batch of calls

Argument convention (live-verified on 8.0): most native tools take their inputs
under a ``params`` (or ``values``) wrapper whose inner shape is the tool's own
schema — inspect ``tool.input_schema`` to see it. Read tools that take no input
(``get_modules``, ``get_available_playbooks``, ``get_current_datetime``) accept
an empty ``{}``.

Everything here is read-only except the optional ``enrich_indicator`` demo,
which triggers the enrichment playbook against a public IP (8.8.8.8).

Requires: ``pip install 'pyfsr[mcp]'``.

Configure via env — anything :meth:`pyfsr.config.EnvConfig.from_env` reads:
  FSR_BASE_URL (or FSR_HOST), FSR_USERNAME + FSR_PASSWORD (or FSR_API_KEY),
  FSR_PORT, FSR_VERIFY_SSL, FSR_SUPPRESS_INSECURE_WARNINGS.

Usage:
  python examples/native_mcp_gateway.py                 # read-only tour
  python examples/native_mcp_gateway.py --enrich 8.8.8.8 # + trigger enrichment
  python examples/native_mcp_gateway.py --connector virustotal
"""

from __future__ import annotations

import argparse

from pyfsr import FortiSOAR
from pyfsr.config import EnvConfig


def connect() -> FortiSOAR:
    return EnvConfig.from_env().client()


def _names(tools) -> list[str]:
    # tools are typed MCPTool, but stay dict-compatible on purpose
    return [t["name"] for t in tools]


def tour_list_tools(client: FortiSOAR) -> None:
    print("== native servers and their tools ==")
    for server in ("modules", "playbooks", "soc", "utility"):
        tools = client.mcp.list_tools(server)
        print(f"  {server:10s} ({len(tools):2d}): {', '.join(_names(tools))}")


def tour_read_calls(client: FortiSOAR) -> None:
    print("\n== read-only tool calls ==")

    # no-input tool → empty args
    now = client.mcp.call_tool("utility", "get_current_datetime", {})
    print("  utility.get_current_datetime ->", now.get("result") if hasattr(now, "get") else now)

    # typed result: MCPToolResult with .ok / .result
    mods = client.mcp.call_tool_result("modules", "get_modules", {})
    module_names = list((mods.result or {}).keys())[:8]
    print(f"  modules.get_modules -> ok={mods.ok}, {len(module_names)}+ modules e.g. {module_names}")

    pbs = client.mcp.call_tool_result("playbooks", "get_available_playbooks", {})
    titles = [p.get("title") for p in (pbs.result or [])][:5]
    print(f"  playbooks.get_available_playbooks -> ok={pbs.ok}, e.g. {titles}")


def tour_inspect_schema(client: FortiSOAR) -> None:
    print("\n== inspect a tool's input schema (drives the args wrapper) ==")
    for t in client.mcp.list_tools("soc"):
        if t["name"] == "enrich_indicator":
            print("  soc.enrich_indicator input_schema:", t.input_schema)
            break


def tour_enrich(client: FortiSOAR, indicator: str) -> None:
    print(f"\n== soc.enrich_indicator (triggers a playbook) for {indicator} ==")
    r = client.mcp.call_tool_result("soc", "enrich_indicator", {"params": {"value": indicator}})
    if r.ok:
        inner = r.result.get("result", r.result) if isinstance(r.result, dict) else r.result
        print("  ok — task_id:", (inner or {}).get("task_id"), "| reputation:", (inner or {}).get("reputation"))
    else:
        print("  not ok:", r.error or r.result)


def tour_session(client: FortiSOAR) -> None:
    print("\n== batched calls over one handshake (client.mcp.session) ==")
    with client.mcp.session("utility") as s:
        print("  tools:", _names(s.list_tools()))
        print("  get_current_datetime:", s.call_tool_result("get_current_datetime", {}).result)


def tour_connector(client: FortiSOAR, name: str) -> None:
    print(f"\n== connector:{name} (feature-gated) ==")
    try:
        tools = client.mcp.list_tools(f"connector:{name}")
        print(f"  ({len(tools)}): {', '.join(_names(tools))}")
    except Exception as exc:  # noqa: BLE001 - example: show the shape of the gate
        # A connector the appliance doesn't expose as an MCP server answers 404
        # "Connector not found"; that surfaces as McpError("Session terminated")
        # during initialize. Use the connector's *install name*, not its label.
        print(f"  not available as an MCP server: {type(exc).__name__}: {str(exc)[:80]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--enrich", metavar="INDICATOR", help="also trigger soc.enrich_indicator for this value")
    ap.add_argument("--connector", metavar="NAME", help="also probe connector:<install-name>'s MCP server")
    args = ap.parse_args()

    client = connect()

    supported = client.supports_native_mcp()
    print(f"supports_native_mcp(): {supported}")
    if supported is False:
        print("This appliance does not expose the native /mcp/ gateway (needs FortiSOAR 8.0+).")
        return

    tour_list_tools(client)
    tour_read_calls(client)
    tour_inspect_schema(client)
    tour_session(client)
    if args.enrich:
        tour_enrich(client, args.enrich)
    if args.connector:
        tour_connector(client, args.connector)


if __name__ == "__main__":
    main()
