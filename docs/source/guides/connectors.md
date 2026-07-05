# Connectors

`client.connectors` ({class}`~pyfsr.api.connectors.ConnectorsAPI`) wraps
FortiSOAR's `/api/integration` surface — discovery, healthcheck, configuration,
operation execution, the Connector Studio dev workspace, and install/uninstall.
`client.agents` ({class}`~pyfsr.api.agents.AgentsAPI`) covers the remote
*execution agent* side: pushing, upgrading, and removing a connector on an
agent, plus a liveness heartbeat.

A complete, runnable walkthrough lives in
[`examples/manage_connectors.py`](https://github.com/dylanspille/pyfsr/blob/main/examples/manage_connectors.py)
— it defaults to read-only and exercises every method below.

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

`connector_detail` fetches a connector's full record — its operations (each with
parameters + output_schema) and configurations. Captured live and trimmed to a
doctest-friendly slice (the `config` dict on each configuration is dropped — it
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

`execute()` returns a typed {class}`~pyfsr.models.ExecuteResult` — `.ok` is the
`status == "Success"` check, `.data` is the connector's own output (shape varies
by connector/operation). Live-verified against `cisa-advisory`'s
`get_known_exploited_vulnerability_cves` — a public, read-only, parameter-less
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

⚠️ For an **agent-bound** connector (see the module warning), `execute()` is
fire-and-forget — it returns immediately with an in-progress status and empty
`data`; the real result is pushed over a websocket, not pollable here.

## Connector Studio dev workspace

Edit a checked-out connector's source, then publish it onto the running
appliance — the same flow as the in-product Studio editor.

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
upload left stale code cached in the integrations service — it triggers a
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
