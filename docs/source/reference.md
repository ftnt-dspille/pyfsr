# API Reference

The complete, auto-generated reference for every pyfsr module and class.

## Module overview

- **Client** — main FortiSOAR client ({class}`pyfsr.client.FortiSOAR`)
- **Records** — generic CRUD over any module ({class}`pyfsr.records.RecordSet`)
- **Query** — fluent query DSL ({class}`pyfsr.query.Query`)
- **Pagination** — Hydra page helpers ({class}`pyfsr.pagination.HydraPage`, {func}`pyfsr.pagination.paginate`)
- **Models** — typed Pydantic records (`Alert`, `Incident`, …)
- **Config** — environment-driven setup ({class}`pyfsr.config.EnvConfig`)
- **Tools** — agent tool registry ({mod}`pyfsr.agent.tools`)
- **MCP** — bundled Model Context Protocol server ({mod}`pyfsr.agent.mcp`)

## Full reference

The per-module pages below are auto-generated. They're listed flat (instead of
nested under the `pyfsr` package) so every module is one click from here.

```{toctree}
:maxdepth: 1
:caption: Core

autoapi/pyfsr/client/index
autoapi/pyfsr/records/index
autoapi/pyfsr/query/index
autoapi/pyfsr/pagination/index
autoapi/pyfsr/fields/index
autoapi/pyfsr/config/index
autoapi/pyfsr/exceptions/index
```

```{toctree}
:maxdepth: 1
:caption: Endpoint APIs & models

autoapi/pyfsr/api/index
autoapi/pyfsr/models/index
autoapi/pyfsr/auth/index
```

```{toctree}
:maxdepth: 1
:caption: Playbook authoring

Compile & decompile YAML <autoapi/pyfsr/authoring/index>
```

```{toctree}
:maxdepth: 1
:caption: Agent & MCP

autoapi/pyfsr/agent/index
```

```{toctree}
:maxdepth: 1
:caption: CLI, appliance & content

autoapi/pyfsr/cli/index
autoapi/pyfsr/appliance/index
autoapi/pyfsr/repo/index
```

```{toctree}
:maxdepth: 1
:caption: Advanced & internal
:hidden:

autoapi/pyfsr/playbook_catalog/index
autoapi/pyfsr/playbook_freshness/index
autoapi/pyfsr/playbook_lint/index
autoapi/pyfsr/playbook_match/index
autoapi/pyfsr/concurrency/index
autoapi/pyfsr/projection/index
autoapi/pyfsr/query_models/index
autoapi/pyfsr/spec/index
autoapi/pyfsr/utils/index
```

## Advanced & internal

These back specific workflows (the playbook compiler's live-target preflight,
loop-concurrency analysis, token-efficient record projection, ...) rather than
everyday client usage, so they're left out of the sidebar. Linked here for
when you need them:

- {doc}`Playbook catalog <autoapi/pyfsr/playbook_catalog/index>` — step-type reference data behind `pyfsr playbook steps`
- {doc}`Playbook freshness <autoapi/pyfsr/playbook_freshness/index>` — catalog staleness probe
- {doc}`Playbook lint <autoapi/pyfsr/playbook_lint/index>` — live-target preflight for compiled playbooks
- {doc}`Playbook match <autoapi/pyfsr/playbook_match/index>` — client-side structural matching over playbook definitions
- {doc}`Concurrency <autoapi/pyfsr/concurrency/index>` — max-concurrent-execution analysis
- {doc}`Projection <autoapi/pyfsr/projection/index>` — token-efficient record summarization
- {doc}`Query models <autoapi/pyfsr/query_models/index>` — typed backing for the query DSL
- {doc}`Spec <autoapi/pyfsr/spec/index>` — bundled OpenAPI spec access
- {doc}`Utils <autoapi/pyfsr/utils/index>` — IRI/validation/file-operation helpers
