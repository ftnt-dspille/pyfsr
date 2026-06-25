# API Reference

The complete, auto-generated reference for every pyfsr module and class.

## Module overview

- **Client** — main FortiSOAR client ({class}`pyfsr.client.FortiSOAR`)
- **Records** — generic CRUD over any module ({class}`pyfsr.records.RecordSet`)
- **Query** — fluent query DSL ({class}`pyfsr.query.Query`)
- **Pagination** — Hydra page helpers ({class}`pyfsr.pagination.HydraPage`, {func}`pyfsr.pagination.paginate`)
- **Models** — typed Pydantic records (`Alert`, `Incident`, …)
- **Config** — environment-driven setup ({class}`pyfsr.config.EnvConfig`)
- **Tools** — agent tool registry ({mod}`pyfsr.tools`)
- **MCP** — bundled Model Context Protocol server ({mod}`pyfsr.mcp`)

## Full reference

The per-module pages below are auto-generated. They're listed flat (instead of
nested under the `pyfsr` package) so every module is one click from here.

```{toctree}
:maxdepth: 2
:caption: Core

autoapi/pyfsr/client/index
autoapi/pyfsr/records/index
autoapi/pyfsr/query/index
autoapi/pyfsr/query_models/index
autoapi/pyfsr/pagination/index
autoapi/pyfsr/projection/index
autoapi/pyfsr/fields/index
autoapi/pyfsr/config/index
autoapi/pyfsr/exceptions/index
```

```{toctree}
:maxdepth: 2
:caption: Endpoint APIs & models

autoapi/pyfsr/api/index
autoapi/pyfsr/models/index
autoapi/pyfsr/auth/index
```

```{toctree}
:maxdepth: 2
:caption: Playbooks, tooling & CLI

Playbooks (authoring) <autoapi/pyfsr/authoring/index>
autoapi/pyfsr/archetypes/index
autoapi/pyfsr/playbook_freshness/index
autoapi/pyfsr/playbook_lint/index
autoapi/pyfsr/tools/index
autoapi/pyfsr/mcp/index
autoapi/pyfsr/cli/index
autoapi/pyfsr/appliance/index
autoapi/pyfsr/spec/index
autoapi/pyfsr/utils/index
```
