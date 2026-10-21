"""``pyfsr agent`` command group -- wire a coding agent to a FortiSOAR box.

``pyfsr agent init`` is the one-command bootstrap: verify the connection with a
real whoami call, optionally save the settings to an env file, and print the
MCP config snippet for the agent product in use (Claude Code, OpenCode, GitHub
Copilot). The snippets launch pyfsr's own MCP server (``pyfsr-mcp``), preferring
the locally installed executable and falling back to ``uvx --from pyfsr[mcp]``.

Safety env vars an agent setup may add to the snippet's ``env`` block:
``FSR_MCP_READONLY=1``, ``FSR_MCP_ALLOW_TOOLS``, ``FSR_MCP_DENY_TOOLS`` (see
:mod:`pyfsr.agent.tools`).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def _server_command() -> tuple[str, list[str]]:
    """The command + args that launch pyfsr-mcp on this machine."""
    exe = shutil.which("pyfsr-mcp")
    if exe:
        return exe, []
    return "uvx", ["--from", "pyfsr[mcp]", "pyfsr-mcp"]


def _env_block(args: argparse.Namespace) -> dict[str, str]:
    env = {"FSR_BASE_URL": args.url, "FSR_API_KEY": args.api_key}
    if args.no_verify_ssl:
        env["FSR_VERIFY_SSL"] = "false"
        env["FSR_SUPPRESS_INSECURE_WARNINGS"] = "true"
    if args.readonly:
        env["FSR_MCP_READONLY"] = "1"
    return env


def _claude_code_snippet(args: argparse.Namespace) -> str:
    command, cmd_args = _server_command()
    cfg = {
        "mcpServers": {
            "fortisoar": {
                "command": command,
                **({"args": cmd_args} if cmd_args else {}),
                "env": _env_block(args),
            }
        }
    }
    return "# .mcp.json (project root), or merge into ~/.claude.json\n" + json.dumps(cfg, indent=2)


def _opencode_snippet(args: argparse.Namespace) -> str:
    command, cmd_args = _server_command()
    cfg = {
        "mcp": {
            "fortisoar": {
                "type": "local",
                "command": [command, *cmd_args],
                "environment": _env_block(args),
                "enabled": True,
            }
        }
    }
    return "# opencode.json (project root) -- merge the 'mcp' key\n" + json.dumps(cfg, indent=2)


def _copilot_snippet(args: argparse.Namespace) -> str:
    command, cmd_args = _server_command()
    cfg = {
        "servers": {
            "fortisoar": {
                "command": command,
                **({"args": cmd_args} if cmd_args else {}),
                "env": _env_block(args),
            }
        }
    }
    return "# .vscode/mcp.json (VS Code / GitHub Copilot agent mode)\n" + json.dumps(cfg, indent=2)


_SNIPPETS = {
    "claude-code": _claude_code_snippet,
    "opencode": _opencode_snippet,
    "copilot": _copilot_snippet,
}


def _verify(args: argparse.Namespace) -> dict | None:
    """whoami against the box; None (with a printed error) when unreachable."""
    from ..client import FortiSOAR

    try:
        client = FortiSOAR(
            args.url,
            token=args.api_key,
            verify_ssl=not args.no_verify_ssl,
            suppress_insecure_warnings=args.no_verify_ssl,
        )
        return client.users.whoami()
    except Exception as exc:  # noqa: BLE001 - report, don't trace
        print(f"error: could not verify the connection: {exc}", file=sys.stderr)
        return None


def _write_env_file(path: Path, args: argparse.Namespace) -> None:
    lines = [f"{k}={v}" for k, v in _env_block(args).items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the first byte (no write-then-chmod exposure window).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)  # an O_TRUNC of a pre-existing looser file keeps its old mode


def cmd_init(args: argparse.Namespace) -> int:
    me = _verify(args)
    if me is None:
        if not args.skip_verify:
            print("hint: re-run with --skip-verify to print the snippets anyway", file=sys.stderr)
            return 1
        print("continuing despite failed verification (--skip-verify)", file=sys.stderr)
    else:
        print(f"connected: {me.get('loginid')} <{me.get('email')}> @ {args.url}")

    if args.save_env:
        path = Path(args.save_env).expanduser()
        _write_env_file(path, args)
        print(f"saved credentials to {path} (mode 600)")

    products = [p for p in _SNIPPETS if getattr(args, p.replace("-", "_"))]
    if not products:
        products = ["claude-code"]
    for product in products:
        print(f"\n--- {product} ---")
        print(_SNIPPETS[product](args))
    print(
        "\nsafety: add FSR_MCP_READONLY=1 to the env block for a read-only surface, "
        "or FSR_MCP_ALLOW_TOOLS/FSR_MCP_DENY_TOOLS to narrow it. Destructive tools "
        "always need confirm=true."
    )
    return 0


def build_subparser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``agent`` subcommands onto an existing subparsers object."""
    p_init = sub.add_parser("init", help="verify a box and print the MCP setup snippet for your coding agent")
    p_init.add_argument("--url", required=True, help="FortiSOAR base URL, e.g. fortisoar.example.com")
    p_init.add_argument("--api-key", required=True, help="API key (Settings > API Keys on the appliance)")
    p_init.add_argument("--no-verify-ssl", action="store_true", help="disable TLS verification (lab boxes)")
    p_init.add_argument("--readonly", action="store_true", help="bake FSR_MCP_READONLY=1 into the snippet")
    p_init.add_argument("--save-env", metavar="PATH", help="also write the FSR_* values to this env file (mode 600)")
    p_init.add_argument("--skip-verify", action="store_true", help="print snippets even if whoami fails")
    p_init.add_argument("--claude-code", action="store_true", help="print the Claude Code .mcp.json snippet (default)")
    p_init.add_argument("--opencode", action="store_true", help="print the OpenCode opencode.json snippet")
    p_init.add_argument("--copilot", action="store_true", help="print the VS Code / Copilot .vscode/mcp.json snippet")
    p_init.set_defaults(func=cmd_init)
