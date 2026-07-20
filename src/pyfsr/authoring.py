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
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .models._integration import ConnectorConfigSummary, ConnectorDefinition, InstalledConnector

logger = logging.getLogger(__name__)


class _AuthoringClient(Protocol):
    """The slice of :class:`~pyfsr.FortiSOAR` this module actually calls.

    A ``Protocol`` instead of a concrete ``FortiSOAR`` import: this module is
    also driven by lightweight structural test doubles (see
    ``tests/unit/test_playbook_authoring.py``'s ``_WarmFakeClient``) that don't
    subclass the real client. A ``Protocol`` type-checks either — the real
    client via structural match, a test fake the same way — without a runtime
    import of :mod:`pyfsr.client` (and without callers needing ``# type:
    ignore`` just to pass a fake into a test). Prefer this over the ``Any``
    that used to sit here: ``Any`` disables type-checking on every attribute
    access, so a call like ``client.connecters.list_configured()`` (typo) or
    passing the wrong object entirely would only surface at runtime, deep
    inside a warm/compile call.
    """

    base_url: str

    @property
    def users(self) -> _UsersLike: ...

    @property
    def picklists(self) -> _PicklistsLike: ...

    @property
    def tags(self) -> _TagsLike: ...

    @property
    def connectors(self) -> _ConnectorsLike: ...

    @property
    def workflow_collections(self) -> _WorkflowCollectionsLike: ...


class _UsersLike(Protocol):
    def list_teams(self, params: dict[str, Any] | None = None) -> list[Any]: ...


class _PicklistsLike(Protocol):
    def all(self, *, refresh: bool = False) -> dict[str, list[Any]]: ...


class _TagsLike(Protocol):
    def map_names(self, *, limit: int = ...) -> dict[str, str]: ...


class _ConnectorsLike(Protocol):
    def list_configured(self, *, refresh: bool = False) -> list[InstalledConnector]: ...

    def definition(self, connector: str, *, version: str | None = None) -> ConnectorDefinition | None: ...


class _WorkflowCollectionsLike(Protocol):
    def import_export(self, data: Any, *, replace: bool = False) -> Any: ...


class PlaybooksExtraNotInstalled(ImportError):
    """Raised when the optional ``fsr_playbooks`` compiler is not installed."""

    def __init__(self, original: Exception | None = None) -> None:
        super().__init__('the playbook compiler is not installed — run: pip install "pyfsr[playbooks]"')
        self.original = original


def _load_compiler() -> tuple[Any, Any]:
    """Import the fsr_playbooks compiler, translating a missing dep to a clear error."""
    try:
        from fsr_playbooks import compile_yaml
        from fsr_playbooks._db import default_db_path
    except ImportError as exc:  # pragma: no cover - exercised via the missing-extra test
        raise PlaybooksExtraNotInstalled(exc) from exc
    return compile_yaml, default_db_path


def _load_decompiler() -> Any:
    """Import the fsr_playbooks decompiler (playbook JSON -> authored YAML)."""
    try:
        from fsr_playbooks.compiler.decompiler import decompile_to_yaml
    except ImportError as exc:  # pragma: no cover - exercised via the missing-extra test
        raise PlaybooksExtraNotInstalled(exc) from exc
    return decompile_to_yaml


def decompile_playbook_yaml(
    fsr_json: dict[str, Any],
    *,
    client: _AuthoringClient | None = None,
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
    client: _AuthoringClient,
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
        row = conn.execute("SELECT warmed_at FROM _pyfsr_warm_meta WHERE section = ?", (section,)).fetchone()
        return bool(row and row[0] is not None and (now - row[0]) < max_age)

    def _stamp(section: str) -> None:
        """Record ``section``'s warm time + provenance for future ``max_age`` skips."""
        if not meta_ok[0]:
            return
        conn.execute(
            "INSERT OR REPLACE INTO _pyfsr_warm_meta (section, warmed_at, source) VALUES (?, ?, ?)",
            (section, now, src_path or "live"),
        )

    def _count(table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    t_start = time.perf_counter()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        # Provenance + incremental-warm bookkeeping (P2/P3) — best-effort (see above).
        # NOTE: pyfsr's warm-state table is `_pyfsr_warm_meta`, NOT `_catalog_meta`.
        # The fsr_playbooks slim DB ships its own `_catalog_meta(key, value, updated_at)`
        # for provenance; `CREATE TABLE IF NOT EXISTS _catalog_meta (...)` was a no-op
        # against that existing table, so pyfsr's `SELECT warmed_at ... WHERE section`
        # hit `no such column` (live-regressed the v0.7.10 release). A distinct,
        # pyfsr-private name avoids the cross-package schema collision.
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _pyfsr_warm_meta (  section TEXT PRIMARY KEY, warmed_at REAL, source TEXT)"
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
            except Exception as exc:
                # Don't stamp a failed/partial warm as fresh: a caller retrying within
                # `max_age` must actually retry, not skip forever on cached bad data.
                summary["teams"] = 0
                logger.warning("warm_catalog: teams section failed, not marking fresh: %s", exc)
            else:
                _stamp("teams")
            _lap("teams", _t)

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
            except Exception as exc:
                summary["picklist_items"] = 0
                logger.warning("warm_catalog: picklists section failed, not marking fresh: %s", exc)
            else:
                _stamp("picklists")
            _lap("picklists", _t)

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
            except Exception as exc:
                summary["tags"] = 0
                logger.warning("warm_catalog: tags section failed, not marking fresh: %s", exc)
            else:
                _stamp("tags")
            _lap("tags", _t)

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
            summary["configurations"] = _count("connector_configs")
            summary["connectors_ms"] = 0
            summary["connectors_skipped"] = 1
        elif connectors:
            _t = time.perf_counter()
            try:
                n_conn = n_ops = n_params = n_cfg = 0
                # Per-appliance configured connector instances (config UUIDs) —
                # the `connector_configs` table the compiler's
                # `resolve_config_id` (resolver/catalog.py) reads to fill a
                # step's default config offline. Empty in the packaged slim
                # catalog (config UUIDs are box-specific). Schema matches the
                # framework's expected shape:
                #   connector_configs(connector, config_id, config_name, is_default)
                # so `resolve_config_id(connector, None)` (the default pick)
                # resolves to the default-flagged config without a live round-trip.
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS connector_configs ("
                    "  connector TEXT NOT NULL,"
                    "  config_id TEXT NOT NULL,"
                    "  config_name TEXT,"
                    "  is_default INTEGER NOT NULL DEFAULT 0,"
                    "  PRIMARY KEY (connector, config_id))"
                )

                # Provenance (P3): each row stamps source='live' + source_path so
                # live-synced connectors are distinguishable from packaged ones;
                # `source` is NOT NULL in the catalog schema, so this is required.
                # Fetch every installed connector's definition concurrently
                # (network-bound), then write serially (one sqlite connection isn't
                # shareable across threads). Cuts warm time from ~Nx one-RTT to
                # ~one-RTT. Uses the public connectors API, not raw endpoints.
                # `_fetch_connector_defs` returns ``ConnectorDefinition`` models
                # (validated at the API boundary), so display fields are already
                # coerced to scalars by ``OperationParam._coerce_display_text``
                # — including onchange sub-params, now that ``onchange`` is typed
                # recursively (sub-params validate as ``OperationParam`` too, not
                # raw ``__pydantic_extra__`` dicts). The earlier list-valued
                # placeholder (activedirectory object_dn) and list description are
                # caught at parse time, so no per-field coercion is needed here.
                # ``category`` is the one exception: it's typed ``str | list[str]``
                # (the wire genuinely sends either), so normalize it to a scalar.
                def _first_if_list(v: Any) -> Any:
                    if isinstance(v, list):
                        return v[0] if v else None
                    return v

                fetch_result = _fetch_connector_defs(client)
                n_fetch_failed = fetch_result.failed
                for fc in fetch_result.fetched:
                    name, ver, d, configs = fc.name, fc.version, fc.definition, fc.configurations
                    conn.execute(
                        "INSERT OR REPLACE INTO connectors "
                        "(name, version, label, category, description, publisher, active, "
                        " cs_approved, cs_compatible, source, source_path) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            name,
                            str(ver or ""),
                            d.get("label"),
                            _first_if_list(d.get("category")),
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
                    # Configured instances (per-appliance config UUIDs) — seeds
                    # the compiler's default-config fill. Replaces this connector's
                    # rows (a re-configured box drops stale UUIDs); other connectors
                    # are untouched. `default` is stored as 0/1 so the fill can pick
                    # the default-flagged config without a live round-trip.
                    conn.execute("DELETE FROM connector_configs WHERE connector = ?", (name,))
                    for cfg in configs:
                        cid = cfg.config_id
                        if not cid:
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO connector_configs "
                            "(connector, config_id, config_name, is_default) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                name,
                                cid,
                                cfg.name,
                                1 if cfg.default else 0,
                            ),
                        )
                        n_cfg += 1
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
                                _first_if_list(op.get("category")),
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
                summary["configurations"] = n_cfg
                summary["connectors_failed"] = n_fetch_failed
            except Exception as exc:
                # A mid-fanout failure still leaves whatever was upserted before
                # it raised -- but don't stamp the section fresh: a real
                # matrix-run bug traced to exactly this (a partial 12/32-connector
                # warmup got cached as "warmed" and a caller's max_age skip left
                # it stuck that way until a forced re-warm). Let the next call
                # retry for real.
                summary.setdefault("connectors", 0)
                logger.warning("warm_catalog: connectors section failed, not marking fresh: %s", exc)
            else:
                if n_fetch_failed:
                    # Some installed connectors' definition fetches failed
                    # (map_threaded's default policy swallows per-item errors —
                    # see _fetch_connector_defs) even though nothing raised here.
                    # Don't cache this as a complete warm or a retry within
                    # max_age would skip forever on a silently-partial catalog.
                    logger.warning(
                        "warm_catalog: %d/%d installed connector definition(s) failed to fetch; "
                        "not marking connectors fresh",
                        n_fetch_failed,
                        n_fetch_failed + n_conn,
                    )
                else:
                    _stamp("connectors")
            _lap("connectors", _t)

        conn.commit()
        summary["total_ms"] = int((time.perf_counter() - t_start) * 1000)
    finally:
        conn.close()
    return summary


def _flatten_op_params(params: Any) -> Iterator[tuple[dict[str, Any], str | None, str | None]]:
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

    def _walk(
        items: Any, parent: str | None, cond: str | None
    ) -> Iterator[tuple[dict[str, Any], str | None, str | None]]:
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


@dataclass(frozen=True)
class _FetchedConnector:
    """One installed connector's definition + per-appliance config instances,
    as fetched by :func:`_fetch_connector_defs` for :func:`warm_catalog` to write."""

    name: str
    version: str
    definition: ConnectorDefinition
    configurations: list[ConnectorConfigSummary]


@dataclass(frozen=True)
class _ConnectorFetchResult:
    """Outcome of a :func:`_fetch_connector_defs` fan-out.

    ``failed`` is what makes a silent partial warm visible to the caller (see
    the function docstring) instead of an opaque ``int`` the caller has to
    remember what it means.
    """

    fetched: list[_FetchedConnector]
    failed: int

    @property
    def n_installed(self) -> int:
        return len(self.fetched) + self.failed


def _fetch_connector_defs(client: _AuthoringClient, *, max_workers: int = 8) -> _ConnectorFetchResult:
    """Fetch each installed connector's full definition concurrently.

    Enumerates the installed set via ``client.connectors.list_configured()`` and
    pulls each definition via ``client.connectors.definition()`` — the public,
    typed API, no raw endpoints. Each definition fetch is an independent network
    call, so they run through the shared :func:`~pyfsr._concurrency.map_threaded`
    pool (requests sessions are safe for concurrent calls).

    ``map_threaded``'s default error policy swallows a per-item failure to
    ``None`` rather than aborting the whole fan-out — good for resilience, but
    it means a transient blip on a handful of connectors previously produced a
    silently-partial warm that the caller (:func:`warm_catalog`) could not tell
    apart from "every installed connector was fetched." That let a partial warm
    get stamped fresh and stay stuck incomplete until a caller noticed and
    forced a re-warm (the exact failure mode a matrix-run investigation traced
    to a *caller-side* staleness heuristic — see MASTER_TRACKER.md). Returning
    ``.failed`` here lets ``warm_catalog`` refuse to stamp a partial fetch as
    fresh, fixing it at the source instead of in every caller.

    ``configurations`` is the connector's configured-instance list carried
    straight off the ``InstalledConnector`` that ``list_configured()`` already
    populated inline — so recording it adds NO extra network call. They're
    per-appliance (a config UUID is specific to one box), so the warm store
    seeds the compiler's default-config fill (connector_args.py) — the
    packaged slim catalog has none.
    """
    from ._concurrency import map_threaded

    installed = [c for c in client.connectors.list_configured() if c.name]
    if not installed:
        return _ConnectorFetchResult(fetched=[], failed=0)

    def _one(ic: InstalledConnector) -> _FetchedConnector | None:
        # `installed` above is pre-filtered to `c.name` truthy, but that
        # narrowing doesn't survive across the closure for mypy.
        assert ic.name is not None
        name, ver = ic.name, ic.version
        d = client.connectors.definition(name, version=ver)
        if d is None:
            return None
        return _FetchedConnector(
            name=name,
            version=str(ver or ""),
            definition=d,
            configurations=list(ic.configurations or []),
        )

    results = map_threaded(_one, installed, max_workers=max_workers)
    fetched = [r for r in results if r is not None]
    return _ConnectorFetchResult(fetched=fetched, failed=len(results) - len(fetched))


class CompiledPlaybook(BaseModel):
    """Result of compiling playbook YAML.

    ``fsr_json`` is the FortiSOAR export envelope ready for
    :meth:`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_export`
    (``None`` when compilation produced blocking errors). ``errors`` holds every
    diagnostic (both ``error`` and ``warning`` severities) as dicts; ``warnings``
    is the warning-only subset. ``ok`` is True only when there are no blocking
    errors and an envelope was produced.
    """

    fsr_json: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    ok: bool = False

    @property
    def warnings(self) -> list[dict[str, Any]]:
        """The non-blocking diagnostics (``severity == "warning"``)."""
        return [e for e in self.errors if e.get("severity") == "warning"]

    @property
    def blocking(self) -> list[dict[str, Any]]:
        """The diagnostics that block deployment (everything not a warning)."""
        return [e for e in self.errors if e.get("severity") != "warning"]

    @property
    def collection_names(self) -> list[str]:
        """Names of the workflow collections in the compiled output."""
        return [c.get("name", "") for c in (self.fsr_json or {}).get("data", [])]

    @property
    def playbook_names(self) -> list[str]:
        """Names of every playbook across all compiled collections."""
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
    client: _AuthoringClient | None = None,
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
            ``_normalize_lax_codes``).

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


def _resolve_catalog(client: _AuthoringClient | None, db_path: str | Path | None) -> Path:
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


def _load_verify() -> tuple[Any, Any]:
    """Import the fsr_playbooks verify gate + its check-group catalog."""
    try:
        from fsr_playbooks import CHECK_GROUPS, verify
    except ImportError as exc:  # pragma: no cover - exercised via the missing-extra test
        raise PlaybooksExtraNotInstalled(exc) from exc
    return verify, CHECK_GROUPS


class VerifiedPlaybook(BaseModel):
    """Result of running a playbook YAML through the fsr_playbooks verify gate.

    ``ready`` is the single go/no-go (the gate's ``ready_to_push``). ``suppressed``
    holds any diagnostics silenced via ``skip=`` — never dropped silently.
    Truthy iff ``ready``.
    """

    ready: bool = False
    required_fixes: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    suppressed: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Alias for :attr:`ready` — ``True`` when the playbook has no blocking issues."""
        return self.ready

    def __bool__(self) -> bool:
        return self.ready

    def summary(self) -> str:
        """A one-line human summary: readiness plus blocking/warning/suppressed counts."""
        head = "READY" if self.ready else "NOT READY"
        bits = [f"{len(self.required_fixes)} blocking", f"{len(self.warnings)} warning(s)"]
        if self.suppressed:
            bits.append(f"{len(self.suppressed)} suppressed")
        return f"{head} — {', '.join(bits)}"


def verify_playbook_yaml(
    text: str,
    *,
    client: _AuthoringClient | None = None,
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


class DeployedPlaybook(BaseModel):
    """Outcome of :func:`build_and_deploy` — verify → compile → push, as one step."""

    verified: VerifiedPlaybook
    compiled: CompiledPlaybook | None = None
    deployed: bool = False
    response: Any = None
    stopped_at: str | None = None  # "verify" | "compile" | None (success)

    @property
    def ok(self) -> bool:
        """``True`` when the playbook made it all the way through and was deployed."""
        return self.deployed

    def __bool__(self) -> bool:
        return self.deployed


def build_and_deploy(
    text: str,
    *,
    client: _AuthoringClient,
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
    client: _AuthoringClient | None = None,
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
