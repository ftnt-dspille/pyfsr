# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **Content Hub mirror now proxies what it doesn't host — widget `.tgz`, SP
  `.zip`, connector `.tgz` long tails via the public Fortinet repo (no FDN
  cert needed).** `deploy/content-hub-mirror/entrypoint.sh` reverse-proxies
  `/fsr-widgets/`, `/widgets/`, `/xf-widgets/`, `/xf/solutions/solutionpacks/`,
  and `/xf/solutions/connectors/` to `repo.fortisoar.fortinet.com` (open HTTPS).
  The `/content-hub/<name>-<ver>/...` upstream proxy also now works **without**
  the FDN cert — plain HTTPS to a no-cert upstream (the public repo, or another
  mirror). New env vars `PUBLIC_PROXY` (default 1), `PUBLIC_FORTINET_HOST`,
  `UPSTREAM_TLS_VERIFY`; `CONNECTORS_PROXY` now defaults to follow `PUBLIC_PROXY`.
  A snapshot catalog + this proxy get "both Fortinet's store and mine" with no
  FDN cert on the mirror at all. Verified locally by the new
  `smoke-test-proxy.sh` against the live public repo (no appliance, no cert).
- **`setup-appliance.sh` now installs AND verifies TLS trust.** The 2026-07-13
  live run noted the content-hub sync skipped TLS verify (so a missing trust
  was invisible until a SP install failed with a misleading "network
  connection" error). The rewrite closes that: the mirror's cert is installed
  into the OS trust store, then **verified** with a real `openssl s_client`
  handshake *before* anything else is touched (hard-fail on a bad install with
  the actual cause, not the runtime symptom). A post-sync verified HTTPS GET
  of the catalog + one per-item `info.json` gates success — the exact code path
  the SP install endpoint uses. New flags: `--cert-file <path>` (trust a
  provided cert), `--check` (read-only verification of trust + env + connector
  repo, re-runnable any time), `--insecure` (skip TLS checks for a quick
  reachability probe), `--no-verify`.

## [0.11.1] - 2026-07-18

### Added
- **Multi-box SSH profiles for the `pyfsr appliance` CLI (C4).** Each
  `[instances.<alias>]` table in `~/.pyfsr/instances.toml` (or `$PYFSR_INSTANCES`)
  can now carry an optional `[instances.<alias>.appliance]` subtable with SSH
  transport fields, so `pyfsr appliance --instance <alias>` resolves a full SSH
  profile (host/user/password/port/key/sudo) from one config file — the SSH
  counterpart of the MCP server's `--instance` flag. `--instance` takes precedence
  over `--host`/`--user`/`--password` (the named profile wins), and an alias
  without an appliance subtable raises a clear error instead of silently
  falling back. `host` defaults to the hostname parsed from the instance's
  `base_url`, so a box whose REST and SSH endpoints share an IP needs no
  repetition. SSH creds can stay out of the TOML by pointing at a
  `PYFSR_APPLIANCE_*` env file (`env_file = ".env.206.ssh"`, relative to the
  toml's dir). The SDK counterpart is `Appliance(instance="206")` via
  `InstanceRegistry.transport(alias)`.

### Documentation
- **9 more read-only endpoints now have offline doctested `list()` examples.**
  Live-captured `roles`, `teams`, `users` (people), `tags`, `comments`,
  `reporting`, `routers`, `rules` (preprocessing), and system `notifications` —
  none previously had a replay fixture — so each module's `list()` docstring now
  runs and output-matches under `demo_client()` with no appliance.
- **More docstring examples now run offline instead of `doctest: +SKIP`.** Converted
  read-only examples whose endpoints already have replay captures (playbook
  `list_versions`/`last_run`/`why_failed`; and, via a new JWT-auth replay fixture
  `demo_client_jwt()`, `system.cluster_health`/`system.license`, `auth_config.get`,
  and `api_users.get`/`query`). The JWT fixture unblocks endpoints that
  `APIKeyAuth` marks unsupported. Write/mutating and live-only examples remain
  correctly skipped. Docstring-doctest gate stays green (26/26 modules).

### Added
- **Idempotent `get_or_create_*` / `ensure_*` helpers across the resource surface** —
  check-then-create methods returning `(obj, created)` so callers can converge
  state without pre-checking existence. An existing record is returned unchanged
  (`created=False`); an absent one is created (`created=True`); re-running is a
  safe no-op. Covers teams, roles, agents, users, schedules, picklist options,
  module fields, navigation items, playbook activation, solution packs, widgets,
  and (keyed by name) export templates and view templates. Append-only resources
  (alerts, attachments, comments) are intentionally excluded — they have no
  natural unique key. Backed by fake-client unit tests, no appliance required.
- **Author a solution pack from Python** — `SolutionPackBuilder` (subclasses the
  `ExportTemplate` content builder, so every `add_*` content method chains the same
  way) plus pack metadata: `.tags()`, `.category()`, and `.post_install_widget()`
  (the *Configure post-install action* — widget name/version, launch button label,
  and "launch automatically the first time"). `client.solution_packs.create(builder,
  publish=)` POSTs `/api/3/solutionpacks` with a nested `SolutionPack Export`
  template — the same shape the Content Hub *Create Solution Pack* wizard sends.
  Live-verified on 8.0.0: creating the pack auto-creates its export template with the
  `solutionPack` back-reference that scopes a later export.
- **`client.solution_packs.install_from_file(path, *, replace=, wait=)`** — install a
  pack from a local `.zip`/`.tgz` (`POST /api/3/solutionpacks/install` multipart,
  `$type` defaults to `solutionpack`), the file counterpart of `install()`. Returns
  the same typed `SolutionPackInstallResponse`.
- **`PostInstallConfig` / `PostInstallWidget` models** typing a pack's
  `infoContent.postInstallConfig` (`{enabled, widgets:[{name, label, version,
  buttonLabel, autoLaunch}]}`), now also on `SolutionPackInfo`. Captured from a live
  `info.json` and the 8.0.0 editor's pack-metadata wizard.
- `examples/solution_pack_full_lifecycle.py` — end-to-end: create-from-content →
  publish → export → uninstall → reinstall from file.

### Fixed
- **`solution_packs.export_pack()` now works when the pack's export template isn't
  expanded in the catalog lookup** (it re-fetches with `$relationships=true`) and
  **defaults the output filename to `.zip`** — the payload is a zip archive, not the
  `.json` the old default implied.
- **The install poll tolerates the transient `503` a pack import triggers.** A larger
  pack import runs a schema migrate that briefly restarts the API; `install_status()`
  now reports that as a non-terminal `"Importing"` status so `wait_for_install()` (and
  `install()` / `install_from_file()` with `wait=True`) keep polling instead of
  aborting the wait. Live-verified end to end on 8.0.0.

## [0.11.0] - 2026-07-17

### Fixed
- **`connectors.list_configurations(name=...)` filtered the wrong thing, silently.**
  The docstring said `name` filters by *connector* name; live-checking showed it
  filters the **configuration** name. Passing a connector name returned `[]` rather
  than raising — the endpoint ignores filters it doesn't understand, and this one
  simply matched nothing — so "list this connector's configurations" reported none
  and looked like an empty result, not a mistake. The docs now state what it does,
  and the existing test no longer encodes the wrong belief: it passed
  `name="virustotal"` (a connector name) while only asserting param passthrough, so
  it could never have caught this.

### Added
- **`client.system_queries`** — saved **datasets** (`/api/3/system_queries`), typed via
  the new `SystemQuery` / `QueryDefinition` / `QueryFilter` models. `list(module=...)`,
  `get`, `find_by_name`, `create`, `ensure` (idempotent), `update`, `delete`, and `run`
  (delegates to `search.run_persisted`, resolving the dataset's module for you).
  `create`/`ensure` take a module **slug** and resolve the `model_metadatas` IRI
  themselves.

  This exists because **a dataset on `threat_intel_feeds` *is* a TAXII collection** —
  the id served at `/api/taxii/1/collections/<id>/objects` is the dataset's uuid. So
  `client.system_queries` defines a collection and the read-only `client.taxii` serves
  it; together they're how FortiSOAR publishes an outgoing threat feed that a FortiGate
  can pull. See `examples/taxii_threat_feed_to_fortigate.py`.

  The filter builders are the point: **FortiSOAR silently ignores a filter that omits
  `type`, and every filter when the body omits `logic`** — returning *all* records
  instead of erroring, which turns "delete what matched" into "delete the module".
  `SystemQueriesAPI.filter()` infers `type` (IRI → `object`, else `primitive`) and
  `.query()` always sets `logic`, so the shape can't be got wrong by accident.
- **`connectors.list_configurations(connector=...)`** — the filter the docs used to
  promise, done properly: takes a machine name or an install id and resolves a name
  to its id first, because the endpoint's `connector` filter is numeric and a name
  passed through errors ("Unknown error occurred"). A not-installed connector
  returns `[]`; a `bool` is rejected (it is an `int` subclass, so `connector=True`
  would otherwise query id 1).

### Deprecated
- **`config=` on `connectors.execute()` / `connectors.healthcheck()` is deprecated in
  favour of `config_id=`.** `config` named two different types across one API — a
  configuration **UUID** here, but the configuration **field map** on
  `create_configuration` / `update_configuration` / `upsert_configuration` /
  `validate_config`. Nothing caught the mix-up: a dict passed where a UUID belonged
  just became a bad query param. `config=` still works and still sends the same wire
  body (the rename is client-side only); passing both raises `ValueError`.

### Documentation
- **The 130 public `pyfsr.models` classes are now documented** (`reference-models.md`).
  They are re-exported from private submodules that autoapi does not page, so
  `pyfsr.models.X` had no doc target and ~52 `:class:`~pyfsr.models.X`` references
  across the docstrings resolved to nothing. That was invisible because
  `nitpick_ignore_regex` masked the whole namespace; the strict `-W -n` build was
  green *over* the dead links. The mask is gone, so the build now proves they
  resolve — 85 model links render from the API pages. The page is generated from
  `__all__` by `scripts/gen_models_reference.py` and a unit test fails in both
  directions if it drifts.
- Documenting the models rendered their docstrings for the first time, which
  surfaced real errors the mask had hidden: `run_env` / `get_execution` /
  `diff_versions` were written as bare relative references to methods that live on
  `PlaybooksAPI`; a docstring pointed at `ContentHubSearch.find_installed_ai_agent`,
  **a method that does not exist** (the shipped name is `get_installed_ai_agent`);
  and two docstrings had prose parsed as a *type* because their first line contained
  a colon (`SystemViewTemplate.viewOptions`, `WidgetRecord.published`). All fixed.
- `docs/source/conf.py` now imports `fsr_playbooks.compiler` up front. This is
  load-bearing, not tidying: sphinx resolves autodoc at read time, before any
  doctest runs, and inspecting 130 pydantic models perturbs the state pydantic uses
  to resolve string annotations — so a later `import fsr_playbooks` inside a doctest
  failed with `PydanticSchemaGenerationError: The type annotation for
  `__pydantic_extra__` must be `dict[str, ...]``. `conf.py` runs first, so importing
  there builds those schemas while the state is clean.

### Fixed
- **`manual_input.answer(by_title=)` matches the prompt's *schema title*, not the
  step name** — the docstrings, the `LookupError` text, the shipped example, and the
  authoring guide all claimed the opposite. Live-proven on 8.0.0: a step named
  `AskNumber` with `title: Enter a six digit number` produces a pending row whose
  `.title` is `Enter a six digit number`. The old claim was true only by accident:
  fsr_playbooks <0.4.11 silently dropped a step's `title:` and defaulted the schema
  title to the step name, so the two strings always coincided. Once the compiler was
  fixed the accident ended, and `examples/do_until_validation_loop.py` (which filtered
  on the step name) could no longer find its own prompt. The two coincide only when a
  step declares no `title:`.
- **`answer()` no longer posts a null `step_iri`.** A response option's `step_iri` is
  wired at author time from the step's `next:`, so a Manual Input step with no next
  step yields an option without one. `ManualInputOption.step_iri` defaults to `None`
  and `ApiResult.__getitem__` returns `None` rather than raising, so the old code
  silently sent `step_iri: null` and `wfinput_resume` answered with an opaque HTTP
  500 (live-verified: the server rejects a null *and* an omitted `step_iri`). It now
  raises up front, naming the cause — the run is unresumable as authored.
- **Corrected the `ManualInput` model's wire notes.** `input` / `response_mapping` /
  `custom_fields` do **not** appear on `list_wfinput/` rows (the model claimed they
  did); they are present on `pending_for_run()` and `retrieve()`, which also carry the
  numeric run id instead of the encrypted Fernet token. The test fixtures invented a
  single merged shape that no endpoint returns — carrying `input` and a
  `/api/wf/api/workflows/<id>/steps/<id>` style `step_iri` on a list row, and pairing
  a step-name `title` with a different schema title. They are now captured from a live
  8.0.0 box, and the real `step_iri` is an `/api/3/workflow_steps/<uuid>` IRI.
- **`pending_for_run()` no longer mis-describes approval gates.** An approval gate is a
  `manual_input` step with `is_approval: true` (the wire reports
  `type: "ApprovalManualInput"`), not the legacy `approval` step type — that one writes
  to the `approvals` module and never reaches this queue.
- **`uv.lock` pinned `fsr-playbooks==0.4.8`, below the `>=0.4.11` floor** declared in
  `pyproject.toml`, so a local `uv sync` silently installed a compiler the project
  rejects (CI is unaffected — it resolves the extra with pip). The lock could not be
  regenerated because `pyfsr[docs]` required `sphinx>=9.1`, which needs Python >=3.12
  while the project supports >=3.10, making the dependency set unresolvable. The docs
  pin is now marker-gated to `python_version >= '3.12'` (docs build on 3.12 in CI), and
  the lock is regenerated at 0.4.27.

### Changed
- **`answer(by_title=)` now raises on an ambiguous match** instead of silently taking
  the newest. Titles are not unique — the same step paused in two runs yields two
  identically-titled rows — so the old behavior could resume an arbitrary run. Pass
  `input_id=`, or use `pending_for_run(task_id)` to scope to one run.

### Deprecated
- **`playbooks.manual_inputs()` and `playbooks.retrieve_manual_input()`** — they hit
  the exact same endpoints as `client.manual_input.list()` / `.retrieve()`
  (`POST .../manual-wf-input/list_wfinput/` and `.../{id}/retrieve_wfinput/`), but in
  a raw-dict form with no typing, filtering, paging, or scoping. They are now thin
  delegates to `client.manual_input` and emit a `DeprecationWarning`; return shapes
  are unchanged (verified payload-identical against a live appliance). `client.manual_input`
  is the single interface for this surface — a direction already begun by
  `playbooks.approval()`, which delegates to it.
- Migration note for `manual_inputs()`: pass `assigned_to="all"`. The old method sent
  a bare body, which the server reads as *all*; `ManualInputAPI.list()` defaults to
  `"me"`. Live-verified on the same queue: `{}` and `"all"` each returned 3 rows while
  `"me"` returned 0 — so a mechanical migration to the default silently returns
  nothing. The delegate passes `"all"` explicitly and a test pins it.
- Not consolidated: `playbooks.resume()` and `manual_input.resume()` share the
  `wfinput_resume/` endpoint but are **not** interchangeable — they build different
  bodies (`playbooks.resume` takes optional `step_iri`/`step_id` plus an `approved=`
  approval shortcut and sends no `user`; `manual_input.resume` requires
  `step_iri`/`step_id`/`user` and has no `approved`). Collapsing either direction
  would drop a capability, so both remain.

### Changed
- **`playbooks.trigger(records=...)` now raises `ValueError` instead of starting a
  silently record-blind run.** It posts to the manual-execute (`notrigger`) route,
  which cannot deliver record context: the appliance's `noTriggerExecuteAction`
  only `array_merge`s the POST body into the trigger step's arguments and
  interprets a fixed key set (`env`/`priority`/`parent_wf`/`step_id`/
  `runtime_tags`/`force_debug`/`_eval_input_params_from_env`/`inputVariables`) —
  `records` is not among them and nothing fetches a record. So `vars.input.records`
  stayed empty and any step reading `{{ vars.input.records[0]['@id'] }}` died with
  `CS-WF-35: Record IRI is empty`, while the trigger call itself returned a valid
  `task_id` and looked successful. Verified as a property of the route, not the
  body: posting the full action envelope
  (`singleRecordExecution`/`__resource`/`__uuid`) to `notrigger` changes nothing.
  Use `trigger_action(route_uuid, module=..., record_uuid=...)` for a record-scoped
  run — the route uuid is the trigger step's `arguments.route`. Nothing in the SDK
  used the old path: the only callers were a fake-client unit test and a
  `# doctest: +SKIP` example, and the AI investigation surface does not go through
  `trigger()` at all (it POSTs the alert to `/api/ai/triage/alert`).

### Fixed
- **`trigger_action` callers could not reach the run they had just started.** The
  record-action route answers `{"task_ids": [...]}` — plural, a list — where
  `notrigger` answers a scalar `{"task_id": ...}` (the appliance fans out one task
  per record and returns `JsonResponse(['task_ids' => $taskIds])` in its
  `singleRecordExecution` branch, which `TriggerActionRequest` always requests).
  `TriggerResponse` declared only `task_id`, so the plural key landed in
  `model_extra` while the `task_ids` *property* — which normalizes `task_id` —
  shadowed it and returned `[]`. Both accessors therefore reported nothing.
  `TriggerResponse` now folds the plural wire key into `task_id` before validation.
- The `TRIGGER_ACTION_RESPONSE` replay fixture claimed a scalar `task_id` that no
  appliance ever sends, which is why the shadowing survived: the doctest asserting
  `.task_id` passed against a fixture that did not match the live wire. The fixture
  is corrected to the captured shape.
- `trigger(inputs=...)` is documented accurately: it arrives as top-level
  `vars.inputs`, and does **not** populate `vars.input.params` (which stays `{}` on
  this route). A trigger declaring `inputVariables` expects those values as
  top-level body keys, not nested under `inputs`.

## [0.10.1] - 2026-07-16

### Fixed
- **A self-hosted mirror built by `content_catalog.write_tree` could not install any
  solution pack.** The artifact and icon were copied only into the numbered build
  dir, while `info.json` / `build.json` went to both it and `latest/`. That asymmetry
  is fatal: the appliance defaults `buildNumber` to the literal `"latest"` when an
  install request omits it, so it fetched `{name}-{ver}/latest/{name}-{ver}.zip` and
  got a 404. `write_tree` now copies the artifact and icon into `latest/` as well,
  matching what the appliance actually fetches. The module contract is corrected —
  it previously specified a `latest/` copy for `info.json` only, and the tests
  asserted the artifact only under the numbered build, which is why this survived.
- The download 404 surfaces as `Unable to download <name> file. Please check the
  network connection to <repo>` — the appliance catches connection and client errors
  in one block, so a missing artifact is reported as a connectivity problem. That
  message sends you debugging the network, the repo host, and TLS trust; none of it
  is the cause. This is now documented on `solution_packs.install`.

### Added
- `solution_packs.install(build_number=)` — send an explicit `buildNumber` instead
  of letting the appliance fall back to the repo's `latest` path. Useful against a
  repo whose `latest` alias is missing or stale.

## [0.10.0] - 2026-07-16

### Added
- **Multi-instance MCP server + instance registry.** `pyfsr.instances.InstanceRegistry`
  maps a short alias (`"206"`, `"ga"`) to a resolved `EnvConfig` and hands out
  cached clients, so one agent process can reach several appliances without
  re-wiring host/auth. Config lives in `~/.pyfsr/instances.toml` (or
  `$PYFSR_INSTANCES`); each entry either points at an existing `FSR_*` env file
  (credentials stay there) or inlines the same `[fortisoar]` shape
  `EnvConfig.from_config_file` already understands. The bundled MCP server takes
  an optional `instance` argument per tool and gains a `list_instances`
  meta-tool; with no config file it falls back to a single `"default"` instance
  from the `FSR_*` environment, so existing single-box callers are unaffected.
  New `pyfsr-mcp` entrypoint (needs the `mcp` extra).
- `EnvConfig.from_mapping()` — build config from an already-parsed `[fortisoar]`
  mapping (the full document or the inner table), reusing the same auth/host
  parsing as `from_config_file` without re-reading a file.
- Agent tools `list_agent_sessions` / `get_agent_session` — read the FortiAI
  Agentic Assistant chat store. The assistant connector's machine name varies by
  deployment, so both accept a `connector` override.
- **`client.actors`** — the actors *union* (`/api/3/actors`), spanning the
  `Person` / `Appliance` / `ApiKey` subtypes that share the single `actors`
  table. `list()` parses each record into its concrete model per `@type`;
  `get(title)` resolves by exact title. Because titles are **not unique** on a
  live box, `find_by_title(title)` returns every match so the ambiguity `get()`
  hides is inspectable.
- **`client.reporting`** — report definitions (`/api/3/reporting`) as typed
  `Report` records. Lookups match on `displayName`; the entity has no `name`.
- **`client.rules`** — delivery rules and channels from the rule-engine app
  (`list_delivery_rules()`, `list_channels()`, `get_delivery_rule()`,
  `get_channel()`) plus crudhub preprocessing rules
  (`list_preprocessing_rules()`, `get_preprocessing_rule()`). The rule engine's
  dual proxy root (`/rule/api/` vs `/api/rule/api/`, build-dependent) is probed
  and cached internally, so callers never see it.
- **`client.views.app()` / `.navigation_sections()`** — the left-hand
  navigation view as a typed `NavigationView`, with `section_titles` instead of
  hand-walking `config["navigation"]`.
- **Content Hub AI agents (8.0.0+)** — `ContentType.AI_AGENT` plus
  `search_installed_ai_agents()`, `search_available_ai_agents()`, and
  `get_installed_ai_agent(name_or_label)` (exact match, unlike the fuzzy
  `search_*`/`find_*` methods).
- **`client.ai.get_mcp_config(name_or_uuid)`** — resolve one registered MCP
  server in a single filtered round-trip (vs scanning `mcp_configs()`).
- New typed models: `AIAgent`, `Report`, `NavigationView`, `DeliveryRule`,
  `RuleChannel`, `PreprocessingRule` — every field set transcribed from live
  8.0.0 responses.

### Changed
- **Config-export resolution now routes through typed SDK APIs** instead of raw
  `client.get`/`post` calls: the actor, report, delivery-rule, rule-channel,
  preprocessing-rule, AI-agent, navigation, MCP-config, and export-template
  resolvers all use the surfaces above. Live-verified byte-identical wire output
  against the previous raw path — no behavior change. `_get_picklist_iri` is
  deliberately left raw (the typed path costs 3+ GETs vs one).
- `Appliance` and `ApiKey` now declare `title` (always `None` — only `Person`
  rows populate the shared `actors` table's title column), so reading `.title`
  across the `Actor` union no longer raises `AttributeError`.

### Fixed
- `client.ai.get_mcp_config()` no longer mistakes an empty response for a match:
  the collection GET uses `extract_members`, not `_as_list` (which coerces a bare
  `{}` into a phantom one-element list).
- **Docs: the solution-pack picklist merge rule was wrong.** The export/import
  guide said picklists are "left as-is" under `whenExists="keep"` and that "a pack
  that renames or reorders items will not change them unless you overwrite". Both
  are false. `"keep"` preserves your **additions** — locally-added items survive
  and nothing is deleted — but the pack's own items are still **upserted by uuid**,
  so a local edit to a pack-shipped item is overwritten. Verified two ways on
  8.0.0: live (a recoloured pack item reverted to the pack's colour) and in the
  appliance source (`PicklistNameConfig::import` points the bundle's payload at
  the matching existing row's `@id`, re-appending only items the bundle doesn't
  ship). The records (`replace`, uuid-matched) and modules (additive merge) rows
  were confirmed correct.

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
