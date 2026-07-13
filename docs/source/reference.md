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

autoapi/pyfsr/authoring/index
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
autoapi/pyfsr/content_catalog/index
```

```{toctree}
:maxdepth: 1
:caption: Advanced & internal

Advanced & internal <reference-advanced>
```
