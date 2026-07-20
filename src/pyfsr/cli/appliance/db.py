"""``pyfsr appliance db`` — Postgres verbs, multi-DB aware.

Every verb resolves its target DB via :class:`Facts` (``--role`` or ``--db``) and
echoes the resolved name before running. Reads use a read-only path; writes go
through :func:`exec_write`, which refuses without an explicit confirmation.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

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


def query(
    facts: Facts, sql: str, *, role: str | None = None, db: str | None = None
) -> tuple[str, list[str], list[list[str]]]:
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
) -> tuple[str, str]:
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


def tables(
    facts: Facts, pattern: str | None = None, *, role: str | None = None, db: str | None = None
) -> tuple[str, list[str], list[list[str]]]:
    """List tables (optionally name-filtered) in the target DB."""
    where = "WHERE schemaname='public'"
    if pattern:
        where += f" AND tablename LIKE '{_like(pattern)}'"
    sql = f"SELECT tablename FROM pg_tables {where} ORDER BY tablename"
    target = facts.resolve_db(role=role, db=db)
    rows = facts.psql(sql, db=target)
    return target, ["table"], rows


def indexes(
    facts: Facts, pattern: str | None = None, *, role: str | None = None, db: str | None = None
) -> tuple[str, list[str], list[list[str]]]:
    """List indexes (optionally name-filtered) in the target DB — the lookup used
    to diagnose ``42P07`` index-name collisions."""
    where = "WHERE schemaname='public'"
    if pattern:
        where += f" AND (indexname LIKE '{_like(pattern)}' OR tablename LIKE '{_like(pattern)}')"
    sql = f"SELECT tablename, indexname FROM pg_indexes {where} ORDER BY tablename, indexname"
    target = facts.resolve_db(role=role, db=db)
    rows = facts.psql(sql, db=target)
    return target, ["table", "index"], rows


class DataClassSize(BaseModel):
    """One ``csadm db --getsize`` data-class footprint, normalised to MB."""

    data_class: str
    size: str  # raw, e.g. "7354 MB" / "8396 kB"
    size_mb: float


class DatabaseInfo(BaseModel):
    """One Postgres database: name, ``pg_size_pretty`` size, and detected role."""

    name: str
    size: str
    role: str


# Unit multipliers to normalise csadm's mixed kB/MB/GB sizes to MB.
_UNIT_MB = {"kb": 1 / 1024, "mb": 1.0, "gb": 1024.0, "tb": 1024.0 * 1024, "b": 1 / (1024 * 1024)}


def _size_to_mb(size: str) -> float:
    parts = size.split()
    if not parts:
        return 0.0
    try:
        value = float(parts[0])
    except ValueError:
        return 0.0
    unit = parts[1].lower() if len(parts) > 1 else "mb"
    return round(value * _UNIT_MB.get(unit, 1.0), 3)


def getsize(facts: Facts, *, timeout: float = 60.0) -> list[DataClassSize]:
    """Parse ``csadm db --getsize`` into a typed per-data-class footprint.

    csadm breaks the database footprint out by data class (primary / audit /
    workflow, plus archived data on newer releases) — the size view to check
    before an upgrade or when storage-pressure symptoms appear. Distinct from
    :func:`list_databases`, which sizes each Postgres DB via ``pg_database_size``;
    this is csadm's own class-level rollup. Needs root, so it runs under sudo.

    The real output (verified live on FSR 7.6.x / csadm) is a short report::

        Reading postgres details from db_config.yml file
        Following is the current database usage:
        Primary Data  : 7354 MB
        Audit Logs    : 1089 MB
        Workflow Logs : 1138 MB
        Archived Data : 8396 kB

    ``size_mb`` normalises the mixed kB/MB units. Empty result means the format
    changed — fall back to :func:`getsize_raw`.
    """
    raw = getsize_raw(facts, timeout=timeout)
    out: list[DataClassSize] = []
    for line in raw.splitlines():
        # Data lines are "<label> : <value> <unit>"; the preamble has no " : ".
        if " : " not in line:
            continue
        label, _, size = line.partition(" : ")
        label, size = label.strip(), size.strip()
        if label and size:
            out.append(DataClassSize(data_class=label, size=size, size_mb=_size_to_mb(size)))
    return out


def getsize_raw(facts: Facts, *, timeout: float = 60.0) -> str:
    """The unparsed ``csadm db --getsize`` output (escape hatch for :func:`getsize`)."""
    return facts.transport.run(["csadm", "db", "--getsize"], sudo=True, timeout=timeout).check().stdout.strip()


def list_databases(facts: Facts) -> list[DatabaseInfo]:
    """Enumerate appliance DBs with sizes and detected content-DB role."""
    rows = facts.psql(
        "SELECT datname, pg_size_pretty(pg_database_size(datname)) "
        "FROM pg_database WHERE datistemplate=false ORDER BY pg_database_size(datname) DESC",
        db="postgres",
    )
    content = facts.content_db()
    out: list[DatabaseInfo] = []
    for name, size in rows:
        role = "content" if name == content else _fixed_role(name)
        out.append(DatabaseInfo(name=name, size=size, role=role))
    return out


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


# Join tables FortiSOAR auto-creates for EVERY module (team-based access control +
# record ownership). A leftover ``<base>_team`` / ``<base>_actor`` whose ``<base>`` is
# no longer a live module is the reliable fingerprint of a deleted-module orphan — it
# distinguishes module litter from Django/Celery/system tables, which never carry these
# companions. (This is exactly the ``teamscoperepro_team`` / ``teamscoperepro_actor``
# shape left behind on fsr130; see CREATE_DELETE_ORPHAN_HARDENING_PLAN.md.)
_MODULE_MARKER_SUFFIXES = ("team", "actor")


class OrphanTable(BaseModel):
    """A physical table left behind by a deleted module (no metadata row backs it)."""

    table: str  # the physical table name present in pg_tables
    base: str  # the inferred former module base table (the family prefix)
    kind: str  # "base" (the module's own table) or "join" (a ``<base>_<rel>`` table)


def _metadata_table_col(facts: Facts, target: str) -> str:
    """Resolve the column holding a module's table name in ``model_metadatas``.

    FortiSOAR's Doctrine mapping has shipped this as both ``tableName`` and
    ``table_name`` across versions, so discover it from ``information_schema``
    rather than hard-coding either spelling.
    """
    rows = facts.psql(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name='model_metadatas' AND lower(column_name) IN ('tablename','table_name') "
        "ORDER BY column_name LIMIT 1",
        db=target,
    )
    col = rows[0][0] if rows and rows[0] and rows[0][0] else ""
    if not col:
        raise RuntimeError(
            "could not resolve the table-name column on model_metadatas (expected 'tableName' or 'table_name')"
        )
    return col


def live_module_tables(facts: Facts) -> set[str]:
    """Base table names backing every *live* module — published (``model_metadatas``)
    UNION staging (``staging_model_metadatas``). The complement of this set, over the
    physical tables, is what :func:`find_orphan_module_tables` reasons about."""
    target = facts.content_db()
    col = _metadata_table_col(facts, target)
    bases: set[str] = set()
    for meta in ("model_metadatas", "staging_model_metadatas"):
        rows = facts.psql(f'SELECT "{col}" FROM public.{meta}', db=target)
        bases.update(r[0] for r in rows if r and r[0])
    return bases


def find_orphan_module_tables(facts: Facts) -> list[OrphanTable]:
    """Sweep the content DB for physical tables left behind by deleted modules.

    A module delete over the API discards the module's metadata but the FortiSOAR
    API cannot ``DROP`` the physical tables, so ``<base>`` and its join tables linger
    (see :func:`drop_module_tables`). This finds them appliance-wide without needing
    the deleted module's name:

    1. ``live`` = every base table still backed by a metadata row (:func:`live_module_tables`).
    2. Any table named ``<base>_team`` / ``<base>_actor`` whose ``<base>`` is **not** live
       marks ``<base>`` as a deleted module (these join tables are auto-created for every
       module, so they are an unambiguous orphan fingerprint — system tables never have them).
    3. The orphan family is ``<base>`` plus every ``<base>_*`` physical table present.

    Non-destructive: returns the candidate list; use ``drop_module_tables(base, yes=True)``
    (or the CLI ``--drop``) to reclaim. Returns ``OrphanTable`` rows sorted by base then table.
    """
    target = facts.content_db()
    live = {b.lower() for b in live_module_tables(facts)}
    rows = facts.psql(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename",
        db=target,
    )
    all_tables = [r[0] for r in rows if r and r[0]]
    present = {t.lower(): t for t in all_tables}

    # Identify deleted-module bases from leftover marker join tables.
    orphan_bases: set[str] = set()
    for t in all_tables:
        for suffix in _MODULE_MARKER_SUFFIXES:
            marker = "_" + suffix
            if t.lower().endswith(marker):
                base = t[: -len(marker)]
                if base and base.lower() not in live:
                    orphan_bases.add(base.lower())
                break

    out: list[OrphanTable] = []
    for base in sorted(orphan_bases):
        family = [t for low, t in present.items() if low == base or low.startswith(base + "_")]
        for tbl in sorted(family):
            kind = "base" if tbl.lower() == base else "join"
            out.append(OrphanTable(table=tbl, base=base, kind=kind))
    return out


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
def _query_with_headers(facts: Facts, sql: str, db: str) -> tuple[list[str], list[list[str]]]:
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
