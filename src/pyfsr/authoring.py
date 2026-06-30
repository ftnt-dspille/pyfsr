"""Compile playbook YAML to the FortiSOAR import envelope.

This is the bridge between the **fsr_playbooks** compiler (YAML → IR → FSR JSON)
and pyfsr's write path. It deliberately does **no** network I/O — it turns YAML
text into the ``{"type": "workflow_collections", "data": [...]}`` envelope that
:meth:`pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_export`
already knows how to push. The deploy step lives next to the client; this module
only compiles.

The compiler is an **optional** dependency: core pyfsr never imports it. Install
it with ``pip install "pyfsr[playbooks]"``. Until then, :func:`compile_playbook_yaml`
raises :class:`PlaybooksExtraNotInstalled` with that hint.

Example::

    from pyfsr.authoring import compile_playbook_yaml

    result = compile_playbook_yaml(open("alert.yaml").read())
    if result.ok:
        client.workflow_collections.import_export(result.fsr_json)
    else:
        for diag in result.errors:
            print(diag)
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PlaybooksExtraNotInstalled(ImportError):
    """Raised when the optional ``fsr_playbooks`` compiler is not installed."""

    def __init__(self, original: Exception | None = None) -> None:
        super().__init__('the playbook compiler is not installed — run: pip install "pyfsr[playbooks]"')
        self.original = original


def _load_compiler():
    """Import the fsr_playbooks compiler, translating a missing dep to a clear error."""
    try:
        from fsr_playbooks import compile_yaml
        from fsr_playbooks._db import default_db_path
    except ImportError as exc:  # pragma: no cover - exercised via the missing-extra test
        raise PlaybooksExtraNotInstalled(exc) from exc
    return compile_yaml, default_db_path


def _load_decompiler():
    """Import the fsr_playbooks decompiler (playbook JSON -> authored YAML)."""
    try:
        from fsr_playbooks.compiler.decompiler import decompile_to_yaml
    except ImportError as exc:  # pragma: no cover - exercised via the missing-extra test
        raise PlaybooksExtraNotInstalled(exc) from exc
    return decompile_to_yaml


def decompile_playbook_yaml(
    fsr_json: dict[str, Any],
    *,
    client: Any = None,
    db_path: str | Path | None = None,
) -> str:
    """Decompile a FortiSOAR WorkflowCollection export envelope into authored YAML.

    The inverse of :func:`compile_playbook_yaml` — turns the JSON a live playbook
    exports as back into the friendly YAML shape (so you can pull a playbook off
    an appliance and edit/version it as source). Catalog resolution mirrors
    compile: explicit ``db_path`` > warm-from-``client`` > packaged slim catalog,
    so connector/team/picklist IRIs render back as friendly names.

    Args:
        fsr_json: the export envelope (``{"type": "workflow_collections",
            "data": [<collection>]}``), e.g. from
            :meth:`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.get`.
        client: optional connected client to warm the catalog from (recommended,
            so custom connectors like ``code-runner`` decompile by name).
        db_path: explicit reference catalog path (overrides ``client``).

    Returns:
        The authored-style YAML as a string.
    """
    decompile_to_yaml = _load_decompiler()
    resolved = _resolve_catalog(client, db_path)
    return decompile_to_yaml(fsr_json, resolved)


# --------------------------------------------------------------------- warmup
def warm_catalog(
    client: Any,
    db_path: str | Path,
    *,
    connectors: bool = True,
    max_age: float | None = None,
) -> dict[str, int]:
    """Warm a reference catalog DB with the target SOAR's per-install data.

    The ``fsr_playbooks`` compiler resolves author-friendly tokens (team
    names, picklist values, tags) to IRIs against a local SQLite catalog. The
    stable tables (step types, handlers, jinja) ship populated in the wheel;
    the **per-install** tables (``teams``/``picklists``/``tags``) are empty
    until warmed. This function fills them from a live client — the native
    equivalent of the dev-only ``fsrpb probe modules`` warmup, callable from
    the installed wheel.

    If ``db_path`` does not exist, it is bootstrapped by copying the packaged
    slim catalog (so stable tables are present) and then the per-install
    tables are created + populated. Pass a writable path you own (e.g. a temp
    file or ``~/.cache/pyfsr/fsr_reference.db``); the packaged catalog in
    site-packages is read-only and must not be warmed in place.

    With ``connectors=True`` (default) it also syncs the **installed connector
    catalog** — ``connectors``/``operations``/``operation_params`` — from the live
    box, so the compiler validates connector/operation/param tokens against what
    is actually installed, INCLUDING custom connectors the packaged catalog can
    never know (e.g. a locally built ``code-runner``). Each installed connector is
    upserted from its live definition; other catalog connectors are left intact.

    Each section is synced independently — a failure in one (e.g. an empty
    picklists response) does not abort the others, mirroring the probe.

    Args:
        client: a connected :class:`pyfsr.FortiSOAR` client.
        db_path: writable path to warm (created from the slim catalog if absent).
        connectors: also sync the installed connector catalog (default True);
            set False to warm only teams/picklists/tags (faster, no per-connector
            definition fetches).
        max_age: incremental warm. When set (seconds), a section whose last warm
            (recorded in the ``_catalog_meta`` table) is younger than ``max_age``
            is **skipped** — its existing rows are kept and no HTTP is done, so
            repeated warms in a session cost nothing for unchanged surfaces. The
            default ``None`` always re-pulls every section (so freshly-created
            teams/tags are picked up immediately). A skipped section reports its
            cached row count and ``<section>_skipped=1`` in the summary.

    Returns:
        A ``{table: row_count}`` summary of what was written (``connectors``/
        ``operations``/``operation_params`` included when ``connectors=True``),
        plus per-section wall-clock under ``<section>_ms`` keys
        (``teams_ms``/``picklists_ms``/``tags_ms``/``connectors_ms``) and the
        overall ``total_ms`` — so a caller can see where warm time goes (the
        connector-definition fan-out is almost always the dominant cost).

    Raises:
        PlaybooksExtraNotInstalled: if the ``pyfsr[playbooks]`` extra is missing.
    """
    _, default_db_path = _load_compiler()
    db = Path(db_path)
    if not db.exists():
        db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(default_db_path(), db)

    summary: dict[str, int] = {}
    now = time.time()
    src_path = str(getattr(client, "base_url", "") or "")
    # Incremental-warm bookkeeping is best-effort: the _catalog_meta table is DDL,
    # and the fsr_playbooks compiler keeps a cached read connection to the same
    # cache DB. If that connection is open, the CREATE can't get its lock — in
    # which case we degrade to a full (non-incremental) warm rather than failing.
    meta_ok = [False]

    def _lap(key: str, t0: float) -> None:
        """Stamp a section's wall-clock (ms) into ``summary[f"{key}_ms"]`` so warm
        timing is observable per surface."""
        summary[f"{key}_ms"] = int((time.perf_counter() - t0) * 1000)

    def _fresh(section: str) -> bool:
        """True when ``section`` was warmed within ``max_age`` seconds (skip it)."""
        if max_age is None or not meta_ok[0]:
            return False
        row = conn.execute("SELECT warmed_at FROM _catalog_meta WHERE section = ?", (section,)).fetchone()
        return bool(row and row[0] is not None and (now - row[0]) < max_age)

    def _stamp(section: str) -> None:
        """Record ``section``'s warm time + provenance for future ``max_age`` skips."""
        if not meta_ok[0]:
            return
        conn.execute(
            "INSERT OR REPLACE INTO _catalog_meta (section, warmed_at, source) VALUES (?, ?, ?)",
            (section, now, src_path or "live"),
        )

    def _count(table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    t_start = time.perf_counter()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        # Provenance + incremental-warm bookkeeping (P2/P3) — best-effort (see above).
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _catalog_meta (  section TEXT PRIMARY KEY, warmed_at REAL, source TEXT)"
            )
            meta_ok[0] = True
        except sqlite3.OperationalError:
            meta_ok[0] = False
        # `teams` — playbook owners (name -> /api/3/teams/<uuid>).
        conn.execute("CREATE TABLE IF NOT EXISTS teams (name TEXT PRIMARY KEY, iri TEXT NOT NULL)")
        if _fresh("teams"):
            summary["teams"] = _count("teams")
            summary["teams_ms"] = 0
            summary["teams_skipped"] = 1
        else:
            _t = time.perf_counter()
            try:
                team_rows = [
                    (t["name"], f"/api/3/teams/{t['uuid']}")
                    for t in client.users.list_teams()
                    if t.get("name") and t.get("uuid")
                ]
                conn.execute("DELETE FROM teams")
                conn.executemany("INSERT OR REPLACE INTO teams (name, iri) VALUES (?, ?)", team_rows)
                summary["teams"] = len(team_rows)
            except Exception:
                summary["teams"] = 0
            _lap("teams", _t)
            _stamp("teams")

        # `picklists` — record-field picklist values (list, value -> item IRI).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS picklists ("
            "  list_name TEXT NOT NULL,"
            "  item_value TEXT NOT NULL,"
            "  item_iri TEXT NOT NULL,"
            "  PRIMARY KEY (list_name, item_value))"
        )
        if _fresh("picklists"):
            summary["picklist_items"] = _count("picklists")
            summary["picklists_ms"] = 0
            summary["picklists_skipped"] = 1
        else:
            _t = time.perf_counter()
            try:
                # One bulk fetch (2 HTTP calls) backs every picklist + its items —
                # was 1 list() + N values(). See PicklistsAPI.all().
                item_rows: list[tuple[str, str, str]] = []
                for nm, items in client.picklists.all().items():
                    for item in items:
                        iri, val = item.iri, item.itemValue
                        if iri and val is not None:
                            item_rows.append((nm, str(val), iri))
                conn.execute("DELETE FROM picklists")
                conn.executemany(
                    "INSERT OR REPLACE INTO picklists (list_name, item_value, item_iri) VALUES (?, ?, ?)",
                    item_rows,
                )
                summary["picklist_items"] = len(item_rows)
            except Exception:
                summary["picklist_items"] = 0
            _lap("picklists", _t)
            _stamp("picklists")

        # `tags` — set_variable.message.tags (name -> /api/3/tags/<uuid>).
        conn.execute("CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY, iri TEXT NOT NULL)")
        if _fresh("tags"):
            summary["tags"] = _count("tags")
            summary["tags_ms"] = 0
            summary["tags_skipped"] = 1
        else:
            _t = time.perf_counter()
            try:
                tag_rows = [(name, iri) for name, iri in client.tags.map_names().items()]
                conn.execute("DELETE FROM tags")
                conn.executemany("INSERT OR REPLACE INTO tags (name, iri) VALUES (?, ?)", tag_rows)
                summary["tags"] = len(tag_rows)
            except Exception:
                summary["tags"] = 0
            _lap("tags", _t)
            _stamp("tags")

        # `connectors`/`operations`/`operation_params` — the INSTALLED connector
        # catalog, so the compiler validates connector/operation/param tokens
        # against what is actually on this box, INCLUDING custom connectors the
        # packaged catalog can never know (e.g. a locally built code-runner). Each
        # installed connector is upserted from its live definition, replacing its
        # own ops/params; other connectors already in the catalog are left intact.
        if connectors and _fresh("connectors"):
            summary["connectors"] = _count("connectors")
            summary["operations"] = _count("operations")
            summary["operation_params"] = _count("operation_params")
            summary["connectors_ms"] = 0
            summary["connectors_skipped"] = 1
        elif connectors:
            _t = time.perf_counter()
            try:
                n_conn = n_ops = n_params = 0
                # Provenance (P3): each row stamps source='live' + source_path so
                # live-synced connectors are distinguishable from packaged ones;
                # `source` is NOT NULL in the catalog schema, so this is required.
                # Fetch every installed connector's definition concurrently
                # (network-bound), then write serially (one sqlite connection isn't
                # shareable across threads). Cuts warm time from ~Nx one-RTT to
                # ~one-RTT. Uses the public connectors API, not raw endpoints.
                for name, ver, d in _fetch_connector_defs(client):
                    cat = d.get("category")
                    if isinstance(cat, list):
                        cat = cat[0] if cat else None
                    conn.execute(
                        "INSERT OR REPLACE INTO connectors "
                        "(name, version, label, category, description, publisher, active, "
                        " cs_approved, cs_compatible, source, source_path) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            name,
                            str(ver or ""),
                            d.get("label"),
                            cat,
                            d.get("description"),
                            d.get("publisher"),
                            1 if d.get("active") else 0,
                            1 if d.get("cs_approved") else 0,
                            1 if d.get("cs_compatible") else 0,
                            "live",
                            src_path,
                        ),
                    )
                    n_conn += 1
                    conn.execute("DELETE FROM operations WHERE connector_name = ?", (name,))
                    conn.execute("DELETE FROM operation_params WHERE connector_name = ?", (name,))
                    for op in d.get("operations") or []:
                        op_name = op.get("operation")
                        if not op_name:
                            continue
                        conn.execute(
                            "INSERT INTO operations "
                            "(connector_name, op_name, title, annotation, category, description, "
                            " visible, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                name,
                                op_name,
                                op.get("title"),
                                op.get("annotation"),
                                op.get("category"),
                                op.get("description"),
                                1 if op.get("visible", True) else 0,
                                1 if op.get("enabled", True) else 0,
                            ),
                        )
                        n_ops += 1
                        # Flatten the param tree: top-level params plus every
                        # `onchange` conditional sub-param (tagged with the
                        # parent param + the option value that reveals it), so
                        # conditional params like smtp_ng.send_email_new's
                        # to/subject/content are known and don't false-positive
                        # as `unknown_param`. See _flatten_op_params.
                        for ordi, (p, parent_name, cond_value) in enumerate(_flatten_op_params(op.get("parameters"))):
                            opts = p.get("options")
                            options_json = json.dumps(opts) if isinstance(opts, list) and opts else None
                            # OR IGNORE: a few connectors (e.g. fortigate-firewall
                            # create_address) repeat a sub-param under nested
                            # onchange branches that collapse to the same
                            # (parent, condition, name) PK; keep the first and
                            # don't let one quirk abort the whole connector warm.
                            conn.execute(
                                "INSERT OR IGNORE INTO operation_params "
                                "(connector_name, op_name, parent_param_name, condition_value, "
                                " param_name, title, type, required, default_value, options_json, "
                                " tooltip, placeholder, description, visible, editable, ord) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    name,
                                    op_name,
                                    parent_name,
                                    cond_value,
                                    p.get("name"),
                                    p.get("title"),
                                    p.get("type"),
                                    1 if p.get("required") else 0,
                                    None if p.get("value") is None else str(p.get("value")),
                                    options_json,
                                    p.get("tooltip"),
                                    p.get("placeholder"),
                                    p.get("description"),
                                    1 if p.get("visible", True) else 0,
                                    1 if p.get("editable", True) else 0,
                                    ordi,
                                ),
                            )
                            n_params += 1
                summary["connectors"] = n_conn
                summary["operations"] = n_ops
                summary["operation_params"] = n_params
            except Exception:
                summary.setdefault("connectors", 0)
            _lap("connectors", _t)
            _stamp("connectors")

        conn.commit()
        summary["total_ms"] = int((time.perf_counter() - t_start) * 1000)
    finally:
        conn.close()
    return summary


def _flatten_op_params(params: Any):
    """Yield ``(param, parent_param_name, condition_value)`` for every operation
    param, descending into ``onchange`` conditional sub-params.

    FortiSOAR connector params nest: a ``select`` param carries an ``onchange``
    map ``{option_value: [sub-param, ...]}`` of params that only appear once that
    option is chosen (e.g. ``smtp_ng.send_email_new`` reveals ``to``/``cc`` only
    after Recipient Type is set, and ``subject``/``content`` after Body Type).
    The connector's flat ``parameters`` list omits these, so a playbook that sets
    one looks like it passes an ``unknown_param``. Recording them — each tagged
    with its parent param name and the option value that reveals it (the
    ``parent_param_name``/``condition_value`` catalog columns) — lets the compiler
    accept them without globally requiring them. Top-level params yield
    ``(param, None, None)``. Params lacking ``.get`` or a ``name`` are skipped.
    """

    def _walk(items: Any, parent: str | None, cond: str | None):
        for p in items or []:
            if not hasattr(p, "get") or not p.get("name"):
                continue
            yield p, parent, cond
            onchange = p.get("onchange")
            if isinstance(onchange, dict):
                pname = p.get("name")
                for opt_value, sub in onchange.items():
                    if isinstance(sub, list):
                        yield from _walk(sub, pname, opt_value)

    yield from _walk(params, None, None)


def _fetch_connector_defs(client: Any, *, max_workers: int = 8) -> list[tuple[str, str, Any]]:
    """Fetch each installed connector's full definition concurrently.

    Enumerates the installed set via ``client.connectors.list_configured()`` and
    pulls each definition via ``client.connectors.definition()`` — the public,
    typed API, no raw endpoints. Each definition fetch is an independent network
    call, so they run through the shared :func:`~pyfsr._concurrency.map_threaded`
    pool (requests sessions are safe for concurrent calls). Returns
    ``(name, version, definition)`` tuples; connectors whose fetch fails or
    returns a non-dict are dropped.
    """
    from ._concurrency import map_threaded

    installed = [c for c in client.connectors.list_configured() if c.name]
    if not installed:
        return []

    def _one(ic: Any) -> tuple[str, str, Any] | None:
        # definition() returns a typed ConnectorDefinition (dict-compatible —
        # the warm writer reads it via .get(...)).
        name, ver = ic.name, ic.version
        d = client.connectors.definition(name, version=ver)
        return (name, str(ver or ""), d) if d is not None else None

    results = map_threaded(_one, installed, max_workers=max_workers)
    return [r for r in results if r is not None]


@dataclass
class CompiledPlaybook:
    """Result of compiling playbook YAML.

    ``fsr_json`` is the FortiSOAR export envelope ready for
    :meth:`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_export`
    (``None`` when compilation produced blocking errors). ``errors`` holds every
    diagnostic (both ``error`` and ``warning`` severities) as dicts; ``warnings``
    is the warning-only subset. ``ok`` is True only when there are no blocking
    errors and an envelope was produced.
    """

    fsr_json: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [e for e in self.errors if e.get("severity") == "warning"]

    @property
    def blocking(self) -> list[dict[str, Any]]:
        return [e for e in self.errors if e.get("severity") != "warning"]

    @property
    def collection_names(self) -> list[str]:
        return [c.get("name", "") for c in (self.fsr_json or {}).get("data", [])]

    @property
    def playbook_names(self) -> list[str]:
        names: list[str] = []
        for col in (self.fsr_json or {}).get("data", []):
            for wf in col.get("workflows", []) or []:
                names.append(wf.get("name", ""))
        return names


def _normalize_lax_codes(lax_codes: Any) -> set | None:
    """Normalize ``lax_codes`` entries to the ``ErrorCode`` enum the compiler matches.

    Callers may pass the friendly code value (``"unknown_param"``), the enum name
    (``"UNKNOWN_PARAM"``), or the ``ErrorCode`` enum itself. The compiler's demote
    pass matches on ``str(ErrorCode.X)`` (== ``"ErrorCode.UNKNOWN_PARAM"``), so a
    bare ``.value`` string silently never matches. Resolving every entry to the
    enum here makes all three forms work — including against compiler builds whose
    own matching only accepts the enum form. Unknown strings pass through verbatim.
    """
    if not lax_codes:
        return None
    try:
        from fsr_playbooks.compiler.errors import ErrorCode
    except ImportError:
        return set(lax_codes)
    by_value = {e.value: e for e in ErrorCode}
    by_name = {e.name: e for e in ErrorCode}
    out: set = set()
    for c in lax_codes:
        if isinstance(c, str):
            out.add(by_value.get(c) or by_name.get(c) or by_name.get(c.upper()) or c)
        else:
            out.add(c)
    return out


def _default_cache_db() -> Path:
    """A writable per-user cache location for the warmed reference catalog."""
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        cache = Path(base)
    else:
        cache = Path.home() / ".cache"
    return cache / "pyfsr" / "fsr_reference.db"


def compile_playbook_yaml(
    text: str,
    *,
    client: Any = None,
    db_path: str | Path | None = None,
    lax_codes: set[str] | None = None,
) -> CompiledPlaybook:
    """Compile playbook YAML text into a :class:`CompiledPlaybook`.

    By default this is **offline** — it compiles against the packaged slim
    catalog (no network I/O), which resolves the stable token set (step types,
    handlers) but not per-install tokens (team names, picklist values, tags).

    Pass ``client`` to make warming **seamless**: a per-user cache catalog is
    warmed from the live instance (teams/picklists/tags) before compiling, so
    author-friendly tokens like ``owners: ["TeamA"]`` resolve to IRIs without
    the caller ever touching SQLite or a ``db_path``. The cache is refreshed
    on every call with a client so freshly-created teams are picked up.

    Args:
        text: the playbook YAML source.
        client: optional connected :class:`pyfsr.FortiSOAR` client. When given,
            the reference catalog is warmed from the instance before compiling
            (overrides nothing — pass ``db_path`` to use a specific catalog).
        db_path: explicit path to a reference catalog. Takes precedence over
            ``client``/the default. Use this to compile against a pre-warmed or
            custom catalog without a live client.
        lax_codes: optional set of diagnostic codes to downgrade from error to
            warning. Accepts the friendly code string (``"unknown_param"``), the
            ``ErrorCode`` enum, or the enum name (``"UNKNOWN_PARAM"``) — all are
            normalized to the enum the compiler matches on (see
            :func:`_normalize_lax_codes`).

    Raises:
        PlaybooksExtraNotInstalled: if the ``pyfsr[playbooks]`` extra is missing.

    Returns:
        A :class:`CompiledPlaybook` with the export envelope and diagnostics.
    """
    compile_yaml, default_db_path = _load_compiler()
    if db_path is not None:
        resolved = Path(db_path)
    elif client is not None:
        # Seamless warm: refresh the per-user cache from the live instance so
        # author-friendly tokens (team names, picklists, tags) resolve without
        # the caller knowing about SQLite. The user never passes a db_path.
        resolved = _default_cache_db()
        warm_catalog(client, resolved)
    else:
        resolved = default_db_path()
    result = compile_yaml(text, resolved, lax_codes=_normalize_lax_codes(lax_codes))
    errors = [e.to_dict() for e in result.errors]
    return CompiledPlaybook(fsr_json=result.fsr_json, errors=errors, ok=result.ok)


def _resolve_catalog(client: Any, db_path: str | Path | None) -> Path:
    """Resolve which reference catalog to use, warming a per-user cache from a
    live client when one is given (same rule as :func:`compile_playbook_yaml`):
    explicit ``db_path`` > warm-from-``client`` > packaged slim catalog."""
    _, default_db_path = _load_compiler()
    if db_path is not None:
        return Path(db_path)
    if client is not None:
        cache = _default_cache_db()
        warm_catalog(client, cache)
        return cache
    return default_db_path()


def _load_verify():
    """Import the fsr_playbooks verify gate + its check-group catalog."""
    try:
        from fsr_playbooks import CHECK_GROUPS, verify
    except ImportError as exc:  # pragma: no cover - exercised via the missing-extra test
        raise PlaybooksExtraNotInstalled(exc) from exc
    return verify, CHECK_GROUPS


@dataclass
class VerifiedPlaybook:
    """Result of running a playbook YAML through the fsr_playbooks verify gate.

    ``ready`` is the single go/no-go (the gate's ``ready_to_push``). ``suppressed``
    holds any diagnostics silenced via ``skip=`` — never dropped silently.
    Truthy iff ``ready``.
    """

    ready: bool = False
    required_fixes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    suppressed: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.ready

    def __bool__(self) -> bool:
        return self.ready

    def summary(self) -> str:
        head = "READY" if self.ready else "NOT READY"
        bits = [f"{len(self.required_fixes)} blocking", f"{len(self.warnings)} warning(s)"]
        if self.suppressed:
            bits.append(f"{len(self.suppressed)} suppressed")
        return f"{head} — {', '.join(bits)}"


def verify_playbook_yaml(
    text: str,
    *,
    client: Any = None,
    db_path: str | Path | None = None,
    live_probe: bool = False,
    skip: list[str] | None = None,
    playbook: str | None = None,
) -> VerifiedPlaybook:
    """Run playbook YAML through the fsr_playbooks **verify gate** — the single
    forcing-function pre-submit check (compile → typed walk → per-step schema →
    optional live probe). This is the method to call before showing or pushing a
    playbook.

    ``skip`` disables check groups or individual diagnostic codes (e.g.
    ``skip=["jinja", "type_mismatch"]``); the available groups are
    ``fsr_playbooks.CHECK_GROUPS``. Skipped diagnostics are surfaced under
    ``VerifiedPlaybook.suppressed``, never dropped silently. Pass ``client`` to
    warm a per-user catalog from the live instance (so record/op/config checks
    have real facts); pass ``live_probe=True`` to additionally probe safe ops on
    the target.

    Returns a :class:`VerifiedPlaybook` (truthy iff ready to push).
    """
    verify, _ = _load_verify()
    catalog = _resolve_catalog(client, db_path)
    res = verify(
        text,
        playbook=playbook,
        live_probe=live_probe,
        disable_checks=list(skip) if skip else None,
        db_path=str(catalog),
    )
    ev = res.get("evidence", {}) if isinstance(res, dict) else {}
    return VerifiedPlaybook(
        ready=bool(res.get("ready_to_push", False)),
        required_fixes=res.get("required_fixes", []),
        warnings=res.get("warnings", []),
        suppressed=ev.get("suppressed", []),
        next_actions=res.get("next_actions", []),
        raw=res,
    )


@dataclass
class DeployedPlaybook:
    """Outcome of :func:`build_and_deploy` — verify → compile → push, as one step."""

    verified: VerifiedPlaybook
    compiled: CompiledPlaybook | None = None
    deployed: bool = False
    response: Any = None
    stopped_at: str | None = None  # "verify" | "compile" | None (success)

    @property
    def ok(self) -> bool:
        return self.deployed

    def __bool__(self) -> bool:
        return self.deployed


def build_and_deploy(
    text: str,
    *,
    client: Any,
    db_path: str | Path | None = None,
    skip: list[str] | None = None,
    live_probe: bool = False,
    force: bool = False,
    replace: bool = False,
) -> DeployedPlaybook:
    """Build-then-push in one call: **verify → compile → import**. Stops (without
    pushing) at the first hard failure and tells you where via ``stopped_at``.

    The verify gate is the guard rail: a not-ready playbook is *not* pushed
    unless ``force=True``. ``skip`` forwards to the gate (same groups/codes as
    :func:`verify_playbook_yaml`). The catalog is warmed once from ``client`` and
    reused for both verify and compile. ``replace=True`` overwrites an existing
    collection on import.
    """
    catalog = _resolve_catalog(client, db_path)
    verified = verify_playbook_yaml(text, db_path=catalog, live_probe=live_probe, skip=skip)
    if not verified.ready and not force:
        return DeployedPlaybook(verified=verified, stopped_at="verify")
    compiled = compile_playbook_yaml(text, db_path=catalog)
    if not compiled.ok:
        return DeployedPlaybook(verified=verified, compiled=compiled, stopped_at="compile")
    response = client.workflow_collections.import_export(compiled.fsr_json, replace=replace)
    return DeployedPlaybook(verified=verified, compiled=compiled, deployed=True, response=response)


def find_operation(
    connector: str,
    query: str = "",
    *,
    client: Any = None,
    db_path: str | Path | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Discover a connector's operations from the reference catalog — the
    fastest way to find *what to call* when authoring a connector step.

    Wraps the fsr_playbooks discovery surface (``find_operation``) against the
    same catalog the compiler uses; pass ``client`` to warm it from the live
    instance. On a single match the result embeds the op's parameter schema, so
    you can drop straight into a step without a follow-up call.
    """
    try:
        from fsr_playbooks.mcp_server.tools_discovery import (
            find_operation as _find_operation,
        )
    except ImportError as exc:  # pragma: no cover
        raise PlaybooksExtraNotInstalled(exc) from exc
    catalog = _resolve_catalog(client, db_path)
    return _find_operation(connector, query, limit=limit, db_path=str(catalog))


def format_diagnostic(diag: dict[str, Any]) -> str:
    """Render one diagnostic dict as a single human-readable line."""
    sev = diag.get("severity", "error").upper()
    code = diag.get("code", "")
    path = diag.get("path", "")
    msg = diag.get("message", "")
    loc = f" at {path}" if path else ""
    line = f"[{sev}] {code}{loc}: {msg}"
    if diag.get("suggestion"):
        line += f" (suggestion: {diag['suggestion']})"
    if diag.get("near"):
        line += f" (near: {diag['near']})"
    return line
