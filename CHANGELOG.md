# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Typed CRUD shortcuts for the common SOC record modules, mirroring the
  `client.alerts` pattern so callers stop hand-rolling raw `client.post(...)`:
  - `client.incidents` (`IncidentsAPI`) and `client.tasks` (`TasksAPI`):
    full `create`/`list`/`get`/`update`/`delete` with friendly picklist-value
    resolution (e.g. `status="Open"`, `severity="High"`) and a `record=` link
    that attaches the new record to a parent (e.g. a task linked to its alert);
    the relationship field is derived from the parent record's IRI.
  - `client.comments` (`CommentsAPI`): `create(content, record=...)` posts an
    analyst comment linked to one or more parent records of any module.
  Shared base `pyfsr.api._record_module.RecordModuleAPI`. 6 unit tests.

## [0.6.4] - 2026-06-20

### Added
- `client.user_settings`: per-user preferences API (`UserSettingsAPI`). `all()` /
  `get(key, default=)` read the calling user's `@settings` blob via
  `GET /api/3/actors/current` (with `/`-separated key traversal); `set(key, value)`
  writes via the only path that persists, `PUT /api/3/user_settings/current/<key>`.
  The module docstring encodes the known footguns (the `/current/`-only write path,
  500/405 on `…/<uuid>`, 404 on `/api/3/settings`, and the silent no-op when writing
  `@settings` on `actors/current`). Live read+write validated.
- `pyfsr playbook check-fresh`: Level-1 catalog freshness probe. Compares the cached
  `fsr_playbooks` reference catalog's provenance (`_catalog_meta`) against a live SOAR
  via cheap GETs (`/api/version`, `/api/publish/error`, `$limit=0` row counts) and reports
  publish/version/add-delete drift. Exit 0 = fresh, 2 = drift, 1 = unstamped/error. New
  `pyfsr.playbook_freshness` module holds the unit-testable comparison logic.

### Fixed
- `pyfsr appliance logs`: corrected the service→log-path map for FortiSOAR 7.6.x — the
  auth log is `cyops-auth/das.log` (not `cyops-auth.log`), the api app log is `prod.log`,
  the workflow engine is `fsr-workflow.log`, and postman moved under `cyops-routing-agent/`;
  added `gateway`/`notifier`/`connectors`/`celery` aliases and corrected the `logs scan`
  systemd unit names. `logs tail` now raises `FileNotFoundError` on a missing path instead
  of returning an empty string. Validated live against FSR 7.6.5.
- `pyfsr appliance` device-UUID resolution: read the install-time `/home/csadmin/device_uuid`
  file (the value the `cyberpgsql`/`elastic` passwords were provisioned with) **before**
  falling back to `csadm license --get-device-uuid`. On an entitlement-drifted box the latter
  returns a different UUID that fails Postgres auth, which broke every `db` verb and content-DB
  discovery. Fixes DB access on drifted appliances; validated live.

## [0.6.3] - 2026-06-20

### Added
- Author playbooks in YAML and deploy them to FortiSOAR. New `pyfsr.authoring.compile_playbook_yaml()`
  bridges the `fsr_playbooks` compiler (YAML → FSR import envelope), and
  `client.workflow_collections.compile_yaml()` / `import_from_yaml()` compile + push via the
  existing `import_export` write path.
- `pyfsr playbook` CLI group: `compile` (offline), `validate` (offline), and `deploy`
  (`--replace`, `--dry-run`) over the API client.
- Optional extra `pyfsr[playbooks]` pulling in the `fsr_playbooks` compiler; the core
  library never imports it.

## [0.6.2] - 2026-06-20

### Added
- `pyfsr appliance service`: status, liveness (wedge detection via endpoint probes), restart, and listeners.
- `pyfsr appliance mq`: RabbitMQ diagnostics — queue health (messages, consumers, backlogs), consumers, vhosts, and permissions.
- `pyfsr appliance logs`: tail (service aliases + raw paths) and error scanning (journalctl rollup).
- Comprehensive validation suite: 45 unit tests covering all appliance CLI families, offline demo, and live example script.

## [0.6.0] - 2026-06-20

### Added
- `pyfsr` console CLI with an `appliance` command group (`db` / `facts` / `transport` /
  `info`) for SSH-driven appliance operations, including a write-guarded `db` verb.
- `modules_admin`: verified `delete_module()` (discard-staging + relationship-referrer
  detach, then publish) and `remove_field()`, plus a relationship-referrer scan; correct
  `uniqueConstraint` object shape on field create. `delete_module(drop_orphan_tables=...)`
  reclaims the physical tables the API leaves orphaned, via the appliance CLI.
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
