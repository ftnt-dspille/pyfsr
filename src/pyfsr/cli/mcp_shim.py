"""``pyfsr-mcp``: deprecated. The agent tools and MCP server moved to fsr-agentic-toolkit.

pyfsr is the SDK only. This entry point hands off to the toolkit's server when it
is installed, so existing MCP configs keep working, and otherwise says how to get it.
"""
from __future__ import annotations

import shutil
import sys

_MOVED = (
    "pyfsr-mcp is deprecated: the agent tools and MCP server moved to fsr-agentic-toolkit.\n"
    "Point your MCP config at `fsr-agentic-toolkit mcp`."
)
_INSTALL = "Install it with `pip install fsr-agentic-toolkit` (or run `uvx fsr-agentic-toolkit mcp`)."


def main(argv: list[str] | None = None) -> int:
    # stdout carries the MCP stdio protocol, so every notice goes to stderr.
    print(_MOVED, file=sys.stderr)
    try:
        from fsr_agentic_toolkit.cli import main as toolkit_main
    except ImportError:
        exe = shutil.which("fsr-agentic-toolkit")
        if exe is None:
            print(_INSTALL, file=sys.stderr)
            return 1
        import os

        os.execv(exe, [exe, "mcp", *(sys.argv[1:] if argv is None else argv)])
        return 0  # only reached when execv is stubbed
    return toolkit_main(["mcp", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
