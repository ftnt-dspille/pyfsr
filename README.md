# pyfsr

[![PyPI version](https://badge.fury.io/py/pyfsr.svg)](https://badge.fury.io/py/pyfsr)
[![Python versions](https://img.shields.io/pypi/pyversions/pyfsr)](https://pypi.org/project/pyfsr/)
[![License: MIT](https://img.shields.io/pypi/l/pyfsr)](https://github.com/ftnt-dspille/pyfsr/blob/main/LICENSE)
[![Tests](https://github.com/ftnt-dspille/pyfsr/actions/workflows/pr-tests.yml/badge.svg)](https://github.com/ftnt-dspille/pyfsr/actions/workflows/pr-tests.yml)
[![Documentation Status](https://github.com/ftnt-dspille/pyfsr/actions/workflows/docs.yml/badge.svg)](https://github.com/ftnt-dspille/pyfsr/actions/workflows/docs.yml)
[![codecov](https://codecov.io/gh/ftnt-dspille/pyfsr/branch/main/graph/badge.svg)](https://codecov.io/gh/ftnt-dspille/pyfsr)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

[Documentation](https://ftnt-dspille.github.io/pyfsr/) · [Installation](#installation) · [Quick start](#quick-start) · [CLI](#command-line-tools) · [AI / agents](#ai--agent-friendly)

**pyfsr** is a batteries-included Python client for the FortiSOAR REST API. It
gives you a typed query/CRUD layer over any module, picklist resolution,
connector execution, playbook-run history, safe deletes — and a ready-made
**AI/agent surface** (tool-schema registry + an optional MCP server) so an agent
can drive FortiSOAR with no glue code.

There's also a `pyfsr` CLI for the things you reach for outside a script:
poking at an appliance's health and services, and authoring playbooks in YAML
and pushing them to a live instance.

Python 3.10+ · Pydantic v2 · MIT.

## Installation

```bash
pip install pyfsr
# with the optional generic MCP server:
pip install 'pyfsr[mcp]'
```

## Quick start

```python
from pyfsr import FortiSOAR, Query

# API-key auth, or ("username", "password")
client = FortiSOAR("soar.example.com", "your-api-key")

# Generic, typed CRUD for ANY module via client.records(module)
incidents = client.records("incidents")

inc = incidents.get("0d2c...")          # by uuid, "module:uuid", or full IRI
inc["name"], inc.uuid                    # records are dict- AND attribute-accessible

# Structured queries with a fluent builder -> a HydraPage you can iterate
page = incidents.query(
    Query().eq("status.itemValue", "Open").like("name", "phish").limit(50)
)
for inc in incidents.iterate(Query().eq("status.itemValue", "Open")):
    ...                                  # lazily walks every page

# Create / update / delete (soft by default; hard= for permanent)
new = incidents.create({"name": "Suspicious login", "severity": "High"},
                       resolve_picklists=True)   # friendly values -> IRIs
incidents.update(new.uuid, {"status": "Closed"}, resolve_picklists=True)
incidents.delete(new.uuid)               # delete(..., hard=True) to purge
```

### Configure from the environment

```python
from pyfsr import EnvConfig

# reads FSR_BASE_URL (+ FSR_API_KEY or FSR_USERNAME/FSR_PASSWORD),
# FSR_PORT, FSR_VERIFY_SSL, FSR_TIMEOUT
client = EnvConfig.from_env().client()
```

## Features

- **Generic record access** — `client.records(module)` for CRUD on any module;
  no hand-built `/api/3/...` URLs or Hydra unwrapping.
- **Query DSL** — `Query().eq(...).in_(...).group(...).sort(...).limit(...)`,
  compiled to the FortiSOAR query-body shape (pagination handled for you).
- **Typed models** — Alert/Incident/Task/Comment come back as Pydantic v2
  models that are also dict-compatible; unknown modules fall back to a lenient
  `BaseRecord`, so custom fields/modules never break.
- **Picklists** — `client.picklists` resolves friendly values (`"High"`) to
  IRIs and discovers which picklist a `(module, field)` binds to.
- **Connectors** — `client.connectors` lists configured connectors, runs
  healthchecks, and executes operations.
- **Playbooks** — `client.playbooks` merges live + historical run history and
  resumes manual-input steps.
- **Safe deletes** — soft-delete/restore + guarded single-row hard delete.
- **Schema discovery** — `client.list_modules()` / `client.describe_module()`.
- **Resilient transport** — configurable `timeout=`, automatic retry with
  backoff on idempotent requests (429/5xx), and secrets masked in verbose logs.
- **Bundled OpenAPI spec** — `pyfsr.spec.load_spec()` for offline reference and
  `drift(client)` to compare the spec against a live appliance.

## AI / agent-friendly

pyfsr ships a transport-neutral **tool registry** for the core operations, with
token-efficient results and structured (never-raised) errors — feed it to
Anthropic tool-use, OpenAI function calling, your own agent loop, or the bundled
MCP server.

```python
from pyfsr.agent.tools import to_anthropic_tools, to_openai_tools, dispatch

tools = to_anthropic_tools()             # or to_openai_tools(), or tool_schemas()

# ... your model picks a tool ...
result = dispatch(client, "search_records",
                  {"module": "alerts", "summary": True, "limit": 10})
# result is JSON-safe and trimmed; failures come back as {"error": {...}}
```

Reads accept `summary=True` or `fields=[...]` to keep payloads small:

```python
client.records("alerts").query(Query().limit(20), summary=True)
```

### Generic MCP server

Point any MCP-capable agent at any FortiSOAR with one command:

```bash
pip install 'pyfsr[mcp]'
FSR_BASE_URL=soar.example.com FSR_API_KEY=... python -m pyfsr.agent.mcp
```

It exposes the same registry (record CRUD, schema discovery, picklists,
connectors, playbook runs) as MCP tools — generic and dependency-light,
distinct from any domain-specific FortiSOAR MCP.

## Command-line tools

Installing pyfsr puts a `pyfsr` command on your path with six groups.

**`pyfsr appliance`** — operational verbs against a FortiSOAR box (most run over
SSH/sudo and stay dependency-light on the far end):

```bash
pyfsr appliance info                 # host, version, content DB, device UUID
pyfsr appliance host                 # mem / swap / load / per-service RSS / disk
pyfsr appliance service restart cyops-postman --yes
pyfsr appliance db                   # Postgres verbs, multi-DB aware
pyfsr appliance es                   # Elasticsearch health + shard state
pyfsr appliance license              # licensing / identity, drift check
pyfsr appliance content-hub sync     # pull the Content Hub catalog + artifacts
```

Other appliance subgroups: `mq` (RabbitMQ), `ha`, `certs`, `logs`, and
`diagnose` (runs `fsr_diagnose.sh`). `--help` on any of them lists the verbs.

**`pyfsr playbook`** — author playbooks as YAML and deploy them:

```bash
pyfsr playbook steps                 # list every step type you can write
pyfsr playbook step-help TYPE        # keys + a compiling example for one type
pyfsr playbook examples              # foundational playbook library (--intent/--stage/--manifest)
pyfsr playbook show SLUG             # print one library playbook's metadata + YAML
pyfsr playbook validate flow.yaml    # compile + report diagnostics (offline)
pyfsr playbook compile flow.yaml     # emit the FSR import envelope (offline)
pyfsr playbook lint flow.yaml        # live preflight: connector steps missing config
pyfsr playbook deploy flow.yaml      # compile and create it on the appliance
```

**`pyfsr records`** — query and manage FortiSOAR records over the API:

```bash
pyfsr records alerts [--status Open] [--severity High]
pyfsr records incidents '<field=value or Query DSL JSON>'
pyfsr records delete <module> <uuid...> [--yes]
```

**`pyfsr repo`** — discover and download from Fortinet's content repo (no
appliance needed).

**`pyfsr widget`** — upload and publish widgets on a live appliance.

**`pyfsr mcp`** — call FortiSOAR's own native MCP tool gateway
(`list-tools` / `call`), distinct from the generic `pyfsr.agent.mcp` server.

## Development

```bash
uv sync
uv run pytest -q                 # unit tests (live tests deselected by default)
uvx ruff check src tests
```

Live integration tests run with `pytest -m integration` and need an
`examples/config.toml` pointing at a FortiSOAR instance.

## License

MIT — see [LICENSE](LICENSE).
