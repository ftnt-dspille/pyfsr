"""Register a public MCP server with FortiSOAR, then call its tools — all pyfsr.

A minimal, fully public/no-credential companion to
``fortisiem_mcp_setup_and_test.py`` (which does the same thing against a real
FortiSIEM instance, but needs one to run). This uses `DeepWiki's public MCP
server <https://mcp.deepwiki.com/mcp>`_ (GitHub repo Q&A, streamable-HTTP, no
auth) so the whole loop is runnable against nothing but a FortiSOAR appliance
you already have:

  register  -- validate + register DeepWiki as an external MCP server in
               FortiSOAR (``client.ai.validate_mcp_server`` /
               ``client.ai.upsert_mcp_server``) — this is "adding an MCP
               server to SOAR."
  call      -- actually invoke a couple of its tools and print real results,
               proving the registration is a real, callable server and not
               just a config row FortiSOAR accepted.
  cleanup   -- remove the registered server (``client.ai.delete_mcp_server``).

How the ``call`` step reaches the server:
FortiSOAR itself exposes no REST endpoint that *runs* a registered external
server's tool — its fsr-ai calls them only inside an agent investigation (that's
why ``fortisiem_mcp_setup_and_test.py`` proves usage by attributing an
investigation's tool calls back to the server). So the ``call`` step here uses
``client.ai.call_registered_tool`` / ``list_registered_tools``, which resolve the
server's url + auth from its registration and do the MCP ``tools/call``
client-side — the same mechanism fsr-ai's agent uses, driven from your process.
DeepWiki takes no auth; a bearer server (e.g. a bridge) uses the stored token, or
pass ``token=...`` when it's write-only.

For an appliance's *own* native MCP gateway (``/mcp/soc/``, ``/mcp/modules/``,
one auto-generated server per installed connector, ...) use ``client.mcp``
(:class:`pyfsr.api.native_mcp.NativeMCPApi`) instead — see
``examples/native_mcp_gateway.py`` for that surface, or the module docstring.

Configure via env — anything :meth:`pyfsr.config.EnvConfig.from_env` reads:
  FSR_BASE_URL (or FSR_HOST), FSR_USERNAME + FSR_PASSWORD (or FSR_API_KEY),
  FSR_PORT, FSR_VERIFY_SSL, FSR_SUPPRESS_INSECURE_WARNINGS.

Usage:
  python examples/register_and_call_public_mcp_server.py register
  python examples/register_and_call_public_mcp_server.py call --repo modelcontextprotocol/python-sdk
  python examples/register_and_call_public_mcp_server.py cleanup
"""

from __future__ import annotations

import argparse
import sys

from pyfsr import FortiSOAR
from pyfsr.config import EnvConfig

MCP_NAME = "DeepWiki (public demo)"
MCP_URL = "https://mcp.deepwiki.com/mcp"

DEFAULT_REPO = "modelcontextprotocol/python-sdk"


def connect() -> FortiSOAR:
    return EnvConfig.from_env().client()


def build_config() -> dict:
    return {
        "name": MCP_NAME,
        "description": "Public no-auth DeepWiki MCP server (GitHub repo Q&A) — pyfsr demo",
        "type": "external",
        "transport": "http",  # FortiSOAR maps http -> streamable_http
        "url": MCP_URL,
        "active": True,
        "authentication": {"type": "NONE"},
    }


def register(client: FortiSOAR) -> str:
    """Validate + register DeepWiki as an external MCP server."""
    # register_and_verify does validate-then-save (validate-then-save is what
    # the UI does too; upsert keys on name, so re-running updates the
    # existing row instead of duplicating it) and hands back the tool list
    # from that same validation call — no separate probe needed.
    try:
        saved = client.ai.register_and_verify(build_config())
    except ValueError as exc:
        sys.exit(str(exc))
    print(f"Validation: tools={saved['tools']}")
    print(f"Registered {MCP_NAME!r} in FortiSOAR as {saved['uuid']}")
    return saved["uuid"]


def call(client: FortiSOAR, repo: str) -> None:
    """The full register→list→call flow, all through pyfsr.

    Once a server is registered, ``client.ai.list_registered_tools`` and
    ``client.ai.call_registered_tool`` resolve its url + auth from the
    registration and speak MCP ``tools/list`` / ``tools/call`` — the same reach
    fsr-ai's agent uses, driven from your own process. FortiSOAR has no REST
    endpoint that runs an external server's tool, so pyfsr does the MCP call
    client-side (the appliance is not in the tool-call path — only the config
    lookup goes through it). For a server whose credential is stored write-only,
    pass ``token=...``; DeepWiki takes no auth so none is needed here.
    """
    print(f"\n-- list_registered_tools({MCP_NAME!r}) --")
    tools = client.ai.list_registered_tools(MCP_NAME)
    print("tools:", [t.name for t in tools])

    # NB: r.ok means status == "success" — a FortiSOAR-native envelope convention.
    # A third-party server like DeepWiki returns its own payload (here markdown
    # text), so .ok is False even on success; use .result / .error for such servers.
    print(f"\n-- call_registered_tool read_wiki_structure({repo!r}) --")
    r = client.ai.call_registered_tool(MCP_NAME, "read_wiki_structure", {"repoName": repo})
    print(str(r.result)[:600])

    print(f"\n-- call_registered_tool ask_question({repo!r}, ...) --")
    r = client.ai.call_registered_tool(
        MCP_NAME,
        "ask_question",
        {"repoName": repo, "question": "What transport layers does this project support?"},
    )
    print(str(r.result)[:800])


def cleanup(client: FortiSOAR) -> None:
    existing = next((m for m in client.ai.list_mcp_servers() if m.get("name") == MCP_NAME), None)
    if not existing:
        print(f"{MCP_NAME!r} is not registered — nothing to clean up.")
        return
    uuid = existing.get("uuid") or existing.get("id")
    client.ai.delete_mcp_server(uuid)
    print(f"Deleted {MCP_NAME!r} ({uuid})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("register", help="validate + register DeepWiki as an external MCP server")
    c = sub.add_parser("call", help="list + call the registered server's tools via client.ai")
    c.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub repo to ask about (default: {DEFAULT_REPO})")
    sub.add_parser("cleanup", help="delete the registered DeepWiki server")
    args = ap.parse_args()

    if args.cmd == "register":
        register(connect())
    elif args.cmd == "call":
        call(connect(), args.repo)
    elif args.cmd == "cleanup":
        cleanup(connect())


if __name__ == "__main__":
    main()
