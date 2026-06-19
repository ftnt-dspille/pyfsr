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

Example:
    >>> client.import_config.import_file("code-snippet-backup.zip", wait=True)
    {'status': 'Import Complete', ...}
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..models._integration import ImportJobResult
from .base import BaseAPI

#: Import-job statuses that mean the import run itself has finished.
_IMPORT_TERMINAL = frozenset({"import complete", "completed", "failed", "error"})


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
        resp = self.client.post(
            "/api/3/import_jobs", data={"status": "InProgress", "file": file_iri}
        )
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

    def wait_for_options(
        self, job_uuid: str, *, interval: float = 2.0, timeout: float = 120.0
    ) -> dict[str, Any]:
        """Poll the job until option generation has populated ``options``.

        Returns the populated ``options`` dict. Raises ``TimeoutError`` if the
        options never appear within ``timeout`` seconds.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get_job(job_uuid)
            options = job.options or {}
            if options:
                return options
            time.sleep(interval)
        raise TimeoutError(f"import job {job_uuid} options not ready after {timeout}s")

    def trigger(self, job_uuid: str) -> dict[str, Any]:
        """Start the import run (``PUT /api/import/<job>``; async)."""
        return self.client.put(f"/api/import/{job_uuid}")

    def wait_for_import(
        self, job_uuid: str, *, interval: float = 3.0, timeout: float = 600.0
    ) -> ImportJobResult:
        """Poll the import run until it reaches a terminal status.

        Returns the latest job record; ``status == "Import Complete"`` means
        success. On timeout, returns the last (non-terminal) poll rather than
        raising.
        """
        deadline = time.monotonic() + timeout
        job = self.get_job(job_uuid)
        while (
            str(job.status or "").strip().lower() not in _IMPORT_TERMINAL
            and time.monotonic() < deadline
        ):
            time.sleep(interval)
            job = self.get_job(job_uuid)
        return job

    # ------------------------------------------------------------- high level
    def import_file(
        self,
        zip_path: str,
        *,
        modify_options: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        wait: bool = True,
        interval: float = 3.0,
        timeout: float = 600.0,
        options_timeout: float = 120.0,
    ) -> ImportJobResult:
        """Import a Configuration-Export ``.zip`` end-to-end.

        Runs the full lifecycle: upload → create job → generate & wait for
        options → (optionally tweak) → trigger → wait. By default the job's
        server-generated options are used as-is, which for a connector-only
        export means *configs are restored without reinstalling the connector*
        (the export's ``includeInstall`` defaults to ``false`` when the connector
        already exists).

        Args:
            zip_path: path to the export ``.zip`` to import.
            modify_options: optional callback given the generated ``options`` dict;
                return the (possibly mutated) dict to ``PUT`` back before
                triggering. Use to toggle ``includeConfigurations`` /
                ``includeInstall`` or prune sections. See
                :func:`connectors_only` for a ready-made one.
            wait: block until the import reaches a terminal status (default True).
            interval: seconds between import-run polls (default 3).
            timeout: give up waiting on the import run after this many seconds.
            options_timeout: give up waiting on option generation after this many
                seconds (default 120).

        Returns:
            With ``wait=True``, the final job record (check ``status ==
            "Import Complete"``). With ``wait=False``, the job record right after
            triggering. Carries ``jobUuid`` either way.
        """
        uploaded = self.client.files.upload(zip_path)
        file_iri = uploaded.get("@id")
        if not file_iri:
            raise ValueError(f"file upload returned no @id: {uploaded!r}")

        job_uuid = self.create_job(file_iri)
        self.generate_options(job_uuid)
        options = self.wait_for_options(job_uuid, timeout=options_timeout)

        if modify_options is not None:
            options = modify_options(options)
            self.set_options(job_uuid, options)

        self.trigger(job_uuid)
        if not wait:
            job = self.get_job(job_uuid)
            # stash job_uuid in extras for callers that still do result["jobUuid"]
            if job.__pydantic_extra__ is not None:
                job.__pydantic_extra__.setdefault("jobUuid", job_uuid)
            return job

        final = self.wait_for_import(job_uuid, interval=interval, timeout=timeout)
        if final.__pydantic_extra__ is not None:
            final.__pydantic_extra__.setdefault("jobUuid", job_uuid)
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
