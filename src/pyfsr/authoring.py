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

import os
import shutil
import sqlite3
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


# --------------------------------------------------------------------- warmup
def warm_catalog(client: Any, db_path: str | Path) -> dict[str, int]:
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

    Each section is synced independently — a failure in one (e.g. an empty
    picklists response) does not abort the others, mirroring the probe.

    Args:
        client: a connected :class:`pyfsr.FortiSOAR` client.
        db_path: writable path to warm (created from the slim catalog if absent).

    Returns:
        A ``{table: row_count}`` summary of what was written.

    Raises:
        PlaybooksExtraNotInstalled: if the ``pyfsr[playbooks]`` extra is missing.
    """
    _, default_db_path = _load_compiler()
    db = Path(db_path)
    if not db.exists():
        db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(default_db_path(), db)

    summary: dict[str, int] = {}
    conn = sqlite3.connect(db)
    try:
        # `teams` — playbook owners (name -> /api/3/teams/<uuid>).
        conn.execute("CREATE TABLE IF NOT EXISTS teams (name TEXT PRIMARY KEY, iri TEXT NOT NULL)")
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

        # `picklists` — record-field picklist values (list, value -> item IRI).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS picklists ("
            "  list_name TEXT NOT NULL,"
            "  item_value TEXT NOT NULL,"
            "  item_iri TEXT NOT NULL,"
            "  PRIMARY KEY (list_name, item_value))"
        )
        try:
            item_rows: list[tuple[str, str, str]] = []
            for name in client.picklists.list():
                for item in client.picklists.values(name):
                    iri = item.get("iri")
                    val = item.get("itemValue")
                    if iri and val is not None:
                        item_rows.append((name, str(val), iri))
            conn.execute("DELETE FROM picklists")
            conn.executemany(
                "INSERT OR REPLACE INTO picklists (list_name, item_value, item_iri) VALUES (?, ?, ?)",
                item_rows,
            )
            summary["picklist_items"] = len(item_rows)
        except Exception:
            summary["picklist_items"] = 0

        # `tags` — set_variable.message.tags (name -> /api/3/tags/<uuid>).
        conn.execute("CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY, iri TEXT NOT NULL)")
        try:
            resp = client.get("/api/3/tags", params={"$limit": 2147483647, "$orderby": "name"})
            tag_rows = [
                (str(m["name"]), str(m["@id"]))
                for m in (resp or {}).get("hydra:member") or []
                if isinstance(m, dict) and m.get("name") and m.get("@id")
            ]
            conn.execute("DELETE FROM tags")
            conn.executemany("INSERT OR REPLACE INTO tags (name, iri) VALUES (?, ?)", tag_rows)
            summary["tags"] = len(tag_rows)
        except Exception:
            summary["tags"] = 0

        conn.commit()
    finally:
        conn.close()
    return summary


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
            warning (forwarded to the compiler).

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
    result = compile_yaml(text, resolved, lax_codes=lax_codes)
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
