# Getting Started

## Install

```{code-block} bash
pip install pyfsr
```

Optional extras:

```{code-block} bash
pip install "pyfsr[mcp]"    # bundled Model Context Protocol server
pip install "pyfsr[dev]"    # linters + test + docs + mcp extras
```

## Connect

Create a {class}`~pyfsr.client.FortiSOAR` client with either an API token or a
`(username, password)` tuple:

```{code-block} python
from pyfsr import FortiSOAR

# API token (recommended)
client = FortiSOAR("soar.example.com", "your-api-token")

# Username / password
client = FortiSOAR("soar.example.com", ("admin", "password"))
```

For self-signed appliances you can disable certificate verification (and
silence the resulting warnings):

```{code-block} python
client = FortiSOAR(
    "soar.example.com",
    "your-api-token",
    verify_ssl=False,
    suppress_insecure_warnings=True,
)
```

See {doc}`guides/authentication` for environment-based config.

## First calls

```{doctest}
>>> client = demo_client()

>>> # Generic CRUD against any module
>>> incidents = client.records("incidents")
>>> incident = incidents.get("0740411d-e852-4eee-b33b-596210d09a9b")
>>> incident["name"]
'pyfsr doctest incident'

>>> # Raw REST escape hatch
>>> data = client.get("/api/3/alerts")
>>> data["hydra:totalItems"]
1
```

`client.alerts.list()`/`.get(uuid)` work the same way as the generic path above,
via the typed, module-specific {class}`~pyfsr.api.alerts.AlertsAPI` -- see
{doc}`guides/records` for the typed-model walkthrough.

## Creating records & picklists

Picklist fields (`severity`, `status`, …) are stored as IRIs, not friendly
strings -- but pyfsr resolves friendly values for you automatically, so you can
just pass `"High"`:

```{code-block} python
alert = client.alerts.create(
    name="Test Alert",
    description="This is a test alert",
    severity="High",        # resolved to its IRI automatically
)
```

Same on the generic record path -- captured live (created, fetched, deleted in
the same session; box left with no extra incidents):

```{doctest}
>>> created = client.records("incidents").create(
...     {"name": "pyfsr doctest incident", "description": "temporary, will be deleted",
...      "severity": "Critical"},
...     resolve_picklists=False,   # already an IRI-resolved doctest fixture; see note below
... )
>>> created["name"]
'pyfsr doctest incident'
```

```{note}
Resolution is on by default (the example above passes `resolve_picklists=False`
only because this doctest replays a captured response rather than a live
metadata lookup). Pass it yourself to skip resolution -- and the metadata lookup
it needs -- when every value you send is already an IRI.
```

## Triggering playbooks

Fire a playbook and wait for it to finish in one call with
{meth}`~pyfsr.api.playbooks.PlaybooksAPI.run_and_wait`:

```{code-block} python
result = client.playbooks.run_and_wait("My Playbook", timeout=60)
if result.succeeded:
    for step in result.steps:
        print(f"  {step.name:30} {step.status:10} {step.duration_ms}ms")
else:
    print(f"failed at: {result.failure.failing_step}")
    print(f"  error: {result.failure.error_message}")
```

For playbooks that pause on a **Manual Input** step, use
{meth}`~pyfsr.api.playbooks.PlaybooksAPI.trigger` then
{meth}`~pyfsr.api.manual_input.ManualInputAPI.answer` to resume. See
{doc}`guides/playbook-authoring` for the full interactive flow.

## Next steps

- {doc}`guides/records` -- generic CRUD and typed models
- {doc}`guides/querying` -- the fluent `Query` DSL
- {doc}`guides/playbook-authoring` -- author, deploy, and trigger playbooks from YAML
- {doc}`guides/playbook-yaml-reference` -- the full step-type syntax reference
- {doc}`guides/ai-agents` -- drive FortiSOAR from an LLM
