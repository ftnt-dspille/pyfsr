"""``appliance.ai`` -- the per-agent MCP tool allow-list of the FortiAI runtime (fsr-ai).

Binding an MCP server to an agent over REST (``AIApi.allow_mcp_server_for_agent``) is
not enough for the agent to *use* its tools. At run time each agent's
``ToolExecutionAgent`` loads only the tools named in the agent package's own
``/opt/fsr-ai/ai-agents/<agent>_<version>/config/memory.yaml``::

    allowed_tools:
      "<mcp-server-uuid>": ["tool_a", "tool_b"]   # [] = the server's tools are blocked

A server that is bound over REST but absent from that file is silently never called: the
investigation completes without it and nothing in the trace says why. No API exposes
the file, so it has to be edited on the box. :func:`allow_mcp_tools` does that
idempotently (it only ever *adds* tool names), keeps a one-time ``.orig`` backup, and
can restart ``fsr-ai`` / ``fsr-ai-celery`` so the agents reload it.

The new text is passed as a command argument, never on stdin: the transports put the
sudo password on stdin, and with passwordless sudo nothing consumes it, so it would
land in the file. Live-verified on 8.0.0 and 8.0.1.
"""

from __future__ import annotations

import re

import yaml
from pydantic import BaseModel, Field

from .transport import Transport, TransportError

#: Where fsr-ai keeps its agent packages (live-verified on 8.0.0 and 8.0.1).
AGENTS_DIR = "/opt/fsr-ai/ai-agents"

#: The services that load the agent packages.
SERVICES = ("fsr-ai", "fsr-ai-celery")


class AllowChange(BaseModel):
    """Outcome of :func:`allow_mcp_tools`."""

    agent: str
    path: str
    mcp_uuid: str
    added: list[str] = Field(default_factory=list)
    before: list[str] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list)
    backup: str | None = None
    restarted: bool = False

    @property
    def changed(self) -> bool:
        """Whether the file was rewritten (``False`` = every tool was already allowed)."""
        return bool(self.added)

    @property
    def ok(self) -> bool:
        """Every requested tool is on the allow-list as read back from the box."""
        return set(self.added) <= set(self.after)

    def __str__(self) -> str:
        if not self.changed:
            return f"{self.agent}: {self.mcp_uuid} already allows the requested tools -- no change"
        verdict = "ok" if self.ok else "FAILED"
        restart = ", fsr-ai restarted" if self.restarted else " (restart fsr-ai to load it)"
        tools = ", ".join(self.added)
        return f"{self.agent}: allowed {tools} on {self.mcp_uuid}: {verdict} (backup {self.backup}){restart}"


def parse(text: str) -> dict[str, list[str]]:
    """The ``allowed_tools`` mapping (server uuid -> tool names) of a ``memory.yaml`` text."""
    data = yaml.safe_load(text) or {}
    raw = data.get("allowed_tools") or {}
    return {str(k): [str(t) for t in (v or [])] for k, v in raw.items()}


def _line(uuid: str, tools: list[str], comment: str = "") -> str:
    body = ", ".join(f'"{t}"' for t in tools)
    return f'  "{uuid}": [{body}]' + (f" # {comment}" if comment else "")


def add_tools(text: str, mcp_uuid: str, tools: list[str], *, comment: str = "") -> str:
    """Return ``memory.yaml`` ``text`` with ``tools`` allowed on ``mcp_uuid``.

    An existing entry for the server is rewritten in place with the union of its tools
    (order kept, trailing comment kept); otherwise a new entry is appended to the
    ``allowed_tools`` block. Nothing is ever removed.
    """
    have = parse(text).get(mcp_uuid)
    if have is not None:
        merged = have + [t for t in tools if t not in have]
        pattern = re.compile(rf'^(\s*)"?{re.escape(mcp_uuid)}"?\s*:\s*\[[^\]]*\](\s*#.*)?$', re.MULTILINE)
        new, n = pattern.subn(lambda m: _line(mcp_uuid, merged) + (m.group(2) or ""), text, count=1)
        if n:
            return new
        raise ValueError(f"{mcp_uuid} is allowed in a layout this helper does not rewrite (multi-line list?)")
    return text.rstrip("\n") + "\n" + _line(mcp_uuid, tools, comment) + "\n"


def agent_dir(transport: Transport, agent: str) -> str:
    """The newest installed package directory of ``agent`` (``siem`` -> ``.../siem_1_0_0``)."""
    out = transport.run(["sh", "-c", 'ls -d "$1"/"$2"_* 2>/dev/null', "sh", AGENTS_DIR, agent], sudo=True)
    dirs = sorted(d for d in out.stdout.split() if re.search(rf"/{re.escape(agent)}_\d+(_\d+)*$", d))
    if not dirs:
        raise TransportError(f"no installed agent package matching {agent!r} under {AGENTS_DIR}")
    return max(dirs, key=lambda d: [int(x) for x in re.findall(r"\d+", d.rsplit("/", 1)[1])])


def memory_path(transport: Transport, agent: str) -> str:
    """Path of the agent's ``config/memory.yaml``."""
    return f"{agent_dir(transport, agent)}/config/memory.yaml"


def allowed_tools(transport: Transport, agent: str) -> dict[str, list[str]]:
    """The agent's allow-list: MCP server uuid -> tool names. Read-only."""
    text = transport.run(["cat", memory_path(transport, agent)], sudo=True).check().stdout
    return parse(text)


def allow_mcp_tools(
    transport: Transport,
    agent: str,
    mcp_uuid: str,
    tools: list[str],
    *,
    comment: str = "",
    restart: bool = False,
    yes: bool = False,
) -> AllowChange:
    """Allow ``tools`` of the MCP server ``mcp_uuid`` for ``agent``. Gated by ``yes``.

    A no-op (nothing written, no ``yes`` needed) when every tool is already allowed.
    Otherwise it keeps a one-time ``memory.yaml.orig``, writes the merged file and reads
    it back so the result reports what is on disk. With ``restart=True`` it restarts
    ``fsr-ai`` and ``fsr-ai-celery`` afterwards (agents only load the file at start).
    """
    path = memory_path(transport, agent)
    text = transport.run(["cat", path], sudo=True).check().stdout
    before = parse(text).get(mcp_uuid, [])
    missing = [t for t in dict.fromkeys(tools) if t not in before]
    if not missing:
        return AllowChange(agent=agent, path=path, mcp_uuid=mcp_uuid, before=before, after=before)
    if not yes:
        raise PermissionError(f"refusing to edit {path} without confirmation (pass yes=True)")

    backup = f"{path}.orig"
    transport.run(["sh", "-c", '[ -e "$2" ] || cp -p "$1" "$2"', "sh", path, backup], sudo=True).check()
    transport.run(
        ["sh", "-c", 'printf "%s" "$1" > "$2"', "sh", add_tools(text, mcp_uuid, missing, comment=comment), path],
        sudo=True,
    ).check()
    after = parse(transport.run(["cat", path], sudo=True).check().stdout).get(mcp_uuid, [])
    restarted = False
    if restart:
        transport.run(["systemctl", "restart", *SERVICES], sudo=True, timeout=180.0).check()
        restarted = True
    return AllowChange(
        agent=agent,
        path=path,
        mcp_uuid=mcp_uuid,
        added=missing,
        before=before,
        after=after,
        backup=backup,
        restarted=restarted,
    )
