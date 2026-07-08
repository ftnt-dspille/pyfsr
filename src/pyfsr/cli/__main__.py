"""``pyfsr`` CLI entry point (argparse, dep-free).

Today this hosts the ``appliance`` command group (P1: ``db`` + ``info``). The
console script is wired as ``pyfsr = "pyfsr.cli.__main__:main"``.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import _output
from . import playbook as playbook_cmds
from . import repo as repo_cmds
from . import widget as widget_cmds
from .appliance import certs as certs_cmds
from .appliance import db as db_cmds
from .appliance import diagnose as diagnose_cmds
from .appliance import es as es_cmds
from .appliance import ha as ha_cmds
from .appliance import host as host_cmds
from .appliance import info as info_cmds
from .appliance import license as license_cmds
from .appliance import logs as logs_cmds
from .appliance import mq as mq_cmds
from .appliance import service as service_cmds
from .appliance.facts import Facts
from .appliance.transport import Transport, TransportError, make_transport


def _add_connection_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("connection")
    g.add_argument("--host", help="appliance host (SSH); defaults to local if on-box")
    g.add_argument("--user", default="csadmin", help="SSH user (default: csadmin)")
    g.add_argument("--password", help="SSH/sudo password (or PYFSR_APPLIANCE_PASSWORD)")
    g.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    g.add_argument("--key", dest="key_path", help="SSH private key path")
    g.add_argument(
        "--insecure-skip-host-key-check",
        action="store_true",
        help="disable SSH host-key verification (MITM-exposed; only for churning lab boxes)",
    )


def _add_target_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("db target")
    g.add_argument("--role", help="DB role: content (default) | das | connectors | …")
    g.add_argument("--db", help="explicit DB name (overrides --role)")


def _make_transport(args: argparse.Namespace) -> Transport:
    return make_transport(
        host=args.host,
        user=args.user,
        password=args.password,
        port=args.port,
        key_path=args.key_path,
        insecure_skip_host_key_check=args.insecure_skip_host_key_check,
    )


def _make_facts(args: argparse.Namespace) -> Facts:
    return Facts(_make_transport(args))


def _emit_target(dbname: str, fmt: str) -> None:
    # Echo the resolved DB to stderr so it never pollutes --json/--csv on stdout.
    if fmt == "table":
        print(f"# target db: {dbname}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyfsr", description="pyfsr command-line tools")
    sub = parser.add_subparsers(dest="group", required=True)

    appliance = sub.add_parser("appliance", help="generic FortiSOAR appliance commands")
    asub = appliance.add_subparsers(dest="command", required=True)

    # --- info ---
    p_info = asub.add_parser("info", help="identity card: host, version, content DB, UUID")
    _add_connection_args(p_info)
    p_info.add_argument("--json", action="store_const", const="json", dest="fmt", default="table")
    p_info.set_defaults(func=cmd_info)

    # --- db group ---
    p_db = asub.add_parser("db", help="Postgres verbs (multi-DB aware)")
    dbsub = p_db.add_subparsers(dest="db_command", required=True)

    p_list = dbsub.add_parser("list", help="enumerate DBs with sizes and roles")
    _add_connection_args(p_list)
    _add_fmt(p_list)
    p_list.set_defaults(func=cmd_db_list)

    p_getsize = dbsub.add_parser("getsize", help="csadm db --getsize (footprint by data class)")
    _add_connection_args(p_getsize)
    _add_fmt(p_getsize)
    p_getsize.set_defaults(func=cmd_db_getsize)

    p_query = dbsub.add_parser("query", help="run a read-only SELECT")
    _add_connection_args(p_query)
    _add_target_args(p_query)
    _add_fmt(p_query)
    p_query.add_argument("sql", help="SQL SELECT to run")
    p_query.set_defaults(func=cmd_db_query)

    p_tables = dbsub.add_parser("tables", help="list tables (optional LIKE/glob pattern)")
    _add_connection_args(p_tables)
    _add_target_args(p_tables)
    _add_fmt(p_tables)
    p_tables.add_argument("pattern", nargs="?", help="name filter (glob * or SQL %%)")
    p_tables.set_defaults(func=cmd_db_tables)

    p_idx = dbsub.add_parser("indexes", help="list indexes (optional pattern)")
    _add_connection_args(p_idx)
    _add_target_args(p_idx)
    _add_fmt(p_idx)
    p_idx.add_argument("pattern", nargs="?", help="name filter (glob * or SQL %%)")
    p_idx.set_defaults(func=cmd_db_indexes)

    p_exec = dbsub.add_parser("exec", help="run repair SQL (gated by --write --yes)")
    _add_connection_args(p_exec)
    _add_target_args(p_exec)
    _add_fmt(p_exec)
    p_exec.add_argument("sql", help="mutating SQL statement")
    p_exec.add_argument("--write", action="store_true", help="acknowledge this mutates")
    p_exec.add_argument("--yes", action="store_true", help="skip confirmation")
    p_exec.set_defaults(func=cmd_db_exec)

    p_drop = dbsub.add_parser(
        "drop-module-tables",
        help="drop orphaned physical tables left by a deleted module",
    )
    _add_connection_args(p_drop)
    _add_fmt(p_drop)
    p_drop.add_argument("table", help="module base tableName")
    p_drop.add_argument("--yes", action="store_true", help="skip confirmation")
    p_drop.set_defaults(func=cmd_db_drop_module_tables)

    p_orphans = dbsub.add_parser(
        "orphans",
        help="sweep for physical tables left behind by deleted modules",
    )
    _add_connection_args(p_orphans)
    _add_fmt(p_orphans)
    p_orphans.add_argument("--drop", action="store_true", help="DROP each reported orphan family (gated by --yes)")
    p_orphans.add_argument("--yes", action="store_true", help="skip confirmation when --drop")
    p_orphans.set_defaults(func=cmd_db_orphans)

    # --- service group ---
    p_svc = asub.add_parser("service", help="systemd / cyops service verbs")
    svcsub = p_svc.add_subparsers(dest="svc_command", required=True)

    p_svc_status = svcsub.add_parser("status", help="csadm services --status (parsed)")
    _add_connection_args(p_svc_status)
    _add_fmt(p_svc_status)
    p_svc_status.add_argument("name", nargs="?", help="limit to one service")
    p_svc_status.add_argument("--raw", action="store_true", help="print raw csadm text instead of the parsed table")
    p_svc_status.set_defaults(func=cmd_service_status)

    p_svc_live = svcsub.add_parser("liveness", help="probe endpoints for active-but-wedged")
    _add_connection_args(p_svc_live)
    _add_fmt(p_svc_live)
    p_svc_live.set_defaults(func=cmd_service_liveness)

    p_svc_restart = svcsub.add_parser("restart", help="restart a cyops service (gated)")
    _add_connection_args(p_svc_restart)
    p_svc_restart.add_argument("name", help="service to restart")
    p_svc_restart.add_argument("--yes", action="store_true", help="skip confirmation")
    p_svc_restart.set_defaults(func=cmd_service_restart)

    p_svc_listen = svcsub.add_parser("listeners", help="listening ports + owning process")
    _add_connection_args(p_svc_listen)
    _add_fmt(p_svc_listen)
    p_svc_listen.set_defaults(func=cmd_service_listeners)

    p_svc_stop = svcsub.add_parser("stop", help="stop a cyops service (gated)")
    _add_connection_args(p_svc_stop)
    p_svc_stop.add_argument("name", help="service to stop")
    p_svc_stop.add_argument("--yes", action="store_true", help="skip confirmation")
    p_svc_stop.set_defaults(func=cmd_service_stop)

    p_svc_start = svcsub.add_parser("start", help="start a cyops service")
    _add_connection_args(p_svc_start)
    p_svc_start.add_argument("name", help="service to start")
    p_svc_start.set_defaults(func=cmd_service_start)

    p_svc_restart_all = svcsub.add_parser("restart-all", help="restart the WHOLE service stack in order (gated)")
    _add_connection_args(p_svc_restart_all)
    p_svc_restart_all.add_argument("--yes", action="store_true", help="skip confirmation")
    p_svc_restart_all.set_defaults(func=cmd_service_restart_all)

    p_svc_stop_all = svcsub.add_parser("stop-all", help="stop the WHOLE service stack in order (gated)")
    _add_connection_args(p_svc_stop_all)
    p_svc_stop_all.add_argument("--yes", action="store_true", help="skip confirmation")
    p_svc_stop_all.set_defaults(func=cmd_service_stop_all)

    p_svc_start_all = svcsub.add_parser("start-all", help="start the WHOLE service stack in order")
    _add_connection_args(p_svc_start_all)
    p_svc_start_all.set_defaults(func=cmd_service_start_all)

    p_svc_ctl = svcsub.add_parser("systemctl", help="drive systemd directly (stop/kill/restart/status; gated)")
    _add_connection_args(p_svc_ctl)
    p_svc_ctl.add_argument("action", help="systemctl action: stop, kill, restart, start, status, is-active, …")
    p_svc_ctl.add_argument("unit", help="systemd unit name (e.g. celeryd.service)")
    p_svc_ctl.add_argument("--signal", help="signal for `kill` (e.g. SIGKILL, 9); default SIGTERM")
    p_svc_ctl.add_argument("--yes", action="store_true", help="confirm a mutating action")
    p_svc_ctl.set_defaults(func=cmd_service_systemctl)

    # --- mq group ---
    p_mq = asub.add_parser("mq", help="RabbitMQ verbs (rabbitmqctl)")
    mqsub = p_mq.add_subparsers(dest="mq_command", required=True)
    for verb, helptext in [
        ("status", "rabbitmqctl status"),
        ("queues", "queues with depth/consumers (flags backlog + zero-consumer)"),
        ("consumers", "list consumers"),
        ("vhosts", "list virtual hosts"),
        ("permissions", "per-vhost permissions"),
    ]:
        sp = mqsub.add_parser(verb, help=helptext)
        _add_connection_args(sp)
        if verb != "status":
            _add_fmt(sp)
        if verb == "permissions":
            sp.add_argument(
                "--all-vhosts",
                action="store_true",
                help="show the permission matrix across every vhost, not just '/'",
            )
        sp.set_defaults(func=_MQ_HANDLERS[verb])

    p_mq_purge = mqsub.add_parser("purge", help="purge all messages from a queue (irreversible, gated)")
    _add_connection_args(p_mq_purge)
    p_mq_purge.add_argument("queue", help="queue name to purge")
    p_mq_purge.add_argument("-p", "--vhost", help="virtual host (default '/')")
    p_mq_purge.add_argument("--yes", action="store_true", help="confirm the purge")
    p_mq_purge.set_defaults(func=cmd_mq_purge)

    p_mq_pw = mqsub.add_parser(
        "purge-workflows",
        help="release a stuck-worker backlog: purge queued workflows + recycle celeryd (SIGKILL by default; gated)",
    )
    _add_connection_args(p_mq_pw)
    p_mq_pw.add_argument("--yes", action="store_true", help="confirm (discards queued tasks)")
    p_mq_pw.add_argument(
        "--graceful", action="store_true", help="csadm warm-stop celeryd instead of SIGKILL (slower; lets tasks finish)"
    )
    p_mq_pw.add_argument(
        "--no-sweep", action="store_true", help="purge only fsr-cluster/celery, not the intra-cyops data queues"
    )
    p_mq_pw.set_defaults(func=cmd_mq_purge_workflows)

    # --- host group ---
    p_host = asub.add_parser("host", help="OS resource metrics (mem / swap / load / RSS / disk)")
    hostsub = p_host.add_subparsers(dest="host_command", required=True)

    p_host_snap = hostsub.add_parser("snapshot", help="one coherent sample: mem, swap, load, worker RSS, disk")
    _add_connection_args(p_host_snap)
    _add_fmt(p_host_snap)
    p_host_snap.add_argument("--disk-path", help="also report this filesystem's usage (e.g. /opt/cyops)")
    p_host_snap.set_defaults(func=cmd_host_snapshot)

    p_host_mem = hostsub.add_parser("mem", help="memory + swap usage (MB)")
    _add_connection_args(p_host_mem)
    _add_fmt(p_host_mem)
    p_host_mem.set_defaults(func=cmd_host_mem)

    p_host_proc = hostsub.add_parser("rss", help="summed/peak RSS for processes matching a regex")
    _add_connection_args(p_host_proc)
    _add_fmt(p_host_proc)
    p_host_proc.add_argument("pattern", help="regex matched against the process command line")
    p_host_proc.set_defaults(func=cmd_host_rss)

    # --- license group ---
    p_lic = asub.add_parser("license", help="licensing / identity (device UUID, drift)")
    licsub = p_lic.add_subparsers(dest="license_command", required=True)

    p_lic_show = licsub.add_parser("show", help="csadm license --show-details (parsed)")
    _add_connection_args(p_lic_show)
    _add_fmt(p_lic_show)
    p_lic_show.add_argument("--raw", action="store_true", help="print raw csadm text instead of the parsed card")
    p_lic_show.set_defaults(func=cmd_license_show)

    p_lic_uuid = licsub.add_parser("device-uuid", help="resolved device UUID (file first, csadm fallback)")
    _add_connection_args(p_lic_uuid)
    p_lic_uuid.set_defaults(func=cmd_license_device_uuid)

    p_lic_drift = licsub.add_parser("drift", help="file vs csadm entitlement UUID drift (exit 1 if drifted)")
    _add_connection_args(p_lic_drift)
    _add_fmt(p_lic_drift)
    p_lic_drift.set_defaults(func=cmd_license_drift)

    # --- logs group ---
    p_logs = asub.add_parser("logs", help="log tail / error scan")
    logssub = p_logs.add_subparsers(dest="logs_command", required=True)

    p_logs_tail = logssub.add_parser("tail", help="tail a cyops service log")
    _add_connection_args(p_logs_tail)
    p_logs_tail.add_argument("service", help=f"service alias ({', '.join(logs_cmds.LOG_PATHS)}) or path")
    p_logs_tail.add_argument("-n", "--lines", type=int, default=100, help="lines (default 100)")
    p_logs_tail.set_defaults(func=cmd_logs_tail)

    p_logs_scan = logssub.add_parser("scan", help="roll up recent journal errors")
    _add_connection_args(p_logs_scan)
    p_logs_scan.add_argument("--minutes", type=int, default=30, help="window (default 30)")
    p_logs_scan.set_defaults(func=cmd_logs_scan)

    p_logs_bundle = logssub.add_parser("bundle", help="csadm log --collect → tarball path")
    _add_connection_args(p_logs_bundle)
    p_logs_bundle.set_defaults(func=cmd_logs_bundle)

    # --- es group ---
    p_es = asub.add_parser("es", help="Elasticsearch health / shard verbs")
    essub = p_es.add_subparsers(dest="es_command", required=True)

    p_es_health = essub.add_parser("health", help="cluster health (green/yellow/red + shard counts)")
    _add_connection_args(p_es_health)
    _add_fmt(p_es_health)
    p_es_health.set_defaults(func=cmd_es_health)

    p_es_shards = essub.add_parser("shards", help="unassigned-shard allocation explain")
    _add_connection_args(p_es_shards)
    _add_fmt(p_es_shards)
    p_es_shards.set_defaults(func=cmd_es_shards)

    # --- ha group ---
    p_ha = asub.add_parser("ha", help="HA cluster verbs (csadm ha)")
    hasub = p_ha.add_subparsers(dest="ha_command", required=True)

    p_ha_nodes = hasub.add_parser("nodes", help="csadm ha list-nodes (parsed)")
    _add_connection_args(p_ha_nodes)
    _add_fmt(p_ha_nodes)
    p_ha_nodes.set_defaults(func=cmd_ha_nodes)

    p_ha_health = hasub.add_parser("health", help="csadm ha show-health (parsed)")
    _add_connection_args(p_ha_health)
    _add_fmt(p_ha_health)
    p_ha_health.set_defaults(func=cmd_ha_health)

    p_ha_replication = hasub.add_parser("replication", help="csadm ha get-replication-stat")
    _add_connection_args(p_ha_replication)
    p_ha_replication.set_defaults(func=cmd_ha_replication)

    # --- certs group ---
    p_certs = asub.add_parser("certs", help="appliance TLS certificate verbs (csadm certs)")
    certssub = p_certs.add_subparsers(dest="certs_command", required=True)

    p_certs_regen = certssub.add_parser(
        "regenerate",
        help="regenerate the self-signed cert (csadm certs --generate <hostname>); gated by --yes",
    )
    _add_connection_args(p_certs_regen)
    p_certs_regen.add_argument("hostname", help="FQDN to issue the cert for (the cert CN)")
    p_certs_regen.add_argument("--yes", action="store_true", help="skip confirmation")
    p_certs_regen.set_defaults(func=cmd_certs_regenerate)

    # --- diagnose ---
    p_diag = asub.add_parser("diagnose", help="run fsr_diagnose.sh on the appliance")
    _add_connection_args(p_diag)
    p_diag.add_argument(
        "--script",
        default="/opt/cyops/scripts/fsr_diagnose.sh",
        help="path to fsr_diagnose.sh on the appliance",
    )
    p_diag.set_defaults(func=cmd_diagnose)

    # --- playbook group (top-level; API-based, distinct from the SSH appliance group) ---
    p_pb = sub.add_parser(
        "playbook",
        help="author playbooks in YAML and deploy via the API",
        description=(
            "Author, validate, and deploy FortiSOAR playbooks from friendly YAML.\n\n"
            "Start here when authoring:\n"
            "  steps           list every step type you can write\n"
            "  step-help TYPE  keys + a real compiling example for one step type\n"
            "  examples        list the foundational playbook library (worked examples\n"
            "                  to adapt; --intent / --stage / --manifest)\n"
            "  show SLUG       print one library playbook's metadata + full YAML\n"
            "  compile FILE    YAML -> FSR import envelope (offline, no network)\n"
            "  validate FILE   compile and report diagnostics (offline)\n"
            "  lint FILE       live preflight: connector steps missing config\n"
            "  deploy FILE     compile + create on the appliance\n"
            "  check-fresh     compare the cached compile catalog vs a live SOAR\n\n"
            "Runtime helpers (Python SDK): client.manual_input.answer() drives a\n"
            "paused Manual Input in one call; see guides/playbook-authoring.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pbsub = p_pb.add_subparsers(dest="pb_command", required=True)
    playbook_cmds.build_subparser(pbsub)

    # --- repo group (top-level; public content repo, no appliance) ---
    p_repo = sub.add_parser(
        "repo",
        help="discover + download from Fortinet's content repo (no appliance)",
    )
    repo_sub = p_repo.add_subparsers(dest="repo_command", required=True)
    repo_cmds.build_subparser(repo_sub)

    # --- widget group (top-level; upload + publish on a live appliance) ---
    p_widget = sub.add_parser("widget", help="upload + publish widgets on a live appliance")
    widget_sub = p_widget.add_subparsers(dest="widget_command", required=True)
    widget_cmds.build_subparser(widget_sub)

    # --- records group (top-level; API-based record CRUD) ---
    p_rec = sub.add_parser("records", help="query and manage FortiSOAR records (alerts, incidents, etc.)")
    recsub = p_rec.add_subparsers(dest="records_command", required=True)

    # alerts list
    p_alerts_list = recsub.add_parser("alerts", help="list alerts with optional filters")
    playbook_cmds.add_connection_args(p_alerts_list)
    _add_fmt(p_alerts_list)
    p_alerts_list.add_argument("--limit", type=int, default=50, help="max results (default 50)")
    p_alerts_list.add_argument("--status", help="filter by status (e.g. Open, Closed)")
    p_alerts_list.add_argument("--severity", help="filter by severity (e.g. Critical, High)")
    p_alerts_list.set_defaults(func=cmd_records_alerts_list)

    # incidents query
    p_incidents_query = recsub.add_parser("incidents", help="query incidents via DSL")
    playbook_cmds.add_connection_args(p_incidents_query)
    _add_fmt(p_incidents_query)
    p_incidents_query.add_argument("query", help="Query DSL JSON or simplified filter (field=value)")
    p_incidents_query.set_defaults(func=cmd_records_incidents_query)

    # records delete
    p_records_delete = recsub.add_parser("delete", help="delete records by module and id")
    playbook_cmds.add_connection_args(p_records_delete)
    p_records_delete.add_argument("module", help="module name (e.g. alerts, incidents)")
    p_records_delete.add_argument("id", nargs="+", help="one or more record UUIDs to delete")
    p_records_delete.add_argument("--yes", action="store_true", help="skip confirmation")
    p_records_delete.set_defaults(func=cmd_records_delete)

    # --- mcp group (top-level; the appliance's own native /mcp/* gateway) ---
    p_mcp = sub.add_parser(
        "mcp",
        help="call FortiSOAR's own native MCP tool gateway (client.mcp) — "
        "not client.ai's external-server registration, see pyfsr.api.native_mcp",
    )
    mcpsub = p_mcp.add_subparsers(dest="mcp_command", required=True)

    p_mcp_list = mcpsub.add_parser("list-tools", help="list the tools a native MCP server advertises")
    playbook_cmds.add_connection_args(p_mcp_list)
    _add_fmt(p_mcp_list)
    p_mcp_list.add_argument(
        "--mcp-server",
        dest="mcp_server",
        default="soc",
        help="'modules' / 'playbooks' / 'soc' / 'utility' / 'connector:<name>' (default: soc)",
    )
    p_mcp_list.set_defaults(func=cmd_mcp_list_tools)

    p_mcp_call = mcpsub.add_parser("call", help="call one tool on a native MCP server")
    playbook_cmds.add_connection_args(p_mcp_call)
    p_mcp_call.add_argument(
        "--mcp-server",
        dest="mcp_server",
        default="soc",
        help="'modules' / 'playbooks' / 'soc' / 'utility' / 'connector:<name>' (default: soc)",
    )
    p_mcp_call.add_argument("tool", help="tool name, e.g. get_alert")
    p_mcp_call.add_argument(
        "--args",
        default="{}",
        help='tool arguments as a JSON object, e.g. \'{"uuid": ["<alert-uuid>"]}\'',
    )
    p_mcp_call.set_defaults(func=cmd_mcp_call)

    # Global HTTP trace flag (applies to all top-level commands)
    parser.add_argument(
        "--log-requests",
        action="store_true",
        help="log outgoing HTTP request bodies",
    )
    parser.add_argument(
        "--log-responses",
        action="store_true",
        help="log incoming HTTP response bodies",
    )

    return parser


def _add_fmt(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_const", const="json", dest="fmt", default="table")
    g.add_argument("--csv", action="store_const", const="csv", dest="fmt")


# --- command handlers ----------------------------------------------------
def cmd_info(args: argparse.Namespace) -> int:
    facts = _make_facts(args)
    _output.kv(info_cmds.identity(facts), fmt=args.fmt)
    return 0


def cmd_db_list(args: argparse.Namespace) -> int:
    dbs = db_cmds.list_databases(_make_facts(args))
    rows = [[d.name, d.size, d.role] for d in dbs]
    _output.render(rows, ["database", "size", "role"], fmt=args.fmt)
    return 0


def cmd_db_getsize(args: argparse.Namespace) -> int:
    sizes = db_cmds.getsize(_make_facts(args))
    rows = [[s.data_class, s.size, s.size_mb] for s in sizes]
    _output.render(rows, ["data_class", "size", "size_mb"], fmt=args.fmt)
    return 0


def cmd_db_query(args: argparse.Namespace) -> int:
    facts = _make_facts(args)
    dbname, headers, rows = db_cmds.query(facts, args.sql, role=args.role, db=args.db)
    _emit_target(dbname, args.fmt)
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_db_tables(args: argparse.Namespace) -> int:
    facts = _make_facts(args)
    dbname, headers, rows = db_cmds.tables(facts, args.pattern, role=args.role, db=args.db)
    _emit_target(dbname, args.fmt)
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_db_indexes(args: argparse.Namespace) -> int:
    facts = _make_facts(args)
    dbname, headers, rows = db_cmds.indexes(facts, args.pattern, role=args.role, db=args.db)
    _emit_target(dbname, args.fmt)
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_db_exec(args: argparse.Namespace) -> int:
    if not args.write:
        print("error: `db exec` mutates — pass --write to acknowledge", file=sys.stderr)
        return 2
    facts = _make_facts(args)
    target = facts.resolve_db(role=args.role, db=args.db)
    print(f"# plan: execute against {target!r}:\n  {args.sql}", file=sys.stderr)
    dbname, status = db_cmds.exec_write(facts, args.sql, role=args.role, db=args.db, yes=args.yes)
    print(f"{dbname}: {status}")
    return 0


def cmd_db_drop_module_tables(args: argparse.Namespace) -> int:
    facts = _make_facts(args)
    planned = db_cmds.find_module_tables(facts, args.table)
    print(
        f"# plan: DROP TABLE CASCADE in {facts.content_db()!r}: {', '.join(planned) or '(none found)'}",
        file=sys.stderr,
    )
    result = db_cmds.drop_module_tables(facts, args.table, yes=args.yes)
    _output.kv(
        {"db": result["db"], "dropped": ", ".join(result["dropped"]) or "(none)"},
        fmt=args.fmt,
    )
    return 0


def cmd_db_orphans(args: argparse.Namespace) -> int:
    facts = _make_facts(args)
    orphans = db_cmds.find_orphan_module_tables(facts)
    _emit_target(facts.content_db(), args.fmt)
    rows = [[o.base, o.table, o.kind] for o in orphans]
    _output.render(rows, ["base", "table", "kind"], fmt=args.fmt)
    if not orphans:
        return 0
    if not args.drop:
        bases = sorted({o.base for o in orphans})
        print(
            f"# {len(orphans)} orphan table(s) across {len(bases)} module(s): "
            f"{', '.join(bases)}. Reclaim with `db orphans --drop --yes`.",
            file=sys.stderr,
        )
        return 1  # non-zero so it's usable as a hygiene gate
    for base in sorted({o.base for o in orphans}):
        print(f"# plan: DROP TABLE CASCADE for module {base!r}", file=sys.stderr)
        result = db_cmds.drop_module_tables(facts, base, yes=args.yes)
        print(f"  {result['db']}: dropped {', '.join(result['dropped']) or '(none)'}")
    return 0


# --- service handlers ----------------------------------------------------
def cmd_service_status(args: argparse.Namespace) -> int:
    t = _make_transport(args)
    if args.raw:
        print(service_cmds.status(t, args.name))
        return 0
    states = service_cmds.services(t, args.name)
    rows = [[s.name, "up" if s.running else "DOWN", s.status, s.since or ""] for s in states]
    _output.render(rows, ["service", "up", "status", "since"], fmt=args.fmt)
    # Non-zero exit if any service is not running, so it's usable as a health gate.
    return 0 if all(s.running for s in states) else 1


def cmd_service_liveness(args: argparse.Namespace) -> int:
    probes = service_cmds.liveness(_make_transport(args))
    rows = [[p.label, f"{p.method} {p.path}", p.code, p.verdict] for p in probes]
    _output.render(rows, ["service", "endpoint", "code", "verdict"], fmt=args.fmt)
    # Non-zero exit if anything is wedged, so it's usable as a health gate.
    return 1 if any(p.code == 0 for p in probes) else 0


def cmd_service_restart(args: argparse.Namespace) -> int:
    r = service_cmds.restart(_make_transport(args), args.name, yes=args.yes)
    print(str(r))
    return 0 if r.ok else 1


def cmd_service_listeners(args: argparse.Namespace) -> int:
    rows = [[lis.local_address, lis.process] for lis in service_cmds.listeners(_make_transport(args))]
    _output.render(rows, ["local_address", "process"], fmt=args.fmt)
    return 0


def cmd_service_stop(args: argparse.Namespace) -> int:
    r = service_cmds.stop(_make_transport(args), args.name, yes=args.yes)
    print(str(r))
    return 0 if r.ok else 1


def cmd_service_start(args: argparse.Namespace) -> int:
    r = service_cmds.start(_make_transport(args), args.name)
    print(str(r))
    return 0 if r.ok else 1


def cmd_service_restart_all(args: argparse.Namespace) -> int:
    r = service_cmds.restart_all(_make_transport(args), yes=args.yes)
    print(str(r))
    return 0 if r.ok else 1


def cmd_service_stop_all(args: argparse.Namespace) -> int:
    r = service_cmds.stop_all(_make_transport(args), yes=args.yes)
    print(str(r))
    return 0 if r.ok else 1


def cmd_service_start_all(args: argparse.Namespace) -> int:
    r = service_cmds.start_all(_make_transport(args))
    print(str(r))
    return 0 if r.ok else 1


def cmd_service_systemctl(args: argparse.Namespace) -> int:
    r = service_cmds.systemctl(_make_transport(args), args.action, args.unit, signal=args.signal, yes=args.yes)
    # For read-only actions (is-active/show) print the queried value, else the outcome.
    print(r.output if r.output else str(r))
    return 0 if r.ok else 1


# --- mq handlers ---------------------------------------------------------
def cmd_mq_status(args: argparse.Namespace) -> int:
    print(mq_cmds.status(_make_transport(args)))
    return 0


def cmd_mq_queues(args: argparse.Namespace) -> int:
    qs = mq_cmds.queues(_make_transport(args))
    rows = [[q.name, q.messages, q.consumers, q.flag] for q in qs]
    _output.render(rows, ["queue", "messages", "consumers", "flag"], fmt=args.fmt)
    return 0


def cmd_mq_consumers(args: argparse.Namespace) -> int:
    rows = [[c.queue, c.channel] for c in mq_cmds.consumers(_make_transport(args))]
    _output.render(rows, ["queue", "channel"], fmt=args.fmt)
    return 0


def cmd_mq_vhosts(args: argparse.Namespace) -> int:
    rows = [[v] for v in mq_cmds.vhosts(_make_transport(args))]
    _output.render(rows, ["vhost"], fmt=args.fmt)
    return 0


def cmd_mq_permissions(args: argparse.Namespace) -> int:
    perms = mq_cmds.permissions(_make_transport(args), all_vhosts=args.all_vhosts)
    rows = [[p.vhost, p.user, p.configure, p.write, p.read] for p in perms]
    _output.render(rows, ["vhost", "user", "configure", "write", "read"], fmt=args.fmt)
    return 0


def cmd_mq_purge(args: argparse.Namespace) -> int:
    result = mq_cmds.purge_queue(_make_transport(args), args.queue, vhost=args.vhost, yes=args.yes)
    print(str(result))
    return 0


def cmd_mq_purge_workflows(args: argparse.Namespace) -> int:
    report = mq_cmds.purge_workflows(
        _make_transport(args), yes=args.yes, graceful=args.graceful, sweep_data_queues=not args.no_sweep
    )
    for step in report.steps:
        print(str(step))
    for purge in report.purges:
        print(str(purge))
    print(f"total purged: {report.total_purged}")
    return 0 if report.ok else 1


# --- host handlers -------------------------------------------------------
def cmd_host_snapshot(args: argparse.Namespace) -> int:
    snap = host_cmds.snapshot(_make_transport(args), disk_path=args.disk_path)
    rows = {
        "mem_used_mb": snap.mem.used_mb,
        "mem_total_mb": snap.mem.total_mb,
        "swap_used_mb": snap.mem.swap_used_mb,
        "swap_total_mb": snap.mem.swap_total_mb,
        "load1": snap.load.load1,
    }
    for name, p in snap.procs.items():
        rows[f"{name}_rss_mb"] = p.sum_mb
        rows[f"{name}_workers"] = p.count
        rows[f"{name}_peak_mb"] = p.peak_mb
    if snap.disk:
        rows[f"disk_{snap.disk.path}_use_pct"] = snap.disk.use_pct
    _output.kv(rows, fmt=args.fmt)
    return 0


def cmd_host_mem(args: argparse.Namespace) -> int:
    m = host_cmds.meminfo(_make_transport(args))
    _output.kv(vars(m), fmt=args.fmt)
    return 0


def cmd_host_rss(args: argparse.Namespace) -> int:
    p = host_cmds.process_rss(_make_transport(args), args.pattern)
    _output.kv(vars(p), fmt=args.fmt)
    return 0


_MQ_HANDLERS = {
    "status": cmd_mq_status,
    "queues": cmd_mq_queues,
    "consumers": cmd_mq_consumers,
    "vhosts": cmd_mq_vhosts,
    "permissions": cmd_mq_permissions,
}


# --- license handlers ----------------------------------------------------
def cmd_license_show(args: argparse.Namespace) -> int:
    t = _make_transport(args)
    if args.raw:
        print(license_cmds.show(t))
        return 0
    d = license_cmds.details(t)
    _output.kv(
        {
            "type": d.type,
            "edition": d.edition,
            "role": d.role,
            "total_users": d.total_users,
            "expiry_date": d.expiry_date,
            "remaining_days": d.remaining_days,
            "serial_no": d.serial_no,
            "device_uuid": d.device_uuid,
        },
        fmt=args.fmt,
    )
    return 0


def cmd_license_device_uuid(args: argparse.Namespace) -> int:
    print(license_cmds.device_uuid(_make_transport(args)))
    return 0


def cmd_license_drift(args: argparse.Namespace) -> int:
    report = license_cmds.drift(_make_transport(args))
    _output.kv(
        {
            "file_uuid": report.file_uuid or "(none)",
            "csadm_uuid": report.csadm_uuid or "(none)",
            "drifted": report.drifted,
            "verdict": report.verdict,
        },
        fmt=args.fmt,
    )
    # Non-zero exit when drifted, so it's usable as a health gate.
    return 1 if report.drifted else 0


# --- logs handlers -------------------------------------------------------
def cmd_logs_tail(args: argparse.Namespace) -> int:
    print(logs_cmds.tail(_make_transport(args), args.service, lines=args.lines), end="")
    return 0


def cmd_logs_scan(args: argparse.Namespace) -> int:
    print(logs_cmds.scan(_make_transport(args), minutes=args.minutes))
    return 0


def cmd_logs_bundle(args: argparse.Namespace) -> int:
    path = logs_cmds.bundle(_make_transport(args))
    print(path)
    return 0


# --- es handlers ---------------------------------------------------------
def cmd_es_health(args: argparse.Namespace) -> int:
    h = es_cmds.health(_make_facts(args))
    _output.kv(
        {
            "status": h.status,
            "cluster": h.cluster_name,
            "nodes": h.num_nodes,
            "data_nodes": h.num_data_nodes,
            "active_shards": h.active_shards,
            "unassigned_shards": h.unassigned_shards,
        },
        fmt=args.fmt,
    )
    return 1 if h.status == "red" else 0


def cmd_es_shards(args: argparse.Namespace) -> int:
    headers, rows = es_cmds.shards(_make_facts(args))
    _output.render(rows, headers, fmt=args.fmt)
    return 0


# --- ha handlers ---------------------------------------------------------
def cmd_ha_nodes(args: argparse.Namespace) -> int:
    nodes = ha_cmds.nodes(_make_transport(args))
    rows = [["*" if n.is_current else "", n.name, n.node_id, n.status, n.role, n.mode, n.fsr_version] for n in nodes]
    _output.render(rows, ["cur", "name", "node_id", "status", "role", "mode", "fsr_version"], fmt=args.fmt)
    return 0


def cmd_ha_health(args: argparse.Namespace) -> int:
    h = ha_cmds.health(_make_transport(args))
    card = {
        "node_name": h.node_name,
        "node_id": h.node_id,
        "mode": h.mode,
        "services_status": h.services_status,
        "queued_workflows": h.queued_workflows,
        "uptime": h.uptime,
    }
    if h.memory:
        card["memory"] = f"{h.memory.used}/{h.memory.total} ({h.memory.percent}%)"
    if h.swap:
        card["swap"] = f"{h.swap.used}/{h.swap.total} ({h.swap.percent}%)"
    for d in h.disks:
        card[f"disk {d.mountpoint}"] = f"{d.used}/{d.total} ({d.percent}%)"
    _output.kv(card, fmt=args.fmt)
    return 0


def cmd_ha_replication(args: argparse.Namespace) -> int:
    print(ha_cmds.replication(_make_transport(args)))
    return 0


# --- certs handler -------------------------------------------------------
def cmd_certs_regenerate(args: argparse.Namespace) -> int:
    print(
        f"# plan: csadm certs --generate {args.hostname} (replaces the cert; restart services afterwards)",
        file=sys.stderr,
    )
    print(certs_cmds.regenerate(_make_transport(args), args.hostname, yes=args.yes))
    return 0


# --- diagnose handler ----------------------------------------------------
def cmd_diagnose(args: argparse.Namespace) -> int:
    print(diagnose_cmds.run(_make_transport(args), path=args.script), end="")
    return 0


# --- records handlers (API-based) ----------------------------------------
def cmd_records_alerts_list(args: argparse.Namespace) -> int:
    """List alerts with optional filtering."""
    client = playbook_cmds._make_client(args)
    client.http_trace = getattr(args, "log_requests", False) or getattr(args, "log_responses", False)

    # Build query filters
    from ..query import Query

    q = Query(module="alerts")
    if hasattr(args, "status") and args.status:
        q.eq("status.itemValue", args.status)
    if hasattr(args, "severity") and args.severity:
        q.eq("severity.itemValue", args.severity)
    q.limit(args.limit)

    # Execute query
    page = client.records("alerts").query(q)
    records = page.members

    # Render as table or JSON
    if args.fmt == "json":
        json.dump([dict(r) if hasattr(r, "__iter__") else r for r in records], sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        if not records:
            print("(no alerts)", file=sys.stderr)
            return 0
        # Extract common fields for display
        headers = ["uuid", "name", "status", "severity", "created"]
        rows = []
        for rec in records:
            row = [
                rec.get("uuid", ""),
                rec.get("name", "")[:50],
                rec.get("status", {}).get("itemValue", "") if isinstance(rec.get("status"), dict) else "",
                rec.get("severity", {}).get("itemValue", "") if isinstance(rec.get("severity"), dict) else "",
                str(rec.get("createDate", ""))[:19],
            ]
            rows.append(row)
        _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_records_incidents_query(args: argparse.Namespace) -> int:
    """Query incidents via DSL or simple filter."""
    client = playbook_cmds._make_client(args)
    client.http_trace = getattr(args, "log_requests", False) or getattr(args, "log_responses", False)

    from ..query import Query

    # Try to parse as JSON first; fall back to simple filter syntax
    try:
        query_dict = json.loads(args.query)
        # If it's a dict with query DSL keys, use it directly
        if any(k in query_dict for k in ["filters", "logic", "sort"]):
            from ..query import Query as QueryCls

            q = QueryCls(module="incidents")
            # Apply the raw DSL body
            for key in ["filters", "logic", "sort", "limit"]:
                if key in query_dict:
                    setattr(q, f"_{key}", query_dict[key])
            page = client.records("incidents").query(q)
        else:
            # Treat as a dict of field=value filters
            q = Query(module="incidents")
            for field, value in query_dict.items():
                q.eq(field, value)
            page = client.records("incidents").query(q)
    except (json.JSONDecodeError, ValueError):
        # Fall back to simple "field=value" syntax
        if "=" in args.query:
            parts = args.query.split("=", 1)
            q = Query(module="incidents")
            q.eq(parts[0].strip(), parts[1].strip())
            page = client.records("incidents").query(q)
        else:
            # Treat as a search term in name
            q = Query(module="incidents")
            q.contains("name", args.query)
            page = client.records("incidents").query(q)

    records = page.members

    # Render
    if args.fmt == "json":
        json.dump([dict(r) if hasattr(r, "__iter__") else r for r in records], sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        if not records:
            print("(no incidents)", file=sys.stderr)
            return 0
        headers = ["uuid", "name", "status", "severity"]
        rows = []
        for rec in records:
            row = [
                rec.get("uuid", ""),
                rec.get("name", "")[:50],
                rec.get("status", {}).get("itemValue", "") if isinstance(rec.get("status"), dict) else "",
                rec.get("severity", {}).get("itemValue", "") if isinstance(rec.get("severity"), dict) else "",
            ]
            rows.append(row)
        _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_records_delete(args: argparse.Namespace) -> int:
    """Delete records by module and id."""
    client = playbook_cmds._make_client(args)
    client.http_trace = getattr(args, "log_requests", False) or getattr(args, "log_responses", False)

    module = args.module
    ids = args.id

    # Confirmation gate
    if not args.yes:
        print(f"# plan: delete {len(ids)} record(s) from {module!r}", file=sys.stderr)
        response = input("Confirm [y/N]: ")
        if response.lower() != "y":
            print("cancelled")
            return 1

    # Execute deletes
    rec_set = client.records(module)
    deleted = []
    failed = []

    for rec_id in ids:
        try:
            rec_set.delete(rec_id)
            deleted.append(rec_id)
        except Exception as e:
            failed.append((rec_id, str(e)))

    # Report
    print(f"deleted: {len(deleted)}/{len(ids)}")
    if deleted:
        for rec_id in deleted:
            print(f"  {rec_id}")
    if failed:
        print(f"failed: {len(failed)}", file=sys.stderr)
        for rec_id, err in failed:
            print(f"  {rec_id}: {err}", file=sys.stderr)
        return 1
    return 0


def cmd_mcp_list_tools(args: argparse.Namespace) -> int:
    """List the tools one of FortiSOAR's native MCP servers advertises."""
    client = playbook_cmds._make_client(args)
    tools = client.mcp.list_tools(args.mcp_server)
    if args.fmt == "json":
        print(json.dumps(tools, indent=2, default=str))
    else:
        rows = [[t["name"], (t.get("description") or "").splitlines()[0][:80]] for t in tools]
        _output.render(rows, ["name", "description"], fmt=args.fmt)
    return 0


def cmd_mcp_call(args: argparse.Namespace) -> int:
    """Call one tool on a native MCP server and print its result as JSON."""
    client = playbook_cmds._make_client(args)
    try:
        arguments = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(f"--args must be a JSON object: {exc}", file=sys.stderr)
        return 1
    if not isinstance(arguments, dict):
        print('--args must be a JSON object (e.g. \'{"uuid": ["..."]}\')', file=sys.stderr)
        return 1
    result = client.mcp.call_tool(args.mcp_server, args.tool, arguments)
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (TransportError, ValueError, PermissionError, ImportError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
