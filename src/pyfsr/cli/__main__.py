"""``pyfsr`` CLI entry point (argparse, dep-free).

Today this hosts the ``appliance`` command group (P1: ``db`` + ``info``). The
console script is wired as ``pyfsr = "pyfsr.cli.__main__:main"``.
"""

from __future__ import annotations

import argparse
import sys

from . import _output
from .appliance import db as db_cmds
from .appliance import info as info_cmds
from .appliance.facts import Facts
from .appliance.transport import TransportError, make_transport


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


def _make_facts(args) -> Facts:
    transport = make_transport(
        host=args.host,
        user=args.user,
        password=args.password,
        port=args.port,
        key_path=args.key_path,
        insecure_skip_host_key_check=args.insecure_skip_host_key_check,
    )
    return Facts(transport)


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

    return parser


def _add_fmt(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_const", const="json", dest="fmt", default="table")
    g.add_argument("--csv", action="store_const", const="csv", dest="fmt")


# --- command handlers ----------------------------------------------------
def cmd_info(args) -> int:
    facts = _make_facts(args)
    _output.kv(info_cmds.identity(facts), fmt=args.fmt)
    return 0


def cmd_db_list(args) -> int:
    facts = _make_facts(args)
    headers, rows = db_cmds.list_databases(facts)
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_db_query(args) -> int:
    facts = _make_facts(args)
    dbname, headers, rows = db_cmds.query(facts, args.sql, role=args.role, db=args.db)
    _emit_target(dbname, args.fmt)
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_db_tables(args) -> int:
    facts = _make_facts(args)
    dbname, headers, rows = db_cmds.tables(facts, args.pattern, role=args.role, db=args.db)
    _emit_target(dbname, args.fmt)
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_db_indexes(args) -> int:
    facts = _make_facts(args)
    dbname, headers, rows = db_cmds.indexes(facts, args.pattern, role=args.role, db=args.db)
    _emit_target(dbname, args.fmt)
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_db_exec(args) -> int:
    if not args.write:
        print("error: `db exec` mutates — pass --write to acknowledge", file=sys.stderr)
        return 2
    facts = _make_facts(args)
    target = facts.resolve_db(role=args.role, db=args.db)
    print(f"# plan: execute against {target!r}:\n  {args.sql}", file=sys.stderr)
    dbname, status = db_cmds.exec_write(facts, args.sql, role=args.role, db=args.db, yes=args.yes)
    print(f"{dbname}: {status}")
    return 0


def cmd_db_drop_module_tables(args) -> int:
    facts = _make_facts(args)
    planned = db_cmds.find_module_tables(facts, args.table)
    print(
        f"# plan: DROP TABLE CASCADE in {facts.content_db()!r}: "
        f"{', '.join(planned) or '(none found)'}",
        file=sys.stderr,
    )
    result = db_cmds.drop_module_tables(facts, args.table, yes=args.yes)
    _output.kv(
        {"db": result["db"], "dropped": ", ".join(result["dropped"]) or "(none)"},
        fmt=args.fmt,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (TransportError, ValueError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
