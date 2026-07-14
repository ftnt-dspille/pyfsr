# Playbook YAML Syntax Reference

This is the authoring reference for the **YAML playbook DSL** that the
`fsr_playbooks` compiler (the `pyfsr[playbooks]` extra) accepts. It is the
companion to the narrative {doc}`playbook-authoring` guide: that page shows the
*workflow* (write → compile → deploy); this page is the *syntax* — every
top-level key, every step `type`, and the friendly fields each step accepts.

The DSL is a thin, friendly layer over FortiSOAR's wire format: you write short
`type:` names and friendly keys like `module:` / `vars:` / `when:`, and the
compiler expands them into the canonical workflow/step/route JSON the import API
expects. Anything you don't recognise on the wire, you can usually still set by
its canonical key — the compiler only rejects *unknown* keys, never canonical
ones.

```{note}
The step catalogue is owned by the compiler and validated against a packaged
reference DB of real FortiSOAR step types. When in doubt, `pyfsr playbook
validate <file>` is the source of truth — it reports unknown keys, missing
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
`"<source> -> <target>"`. The volatile fields ��� `uuid`, `top`/`left` (canvas
position), and the `/api/3/workflow_steps/<uuid>` IRIs in each route — are
compiler-generated and stable across runs, so you only need to author the
friendly shape on the left.

## File structure

A playbook file describes **one collection** and the **workflows** (playbooks)
inside it:

```yaml
collection: My Collection          # required — the collection name
description: What this does         # optional
visible: true                       # optional — show in the UI (default true)

playbooks:                          # required — one or more workflows
  - name: My Playbook               # required — workflow name
    is_active: false                # optional — live trigger? (default false)
    trigger: start                  # optional — trigger step type (default "start")
    parameters: []                  # optional — referenced-playbook input params
    steps:                          # required — the step list
      - name: Start
        type: start
        next: Do Something
      - name: Do Something
        type: set_variable
        vars: {greeting: hello}
```

| Top-level key | Meaning |
|---|---|
| `collection` | Collection display name. |
| `description` | Free-text description. |
| `visible` | Whether the collection shows in the UI (default `true`). |
| `playbooks` | List of workflows; each is one playbook. |

| Playbook key | Meaning |
|---|---|
| `name` | Workflow name (required). |
| `is_active` | If `true`, the playbook is **live** and its trigger fires. Leave `false` for manual/referenced playbooks. |
| `trigger` | Short-name of the trigger step type; defaults to `start`. Usually inferred from the first `start*` step instead. |
| `parameters` | Input parameters for a referenced playbook (`vars.input.params.<name>`). |
| `steps` | The step list (see below). |

## Steps: common shape

Every step has a `name`, a `type`, and (except terminals/decisions) a `next:`
pointing at the next step's `name`:

```yaml
- name: Enrich IP          # unique within the playbook; also the jinja slug
  type: connector
  next: Decide             # name of the next step
  arguments: {...}         # type-specific (many types have friendlier keys)
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
| `start` | `cybersponse.abstract_trigger` | Manual / referenced trigger (the default Start). |
| `start_on_create` | `cybersponse.post_create` | **Auto-fire when a record is created** in a module. |
| `start_on_update` | `cybersponse.post_update` | Auto-fire when a record is updated. |
| `set_variable` | `SetVariable` | Define `vars.*` values. |
| `decision` | `Decision` | Branch on conditions. |
| `connector` | `Connectors` | Run a connector operation. |
| `find_record` | `FindRecords` | Query records of a module. |
| `create_record` | `InsertData` | Create a record. |
| `update_record` | `UpdateRecord` | Update a record. |
| `ingest_bulk_feed` | `IngestBulkFeed` | Bulk feed insert (bypasses on-create triggers). |
| `delay` | `Delay` | Wait. |
| `manual_input` | `ManualInput` | Pause for human input. |
| `approval` | `Approval` | Approval gate. |
| `code_snippet` | `CodeSnippet` | Run a Python snippet. |
| `workflow_reference` | `WorkflowReference` | Call another playbook. |
| `stop` / `end` | `Connectors` (`cyops_utilities.no_op`) | First-class no-op terminal. |

### `start` — manual trigger

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

### `start_on_create` / `start_on_update` — record triggers

Auto-fire when a record is created (or updated) in `module:`. Set the
playbook's `is_active: true` for it to actually fire.

```yaml
- name: Start
  type: start_on_create
  module: heists                 # required — the module to watch
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
changes. The compiler expands `when:` into the canonical `fieldbasedtrigger`
envelope (`resource`/`resources`, `step_variables`, `triggerOnSource`, …) for
you.

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

Branches carry their own `next:` per condition entry — there is no step-level
`next:` or `branches:`:

```yaml
- name: Big Score?
  type: decision
  conditions:
    - condition: "{{ vars.input.records[0].takeUsd > 1000000 }}"
      label: big
      next: Alert The Boss
    - label: default
      next: Log It
```

### `connector`

Connector op, operation name, and params go **under `arguments:`**. Resolve the
exact `connector` / `operation` / param names with the discovery tools
(`pyfsr playbook` MCP / `find_operation`) — don't guess them:

```yaml
- name: Enrich IP
  type: connector
  arguments:
    connector: virustotal
    operation: get_ip_reputation
    ip: "{{ vars.input.records[0].sourceIp }}"
  next: Decide
```

### `find_record` / `create_record` / `update_record`

```yaml
- name: Find Open Heists
  type: find_record
  arguments:
    module: heists
    query: {logic: AND, filters: [{field: status, operator: eq, value: Open}]}

- name: Log It
  type: create_record
  arguments:
    module: heist_logs
    resource: {note: "triggered by {{ vars.input.records[0].codename }}"}

- name: Stamp Status
  type: update_record
  arguments:
    module: heists                                     # → collectionType
    collection: "{{ vars.input.records[0]['@id'] }}"   # the record IRI to update
    resource: {status: Briefed}
```

`module:` is friendly-expanded: on `create_record` it becomes the target
`collection` IRI; on `update_record` it becomes `collectionType` (and
`collection:` stays the *record* IRI you're updating). Bare picklist labels
(e.g. `status: Briefed`) are auto-resolved to picklist IRIs.

### `delay`, `approval`

These accept their canonical `arguments:` (see `pyfsr playbook validate` /
`pyfsr playbook step-help <type>`).

### `code_snippet` — run a Python snippet

The Python source goes under `arguments.code:` (a friendly shorthand the
compiler maps to the canonical `arguments.params.python_function`). The snippet
runs through the `code-snippet` connector, so a configured connector is
required (set `allow_imports` on it to `import` anything):

```yaml
- name: Reconcile
  type: code_snippet
  arguments:
    code: |
      import json
      print(json.dumps({"risk": "high" if ... else "low"}))
  next: Decide
```

```{important}
The `code-snippet` sandbox execs the snippet at **module level** (a top-level
`return` is a `SyntaxError`) and restricts `open`. Surface a result with
`print(json.dumps(...))` — the connector auto-deserializes it into a
`code_output` dict read downstream at `vars.steps.<name>.data.code_output.*`.
See {doc}`playbook-authoring` for the full sandbox-compatible pattern, the
upstream-output jinja paths, and the unrestricted-python escape hatch.
```

### `manual_input` — pause for human input

`manual_input` keys (`title`, `description`, `options`, `inputs`) go at the
**step level**, not under `arguments:`. `options`, like `decision` conditions,
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
`.title` field is the **step name** (`AskNumber` above), not the schema title.
The one-call `client.manual_input.answer(value, by_step=...)` hides this and the
list-token-vs-numeric-id gotcha.
```

### `workflow_reference` — call another playbook

Name the target playbook under `arguments:` as `target:` (a friendly alias the
compiler resolves to the wire `workflowReference:` IRI — prefer `target:`).
`apply_async: false` makes the parent wait synchronously so it can read the
child's output:

```yaml
- name: CallChild
  type: workflow_reference
  next: StampResult
  apply_async: false
  arguments:
    target: Validate Six Digit Number
```

**Cross-playbook output contract.** The child's output is whatever its *last*
`set_variable` step sets. The parent reads it as `vars.steps.<refStep>.<childVar>`
— so if the child ends with `set_variable` writing `is_valid_number`, the parent
reads `vars.steps.CallChild.is_valid_number`. (In Jinja, spaces in a step name
become underscores.)

### `do_until` / `retry:` — loop a step until a condition holds

Attach a `retry:` block (`until` / `times` / `delay`) to re-run a step until the
Jinja `until` evaluates true. On a `workflow_reference` this re-launches the
child each turn — e.g. re-popping a `manual_input` until the answer validates:

```yaml
- name: CallChild
  type: workflow_reference
  next: StampResult
  apply_async: false
  arguments:
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
`examples/do_until_validation_loop.py`).
```

### `stop` / `end`

First-class no-op terminals — use them on a branch that should do nothing
rather than leaving it dangling:

```yaml
- name: Done
  type: end
```

## Jinja value transforms (filters)

FortiSOAR evaluates `{{ … }}` with Jinja2, so every standard Jinja filter is
available for reshaping an upstream step's output before the next step reads it.
These are the transforms you reach for most on a list of records
(`vars.input.records`, a `find_record` result at `vars.steps.<Step>.data`, etc.).
All are chainable with `|`.

- **`selectattr` / `rejectattr`** — keep (or drop) items whose attribute passes a
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

- **`select` / `reject`** — same idea on scalars in a list (no attribute):
  `{{ some_list | reject('equalto', '') | list }}` drops empty strings.

- **`map`** — pluck one attribute from every item: `map(attribute='sourceIp')`.
  Pair with `unique`/`join` to build a deduped, comma-joined string:

  ```jinja
  {{ vars.input.records | map(attribute='sourceIp') | unique | join(', ') }}
  ```

- **`unique`** — de-duplicate a list (order-preserving).
- **`sort`** — order a list; `sort(attribute='severity', reverse=True)` for records.
- **`groupby`** — bucket records by an attribute into `(grouper, items)` pairs,
  e.g. count alerts per status:

  ```jinja
  {% for status, items in vars.input.records | groupby('status') -%}
  {{ status }}: {{ items | length }}
  {% endfor %}
  ```

- **`join`** — flatten a list to a string with a separator: `| join(', ')`.

```{caution}
There is **no `split` filter** in Jinja. To split a string, call the Python
`.split()` method on it instead — `{{ device.split(':')[0] }}`,
`{{ vars.record_metadata.get('tags').split(',') }}`. (FortiSOAR also ships a
custom `np_split` filter, but that batches a list into chunks — a different job.)
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
[`examples/heist_tracker.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/heist_tracker.py)
(modules → permissions → on-create playbook → triggering record).
```
