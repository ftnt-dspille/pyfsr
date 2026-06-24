---
hide-toc: false
---

# pyfsr

A modern, typed Python client for the **FortiSOAR** REST API — record CRUD, a
fluent query DSL, Pydantic models, and a framework-agnostic tool registry for
driving FortiSOAR from an LLM agent.

```{code-block} bash
pip install pyfsr
```

```{code-block} python
from pyfsr import FortiSOAR

client = FortiSOAR("soar.example.com", "your-api-token")
alerts = client.alerts.list()
```

---

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Getting Started
:link: getting-started
:link-type: doc

Install pyfsr, connect to an appliance, and make your first calls.
:::

:::{grid-item-card} Authentication
:link: guides/authentication
:link-type: doc

API keys vs. username/password, SSL options, and environment config.
:::

:::{grid-item-card} Working with Records
:link: guides/records
:link-type: doc

Generic CRUD over any module, typed models, and picklist resolution.
:::

:::{grid-item-card} Querying
:link: guides/querying
:link-type: doc

Build FortiSOAR queries fluently with the `Query` DSL and paginate results.
:::

:::{grid-item-card} Module Administration
:link: guides/module-admin
:link-type: doc

Create modules, add and alter fields, track pending changes, and publish.
:::

:::{grid-item-card} Field Schema Reference
:link: guides/module-field-schema
:link-type: doc

Every field type, its properties, and how relationship fields wire to other modules.
:::

:::{grid-item-card} Connectors
:link: guides/connectors
:link-type: doc

Discover, configure, execute, and install connectors; manage remote agents.
:::

:::{grid-item-card} Playbook Authoring
:link: guides/playbook-authoring
:link-type: doc

Author playbooks in YAML, compile them, and deploy through the API or CLI.
:::

:::{grid-item-card} AI & Agents
:link: guides/ai-agents
:link-type: doc

Expose FortiSOAR as tools to Claude, OpenAI, or the bundled MCP server.
:::

:::{grid-item-card} API Reference
:link: reference
:link-type: doc

The complete, auto-generated reference for every module and class.
:::

::::

```{toctree}
:hidden:
:caption: Guides

getting-started
guides/authentication
guides/records
guides/querying
guides/module-admin
guides/module-field-schema
guides/connectors
guides/playbook-authoring
guides/ai-agents
```

```{toctree}
:hidden:
:caption: Reference

reference
```
