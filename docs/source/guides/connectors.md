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

```python
conn = client.connectors

conn.list_configured()                 # installed + configured connectors
conn.resolve_version("cyops_utilities")  # -> "3.7.1" (or None if not installed)

# The spec-canonical operations-discovery endpoint (POST by integer id):
detail = conn.connector_detail("cyops_utilities")
[op["operation"] for op in detail["operations"]]

# The dedicated, filterable configuration listing:
conn.list_configurations(name="cyops_utilities", active=True)

conn.healthcheck("fortinet-fortisiem")   # {status: "Available", ...}
```

## Executing an operation

```python
conn.execute("virustotal", "get_reputation_ip", params={"ip": "8.8.8.8"})
# {'operation': 'get_reputation_ip', 'status': 'Success', 'data': {...}}
```

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
