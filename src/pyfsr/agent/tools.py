"""Framework-agnostic tool registry for driving FortiSOAR from an LLM agent.

This is the foundation of pyfsr's AI/agent surface: a declarative catalogue of
the core FortiSOAR operations (record CRUD, discovery, picklists, connectors,
playbook runs) as JSON-Schema tool definitions, plus a :func:`dispatch` that
executes a tool call against a live :class:`~pyfsr.client.FortiSOAR` client and
returns JSON-serializable, token-trimmed results.

It is deliberately transport-neutral — no MCP, no provider SDK — so it can feed:

- the optional bundled MCP server (``python -m pyfsr.agent.mcp``, a thin consumer),
- Anthropic tool-use (:func:`to_anthropic_tools`),
- OpenAI-style function calling (:func:`to_openai_tools`),
- or any home-grown agent loop (:func:`tool_schemas` + :func:`dispatch`).

Example::

    from pyfsr import FortiSOAR
    from pyfsr.agent.tools import to_anthropic_tools, dispatch

    client = FortiSOAR("soar.example.com", token=api_key)
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

from ..exceptions import FortiSOARException
from ..projection import project, to_jsonable
from .archetypes import map_use_case

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
_CONNECTOR = {
    "type": "string",
    "description": "Connector name, e.g. 'virustotal' or 'code-snippet'. Use list_connectors to discover.",
}
_CONFIG = {
    "type": "object",
    "description": "Connector configuration field values (the connector's own field map). "
    "Use default_connector_config to get a complete, schema-valid starting point.",
}
_CONFIG_NAME = {
    "type": "string",
    "description": "A label for this configuration (required; what the UI shows).",
}
_CONFIG_ID = {
    "type": "string",
    "description": "A configuration UUID (the config_id). Reusing an existing one updates it.",
}
_PLAYBOOK = {
    "type": "string",
    "description": "Playbook name. Scope to one playbook by its display name.",
}


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


def _h_create_record(client, *, module, data, resolve_picklists=True) -> Any:
    rec = client.records(module).create(data, resolve_picklists=resolve_picklists)
    return to_jsonable(rec)


def _h_update_record(client, *, module, ref, data, resolve_picklists=True) -> Any:
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


def _h_run_connector_operation(client, *, connector, operation, params=None, config_name=None) -> Any:
    return client.connectors.execute(connector, operation, params=params or {}, config_name=config_name)


def _h_list_playbook_runs(client, *, playbook=None, limit=20) -> Any:
    runs = client.playbooks.execution_history(playbook=playbook, limit=limit)
    return {"runs": to_jsonable(runs)}


def _h_get_playbook_run(client, *, run_pk) -> Any:
    return to_jsonable(client.playbooks.get_execution(run_pk))


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


# -- module admin -----------------------------------------------------------
def _h_create_module(
    client,
    *,
    module,
    fields=None,
    label=None,
    plural=None,
    grant_to=None,
    options=None,
) -> Any:
    """Create a module in staging (publish to make it live). RBAC optional via grant_to."""
    kwargs: dict[str, Any] = {"label": label, "plural": plural, "fields": fields, "grant_to": grant_to}
    # Drop None-valued kwargs so create_module's own defaults apply; merge passthrough options.
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    if options:
        kwargs.update(options)
    return to_jsonable(client.modules_admin.create_module(module, **kwargs))


def _h_delete_module(
    client,
    *,
    module,
    detach_relationships=False,
    drop_orphan_tables=None,
    publish=True,
    timeout=600.0,
    poll_interval=10.0,
) -> Any:
    return client.modules_admin.delete_module(
        module,
        detach_relationships=detach_relationships,
        drop_orphan_tables=drop_orphan_tables,
        publish=publish,
        timeout=timeout,
        poll_interval=poll_interval,
    )


def _h_publish(client, *, timeout=600.0, poll_interval=10.0, precheck=True) -> Any:
    return client.modules_admin.publish(timeout=timeout, poll_interval=poll_interval, precheck=precheck)


# -- connector configuration ------------------------------------------------
def _h_default_connector_config(client, *, connector, version=None) -> Any:
    return client.connectors.default_config(connector, version=version)


def _h_validate_connector_config(client, *, connector, config, version=None) -> Any:
    result = client.connectors.validate_config(connector, config, version=version)
    # ConfigValidationResult -> JSON-safe projection of its problem fields.
    return {
        "valid": bool(getattr(result, "valid", False)),
        "missing": list(getattr(result, "missing", []) or []),
        "invalid": list(getattr(result, "invalid", []) or []),
        "unknown": list(getattr(result, "unknown", []) or []),
        "errors": list(getattr(result, "errors", []) or []),
    }


def _h_create_connector_configuration(
    client,
    *,
    connector,
    config,
    name,
    default=False,
    agent=None,
    validate=True,
    autofill=True,
    exist_ok=False,
    version=None,
) -> Any:
    return to_jsonable(
        client.connectors.create_configuration(
            connector,
            config,
            name=name,
            default=default,
            agent=agent,
            validate=validate,
            autofill=autofill,
            exist_ok=exist_ok,
            version=version,
        )
    )


def _h_update_connector_configuration(
    client,
    *,
    connector,
    config_id,
    config,
    name,
    default=False,
    agent=None,
    validate=True,
    autofill=True,
    version=None,
) -> Any:
    return to_jsonable(
        client.connectors.update_configuration(
            connector,
            config_id,
            config,
            name=name,
            default=default,
            agent=agent,
            validate=validate,
            autofill=autofill,
            version=version,
        )
    )


def _h_upsert_connector_configuration(
    client,
    *,
    connector,
    config,
    name,
    default=False,
    agent=None,
    validate=True,
    autofill=True,
    version=None,
) -> Any:
    return to_jsonable(
        client.connectors.upsert_configuration(
            connector,
            config,
            name=name,
            default=default,
            agent=agent,
            validate=validate,
            autofill=autofill,
            version=version,
        )
    )


# -- playbook run debugging -------------------------------------------------
def _h_last_playbook_run(client, *, playbook=None, playbook_uuid=None) -> Any:
    run = client.playbooks.last_run(playbook=playbook, playbook_uuid=playbook_uuid)
    return to_jsonable(run) if run is not None else {"run": None}


def _h_why_playbook_failed(client, *, playbook=None, playbook_uuid=None) -> Any:
    failure = client.playbooks.why_failed(playbook=playbook, playbook_uuid=playbook_uuid)
    return to_jsonable(failure) if failure is not None else {"failure": None}


def _h_wait_for_playbook_run(
    client,
    *,
    playbook=None,
    playbook_uuid=None,
    since=None,
    timeout=120,
    poll_interval=3,
) -> Any:
    run = client.playbooks.wait_for_run(
        playbook=playbook,
        playbook_uuid=playbook_uuid,
        since=since,
        timeout=timeout,
        poll_interval=poll_interval,
    )
    return to_jsonable(run)


# -- record upsert ----------------------------------------------------------
def _h_upsert_record(client, *, module, data, key=None, resolve_picklists=True) -> Any:
    rec = client.records(module).upsert(data, key=key, resolve_picklists=resolve_picklists)
    return to_jsonable(rec)


def _h_get_or_create_record(client, *, module, data, key="uuid", resolve_picklists=True) -> Any:
    record, created = client.records(module).get_or_create(data, key=key, resolve_picklists=resolve_picklists)
    return {"record": to_jsonable(record), "created": bool(created)}


# -- scheduling -------------------------------------------------------------
def _h_schedule_playbook(
    client,
    *,
    name,
    cron,
    playbook=None,
    playbook_uuid=None,
    timezone="UTC",
    enabled=True,
    exit_if_running=True,
) -> Any:
    if playbook_uuid is not None:
        workflow_iri = f"/api/3/workflows/{playbook_uuid}"
    elif playbook is not None:
        workflow_iri = client.playbooks.resolve_iri(playbook)
        if workflow_iri is None:
            raise ValueError(f"No playbook named {playbook!r}")
    else:
        raise ValueError("schedule_playbook requires 'playbook' (name) or 'playbook_uuid'")
    return to_jsonable(
        client.schedules.create(
            name,
            workflow_iri,
            cron,
            timezone=timezone,
            enabled=enabled,
            exit_if_running=exit_if_running,
        )
    )


def _h_trigger_schedule_now(client, *, name=None, task_id=None) -> Any:
    return to_jsonable(client.schedules.trigger_now(name=name, task_id=task_id))


def _h_delete_schedule(client, *, name) -> Any:
    client.schedules.delete(name)
    return {"deleted": True, "name": name}


# -- archetypes -------------------------------------------------------------
def _h_map_use_case(client, *, use_case) -> Any:
    # Classifies against the local archetype store (no appliance I/O -- `client` is unused).
    # The default store seeds itself from the shipped `reconcile-and-report` archetype on
    # first use; see map_use_case for the return shape.
    return map_use_case(use_case)


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
        "Fetch a single record by reference. Pass summary=true or fields=[...] to keep the result small.",
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
        "Create a record in a module. data is a field->value mapping; friendly picklist "
        "values (e.g. 'High') map to IRIs automatically — set resolve_picklists=false to skip.",
        _obj(
            {
                "module": _MODULE,
                "data": {
                    "type": "object",
                    "description": "Field -> value mapping for the new record.",
                },
                "resolve_picklists": {
                    "type": "boolean",
                    "description": "Map friendly picklist values to IRIs before sending "
                    "(default true; set false to skip).",
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
                    "description": "Map friendly picklist values to IRIs before sending "
                    "(default true; set false to skip).",
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
        _obj({"name": {"type": "string", "description": "Picklist name, e.g. 'Severity'."}}, ["name"]),
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
        "List recent playbook runs (live + historical, newest first). Scope to one playbook by name.",
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
    ToolSpec(
        "create_module",
        "Create a new module in STAGING (call publish to make it live). Define its fields and "
        "optionally grant a role permissions in one call via grant_to (e.g. "
        "['Full App Permissions']) — otherwise the new module gets no role permissions and "
        "record writes will 403 until you grant them. Returns the created staging module.",
        _obj(
            {
                "module": _MODULE,
                "fields": {
                    "type": "array",
                    "description": "Field definitions (each a {name, type, ...} dict). "
                    "Use describe_module on an existing module to see the field-spec shape.",
                    "items": {"type": "object"},
                },
                "label": {"type": "string", "description": "Display label (defaults to module name)."},
                "plural": {"type": "string", "description": "Plural label."},
                "grant_to": {
                    "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    "description": "Role name(s) to grant full CRUD+execute on the new module "
                    "(e.g. 'Full App Permissions'). Explicit opt-in; never auto-grants.",
                },
                "options": {
                    "type": "object",
                    "description": "Extra create_module options passed through (ownable, trackable, "
                    "indexable, taggable, queueable, recycle_bin, multi_tenancy, record_uniqueness, "
                    "default_sort, create_view_templates). All have sensible defaults.",
                },
            },
            ["module"],
        ),
        _h_create_module,
    ),
    ToolSpec(
        "delete_module",
        "Delete a module — the only operation that actually removes one. By default detaches "
        "reverse relationships, publishes the change, and (when drop_orphan_tables is set) drops "
        "the physical tables. Set publish=false to leave the delete in staging.",
        _obj(
            {
                "module": _MODULE,
                "detach_relationships": {
                    "type": "boolean",
                    "description": "Detach reverse-relationship references first (default true). "
                    "If false and refs exist, the delete fails.",
                },
                "drop_orphan_tables": {
                    "type": "string",
                    "description": "Drop the module's physical tables after publish "
                    "(pass 'Facts' or the table name). Omit to leave them orphaned.",
                },
                "publish": {
                    "type": "boolean",
                    "description": "Publish the delete appliance-wide immediately (default true).",
                },
            },
            ["module"],
        ),
        _h_delete_module,
    ),
    ToolSpec(
        "publish",
        "Commit ALL staged schema changes appliance-wide (module creates/deletes/edits). This is "
        "appliance-wide, not module-scoped — every staged change ships at once. Polls until the "
        "publish job finishes. Call after create_module/delete_module to make them live.",
        _obj(
            {
                "timeout": {"type": "number", "description": "Max seconds to wait (default 600)."},
                "poll_interval": {"type": "number", "description": "Poll cadence in seconds (default 10)."},
                "precheck": {
                    "type": "boolean",
                    "description": "Validate the draft before publishing (default true).",
                },
            }
        ),
        _h_publish,
    ),
    ToolSpec(
        "default_connector_config",
        "Build a complete, runtime-valid default configuration for a connector — every field's "
        "default plus the onchange-revealed sub-fields. Call this first, edit the values you need "
        "(credentials etc.), then pass the result as `config` to create_/upsert_connector_configuration.",
        _obj(
            {
                "connector": _CONNECTOR,
                "version": {"type": "string", "description": "Connector version (resolved if omitted)."},
            },
            ["connector"],
        ),
        _h_default_connector_config,
    ),
    ToolSpec(
        "validate_connector_config",
        "Validate a connector config dict against the connector's schema BEFORE submitting. Returns "
        "{valid, missing, invalid, unknown, errors} so you can fix problems client-side rather than "
        "discovering them as a runtime failure.",
        _obj(
            {
                "connector": _CONNECTOR,
                "config": _CONFIG,
                "version": {"type": "string", "description": "Connector version (resolved if omitted)."},
            },
            ["connector", "config"],
        ),
        _h_validate_connector_config,
    ),
    ToolSpec(
        "create_connector_configuration",
        "Create a named connector configuration (persists credentials). For a config that may "
        "already exist, set exist_ok=true (delegates to upsert; safe to re-run) instead of failing "
        "on a duplicate name. autofill=true (default) fills any schema-defaulted fields you omit.",
        _obj(
            {
                "connector": _CONNECTOR,
                "config": _CONFIG,
                "name": _CONFIG_NAME,
                "default": {"type": "boolean", "description": "Mark this the connector's default config."},
                "agent": {"type": "string", "description": "Run on a remote agent (its uuid); omit for self-agent."},
                "validate": {"type": "boolean", "description": "Validate config against schema first (default true)."},
                "autofill": {"type": "boolean", "description": "Fill schema-defaulted fields (default true)."},
                "exist_ok": {
                    "type": "boolean",
                    "description": "Delegate to upsert if a config with this name exists (default false).",
                },
                "version": {"type": "string", "description": "Connector version (resolved if omitted)."},
            },
            ["connector", "config", "name"],
        ),
        _h_create_connector_configuration,
    ),
    ToolSpec(
        "update_connector_configuration",
        "Update an existing connector configuration identified by its config_id (PUT). Same options "
        "as create for validation and autofill.",
        _obj(
            {
                "connector": _CONNECTOR,
                "config_id": _CONFIG_ID,
                "config": _CONFIG,
                "name": _CONFIG_NAME,
                "default": {"type": "boolean", "description": "Mark this the connector's default config."},
                "agent": {"type": "string", "description": "Run on a remote agent (its uuid); omit for self-agent."},
                "validate": {"type": "boolean", "description": "Validate config against schema first (default true)."},
                "autofill": {"type": "boolean", "description": "Fill schema-defaulted fields (default true)."},
                "version": {"type": "string", "description": "connector version (resolved if omitted)."},
            },
            ["connector", "config_id", "config", "name"],
        ),
        _h_update_connector_configuration,
    ),
    ToolSpec(
        "upsert_connector_configuration",
        "Create a named configuration, or update it in place if one already exists with the same name — "
        "the idempotent write safe to re-run from a deploy script. Preferred over create_connector_configuration "
        "when the config may already exist.",
        _obj(
            {
                "connector": _CONNECTOR,
                "config": _CONFIG,
                "name": _CONFIG_NAME,
                "default": {"type": "boolean", "description": "Mark this the connector's default config."},
                "agent": {"type": "string", "description": "Run on a remote agent (its uuid); omit for self-agent."},
                "validate": {"type": "boolean", "description": "Validate config against schema first (default true)."},
                "autofill": {"type": "boolean", "description": "Fill schema-defaulted fields (default true)."},
                "version": {"type": "string", "description": "Connector version (resolved if omitted)."},
            },
            ["connector", "config", "name"],
        ),
        _h_upsert_connector_configuration,
    ),
    ToolSpec(
        "last_playbook_run",
        "Return the most recent run of a playbook (live or historical). Returns {run: null} if none. "
        "Use why_playbook_failed to get just the failure detail, or get_playbook_run for a full run by pk.",
        _obj(
            {
                "playbook": _PLAYBOOK,
                "playbook_uuid": {"type": "string", "description": "Identify the playbook by UUID instead of name."},
            }
        ),
        _h_last_playbook_run,
    ),
    ToolSpec(
        "why_playbook_failed",
        "Return the slim failure detail of the most recent run of a playbook: "
        "{status, failing_step, error_message, pk}. Returns {failure: null} if the run succeeded or "
        "no run exists. Pulls the populated error_message (absent from the run list).",
        _obj(
            {
                "playbook": _PLAYBOOK,
                "playbook_uuid": {"type": "string", "description": "Identify the playbook by UUID instead of name."},
            }
        ),
        _h_why_playbook_failed,
    ),
    ToolSpec(
        "wait_for_playbook_run",
        "Block until the newest run of a playbook reaches a terminal state, then return its summary. "
        "Pass since (an ISO timestamp or prior run's modified time) to wait for a run newer than that. "
        "Raises TimeoutError (returned as an error) if no terminal state within timeout.",
        _obj(
            {
                "playbook": _PLAYBOOK,
                "playbook_uuid": {"type": "string", "description": "Identify the playbook by UUID instead of name."},
                "since": {
                    "type": "string",
                    "description": "Only consider runs newer than this (ISO timestamp). Use after triggering.",
                },
                "timeout": {"type": "number", "description": "Max seconds to wait (default 120)."},
                "poll_interval": {"type": "number", "description": "Poll cadence in seconds (default 3)."},
            }
        ),
        _h_wait_for_playbook_run,
    ),
    ToolSpec(
        "upsert_record",
        "Insert a record or update an existing one. With key omitted, FortiSOAR matches by natural key; "
        "pass key (a field name) to match on that field. Friendly picklist values map to IRIs by default.",
        _obj(
            {
                "module": _MODULE,
                "data": {"type": "object", "description": "Field -> value mapping for the record."},
                "key": {
                    "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    "description": "Field name(s) to match an existing record on (default: natural key).",
                },
                "resolve_picklists": {
                    "type": "boolean",
                    "description": "Map friendly picklist values to IRIs before sending (default true).",
                },
            },
            ["module", "data"],
        ),
        _h_upsert_record,
    ),
    ToolSpec(
        "get_or_create_record",
        "Look up a record by key field(s); create it if absent. Returns {record, created} where created "
        "is true if the record was newly made. key defaults to 'uuid'; multiple keys are AND'ed.",
        _obj(
            {
                "module": _MODULE,
                "data": {"type": "object", "description": "Field -> value mapping to match/create on."},
                "key": {
                    "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    "description": "Field name(s) to match on (default 'uuid'). Must be present in data.",
                },
                "resolve_picklists": {
                    "type": "boolean",
                    "description": "Map friendly picklist values to IRIs before sending (default true).",
                },
            },
            ["module", "data"],
        ),
        _h_get_or_create_record,
    ),
    ToolSpec(
        "schedule_playbook",
        "Create a periodic task that runs a playbook on a cron schedule (daily/weekly/etc). "
        "Returns the created schedule with its server-generated id. The playbook fires "
        "asynchronously on the cron; use trigger_schedule_now to fire it immediately and "
        "wait_for_playbook_run to track the resulting run. cron is 5-field: "
        "'minute hour day_of_month month_of_year day_of_week' (e.g. '7 2 * * *' = 02:07 daily).",
        _obj(
            {
                "name": {"type": "string", "description": "Schedule display name."},
                "playbook": _PLAYBOOK,
                "playbook_uuid": {
                    "type": "string",
                    "description": "Identify the playbook by UUID instead of name.",
                },
                "cron": {
                    "type": "string",
                    "description": "5-field cron: 'minute hour day_of_month month_of_year day_of_week'.",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone for the cron (default UTC).",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Create the task enabled (default true).",
                },
                "exit_if_running": {
                    "type": "boolean",
                    "description": "Skip a fire if the previous run is still active (default true).",
                },
            },
            ["name", "cron"],
        ),
        _h_schedule_playbook,
    ),
    ToolSpec(
        "trigger_schedule_now",
        "Fire a scheduled task immediately, out-of-band of its cron. The trigger is "
        "asynchronous; pair with wait_for_playbook_run to track the resulting run. Identify "
        "the schedule by name (resolved to its id) or by task_id (the id from schedule_playbook).",
        _obj(
            {
                "name": {"type": "string", "description": "Schedule display name (resolved to its id)."},
                "task_id": {
                    "type": "string",
                    "description": "The schedule's id (Fernet token from schedule_playbook) instead of name.",
                },
            }
        ),
        _h_trigger_schedule_now,
    ),
    ToolSpec(
        "delete_schedule",
        "Delete a scheduled periodic task entirely by name. Resolves the task's current id and "
        "DELETEs it. Use disable to merely pause a schedule; use this to remove one created for "
        "testing or no longer wanted.",
        _obj(
            {"name": {"type": "string", "description": "Schedule display name to delete."}},
            ["name"],
        ),
        _h_delete_schedule,
    ),
    ToolSpec(
        "map_use_case",
        "Classify a free-text operational use case to a FortiSOAR archetype and fill its "
        "parameter slots. Returns the matched archetype name, a confidence + rationale, the "
        "filled vs pending parameters, and notes. Use this as the entry point when standing up "
        "a new use case: then create the module from the archetype's module_schema, configure the "
        "manifest's connectors, and push a playbook from its skeleton. No appliance I/O -- reads "
        "the local archetype store only.",
        _obj(
            {
                "use_case": {
                    "type": "string",
                    "description": "Free-text use case, e.g. 'compare FortiCloud assets vs "
                    "ServiceNow CMDB, email a CSV on mismatches'.",
                }
            },
            ["use_case"],
        ),
        _h_map_use_case,
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
    return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in _TOOLS]


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
