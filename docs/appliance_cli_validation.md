---
title: Appliance CLI Validation & Examples
category: tools
status: reference
source: live-verified
topics:
- appliance-cli
- ssh
- database
- service-health
- rabbitmq
- diagnostics
last_verified: '2026-06-20'
canonical: true
summary: 'Live-validated (10.99.249.205, FSR 7.6.5) guide to pyfsr appliance CLI (P1+P2):
  transport abstraction, sudo hygiene, database layer, service diagnostics, RabbitMQ
  checks, log scanning with 45 unit tests and dual demo modes.'
---

# Appliance CLI Validation & Examples

**Status**: P2 shipped; unit-tested in full and **live-validated against a real
appliance (10.99.249.205, FSR 7.6.5) on 2026-06-20** for the SSH / service / MQ /
log layers. See the *Live validation* section below for what the live run actually
proved, the bug it surfaced and fixed, and the **DB-layer blocker still open**.

## Summary

The `pyfsr appliance` CLI (phases P1 + P2) provides comprehensive diagnostics and troubleshooting for FortiSOAR appliances, with:

### P1: Transport & Database Layer (✓ complete)
- **Transport abstraction** — `LocalTransport` (direct exec) + `SSHTransport` (remote ssh)
- **Sudo hygiene** — passwords via stdin (`-S`), env vars re-applied inside sudo context (avoids `env_reset` stripping)
- **Facts caching** — device UUID, content DB discovery, version
- **Database verbs** — read-only query, list DBs, find module tables, drop tables

### P2: Live Triage Verbs (✓ complete)
- **Service diagnostics** — status, liveness (detects wedged services), restart, listeners
- **RabbitMQ checks** — queue depth/consumers, backlogs, zero-consumer queues, vhosts, permissions
- **Log scanning** — tail service logs, scan recent errors via journalctl

## Test Coverage

**45 unit tests** validate every command family offline (using `FakeTransport`):

```bash
python -m pytest tests/unit/test_appliance_cli.py -v
```

### Test Breakdown
- **Transport** (7 tests): sudo wrapping, SSH config, password handling, auth
- **Database** (10 tests): facts caching, DB discovery, role resolution, table operations
- **Service** (7 tests): status, liveness (wedge detection), restart gate, listeners
- **RabbitMQ** (8 tests): queue flagging, consumer tracking, vhost/permission listing
- **Logs** (8 tests): tail (aliases + raw paths), error scanning, unit coverage

**Key validation points**:
- ✓ Sudo commands properly wrap env vars inside privileged context
- ✓ Service liveness detects "active but wedged" (HTTP 000/timeout)
- ✓ MQ queues flag "NO CONSUMERS" and "BACKLOG (>1000)" separately
- ✓ Logs tail uses service aliases and raw paths
- ✓ Permission gates block restart/drop/exec without `--yes`

## Live Examples

### 1. Offline Demo (no appliance needed)
Demonstrates all commands with mocked appliance data:

```bash
python examples/appliance_cli_test_demo.py
```

**Output**: Shows service status, MQ health, logs, DB discovery, permission gates.

### 2. Live Example (requires appliance)
Validates commands against a real appliance:

```bash
# Local appliance (auto-detect /opt/cyops)
python examples/appliance_cli_live_example.py

# Remote appliance
python examples/appliance_cli_live_example.py --host 10.0.0.1 --user csadmin

# With password (or set PYFSR_APPLIANCE_PASSWORD)
python examples/appliance_cli_live_example.py --host 10.0.0.1 --password secret
```

**Features**:
- Auto-detects local vs remote (SSH)
- Reads credentials from CLI, env, or prompts
- Exercises all command families
- Pretty-prints results with formatting hints

## Command Families

### Service (systemd / cyops)
```python
from pyfsr.cli.appliance import service

service.status(transport)                    # csadm services --status
service.status(transport, name="cyops-auth") # per-service
service.liveness(transport)                  # Probe endpoints, detect wedges
service.restart(transport, name, yes=True)   # Restart (gated)
service.listeners(transport)                 # ss -tlnp (ports + processes)
```

**Wedge detection**: curl with `--max-time 6` → code 000 = no response = wedged → restart candidate.

### RabbitMQ (rabbitmqctl -q)
```python
from pyfsr.cli.appliance import mq

mq.status(transport)              # Broker status
mq.queues(transport)              # [queue, messages, consumers, flag]
mq.consumers(transport)           # Active subscriptions
mq.vhosts(transport)              # Virtual hosts
mq.permissions(transport)         # User → configure/write/read
```

**Flags**:
- "NO CONSUMERS" — queue has messages but no worker
- "BACKLOG (>1000)" — queue depth ≥ 1000

### Logs (journalctl + tail)
```python
from pyfsr.cli.appliance import logs

logs.tail(transport, "auth", lines=100)       # Service alias → path lookup
logs.tail(transport, "/var/log/custom.log")   # Raw path fallback
logs.scan(transport, minutes=30)              # Recent errors across units
```

**Service aliases** (hardcoded):
- `auth` → `/var/log/cyops/cyops-auth/cyops-auth.log`
- `api`, `postman`, `integrations`, `workflow`, `nginx`

**Scan units**: `cyops-auth`, `cyops-api`, `cyops-workflow`, `cyops-integrations`, `celeryd`

### Database (psql)
```python
from pyfsr.cli.appliance.facts import Facts
from pyfsr.cli.appliance import db

facts = Facts(transport)

db.query(facts, "SELECT ...")                  # Read-only (rejects writes)
db.list_databases(facts)                       # [name, size, role]
db.find_module_tables(facts, "widgets")        # Base + join tables
db.drop_module_tables(facts, "widgets", yes=True)  # Delete + publish
db.exec_write(facts, "DELETE...", yes=True)    # Arbitrary writes (gated)
```

## Known Issues & Workarounds

### Module delete (`drop_module_tables`)
- **Issue**: After soft-delete (discard staging), physical tables remain orphaned
- **Workaround**: `db drop --yes` finds + drops the orphans as a cleanup step

### Import wizard stall
- **Issue**: Config import is a publish-in-disguise; poll `/api/3` returns 503 mid-migrate
- **Observed**: Job status transitions to Error with errorMessage if anything fails
- **Workaround**: Check `POST /api/modules-admin/jobs/{id}` status before declaring success

### Appliance auto-mirror (lab box 10.99.249.159)
- **Issue**: Lab box auto-mirrors staging→published, distorting `is_published` checks
- **Context**: For ad-hoc testing only; does NOT apply to prod or test boxes

## Integration

The CLI is wired into `pyfsr appliance` commands via the Click CLI in `src/pyfsr/cli/appliance/__init__.py`. To use:

```bash
# Install pyfsr
pip install -e .

# Run appliance commands
pyfsr appliance service status
pyfsr appliance mq queues
pyfsr appliance logs scan
pyfsr appliance db list
```

## Files

- **Commands**: `src/pyfsr/cli/appliance/{service,mq,logs,db}.py`
- **Transport**: `src/pyfsr/cli/appliance/transport.py` (LocalTransport, SSHTransport)
- **Facts**: `src/pyfsr/cli/appliance/facts.py` (device UUID, content DB, psql runner)
- **Tests**: `tests/unit/test_appliance_cli.py` (45 tests, all passing)
- **Examples**: `examples/appliance_cli_{test_demo,live_example}.py`

## Live validation (10.99.249.205, FSR 7.6.5 — 2026-06-20)

First real-appliance run, read-only verbs, over `SSHTransport` (`csadmin`, sudo via
`-S`). Drove `examples/appliance_cli_live_example.py` + the `pyfsr appliance`
console group directly.

**Confirmed working live:**
- **Transport / sudo** — SSH connect, `sudo -S`, and env-in-sudo all behaved as designed.
- **`service status`** — real `csadm services --status` table (16 services).
- **`service liveness`** — probes ran; flagged auth `POST /auth/authenticate` 500 and
  `GET /api/3` 403 (expected without API auth) vs `das` license 200.
- **`service listeners`** — real `ss -tlnp` output.
- **`mq status` / `mq vhosts`** — real `rabbitmqctl` output (13 vhosts).
- **`logs scan`** — journalctl roll-up clean.
- **`facts.device_uuid()`** — parsed.

**Bug found and fixed:** `logs.py` `LOG_PATHS` were stale guesses. On 7.6.5 the real
files differ — auth → `cyops-auth/das.log` (not `cyops-auth.log`), api → `prod.log`,
workflow → `fsr-workflow.log`, postman moved under `cyops-routing-agent/`. The old
map tailed non-existent files and returned **empty silently**. Fixed the map to the
verified paths, added `gateway`/`notifier`/`connectors`/`celery` aliases, corrected
`_SCAN_UNITS` to the real systemd unit names (`fsr-api-consumer`/`fsr-workflow`/…),
and made `tail` raise `FileNotFoundError` on a missing path instead of empty-string.
Re-verified live (`logs tail workflow` returns content; missing path errors). +2 unit
tests.

**`mq queues`/`consumers`/`permissions` returned empty** — not a bug: they target the
default `/` vhost, which is empty here (real queues live in the per-tenant `vhost_*`).
This matches the known limitation (FOLLOWUPS: "MQ commands only cover the `/` vhost").

**DB layer — blocker found AND fixed (live-validated):** initially every `db` verb
and content-DB discovery failed on 205 with `FATAL: password authentication failed for
user "cyberpgsql"`. Root cause: `facts.py` resolved the device UUID (= the pg/ES
password) from `csadm license --get-device-uuid` **first**, but on a box whose
entitlement has drifted (FortiCloud re-issue) that returns the *current* UUID, which
differs from the **original install-time UUID** the DB was provisioned with. Proven
live on 205: the two values **differ**, and only the file value authenticates.

Fix: `facts.device_uuid()` now reads `/home/csadmin/device_uuid` (the install-time
file, csadmin-readable, no sudo) **first**, with `csadm license` as fallback. After the
fix, live on 205: `db list` correctly fingerprints `venom` as the content DB and
resolves the role DBs (`das`/`connectors`/`notifier`/`gateway`); `db query "SELECT
count(*) FROM model_metadatas"` → 65; the write-guard correctly refuses `DROP TABLE`
via `db query` (directs to `db exec --write --yes`); `info` resolves version/content
DB/UUID. The destructive `db drop-module-tables` / `delete_module(drop_orphan_tables=…)`
path still needs a run against a real orphan, but its auth + gating are now proven live.

**Packaging note (not a code bug):** the *installed* `pyfsr` console script is a stale
wheel without the appliance connection flags (`--host/--user/...`); the CLI must be run
from a `pip install -e .` checkout until the appliance CLI is released.

## Next Steps

1. **Orphan-table drop** — the only `db` path not yet run live (it mutates); exercise
   `db drop-module-tables` / `delete_module(drop_orphan_tables=…)` against a real orphan.
2. **Doc updates** — fold into user guides / troubleshooting docs as reference.

---

**Last validated**: 2026-06-20 (live, 10.99.249.205, FSR 7.6.5 — read-only verbs across
service/mq/logs/db, incl. content-DB discovery after the device-UUID fix)
**Test status**: full unit suite green; `logs` path fix + device-UUID file-first fix
(+ split device-uuid test into primary/fallback)
**Demo status**: Offline + live examples working
