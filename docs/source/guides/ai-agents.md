# AI & Agents

pyfsr ships a **framework-agnostic tool registry** -- a declarative catalogue of
core FortiSOAR operations (record CRUD, discovery, picklists, connectors,
playbook runs) as JSON-Schema tool definitions, plus a `dispatch()` that
executes a tool call against a live client and returns JSON-safe, token-trimmed
results.

It's deliberately transport-neutral (no MCP, no provider SDK), so the same
registry can feed Anthropic tool-use, OpenAI function calling, the bundled MCP
server, or a home-grown agent loop.

```{seealso}
End-to-end FortiAI / FortiSIEM-MCP examples:
[`fortisiem_mcp_setup_and_test.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/fortisiem_mcp_setup_and_test.py),
[`trigger_ai_investigation.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/trigger_ai_investigation.py),
and [`run_single_ai_agent.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/run_single_ai_agent.py).
See the [examples index](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/README.md) for the full set.
```

```{hint}
Building an AI agent with **external coding tools** (GitHub Copilot, Cursor,
Windsurf, Claude Code)? Start from the repo's
[`AGENTS.md`](https://github.com/ftnt-dspille/pyfsr/blob/main/AGENTS.md) -- it
covers setup (`EnvConfig.from_env`, `instances.toml`), the `run_and_wait`
trigger-and-wait primitive, playbook authoring CLI, and the full tool registry
in one place.
```

## Why use it

Wiring an LLM to FortiSOAR by hand means hand-writing JSON-Schema for every
operation, normalizing Hydra envelopes, trimming huge records down to fit a
context window, and turning every HTTP error into something the model can read.
The registry does all of that for you:

- **Discovery built in.** The model can learn the appliance at runtime --
  `list_modules` → `describe_module` → act -- instead of you hard-coding field
  names and module types that differ per deployment.
- **Token-trimmed results.** Every tool supports `summary=true` / `fields=[...]`
  so a 60-field alert doesn't blow the context window when an agent is scanning
  dozens of records.
- **Picklist resolution.** Agents pass friendly values (`"High"`) and the tool
  maps them to the IRIs the API actually requires -- the single most common
  cause of failed writes.
- **Errors as data, not exceptions.** Every failure returns a structured
  `{"error": {...}}` the model can read and self-correct from, so one bad call
  doesn't kill the agent loop.
- **Write once, run anywhere.** The same registry feeds Claude, OpenAI, MCP, or
  your own loop -- no per-provider glue.

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
  - Validate a config against the schema before submitting -- returns `{valid, missing, invalid, ...}`.
* -
  - `create_connector_configuration`
  - Create a named config; `exist_ok=true` delegates to upsert, `autofill=true` fills schema defaults.
* -
  - `update_connector_configuration`
  - Update an existing config by `config_id`.
* -
  - `upsert_connector_configuration`
  - Idempotent create-or-replace by name -- the safe default for deploy scripts.
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
token-trimmed result -- never raises; a failure comes back as `{"error": {...}}`.
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
>>> len(hits["members"]), hits["members"][0]["name"]
(1, 'Response Capture Test Alert')
>>> conns = dispatch(client, "list_connectors", {})
>>> conns["connectors"][:3]
['code-snippet -- Code Snippet v2.2.1 (1 config)', 'mitre-attack -- MITRE ATT&CK v2.0.2 (1 config)', 'smtp -- SMTP v2.6.0 (1 config)']
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
against the same captured alert, so their return shapes are doctested too -- an
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
resolve (typo, wrong casing) comes back as a structured, actionable error --
field, bad value, and the valid options -- instead of an opaque box 400, because
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
(`list_picklists`, `get_picklist_values`) are doctested above too -- a model uses
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

### Running a single agent

An investigation runs the whole pipeline. When you only need one question
answered -- enrich this indicator, query the SIEM, look up a ticket -- call the
agent directly with `run_agent`. It is far cheaper, and returns the agent's own
`outputformat` (`answer` / `evidence` / `confidence`) rather than an
investigation's `summary`/`hypotheses`.

You never have to guess the payload: every agent publishes its input contract,
and `run_agent` validates against it before spending an LLM call.

```{doctest}
>>> schema = client.ai.agent_input_schema("ioc-enrichment")   # doctest: +SKIP
>>> sorted(schema)                                            # doctest: +SKIP
['ioc', 'question']
>>> result = client.ai.run_agent(                             # doctest: +SKIP
...     "ioc-enrichment",
...     {"question": "Is this IP known to be malicious?",
...      "ioc": [{"type": "IP Address", "value": "8.8.8.8"}]},
...     wait=True,
... )
>>> result.answer, result.confidence                          # doctest: +SKIP
('No', '95%')
```

Omit a required key and it fails locally, naming the key, without calling the
API. Pass `validate=False` to skip the schema lookup when you already know the
shape.

Two things that bite:

- The trigger goes to `/api/ai/agents/{name}/trigger` (**plural**). The service
  mounts the same router under `/ai/triage` too, but the front door only
  authorises the `agents` form -- the `triage` form is rejected with a bare
  `Access Denied` no matter what role you hold.
- The caller needs `execute.ai_agents` (and `read.ai_agents` to read the input
  schema). A missing permission looks identical to a wrong path.

With `wait=True`, a timeout returns the latest result with a non-terminal
`status` rather than raising -- check `result.status` before trusting `answer`.
See [`examples/run_single_ai_agent.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/run_single_ai_agent.py).

### Chat and the Orchestrator (8.0.1)

`client.ai.chat()` opens a multi-turn session with the SOC chat assistant (the
Conversation Agent), exactly as the in-app assistant drives it:
`POST /api/ai/agents/conversation/trigger` with an `X-CHAT-SESSION-ID` header,
then poll the task. The session carries `previous_response_id` / `request_id`
between turns. (`/api/ai/chat/` also exists, but the UI does not call it and its
request shape does not match the agent's.)

`client.ai.orchestrate()` is the same protocol against the Orchestrator Agent.
It can pause a turn for a **clarification** (a missing value) or an
**approval** (a state-changing action it inferred). A paused turn has
`turn.is_paused`; answer it on the same session so the plan resumes rather than
starting over.

```{doctest}
>>> s = client.ai.orchestrate()                                        # doctest: +SKIP
>>> turn = s.ask("Block the malicious IP address on the firewall.")    # doctest: +SKIP
>>> turn.needs_clarification, turn.pending.question                    # doctest: +SKIP
(True, 'Cannot block ... Please specify the IP address to proceed.')
>>> turn = s.reply("198.51.100.23")                                    # doctest: +SKIP
>>> # an approval pause is answered with s.approve() / s.deny()
```

On 8.0.1 the Conversation Agent does not call the Orchestrator, so
`orchestrate()` is the only way to reach it.

The playbook designer's and connector wizard's assistants use the same
protocol. `client.ai.playbook_assistant()` returns an outline (and keeps its
`playbook_context`); `generate_steps()` turns the outline into designer steps,
the way the designer does. Nothing is saved.

```{doctest}
>>> pb = client.ai.playbook_assistant()                                 # doctest: +SKIP
>>> outline = pb.ask("On a new Critical alert, look up the source IP in VirusTotal ...")  # doctest: +SKIP
>>> steps = pb.generate_steps()                                         # doctest: +SKIP
>>> steps.playbook_steps                                                # doctest: +SKIP
```

Step generation makes one LLM call per step and resends the whole context each
time. A 7-step playbook used about 330k FortiAI tokens on 8.0.1, and one run
failed on malformed JSON. `client.ai.connector_assistant()` is a cheap guided
conversation (about 6k tokens a turn) that writes working connector code. Its
own import step fails on 8.0.1, so ask it to show the files and install them
with `client.connectors.install_from_dir`.

### Insights (8.0.1)

An Insight is a question about your data ("which alert sources produced the
most Critical alerts this week?") that fsr-ai plans into agent steps, runs, and
summarizes. `client.ai.insights` drives the Insight cards widget's flow:

```{doctest}
>>> run = client.ai.insights.create("Critical alert sources",                    # doctest: +SKIP
...     query="Which alert sources produced the most Critical alerts in the last 7 days?")
>>> run.result["concise_summary"], run.insight_id                               # doctest: +SKIP
>>> run = client.ai.insights.run_template("High Risk Active Alerts")             # doctest: +SKIP
```

`create` generates a chain of thought and a plan (two LLM calls), refuses an
infeasible plan, executes it, and saves it as an `insights` record. The shipped
`insight_templates` carry ready plans; `run_template` executes one without the
planning calls. `list`, `get`, `delete` and `trigger` (re-run a saved insight
now) cover the rest. `socrole` (`SOC Analyst`, `SOC Manager`, `Threat
Analyst`, `Infrastructure Admin`) shapes both the plan and the summary. On
8.0.1 a create took about 45 s and 8k FortiAI tokens.

### Traces (8.0.1)

Every agent run records a trace -- the data behind the in-app Trace Flow panel.
A run's trace id is its task id.

```{doctest}
>>> tree = client.ai.traces.execution_tree(task_id)           # doctest: +SKIP
>>> tree.total_steps, tree.count_by_type()                    # doctest: +SKIP
(173, {'AGENT': 57, 'LLM': 29, 'TOOL': 17, ...})
>>> client.ai.traces.tokens(task_id)                          # doctest: +SKIP
{'input_tokens': 35093, 'output_tokens': 5805, 'total_tokens': 40898, 'llm_calls': 29}
>>> [(s.provider, s.model, s.usage) for s in client.ai.traces.llm_calls(task_id)][:1]  # doctest: +SKIP
[('FSRAI', None, {...})]
```

#### An investigation, question by question

`traces.investigation(task_id)` rebuilds an alert investigation from its
traces: one `AgentRun` per sub-agent run, each with the question it was asked,
its answer/evidence/confidence, and every tool call it made. Each call records
the MCP server that ran it, its real output, and `selected_by` -- who decided it
should run:

| `selected_by` | Meaning |
|---|---|
| `code` | No LLM step asked for it; the agent's code ran a fixed lookup |
| `llm` | An LLM step picked it without having seen any tool result |
| `llm-chained` | An LLM step picked it after reading earlier tool results |

```{doctest}
>>> inv = client.ai.traces.investigation(task_id)                        # doctest: +SKIP
>>> for run in inv.questions:                                              # doctest: +SKIP
...     print(run.agent, run.answer, [(c.tool_name, c.selected_by) for c in run.tool_calls])
Threat Intelligence Provider No [('get_indicators', 'code'), ('enrich_indicator', 'code'),
  ('get_alerts_linked_to_indicators', 'llm'), ...]
>>> m = inv.metrics()                                                      # doctest: +SKIP
>>> m["selected_by"], m["questions_multi_tool"], m["questions_chained"]    # doctest: +SKIP
({'code': 24, 'llm': 17}, 8, 0)
```

On 8.0.0 each question got exactly one LLM-chosen tool call. A raw count of tool
calls per question overstates the change in 8.0.1, because most provider agents
also run fixed lookups from code. `questions_chained` is the measure of an agent
reading a result and calling again.

8.0.1 no longer writes `llm_activity_logs` for investigations, so
`investigation_tool_calls()`, `attribute_tool_calls()` and `find_investigations()`
read the traces when the box has them (`ToolCall.source == "traces"`) and fall
back to the logs on 8.0.0. `tool_usage()` reads only the logs.

Known 8.0.1 server defects the client works around or documents:

- Paging with a cursor fails server-side, so `traces.iter()` stops after the
  first page with a warning (use `page_size=500`).
- `/spans/{id}/tree` 404s for any non-root span; `span_subtree()` falls back to
  the span's trace tree.
- `/spans/{id}/lineage` always 500s.
- LLM spans record provider `FSRAI` and an empty model for FortiAI-proxy calls.
- `purge_older_than()` deletes **every** trace older than the cutoff -- there
  is no per-trace delete.

### Scoring investigations (`pyfsr.ai_eval`, 8.0.1)

A trace shows what an investigation did, but not whether it did the right
thing. `pyfsr.ai_eval` runs investigations against stub MCP servers with known
answers, so each run can be scored.

A **suite** (YAML; the bundled one is `pyfsr/ai_eval/suites/default.yaml`)
defines two things:

- **Stub servers**, each registered in FortiSOAR as its own MCP server:
  - five evidence servers (SIEM, EDR, identity, CMDB, threat intel);
  - two distractors (cloud billing, marketing CRM) that are no help in any
    scenario.

  Each tool answers from rules keyed on its arguments. A call about an entity
  the scenario has no data for gets an empty default answer.
- **Scenarios**. Each has:
  - an alert;
  - the calls a good investigation makes;
  - the facts it should surface;
  - the right verdict.

  Some facts can only be reached by *pivoting*: the entity is learned from one
  tool's result and then looked up in another. In `fin-ws-lateral`, the EDR
  shows an SMB connection to a file server, and only a SIEM search on that
  server finds the payroll exfiltration.

```bash
pyfsr ai-eval deploy --instance lab          # connector + fixtures + MCP servers + agent allowlists
pyfsr ai-eval run --instance lab --runs 3 --out report.json
pyfsr ai-eval log --instance lab             # the stubs' own call log
pyfsr ai-eval teardown --instance lab
```

`deploy` installs the bundled `fsr-ai-eval-stub` connector. Like the Microsoft
Teams connector's bot listener, the connector starts a local listener when it
is configured. The listener is a stdlib-only MCP server on `127.0.0.1`, so no
extra packages or network paths are needed; fsr-ai reaches it on the same box.
Each stub is then registered and allowed for the provider agents the suite
names. Custom connectors need connector development mode on.

Each run is scored from its trace:

| Score | Meaning |
|---|---|
| `verdict` | 1 for an expected classification, 0.5 for a partial one (e.g. *Suspicious*) |
| `tool_recall` | Share of the required calls that were made |
| `query_accuracy` | Share of the required calls made with the right entity |
| `chain_recall` | Share of the pivot calls made with the right entity |
| `precision` | Share of stub calls that found data (not a distractor, an unknown entity or a refusal) |
| `fact_retrieval` / `fact_use` / `fact_in_summary` | Facts some tool returned / that reached an answer or the summary / that reached the summary |
| `lost_facts` | Facts that were retrieved but never used |
| `composite` | Weighted mean (`pyfsr.ai_eval.WEIGHTS`) |

Over repeated runs, `aggregate()` reports:

- the spread of each score;
- the verdict distribution;
- how often each expected call and fact was hit;
- which tools were actually used, and who chose them (`selected_by`).

Together these show which servers the agents should have used, which ones they
did use, and where the evidence was lost.

```{doctest}
>>> from pyfsr.ai_eval import load_suite, deploy, run_suite           # doctest: +SKIP
>>> suite = load_suite()                                               # doctest: +SKIP
>>> deploy(client, suite)                                              # doctest: +SKIP
>>> report = run_suite(client, suite, runs=3)                          # doctest: +SKIP
>>> report["summary"]["scenarios"]["fin-ws-lateral"]["chain_recall"]   # doctest: +SKIP
{'mean': 0.0, 'min': 0.0, 'max': 0.0, 'stdev': 0.0}
```

### Token cost of an investigation

Every run also records its tokens and a price. An investigation's
`InvestigationTrace.tokens` holds the root trace's totals, which already
include every sub-agent run. `tokens_by_agent()` splits them per agent, so the
parts add up to the total. When the default LLM profile goes through the
FortiAI proxy, `run` also reads the FortiAI balance before and after each
investigation (`client.ai.token_balance()`). The drop is the run's
`metered_tokens`, and the cost is priced from it when present. Anything else
using FortiAI on the appliance at the same time inflates it; pass `--no-meter`
to skip it.

`TokenPricing` defaults to FortiAI's terms: 5,000,000 tokens a month included
per appliance, and top-ups at $100 per 500,000 tokens (`usd_per_million=200`).
For a profile on your own OpenAI, Anthropic or Gemini key, set a blended rate,
or separate input and output rates. The aggregate's `budget` gives:

- tokens and USD per investigation;
- how many investigations fit in the free monthly allowance.

```bash
pyfsr ai-eval balance --instance lab               # allowance, remaining, used
pyfsr ai-eval cost --instance lab <task_id> ...     # tokens per agent + cost, any finished investigation
pyfsr ai-eval run --instance lab --usd-per-million 200 --free-tokens 5000000
```

Which model an investigation uses comes from the LLM profiles
(`client.ai.list_llm_configs()`) and each agent's config
(`client.ai.get_agent_config(name, version).config.llm_provider`, a profile
uuid). The stock 8.0.1 profiles both go through the `fortinet-fortiai-proxy`
connector:

- *Low Reasoning* (the default) runs `gpt-4.1`;
- *High Reasoning* runs `gpt-5.4`, used by the hypothesis, verdict and
  metric-computation agents.

A profile's `provider` is one of:

| `provider` | Billing | Setup |
|---|---|---|
| `fortisoar` | through a connector: `fortinet-fortiai-proxy` (FortiAI tokens) or `openai` (your key) | `config={"connector_name", "connector_config_id"}` |
| `openai` / `anthropic` / `gemini` | your provider account directly | `modelname` + `apikey` |

Only installed connectors on fsr-ai's allow list (`fortinet-fortiai-proxy` and
`openai` as shipped) appear in `list_providers()`. In the fsr-ai 8.0.0
source, the native clients pass only the API key, so a profile's `baseurl` is
stored but not used. An Azure OpenAI or self-hosted endpoint therefore has to
go through a connector. `verify_llm_config()` only works for `openai`
profiles. A native profile has no FortiAI balance, so `token_balance()` raises
`ValueError` and runs are priced from the traces.

### Switching to a third-party LLM

The UI path is **System Configuration → FortiAI**, which opens the *AI
Configuration* wizard. pyfsr does the same steps:

```bash
pyfsr llm status --instance lab                     # profiles, wizard providers, each agent's profile
OPENAI_API_KEY=... pyfsr llm setup --instance lab --profile "OpenAI GPT-4.1" --model GPT-4.1
pyfsr llm assign --instance lab "OpenAI GPT-4.1"   # every agent; writes an undo snapshot
pyfsr ai-eval run --instance lab --usd-per-million <your blended rate>
pyfsr llm restore --instance lab ~/.pyfsr/llm-snapshots/<file>.json
```

`setup_connector_llm()` works in four steps:

1. installs the connector from Content Hub;
2. saves a connector configuration with the key and model;
3. health-checks it and sends a one-line test completion (`--no-verify` skips
   it). The health check passes on a valid key whose account has no credits;
   the test completion catches that before any agent is switched;
4. creates a `fortisoar` reasoning profile that points at the configuration.

fsr-ai sends no model name, so the connector configuration decides the model;
use one configuration per model. The key comes from an environment variable or
a file (`--api-key-file`), never from the command line. For Azure OpenAI, pass
`--azure-endpoint` and `--azure-deployment`.

`assign_llm()` sets `llm_provider` in each agent's config row, which is what
investigations use. It also writes the agent record through
`POST /api/ai/agent/llm/config`, as the wizard does, where the API gateway
allows it; on 8.0.1 the gateway returns 403 for API sessions. It returns the
previous assignments for `restore_llm_assignments()`.

On 8.0.1, `/api/ai/llm/allowed-providers` and the wizard list only
`fortinet-fortiai-proxy` unless the `fortiai-configurations` key-store record
has `bringYourLLM: {"enabled": true}`. That flag only changes the UI: with it
off, an investigation still calls the `openai` connector
(live-verified on 8.0.1).

### Step timeout and retry (8.0.1)

`client.playbooks.set_step_timeout(step, operation_timeout=5, retry=2)` writes
the designer's *Timeout* option (`arguments.timeout`) on a connector step. On
8.0.1, retries fire on a **timeout only**, not when the operation raises an
error. The appliance stores any value unchecked, so the designer's rule (whole
seconds, total under 1800s) is enforced client-side.

## Use case: triage an alert end-to-end

A SOC analyst asks an agent *"Triage the latest critical alert and tell me if
it's a real threat."* With the registry attached, the model can carry out the
whole workflow itself -- no bespoke code per step:

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
structured `{"error": {...}}` dict -- never a raised exception** -- so an agent
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
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        tools=tools,
        messages=messages,
    )
    messages.append({"role": "assistant", "content": resp.content})

    if resp.stop_reason != "tool_use":
        # No more tools requested -- Claude's final answer is in resp.content.
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
`create_record` on `comments` with its summary -- each step a tool call pyfsr
executes against the live appliance. Because `dispatch()` returns errors as
`{"error": {...}}` data rather than raising, a bad call just comes back as a
tool result Claude can read and correct, and the loop keeps going.

```{tip}
Install the SDK with `pip install anthropic`. The same loop works against the
bundled MCP server or OpenAI's function calling -- only the transport changes,
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

### MCP client config (Copilot, Cursor, Windsurf, Claude)

The pyfsr MCP server runs over stdio, so any MCP-compatible client can drive
it. Point your tool at `python -m pyfsr.agent.mcp` with the `FSR_*`
environment set:

**Claude Desktop / Claude Code** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "pyfsr": {
      "command": "python",
      "args": ["-m", "pyfsr.agent.mcp"],
      "env": {
        "FSR_BASE_URL": "https://fortisoar.example.com:13000",
        "FSR_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

**Cursor / Windsurf** (`.cursor/mcp.json` or `~/.codeium/windsurf/mcp_config.json`):
```json
{
  "mcpServers": {
    "pyfsr": {
      "command": "python",
      "args": ["-m", "pyfsr.agent.mcp"],
      "env": {
        "FSR_BASE_URL": "https://fortisoar.example.com:13000",
        "FSR_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

**GitHub Copilot** (`.vscode/mcp.json` in the repo):
```json
{
  "servers": {
    "pyfsr": {
      "command": "python",
      "args": ["-m", "pyfsr.agent.mcp"],
      "env": {
        "FSR_BASE_URL": "https://fortisoar.example.com:13000",
        "FSR_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

For username/password instead of API key, use `FSR_USERNAME` and `FSR_PASSWORD`
in place of `FSR_API_KEY`. For multiple appliances, see the
**Multi-instance config** section below.

### Multi-instance config (`instances.toml`)

When you manage multiple FortiSOAR appliances, list them in
`~/.pyfsr/instances.toml` and switch with `--instance`:

```toml
[instances.prod]
base_url = "https://fortisoar.example.com:13000"
username = "csadmin"
password = "<password>"
verify_ssl = false

[instances.dev]
base_url = "https://dev.fortisoar.example.com:13000"
api_key = "<dev-api-key>"
```

The CLI reads this automatically:

```bash
pyfsr instances list          # show configured aliases
pyfsr instances show prod     # resolved settings (no secrets)
pyfsr instances check         # connect to each; exit 1 if any fails
```

Any `pyfsr` CLI command accepts `--instance prod` to target a specific
appliance. The MCP server picks the instance marked `default = true` (or the
first listed).

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
DSL -- compile/validate/push/dry-run, step-type and connector-op *discovery*
(`get_step_type`, `get_op_schema`, `find_operation`), single-step `step_test`,
and recipes. The two don't overlap on the four tasks, so running both gives an
agent the full create-configure-run-build loop with no gaps.

```{seealso}
The {mod}`pyfsr.agent.tools` and {mod}`pyfsr.agent.mcp` modules in the {doc}`../reference`
for the complete tool list and dispatch signatures.
```

## Authoring your own AI agent

Everything above drives the agents FortiSOAR *already ships*. FortiSOAR 8.0 also
lets you install **your own** agentic-AI agent -- a reusable "skill" the
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

- **`info.json`** -- the manifest. `name` must equal the folder name; `agentclass`
  must name a class defined in `agent.py`; `configuration.fields` is the config
  form the UI renders (config-type toggle, LLM-provider picker, MCP-server
  multiselect, masking agent); `inputformat`/`outputformat` document the JSON the
  agent consumes and returns.
- **`agent.py`** -- subclasses the platform's `BaseAgent` and implements
  `act(input_data)`. It pulls a prompt by uuid
  (`self.get_prompt_by_uuid("<uuid>")`), `.format(**inputs)`s the templates, and
  calls the LLM. The uuid it references **must** exist in `prompt.yaml`.
- **`prompt.yaml`** -- the prompts, keyed by uuid. Each entry is exactly what the
  UI's *Edit Prompt* screen edits: `name`, `description`, `system_instruction`
  (System Prompt Template), `user_instruction` (User Prompt Template),
  `response_format` (the JSON schema the model must return), and
  `validation_instruction`. Any `{placeholder}` in a template -- `{query}`,
  `{data}`, `{verdict}`, `{key_findings}` -- is filled by `act()` at call time.
- **`config/memory.yaml`** -- the agent's MCP-tool allowlist: a map of registered
  **MCP-configuration uuid** → the list of tool names on that server the agent may
  call. This is the safety boundary -- an agent can only reach tools it's explicitly
  granted here. An empty list binds the server without (yet) allowing any tool.

### Validate, pack, and upload

`pyfsr` models the whole package ({class}`~pyfsr.models.AgentPackage`) and checks
the mistakes that otherwise fail *silently on the appliance* -- an `agentclass`
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

# Import straight from a source directory -- pyfsr validates + packs it on the fly:
result = client.ai.import_agent("./my-agents/incident-scorer", replace=True)
agent_uuid = result["uuid"]

# ...or pack once and upload the zip yourself:
zip_path = pack_agent("./my-agents/incident-scorer")   # -> ./my-agents/incident-scorer.zip
client.ai.import_agent(zip_path, replace=True)
```

For the whole install in one call, `client.ai.install_agent(path)` turns on the
*Advanced Development Settings* agent toggle if it is off, imports with
`replace=True`, activates the agent and returns it. A replaced agent's new code
is live without restarting fsr-ai. `client.ai.uninstall_agent(name)` removes an
agent's record, config and files (built-in agents need `force=True`).

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

1. **Activate it** -- an inactive agent is never selected:

   ```{code-block} python
   client.ai.activate_agent([agent_uuid])          # active=True by default
   ```

2. **Give it an LLM + MCP config** -- if it shouldn't inherit the default, set its
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

3. **Give the investigation planner a reason to ask it** (8.0.1). Tag the agent
   `Triage` in `info.json`, then add a row naming its source to the planner's
   tool table. The planner writes one question per active row *before* it picks
   agents, so without a row a custom agent never gets a question:

   ```{code-block} python
   client.ai.investigation_tools()                      # the current table
   client.ai.set_investigation_tool("Authorized security testing", "Pentest Registry", "host")
   client.ai.reset_investigation_tools()                # back to the built-in table
   ```

   The table lives in the Organization Context record `INFRA_INFO/TOOL_LIST` and
   replaces the built-in one, so `set_investigation_tool` seeds it with the
   built-in rows. Also, `act()` must call `self.initialize()` first, as the
   shipped agents do; fsr-ai does not call it.

4. **Verify it's eligible** -- it should now appear active in the agent list, and
   the investigation pipeline (or a direct `run_agent`) can invoke it:

   ```{code-block} python
   [a["name"] for a in client.ai.list_agents(active=True)]
   client.ai.run_agent("incident-scorer", {"natural_language_task": "...", "data": {...}})
   ```

```{note}
Custom agents require FortiAI to be enabled (`client.ai.enable_features()`) and an
appliance at `fsrMinCompatibility` or newer -- the shipped agents target 8.0.0.
```
