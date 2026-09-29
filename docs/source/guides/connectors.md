# Connectors

`client.connectors` ({class}`~pyfsr.api.connectors.ConnectorsAPI`) wraps
FortiSOAR's `/api/integration` surface -- discovery, healthcheck, configuration,
operation execution, the Connector Studio dev workspace, and install/uninstall.
`client.agents` ({class}`~pyfsr.api.agents.AgentsAPI`) covers the remote
*execution agent* side: pushing, upgrading, and removing a connector on an
agent, plus a liveness heartbeat.

A complete, runnable walkthrough lives in
[`examples/manage_connectors.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/manage_connectors.py)
-- it defaults to read-only and exercises every method below.

## Discovery & health

```{doctest}
>>> client = demo_client()
>>> conn = client.connectors
>>> installed = conn.list_configured()           # installed + configured connectors
>>> [c.name for c in installed[:3]]             # doctest: +ELLIPSIS
['smtp', 'code-snippet', ...]
>>> conn.resolve_version("mitre-attack")         # the configured version (None if absent)
'2.0.2'
>>> conn.resolve_version("not-installed") is None
True
>>> conn.configurations("mitre-attack")          # [{config_id, name, default}]
[ConnectorConfigSummary(id=7, config_id='01e4e6b4-c34e-4fc1-b692-bb08591f1fe5', name='Demo', default=True)]
>>> hc = conn.healthcheck("mitre-attack")        # status="Available" is green
>>> (hc.status, hc.name, hc.version)
('Available', 'mitre-attack', '2.0.2')
```

`connector_detail` fetches a connector's full record -- its operations (each with
parameters + output_schema) and configurations. Captured live and trimmed to a
doctest-friendly slice (the `config` dict on each configuration is dropped -- it
carries connection details):

```{doctest}
>>> detail = conn.connector_detail("smtp")
>>> (detail["name"], detail["version"], detail["config_count"])
('smtp', '2.6.0', 1)
>>> [o["operation"] for o in detail["operations"][:3]]  # doctest: +ELLIPSIS
['send_email_new', ...]
>>> [c["name"] for c in detail["configuration"]]        # doctest: +ELLIPSIS
['localhost-postfix']
```

## Executing an operation

`execute()` returns a typed {class}`~pyfsr.models.ExecuteResult` -- `.ok` is the
`status == "Success"` check, `.data` is the connector's own output (shape varies
by connector/operation). Live-verified against `cisa-advisory`'s
`get_known_exploited_vulnerability_cves` -- a public, read-only, parameter-less
feed lookup safe to demo against a real vendor connector (the only side effect
is CISA's public catalog serving one GET):

```{doctest}
>>> result = conn.execute("cisa-advisory", "get_known_exploited_vulnerability_cves")
>>> result.ok
True
>>> result.data["title"]
'CISA Catalog of Known Exploited Vulnerabilities'
>>> result.data["vulnerabilities"][0]["cveID"]
'CVE-2026-45659'
```

### Operations with input parameters

Most connector operations take inputs. Pass them as `params=` -- a dict keyed
by the parameter name (the `name` field from the operation's definition, not
its display `title`):

```python
r = conn.execute("nist-nvd", "get_specific_cve_details", params={"cveId": "CVE-2021-44228"})
r.ok          # True
r.data["vulnerabilities"][0]["cve"]["id"]   # "CVE-2021-44228"
```

To discover what parameters an operation needs before calling it, use
`action_ui_schema()` -- it returns each parameter with its `name`, `type`,
`required`, and `title`:

```python
params = conn.action_ui_schema("nist-nvd", "get_specific_cve_details")
for p in params:
    print(f"  {p.name}: type={p.type}  required={p.required}  title={p.title}")
# cveId: type=text  required=True  title=CVE ID
```

Or iterate the full operation list with `operations()`:

```python
for op in conn.operations("virustotal"):
    required = [p.name for p in op.parameters if p.required]
    print(f"  {op.operation}: required={required}")
# url_re_analyze: required=['id']
# query_url: required=['url']
# query_ip: required=['ip']
# file_reputation: required=['file_hash']
# ...
```

### Selecting a configuration

When a connector has multiple configurations, `execute()` uses the **default**
one automatically. Pass `config=` to target a specific one -- it accepts either
a configuration **name** or **UUID**:

```python
# By name:
r = conn.execute("nist-nvd", "get_specific_cve_details",
                 params={"cveId": "CVE-2024-3094"}, config="nvd-public")
r.ok        # True

# By UUID:
r = conn.execute("nist-nvd", "get_specific_cve_details",
                 params={"cveId": "CVE-2024-3094"},
                 config="10580fb2-2693-4d9f-8408-1aa344affbf9")
r.ok        # True
```

To list available configurations:

```python
for cfg in conn.configurations("nist-nvd"):
    print(f"  {cfg.name}  default={cfg.default}  config_id={cfg.config_id}")
# nvd-public  default=True  config_id=10580fb2-2693-4d9f-8408-1aa344affbf9
```

⚠️ For an **agent-bound** connector (see the module warning), `execute()` is
fire-and-forget -- it returns immediately with an in-progress status and empty
`data`; the real result is pushed over a websocket, not pollable here.

### Predicting the return shape

`.data` varies by connector and operation. `output_schema()` returns the
operation's declared output fields so you know what to expect before calling:

```python
schema = conn.output_schema("virustotal", "query_ip")
# {"output_schema": [{"name": "permalink", "type": "text"},
#                     {"name": "positives", "type": "integer"}, ...]}
```

Not every operation declares an output schema -- an empty or missing
`output_schema` key means the connector didn't specify one.

## Dynamic operation parameters (`apiOperation`)

A `select`/`multiselect` parameter can declare `apiOperation` -- the name of a
sibling connector operation whose result populates the dropdown at render time.
This lets an operation's choices come from the live remote system (VMs,
severities, locations, ...) instead of a hardcoded `options` list. The
populating operation is `visible: false` (hidden from the playbook palette) and
receives the connector `config` so it can authenticate.

`action_ui_schema()` returns the params with their `apiOperation` and
`apiOnchange` fields, so a UI or agent can detect which params are dynamic:

```python
params = conn.action_ui_schema("cisco-threatgrid", "submit_sample")
for p in params:
    if p.apiOperation:
        print(f"{p.name}: type={p.type} -> call {p.apiOperation}")
    else:
        print(f"{p.name}: type={p.type} (static)")
```

To resolve the choices for a dynamic param, call the populating operation via
{meth}`~pyfsr.api.connectors.ConnectorsAPI.execute` -- the result is a plain
string list or `[{"title": "...", "value": "..."}]` objects:

```python
defn = conn.definition("cisco-threatgrid")
op = next(o for o in defn.operations if o.operation == "submit_sample")
vm_param = next(p for p in op.parameters if p.apiOperation == "get_available_vms")

result = conn.execute("cisco-threatgrid", vm_param.apiOperation, config="<config-uuid>")
choices = result.data  # ["Windows 7 64-bit", "Linux 64-bit", ...]
```

When `apiOnchange=True`, the populating operation also receives the current
values of all sibling parameters in `params` (for cascading dropdowns like
sap-rfc's pick-a-module-then-its-params-appear). Pass them as the `params`
argument to `execute`:

```python
result = conn.execute(
    "sap-rfc", "get_rfc_function_params",
    config="<config-uuid>",
    params={"function_name": "RFC_READ_TABLE"},  # sibling value
)
# result.data = {"options": "RFC_READ_TABLE", "onchange": {"RFC_READ_TABLE": [param, ...]}}
```

See the Connector Building Guide (section "Dynamic Options from an Operation")
for the full `info.json` declaration + Python handler patterns.

## Creating, rotating, and deleting a configuration

`create_configuration`/`update_configuration`/`delete_configuration` write
credentials via `POST`/`PUT`/`DELETE /api/integration/configuration/`. Captured
live against a throwaway `virustotal` config (`api_key` is a placeholder value,
never a real credential) -- created, rotated, then deleted, leaving the box with
0 `virustotal` configs afterwards, same as before:

```{doctest}
>>> created = conn.create_configuration(
...     "virustotal",
...     {"server": "www.virustotal.com", "api_key": "test-doctest-key", "verify_ssl": True},
...     name="pyfsr-doctest-config",
...     validate=False,   # skip the schema fetch (config_schema) for this offline demo
...     autofill=False,
... )
>>> (created.name, created.config["server"])
('pyfsr-doctest-config', 'www.virustotal.com')
```

Note `config["api_key"]` comes back as the literal string `"NULL"` regardless of
what was sent -- the server never echoes a stored secret, only this sentinel:

```{doctest}
>>> created.config["api_key"]
'NULL'
```

`update_configuration` sends the config whole -- include every field, not just
the one you're rotating:

```{doctest}
>>> updated = conn.update_configuration(
...     "virustotal", created.config_id,
...     {"server": "www.virustotal.com", "api_key": "test-rotated-key", "verify_ssl": True},
...     name="pyfsr-doctest-config",
...     validate=False,
...     autofill=False,
... )
>>> updated.name
'pyfsr-doctest-config'
```

```{note}
The `PUT` response omits `connector_name`/`connector_version` -- present on
`create_configuration`'s response, absent on `update_configuration`'s. Don't
rely on either field being there after an update.
```

```{doctest}
>>> conn.delete_configuration(created.config_id) is None
True
```

## Making a configuration the default

A connector whose configuration isn't marked default fails its healthcheck with
`Could not find a configuration matching the id get_default_config or the
default configuration` -- the config is there and usable by name, but anything
resolving by default gets nothing.

There is no flag-only route: `PUT /api/integration/configuration/{config_id}/`
replaces the whole record. That makes the obvious fix dangerous, because the
**listing** returns `config: null` while only the **single-record GET** carries
the real field map -- build the `PUT` body from the listing and you wipe the
credentials. `set_default_configuration` does the read-then-echo for you:

```python
conn.set_default_configuration("fortigate-firewall")             # the only config
conn.set_default_configuration("fortigate-firewall", "a5fb56f2") # by config_id
conn.set_default_configuration("fortigate-firewall", name="fortigate-lab")
```

It deliberately skips `validate`/`autofill` -- the stored config is already what
the appliance accepted, and materializing it against the schema would rewrite
fields this call has no business touching. A remote-agent binding is carried
over explicitly; omitting `agent` on the `PUT` silently moves execution back to
the self-agent. If the appliance returns the `"NULL"` secret sentinel instead of
a stored value, the call raises rather than writing that sentinel over a live
credential.

```{note}
The stored ciphertext for a secret legitimately **changes** across this call --
the appliance re-encrypts on save while the plaintext does not change. Verified
on a live 8.0.0 appliance: a FortiGate configuration that could not be
health-checked reported `Available` afterwards, with the upstream still
reachable. Don't read the changed value as corruption.
```

```{warning}
The trailing slash on `/api/integration/configuration/{config_id}/` is
mandatory. Without it the gateway rejects the call with `403 Could not validate
HMAC fingerprint`, which reads like a permissions or auth problem rather than a
URL typo.
```

## Data ingestion

Connectors that support data ingestion (the *Data Ingestion* page's connector
picker) can be wired up programmatically. `data_ingest_wizard()` reproduces
every write the UI's *Configure Data Ingestion* wizard makes -- resolve the
config, clone the sample ingestion playbooks into a per-configuration
collection, activate them, create the schedule, and write the metadata record:

```python
result = conn.data_ingest_wizard(
    "fortinet-fortisiem",
    config="prod",
    cron="*/15 * * * *",          # schedule; omit to build playbooks only
)
result.collection_uuid            # '1f9b6533-...' (the config_id)
result.playbooks                  # [Workflow(...), Workflow(...), Workflow(...)]
result.schedule_id                # 'Ingestion_fortinet-fortisiem_prod_1f9b6533-...'
result.existed                    # False -- the wizard built it
```

`ensure_ingestion()` is the idempotent front door -- set up only if it isn't
already, without writing anything on the second call:

```python
first = conn.ensure_ingestion("fortinet-fortisiem", config="prod", cron="*/15 * * * *")
first.existed                     # False -- the wizard built it
again = conn.ensure_ingestion("fortinet-fortisiem", config="prod")
again.existed                     # True -- returned as-is, no writes
```

`ingestion_status()` is the read-only check -- "is ingestion set up for this
config, and is its schedule running?" In the demo box nothing is configured:

```{doctest}
>>> status = conn.ingestion_status("mitre-attack")
>>> status.configured
False
```

`trigger_ingestion()` fires the ingest playbook right now -- the *Trigger
Ingestion Now* button. It bypasses the scheduler, so it works even when the
schedule is disabled or absent:

```python
conn.trigger_ingestion("fortinet-fortisiem", config="prod")
# {'task_id': '9d4af948-2a04-4d1e-9ab1-b83d3252ce18'}
```

`remove_ingestion()` tears it all down -- delete the schedule, the metadata
record, and (by default) the per-config collection with its cloned playbooks:

```python
conn.remove_ingestion("fortinet-fortisiem", config="prod")
# IngestionTeardownResult(connector='fortinet-fortisiem', config_id='1f9b6533-...',
#   schedule_deleted=True, metadata_deleted=1, collection_deleted=True)

conn.remove_ingestion("fortinet-fortisiem", config="prod",
                       delete_collection=False)   # keep the playbooks
```

## Idempotent configuration helpers

`create_configuration` 400s on the second call with the same `name`.
`upsert_configuration` is the idempotent write -- create-or-update by name --
that the UI's *Save* button performs. Safe to re-run from a deploy script:

```{doctest}
>>> cfg = conn.upsert_configuration(
...     "virustotal",
...     {"server": "www.virustotal.com", "api_key": "test-doctest-key", "verify_ssl": True},
...     name="pyfsr-doctest-config",
...     validate=False,
...     autofill=False,
... )
>>> (cfg.name, cfg.config["server"])
('pyfsr-doctest-config', 'www.virustotal.com')
>>> cfg2 = conn.upsert_configuration(
...     "virustotal",
...     {"server": "www.virustotal.com", "api_key": "test-rotated-key", "verify_ssl": True},
...     name="pyfsr-doctest-config",
...     validate=False,
...     autofill=False,
... )
>>> cfg2.config_id == cfg.config_id
True
>>> conn.delete_configuration(cfg.config_id) is None
True
```

`default_config()` builds a schema-complete starting point -- every field
filled with its declared default, including `onchange`-revealed sub-fields -- so
you only override what you need:

```python
cfg = conn.default_config("code-snippet")   # {"allow_imports": False, "restrict_imports": ""}
cfg["allow_imports"] = True
conn.upsert_configuration("code-snippet", cfg, name="dev")
```

`ensure_configured()` is the one-call setup: install from Content Hub if the
connector isn't there yet, then create-or-update the named config:

```python
conn.ensure_configured(
    "servicenow",
    {"server_url": "https://snow.example.com", "username": "api", "password": "<pw>"},
    config_name="prod",
    version="4.4.5",       # only needed if the connector isn't installed yet
    default=True,
)
```

## Connector Studio dev workspace

Edit a checked-out connector's source, then publish it onto the running
appliance -- the same flow as the in-product Studio editor.

```python
dev = conn.dev_list()                       # connectors checked out for editing
entity_id = dev[0]["id"]

conn.dev_edit(entity_id)                     # open for editing (Studio "Edit")
conn.dev_read_file(entity_id, "/hello-world_1_0_0_dev/info.json")
conn.dev_write_file(entity_id, {"path": "info.json", "content": "{...}"})
conn.dev_publish(entity_id, replace=True)    # land changes + refresh integrations
```

```{note}
`dev_publish()` is also the supported escape hatch when a same-version `.tgz`
upload left stale code cached in the integrations service -- it triggers a
service refresh the standard `$replace=true` install path does not.
```

## Install / uninstall

```python
# Appliance (self-agent):
conn.install("fortinet-fortisiem", "6.1.0", wait=True)   # by name from Content Hub
conn.install_from_file("hello-world-1.0.0.tgz", replace=True)  # upload a .tgz bundle
conn.uninstall("fortinet-fortisiem")

# Remote agent:
client.agents.install_connector(agent_id, name="cyops_utilities", version="3.7.1")
client.agents.upgrade_connector(agent_id, name="cyops_utilities", version="3.8.0")
client.agents.uninstall_connector(agent_id, name="cyops_utilities", version="3.8.0")

client.agents.heartbeat(agent_id)            # liveness over the secure-message bus
```

```{warning}
Appliance uninstall ({meth}`~pyfsr.api.connectors.ConnectorsAPI.uninstall`) and
agent uninstall ({meth}`~pyfsr.api.agents.AgentsAPI.uninstall_connector`) are
distinct: the first removes the connector from the appliance's self-agent by
integer id, the second removes it from a named remote agent.
```

A connector's Python dependencies (from its `requirements.txt`) are installed
automatically as part of the install. If that auto-install fails, operations
blow up at runtime even though the configuration health is green. Check with
`dependencies_status()` and retry with `install_dependencies()`:

```{doctest}
>>> ds = conn.dependencies_status("mitre-attack")
>>> ds.dependencies_installed
True
```

```python
conn.install_dependencies("mitre-attack")   # retry the failed auto-install
```
