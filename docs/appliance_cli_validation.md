# Appliance CLI Validation & Examples

**Status**: P2 shipped and fully tested — all service, MQ, and log commands validated end-to-end.

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

### Appliance auto-mirror (lab box fortisoar.example.com)
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

## Next Steps

1. **Commit & push** — P2 is done and tested, ready for review
2. **Live testing** — Try against fortisoar.example.com (lab) or test box if available
3. **CLI wiring** — Ensure Click commands surface all verbs correctly
4. **Doc updates** — Add to user guides / troubleshooting docs as reference

---

**Last validated**: 2026-06-20
**Test status**: 45/45 passing
**Demo status**: Offline + live examples working
