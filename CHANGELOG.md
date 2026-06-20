# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.6.0] - 2026-06-20

### Added
- `modules_admin`: verified `delete_module()` (discard-staging + relationship-referrer
  detach, then publish) and `remove_field()`, plus a relationship-referrer scan; correct
  `uniqueConstraint` object shape on field create.
- `import_config` / `export_config`: treat config import as a publish — 503 tolerance
  during migrate, refusal on risky (table-rename) changes, and post-import verification.
- Typed `solution_packs` install/uninstall, an `Appliance` actor, and picklist IRI
  tightening; semantic IRI NewTypes (`PicklistIRI` / `RecordIRI`) across the records
  surface.
- Roles + import-config APIs and an expanded connector/playbook/collection surface;
  `find_installed_connectors()` partial-match search; `pack_connector` bundler with
  connector-config validation.
- Query DSL: pydantic-backed `QueryBody`/`FilterLeaf`/`FilterGroup`/`SortSpec` models
  (`Query.model()` returns the typed body), an operator knowledge base
  (`OPERATOR_SPECS` with per-operator arity/category) that validates value shape and
  suggests fixes for unsupported operators (e.g. `isnotnull` → `isnull value=False`),
  and a shipped field/relationship KB (`pyfsr.fields`) so `Query(module=...)` can
  validate field paths and relationship dot-walks.
- `PlaybooksAPI.get_definition()` / `bulk_upsert()` / `query()` for the playbook-definition
  surface at `/api/3/workflows`, including the bulk re-push path and body-filter queries.
- `client.ai` (`AIApi`): drive the FortiAI agentic investigation service.
  - `investigate_alert()` / `start_alert_investigation()` / `wait_for_result()` /
    `get_status()` / `get_result()` — trigger and poll the triage pipeline
    (normalize → hypothesize → plan → gather over MCP → verdict).
  - `enable_features()` / `features_enabled()` — the AI features / terms-acceptance
    gate (`publicValues.ai_feature` in System Settings).
  - `list_providers()` / `list_llm_configs()` / `create_llm_config()` /
    `test_llm_config()` / `delete_llm_config()` — LLM reasoning-profile management.
  - `list_mcp_servers()` / `validate_mcp_server()` / `register_mcp_server()` /
    `delete_mcp_server()` — MCP-server registration for the investigation agents.
- Agent tool registry (`pyfsr.tools`, also exposed via `python -m pyfsr.mcp`):
  `investigate_alert`, `get_investigation_result`, and `list_ai_config` tools.

## [0.4.0] - 2026-06-15

### Added
- `Query.changed()` / `Query.in_all()` and the `changed` / `in_all` trigger-condition
  operators.
- `RecordSet.upsert()` and `RecordSet.bulk_upsert()` (insert-or-update via
  `/api/3/upsert/<module>` and `/api/3/bulkupsert/<module>`).
- `WorkflowCollectionsAPI.upsert()` / `bulk_upsert()` / `restore()` for the
  collection-specific re-push and recycle-bin lifecycle.
- `PlaybooksAPI.get(step_detail=True)` and `PlaybooksAPI.run_env()` for the per-step
  execution trace + Jinja run-context.
- `ConnectorsAPI.definition()` / `operations()` / `files()` for connector
  operation-definition and source-file discovery.
- `client.wf_tools` (`WfToolsAPI`): server-side Jinja rendering (`render` / `render_raw`)
  and global ("dynamic") variables (`dynamic_variables` / `dynamic_variable`).
- `PicklistsAPI.options(name)` — the valid friendly values of a picklist.
- `PicklistsAPI.resolve_record_fields(..., strict=, report=)` — actionable feedback
  on a friendly value that isn't in the picklist: `strict=True` raises
  `PicklistResolutionError` (new) naming the field/value/valid options; `report=[]`
  collects misses as `{field, value, picklist, valid_values}` without raising.
- `ModulesAPI.describe(with_values=)`, `format_module()` / `print_module()`,
  `search()`, `fields()`, `find_field()` — module/field schema discovery, including
  each picklist field's accepted friendly vocabulary.
- `client.modules_admin` (`ModulesAdminAPI`): create modules, add/alter fields, and
  `publish()` staged schema changes (synchronous by default — tolerates the transient
  migrate-cycle states and polls until the appliance is ready).

### Changed
- Error parsing now surfaces Symfony validation bodies (`detail` / `violations` /
  `title`), not just `message` — previously these collapsed to "Unknown error occurred",
  hiding the real cause of a 400.

## [0.2.3] - 2024-01-03

### Added
- Created a `CHANGELOG.md` file.
- APIKey Validation logic in `APIKeyAuth`.

### Changed
- Pytest fixture to use both APIKey and User/Pass Auth
- Moved order of params in Auth initialization

### Fixed
- Issue with `APIKeyAuth` not validating the API Key correctly.
