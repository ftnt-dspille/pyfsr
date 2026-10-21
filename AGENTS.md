# AGENTS.md -- pyfsr

Guide for coding agents (Claude Code, OpenCode, Copilot, Cursor, etc.) working
with pyfsr, and for agents driving a FortiSOAR box THROUGH pyfsr's MCP server.

## What this is

**pyfsr** is a batteries-included Python client for the FortiSOAR REST API --
typed CRUD, query DSL, picklist resolution, connector execution and
installation, playbook-run history, user/module admin, and an AI/agent tool
registry + MCP server (`pyfsr-mcp`). It also has a CLI for appliance ops,
playbook authoring, connector development, and record management.

The sibling [`fsr_playbooks`](https://pypi.org/project/fsr-playbooks/) package
does YAML → FortiSOAR playbook **compilation**. pyfsr does the **transport**:
push compiled playbooks, trigger them, query records. pyfsr's
`compile_playbook_yaml`/`publish_playbook` tools call fsr_playbooks through the
`pyfsr[playbooks]` extra, so an agent gets the whole authoring loop from one
server.

## Wire up your agent in two minutes

```bash
pip install 'pyfsr[mcp]'
pyfsr agent init --url fortisoar.example.com --api-key '<your-api-key>'
```

`agent init` verifies the connection with a real whoami call, then prints the
MCP config snippet to paste: `--claude-code` (a project `.mcp.json`),
`--opencode` (`opencode.json`), `--copilot` (`.vscode/mcp.json`), or all three.
Ready-made examples live in [`examples/mcp/`](examples/mcp/). No local install
at all: configure the server command as
`uvx --from 'pyfsr[mcp]' pyfsr-mcp`.

First tool call in any session: `whoami` -- it confirms who you are, which box
you're on, and the FortiSOAR version before anything else runs.

## Safety model (read this before writing anything)

Every agent surface (the MCP server, `to_anthropic_tools`/`to_openai_tools`,
and `dispatch`) shares one policy gate:

- `FSR_MCP_READONLY=1` -- mutating tools are hidden from the listing AND
  refused if called by name. Start here when exploring an unfamiliar box.
- `FSR_MCP_ALLOW_TOOLS` / `FSR_MCP_DENY_TOOLS` -- comma-separated tool names or
  globs (e.g. `FSR_MCP_DENY_TOOLS=appliance_*,delete_*`).
- High-impact tools (`delete_record`, `delete_module`, `delete_schedule`,
  `publish`, `create_user`, `install_connector`) never run without
  `confirm: true` in the call's arguments. Tell the user exactly what will
  change before setting it.
- `FortiSOAR(..., dry_run=True)` skips every POST/PUT/PATCH/DELETE at the
  client level (reads still work) -- useful for rehearsing a script.

**Never trust a 200: verify the effect.** After every write, read the state
back and check it:

| You just did | Verify with |
|---|---|
| `create_record` / `update_record` | `get_record` on the returned uuid; check the fields landed |
| `create_module` + `publish` | `describe_module`; then a test `create_record` in it |
| `publish_playbook` | trigger it, then `wait_for_playbook_run` + `diagnose_run` |
| `install_connector` | `healthcheck_connector` (status 'Available'); `list_connector_operations` |
| connector configuration writes | `healthcheck_connector`; `run_connector_operation` on a read-only op |
| `create_user` | `whoami` as that user, or query the people module |
| anything on a record | `record_lifecycle` shows the full change timeline |

## Capability map (what to use for what)

| Task | Tools (MCP) / API |
|---|---|
| connect + verify | `whoami`; `list_modules` |
| discover schema | `list_modules`, `describe_module`, `list_picklists`, `get_picklist_values` |
| records | `search_records`, `query_records`, `get_record`, `create_record`, `update_record`, `upsert_record`, `get_or_create_record` |
| users | `create_user` (idempotent by email); `client.users` for roles/teams detail |
| custom modules | `create_module` (fields + `grant_to`), then `publish`; `delete_module` to remove |
| connectors: discover | `list_connectors`, `list_connector_operations` (live), `default_connector_config` |
| connectors: run | `run_connector_operation`; `healthcheck_connector` first |
| connectors: install | `install_connector` (Content Hub name or local .tgz) |
| connectors: build | CLI `pyfsr connector scaffold / package / deploy` (see below) |
| playbooks: author | `compile_playbook_yaml` (offline), `publish_playbook` |
| playbooks: run + debug | `last_playbook_run`, `wait_for_playbook_run`, `why_playbook_failed`, `diagnose_run` |
| investigation | `investigate_alert`, `get_investigation_result`; write findings back with `update_record` |
| audit trail | `record_lifecycle`, `execution_context`, `audit_activities` |
| scheduling | `schedule_playbook`, `trigger_schedule_now`, `schedule_audit`, `delete_schedule` |
| box internals (SSH) | `appliance_*` (read-only; needs `PYFSR_APPLIANCE_*` env) |

Errors come back as data, never exceptions: `{"error": {type, message, tool,
status_code, method, url, body}}`, plus `valid_values` on a picklist miss. Read
the body snippet -- it usually names the exact field the box rejected.

## Setup

```bash
pip install pyfsr
pip install 'pyfsr[mcp]'        # + the MCP server
pip install 'pyfsr[playbooks]'  # + YAML playbook compile/publish
```

Python 3.10+. Pydantic v2. MIT.

### Configure from the environment

```python
from pyfsr import EnvConfig

# reads FSR_BASE_URL (+ FSR_API_KEY or FSR_USERNAME/FSR_PASSWORD),
# FSR_PORT, FSR_VERIFY_SSL, FSR_TIMEOUT
client = EnvConfig.from_env().client()
```

Or with named instances (`~/.pyfsr/instances.toml`):

```bash
pyfsr instances list                 # every alias: base URL, auth kind
pyfsr instances show 206             # one instance's resolved settings
pyfsr instances check                # connect to each box; exit 1 if any fails
```

## Playbook authoring + deployment (pyfsr CLI)

pyfsr can author and deploy playbooks directly, or you can use `fsr_playbooks`
for the full YAML compiler:

```bash
pyfsr playbook steps                 # list every step type you can write
pyfsr playbook step-help TYPE        # keys + a compiling example for one type
pyfsr playbook examples              # foundational playbook library
pyfsr playbook show SLUG             # print one library playbook's YAML
pyfsr playbook validate flow.yaml    # compile + report diagnostics (offline)
pyfsr playbook compile flow.yaml     # emit the FSR import envelope (offline)
pyfsr playbook lint flow.yaml        # live preflight: connector steps missing config
pyfsr playbook deploy flow.yaml      # compile and create on the appliance
```

## Connector development (the full ConnectorsV2 loop)

```bash
pyfsr connector scaffold acme-threat-feed      # minimal installable skeleton
# ... edit operations.py + info.json ...
pyfsr connector package acme-threat-feed       # dist/acme-threat-feed-1.0.0.tgz
FSR_BASE_URL=fortisoar.example.com FSR_API_KEY='<your-api-key>' \
  pyfsr connector deploy acme-threat-feed \
  --config-json '{"server": "https://service.example.com"}'
```

The scaffold ships one config field (`server`), one operation (`ping`) and a
health check, so it installs and runs end to end before any real logic exists.
`deploy` packages (if given a directory), installs with `$replace` (idempotent
redeploys), waits for the connector to activate, upserts the configuration as
default, and healthchecks. Same flow from Python: `pyfsr.connector_dev`
(`scaffold_connector`, `package_connector`, `deploy_connector`).

## Connector icons (pyfsr CLI)

```bash
pip install 'pyfsr[icons]'
pyfsr connector icon FortiAIGate.svg --connector path/to/connector   # writes images/<icon_large_name> (80x80) + <icon_small_name> (32x32)
```

Takes an SVG (Fortinet's library is https://icons.fortinet.com) and lays it out like the stock Fortinet
connector icons. It draws the SVG with `rsvg-convert`, `cairosvg`, macOS Quick Look or headless Chrome,
whichever is installed. The FORTINET wordmark is copied from the connector's current icons (or
`--wordmark-from DIR`); `--no-wordmark` omits it.

## Trigger + wait for a playbook (the simple way)

```python
# One call: trigger + poll to terminal + typed result with timing
result = client.playbooks.run_and_wait("My Playbook", timeout=60)

print(result.status)           # 'finished' / 'failed' / 'terminated'
print(result.succeeded)         # True iff status == 'finished'

for step in result.steps:
    print(f"  {step.name:40} {step.status:10} {step.duration_ms or 0:6}ms"
          f"  {'*** SLOW' if step.is_slow else ''}")

if result.failure:
    print(f"failed at: {result.failure.failing_step}")
    print(f"  error: {result.failure.error_message}")
```

`run_and_wait` auto-picks the trigger route (action vs notrigger) based on
whether you pass `record_uuid` + `module` or just a playbook name. Returns a
`RunResult` with `status`, `task_id`, `steps` (with `duration_ms`/`is_slow`),
`failure`, and `children` (sub-playbook runs).

### Lower-level primitives (when you need more control)

```python
# Trigger without waiting
run = client.playbooks.trigger("My Playbook", inputs={})
task_id = run["task_id"]

# Poll by task_id
client.playbooks.wait(task_id, timeout=300, interval=3)

# Non-blocking status check
client.playbooks.status(task_id)

# Poll by playbook name (not task_id)
client.playbooks.wait_for_run("My Playbook", since=<timestamp>)

# Diagnose a failure
client.playbooks.why_failed(run_pk)
```

## Quick start (records, queries, CRUD)

```python
from pyfsr import FortiSOAR, Query

client = FortiSOAR("fortisoar.example.com", token="<your-api-key>")

incidents = client.records("incidents")
inc = incidents.get("0d2c...")          # by uuid, "module:uuid", or IRI
inc["name"], inc.uuid                    # dict- AND attribute-accessible

page = incidents.query(
    Query().eq("status.itemValue", "Open").like("name", "phish").limit(50)
)
for inc in incidents.iterate(Query().eq("status.itemValue", "Open")):
    ...                                  # lazily walks every page

new = incidents.create({"name": "Suspicious login", "severity": "High"},
                        resolve_picklists=True)
incidents.update(new.uuid, {"status": "Closed"}, resolve_picklists=True)
incidents.delete(new.uuid)               # soft by default; hard=True to purge
```

## AI / agent-friendly

pyfsr ships a transport-neutral tool registry for the core operations, with
token-efficient results and structured (never-raised) errors:

```python
from pyfsr.agent.tools import to_anthropic_tools, to_openai_tools, dispatch

tools = to_anthropic_tools()             # or to_openai_tools()
result = dispatch(client, "search_records",
                  {"module": "alerts", "summary": True, "limit": 10})
```

Or run the generic MCP server:

```bash
pip install 'pyfsr[mcp]'
FSR_BASE_URL=soar.example.com FSR_API_KEY=... python -m pyfsr.agent.mcp
```

## Jinja filter lookup (before grepping)

When you need a FortiSOAR Jinja filter, query the reference DB before grepping
the corpus. The `fsr_reference.db` has 170+ filters, 15 globals, 39 tests --
all with full signatures, curated docs, and real usage examples.

```bash
pyfsr jinja find picklist             # signature + curated doc + examples
pyfsr jinja search "query body"       # full-text search
pyfsr jinja list                      # list all filters
pyfsr jinja examples picklist         # real-world usage examples
pyfsr jinja idioms                    # common Jinja patterns
```

The full reference DB (65MB) is bundled with the `fsr_playbooks` package, or
can be built from the
[fsr-playbook-framework repo](https://github.com/ftnt-dspille/fsr-playbook-framework)
(`data/fsr_reference.db`).

## Picklist IRI resolution

```python
# Resolve a friendly value to its IRI on this box
iri = client.picklists.resolve("Indicator Extracted", picklist="AlertState")

# Reverse-resolve an IRI back to a friendly name
info = client.picklists.reverse_resolve("/api/3/picklists/501d0562-...")
# -> {"picklist": "AlertState", "itemValue": "Indicator Extracted", "iri": "..."}

# Build a Jinja picklist expression that resolves dynamically at runtime
expr = client.picklists.jinja_picklist_expr("AlertState", "Indicator Extracted")
# -> '{{ "AlertState" | picklist("Indicator Extracted", "@id") }}'
```

## Step timing + run inspection

```python
# Trigger + poll + return typed result with timing + children
result = client.playbooks.run_and_wait("My Playbook", timeout=60)
print(result.status)
print(result.slow_steps)       # steps > 30s

# Sorted step timeline with timing
for s in client.playbooks.step_timeline(run_pk):
    print(f"  {s.name:40} {s.status:10} {s.duration_ms or 0:6}ms")

# Full audit lifecycle of a record
life = client.audit.lifecycle(alert_uuid, entity_type="alerts")
print(life.summary())
for e in life.entries:
    print(f"  [{e.timestamp_iso}] {e.kind:10} {e.operation:12} {e.playbook_name or ''}")

# What else was happening to the record during a specific run?
ctx = client.audit.execution_context(run_pk, window_seconds=120)
print(ctx.summary())
for ch in ctx.concurrent_changes:
    print(f"  [{ch.timestamp_iso}] {ch.operation:12} by={ch.user or '?':10} pb={ch.playbook_name or ''}")
```

## CLI overview

```bash
pyfsr instances list                 # named-instance registry
pyfsr appliance info                  # host, version, content DB
pyfsr appliance service restart cyops-postman --yes
pyfsr playbook steps                  # step types for YAML authoring
pyfsr playbook validate flow.yaml     # compile + diagnostics (offline)
pyfsr playbook deploy flow.yaml       # compile + create on appliance
pyfsr records alerts [--status Open]  # query records
pyfsr records delete <module> <uuid>  # delete records
pyfsr mcp list-tools                  # FortiSOAR's native MCP gateway
```
