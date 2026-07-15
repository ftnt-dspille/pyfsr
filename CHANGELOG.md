# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.9.0] - 2026-07-15

### Added
- **Playbook version control** — `client.playbooks` now exposes the
  `workflow_versions` snapshot history (the editor's "Versions" tab):
  `list_versions()`, `get_version()`, `create_version()`,
  `restore_version()`, `delete_version()`, and `diff_versions()`. The diff
  is client-side (FortiSOAR has no diff endpoint); it compares two
  snapshots' step graphs by uuid, surfacing added/removed/changed steps,
  routes, and groups. New typed models: `PlaybookVersion`, `VersionDiff`,
  `VersionStepDelta`, `CreateVersionRequest`. CLI mirrors all six verbs
  under `pyfsr playbook versions` (`list` / `get` / `create` / `restore` /
  `delete` / `diff`).
- **`pyfsr appliance content-hub sync`** — pull the Content Hub catalog +
  artifacts from REPOSERVER via `csadm package content-hub sync` (forced
  by default; `--no-force` for a scheduled sync). Gated by `--yes`.
- `--sudo-password` CLI flag (and `PYFSR_APPLIANCE_SUDO_PASSWORD` env) for
  key-auth boxes that still need sudo creds for `csadm`.

### Changed
- **Transport is hidden from the public API.** `Appliance` now accepts only
  connection kwargs (`host`, `user`, `key_path`, …) — the `transport=` and
  `facts=` constructor params and the `.transport` / `.facts` properties are
  removed. `box.run(argv)` is the escape hatch for arbitrary commands;
  `box.db.resolve_db()` exposes DB resolution. Transport classes
  (`Transport`, `SSHTransport`, `make_transport`, …) are no longer
  re-exported from `pyfsr.cli.appliance`; import from
  `pyfsr.cli.appliance.transport` directly only when needed.
- `PlaybookVersion.json` property renamed to `.snapshot` (avoid shadowing
  pydantic v2's `BaseModel.json()` method, which confused mypy).
- README: fixed stale MCP module path (`pyfsr.mcp` → `pyfsr.agent.mcp`),
  dev install (`uv sync --extra dev` → `uv sync`), and documented all six
  CLI groups (was "two").

## [0.6.7] - 2026-06-22

### Added
- `FortiSOAR(..., dry_run=False)` is now a real constructor parameter (stored as
  `client.dry_run`). When True, mutating requests (POST/PUT/PATCH/DELETE) are not
  sent — they are logged and a synthetic 200 whose body echoes the would-be request
  (`{dryRun, method, url, params, data}`) is returned, so a caller's write path runs
  without touching the appliance. Reads pass through unchanged. Previously callers
  (e.g. alertforge) set `dry_run` as an ad-hoc attribute the client silently ignored.
- `client.picklists.validate_record_fields(module, fields)`: dry-run picklist
  resolution that returns the misses (`[{field, value, picklist, valid_values}, ...]`)
  without mapping or writing — empty list means every picklist field resolves cleanly.
  Lets a caller validate friendly-value mappings before committing a write.
- `pyfsr appliance` gained three csadm/RabbitMQ verbs (all live-validated against a
  FortiSOAR appliance — FSR 7.6.x / RabbitMQ 3.13.2):
  - `appliance db getsize` — `csadm db --getsize`, the database footprint broken
    out by data class (primary / audit / workflow / archived). Parsed from csadm's
    `<class> : <size>` report into a structured table (`db getsize --json/--csv`
    supported); `db_cmds.getsize_raw()` exposes the unparsed text. Distinct from
    `db list`, which sizes each Postgres DB via `pg_database_size`.
  - `appliance mq permissions --all-vhosts` — the per-vhost permission matrix
    (enumerates vhosts and runs `list_permissions -p <vhost>` for each, with a
    `vhost` column). The bare `mq permissions` still covers only the default `/`.
  - `appliance certs regenerate <hostname>` — regenerate the self-signed TLS cert
    via `csadm certs --generate <hostname>` (the documented fix for the expired-cert
    "Unable to load API credentials from cache or DAS" failure). Gated by `--yes`;
    restart services afterwards. New `pyfsr.cli.appliance.certs` module.
- `pyfsr.cli.appliance.host` — typed OS resource metrics over SSH (no sudo):
  `meminfo`/`loadavg`/`process_rss(regex)`/`disk`, plus `snapshot()` which gathers
  mem/swap/load/per-process RSS/disk in one round-trip and returns a typed
  `HostSnapshot` (with `.summary()`). CLI: `appliance host snapshot|mem|rss`.
- `appliance mq purge <queue>` and `appliance mq purge-workflows` — the latter
  releases a stuck-worker backlog by purging the `fsr-cluster/celery` queue and
  recycling `celeryd` (SIGKILL by default so systemd respawns a clean pool against
  the empty queue; `--graceful` for the csadm warm-stop path), then restarting
  `cyops-integrations-agent`. Returns a typed `WorkflowPurgeReport`. Also
  `mq.queue_depth`/`nonempty_queues`/`purge_queue`. All gated by `--yes`.
- `appliance service stop|start|systemctl` — `csadm` stop/start plus a direct
  `systemctl <action> <unit>` escape hatch (`--signal` for `kill`); mutating
  actions gated by `--yes`, read-only ones (`is-active`/`status`) ungated.

### Changed
- Appliance command return types are now typed dataclasses instead of loose
  `str` / `(headers, rows)` tuples, so inputs and outputs are clear from the docs:
  - `service.services()` → `list[ServiceState]` (parsed `csadm services --status`,
    ANSI-stripped, `running: bool`); `service.restart/stop/start/systemctl` →
    `ServiceActionResult` (`.ok`); `service.listeners()` → `list[Listener]`.
  - `mq.queues/consumers/permissions` → `list[QueueInfo|Consumer|Permission]`;
    `mq.vhosts` → `list[str]`.
  - `ha.nodes()` → `list[HaNode]`, `ha.health()` → `HaHealth` (typed mem/swap/disk);
    `ha.nodes_raw`/`health_raw` keep the unparsed text.
  - `license.details()` → `LicenseDetails` (typed `total_users`/`remaining_days`);
    `license.show()` stays raw.
  - `db.list_databases()` → `list[DatabaseInfo]`; `db.getsize()` →
    `list[DataClassSize]` (adds `size_mb` normalising mixed kB/MB units).

### Fixed
- `appliance mq` listings (`vhosts`/`permissions`/`queues`/`consumers`) leaked
  RabbitMQ's column-header row as a bogus data record on modern RabbitMQ (≥3.8):
  `-q` alone no longer suppresses headers (confirmed live on 3.13.2 — `list_vhosts`
  emitted a vhost literally named `name`). Listings now pass `--no-table-headers`.
- `client.playbooks.clone(uuid, new_name, *, collection=None, is_active=False)`:
  clone a playbook definition under a new name. Fetches the source with its
  steps/routes/groups inlined, regenerates **every** owned UUID (workflow + steps
  + routes + groups) and rewires all internal references (route
  `sourceStep`/`targetStep`, `triggerStep`, step `group`) via a single global
  UUID substitution, drops server-managed fields, and POSTs the copy. Defaults the
  clone to inactive so it can't fire on triggers before review; optionally re-homes
  it into a different collection.

## [0.6.6] - 2026-06-21

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
  Shared base `pyfsr.api._record_module.RecordModuleAPI`. 6 unit tests; live
  round-trip (create + link + delete for all three modules) validated on the dev box.
- `RecordSet.delete_by_query(query, hard=)`: bulk-delete every record matching a
  structured filter in one call via `DELETE /api/3/delete-with-query/<module>`
  (the route is DELETE-only and carries the filter as its body). Accepts a `Query`
  or raw `{logic, filters}` dict; rejects an empty filter so it can't wipe a whole
  module; `hard=True` purges via `$hardDelete`. 3 unit tests; live validated on the
  dev box (deleted 3 throwaway alerts by filter → `{"total_records_deleted": 3}`).
- `client.modules_admin.revert()`: discard **all** pending staged schema changes via
  `PUT /api/publish/revert` (the inverse of `publish()`) — use it to abandon a
  half-built change or clear a wedged staged draft. Appliance-wide, like publish;
  synchronous (no DB-migrate 503 window). 1 unit test; live validated (reverted a
  pending staged change → `{"status": "reverted"}`, staging back to 0 pending).
- `client.views` (`ViewsAPI`): resolve a module's **active** system view template
  (SVT) layout via `GET /api/views/1/modules-<module>-<kind>` — `views.detail(module)`,
  `views.listing(module)`, `views.form(module)`, plus generic `views.resolve(module,
  kind=)`. A module can carry duplicate `isDefault: true` SVT rows, so the live layout
  must be resolved through this endpoint, never picked by name/flag. 4 unit tests; live
  validated on the dev box (resolved detail UUID confirmed among the raw SVT rows).

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
