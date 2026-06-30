# Playbook Authoring & Deployment

pyfsr can author FortiSOAR playbooks from **YAML** and deploy them through the
same import path the UI uses. You write a collection as readable YAML, an
optional compiler turns it into the FortiSOAR export envelope, and pyfsr pushes
it to the appliance — no hand-building of workflow/step/route JSON.

```{seealso}
Runnable examples:
[`examples/deploy_playbook_from_yaml.py`](https://github.com/dylanspille/pyfsr/blob/main/examples/deploy_playbook_from_yaml.py)
(YAML → compile → deploy),
[`examples/create_safe_playbook.py`](https://github.com/dylanspille/pyfsr/blob/main/examples/create_safe_playbook.py)
(hand-built JSON), and the sample
[`examples/playbooks/yaml_demo.yaml`](https://github.com/dylanspille/pyfsr/blob/main/examples/playbooks/yaml_demo.yaml).
```

## The compiler is an optional extra

The YAML→JSON compiler ships separately from core pyfsr. Install it with the
`playbooks` extra:

```bash
pip install "pyfsr[playbooks]"
```

Core pyfsr never imports it. Until it's installed, the compile/deploy entry
points raise {class}`~pyfsr.authoring.PlaybooksExtraNotInstalled` with that exact
hint. The non-authoring collection methods
({meth}`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_from_file`,
`list`, `delete`, …) work without it.

## Writing a playbook in YAML

A playbook file describes one collection and its workflows. The smallest useful
shape (see the sample for the full file):

```yaml
collection: pyfsr YAML Demo
description: Authored in YAML, deployed with pyfsr.
visible: true

playbooks:
  - name: pyfsr YAML Demo - Stamp Result
    is_active: false
    steps:
      - name: Start
        type: start
        next: Set Result

      - name: Set Result
        type: set_variable
        vars:
          greeting: hello from pyfsr
          source: yaml
```

The YAML schema (step types, their arguments, routing) is owned by the
`fsr_playbooks` compiler. The compiler validates every step against a reference
catalog of FortiSOAR step types and emits diagnostics (with `code`, `path`,
`message`, and often a `suggestion`) when something won't import.

```{tip}
For the **full DSL** — every top-level key, every step `type`, the friendly
fields each accepts, and the `start_on_create` / `start_on_update` record
triggers — see the {doc}`playbook-yaml-reference`.
```

## Deploying from Python

The high-level path lives on `client.workflow_collections`. Compile and import
in one call:

```python
from pyfsr import FortiSOAR

client = FortiSOAR(base_url="https://fortisoar.example.com", auth="<api-key>")

created = client.workflow_collections.import_from_yaml(
    "alert_triage.yaml",
    replace=True,            # hard-delete + recreate a same-uuid collection
)
for col in created:
    print(col["name"], col["uuid"])
```

To inspect diagnostics before pushing anything, compile first (offline, no
network) and check the result:

```python
result = client.workflow_collections.compile_yaml("alert_triage.yaml")
if not result.ok:
    from pyfsr.authoring import format_diagnostic
    for diag in result.blocking:
        print(format_diagnostic(diag))
else:
    print("collections:", result.collection_names)
    print("playbooks:", result.playbook_names)
    client.workflow_collections.import_export(result.fsr_json, replace=True)
```

The `CompiledPlaybook` shape is doctested (compilation is offline, no network).
`ok` is `True` only when there are no blocking errors; `collection_names` and
`playbook_names` read off the produced envelope:

```{doctest}
>>> from pyfsr.authoring import compile_playbook_yaml
>>> yaml = '''
... name: demo-triage
... description: Doctested example playbook
... playbooks:
...   - name: Triage Alert
...     description: one step
...     steps:
...       - name: Start
...         type: start
...         next: Set Note
...       - name: Set Note
...         type: set_variable
...         manual_input:
...           - name: note
...             type: text
...             value: hello
... '''
>>> result = compile_playbook_yaml(yaml)
>>> result.ok, result.collection_names, result.playbook_names
(True, ['00 - FSR Studio'], ['Triage Alert'])
```

A blocking error keeps `ok` `False` and leaves `fsr_json` `None` — `errors`
holds every diagnostic so you can surface *why* before anything is deployed:

```{doctest}
>>> bad = compile_playbook_yaml("name: x\nplaybooks:\n  - name: P\n    steps: []")
>>> bad.ok, bad.fsr_json
(False, None)
>>> [e["code"] for e in bad.errors]
['no_trigger']
```

{meth}`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_from_yaml`
options:

| Option | Effect |
|---|---|
| `replace=True` | Hard-delete any existing collection whose uuid matches, then recreate (the UI's "Replace existing playbook collection" flow). Without it a duplicate uuid raises `409 UniqueConstraintViolationException`. |
| `strict_warnings=True` | Treat compiler **warnings** as blocking, not just errors. |
| `db_path=...` | Override the reference catalog (defaults to the packaged one). |

Compilation that produces blocking errors raises `ValueError` with the formatted
diagnostics; a missing compiler raises `PlaybooksExtraNotInstalled`.

### The compile result

{meth}`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.compile_yaml`
returns a {class}`~pyfsr.authoring.CompiledPlaybook`:

| Attribute | Meaning |
|---|---|
| `ok` | True only when there are no blocking errors and an envelope was produced. |
| `fsr_json` | The `{"type": "workflow_collections", "data": [...]}` envelope, ready for `import_export` (`None` on blocking errors). |
| `errors` | Every diagnostic (errors **and** warnings) as dicts. |
| `blocking` / `warnings` | The error-only and warning-only subsets. |
| `collection_names` / `playbook_names` | Convenience name lists from the envelope. |

## Deploying from the CLI

The `pyfsr playbook` command group offers the same flow without writing Python.
Unlike `pyfsr appliance` (which uses SSH), these talk to the FortiSOAR **API**
and read connection details from the `FSR_*` environment (see
{class}`~pyfsr.config.EnvConfig`), with optional flag overrides.

```bash
# Compile only — emit the envelope JSON, diagnostics to stderr (no network)
pyfsr playbook compile alert_triage.yaml -o envelope.json

# Validate — compile and report a diagnostics summary; nonzero exit on errors
pyfsr playbook validate alert_triage.yaml

# Deploy — compile then import via the API client
pyfsr playbook deploy alert_triage.yaml --replace

# See what deploy would create without posting anything
pyfsr playbook deploy alert_triage.yaml --dry-run
```

Connection overrides (any omitted value falls back to `FSR_*` env):

```bash
pyfsr playbook deploy alert_triage.yaml --replace \
    --server fortisoar.example.com --username csadmin --password '...' \
    --port 13002 --no-verify-ssl
```

## Discovering step types

You don't have to memorize the friendly `type:` keywords or their keys. The
`pyfsr playbook` group is the authoring index — `pyfsr playbook --help` lists
every affordance, and two offline commands enumerate the step catalog:

```bash
# List every friendly step type with its canonical FSR name + purpose
pyfsr playbook steps

# Keys, pitfalls, the typed-args schema, and a real compiling example for one type
pyfsr playbook step-help manual_input
pyfsr playbook step-help decision --schema
```

The same data is available from Python via {func}`pyfsr.playbook_catalog.list_step_types`
and {func}`pyfsr.playbook_catalog.step_help`.

## Worked examples — the foundational library

`step-help` shows one *atom* (one step type). The **foundational playbook library**
shows whole *molecules*: complete, compiling, use-case-shaped playbooks you retrieve
by intent and adapt — the layer an agent few-shots on when translating a goal to
SOAR operations. It lives at `examples/playbooks/library/`, grouped by SOC stage
(triggers / enrichment / decision / action / notify / control).

```bash
# List every library playbook with its stage, intent, step types, and compile status
pyfsr playbook examples

# Filter by intent or stage
pyfsr playbook examples --intent incident
pyfsr playbook examples --stage action

# Print one playbook's metadata + the full friendly YAML to adapt
pyfsr playbook show create-incident-from-alert

# Emit the retrieval manifest (intent + facets per playbook) for NL->playbook tooling
pyfsr playbook examples --manifest
```

Every library playbook compiles and carries a `goal` / `trigger` / `inputs` /
`outputs` / `connectors` / `adapts-to` front-matter block. `cold*` in the compile
column means it compiles but references connectors the offline slim catalog
doesn't carry — run `pyfsr playbook deploy <file> --refresh-catalog` to resolve
them against a live instance. The manifest and listing are available from Python
via {func}`pyfsr.playbook_library.list_library`,
{func}`pyfsr.playbook_library.library_manifest`, and
{func}`pyfsr.playbook_library.library_show`.

## Testing interactive playbooks & inspecting runs

A playbook that pauses on a **Manual Input** / **Approval** step can be driven
end to end from Python. {meth}`~pyfsr.api.manual_input.ManualInputAPI.answer`
finds the pending prompt, resolves the numeric run id / submit option / user, and
resumes — in one call (it hides the gotcha that a prompt's `.title` is the *step
name*):

```python
client.playbooks.trigger("Loop Until Six Digits")
client.manual_input.answer(654321, by_title="AskNumber")   # fill + resume
```

To inspect what ran, {meth}`~pyfsr.api.playbooks.PlaybooksAPI.run_tree` resolves a
`task_id` to the run **plus its child runs** (no finding the parent by name), and
{meth}`~pyfsr.api.playbooks.PlaybooksAPI.step_status` reads a step's outcome:

```python
resp = client.playbooks.trigger("Loop Until Six Digits")
tree = client.playbooks.run_tree(resp["task_id"])   # parent + child runs, with pks
client.playbooks.step_status(tree.pk, "StampResult")  # -> "finished"
```

```{note}
FortiSOAR only records runtime `set_variable` / jinja values in the retrievable
run record when **global workflow debug logging is enabled**; with it off (the
default) `run_env(...).env` is empty for them. Either enable debug logging on the
appliance to inspect env, or assert on a step's **status** (via `step_status`),
which is always recorded.
```

A complete worked example lives in `examples/do_until_validation_loop.py`.

## Importing an existing export

If you already have a `*.json` export from the UI's **Export** button (no
compiler needed), import it directly:

```python
client.workflow_collections.import_from_file("exported_playbooks.json", replace=True)
```

## Keeping the compile catalog fresh

The compiler validates against a cached reference catalog of FortiSOAR step
types. `pyfsr playbook check-fresh` compares that catalog's provenance against a
live appliance and flags drift (exit `0` fresh, `2` drift, `1` error/unstamped):

```bash
pyfsr playbook check-fresh --server fortisoar.example.com
```

If it reports drift, re-run the compiler's `warmup` against the target to
refresh the catalog before deploying.
