# pyfsr SDK Roadmap — making pyfsr the reusable FortiSOAR client

**Status:** LIVING DOC — update the Progress Ledger + phase checkboxes as work lands.
**Owner:** Dylan Spille · **Started:** 2026-06-08 · **Target consumers:** fsrpb, the
connector, pyfsr-cli, and any future FortiSOAR Python project.

---

## 1. Goal & principles

Turn pyfsr from a thin transport + a few resource modules into a **user-friendly,
batteries-included FortiSOAR client** that other projects depend on instead of
re-implementing the same FortiSOAR access patterns by hand.

**Dividing line (unchanged):** pyfsr = transport + raw resource access + FSR semantics
that are *generic* (CRUD, query DSL, picklists, hydra, connectors, playbook runs,
safe-delete). fsrpb/connector keep the *domain* logic (YAML compiler, agent UX, MCP
tools, on-platform crudhub bridge, local reference DB).

**Hard constraints**
- **No dependency on fsrpb / fsr_core.** Extraction direction is one-way: fsrpb → pyfsr.
  `fsr_core` imports pyfsr nowhere today; keep it that way.
- **Python 3.10+** (pyfsr's floor — distinct from `fsr_core`, which must stay 3.9-clean).
  PEP 604 unions, `match`, etc. are fine here.
- **Pydantic models from the start** (decided 2026-06-08). Core entities return typed
  objects, not bare dicts. Adds a runtime dep — acceptable for DX. Use Pydantic v2.
- Backward-compatible: existing `client.get/post/put/delete/query`, `client.alerts`,
  `client.content_hub`, `client.export_config`, `client.files` keep working.
- Every phase ships green: `ruff check`, `ruff format --check`, unit tests, and (where a
  live box is available) the opt-in `-m integration` suite.

---

## 2. Current state (baseline, 2026-06-08)

Surface today (`src/pyfsr/`):
- `client.py` — `FortiSOAR`: `request/get/post/put/delete`, `query(module, body)`,
  https/port normalization, auth wiring.
- `api/alerts.py` — `AlertsAPI` CRUD (the only typed module).
- `api/content_hub.py` — `ContentHubSearch` (packs/connectors/widgets search).
- `api/solution_packs.py`, `api/export_config.py` — export workflow.
- `api/base.py` — `BaseAPI` (just holds `self.client`).
- `auth/` — `APIKeyAuth`, `UserPasswordAuth`, `BaseAuth` (unsupported-op gating).
- `utils/file_operations.py` — `upload` / `upload_many`.
- Versioning via hatch-vcs; `py.typed` shipped; ruff/pre-commit; tests deselect
  `integration` by default (`pytest -m integration` to opt in).

What's missing = everything in §3.

---

## 3. The gap (evidence-backed)

Patterns other projects reimplement that belong in pyfsr (source refs are for extraction,
NOT for importing — copy + de-couple):

| # | Capability | Source to mine | Notes |
|---|---|---|---|
| 1 | Generic record CRUD by module | `fsr-playbook-framework/fsr_core/mcp_server/tools_triage.py:1493-1705` (`get_record`, `search_module_records`) | Strip the agent token-budget projection; keep core fetch/search. |
| 2 | Query DSL builder | inline in `fsr-playbook-framework/python/_prove_rel_query2.py`, `cli.py`, etc. | `{logic, filters:[{field,operator,value}], sort, __selectFields, __ignoreFields}` → `POST /api/query/{module}`. |
| 3 | Picklist resolution | `fsr-playbook-framework/python/picklists.py` (~240 LOC, self-contained) | value↔IRI, field→picklist (Jaccard fallback), caching. Move wholesale. |
| 4 | Hydra envelope + pagination | scattered | `hydra:member` / `hydra:totalItems`, `$limit/$page` lazy iterator. |
| 5 | Connector ops | `tools_execution.py:1488-1700` (`run_op`), `:2017-2071` (`healthcheck`), `tools_triage.py:1052-1141` (`list_configured`) | Drop agent confirm-gates; keep execute/healthcheck/list. Document agent-proxied-execute-async caveat. |
| 6 | Playbook run listing | `tools_execution.py:2074-2105+` (`_fetch_runs_both`, `_shape_run`) | Live (`/api/wf/api/workflows/`) + historical dedup by `@id`. |
| 7 | Safe-delete + recycle | `python/cli.py`, `python/recover.py`, `_live_crudhub.py:87-98` | `$hardDelete`, `$showDeleted`, restore via `deletedAt:null`, empty-body DELETE no-op guard. |
| 8 | Env→client config | `fsr-playbook-framework/python/probes/_env.py:46-108` (`EnvConfig`, `get_client`) | `FSR_*` env → client; http/https/port restore. |

**OpenAPI spec** (`Miscellaneous/fortisoar-api-docs/build/fortisoar.curated.openapi.yaml`):
OpenAPI 3.1, 99 paths / 130 ops, **no operationIds**, 14 schemas (Incident/Task/Comment
auto-derived + rich; Alert/QueryBody/Hydra hand-curated). → **not a full-codegen target.**
Use it for: (a) typed models for ~3-10 core modules, (b) bundled reference, (c) drift
detection. Re-runnable pipeline: `module_to_schema.py` → `live_test.py` → `build_curated.py`
in `Miscellaneous/fortisoar-api-docs/src/`.

---

## 4. Target architecture

```
pyfsr/
  client.py        FortiSOAR — adds .records, .picklists, .connectors, .playbooks accessors
  query.py         Query builder (filters/sort/select) → POST /api/query/{module}
  pagination.py    HydraPage + paginate() lazy iterator over hydra envelopes
  records.py       RecordsAPI / RecordSet — generic CRUD for any module
  models/          Pydantic v2 models (BaseRecord, Alert, Incident, Task, Comment, Hydra…)
    __init__.py
    base.py        BaseRecord (@id/IRI, uuid, picklist coercion helpers)
    _generated.py  models derived from the OpenAPI spec (regenerable)
  picklists.py     PicklistsAPI — value↔IRI, field→picklist (ported)
  connectors.py    ConnectorsAPI — execute / healthcheck / list_configured
  playbooks.py     PlaybooksAPI — runs() live+historical, get/resume
  config.py        EnvConfig.from_env() → FortiSOAR (FSR_* loader)
  api/…            existing alerts/content_hub/export (kept; alerts may re-home onto records)
  resources/
    fortisoar.curated.openapi.yaml   bundled spec (reference + drift)
```

**Naming/DX targets**
- `client.records("incidents").get(uuid)` / `.search(q)` / `.create(model_or_dict)` /
  `.update(uuid, …)` / `.delete(uuid, hard=False)` / `.restore(uuid)`.
- `client.records("incidents").query(Query().eq("severity.itemValue","Critical").limit(50))`
  → returns a `HydraPage[Incident]` you can iterate / `.all()` / `.paginate()`.
- Typed returns when a model exists for the module; `BaseRecord` otherwise.
- Picklist ergonomics: pass `"Critical"` for a picklist field and have create/update
  resolve it to the IRI automatically (opt-in flag).

---

## 5. Phases

> Mark `[x]` when landed; add the commit hash. Keep the Progress Ledger (§6) in sync.

### P1 — Backbone: records + query + pagination  ⏳ NOT STARTED
- [ ] `query.py`: `Query` builder — `.eq/.neq/.in_/.contains/.gt/.lt/.between`, `.and_/.or_`
      groups, `.sort()`, `.select()/.ignore()`, `.limit()/.page()`; `.to_body()` emits the
      DSL dict. Unit-tested against known-good payloads (mine `_prove_rel_query2.py`).
- [ ] `pagination.py`: `HydraPage` (members, total, page, has_next) + `paginate()` lazy
      generator that walks `$page` until exhausted.
- [ ] `records.py`: `RecordsAPI`/`RecordSet` — `get(uuid|iri)`, `search(q, limit)`,
      `query(Query)`, `create`, `update`, `delete`, `list`. Hydra-aware; module-or-IRI input;
      `module:uuid` shorthand.
- [ ] Wire `client.records(module)` accessor.
- [ ] Unit tests with mocked Session (extend `tests/conftest.py` mock_client).
- [ ] Docs: usage snippet in README.

### P2 — Pydantic models (core)  ⏳ NOT STARTED
- [ ] Add `pydantic>=2` to deps; `models/base.py` `BaseRecord` (extra=allow, IRI/uuid
      helpers, picklist field coercion).
- [ ] Hand-write Alert; generate Incident/Task/Comment from the OpenAPI schemas
      (`models/_generated.py` + a `scripts/gen_models.py` that reads the curated spec).
- [ ] `RecordSet` returns typed models when registered for the module, else `BaseRecord`.
- [ ] Re-home `AlertsAPI` onto records (keep `client.alerts` as a typed convenience shim).
- [ ] Tests: round-trip dict↔model, picklist coercion.

### P3 — Picklists  ⏳ NOT STARTED
- [ ] Port `python/picklists.py` → `pyfsr/picklists.py` as `PicklistsAPI` (drop sqlite/DB
      coupling; keep live lookups + in-process cache). `list()`, `values(name)`,
      `for_field(module, field)`, `resolve(value, picklist)`, `resolve_record_fields(...)`.
- [ ] Wire `client.picklists`; opt-in auto-resolution in `records.create/update`.
- [ ] Tests with mocked picklist endpoints.

### P4 — Safe deletes + recycle  ⏳ NOT STARTED
- [ ] `delete(uuid, hard=False)`, `restore(uuid)`, `get(..., show_deleted=True)`,
      `search(..., show_deleted=…)`. Bake in the empty-body DELETE no-op guard.
- [ ] Document the hazards (hardDelete is single-row, soft-delete reserves uuid+name).
- [ ] Tests.

### P5 — Connectors & playbooks  ⏳ NOT STARTED
- [ ] `connectors.py`: `execute(name, version, op, params, config_id=None)`,
      `healthcheck(name, version)`, `list_configured(...)`. Document agent-proxied-execute
      async caveat (result is websocket-pushed; not pollable) — surface a clear warning,
      don't falsely report success.
- [ ] `playbooks.py`: `runs(limit, status=…)` (live+historical dedup), `get(run_id)`,
      `resume(...)` (wfinput_resume). Models for run shape.
- [ ] Tests.

### P6 — DX polish & release  ⏳ NOT STARTED
- [ ] `config.py`: `EnvConfig.from_env()` → `FortiSOAR` (FSR_* loader, http/port restore).
- [ ] Retry+timeout (urllib3 Retry on idempotent GETs; configurable timeout).
- [ ] Richer exceptions mapped from FSR error bodies; ensure auth-header masking on logs.
- [ ] Bundle `resources/fortisoar.curated.openapi.yaml`; optional `pyfsr.spec` loader +
      drift check.
- [ ] README rewrite (quickstart, recipes), API docs (sphinx already present).
- [ ] Tag a clean `v0.3.0` once P1–P3 are in; cut releases per phase thereafter.

---

## 6. Progress ledger

| Phase | Status | Commit(s) | Notes |
|---|---|---|---|
| Pre-work: tooling + API-consistency refactor | ✅ DONE | `4b2d13d` `bafe17f` `5007c46` | hatch-vcs, ruff, BaseAPI unify, content_hub wired. Pushed to origin. |
| Pre-work: integration tests deselected by default | ✅ DONE | `ef3e252` | plain `pytest` now unit-only (30 passed / 14 deselected, 0.04s). |
| P1 backbone | ⏳ not started | — | |
| P2 pydantic models | ⏳ not started | — | |
| P3 picklists | ⏳ not started | — | |
| P4 safe deletes | ⏳ not started | — | |
| P5 connectors & playbooks | ⏳ not started | — | |
| P6 DX & release | ⏳ not started | — | |

---

## 7. Open questions / decisions

- **Pydantic v2 hard dep vs optional extra?** Decision: hard dep (DX priority). Revisit if a
  consumer needs a zero-dep install.
- **Model coverage source of truth:** spec-derived for Incident/Task/Comment; live
  `introspect_schemas()` for the rest. Where to cache generated models (committed
  `_generated.py` vs runtime)? → commit generated, regenerate via script.
- **alerts.py fate:** keep as a typed shim over `records("alerts")` for backward compat.
- **fsrpb migration:** once P1–P3 land, point fsrpb's `probes/_env.py` + triage helpers at
  pyfsr and delete the duplicated logic (separate follow-up, tracked in fsrpb's plan).

---

## 8. References
- Extraction sources: see §3 table (paths are in sibling repos under `~/PycharmProjects`).
- OpenAPI spec + build pipeline: `Miscellaneous/fortisoar-api-docs/`.
- FSR delete/recycle hazards, verified endpoints, agent-async caveat: fsrpb auto-memory.
