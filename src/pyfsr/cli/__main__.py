"""``pyfsr`` CLI entry point (argparse, dep-free).

Today this hosts the ``appliance`` command group (P1: ``db`` + ``info``). The
console script is wired as ``pyfsr = "pyfsr.cli.__main__:main"``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from . import _output
from . import playbook as playbook_cmds
from .appliance import db as db_cmds
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

    # --- service group ---
    p_svc = asub.add_parser("service", help="systemd / cyops service verbs")
    svcsub = p_svc.add_subparsers(dest="svc_command", required=True)

    p_svc_status = svcsub.add_parser("status", help="csadm services --status")
    _add_connection_args(p_svc_status)
    p_svc_status.add_argument("name", nargs="?", help="limit to one service")
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
        sp.set_defaults(func=_MQ_HANDLERS[verb])

    # --- license group ---
    p_lic = asub.add_parser("license", help="licensing / identity (device UUID, drift)")
    licsub = p_lic.add_subparsers(dest="license_command", required=True)

    p_lic_show = licsub.add_parser("show", help="csadm license --show-details")
    _add_connection_args(p_lic_show)
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

    # --- playbook group (top-level; API-based, distinct from the SSH appliance group) ---
    p_pb = sub.add_parser("playbook", help="author playbooks in YAML and deploy via the API")
    pbsub = p_pb.add_subparsers(dest="pb_command", required=True)
    playbook_cmds.build_subparser(pbsub)

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
    facts = _make_facts(args)
    headers, rows = db_cmds.list_databases(facts)
    _output.render(rows, headers, fmt=args.fmt)
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


# --- service handlers ----------------------------------------------------
def cmd_service_status(args: argparse.Namespace) -> int:
    print(service_cmds.status(_make_transport(args), args.name))
    return 0


def cmd_service_liveness(args: argparse.Namespace) -> int:
    probes = service_cmds.liveness(_make_transport(args))
    rows = [[p.label, f"{p.method} {p.path}", p.code, p.verdict] for p in probes]
    _output.render(rows, ["service", "endpoint", "code", "verdict"], fmt=args.fmt)
    # Non-zero exit if anything is wedged, so it's usable as a health gate.
    return 1 if any(p.code == 0 for p in probes) else 0


def cmd_service_restart(args: argparse.Namespace) -> int:
    out = service_cmds.restart(_make_transport(args), args.name, yes=args.yes)
    print(out or f"restarted {args.name}")
    return 0


def cmd_service_listeners(args: argparse.Namespace) -> int:
    headers, rows = service_cmds.listeners(_make_transport(args))
    _output.render(rows, headers, fmt=args.fmt)
    return 0


# --- mq handlers ---------------------------------------------------------
def cmd_mq_status(args: argparse.Namespace) -> int:
    print(mq_cmds.status(_make_transport(args)))
    return 0


def _mq_table(args: argparse.Namespace, fn: Callable[[Transport], tuple[list[str], list[list[str]]]]) -> int:
    headers, rows = fn(_make_transport(args))
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_mq_queues(args: argparse.Namespace) -> int:
    return _mq_table(args, mq_cmds.queues)


def cmd_mq_consumers(args: argparse.Namespace) -> int:
    return _mq_table(args, mq_cmds.consumers)


def cmd_mq_vhosts(args: argparse.Namespace) -> int:
    return _mq_table(args, mq_cmds.vhosts)


def cmd_mq_permissions(args: argparse.Namespace) -> int:
    return _mq_table(args, mq_cmds.permissions)


_MQ_HANDLERS = {
    "status": cmd_mq_status,
    "queues": cmd_mq_queues,
    "consumers": cmd_mq_consumers,
    "vhosts": cmd_mq_vhosts,
    "permissions": cmd_mq_permissions,
}


# --- license handlers ----------------------------------------------------
def cmd_license_show(args: argparse.Namespace) -> int:
    print(license_cmds.show(_make_transport(args)))
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
