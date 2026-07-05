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

Why a plain MCP client for the ``call`` step, not something on ``client``:
FortiSOAR's fsr-ai only calls a registered external server *during an agent
investigation* — there's no REST passthrough to invoke one of its tools
directly for testing (that's why ``fortisiem_mcp_setup_and_test.py`` proves
usage by attributing an *investigation's* tool calls back to the server,
rather than calling a tool directly). Since DeepWiki takes no auth, calling
it directly here is simpler and just as real a proof.

For an appliance's *own* native MCP gateway (``/mcp/soc/``, ``/mcp/modules/``,
one auto-generated server per installed connector, ...) use ``client.mcp``
(:class:`pyfsr.api.native_mcp.NativeMCPApi`) instead — see
``examples/`` for that surface once written, or the module docstring.

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
import asyncio
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
    print(f"Validation: tools={[t.get('name') for t in saved['tools']]}")
    print(f"Registered {MCP_NAME!r} in FortiSOAR as {saved['uuid']}")
    return saved["uuid"]


async def _call_tools(repo: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(MCP_URL) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print(f"\n-- read_wiki_structure({repo!r}) --")
            structure = await session.call_tool("read_wiki_structure", {"repoName": repo})
            for block in structure.content:
                text = getattr(block, "text", None)
                if text:
                    print(text[:600])

            print(f"\n-- ask_question({repo!r}, ...) --")
            answer = await session.call_tool(
                "ask_question",
                {"repoName": repo, "question": "What transport layers does this project support?"},
            )
            for block in answer.content:
                text = getattr(block, "text", None)
                if text:
                    print(text[:800])


def call(repo: str) -> None:
    """Call a couple of DeepWiki's tools directly — no FortiSOAR round-trip
    needed for the call itself, since the point is proving the *server* (the
    thing FortiSOAR just registered) is real and answers real queries."""
    asyncio.run(_call_tools(repo))


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
    c = sub.add_parser("call", help="call a couple of DeepWiki's tools directly")
    c.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub repo to ask about (default: {DEFAULT_REPO})")
    sub.add_parser("cleanup", help="delete the registered DeepWiki server")
    args = ap.parse_args()

    if args.cmd == "register":
        register(connect())
    elif args.cmd == "call":
        call(args.repo)
    elif args.cmd == "cleanup":
        cleanup(connect())


if __name__ == "__main__":
    main()
