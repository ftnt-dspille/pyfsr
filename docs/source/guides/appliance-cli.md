# Appliance Administration CLI

The `pyfsr appliance` command group administers a FortiSOAR **appliance** —
the host itself, not the REST API. Where `pyfsr playbook` and the rest of the
SDK talk to `/api/3`, `pyfsr appliance` reaches the box over **SSH** (or runs
locally when invoked on-box) and drives `csadm`, `psql`, `rabbitmqctl`,
`systemctl`, and the Elasticsearch/HA tooling for you.

Use it to answer operational questions ("are all services up?", "what's the
queue backlog?", "is the cluster green?") and to perform recovery actions
(restart a wedged service, purge a stuck workflow queue, drop orphaned module
tables) without hand-typing `sudo csadm …` over an SSH session.

```{warning}
These commands act directly on a production appliance. The mutating ones
(`db exec`, `db drop-module-tables`, `service restart/stop`, `mq purge`,
`certs regenerate`, …) are gated behind `--yes` and, for SQL writes, an
additional `--write`. Read [Destructive commands](#destructive-commands)
before using them.
```

## Connecting

Connection flags live on **each subcommand** (not on the `appliance` group), so
they follow the verb:

```bash
# Remote, over SSH
pyfsr appliance info --host 10.0.0.1 --user csadmin --key ~/.ssh/id_rsa

# Remote, password auth
pyfsr appliance service status --host 10.0.0.1 --password '...'
```

| Flag | Meaning |
|---|---|
| `--host` | SSH target. **Omit it to run locally** — if `/opt/cyops` exists on the current box, the CLI execs directly instead of opening an SSH connection. |
| `--user` | SSH user (default `csadmin`). |
| `--password` | SSH/sudo password. |
| `--port` | SSH port (default `22`). |
| `--key` | SSH private-key path. |
| `--insecure-skip-host-key-check` | Skip SSH host-key verification — MITM-exposed, lab only. |

All connection values also read from the environment, so you can set them once:

```bash
export PYFSR_APPLIANCE_HOST=10.0.0.1
export PYFSR_APPLIANCE_USER=csadmin
export PYFSR_APPLIANCE_PASSWORD='...'

pyfsr appliance info
pyfsr appliance service status
```

```{note}
Secrets never reach `argv` (they'd show up in `ps`): the password is passed to
SSH/`sudo -S` over stdin, and the Postgres/Elasticsearch password — the
appliance **device UUID** — is resolved once and never logged. Privileged
verbs (`csadm`, `rabbitmqctl`, `journalctl`) run under `sudo -S`, so the
account needs sudo on the box.
```

## Inspecting the appliance

Start with the identity card and a service sweep:

```bash
pyfsr appliance info              # host, FortiSOAR version, content DB, device UUID
pyfsr appliance service status    # parsed csadm services; exit 1 if anything is DOWN
pyfsr appliance service liveness  # probes endpoints for "up but wedged" services
```

Resource and queue health:

```bash
pyfsr appliance host snapshot --disk-path /opt/cyops   # one coherent mem/swap/load/RSS/disk sample
pyfsr appliance host rss 'celeryd'                      # summed + peak RSS for matching processes
pyfsr appliance mq queues                               # depth + consumers; flags backlog / zero-consumer
pyfsr appliance es health                               # cluster green/yellow/red; exit 1 if red
pyfsr appliance ha nodes                                # HA nodes, current node marked with *
```

The typed return shapes — the values you get back in Python or from `--json`:

```{doctest}
>>> box = demo_box()
>>> box.host.meminfo()                              # mem + swap, in MB
MemInfo(total_mb=24096, used_mb=12000, free_mb=500, swap_total_mb=8191, swap_used_mb=1024)
>>> box.host.snapshot().summary()                   # one coherent sample, one line
'mem 12000/24096MB | swap 1024/8191MB | celeryd 3.0MB/2w (peak 2.0MB) | integrations 0.0MB/0w (peak 0.0MB) | load 1.5 | /opt/cyops 50%'
>>> box.mq.queues()                                 # depth + consumers; flag calls out trouble
[QueueInfo(name='task_queue', messages=100, consumers=1, flag=''), QueueInfo(name='default_queue', messages=50, consumers=2, flag='')]
>>> box.es.health()                                 # green/yellow/red + shard counts  # doctest: +ELLIPSIS
ESHealth(status='green', cluster_name='fortisoar', num_nodes=1, num_data_nodes=1, active_shards=120, unassigned_shards=0, ...)
>>> box.ha.nodes()                                  # current node marked is_current=True
[HaNode(node_id='572b3ecd3ddbc133a650f3faecc7c286', name='fsr-1', status='active', role='primary', comment='primary server', mode='operational', fsr_version='7.6.2-5507', is_current=True)]
```

A wedged service — *active* per `systemctl` but never responding — is what
`service liveness` catches (HTTP `000` within a hard timeout). Each probe is
`ok` / `unexpected (<code>)` / `WEDGED`:

```{doctest}
>>> [p.verdict for p in box.service.liveness()]
['ok', 'ok', 'ok']
```

Most read verbs accept `--json` (and some `--csv`) for scripting:

```bash
pyfsr appliance host mem --json
pyfsr appliance mq queues --json
```

## Querying the database

`db query` runs a **read-only** SELECT against a chosen database. Pick the
database by logical role (`--role`, default `content`) or by explicit name
(`--db`, which overrides `--role`):

```bash
pyfsr appliance db list                                  # databases with sizes + roles
pyfsr appliance db tables 'widget%'                      # tables matching a LIKE/glob pattern
pyfsr appliance db query "SELECT COUNT(*) FROM widgets"
pyfsr appliance db query --role content "SELECT id, name FROM widgets LIMIT 10" --json
pyfsr appliance db query --db venom "SELECT * FROM model_metadatas LIMIT 5"
```

The return is `(dbname, headers, rows)` — the resolved DB name first (so you can
see *which* DB answered), then the column headers, then the rows as lists of
strings:

```{doctest}
>>> dbname, headers, rows = box.db.query("SELECT count(*) FROM widgets")
>>> (dbname, headers, rows)
('venom', ['count'], [['42']])
>>> dbname, headers, rows = box.db.query("SELECT id, name FROM widgets LIMIT 2")
>>> (dbname, headers, rows)
('venom', ['id', 'name'], [['1', 'widget-alpha'], ['2', 'widget-beta']])
>>> box.db.databases()                             # name + pg_size_pretty + detected role
[DatabaseInfo(name='venom', size='7 GB', role='content'), DatabaseInfo(name='das', size='200 MB', role='das'), DatabaseInfo(name='postgres', size='8 MB', role='')]
```

### How the target database is chosen

FortiSOAR runs **several** Postgres databases, not one. `db query`/`db exec`
resolve which one to hit by this precedence:

1. **`--db <name>`** — an explicit name wins outright; no resolution happens.
2. **`--role <role>`** — a *fixed-name* role maps directly: `das`, `gateway`,
   `connectors`, `notifier`, `data_archival` (each is its own DB of that name).
3. **`--role content`** (the default) — the content DB is **install-specific**,
   so its name is *discovered*, not looked up. pyfsr lists every database and
   fingerprints for the one that holds the `model_metadatas` table — that table
   exists only in the content DB, so its presence identifies it unambiguously.
   (Commonly `venom`, but never assume the name.)

```{doctest}
>>> box.facts.resolve_db(db="venom")              # explicit name → verbatim
'venom'
>>> box.facts.resolve_db(role="das")             # fixed role → its DB name
'das'
>>> box.facts.resolve_db()                        # default content role → discovered
'venom'
```

The DB/ES password is the appliance **device UUID** (user `cyberpgsql` /
`elastic`); it's resolved once from the install-time file and never logged. An
unknown role is rejected outright rather than silently hitting the wrong DB:

```{doctest}
>>> box.facts.resolve_db(role="bogus")
Traceback (most recent call last):
    ...
pyfsr.cli.appliance.transport.TransportError: unknown DB role 'bogus'; known roles: content, das, gateway, connectors, notifier, data_archival
```

### Raw SQL safety — read the fine print

`db query` and `db exec` run your SQL **verbatim** through `psql`. There is no
parser between you and Postgres — what you type is what runs. Two consequences
worth knowing cold:

- **No `WHERE` guard, no transaction.** `db exec "DELETE FROM widgets"` with no
  `WHERE` wipes the table; there is no dry-run, no rollback, no confirmation
  beyond `--yes`. The guard is the `--write` + `--yes` *process* gate, not a
  value-level check on the SQL. Treat every `exec` as irreversible.
- **`--yes` is a process gate, not a value gate.** It confirms *intent* ("I'm
  sure I want to run a write"); it does **not** inspect the statement. A typo'd
  `WHERE` or a missing `LIMIT` runs exactly as written. Review the SQL before
  the flag, not after.
- **The write-detection is a leading-keyword regex.** `db query` refuses
  statements whose first word is `INSERT`/`UPDATE`/`DELETE`/`DROP`/`CREATE`/
  `ALTER`/`TRUNCATE`/`GRANT`/`REVOKE`/`COMMENT`/`REINDEX`/`VACUUM`. A mutating
  statement hidden behind a leading `WITH` (CTE) — e.g.
  `WITH x AS (...) DELETE FROM ...` — is **not** detected and would slip past
  `db query`. Use `db exec` (with `--write --yes`) for any such statement, and
  don't rely on `db query`'s refusal as a security boundary.

```{doctest}
>>> box.db.query("DELETE FROM widgets")          # write blocked on the read path
Traceback (most recent call last):
    ...
ValueError: refusing to run a mutating statement via `db query` ��� use `db exec --write --yes`
```

`db query` refuses anything that isn't a read. Mutating SQL goes through
`db exec` and is doubly gated — see below.

## Logs and diagnostics

```bash
pyfsr appliance logs tail workflow -n 200    # tail a cyops service log
pyfsr appliance logs scan --minutes 60       # roll up recent journal errors
pyfsr appliance logs bundle                  # csadm log --collect → tarball path (slow)
pyfsr appliance diagnose                     # run fsr_diagnose.sh on the appliance
pyfsr appliance license drift                # device-UUID file vs csadm; exit 1 if drifted
```

## Destructive commands

Every state-changing verb requires `--yes`. SQL writes additionally require
`--write`, so an `exec` can't run unless you explicitly opt into *both* "this is
a write" and "I'm sure".

```bash
# Repair SQL — needs BOTH --write and --yes
pyfsr appliance db exec --write --yes \
    "UPDATE workflow_steps SET status='complete' WHERE id='...'"

# Drop orphaned physical tables left behind by a module delete (DROP ... CASCADE)
pyfsr appliance db drop-module-tables widgets --yes

# Service control
pyfsr appliance service restart celeryd --yes
pyfsr appliance service stop postgresql --yes
pyfsr appliance service restart-all --yes        # whole stack, serial, can take minutes

# RabbitMQ
pyfsr appliance mq purge my-queue --yes                  # purge one queue (irreversible)
pyfsr appliance mq purge-workflows --yes --graceful      # clear stuck backlog + recycle celeryd

# TLS
pyfsr appliance certs regenerate appliance.corp.com --yes   # restart services afterward
```

```{tip}
`db drop-module-tables` targets the physical tables that a module deletion
leaves orphaned — the API delete discards the staging definition and republishes
but does **not** drop the underlying table. Always `db query` for the matching
table names first, confirm they're truly orphaned, then drop.
```

## From Python

Everything above is also available programmatically through
{class}`~pyfsr.appliance.Appliance` — the same verbs, grouped the same way, so the Python
API mirrors the CLI. Construct it with SSH details (or run on-box with no `host`
for a local transport):

```python
from pyfsr import Appliance

box = Appliance(host="10.0.0.1", user="csadmin", key_path="~/.ssh/id_rsa")

box.info()                                  # identity card
dbname, headers, rows = box.db.query("SELECT count(*) FROM alerts")
for q in box.mq.queues():
    print(q)
print(box.service.status())
print(box.es.health())
```

The return shapes below are **real** — captured off a lab appliance and frozen
as fixtures — and they are **doctested**, so they can't silently drift from what
the box actually returns. (`demo_box()` builds a healthy `Appliance` over a
replay transport seeded with those captures; it ships in
``pyfsr._testing`` for exactly this kind of offline verification.)

```{doctest}
>>> box = demo_box()
>>> box.info()                                   # identity card (device UUID masked)
{'target': 'demo', 'fsr_version': '7.6.5', 'device_uuid': '0123…cdef', 'content_db': 'venom', 'db_user': 'cyberpgsql'}
>>> print(box.service.status())                  # parsed csadm services --status
cyops-auth...............[Running]      since Fri 2026-05-22 01:18:16 UTC
cyops-api................[Running]      since Thu 2026-05-07 14:10:22 UTC
>>> box.service.services()                       # typed per-service states
[ServiceState(name='cyops-auth', running=True, status='Running', since='Fri 2026-05-22 01:18:16 UTC'), ServiceState(name='cyops-api', running=True, status='Running', since='Thu 2026-05-07 14:10:22 UTC')]
>>> box.db.tables()                              # (dbname, headers, rows)
('venom', ['table'], [['widgets'], ['widgets_alerts'], ['widgets_team'], ['gadgets']])
>>> box.db.sizes()                              # csadm db --getsize, normalised to MB
[DataClassSize(data_class='Primary Data', size='7354 MB', size_mb=7354.0), DataClassSize(data_class='Audit Logs', size='1089 MB', size_mb=1089.0), DataClassSize(data_class='Workflow Logs', size='1138 MB', size_mb=1138.0), DataClassSize(data_class='Archived Data', size='8396 kB', size_mb=8.199)]
>>> box.db.find_module_tables("widgets")         # base + join tables (orphan cleanup)
['widgets', 'widgets_alerts', 'widgets_team']
>>> box.host.disk("/opt/cyops")                 # df -Pm, in MB
DiskUsage(path='/opt/cyops', size_mb=102400, used_mb=51200, avail_mb=51200, use_pct=50)
>>> box.license.drift()                         # file UUID vs csadm entitlement UUID  # doctest: +ELLIPSIS
DriftReport(file_uuid='0123...', csadm_uuid='0123...', drifted=False, verdict='ok (file == csadm; no entitlement drift)')
>>> box.es.shards()                             # unassigned-shard explain (empty = healthy)
(['info'], [['(no unassigned shards)']])
```

### Adding a validated return example to any guide

The capture → fixture → doctest loop above is the pattern every guide should
use for return shapes, so examples can't silently drift from the code. When you
add or change a return example:

1. **Make it a `{doctest}` block**, not a plain `python` fence. Only explicit
   `{doctest}` / `.. doctest::` directives run under `make doctest` ��� plain
   blocks are never checked, so an illustrative example is an un-validated
   example.
2. **Source the shape from a real capture**, frozen in `pyfsr._testing`. For
   appliance verbs use `demo_box()`; for REST-API shapes use `demo_client()`
   (both replay recorded responses with no network). Don't hand-type a shape
   you believe is right — re-capture it.
3. **Mask volatile fields** with `# doctest: +ELLIPSIS` (UUIDs, timestamps,
   sizes, IRIs) and a comment saying *why* the field is masked. Never edit a
   fixture by hand to make a doctest pass — re-capture, or the example stops
   representing reality.
4. **Re-run the gates** before pushing: `make doctest` (examples execute) and
   `make html -W -n` (strict — any Sphinx warning, e.g. a header-level skip,
   fails the build). CI runs both.

Refreshing the underlying fixtures on a FortiSOAR version bump is a separate,
manual step. For the appliance captures, run
`python scripts/capture_appliance_fixtures.py` against a live box (creds
required); see the module docstring of `pyfsr._testing.appliance_captures` for
the provenance stamp and refresh workflow. For the REST captures backing
`demo_client()`, the raw JSON lives in `tests/resources/mock_responses/` and a
trimmed, doctest-friendly slice lives in `pyfsr._testing.client_captures` —
re-record the raw files from a live box, then re-trim there.

Connection arguments fall back to the same `PYFSR_APPLIANCE_*` environment
variables, so `Appliance()` with no arguments works on-box or with the env set.

The verbs are grouped under attributes that match the CLI command groups —
`box.db`, `box.service`, `box.mq`, `box.host`, `box.license`, `box.logs`,
`box.es`, `box.ha`, `box.certs` — plus `box.info()` and `box.diagnose()`. The
same gating applies: mutating calls take `yes=True`, and SQL writes go through
`box.db.execute(..., yes=True)` (reads use `box.db.query(...)`).

```python
# Mutating calls are gated exactly like the CLI's --yes / --write
box.db.execute("UPDATE workflow_steps SET status='complete' WHERE id='...'", yes=True)
box.db.drop_module_tables("widgets", yes=True)
box.service.restart("celeryd", yes=True)
box.mq.purge_workflows(graceful=True, yes=True)
```

If you already have a REST client, `client.appliance(...)` reuses its host and
just needs the SSH credentials (the REST and SSH transports are separate):

```python
from pyfsr import FortiSOAR

client = FortiSOAR("https://10.0.0.1", token="<api-key>")
box = client.appliance(key_path="~/.ssh/id_rsa")
box.service.liveness()
```

For any verb not surfaced as a method, drop down to `box.facts` /
`box.transport` and call the underlying `pyfsr.cli.appliance.*` functions
directly.

## When to use the API instead

`pyfsr appliance` is for the *box*. For anything that has a REST endpoint —
records, queries, modules, connectors, playbooks — use the SDK client or the
`pyfsr playbook` / record CLI verbs, which authenticate against `/api/3` with
`FSR_*` settings rather than SSH. See {doc}`authentication` and
{doc}`playbook-authoring`.
