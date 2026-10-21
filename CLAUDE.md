# pyfsr -- project instructions

Read `AGENTS.md` first: it holds the capability map, the agent safety model
(FSR_MCP_READONLY, allow/deny globs, confirm-gated tools), the
verify-the-effect table, and the MCP setup snippets. This file only adds
repo-specific rules.

## Rules

- Public repo. No lab IPs, hostnames, credentials or capture dates anywhere
  tracked (code, tests, docstrings). Placeholders only: `fortisoar.example.com`,
  `<your-api-key>`. Where live verification matters, say "live-verified on
  8.0.0", never when or where.
- No em or en dashes in files; the pre-commit hook rejects them. Use hyphens.
- Commits: author as the user, no AI attribution lines.
- Tests: `.venv/bin/python -m pytest tests/unit` must stay green; it runs
  offline (no box). A new tool in `src/pyfsr/agent/tools.py` needs its
  mutating/destructive flag considered and a test in
  `tests/unit/test_agent_safety.py` (the flag-relationship tests will fail
  loudly if you forget).
- Lint: pre-commit runs ruff check + format; run
  `.venv/bin/python -m ruff check --fix` before committing.
- The `pyfsr` CLI is argparse (`src/pyfsr/cli/__main__.py` wires the groups);
  new groups get a module with `build_subparser(sub)`.
- `src/pyfsr/agent/tools.py` is the single tool registry; the MCP server
  (`agent/mcp.py`), Anthropic and OpenAI exports all render from it. Never add
  a tool to one surface only.
- Docstrings record live-verified behavior; do not soften or delete those
  notes without re-verifying on a box.
