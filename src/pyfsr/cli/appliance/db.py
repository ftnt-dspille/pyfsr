"""``pyfsr appliance db`` — Postgres verbs, multi-DB aware.

Every verb resolves its target DB via :class:`Facts` (``--role`` or ``--db``) and
echoes the resolved name before running. Reads use a read-only path; writes go
through :func:`exec_write`, which refuses without an explicit confirmation.
"""

from __future__ import annotations

import re

from .facts import Facts

# A bare leading verb that mutates — used to reject writes on the read-only path.
_WRITE_RE = re.compile(
    r"^\s*(insert|update|delete|drop|create|alter|truncate|grant|revoke|comment|reindex|vacuum)\b",
    re.IGNORECASE,
)

# A valid Postgres identifier (table name). Module tableNames are themselves
# constrained to this shape by modules_admin, but these helpers take a raw string
# from CLI args / callers, so we re-validate before interpolating into SQL.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")


def _require_identifier(name: str) -> str:
    """Reject anything that isn't a bare SQL identifier (anti-injection guard)."""
    if not _IDENT_RE.fullmatch(name or ""):
        raise ValueError(f"invalid table identifier {name!r}: must match [A-Za-z_][A-Za-z0-9_]{{0,62}}")
    return name


def is_write_sql(sql: str) -> bool:
    """True if ``sql`` looks like a mutating statement."""
    return bool(_WRITE_RE.match(sql))


def query(facts: Facts, sql: str, *, role: str | None = None, db: str | None = None):
    """Run a **read-only** query. Rejects mutating SQL (use ``db exec --write``).

    Returns ``(dbname, headers, rows)``.
    """
    if is_write_sql(sql):
        raise ValueError("refusing to run a mutating statement via `db query` — use `db exec --write --yes`")
    target = facts.resolve_db(role=role, db=db)
    headers, rows = _query_with_headers(facts, sql, target)
    return target, headers, rows


def exec_write(
    facts: Facts,
    sql: str,
    *,
    role: str | None = None,
    db: str | None = None,
    yes: bool = False,
):
    """Run a mutating statement. Refuses unless ``yes=True``.

    Returns ``(dbname, status_line)`` where ``status_line`` is psql's command tag
    (e.g. ``DROP TABLE``). Caller is responsible for having printed the plan.
    """
    target = facts.resolve_db(role=role, db=db)
    if not yes:
        raise PermissionError(f"refusing to execute write against {target!r} without confirmation (pass --yes)")
    rows = facts.psql(sql, db=target, tuples_only=False)
    status = rows[-1][0] if rows and rows[-1] else "OK"
    return target, status


def tables(facts: Facts, pattern: str | None = None, *, role: str | None = None, db: str | None = None):
    """List tables (optionally name-filtered) in the target DB."""
    where = "WHERE schemaname='public'"
    if pattern:
        where += f" AND tablename LIKE '{_like(pattern)}'"
    sql = f"SELECT tablename FROM pg_tables {where} ORDER BY tablename"
    target = facts.resolve_db(role=role, db=db)
    rows = facts.psql(sql, db=target)
    return target, ["table"], rows


def indexes(facts: Facts, pattern: str | None = None, *, role: str | None = None, db: str | None = None):
    """List indexes (optionally name-filtered) in the target DB — the lookup used
    to diagnose ``42P07`` index-name collisions."""
    where = "WHERE schemaname='public'"
    if pattern:
        where += f" AND (indexname LIKE '{_like(pattern)}' OR tablename LIKE '{_like(pattern)}')"
    sql = f"SELECT tablename, indexname FROM pg_indexes {where} ORDER BY tablename, indexname"
    target = facts.resolve_db(role=role, db=db)
    rows = facts.psql(sql, db=target)
    return target, ["table", "index"], rows


def list_databases(facts: Facts):
    """Enumerate appliance DBs with sizes and detected content-DB role."""
    rows = facts.psql(
        "SELECT datname, pg_size_pretty(pg_database_size(datname)) "
        "FROM pg_database WHERE datistemplate=false ORDER BY pg_database_size(datname) DESC",
        db="postgres",
    )
    content = facts.content_db()
    out = []
    for name, size in rows:
        role = "content" if name == content else _fixed_role(name)
        out.append([name, size, role])
    return ["database", "size", "role"], out


def find_module_tables(facts: Facts, base_table: str) -> list[str]:
    """All physical tables left orphaned by a module delete: the base table and
    its join tables (``<base>_<x>``), discovered from ``pg_tables`` in the content DB.

    Matches ``base_table`` exactly plus any ``base_table_*`` (relationship,
    ``_team``, ``_actor``, etc.). Returns the actual table names present.
    """
    _require_identifier(base_table)
    target = facts.content_db()
    rows = facts.psql(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND "
        f"(tablename='{base_table}' OR tablename LIKE '{base_table}\\_%' ESCAPE '\\') "
        "ORDER BY tablename",
        db=target,
    )
    return [r[0] for r in rows if r and r[0]]


def drop_module_tables(facts: Facts, base_table: str, *, yes: bool = False) -> dict:
    """Drop the orphaned physical tables for a deleted module (``DROP TABLE ... CASCADE``).

    Discovers the table set with :func:`find_module_tables`, then drops each with
    CASCADE in the content DB. Refuses without ``yes=True``. Returns
    ``{"db", "dropped": [...], "planned": [...]}``.
    """
    target = facts.content_db()
    planned = find_module_tables(facts, base_table)
    if not yes:
        raise PermissionError(
            f"refusing to drop {len(planned)} table(s) in {target!r} without confirmation "
            f"(pass --yes): {', '.join(planned) or '(none found)'}"
        )
    dropped = []
    for tbl in planned:
        _require_identifier(tbl)
        facts.psql(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE', db=target, tuples_only=False)
        dropped.append(tbl)
    return {"db": target, "dropped": dropped, "planned": planned}


# --- internals -----------------------------------------------------------
def _query_with_headers(facts: Facts, sql: str, db: str):
    args_rows = facts.psql(sql, db=db, tuples_only=False)
    # psql with -A (no -t) puts headers on line 0; psql() already split on \x1f.
    if not args_rows:
        return [], []
    # Drop the trailing "(N rows)" summary line if present.
    if len(args_rows[-1]) == 1 and re.match(r"^\(\d+ rows?\)$", args_rows[-1][0].strip()):
        args_rows = args_rows[:-1]
    headers = args_rows[0]
    return headers, args_rows[1:]


def _like(pattern: str) -> str:
    """Translate a shell-style ``*`` glob into a SQL LIKE pattern; if the caller
    already used ``%`` leave it alone."""
    if "%" in pattern:
        return pattern.replace("'", "''")
    return pattern.replace("'", "''").replace("*", "%")


def _fixed_role(name: str) -> str:
    from .facts import FIXED_ROLE_DBS

    for role, dbname in FIXED_ROLE_DBS.items():
        if dbname == name:
            return role
    return ""
