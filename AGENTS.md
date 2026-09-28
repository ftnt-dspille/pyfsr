# AGENTS.md -- pyfsr

Guide for coding agents (Copilot, Cursor, Windsurf, Claude, etc.) working with
pyfsr. For Jinja/picklist/audit details, see the sections below from the
original file.

## What this is

**pyfsr** is a batteries-included Python client for the FortiSOAR REST API --
typed CRUD, query DSL, picklist resolution, connector execution, playbook-run
history, and an AI/agent tool registry + MCP server. It also has a CLI for
appliance ops, playbook authoring, and record management.

The sibling [`fsr_playbooks`](https://pypi.org/project/fsr-playbooks/) package
does YAML → FortiSOAR playbook **compilation**. pyfsr does the **transport**:
push compiled playbooks, trigger them, query records. They're separate packages.

## Setup

```bash
pip install pyfsr
pip install 'pyfsr[mcp]'    # + generic MCP server
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

client = FortiSOAR("soar.example.com", "your-api-key")

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

The full DB (65MB) lives at:
`/Users/dylanspille/PycharmProjects/fsr-playbook-framework/data/fsr_reference.db`

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
