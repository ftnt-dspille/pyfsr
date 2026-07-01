"""Configuration *import* — the other half of the Export Wizard.

Wraps FortiSOAR's ``/api/import`` + ``/api/3/import_jobs`` surface so callers can
re-apply a Configuration-Export ``.zip`` (the kind :class:`~pyfsr.api.export_config.ExportConfigAPI`
produces) without hand-driving the four-step job lifecycle. Accessed as
``client.import_config``.

The import is an **upsert keyed by ``config_id``**: re-importing a connector's
configs restores any that were deleted with their original UUIDs (so playbook
steps that reference a config by id keep working) and leaves existing ones in
place rather than duplicating them. Connector *secrets* travel in the export
encrypted with the appliance's key, so a same-appliance round-trip restores
them intact; importing onto a different appliance only decrypts if its
``PASSWORD_ENCRYPTION_KEY`` matches.

Lifecycle (each step verified live on 7.6.5):

1. ``POST /api/3/files`` — upload the ``.zip`` (via ``client.files.upload``).
2. ``POST /api/3/import_jobs`` ``{status:"InProgress", file:"/api/3/files/<uuid>"}``.
3. ``GET /api/import/<job>`` — kicks off **async** option generation; its body is
   a progress log, *not* the options. Poll ``GET /api/3/import_jobs/<job>`` until
   ``options`` is populated (status becomes ``"Reviewing"``).
4. Optionally ``PUT /api/3/import_jobs/<job>`` ``{options:...}`` to tweak, then
   ``PUT /api/import/<job>`` to trigger. Poll until ``status == "Import Complete"``.

An import that carries **module/schema** changes drives the appliance through the
same backup + DB migrate + cache-rebuild cycle a publish does, so — like
:meth:`~pyfsr.api.modules_admin.ModulesAdminAPI.publish` — the pollers here ride
through the transient 5xx / "System Backup" / "Clearing Cache" / "Schema Update"
states instead of failing on them, and :meth:`~ImportConfigAPI.import_file`
settles on a responsive schema cache before returning (see
:meth:`~ImportConfigAPI.wait_until_ready`).

Example:
    >>> client.import_config.import_file("code-snippet-backup.zip", wait=True)
    {'status': 'Import Complete', ...}
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..exceptions import FortiSOARException, describe_migrate_failure, is_migrate_transient
from ..models._integration import ImportJobResult
from .base import BaseAPI

#: Import-job statuses that mean the import run itself has finished.
_IMPORT_TERMINAL = frozenset({"import complete", "completed", "failed", "error"})

#: Terminal statuses that mean the import (and any schema migrate it triggered) failed.
#: The appliance surfaces a failed migrate right on the job — ``status == "Error"`` with
#: the publish exception in ``errorMessage`` (e.g. the ``42P07`` duplicate-index wedge).
_IMPORT_FAILED = frozenset({"failed", "error"})

#: Module-level ``changes`` fields that rewrite a table's identity — these drive a
#: destructive migrate (rename + index/constraint rebuild) that can fail or wedge the
#: appliance (e.g. a tableName rename whose ``CREATE INDEX`` collides with the old one).
_RISKY_MODULE_FIELDS = frozenset({"tablename", "name", "type"})


def _job_uuid(resp: dict[str, Any]) -> str | None:
    """Pull the import-job uuid out of an ``/api/3/import_jobs`` POST reply."""
    if not isinstance(resp, dict):
        return None
    uuid = resp.get("uuid")
    if isinstance(uuid, str) and uuid:
        return uuid
    iri = resp.get("@id")
    if isinstance(iri, str) and iri:
        return iri.rstrip("/").split("/")[-1]
    return None


class ImportConfigAPI(BaseAPI):
    """Apply a Configuration-Export ``.zip`` back onto the appliance."""

    # ------------------------------------------------------------- low level
    def create_job(self, file_iri: str) -> str:
        """Create an import job for an already-uploaded file; return its uuid.

        ``file_iri`` is the ``@id`` of a ``/api/3/files`` record (what
        ``client.files.upload`` returns under ``@id``).
        """
        resp = self.client.post("/api/3/import_jobs", data={"status": "InProgress", "file": file_iri})
        uuid = _job_uuid(resp)
        if not uuid:
            raise ValueError(f"could not determine import-job uuid from response: {resp!r}")
        return uuid

    def generate_options(self, job_uuid: str) -> None:
        """Trigger option generation for an import job (async; returns immediately).

        ``GET /api/import/<job>`` starts the server walking the bundle to build
        the per-section ``options``. The response is a progress log — the options
        themselves land on the job record, so follow this with
        :meth:`wait_for_options`.
        """
        self.client.get(f"/api/import/{job_uuid}")

    def get_job(self, job_uuid: str) -> ImportJobResult:
        """Fetch the full import-job record (``options``, ``status``, ``log``)."""
        resp = self.client.get(f"/api/3/import_jobs/{job_uuid}")
        return ImportJobResult.model_validate(resp if isinstance(resp, dict) else {"result": resp})

    def set_options(self, job_uuid: str, options: dict[str, Any]) -> dict[str, Any]:
        """Overwrite an import job's ``options`` (``PUT /api/3/import_jobs/<job>``)."""
        return self.client.put(f"/api/3/import_jobs/{job_uuid}", data={"options": options})

    def wait_for_options(self, job_uuid: str, *, interval: float = 2.0, timeout: float = 120.0) -> dict[str, Any]:
        """Poll the job until option generation has populated ``options``.

        Returns the populated ``options`` dict. Raises ``TimeoutError`` if the
        options never appear within ``timeout`` seconds. Migrate-cycle outages
        (5xx / "System Backup" / "Clearing Cache" / "Schema Update") are ridden
        through as "still working" rather than surfaced.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                job = self.get_job(job_uuid)
            except Exception as exc:
                if not is_migrate_transient(exc):
                    raise
                time.sleep(interval)
                continue
            options = job.options or {}
            if options:
                return options
            time.sleep(interval)
        raise TimeoutError(f"import job {job_uuid} options not ready after {timeout}s")

    def trigger(self, job_uuid: str) -> dict[str, Any]:
        """Start the import run (``PUT /api/import/<job>``; async)."""
        return self.client.put(f"/api/import/{job_uuid}")

    def wait_for_import(self, job_uuid: str, *, interval: float = 3.0, timeout: float = 600.0) -> ImportJobResult:
        """Poll the import run until it reaches a terminal status.

        Returns the latest job record; ``status == "Import Complete"`` means
        success. On timeout, returns the last (non-terminal) poll rather than
        raising.

        An import that carries **module/schema** changes puts the appliance
        through the same backup + DB migrate + cache-rebuild cycle a publish
        does, during which ``GET /api/3/import_jobs/<job>`` (and the API at
        large) briefly returns 503s and state strings like "System Backup",
        "Clearing Cache", or "Schema Update". Mirroring
        :meth:`~pyfsr.api.modules_admin.ModulesAdminAPI._wait_for_publish`, any
        such transient failure is treated as "still importing, keep waiting" —
        only a cleanly fetched job record is allowed to decide the outcome, so a
        mid-migrate outage never aborts a healthy import.
        """
        deadline = time.monotonic() + timeout
        job: ImportJobResult | None = None
        while True:
            try:
                job = self.get_job(job_uuid)
            except Exception as exc:
                # API down mid-migrate — the outage is the signal the import is
                # still running; keep polling until it stabilises or we time out.
                if not is_migrate_transient(exc):
                    raise
            else:
                if str(job.status or "").strip().lower() in _IMPORT_TERMINAL:
                    return job
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
        if job is None:
            # Never got a single clean poll within the window (whole import ran
            # under a migrate outage) — surface that rather than a bare None.
            raise TimeoutError(f"import job {job_uuid} never returned a readable status within {timeout}s")
        return job

    def wait_until_ready(self, *, interval: float = 3.0, timeout: float = 300.0) -> bool:
        """Block until the appliance schema layer answers cleanly again.

        After an import that carries module/schema changes reports ``Import
        Complete``, the appliance keeps rebuilding its schema cache *appliance-
        wide* for a while longer — so the very next ``list_modules()`` / record
        query can still hit a 503 "Clearing Cache" or "Schema Update". This polls
        the schema-metadata endpoint (the one that surfaces those states),
        treating every transient failure as "not settled yet", and returns once
        it gets a clean response.

        Returns ``True`` when the schema layer is responsive, ``False`` if it was
        still settling when ``timeout`` elapsed (callers can retry their own
        verification with that in mind rather than crashing).
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.client.get("/api/3/staging_model_metadatas", params={"$limit": 1})
                return True
            except Exception as exc:
                if not is_migrate_transient(exc):
                    raise
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    # ------------------------------------------------------------- high level
    def import_file(
        self,
        zip_path: str,
        *,
        modify_options: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        resolve: str | None = None,
        allow_schema_changes: bool = False,
        verify: bool = True,
        wait: bool = True,
        interval: float = 3.0,
        timeout: float = 600.0,
        options_timeout: float = 120.0,
        settle: bool = True,
        settle_timeout: float = 300.0,
    ) -> ImportJobResult:
        """Import a Configuration-Export ``.zip`` end-to-end.

        Runs the full lifecycle: upload → create job → generate & wait for
        options → resolve conflicts → trigger → wait → verify. The option step is
        the wizard's "Choose Modules and Views to Import" screen: the appliance
        diffs the bundle against live and reports per-field ``changes`` plus a
        per-field merge action (overwrite vs keep existing).

        **Refuse-by-default safety.** Some of those changes drive a *destructive*
        appliance-wide migrate — a ``tableName`` rename, a field type change, or a
        change to a unique-constraint field — which can fail outright or wedge the
        box (e.g. a rename whose ``CREATE INDEX`` collides with the old table's
        index: Postgres ``42P07``). If the generated options contain any such
        change and you haven't said how to handle it, this raises ``ValueError``
        *before* triggering — mirroring :meth:`~pyfsr.api.modules_admin.ModulesAdminAPI.publish`'s
        precheck. Pick one of ``modify_options`` / ``resolve`` / ``allow_schema_changes``
        to proceed (see :func:`inspect_changes` to view the risks first).

        Args:
            zip_path: path to the export ``.zip`` to import.
            modify_options: callback given the generated ``options`` dict; return
                the (mutated) dict to ``PUT`` back before triggering. Full control;
                takes precedence over ``resolve``. See :func:`connectors_only`,
                :func:`overwrite_all`, :func:`keep_existing`, :func:`skip_schema_changes`.
            resolve: one-shot conflict strategy instead of a callback — one of
                ``"overwrite"`` (apply all field changes), ``"keep_existing"``
                (keep every existing field, add only new ones), or ``"skip_schema"``
                (import records/views but do **not** apply schema changes — the safe
                way past a risky rename). This is the "just do it" flag.
            allow_schema_changes: proceed with the server-default options even when
                risky changes are present, without resolving them (default False).
            verify: raise :class:`~pyfsr.exceptions.FortiSOARException` if the run
                finishes in a failure state (``status`` "Error"/"Failed") — the
                appliance reports a failed schema migrate right on the job, with the
                publish exception in ``errorMessage`` (e.g. the ``42P07`` duplicate-
                index wedge). With ``verify=False`` the failed job is returned for
                the caller to inspect instead (default True).
            wait: block until the import reaches a terminal status (default True).
            interval: seconds between import-run polls (default 3).
            timeout: give up waiting on the import run after this many seconds.
            options_timeout: give up waiting on option generation after this many
                seconds (default 120).
            settle: after a successful wait, block until the appliance schema
                cache is responsive again (default True) so a follow-on
                ``list_modules()`` / query doesn't hit a "Clearing Cache" /
                "Schema Update" 503. See :meth:`wait_until_ready`. Ignored when
                ``wait=False``.
            settle_timeout: give up waiting on the schema cache to settle after
                this many seconds (default 300); does not raise.

        Returns:
            With ``wait=True``, the final job record (check ``status ==
            "Import Complete"``). With ``wait=False``, the job record right after
            triggering. Carries ``jobUuid`` either way.

        Raises:
            ValueError: if ``resolve`` is unknown, or if risky schema changes are
                present and none of ``modify_options`` / ``resolve`` /
                ``allow_schema_changes`` was given.
            FortiSOARException: if ``verify`` is set and the underlying schema
                migrate failed (carries the appliance's error, with remediation
                guidance for the half-applied-migration wedge).
        """
        if resolve is not None and resolve not in _RESOLVERS:
            raise ValueError(
                f"unknown resolve strategy {resolve!r}; choose one of {sorted(_RESOLVERS)} or pass modify_options=..."
            )

        uploaded = self.client.files.upload(zip_path)
        file_iri = uploaded.get("@id")
        if not file_iri:
            raise ValueError(f"file upload returned no @id: {uploaded!r}")

        job_uuid = self.create_job(file_iri)
        self.generate_options(job_uuid)
        options = self.wait_for_options(job_uuid, timeout=options_timeout)

        # Resolve the wizard's conflict step. A caller-supplied strategy (callback
        # or named resolver) takes ownership; otherwise refuse on risky changes
        # unless explicitly allowed.
        if modify_options is not None:
            options = modify_options(options)
            self.set_options(job_uuid, options)
        elif resolve is not None:
            options = _RESOLVERS[resolve](options)
            self.set_options(job_uuid, options)
        elif not allow_schema_changes:
            risks = inspect_changes(options)
            if risks:
                listed = "\n  - ".join(f"[{r['module']}] {r['message']}" for r in risks)
                raise ValueError(
                    "refusing to import: the generated options contain schema "
                    "changes that can fail or wedge the appliance-wide migrate:\n  - "
                    f"{listed}\n"
                    "Choose how to proceed: resolve='keep_existing' or 'skip_schema' "
                    "to import without applying these changes, resolve='overwrite' to "
                    "apply them, a custom modify_options=..., or allow_schema_changes="
                    "True to bypass this check."
                )

        self.trigger(job_uuid)
        if not wait:
            job = self.get_job(job_uuid)
            # Record the polled job UUID on the typed model (the wire record
            # carries its own @id/uuid, but legacy dict-compat callers read
            # ``result["jobUuid"]`` — declared field, not __pydantic_extra__).
            if job.jobUuid is None:
                job.jobUuid = job_uuid
            return job

        final = self.wait_for_import(job_uuid, interval=interval, timeout=timeout)
        if settle and str(final.status or "").strip().lower() in _IMPORT_TERMINAL:
            # Job reports done, but the appliance-wide schema/cache rebuild can
            # outlive it — wait for the schema layer to answer cleanly so the
            # caller's next list_modules()/query doesn't hit a stray 503.
            self.wait_until_ready(interval=interval, timeout=settle_timeout)
        if verify and str(final.status or "").strip().lower() in _IMPORT_FAILED:
            # A failed schema migrate is reported right on the job — status "Error"
            # with the publish exception in errorMessage (e.g. the 42P07 duplicate-
            # index wedge). Raise it with remediation guidance rather than handing
            # back a job the caller has to remember to inspect.
            raise FortiSOARException(describe_migrate_failure(final.status, final.errorMessage))
        if final.jobUuid is None:
            final.jobUuid = job_uuid
        return final


def connectors_only(options: dict[str, Any]) -> dict[str, Any]:
    """``modify_options`` callback: import only connector configs, nothing else.

    Forces ``includeConfigurations=True`` / ``includeInstall=False`` on every
    connector entry (restore configs, don't reinstall the connector) and clears
    the other top-level sections so a broad export only re-applies connector
    configuration. A connector-only export is already scoped this way; this is
    insurance when importing from a larger bundle.
    """
    conn = options.get("connectors")
    if isinstance(conn, dict):
        for entry in conn.get("values") or []:
            entry["includeConfigurations"] = True
            entry["includeInstall"] = False
        conn["include"] = True
    for key, val in list(options.items()):
        if key == "connectors":
            continue
        if isinstance(val, dict) and "include" in val:
            val["include"] = False
    return options


# --------------------------------------------------------------- module options
# The generated ``options["modules"]["values"]`` mirror the wizard's "Choose Modules
# and Views" screen. Each attribute carries the merge action the UI shows as a
# dropdown: "Overwrite with new version" == ``include: true / _include: "yes"``,
# "Keep old version" == ``include: false / _include: "no"``. The per-module "Schema"
# checkbox is ``_schema``. The helpers below set those levers; ``inspect_changes``
# reports the changes that make applying them dangerous.


def _iter_modules(options: dict[str, Any]):
    """Yield each module entry under ``options["modules"]["values"]``."""
    mods = options.get("modules") if isinstance(options, dict) else None
    if isinstance(mods, dict):
        for m in mods.get("values") or []:
            if isinstance(m, dict):
                yield m


def _iter_attributes(module: dict[str, Any]):
    """Yield each attribute dict under a module's ``attributes`` list."""
    for a in module.get("attributes") or []:
        if isinstance(a, dict):
            yield a


def overwrite_all(options: dict[str, Any]) -> dict[str, Any]:
    """``modify_options`` helper: "Overwrite with new version" for every field.

    Sets ``include=True`` / ``_include="yes"`` on every module attribute, applying
    all field changes from the bundle. Note this *applies* schema changes too — if
    a change is a risky rename/type change, prefer :func:`skip_schema_changes`.
    """
    for m in _iter_modules(options):
        for a in _iter_attributes(m):
            a["include"] = True
            a["_include"] = "yes"
    return options


def keep_existing(options: dict[str, Any], fields: list[str] | None = None) -> dict[str, Any]:
    """``modify_options`` helper: "Keep old version" for existing fields.

    Sets ``include=False`` / ``_include="no"`` on each *existing* field (one whose
    counterpart is already live), so its incoming change is not applied; brand-new
    fields are left included so they're still added. ``fields`` limits this to the
    named fields (matched on ``name`` or ``title``); ``None`` keeps every existing
    field.
    """
    want = set(fields) if fields is not None else None
    for m in _iter_modules(options):
        for a in _iter_attributes(m):
            if not a.get("exists"):
                continue  # new fields have no keep/overwrite choice — leave them in
            if want is None or a.get("name") in want or a.get("title") in want:
                a["include"] = False
                a["_include"] = "no"
    return options


def skip_schema_changes(options: dict[str, Any]) -> dict[str, Any]:
    """``modify_options`` helper: import records/views but apply no schema changes.

    Clears each module's ``_schema`` flag (the wizard's per-module "Schema"
    checkbox), so the import does not run the schema migration — no table rename,
    column type change, or index/constraint rebuild. This is the safe way past a
    risky change (see :func:`inspect_changes`) that would otherwise fail or wedge
    the appliance-wide migrate.
    """
    for m in _iter_modules(options):
        m["_schema"] = False
    return options


def inspect_changes(options: dict[str, Any]) -> list[dict[str, Any]]:
    """Report schema changes in generated import options that can break a migrate.

    Walks the included modules/attributes and flags the change classes that drive a
    *destructive* appliance-wide migrate: a module ``tableName``/identity change (a
    rename whose ``CREATE INDEX`` can collide → Postgres ``42P07``), an attribute
    type change (column rewrite), and any change to a unique-constraint field
    (constraint/index rebuild). Each item is
    ``{module, scope, field, kind, message}``. An empty list means the options are
    safe to import as-is.
    """
    risks: list[dict[str, Any]] = []
    for m in _iter_modules(options):
        if not m.get("include", True):
            continue
        mtype = m.get("type") or m.get("name")
        for ch in m.get("changes") or []:
            if not isinstance(ch, dict):
                continue
            field = ch.get("field")
            if isinstance(field, str) and field.lower() in _RISKY_MODULE_FIELDS:
                risks.append(
                    {
                        "module": mtype,
                        "scope": "module",
                        "field": field,
                        "kind": f"{field} change",
                        "message": ch.get("message") or f"{field} changed",
                    }
                )
        for a in _iter_attributes(m):
            if not a.get("include", True):
                continue  # field set to "keep existing" — its change won't apply
            aname = a.get("name")
            entries: list[dict[str, Any]] = []
            changes = a.get("changes")
            if isinstance(changes, dict):
                for v in changes.values():
                    if isinstance(v, list):
                        entries.extend(e for e in v if isinstance(e, dict))
            if any(e.get("field") == "type" for e in entries):
                risks.append(
                    {
                        "module": mtype,
                        "scope": "attribute",
                        "field": aname,
                        "kind": "field type change",
                        "message": f"field {aname!r} changes type (column rewrite)",
                    }
                )
            if a.get("inUniqueConstraint") and entries:
                risks.append(
                    {
                        "module": mtype,
                        "scope": "attribute",
                        "field": aname,
                        "kind": "unique-constraint field change",
                        "message": f"field {aname!r} is in a unique constraint and changes (constraint/index rebuild)",
                    }
                )
    return risks


#: Named one-shot conflict strategies for ``import_file(resolve=...)``.
_RESOLVERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "overwrite": overwrite_all,
    "keep_existing": keep_existing,
    "skip_schema": skip_schema_changes,
}
