# MCP setup snippets

Point a coding agent at any FortiSOAR box with its URL and an API key. Each
file here is the minimal config for one agent product; copy it, fill in your
values, and restart the agent. `pyfsr agent init --url ... --api-key ...`
generates these for you after verifying the connection.

| Agent | File | Where it goes |
|---|---|---|
| Claude Code | `claude-code.mcp.json` | `.mcp.json` in the project root (or merge into `~/.claude.json`) |
| OpenCode | `opencode.json` | `opencode.json` in the project root (merge the `mcp` key) |
| GitHub Copilot (VS Code) | `copilot-vscode-mcp.json` | `.vscode/mcp.json` |

The snippets use `uvx --from 'pyfsr[mcp]' pyfsr-mcp` so nothing needs a local
install; with pyfsr installed, point `command` at the `pyfsr-mcp` executable
instead.

Safety knobs for the `env` block (details in `AGENTS.md`):

- `FSR_MCP_READONLY=1` -- read-only tool surface.
- `FSR_MCP_ALLOW_TOOLS` / `FSR_MCP_DENY_TOOLS` -- narrow the surface with
  comma-separated names or globs.
- High-impact tools (deletes, `publish`, `create_user`, `install_connector`)
  always require `confirm: true` per call.

Multi-box: create `~/.pyfsr/instances.toml` and every tool gains an `instance`
argument plus a `list_instances` tool (see the pyfsr docs).
