# Playbook YAML Syntax Reference

This is the authoring reference for the **YAML playbook DSL** that the
`fsr_playbooks` compiler (the `pyfsr[playbooks]` extra) accepts. It is the
companion to the narrative {doc}`playbook-authoring` guide: that page shows the
*workflow* (write → compile → deploy); this page is the *syntax* -- every
top-level key, every step `type`, and the friendly fields each step accepts.

The DSL is a thin, friendly layer over FortiSOAR's wire format: you write short
`type:` names and friendly keys like `module:` / `vars:` / `when:`, and the
compiler expands them into the canonical workflow/step/route JSON the import API
expects. Anything you don't recognise on the wire, you can usually still set by
its canonical key -- the compiler only rejects *unknown* keys, never canonical
ones.

```{note}
The step catalogue is owned by the compiler and validated against a packaged
reference DB of real FortiSOAR step types. When in doubt, `pyfsr playbook
validate <file>` is the source of truth -- it reports unknown keys, missing
fields, and wrong shapes with a `path:` into your YAML.
```

## What the compiler turns a step into

The friendly `type:` / `vars:` / `next:` you write expand into the canonical
workflow/step/route JSON the import API expects. Compiling is offline (no
network), so you can inspect exactly what gets sent before deploying:

```{doctest}
>>> from pyfsr.authoring import compile_playbook_yaml
>>> result = compile_playbook_yaml('''
... name: wire-shape-demo
... description: show one step's canonical JSON
... playbooks:
...   - name: Demo
...     steps:
...       - name: Start
...         type: start
...         next: Set Greeting
...       - name: Set Greeting
...         type: set_variable
...         vars:
...           greeting: hello from pyfsr
...           count: 3
... ''')
>>> result.ok
True
>>> wf = result.fsr_json["data"][0]["workflows"][0]
>>> step = wf["steps"][1]
>>> step["@type"], step["name"], step["arguments"]
('WorkflowStep', 'Set Greeting', {'greeting': 'hello from pyfsr', 'count': 3})
>>> [r["name"] for r in wf["routes"]]
['Start -> Set Greeting']
```

The `set_variable` step type maps to a fixed `stepType` IRI (a UUID the
compiler resolves from its catalog); the friendly `vars:` mapping lands verbatim
in `arguments`, and `next:` becomes a `WorkflowRoute` whose `name` is
`"<source> -> <target>"`. The volatile fields -- `uuid`, `top`/`left` (canvas
position), and the `/api/3/workflow_steps/<uuid>` IRIs in each route -- are
compiler-generated and stable across runs, so you only need to author the
friendly shape on the left.

## File structure

A playbook file describes **one collection** and the **workflows** (playbooks)
inside it:

```yaml
collection: My Collection          # required -- the collection name
description: What this does         # optional
visible: true                       # optional -- show in the UI (default true)

playbooks:                          # required -- one or more workflows
  - name: My Playbook               # required -- workflow name
    is_active: false                # optional -- ship disabled (default true)
    trigger: start                  # optional -- trigger step type (default "start")
    parameters: []                  # optional -- referenced-playbook input params
    steps:                          # required -- the step list
      - name: Start
        type: start
        next: Do Something
      - name: Do Something
        type: set_variable
        vars: {greeting: hello}
```

| Top-level key | Meaning |
|---|---|
| `collection` | Collection display name (wrap mode -- replaces the whole collection). |
| `into_collection` | Collection name (per-playbook mode -- only touches listed playbooks within a shared target). Mutually exclusive with `collection`; if neither is set, defaults to `00 - FSR Studio`. |
| `description` | Free-text description. |
| `visible` | Whether the collection shows in the UI (default `true`). |
| `tags` | List of tag names for the collection. |
| `exported_tags` | List of tag names used by the Data Ingestion Wizard. |
| `uuid` | Collection UUID (round-trip preservation; omitted for new collections). |
| `playbooks` | List of workflows; each is one playbook. |

| Playbook key | Meaning |
|---|---|
| `name` | Workflow name (required). |
| `description` | Free-text description. |
| `tag` / `tags` | Tag string or list of tag names. |
| `is_active` | If `true` (the default), the playbook is **live** and its trigger fires. Set `false` to ship a disabled draft. |
| `debug` | Verbose runtime tracing (default `false`). |
| `is_private` | Private to owner teams; derived from `owners` when omitted. |
| `owners` | List of team names or IRIs for private visibility. |
| `priority` | Workflow priority: `High` (default), `Medium`, or `Low`. |
| `trigger` | Short-name of the trigger step type; defaults to `start`. Usually inferred from the first `start*` step instead. |
| `parameters` | Input parameters for a referenced playbook -- a list of names or a mapping `{name: type}`. |
| `steps` | The step list (see below). |
| `annotations` | Canvas notes/blocks (round-trip preservation). |
| `uuid` | Playbook UUID (round-trip preservation; omitted for new playbooks). |

## Steps: common shape

Every step has a `name`, a `type`, and (except terminals/decisions) a `next:`
pointing at the next step's `name`:

```yaml
- name: Enrich IP          # unique within the playbook; also the jinja slug
  type: connector
  next: Decide             # name of the next step
  connector: virustotal    # type-specific keys go at the step top level
  operation: query_ip
  params:
    ip: "{{ vars.input.records[0].sourceIp }}"
```

- **`name`** is also how you reference a step's output downstream:
  `{{ vars.steps.Enrich_IP.data }}` (spaces become underscores).
- **`next`** wires the linear flow. `decision` / `manual_input` steps put `next:`
  on each branch instead (see those types).
- Terminal steps (`stop` / `end`) omit `next`.

## Step types

Friendly `type:` → canonical FortiSOAR step type (from the compiler's alias
table). Use the friendly name on the left:

| `type:` | FortiSOAR step | Purpose |
|---|---|---|
| `start` | `cybersponse.abstract_trigger` | Manual / referenced trigger (the default Start). With `module:`, becomes a manual Execute-menu trigger (`cybersponse.action`). |
| `start_on_create` | `cybersponse.post_create` | **Auto-fire when a record is created** in a module. |
| `start_on_update` | `cybersponse.post_update` | Auto-fire when a record is updated. |
| `start_on_delete` | `cybersponse.post_delete` | Auto-fire when a record is deleted. |
| `api_endpoint` | `cybersponse.api_call` | Expose the playbook at `POST /api/triggers/1/<route>`. |
| `set_variable` | `SetVariable` | Define `vars.*` values. |
| `decision` | `Decision` | Branch on conditions. |
| `connector` | `Connectors` | Run a connector operation. |
| `find_record` | `FindRecords` | Query records of a module. |
| `create_record` | `InsertData` | Create a record. |
| `update_record` | `UpdateRecord` | Update a record. |
| `delete_record` | `Connectors` (`cyops_utilities.make_cyops_request`) | Delete a record (via a utility DELETE call). |
| `ingest_bulk_feed` | `IngestBulkFeed` | Bulk feed insert (bypasses on-create triggers). |
| `delay` | `Delay` | Wait. |
| `manual_input` | `ManualInput` | Pause for human input. |
| `approval` | `Approval` | Approval gate. |
| `code_snippet` | `CodeSnippet` | Run a Python snippet. |
| `send_email` | `SendMail` | Send an email (via the SMTP connector). |
| `create_task` | `ManualTask` | Create a task record. |
| `workflow_reference` | `WorkflowReference` | Call another playbook (same collection). |
| `trigger_tenant_playbook` | `RemotePlaybookReference` | Call a playbook in another tenant. |
| `utilities` | `Connectors` (`cyops_utilities`) | Run a utility operation (convert_json_to_csv, compute_hash, ...). |
| `stop` / `end` | `Connectors` (`cyops_utilities.no_op`) | First-class no-op terminal. |

### `start` -- manual trigger

```yaml
- name: Start
  type: start
  next: First Step
```

Bind a `module:` to make it a manual Execute-menu trigger on that module's
records:

```yaml
- name: Start
  type: start
  module: alerts
  next: First Step
```

### `start_on_create` / `start_on_update` / `start_on_delete` -- record triggers

Auto-fire when a record is created (or updated, or deleted) in `module:`. Set
the playbook's `is_active: true` (the default) for it to actually fire.

```yaml
- name: Start
  type: start_on_create
  module: heists                 # required -- the module to watch
  next: Stamp Status
```

Add a `when:` field-based filter to fire only on records matching a query
(`logic` + `filters`, each `{field, op, value}`):

```yaml
- name: Start
  type: start_on_create
  module: heists
  when:
    logic: AND
    filters:
      - {field: takeUsd, op: gt, value: 1000000}
  next: Stamp Status
```

For `start_on_update`, `op: changed` (no `value`) fires when the listed field
changes. For `start_on_delete`, the deleted record(s) arrive at
`vars.input.records`. The compiler expands `when:` into the canonical
`fieldbasedtrigger` envelope (`resource`/`resources`, `step_variables`,
`triggerOnSource`, ...) for you.

```{important}
A `start_on_create` / `start_on_update` playbook only fires when the workflow is
`is_active: true`. The triggering record arrives as
`{{ vars.input.records[0] }}`.
```

### `set_variable`

Write a top-level `vars:` mapping (not `arguments:`):

```yaml
- name: Set Inputs
  type: set_variable
  vars:
    greeting: hello from pyfsr
    source_ip: "{{ vars.input.records[0].sourceIp }}"
  next: Next Step
```

### `decision`

Branches carry their own `next:` per condition entry -- there is no step-level
`next:` or `branches:`:

```yaml
- name: Big Score?
  type: decision
  conditions:
    - condition: "{{ vars.input.records[0].takeUsd > 1000000 }}"
      next: Alert The Boss
    - default: true
      next: Log It
```

### `connector`

Connector op, operation name, and params go at the **step top level** (not
nested under `arguments:`). Resolve the exact `connector` / `operation` / param
names with the discovery tools (`pyfsr playbook` MCP / `find_operation`) -- don't
guess them:

```yaml
- name: Enrich IP
  type: connector
  connector: virustotal
  operation: query_ip
  params:
    ip: "{{ vars.input.records[0].sourceIp }}"
  next: Decide
```

### `find_record` / `create_record` / `update_record`

```yaml
- name: Find Open Heists
  type: find_record
  module: heists
  query: {logic: AND, filters: [{field: status, operator: eq, value: Open}]}

# Or use the friendly query fields:
- name: Find Recent
  type: find_record
  module: heists
  filters:
    - {field: status, op: eq, value: Open}
  logic: AND
  limit: 50
  sort:
    - {field: createDate, direction: DESC}
  select: [name, status, takeUsd]
  next: Decide

- name: Log It
  type: create_record
  module: heist_logs
  resource: {note: "triggered by {{ vars.input.records[0].codename }}"}

- name: Stamp Status
  type: update_record
  module: heists                                     # -> collectionType
  record: "{{ vars.steps.Find_Open_Heists[0]['@id'] }}"  # first record IRI from the find step
  resource: {status: Briefed}
```

A `find_record` step's result is a **list of records** at `vars.steps.<name>`
directly (not `.data`). Index the first hit with `[0]`, then read any field
(`['@id']`, `.status`, etc.). Use `| length` to check how many matched.

`module:` is friendly-expanded: on `create_record` it becomes the target
`collection` IRI; on `update_record` it becomes `collectionType` (and
`record:` stays the *record* IRI you're updating). Bare picklist labels
(e.g. `status: Briefed`) are auto-resolved to picklist IRIs.

### `delay`

Friendly duration fields -- the compiler expands to FSR's canonical time-based
rule:

```yaml
- name: Wait a Bit
   type: delay
   seconds: 30
   next: Next Step
```

Accepts `seconds`, `minutes`, `hours`, `days` (any combination; defaults to
1 second if all are zero/absent).

### `approval`

An approval gate is a specialized `manual_input` with button-only options
(approve/reject). The compiler handles the wire shape; see `pyfsr playbook
step-help approval` for the full schema.

### `send_email`

Friendly email fields at the step top level (the compiler routes through the
SMTP connector's `send_email` operation):

```yaml
- name: Notify Team
  type: send_email
  to: "{{ vars.input.records[0].ownerEmail }}"
  subject: "Critical alert: {{ vars.input.records[0].name }}"
  body: "Investigation triggered for alert {{ vars.input.records[0].name }}"
  from: "soc@example.com"
  cc: ["backup@example.com"]
  next: Next Step
```

Accepts `to`, `cc`, `bcc` (string or list), `subject`, `body`, `from`, and
`attachments`.

### `create_task`

Creates a record in the `tasks` module (the compiler defaults the collection
to `tasks`):

```yaml
- name: Create Followup Task
  type: create_task
  resource:
    name: "Investigate {{ vars.input.records[0].name }}"
    description: "Auto-created by playbook"
    status: Open
  next: Next Step
```

### `delete_record`

Deletes a single record by IRI, or bulk-deletes via a query (compiles to a
`cyops_utilities.make_cyops_request` DELETE call):

```yaml
- name: Delete Old Record
  type: delete_record
  module: heists
  record: "{{ vars.steps.Find_Old.data[0]['@id'] }}"
  next: Done

- name: Bulk Delete
  type: delete_record
  module: heists
  query:
    logic: AND
    filters:
      - {field: status, op: eq, value: Closed}
  next: Done
```

Accepts `record` (single IRI), `record_id` (uuid + `module`), or `query`
(bulk delete via `delete-with-query`). Set `show_deleted: true` to include
already-deleted records in query results.

### `api_endpoint`

Exposes the playbook at `POST /api/triggers/1/<route>`:

```yaml
- name: Start
  type: api_endpoint
  route: my-webhook
  next: Handle Request
```

Authentication defaults to Token Based (`[""]`); set `authentication_methods:
["anonymous"]` for no-auth, or `["Basic"]` for HTTP Basic.

### `utilities`

Runs a `cyops_utilities` operation (convert_json_to_csv, compute_hash,
make_cyops_request, no_op, ...). Put the operation name and params at the step
top level:

```yaml
- name: Hash Value
  type: utilities
  operation: compute_hash
  params:
    algorithm: sha256
    value: "{{ vars.input.records[0].sourceIp }}"
  next: Next Step
```

### `trigger_tenant_playbook`

Calls a playbook in another FortiSOAR tenant (requires a `workflowReference:`
IRI -- the local-name `target:` form can't cross tenants):

```yaml
- name: Call Remote Tenant
  type: trigger_tenant_playbook
  workflowReference: "/api/3/workflows/<uuid>"
  pickFromTenant: true
  next: Done
```

### `code_snippet` -- run a Python snippet

The Python source goes at the step top level as `code:` (a friendly shorthand the
compiler maps to the canonical `params.python_function`). The snippet
runs through the `code-snippet` connector, so a configured connector is
required (set `allow_imports` on it to `import` anything):

```yaml
- name: Reconcile
  type: code_snippet
  code: |
    import json
    print(json.dumps({"risk": "high" if ... else "low"}))
  next: Decide
```

```{important}
The `code-snippet` sandbox execs the snippet at **module level** (a top-level
`return` is a `SyntaxError`) and restricts `open` (no file access). Surface a
result with `print(json.dumps(...))` -- the connector auto-deserializes it
into a `code_output` dict read downstream at
`vars.steps.<name>.data.code_output.*`.
See {doc}`playbook-authoring` for the full sandbox-compatible pattern, the
upstream-output jinja paths, and the unrestricted-python escape hatch.
```

### `manual_input` -- pause for human input

`manual_input` keys (`title`, `description`, `options`, `inputs`) go at the
**step top level** (not nested under `arguments:`). `options`, like `decision`
carry a per-entry `next:` for branching; `inputs` declare the fields the human
fills in. A submitted field is read downstream as
`vars.steps.<thisStep>.input.<field>`:

```yaml
- name: AskNumber
  type: manual_input
  title: Enter a six digit number
  description: Please enter a number that is exactly 6 digits long.
  inputs:
    - {name: my_number, kind: integer, label: My Number, required: true}
  options:
    - {option: Submit, primary: true}
  next: Validate
```

```{note}
`description:` is optional: when omitted the compiler falls back to the step's
`title:` (the FortiSOAR runtime rejects a genuinely empty description body, so
the fallback keeps a description-less prompt runnable). Set an explicit
`description:` when you want prompt text distinct from the title.
```

```{note}
When driving a paused prompt with `client.manual_input`, a pending input's
`.title` field is the prompt's **schema title** (the step's `title:`), not the
step name. The one-call `client.manual_input.answer(value, by_title=...)` hides
this and the list-token-vs-numeric-id gotcha.
```

### `workflow_reference` -- call another playbook

Name the target playbook at the step top level as `target:` (a friendly alias the
compiler resolves to the wire `workflowReference:` IRI -- prefer `target:`).
`apply_async: false` makes the parent wait synchronously so it can read the
child's output:

```yaml
- name: CallChild
  type: workflow_reference
  next: StampResult
  apply_async: false
  target: Validate Six Digit Number
```

**Cross-playbook output contract.** The child's output is whatever its *last*
`set_variable` step sets. The parent reads it as `vars.steps.<refStep>.<childVar>`
-- so if the child ends with `set_variable` writing `is_valid_number`, the parent
reads `vars.steps.CallChild.is_valid_number`. (In Jinja, spaces in a step name
become underscores.)

### `stop` / `end`

First-class no-op terminals -- use them on a branch that should do nothing
rather than leaving it dangling:

```yaml
- name: Done
  type: end
```

## Cross-cutting step features

These keys work on most step types -- they're not step-specific arguments but
compiler-level sugar that applies across the DSL.

### `for_each:` -- loop a step over a list

Attach a `for_each:` block to iterate a step over a Jinja list expression.
Each iteration gets `{{ vars.for_each.item }}` (the current element):

```yaml
- name: Check Each IP
  type: connector
  connector: virustotal
  operation: query_ip
  params:
    ip: "{{ vars.for_each.item }}"
  next: Done
  for_each:
    item: "{{ vars.steps.Extract_IPs.data.ips }}"
    parallel: true
    max_parallel: 4
    condition: "{{ vars.for_each.item | length > 0 }}"
```

Keys: `item` (required, Jinja list expression), `parallel` (default `false`),
`condition` (optional Jinja filter per iteration), `batch_size` (bulk mode),
`break_loop` (Jinja condition to stop early), `max_parallel` / `concurrency_count`
(parallel loop cap, min 2). Not allowed on control-flow steps (`start*`,
`decision`, `end`, `manual_input`).

### `with:` -- compile-time Jinja alias

Bind short names to long Jinja expressions at compile time; the compiler
rewrites every `vars.<name>` reference in the step's arguments to the bound
expression. This keeps complex `vars.steps.X.data.Y.Z` paths readable:

```yaml
- name: Build Verdict
  type: set_variable
  with:
    yeti: "{{ vars.steps.Yeti_Search.data }}"
    netbox: "{{ vars.steps.NetBox_Lookup.data }}"
  vars:
    yeti_hits: "{{ vars.yeti.total | default(0) }}"
    asset_tenant: "{{ vars.netbox.results[0].tenant.name if (vars.netbox.count | default(0)) > 0 else 'Unknown' }}"
  next: Branch on Threat
```

Here `vars.yeti.total` is rewritten at compile time to
`vars.steps.Yeti_Search.data.total`. The `{{ }}` wrapper is optional in the
binding value.

### `retry:` / `do_until:` -- loop until a condition holds

Attach a `retry:` block (`until` / `times` / `delay`) to re-run a step until
the Jinja `until` evaluates true. On a `workflow_reference` this re-launches
the child each turn -- e.g. re-popping a `manual_input` until the answer validates:

```yaml
- name: CallChild
  type: workflow_reference
  next: StampResult
  apply_async: false
  target: Validate Six Digit Number
  retry:
    until: "{{ vars.steps.CallChild.is_valid_number == true }}"
    times: 8
    delay: 1
```

```{note}
Each `retry:` turn produces its own child run linked to the parent by
`parent_wf`; the parent itself may also span several run records. Locate the
real parent as the top-level run (`parent_wf` null) and enumerate loop turns
with `client.playbooks.child_runs(parent_pk)` (or `run_tree`) rather than
counting runs by name. The full worked example is
`examples/playbooks/do_until_validation_demo.yaml` (driver:
`examples/playbook_do_until_loop.py`).
```

`retry:` and `do_until:` compile to the same wire block; use one or the other.
`retry:` without `until:` is silently dropped by FSR at import time.

### `post_comment:` -- comment on the triggering record

Sugar for posting a collaboration comment on the triggering record:

```yaml
- name: Annotate
  type: set_variable
  vars: {note: "investigation complete"}
  post_comment: "Auto-triaged: {{ vars.input.records[0].name }}"
  next: Done
```

### `on_remote:` -- route to a remote agent

Route step execution to a named FortiSOAR Agent, or use `pick_from_record` for
record-ownership-based selection:

```yaml
- name: Block on Edge
  type: connector
  connector: fortigate
  operation: block_ip
  on_remote: edge-1
  next: Done
```

### `set:` -- step variables (alias for step_variables)

`set:` is a friendly alias for `step_variables` -- variables scoped to the
current step's execution context:

```yaml
- name: Capture
  type: connector
  connector: virustotal
  operation: query_ip
  set:
    verdict: "{{ vars.steps.Capture.data.last_analysis_stats }}"
  next: Decide
```

### Decision: `display:` / `when:` / `default:`

Decision conditions accept friendly key aliases: `display:` (the branch label,
rewritten to wire `option:`) and `when:` (the condition, rewritten to wire
`condition:`). A step-level `default:` is sugar for an Else branch:

```yaml
- name: Big Score?
  type: decision
  conditions:
    - display: High Value
      when: "{{ vars.input.records[0].takeUsd > 1000000 }}"
      next: Alert The Boss
    - display: Medium Value
      when: "{{ vars.input.records[0].takeUsd > 100000 }}"
      next: Log It
  default: Log It
```

## Jinja value transforms (filters)

FortiSOAR evaluates `{{ … }}` with Jinja2, so every standard Jinja filter is
available for reshaping an upstream step's output before the next step reads it.
These are the transforms you reach for most on a list of records
(`vars.input.records`, a `find_record` result at `vars.steps.<Step>.data`, etc.).
All are chainable with `|`.

- **`selectattr` / `rejectattr`** -- keep (or drop) items whose attribute passes a
  test. Filter a record set down to the ones that matter:

  ```jinja
  {# open, high-severity alerts only #}
  {{ vars.steps.Find_Alerts.data | selectattr('status', 'equalto', 'Open')
                                 | selectattr('severity', 'equalto', 'High') | list }}
  ```

  ```{note}
  `selectattr`/`sort` reach attributes with dotted access, which works on objects
  but not plain dicts. When a step hands you a list of **dicts**, the common idiom
  is to normalize first (e.g. run it through a `code_snippet`, or use the FortiSOAR
  custom `json_query` filter) before `selectattr`.
  ```

- **`select` / `reject`** -- same idea on scalars in a list (no attribute):
  `{{ some_list | reject('equalto', '') | list }}` drops empty strings.

- **`map`** -- pluck one attribute from every item: `map(attribute='sourceIp')`.
  Pair with `unique`/`join` to build a deduped, comma-joined string:

  ```jinja
  {{ vars.input.records | map(attribute='sourceIp') | unique | join(', ') }}
  ```

- **`unique`** -- de-duplicate a list (order-preserving).
- **`sort`** -- order a list; `sort(attribute='severity', reverse=True)` for records.
- **`groupby`** -- bucket records by an attribute into `(grouper, items)` pairs,
  e.g. count alerts per status:

  ```jinja
  {% for status, items in vars.input.records | groupby('status') -%}
  {{ status }}: {{ items | length }}
  {% endfor %}
  ```

- **`join`** -- flatten a list to a string with a separator: `| join(', ')`.

```{caution}
There is **no `split` filter** in Jinja. To split a string, call the Python
`.split()` method on it instead -- `{{ device.split(':')[0] }}`,
`{{ vars.record_metadata.get('tags').split(',') }}`. (FortiSOAR also ships a
custom `np_split` filter, but that batches a list into chunks -- a different job.)
```

Beyond the built-ins, FortiSOAR adds ~30 custom filters/globals (date math,
`picklist`, `extract_artifacts`, `toJSON`, `json_query`, …); consult your
appliance's Dynamic Values picker for the full, version-exact list.

## Compile, validate, deploy

```bash
pyfsr playbook validate heist_intake.yaml      # diagnostics only, no network
pyfsr playbook compile  heist_intake.yaml -o envelope.json
pyfsr playbook deploy   heist_intake.yaml --replace
```

`validate` compiles offline and prints one line per diagnostic to stderr
(nonzero exit on any error). Each diagnostic carries a stable `code`, a `path`
into your YAML, a human `message`, and a `severity` (`error` or `warning`):

```{doctest}
>>> from pyfsr.authoring import compile_playbook_yaml, format_diagnostic
>>> bad = compile_playbook_yaml('''
... name: bad-demo
... playbooks:
...   - name: P
...     steps:
...       - name: S
...         type: not_a_real_type
... ''')
>>> bad.ok, bad.fsr_json
(False, None)
>>> [d["code"] for d in bad.errors]
['unknown_step_type']
>>> diag = bad.errors[0]
>>> (diag["severity"], diag["path"])
('error', 'playbooks[0].steps[0].type')
>>> format_diagnostic(diag)              # the line `validate` prints
"[ERROR] unknown_step_type at playbooks[0].steps[0].type: unknown step type: 'not_a_real_type'"
```

The `code` is the stable machine identifier to branch on (e.g.
`unknown_step_type`, `missing_field`, `no_trigger`); `path` is the
YAML-location you fix. `format_diagnostic` renders the same `[SEVERITY] code at
path: message` line the CLI emits, so in-process checks and the CLI stay in
sync.

…or from Python with
{meth}`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_from_yaml`.
See {doc}`playbook-authoring` for the full deploy flow and the compile-result
object.

```{seealso}
Sample file:
[`examples/playbooks/yaml_demo.yaml`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/playbooks/yaml_demo.yaml)
and the end-to-end
[`examples/e2e_module_playbook_demo.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/e2e_module_playbook_demo.py)
(modules → permissions → on-create playbook → triggering record).
```
