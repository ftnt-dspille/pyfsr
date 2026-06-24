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
pyfsr appliance service restart --name celeryd --yes
pyfsr appliance service stop --name postgresql --yes
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

## When to use the API instead

`pyfsr appliance` is for the *box*. For anything that has a REST endpoint —
records, queries, modules, connectors, playbooks — use the SDK client or the
`pyfsr playbook` / record CLI verbs, which authenticate against `/api/3` with
`FSR_*` settings rather than SSH. See {doc}`authentication` and
{doc}`playbook-authoring`.
