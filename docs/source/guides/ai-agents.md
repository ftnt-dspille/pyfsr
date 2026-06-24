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
```

Inspect any tool's full JSON-Schema (parameters, defaults, enums) at runtime
with `get_tool("query_records").input_schema`.

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
from pyfsr.tools import list_tools, tool_schemas, dispatch

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
from pyfsr.tools import to_anthropic_tools, dispatch

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
from pyfsr.tools import to_openai_tools, dispatch

tools = to_openai_tools()                          # feed to chat.completions
result = dispatch(client, "search_records", {"module": "alerts"})
```

## Bundled MCP server

Install the extra and run the server over the tool registry:

```{code-block} bash
pip install "pyfsr[mcp]"
python -m pyfsr.mcp
```

The server reads `FSR_*` environment variables (see
{doc}`authentication`) to build its client, and exposes the same registry of
tools to any MCP-compatible host.

```{seealso}
The {mod}`pyfsr.tools` and {mod}`pyfsr.mcp` modules in the {doc}`../reference`
for the complete tool list and dispatch signatures.
```
