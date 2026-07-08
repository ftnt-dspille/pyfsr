# AI & Agents

pyfsr ships a **framework-agnostic tool registry** — a declarative catalogue of
core FortiSOAR operations (record CRUD, discovery, picklists, connectors,
playbook runs) as JSON-Schema tool definitions, plus a `dispatch()` that
executes a tool call against a live client and returns JSON-safe, token-trimmed
results.

It's deliberately transport-neutral (no MCP, no provider SDK), so the same
registry can feed Anthropic tool-use, OpenAI function calling, the bundled MCP
server, or a home-grown agent loop.

```{seealso}
End-to-end FortiAI / FortiSIEM-MCP examples:
[`connect_fortisiem_mcp.py`](https://github.com/dylanspille/pyfsr/blob/main/examples/connect_fortisiem_mcp.py),
[`trigger_ai_investigation.py`](https://github.com/dylanspille/pyfsr/blob/main/examples/trigger_ai_investigation.py),
and [`investigate_fortisiem_incident.py`](https://github.com/dylanspille/pyfsr/blob/main/examples/investigate_fortisiem_incident.py).
See the [examples index](https://github.com/dylanspille/pyfsr/blob/main/examples/README.md) for the full set.
```

## Why use it

Wiring an LLM to FortiSOAR by hand means hand-writing JSON-Schema for every
operation, normalizing Hydra envelopes, trimming huge records down to fit a
context window, and turning every HTTP error into something the model can read.
The registry does all of that for you:

- **Discovery built in.** The model can learn the appliance at runtime —
  `list_modules` → `describe_module` → act — instead of you hard-coding field
  names and module types that differ per deployment.
- **Token-trimmed results.** Every tool supports `summary=true` / `fields=[...]`
  so a 60-field alert doesn't blow the context window when an agent is scanning
  dozens of records.
- **Picklist resolution.** Agents pass friendly values (`"High"`) and the tool
  maps them to the IRIs the API actually requires — the single most common
  cause of failed writes.
- **Errors as data, not exceptions.** Every failure returns a structured
  `{"error": {...}}` the model can read and self-correct from, so one bad call
  doesn't kill the agent loop.
- **Write once, run anywhere.** The same registry feeds Claude, OpenAI, MCP, or
  your own loop — no per-provider glue.

## Available tools

The registry ships these tools, grouped by what they do:

```{list-table}
:header-rows: 1
:widths: 18 32 50

* - Group
  - Tool
  - What it does
* - **Discovery**
  - `list_modules`
  - List every module (type/label/plural). Start here to find the right module type.
* -
  - `describe_module`
  - Describe a module's fields: name, type, required-ness, and bound picklist.
* - **Records**
  - `get_record`
  - Fetch one record by reference; `summary`/`fields` keep the result small.
* -
  - `search_records`
  - Free-text search a module; returns a page of records.
* -
  - `query_records`
  - Structured query with `{field, operator, value}` filter conditions.
* -
  - `create_record`
  - Create a record; `resolve_picklists=true` accepts friendly picklist values.
* -
  - `update_record`
  - Update an existing record's fields by reference.
* -
  - `delete_record`
  - Delete one record (soft by default; `hard=true` to purge). Never collection-wide.
* - **Picklists**
  - `list_picklists`
  - List every picklist name on the appliance.
* -
  - `get_picklist_values`
  - List a picklist's items (itemValue, uuid, iri, ordinal).
* -
  - `resolve_picklist`
  - Resolve a friendly value (e.g. `"High"`) to its IRI.
* - **Connectors**
  - `list_connectors`
  - List installed + configured connectors with versions/configs.
* -
  - `healthcheck_connector`
  - Live-check whether a connector configuration is reachable.
* -
  - `run_connector_operation`
  - Execute one connector operation.
* - **Playbooks**
  - `list_playbook_runs`
  - List recent playbook runs (live + historical, newest first).
* -
  - `get_playbook_run`
  - Fetch one playbook run by its pk.
* - **FortiAI**
  - `investigate_alert`
  - Trigger an agentic investigation of an alert (normalize → hypothesize → plan → gather evidence → verdict).
* -
  - `get_investigation_result`
  - Fetch the status/verdict of an investigation by `task_id`.
* -
  - `list_ai_config`
  - Report FortiAI config: enabled features, LLM profiles, registered MCP servers.
* - **Modules (admin)**
  - `create_module`
  - Create a module in staging; `grant_to` wires RBAC in one call. Call `publish` to make it live.
* -
  - `delete_module`
  - Delete a module (the only op that actually removes one); optionally drops orphan tables.
* -
  - `publish`
  - Commit ALL staged schema changes appliance-wide (appliance-wide, not module-scoped).
* - **Connector config**
  - `default_connector_config`
  - Build a complete, runtime-valid default config (handles `onchange` sub-fields). Call first, then edit.
* -
  - `validate_connector_config`
  - Validate a config against the schema before submitting — returns `{valid, missing, invalid, ...}`.
* -
  - `create_connector_configuration`
  - Create a named config; `exist_ok=true` delegates to upsert, `autofill=true` fills schema defaults.
* -
  - `update_connector_configuration`
  - Update an existing config by `config_id`.
* -
  - `upsert_connector_configuration`
  - Idempotent create-or-replace by name — the safe default for deploy scripts.
* - **Playbook runs**
  - `last_playbook_run`
  - Most recent run of a playbook (live or historical); `{run: null}` if none.
* -
  - `why_playbook_failed`
  - Slim failure detail `{status, failing_step, error_message, pk}` of the most recent run.
* -
  - `wait_for_playbook_run`
  - Block until the newest run reaches a terminal state; return its summary.
* - **Records (upsert)**
  - `upsert_record`
  - Insert-or-update by natural key (or a `key` field); friendly picklists resolved by default.
* -
  - `get_or_create_record`
  - Look up by key field(s), create if absent; returns `{record, created}`.
* - **Scheduling**
  - `schedule_playbook`
  - Create a periodic task that runs a playbook on a cron schedule; returns the created schedule.
* -
  - `trigger_schedule_now`
  - Fire a scheduled task immediately (out-of-band of its cron); pair with `wait_for_playbook_run`.
* -
  - `delete_schedule`
  - Delete a scheduled periodic task entirely by name (use `disable` to merely pause).
```

Inspect any tool's full JSON-Schema (parameters, defaults, enums) at runtime
with `get_tool("query_records").input_schema`.

## Calling tools

`dispatch(client, name, arguments)` runs one tool and returns a JSON-safe,
token-trimmed result — never raises; a failure comes back as `{"error": {...}}`.
The read tools resolve against the replay session `demo_client()` builds, so
their return shapes are doctested here (write ops need a live appliance):

```{doctest}
>>> from pyfsr.agent.tools import dispatch
>>> client = demo_client()
>>> r = dispatch(client, "get_record", {"module": "alerts",
...     "ref": "9f0eb603-ac1e-41c3-b47b-444589beed39"})
>>> (r["@type"], r["name"])
('Alert', 'Response Capture Test Alert')
>>> hits = dispatch(client, "query_records", {"module": "alerts",
...     "filters": [{"field": "name", "operator": "eq",
...                  "value": "Response Capture Test Alert"}]})
>>> len(hits["members"]), hits["members"][0]["@type"]
(1, 'Alert')
>>> conns = dispatch(client, "list_connectors", {})
>>> [c.name for c in conns["connectors"][:3]]
['smtp', 'code-snippet', 'mitre-attack']
>>> mods = dispatch(client, "list_modules", {})
>>> [m["type"] for m in mods["modules"][:3]]
['agents', 'alerts', 'announcements']
>>> desc = dispatch(client, "describe_module", {"module": "alerts"})
>>> (desc["module"], desc["label"], desc["plural"])
('alerts', 'Alert', 'alerts')
>>> sev = next(f for f in desc["fields"] if f["name"] == "severity")
>>> (sev["type"], sev["picklist_name"])
('picklists', 'Severity')
>>> pl = dispatch(client, "list_picklists", {})
>>> pl["picklists"]
['AlertStatus', 'Severity']
>>> vals = dispatch(client, "get_picklist_values", {"name": "Severity"})
>>> [v["itemValue"] for v in vals["values"]]
['Minimal', 'Low', 'Medium', 'High', 'Critical']
```

The write tools (`create_record` / `update_record` / `delete_record`) replay
against the same captured alert, so their return shapes are doctested too — an
agent learns the envelope each tool returns without a live box:

```{doctest}
>>> client = demo_client()
>>> created = dispatch(client, "create_record", {"module": "alerts",
...     "data": {"name": "New Alert"}})
>>> (created["@type"], created["name"])
('Alert', 'Response Capture Test Alert')
>>> updated = dispatch(client, "update_record", {"module": "alerts",
...     "ref": "9f0eb603-ac1e-41c3-b47b-444589beed39",
...     "data": {"description": "revised"}})
>>> updated["@type"]
'Alert'
>>> dispatch(client, "delete_record", {"module": "alerts",
...     "ref": "9f0eb603-ac1e-41c3-b47b-444589beed39"})
{'deleted': '9f0eb603-ac1e-41c3-b47b-444589beed39', 'module': 'alerts', 'hard': False}
```

A `create_record` whose `data` carries a friendly picklist value that doesn't
resolve (typo, wrong casing) comes back as a structured, actionable error —
field, bad value, and the valid options — instead of an opaque box 400, because
the MCP write tools default `strict_picklists=True`:

```{doctest}
>>> client = demo_client()
>>> out = dispatch(client, "create_record", {"module": "alerts",
...     "data": {"severity": "Nope"}})
>>> out["error"]["type"], out["error"]["field"], out["error"]["picklist"]
('PicklistResolutionError', 'severity', 'Severity')
>>> "High" in out["error"]["valid_values"]
True
```

The discovery tools (`list_modules`, `describe_module`) and picklist tools
(`list_picklists`, `get_picklist_values`) are doctested above too — a model uses
`list_modules` → `describe_module` to learn a module's fields (and which are
picklist-backed) before it writes a record, and `list_picklists` /
`get_picklist_values` to resolve the friendly strings a picklist accepts.

### FortiAI investigation

`investigate_alert` kicks off a FortiAI agentic investigation (normalize →
hypothesize → plan → gather evidence over MCP → verdict). With `wait=false`
(default) it returns a `{"task_id", "status"}` handle immediately; poll it with
`get_investigation_result`, which returns the status plus the full verdict payload
(per-phase progress, summary with classification and key findings, hypotheses,
recommended next actions). Captured from a live 8.0 appliance; the verdict is
trimmed (one representative finding/hypothesis/log; all nine phase states kept):

```{doctest}
>>> client = demo_client()
>>> started = dispatch(client, "investigate_alert", {
...     "ref": "alerts:9f0eb603-ac1e-41c3-b47b-444589beed39"})
>>> started["status"]
'pending'
>>> result = dispatch(client, "get_investigation_result",
...                    {"task_id": started["task_id"]})
>>> result["status"]
'completed'
>>> result["result"]["summary"]["classification"]
'Inconclusive'
>>> [p["state"] for p in result["result"]["phases"]][:3]
['normalization', 'context_enrichment', 'hypothesis']
>>> result["result"]["playbook"]["immediate_next_actions"][0]  # doctest: +ELLIPSIS
'Preserve forensic evidence...'
```

## Use case: triage an alert end-to-end

A SOC analyst asks an agent *"Triage the latest critical alert and tell me if
it's a real threat."* With the registry attached, the model can carry out the
whole workflow itself — no bespoke code per step:

1. `query_records` on `alerts` filtered by `severity = Critical`, sorted newest
   first, `summary=true` → finds the alert without flooding its context.
2. `get_record` with `fields=[...]` → pulls just the fields it needs to reason.
3. `investigate_alert` → kicks off a FortiAI investigation that gathers evidence
   over the appliance's MCP servers and returns a verdict.
4. `create_record` on `comments` (with `resolve_picklists=true`) → writes its
   findings back to the alert so the human analyst sees them in FortiSOAR.

Every step is a tool call the model chooses; pyfsr handles discovery,
trimming, picklist IRIs, and error reporting so the agent stays on task.

## The registry

```{code-block} python
from pyfsr.agent.tools import list_tools, tool_schemas, dispatch

list_tools()        # names of every registered tool
tool_schemas()      # raw JSON-Schema definitions
```

Every result is JSON-serializable, and **every failure is returned as a
structured `{"error": {...}}` dict — never a raised exception** — so an agent
can read the message and self-correct.

## Anthropic (Claude) tool-use

`to_anthropic_tools()` returns the registry in Claude's tool-use shape
(`{name, description, input_schema}`), and `dispatch()` runs whatever tool the
model picks. Wiring the two together is a short loop: send the tools, run any
`tool_use` blocks Claude returns, feed the results back, and repeat until it
stops asking for tools.

```{code-block} python
import json

import anthropic
from pyfsr import FortiSOAR
from pyfsr.agent.tools import to_anthropic_tools, dispatch

soar = FortiSOAR("soar.example.com", "your-api-token")
llm = anthropic.Anthropic()                 # reads ANTHROPIC_API_KEY
tools = to_anthropic_tools()

messages = [{
    "role": "user",
    "content": "Find the latest critical alert and add a comment summarizing it.",
}]

while True:
    resp = llm.messages.create(
        model="claude-opus-4-8",
        max_tokens=1024,
        tools=tools,
        messages=messages,
    )
    messages.append({"role": "assistant", "content": resp.content})

    if resp.stop_reason != "tool_use":
        # No more tools requested — Claude's final answer is in resp.content.
        print(resp.content[-1].text)
        break

    # Run every tool Claude asked for and return the results in one turn.
    results = []
    for block in resp.content:
        if block.type == "tool_use":
            out = dispatch(soar, block.name, block.input)   # JSON-safe, never raises
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(out),
            })
    messages.append({"role": "user", "content": results})
```

A typical run of the prompt above has Claude call `query_records` (filter alerts
by `severity = Critical`, newest first), then `get_record` to read it, then
`create_record` on `comments` with its summary — each step a tool call pyfsr
executes against the live appliance. Because `dispatch()` returns errors as
`{"error": {...}}` data rather than raising, a bad call just comes back as a
tool result Claude can read and correct, and the loop keeps going.

```{tip}
Install the SDK with `pip install anthropic`. The same loop works against the
bundled MCP server or OpenAI's function calling — only the transport changes,
not the registry.
```

## OpenAI function calling

```{code-block} python
from pyfsr.agent.tools import to_openai_tools, dispatch

tools = to_openai_tools()                          # feed to chat.completions
result = dispatch(client, "search_records", {"module": "alerts"})
```

## Bundled MCP server

Install the extra and run the server over the tool registry:

```{code-block} bash
pip install "pyfsr[mcp]"
python -m pyfsr.agent.mcp
```

The server reads `FSR_*` environment variables (see
{doc}`authentication`) to build its client, and exposes the same registry of
tools to any MCP-compatible host.

### Two MCP servers: pyfsr vs fsr_playbooks

pyfsr ships the **runtime/admin** MCP server (this one); the separate
`fsr_playbooks` package ships the **playbook-authoring** MCP server
(`python -m fsr_playbooks.mcp_server` or `fsrpb mcp`). They share the same
`FSR_*` environment (`fsr_playbooks` builds its `FortiSOAR` client from the same
vars), so point both at one appliance. An agent that must *create modules,
configure connectors, run connector actions, and build playbooks* uses **both**:

| Task | Server | Tool(s) |
|------|--------|---------|
| Create custom modules | pyfsr | `create_module` → `publish` (grant RBAC via `grant_to`) |
| Configure connectors | pyfsr | `default_connector_config` → `validate_connector_config` → `upsert_connector_configuration` |
| Run connector actions | pyfsr | `run_connector_operation` (fsr_playbooks' `run_op` is the richer, safety-gated variant for authoring) |
| Build playbooks | fsr_playbooks | `compile_yaml` → `validate_yaml` → `push_playbook` → `dry_run_playbook`; debug with `why_did_playbook_fail`, `step_test` |
| Trigger & verify a run | pyfsr | create a triggering record → `wait_for_playbook_run` → `why_playbook_failed` |

pyfsr owns discovery, record CRUD, module admin, connector config, connector
*run*, and playbook *run* inspection/debugging. fsr_playbooks owns the playbook
DSL — compile/validate/push/dry-run, step-type and connector-op *discovery*
(`get_step_type`, `get_op_schema`, `find_operation`), single-step `step_test`,
and recipes. The two don't overlap on the four tasks, so running both gives an
agent the full create-configure-run-build loop with no gaps.

```{seealso}
The {mod}`pyfsr.agent.tools` and {mod}`pyfsr.agent.mcp` modules in the {doc}`../reference`
for the complete tool list and dispatch signatures.
```

## Authoring your own AI agent

Everything above drives the agents FortiSOAR *already ships*. FortiSOAR 8.0 also
lets you install **your own** agentic-AI agent — a reusable "skill" the
investigation orchestrator can route work to (compute a metric, retrieve records,
enrich an indicator, summarize a case). You'd author one when the built-in agents
don't cover a step your SOC repeats: a bespoke scoring formula, an in-house
enrichment source, a house-style report format. `pyfsr` packages, validates,
uploads, and exports these agents so you don't hand-build the zip or curl the
multipart endpoint.

### The package model

An AI agent ships as a zip whose **single top-level folder is the agent's
`name`**. Both the Fortinet-published agents and yours share this layout:

```text
metric-computation/
  info.json            # manifest: name, agentclass, version, config form, I/O
  agent.py             # the class named by info.json "agentclass"; implements act()
  __init__.py
  prompt.yaml          # prompt registry keyed by uuid
  config/
    memory.yaml        # allowed_tools: {<mcp_config_uuid>: [tool, ...]}
  images/
    small.png
    large.png
  constants.py         # optional helper modules
```

What each file does:

- **`info.json`** — the manifest. `name` must equal the folder name; `agentclass`
  must name a class defined in `agent.py`; `configuration.fields` is the config
  form the UI renders (config-type toggle, LLM-provider picker, MCP-server
  multiselect, masking agent); `inputformat`/`outputformat` document the JSON the
  agent consumes and returns.
- **`agent.py`** — subclasses the platform's `BaseAgent` and implements
  `act(input_data)`. It pulls a prompt by uuid
  (`self.get_prompt_by_uuid("<uuid>")`), `.format(**inputs)`s the templates, and
  calls the LLM. The uuid it references **must** exist in `prompt.yaml`.
- **`prompt.yaml`** — the prompts, keyed by uuid. Each entry is exactly what the
  UI's *Edit Prompt* screen edits: `name`, `description`, `system_instruction`
  (System Prompt Template), `user_instruction` (User Prompt Template),
  `response_format` (the JSON schema the model must return), and
  `validation_instruction`. Any `{placeholder}` in a template — `{query}`,
  `{data}`, `{verdict}`, `{key_findings}` — is filled by `act()` at call time.
- **`config/memory.yaml`** — the agent's MCP-tool allowlist: a map of registered
  **MCP-configuration uuid** → the list of tool names on that server the agent may
  call. This is the safety boundary — an agent can only reach tools it's explicitly
  granted here. An empty list binds the server without (yet) allowing any tool.

### Validate, pack, and upload

`pyfsr` models the whole package ({class}`~pyfsr.models.AgentPackage`) and checks
the mistakes that otherwise fail *silently on the appliance* — an `agentclass`
that isn't in `agent.py`, a prompt uuid the code references but the yaml omits, a
manifest icon that isn't in the bundle:

```{code-block} python
from pyfsr import FortiSOAR, pack_agent
from pyfsr.models import AgentPackage

# Inspect + validate a source folder before uploading (raises on any defect):
pkg = AgentPackage.from_dir("./my-agents/incident-scorer")
print(pkg.info.agentclass, pkg.info.version)
print(pkg.memory.mcp_configuration_uuids())   # which MCP servers it's wired to

client = FortiSOAR("soar.example.com", token="<api-key>")

# Import straight from a source directory — pyfsr validates + packs it on the fly:
result = client.ai.import_agent("./my-agents/incident-scorer", replace=True)
agent_uuid = result["uuid"]

# ...or pack once and upload the zip yourself:
zip_path = pack_agent("./my-agents/incident-scorer")   # -> ./my-agents/incident-scorer.zip
client.ai.import_agent(zip_path, replace=True)
```

`import_agent` accepts either a directory (validated and packed for you) or a
prebuilt `.zip`. `replace=True` overwrites an already-installed agent of the same
name+version; without it, re-importing an existing version is rejected.

The quickest way to author a new agent is to **clone a shipped one** as a
starting point:

```{code-block} python
client.ai.export_agent(agent_uuid, "./incident-scorer-backup.zip")
```

Unzip it, rename the folder + `info.json` `name`, edit `agent.py`/`prompt.yaml`,
then `import_agent` the folder back.

### Ensuring your agent is actually used

An imported agent lands **inactive** and on the default config. Three things make
the orchestrator route to it:

1. **Activate it** — an inactive agent is never selected:

   ```{code-block} python
   client.ai.activate_agent([agent_uuid])          # active=True by default
   ```

2. **Give it an LLM + MCP config** — if it shouldn't inherit the default, set its
   config so it has a reasoning profile and can reach the MCP tools its
   `memory.yaml` allowlist names:

   ```{code-block} python
   # grant one MCP server to the agent (read-modify-write of its config):
   client.ai.allow_mcp_server_for_agent("incident-scorer", "1.0.0", mcp_uuid)
   # or set the whole inner config (llm_provider, mcp_server, masking_agent):
   client.ai.update_agent_config("incident-scorer", "1.0.0",
                                 {"llm_provider": llm_uuid, "mcp_server": [mcp_uuid]})
   ```

   Confirm what's live with `client.ai.get_agent_config("incident-scorer", "1.0.0")`
   and `client.ai.describe_agent_mcp_servers(...)`.

3. **Verify it's eligible** — it should now appear active in the agent list, and
   the investigation pipeline (or a direct `run_agent`) can invoke it:

   ```{code-block} python
   [a["name"] for a in client.ai.list_agents(active=True)]
   client.ai.run_agent("incident-scorer", {"natural_language_task": "...", "data": {...}})
   ```

```{note}
Custom agents require FortiAI to be enabled (`client.ai.enable_features()`) and an
appliance at `fsrMinCompatibility` or newer — the shipped agents target 8.0.0.
```
