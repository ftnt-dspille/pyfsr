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

### `delay`, `code_snippet`, `manual_input`, `approval`, `workflow_reference`

These accept their canonical `arguments:` (see `pyfsr playbook validate` /
`get_step_type`). `manual_input` keys (`title`, `description`, `options`,
`inputs`) go at the **step level**, not under `arguments:`, and its `options`,
like `decision` conditions, carry a per-entry `next:` for branching.

```{note}
`description:` on `manual_input` is optional: when omitted the compiler now falls
back to the step's `title:` (the FortiSOAR runtime rejects a genuinely empty
description body, so the fallback keeps a description-less prompt runnable). Set
an explicit `description:` when you want prompt text distinct from the title.
```

### `stop` / `end`

First-class no-op terminals — use them on a branch that should do nothing
rather than leaving it dangling:

```yaml
- name: Done
  type: end
```

## Compile, validate, deploy

```bash
pyfsr playbook validate heist_intake.yaml      # diagnostics only, no network
pyfsr playbook compile  heist_intake.yaml -o envelope.json
pyfsr playbook deploy   heist_intake.yaml --replace
```

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
