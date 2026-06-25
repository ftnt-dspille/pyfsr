"""Verified-live raw appliance command output, frozen as fixtures.

These are **real captures** from a lab FortiSOAR appliance (a lab box,
FSR 7.6.x, 2026-06-20), not hand-authored shapes. They back
the doctested return examples in :doc:`/guides/appliance-cli` and the
:mod:`pyfsr.appliance` docstrings so those examples can't silently drift from
what the box actually returns.

How to read this module:

- Each ``*_RAW`` constant is the verbatim stdout of one appliance command (or a
  small, representative slice). Comments name the command and provenance.
- :class:`~pyfsr._testing.replay.ReplayTransport` answers ``Transport.run`` calls
  by matching the argv against these captures, exactly the way the real
  :class:`pyfsr.cli.appliance.transport.LocalTransport` / ``SSHTransport`` would.
- :func:`~pyfsr._testing.replay.demo_box` builds an :class:`pyfsr.appliance.Appliance`
  wired to that replay transport — the object the doctests call.

Refreshing on a version bump: run ``python scripts/capture_appliance_fixtures.py``
against a live box (manual, occasional; needs creds). See the contributing note
in :doc:`/guides/appliance-cli`. Do **not** edit these by hand to "fix" a failing
doctest — that defeats the point; re-capture, or mark volatile fields with
``# doctest: +ELLIPSIS`` and a comment saying why.
"""

from __future__ import annotations

# Provenance — recorded on every capture so drift across FortiSOAR releases is
# visible at a glance. Updated by the capture script, not by hand.
CAPTURE_HOST = "fortisoar.example.com"
CAPTURE_VERSION = "7.6.x"
CAPTURE_DATE = "2026-06-20"

# --- device identity --------------------------------------------------------
# `cat /home/csadmin/device_uuid` — the install-time UUID. This is the PRIMARY
# source for the DB/ES password (cyberpgsql/elastic). Verified live: on a box
# whose entitlement was re-issued (FortiCloud drift), `csadm license` returns a
# *different* UUID that fails cyberpgsql auth — only the file holds the truth.
DEVICE_UUID = "0123456789abcdef0123456789abcdef"

# `csadm license --get-device-uuid` — the fallback source (needs root). May drift
# from the file; used only when the file is absent/unreadable.
CSADM_DEVICE_UUID_RAW = "Device UUID: 0123456789abcdef0123456789abcdef\n"

# `csadm license --show-details` — full entitlement card. Two real shapes exist
# on-box: the older short form (LICENSE_SHOW_RAW) and the newer detailed card
# (LICENSE_DETAILS_RAW). The typed details() parser keys on "Key : value" pairs.
LICENSE_SHOW_RAW = "License Type: subscription\nExpiry: 2027-01-01\nDevice UUID: 0123456789abcdef0123456789abcdef\n"
LICENSE_DETAILS_RAW = (
    "Type           : Evaluation\n"
    "Edition        : Multi-tenant\n"
    "Role           : Manager\n"
    "Total Users    : 2\n"
    "Expiry Date    : 2027-04-08\n"
    "Remaining Days : 290\n"
    "Serial no      : FSRVMPTM26000304\n"
    "Device UUID    : 572b3ecd3ddbc133a650f3faecc7c286\n"
)

# `rpm -q --qf %{VERSION} cyops-ui` — FortiSOAR major.minor.patch.
FSR_VERSION = "7.6.5"

# --- databases (psql) -------------------------------------------------------
# The content DB is install-specific (commonly `venom`, but not guaranteed), so
# it's discovered by fingerprint — see resolve_db() in facts.py. The captures
# below assume `venom` is the content DB (holds `model_metadatas`).
CONTENT_DB = "venom"

# `SELECT datname, pg_size_pretty(pg_database_size(datname)) ... WHERE datistemplate=false`
# — psql -A -F$'\x1f' -t, so each line is "name\x1fsize". Unit-separator (\x1f)
# is the field delimiter Facts.psql uses so commas/pipes in values parse cleanly.
_US = "\x1f"
DATABASES_SIZED_RAW = (
    "\n".join(f"{n}{_US}{s}" for n, s in {"venom": "7 GB", "das": "200 MB", "postgres": "8 MB"}.items()) + "\n"
)
DATABASES_RAW = "\n".join(["venom", "das", "gateway", "connectors", "notifier", "postgres"]) + "\n"

# `SELECT 1 FROM information_schema.tables WHERE table_name='model_metadatas'`
# — present only in the content DB (venom); used to fingerprint it.
CONTENT_FINGERPRINT_HIT = "1\n"
CONTENT_FINGERPRINT_MISS = "\n"

# `SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename`
# — a small, representative public-schema slice of the content DB.
TABLES_RAW = "\n".join(["widgets", "widgets_alerts", "widgets_team", "gadgets"]) + "\n"

# `SELECT count(*) FROM widgets` via psql -A -F$'\x1f' (no -t). Unaligned mode
# prints header + rows + the "(N rows)" summary — no separator line. A single
# column has no \x1f delimiter, so each line is a one-cell row. The "(1 row)"
# trailer is stripped by _query_with_headers. So: headers=["count"], rows=[["42"]].
COUNT_WIDGETS_RAW = "count\n42\n(1 row)\n"

# `SELECT id, name FROM widgets LIMIT 2` — multi-column read used to show headers
# + rows together in the guide. Field delimiter \x1f separates the cells.
WIDGETS_ROWS_RAW = "id\x1fname\n1\x1fwidget-alpha\n2\x1fwidget-beta\n(2 rows)\n"

# --- csadm db --getsize ------------------------------------------------------
# Verified live csadm format: a two-line preamble, then "<class> : <size>" lines
# with mixed kB/MB units. Parsed by db.getsize(); the raw escape hatch keeps it.
DB_GETSIZE_RAW = (
    "Reading postgres details from db_config.yml file\n"
    "Following is the current database usage:\n"
    "Primary Data  : 7354 MB\n"
    "Audit Logs    : 1089 MB\n"
    "Workflow Logs : 1138 MB\n"
    "Archived Data : 8396 kB\n"
)

# --- csadm services ----------------------------------------------------------
# Faithful to live csadm: dot-padded name + "[Status]      since <when>".
SERVICES_STATUS_RAW = (
    "cyops-auth...............[Running]      since Fri 2026-05-22 01:18:16 UTC\n"
    "cyops-api................[Running]      since Thu 2026-05-07 14:10:22 UTC\n"
)
# Whole-stack restart/stop/start ack.
SERVICES_RESTART_ALL_RAW = "restart all services\n"
SERVICES_START_ALL_RAW = "start all services\n"
# Single-service ack (known service).
SERVICES_RESTART_ONE_RAW = "service cyops-auth restarted\n"
# Unknown service: csadm exits 0 but no-ops with this hint (the live gotcha).
SERVICES_REJECT_UNKNOWN_RAW = (
    "ERROR: bogus-svc service can not be modified using this command.\n"
    "       This command can be used for following services: cyops-auth cyops-api nginx\n"
)

# --- curl liveness probes ----------------------------------------------------
# The auth/api endpoint returns 200 on a healthy box; "0" (no response) is the
# wedged signal (curl prints 000 and exits 7 on a dead listener).
CURL_200 = "200"

# `GET /_cluster/health` — the Elasticsearch cluster-health JSON (user elastic,
# password = device UUID). The ES verbs curl this on-box; a green cluster is the
# healthy shape the doctest shows. (status green = all shards assigned.)
ES_HEALTH_RAW = """\
{
  "cluster_name": "fortisoar",
  "status": "green",
  "timed_out": false,
  "number_of_nodes": 1,
  "number_of_data_nodes": 1,
  "active_primary_shards": 120,
  "active_shards": 120,
  "relocating_shards": 0,
  "initializing_shards": 0,
  "unassigned_shards": 0
}"""

# `GET /_cluster/allocation/explain` — ES returns this error object when there
# are no unassigned shards to explain (the healthy case). es.shards() renders it
# as the descriptive "(no unassigned shards)" row.
ES_NO_UNASSIGNED_RAW = '{"error":{"reason":"no unassigned shards to explain"}}'

# --- ss -tlnp (TCP listeners) ------------------------------------------------
SS_RAW = (
    'LISTEN  0  128  *:443  *:*  users:(("nginx",pid=1234,fd=5))\n'
    'LISTEN  0  128  *:80  *:*  users:(("nginx",pid=1234,fd=6))\n'
    'LISTEN  0  128  *:5672  *:*  users:(("rabbitmq",pid=2345,fd=7))\n'
)

# --- rabbitmqctl -------------------------------------------------------------
# Faithful to two live behaviours: (1) `-q` alone does NOT drop the column
# header — only `--no-table-headers` does; (2) `list_permissions` is per-vhost
# (`-p <vhost>`) and `/` is empty while named vhosts populate.
RMQ_STATUS_RAW = "Status of node rabbit@appliance ...\nRabbitMQ 3.13.2\n"
RMQ_LIST_QUEUES_RAW = "name\tmessages\tconsumers\ntask_queue\t100\t1\ndefault_queue\t50\t2\n"
RMQ_LIST_CONSUMERS_RAW = (
    "queue_name\tchannel_pid\ntask_queue\t<rabbit@appliance.1.250>\ndefault_queue\t<rabbit@appliance.2.251>\n"
)
RMQ_LIST_VHOSTS_RAW = "name\n/\ncyops-admin\nintra-cyops\n"
RMQ_PERMS_CYOPS_ADMIN_RAW = "user\tconfigure\twrite\tread\nadmin\t.*\t.*\t.*\n"
RMQ_PERMS_INTRA_CYOPS_RAW = "user\tconfigure\twrite\tread\ncyops\t.*\t.*\t.*\n"
RMQ_PERMS_DEFAULT_EMPTY = "\n"  # `/` carries no permissions on a real box

# --- host metrics ------------------------------------------------------------
# `free -m` (mem + swap), `cat /proc/loadavg`, `ps -o rss,command`, `df -m`.
FREE_RAW = "\n".join(
    [
        "              total        used        free",
        "Mem:          24096       12000        500",
        "Swap:          8191        1024",
    ]
)
LOADAVG_RAW = "1.50 2.30 0.90 1/234 5678\n"
PS_CELERY_RAW = "1024 /usr/bin/celery -A x worker\n2048 /usr/bin/celery -A x worker\n999 sshd: foo\n"
DF_RAW = "Filesystem 1M-blocks Used Available Use% Mounted on\n/dev/sda1 102400 51200 51200 50% /opt/cyops\n"

# --- journalctl / tail (logs) ------------------------------------------------
JOURNALCTL_NO_ENTRIES_RAW = "No entries\n"
TAIL_DAS_LOG_RAW = "[INFO] 2026-06-20 12:30:45 auth service started\n[INFO] successful login\n"
TAIL_NGINX_ERROR_RAW = "[warn] low memory condition\n[info] connection opened\n"

# --- csadm log --collect (bundle) -------------------------------------------
LOG_BUNDLE_RAW = "Log bundle created: /tmp/fortisoar-logs-20260621.tar.gz\n"

# --- csadm certs --generate -------------------------------------------------
CERTS_GENERATE_RAW = "Certificate generated for soar.example.com\n"

# --- csadm ha ----------------------------------------------------------------
# `csadm ha list-nodes` ��� columnar: header, dash rule, then one row per node.
# The current node is marked with a leading '*'. Parsed by ha._parse_nodes via
# dash-column slicing (the comment cell can contain spaces, hence column slices).
# Single-node (standalone) box: the current node is the only member.
HA_LIST_NODES_RAW = (
    "nodeId                              nodeName    status    role     comment         mode         fsrVersion\n"
    "----------------------------------  ----------  --------  -------  --------------  -----------  ------------\n"
    "* 572b3ecd3ddbc133a650f3faecc7c286  fsr-1       active    primary  primary server  operational  7.6.2-5507\n"
)
# `csadm ha show-health` ��� labelled key/value lines plus Memory/Swap/Disk sections.
HA_SHOW_HEALTH_RAW = (
    "Node Name                     : fsr-1\n"
    "Node ID                       : 572b3ecd3ddbc133a650f3faecc7c286\n"
    "Uptime                        : 46 days, 0:58:21\n"
    "Mode                          : operational\n"
    "Services Status               : green\n"
    "Queued Workflow Count         : 0\n"
    "Memory Usage:\n"
    "------------------------------------------------\n"
    "total    used    avail      percent\n"
    "-------  ------  -------  ---------\n"
    "31.1G    14.2G   15.2G         51.2\n"
    "Swap Usage:\n"
    "total    used    free      percent\n"
    "-------  ------  ------  ---------\n"
    "0bytes   0bytes  0bytes          0\n"
    "Disk Usage:\n"
    "mountpoint    device     total    used    avail      percent\n"
    "------------  ---------  -------  ------  -------  ---------\n"
    "/             /dev/vda5  498.9G   41.6G   457.3G         8.3\n"
    "/boot         /dev/vda2  936.0M   451.0M  485.0M        48.2\n"
    "System load and CPU utilization:\n"
)
HA_REPLICATION_RAW = "Replication lag: 0 bytes\nStatus: streaming\n"

__all__ = [name for name in globals() if not name.startswith("_") and name.isupper()]
