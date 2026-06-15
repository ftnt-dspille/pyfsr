"""Framework-agnostic tool registry for driving FortiSOAR from an LLM agent.

This is the foundation of pyfsr's AI/agent surface: a declarative catalogue of
the core FortiSOAR operations (record CRUD, discovery, picklists, connectors,
playbook runs) as JSON-Schema tool definitions, plus a :func:`dispatch` that
executes a tool call against a live :class:`~pyfsr.client.FortiSOAR` client and
returns JSON-serializable, token-trimmed results.

It is deliberately transport-neutral — no MCP, no provider SDK — so it can feed:

- the optional bundled MCP server (``python -m pyfsr.mcp``, a thin consumer),
- Anthropic tool-use (:func:`to_anthropic_tools`),
- OpenAI-style function calling (:func:`to_openai_tools`),
- or any home-grown agent loop (:func:`tool_schemas` + :func:`dispatch`).

Example::

    from pyfsr import FortiSOAR
    from pyfsr.tools import to_anthropic_tools, dispatch

    client = FortiSOAR("soar.example.com", api_key)
    tools = to_anthropic_tools()                       # feed to Claude
    # ... model decides to call a tool ...
    result = dispatch(client, "search_records",
                      {"module": "alerts", "summary": True, "limit": 10})

Every result is JSON-safe and every failure is returned as a structured
``{"error": {...}}`` dict (never a raised exception), so an agent can read the
message and self-correct.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .exceptions import FortiSOARException
from .projection import project, to_jsonable

# --------------------------------------------------------------------------- spec


@dataclass(frozen=True)
class ToolSpec:
    """A single agent-callable FortiSOAR operation.

    ``input_schema`` is a JSON Schema (draft 2020-12 compatible object schema)
    describing the tool's arguments; ``handler`` runs the operation against a
    live client and returns a JSON-serializable result.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]

    def to_dict(self) -> dict[str, Any]:
        """Generic ``{name, description, input_schema}`` (no handler)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# Reusable schema fragments -------------------------------------------------

_MODULE = {
    "type": "string",
    "description": "Module type, e.g. 'alerts' or 'incidents'. Use list_modules to discover.",
}
_REF = {
    "type": "string",
    "description": "Record reference: bare uuid, 'module:uuid' shorthand, or full /api/3/... IRI.",
}
_FIELDS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Return only these fields (the record's @id/uuid are always kept).",
}
_SUMMARY = {
    "type": "boolean",
    "description": "Return a compact identity + triage summary instead of the full record(s).",
}
_LIMIT = {"type": "integer", "description": "Maximum number of records to return.", "minimum": 1}


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


# --------------------------------------------------------------------------- handlers


def _h_list_modules(client) -> Any:
    return {"modules": client.list_modules()}


def _h_describe_module(client, *, module: str) -> Any:
    return client.describe_module(module)


def _h_get_record(client, *, module, ref, fields=None, summary=False) -> Any:
    rec = client.records(module).get(ref)
    return project(rec, fields=fields, summary=summary)


def _h_search_records(client, *, module, term="", limit=30, fields=None, summary=False) -> Any:
    page = client.records(module).search(term, limit=limit)
    return project(page, fields=fields, summary=summary)


def _h_query_records(
    client, *, module, filters=None, logic="AND", sort=None, limit=30, fields=None, summary=False
) -> Any:
    body: dict[str, Any] = {"logic": logic, "filters": filters or []}
    if sort:
        body["sort"] = sort
    if limit:
        body["limit"] = limit
    page = client.records(module).query(body)
    return project(page, fields=fields, summary=summary)


def _h_create_record(client, *, module, data, resolve_picklists=False) -> Any:
    rec = client.records(module).create(data, resolve_picklists=resolve_picklists)
    return to_jsonable(rec)


def _h_update_record(client, *, module, ref, data, resolve_picklists=False) -> Any:
    rec = client.records(module).update(ref, data, resolve_picklists=resolve_picklists)
    return to_jsonable(rec)


def _h_delete_record(client, *, module, ref, hard=False) -> Any:
    client.records(module).delete(ref, hard=hard)
    return {"deleted": ref, "module": module, "hard": bool(hard)}


def _h_list_picklists(client) -> Any:
    return {"picklists": client.picklists.list()}


def _h_get_picklist_values(client, *, name) -> Any:
    return {"name": name, "values": client.picklists.values(name)}


def _h_resolve_picklist(client, *, value, picklist=None, module=None, field=None) -> Any:
    iri = client.picklists.resolve(value, picklist=picklist, module=module, field=field)
    return {"value": value, "iri": iri, "resolved": iri is not None}


def _h_list_connectors(client) -> Any:
    return {"connectors": client.connectors.list_configured()}


def _h_healthcheck_connector(client, *, connector, config=None) -> Any:
    return client.connectors.healthcheck(connector, config=config)


def _h_run_connector_operation(
    client, *, connector, operation, params=None, config_name=None
) -> Any:
    return client.connectors.execute(
        connector, operation, params=params or {}, config_name=config_name
    )


def _h_list_playbook_runs(client, *, playbook=None, limit=20) -> Any:
    return {"runs": client.playbooks.runs(playbook=playbook, limit=limit)}


def _h_get_playbook_run(client, *, run_pk) -> Any:
    return client.playbooks.get(run_pk)


def _h_investigate_alert(client, *, ref, wait=False, timeout=600) -> Any:
    return client.ai.investigate_alert(ref, wait=bool(wait), timeout=timeout)


def _h_get_investigation_result(client, *, task_id) -> Any:
    return {"status": client.ai.get_status(task_id), "result": client.ai.get_result(task_id)}


def _h_list_ai_config(client) -> Any:
    return {
        "features_enabled": client.ai.features_enabled(),
        "llm_configs": client.ai.list_llm_configs(),
        "mcp_servers": client.ai.list_mcp_servers(),
    }


# --------------------------------------------------------------------------- registry

_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "list_modules",
        "List every module (type/label/plural) on the FortiSOAR appliance. Start here to "
        "discover the correct module type before reading or writing records.",
        _obj({}),
        _h_list_modules,
    ),
    ToolSpec(
        "describe_module",
        "Describe one module's fields: name, title, type, required-ness, and the picklist a "
        "field binds to. Use before creating/updating records to know the field shape.",
        _obj({"module": _MODULE}, ["module"]),
        _h_describe_module,
    ),
    ToolSpec(
        "get_record",
        "Fetch a single record by reference. Pass summary=true or fields=[...] to keep the "
        "result small.",
        _obj(
            {"module": _MODULE, "ref": _REF, "fields": _FIELDS, "summary": _SUMMARY},
            ["module", "ref"],
        ),
        _h_get_record,
    ),
    ToolSpec(
        "search_records",
        "Free-text search a module. Returns a page of records; use summary=true to keep the "
        "output compact when scanning many results.",
        _obj(
            {
                "module": _MODULE,
                "term": {"type": "string", "description": "Free-text search term."},
                "limit": _LIMIT,
                "fields": _FIELDS,
                "summary": _SUMMARY,
            },
            ["module"],
        ),
        _h_search_records,
    ),
    ToolSpec(
        "query_records",
        "Structured query of a module with filter conditions. Each filter is "
        "{field, operator, value}; operator is one of eq/neq/gt/gte/lt/lte/in/contains/like/etc.",
        _obj(
            {
                "module": _MODULE,
                "filters": {
                    "type": "array",
                    "description": "Filter conditions, each {field, operator, value}.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "operator": {"type": "string"},
                            "value": {},
                        },
                        "required": ["field", "operator"],
                    },
                },
                "logic": {
                    "type": "string",
                    "enum": ["AND", "OR"],
                    "description": "How to combine filters (default AND).",
                },
                "sort": {
                    "type": "array",
                    "description": "Sort clauses, each {field, direction} (direction ASC/DESC).",
                    "items": {"type": "object"},
                },
                "limit": _LIMIT,
                "fields": _FIELDS,
                "summary": _SUMMARY,
            },
            ["module"],
        ),
        _h_query_records,
    ),
    ToolSpec(
        "create_record",
        "Create a record in a module. data is a field->value mapping; set resolve_picklists=true "
        "to pass friendly picklist values (e.g. 'High') and have them mapped to IRIs.",
        _obj(
            {
                "module": _MODULE,
                "data": {
                    "type": "object",
                    "description": "Field -> value mapping for the new record.",
                },
                "resolve_picklists": {
                    "type": "boolean",
                    "description": "Map friendly picklist values to IRIs before sending.",
                },
            },
            ["module", "data"],
        ),
        _h_create_record,
    ),
    ToolSpec(
        "update_record",
        "Update an existing record by reference. data carries the fields to change.",
        _obj(
            {
                "module": _MODULE,
                "ref": _REF,
                "data": {"type": "object", "description": "Field -> value mapping to update."},
                "resolve_picklists": {
                    "type": "boolean",
                    "description": "Map friendly picklist values to IRIs before sending.",
                },
            },
            ["module", "ref", "data"],
        ),
        _h_update_record,
    ),
    ToolSpec(
        "delete_record",
        "Delete a single record by reference. Soft-delete by default (recycle bin where "
        "supported); set hard=true to permanently delete. Never operates collection-wide.",
        _obj(
            {
                "module": _MODULE,
                "ref": _REF,
                "hard": {
                    "type": "boolean",
                    "description": "Permanently delete instead of soft-delete.",
                },
            },
            ["module", "ref"],
        ),
        _h_delete_record,
    ),
    ToolSpec(
        "list_picklists",
        "List every picklist name on the appliance.",
        _obj({}),
        _h_list_picklists,
    ),
    ToolSpec(
        "get_picklist_values",
        "List a picklist's items (itemValue, uuid, iri, ordinal).",
        _obj(
            {"name": {"type": "string", "description": "Picklist name, e.g. 'Severity'."}}, ["name"]
        ),
        _h_get_picklist_values,
    ),
    ToolSpec(
        "resolve_picklist",
        "Resolve a friendly picklist value (e.g. 'High') to its IRI. Provide either an explicit "
        "picklist name or a (module, field) pair to auto-discover the picklist.",
        _obj(
            {
                "value": {
                    "type": "string",
                    "description": "Friendly value to resolve, e.g. 'High'.",
                },
                "picklist": {"type": "string", "description": "Explicit picklist name."},
                "module": _MODULE,
                "field": {
                    "type": "string",
                    "description": "Field name to discover the picklist from.",
                },
            },
            ["value"],
        ),
        _h_resolve_picklist,
    ),
    ToolSpec(
        "list_connectors",
        "List installed + configured connectors with their versions and configurations.",
        _obj({}),
        _h_list_connectors,
    ),
    ToolSpec(
        "healthcheck_connector",
        "Live-check whether a connector configuration is reachable. status='Available' is green; "
        "status='no-config' means it isn't configured on this instance.",
        _obj(
            {
                "connector": {
                    "type": "string",
                    "description": "Connector name, e.g. 'virustotal'.",
                },
                "config": {"type": "string", "description": "Optional configuration UUID."},
            },
            ["connector"],
        ),
        _h_healthcheck_connector,
    ),
    ToolSpec(
        "run_connector_operation",
        "Execute one connector operation. For agent-bound connectors the call is fire-and-forget "
        "and returns empty data (not a failure).",
        _obj(
            {
                "connector": {
                    "type": "string",
                    "description": "Connector name, e.g. 'virustotal'.",
                },
                "operation": {
                    "type": "string",
                    "description": "Operation name, e.g. 'get_reputation_ip'.",
                },
                "params": {"type": "object", "description": "Operation parameters."},
                "config_name": {
                    "type": "string",
                    "description": "Select a non-default configuration by name.",
                },
            },
            ["connector", "operation"],
        ),
        _h_run_connector_operation,
    ),
    ToolSpec(
        "list_playbook_runs",
        "List recent playbook runs (live + historical, newest first). Scope to one playbook by "
        "name.",
        _obj(
            {
                "playbook": {
                    "type": "string",
                    "description": "Playbook name to scope to (optional).",
                },
                "limit": _LIMIT,
            }
        ),
        _h_list_playbook_runs,
    ),
    ToolSpec(
        "get_playbook_run",
        "Fetch one playbook run by its pk (the trailing id of a run's @id).",
        _obj({"run_pk": {"type": "string", "description": "The run's primary key."}}, ["run_pk"]),
        _h_get_playbook_run,
    ),
    ToolSpec(
        "investigate_alert",
        "Trigger a FortiAI agentic investigation of an alert (normalize → hypothesize → plan → "
        "gather evidence over MCP → verdict). Pass an alert reference; set wait=true to block for "
        "the final verdict, or wait=false to return a {task_id} to poll with "
        "get_investigation_result.",
        _obj(
            {
                "ref": _REF,
                "wait": {
                    "type": "boolean",
                    "description": "Block until the investigation reaches a verdict.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait when wait=true (default 600).",
                },
            },
            ["ref"],
        ),
        _h_investigate_alert,
    ),
    ToolSpec(
        "get_investigation_result",
        "Fetch the status and current result/verdict of a FortiAI investigation by its task_id.",
        _obj(
            {"task_id": {"type": "string", "description": "The investigation task id."}},
            ["task_id"],
        ),
        _h_get_investigation_result,
    ),
    ToolSpec(
        "list_ai_config",
        "Report the FortiAI configuration: whether AI features are enabled, the configured LLM "
        "reasoning profiles, and the registered MCP servers the agents can call.",
        _obj({}),
        _h_list_ai_config,
    ),
)

#: Registry keyed by tool name.
REGISTRY: dict[str, ToolSpec] = {t.name: t for t in _TOOLS}


# --------------------------------------------------------------------------- public API


def list_tools() -> list[ToolSpec]:
    """Return every registered :class:`ToolSpec`."""
    return list(_TOOLS)


def get_tool(name: str) -> ToolSpec:
    """Return one tool by name (raises ``KeyError`` if unknown)."""
    return REGISTRY[name]


def tool_schemas() -> list[dict[str, Any]]:
    """Generic ``[{name, description, input_schema}, ...]`` for every tool."""
    return [t.to_dict() for t in _TOOLS]


def to_anthropic_tools() -> list[dict[str, Any]]:
    """Tool definitions in Anthropic tool-use shape (``input_schema`` key)."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in _TOOLS
    ]


def to_openai_tools() -> list[dict[str, Any]]:
    """Tool definitions in OpenAI function-calling shape (``parameters`` key)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in _TOOLS
    ]


def _agent_error(exc: Exception, *, tool: str) -> dict[str, Any]:
    """Render an exception as a structured, agent-readable error dict."""
    err: dict[str, Any] = {
        "type": exc.__class__.__name__,
        "message": str(getattr(exc, "message", None) or exc) or repr(exc),
        "tool": tool,
    }
    if isinstance(exc, FortiSOARException):
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        if status is not None:
            err["status_code"] = status
    return {"error": err}


def dispatch(client, name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Execute a tool call against ``client`` and return a JSON-safe result.

    Looks ``name`` up in the :data:`REGISTRY` and invokes its handler with
    ``**arguments``. Any failure — unknown tool, bad arguments, or an API error —
    is returned as a structured ``{"error": {...}}`` dict rather than raised, so
    an agent loop never has to wrap the call in a try/except.
    """
    spec = REGISTRY.get(name)
    if spec is None:
        return {
            "error": {
                "type": "UnknownTool",
                "message": f"no such tool {name!r}; known tools: {sorted(REGISTRY)}",
                "tool": name,
            }
        }
    try:
        return spec.handler(client, **(arguments or {}))
    except TypeError as exc:  # bad/missing arguments for the handler
        return _agent_error(exc, tool=name)
    except Exception as exc:  # noqa: BLE001 - surface every failure as data to the agent
        return _agent_error(exc, tool=name)
