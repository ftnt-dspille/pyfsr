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

```{code-block} python
# Typed, module-specific API
alerts = client.alerts.list()
alert = client.alerts.get("alert-uuid")

# Generic CRUD against any module
incidents = client.records("incidents")
incident = incidents.get("incident-uuid")

# Raw REST escape hatch
data = client.get("/api/3/alerts")
```

## Creating records & picklists

Picklist fields (`severity`, `status`, …) are stored as IRIs, not friendly
strings — but pyfsr resolves friendly values for you automatically, so you can
just pass `"High"`:

```{code-block} python
alert = client.alerts.create(
    name="Test Alert",
    description="This is a test alert",
    severity="High",        # resolved to its IRI automatically
)

# Same on the generic record path:
client.records("incidents").create({"name": "Breach", "severity": "Critical"})
```

```{note}
Resolution is on by default. Pass `resolve_picklists=False` to skip it (and the
metadata lookup it needs) when every value is already an IRI.
```

## Next steps

- {doc}`guides/records` — generic CRUD and typed models
- {doc}`guides/querying` — the fluent `Query` DSL
- {doc}`guides/ai-agents` — drive FortiSOAR from an LLM
