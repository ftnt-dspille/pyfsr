# Configuration Export & Import

FortiSOAR's **Configuration Export/Import Wizard** bundles pieces of an
appliance — module schema and records, picklists, connectors and their configs,
playbook collections, roles, teams, dashboards, and more — into a portable
`.zip`, and re-applies that bundle to another (or the same) box. pyfsr wraps both
halves:

| Direction | Surface | Class |
| --- | --- | --- |
| **Export** | `client.export_config` | {class}`~pyfsr.api.export_config.ExportConfigAPI` |
| **Import** | `client.import_config` | {class}`~pyfsr.api.import_config.ImportConfigAPI` |

An export is driven by an **export template** — the wizard's saved selection of
what to include. pyfsr gives you a typed builder, {class}`~pyfsr.api.export_config.ExportTemplate`
(re-exported as `pyfsr.ExportTemplate`), plus one-call convenience methods that
build a throwaway template, run the export, and clean up after themselves.

```{note}
Configuration export/import requires **username/password** auth — the operation
is not available with an API-key token. pyfsr raises
{class}`~pyfsr.exceptions.UnsupportedAuthOperationError` up front if the current
auth method can't perform it.
```

## Quickstart: round-trip a module's records

The most common task is backing up (and restoring) records from a single module.
{meth}`~pyfsr.api.export_config.ExportConfigAPI.export_record_data` does the whole
export in one call; {meth}`~pyfsr.api.import_config.ImportConfigAPI.import_file`
does the whole import.

```python
from pyfsr import FortiSOAR, Query

client = FortiSOAR("soar.example.com", username="csadmin", password="<your-password>")

# Export every Open alert to a .zip (throwaway template, auto-cleaned).
path = client.export_config.export_record_data(
    "alerts",
    query=Query(module="alerts").eq("status", "Open"),
    limit=5000,
    output_path="open_alerts.zip",
)

# ...later, on this or another appliance, restore it end-to-end.
result = client.import_config.import_file("open_alerts.zip", wait=True)
print(result.status)   # "Import Complete"
```

A runnable version that also *proves* the data lands (it deletes the record
between export and import, then confirms it comes back) ships as
`examples/export_import_records.py`.

## Exporting

### The `limit` trigger for record sets

The single most surprising thing about the export engine: **a record set emits
rows only when its query carries a `limit`.** A record set with no limit exports
an empty data file — silently. There is no "export everything unbounded" option.
pyfsr injects a limit for you (default in
{meth}`~pyfsr.api.export_config.ExportTemplate.add_record_set`), so you rarely
set it by hand, but you **do** need to raise it above the number of matching
records or the export truncates:

```python
# Count first, then export all matches.
n = client.records("alerts").count(Query(module="alerts").eq("status", "Open"))
path = client.export_config.export_record_data(
    "alerts",
    query=Query(module="alerts").eq("status", "Open"),
    limit=n,
)
```

### Record data vs. module schema

`export_record_data` exports **rows**. It does *not* carry the module's schema —
the import side assumes the target already has an `alerts` module. To move the
*schema* (fields, picklists it references, view templates), add those categories
to a template explicitly (next section).

### Building a full template

For anything beyond a single record set, compose a
{class}`~pyfsr.api.export_config.ExportTemplate` and hand it to
{meth}`~pyfsr.api.export_config.ExportConfigAPI.create_template`, then export by
its uuid. The builder is fluent, and name-based categories (picklists,
connectors, playbook collections, roles, teams, …) are resolved to IRIs for you
at `create_template` time — you work in friendly names.

```python
from pyfsr import ExportTemplate, Query

tmpl = (
    ExportTemplate("Alert backup")
    .add_module("alerts")                       # schema for the alerts module
    .add_record_set("alerts", query=Query(module="alerts").eq("status", "Open"))
    .add_picklist("AlertStatus")
    .add_connector("OpenAI")                     # with its saved configurations
    .add_playbook_collection("Incident Response")
    .add_role("SOC Analyst")
    .add_team("Tier 1")
)

created = client.export_config.create_template(tmpl)
uuid = created["@id"].split("/")[-1]
client.export_config.export_by_template_uuid(uuid, output_path="alert_backup.zip")
```

Available `add_*` categories on the builder include: `add_module`,
`add_record_set`, `add_view_template`, `add_picklist`, `add_connector`,
`add_playbook_collection`, `add_role`, `add_team`, `add_actor`,
`add_navigation`, `add_report`, `add_rule`, `add_rule_channel`,
`add_preprocessing_rule`, `add_dashboard`, `add_widget`, `add_ai_agent`, and
`add_mcp_configuration`.

```{note}
`add_ai_agent` and `add_mcp_configuration` are **8.0.0+** categories and are
version-gated — exporting them against an older appliance raises.
```

### Exporting a single connector (with configs)

Backing up an installed connector and its saved configurations — including the
encrypted secrets — is common enough to have its own one-call helper:

```python
path = client.export_config.export_connector("code-snippet", output_path="code_snippet.zip")
```

The archive's `connectors/data.json` preserves each `config_id` and carries
secrets in the appliance's encrypted form, so feeding it straight back to
`import_file` restores the connector configs intact. Set
`include_configurations=False` to export just the connector.

## Importing

{meth}`~pyfsr.api.import_config.ImportConfigAPI.import_file` runs the full wizard
lifecycle for you: **upload → create job → generate options → resolve conflicts →
trigger → wait → verify → settle.** With `wait=True` (the default) it blocks
until the job reaches a terminal status and returns the final
{class}`~pyfsr.models.ImportJobResult`.

```python
result = client.import_config.import_file("alert_backup.zip", wait=True)
assert result.status == "Import Complete"
```

### The conflict step, and refuse-by-default safety

The wizard's *"Choose Modules and Views to Import"* screen is where the appliance
diffs your bundle against what's live and reports, per field, what would change
and how to merge it (overwrite the live value vs. keep the existing one).

Some of those changes drive a **destructive, appliance-wide schema migrate** — a
`tableName` rename, a field type change, or a change to a unique-constraint field.
These can fail outright or *wedge* the box (e.g. a rename whose `CREATE INDEX`
collides with the old table's index — Postgres `42P07`). Because that blast
radius is appliance-wide (exactly like
{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.publish`), `import_file`
**refuses by default**: if the generated options contain any risky change and you
haven't said how to handle it, it raises `ValueError` *before* triggering.

You pick how to proceed with the `resolve=` one-shot flag:

| `resolve=` | Effect |
| --- | --- |
| `"overwrite"` | Apply every field change from the bundle. |
| `"keep_existing"` | Keep every existing field; add only genuinely new ones. |
| `"skip_schema"` | Import records/views but do **not** apply schema changes — the safe way past a risky rename. |

```python
# Restore records and views without touching live schema.
result = client.import_config.import_file("alert_backup.zip", resolve="skip_schema")
```

To inspect the risks before committing, generate the options yourself and read
them with {func}`~pyfsr.api.import_config.inspect_changes`, or drop to the
step-by-step methods (`create_job`, `generate_options`, `wait_for_options`,
`set_options`, `trigger`, `wait_for_import`). For full control over the merge,
pass `modify_options=` — a callback that receives the options dict and returns
the mutated dict; the module-level helpers
{func}`~pyfsr.api.import_config.connectors_only`,
{func}`~pyfsr.api.import_config.overwrite_all`,
{func}`~pyfsr.api.import_config.keep_existing`, and
{func}`~pyfsr.api.import_config.skip_schema_changes` are ready-made callbacks.

For connector bundles specifically, {func}`~pyfsr.api.import_config.connector_flags`
sets the two per-connector toggles — `includeInstall` (reinstall the connector)
and `includeConfigurations` (restore its saved configs) — without disturbing the
rest of the bundle:

```python
from pyfsr.api.import_config import connector_flags

# Restore connector configs but do not reinstall the connectors themselves.
client.import_config.import_file(
    "bundle.zip",
    modify_options=lambda o: connector_flags(o, include_install=False, include_configurations=True),
)
```

```{warning}
`allow_schema_changes=True` bypasses the precheck entirely and triggers with the
server-default options even when risky changes are present. Reach for a `resolve=`
strategy first; only bypass when you understand the migrate.
```

### Waiting, verifying, and settling

- **`verify=True`** (default) raises {class}`~pyfsr.exceptions.FortiSOARException`
  if the job finishes in a failure state, surfacing the appliance's own
  `errorMessage` (including the `42P07` wedge). With `verify=False` the failed
  job is returned for you to inspect instead.
- **`settle=True`** (default) blocks after a successful import until the schema
  cache is responsive again, so a follow-on `list_modules()` or query doesn't hit
  a *"Clearing Cache" / "Schema Update"* 503.
- **`wait=False`** returns right after triggering (the job carries `jobUuid`);
  poll later with {meth}`~pyfsr.api.import_config.ImportConfigAPI.wait_for_import`.
```
