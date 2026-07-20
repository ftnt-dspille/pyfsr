"""Connector discovery, health, and operation execution.

Wraps FortiSOAR's ``/api/integration`` surface so callers don't hand-build
execute payloads or hunt for a connector's configured version / config UUID.
Covers discovery, healthcheck, operation execution, and writing a connector's
*configuration* (its credentials) — see :meth:`ConnectorsAPI.create_configuration`.

Accessed as ``client.connectors``.

Example:
    >>> client = demo_client()
    >>> conn = client.connectors
    >>> [c.name for c in conn.list_configured()[:3]]   # installed + configured
    ['smtp', 'code-snippet', 'mitre-attack']
    >>> conn.resolve_version("mitre-attack")            # the configured version
    '2.0.2'
    >>> conn.healthcheck("mitre-attack").status         # "Available" is green
    'Available'

    Writes (install, create_configuration, execute) need a live appliance; see
    the connectors guide for those::

        conn.install("fortinet-fortisiem", "6.1.0", wait=True)
        conn.create_configuration("fortinet-fortisiem", {...}, name="prod")
        conn.execute("virustotal", "get_reputation_ip", params={"ip": "8.8.8.8"})

.. note::
    Setting up **data ingestion** (the *Configure Data Ingestion* wizard) is not
    automated here — configure the connector with this API, then run the wizard
    in the UI to map fetched data and schedule the ingestion playbook.

.. warning::
    Execution is **synchronous only for connectors that run on the FortiSOAR
    appliance itself**. For connectors bound to a remote *agent*, the
    ``/api/integration/execute/`` call is fire-and-forget: it returns
    immediately with an in-progress status and an empty ``data``, and the real
    result is pushed over a websocket (not pollable here). ``execute()`` does
    not — and cannot — wait for those; don't treat an empty ``data`` from an
    agent-bound connector as failure.
"""

from __future__ import annotations

import json
import mimetypes
import re
import tarfile
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any

from ..exceptions import APIError, ConfigurationExistsError, ConfigValidationError
from ..models._integration import (
    ConfigValidationResult,
    ConnectorConfig,
    ConnectorConfigSummary,
    ConnectorDefinition,
    EnsureVersionResult,
    ExecuteResult,
    HealthcheckResult,
    InstalledConnector,
    InstallJobStatus,
    IntegrationListEnvelope,
    Operation,
    OperationParam,
)
from ..pagination import extract_members
from ._solutionpacks import upload_solutionpack
from .base import BaseAPI


def _resolve_config_id_kwarg(config_id: str | None, config: str | None) -> str | None:
    """Fold the deprecated ``config=`` UUID keyword into ``config_id=``.

    ``config`` means two different things across this API — a configuration UUID
    on :meth:`ConnectorsAPI.execute` / :meth:`ConnectorsAPI.healthcheck`, but the
    configuration **field map** on ``create``/``update``/``upsert_configuration``
    and ``validate_config``. Same name, different types, and nothing catches the
    mix-up: a dict passed where a UUID was meant just becomes a bad query param.
    The UUID sense is being renamed to ``config_id``; ``config`` still works.
    """
    if config is None:
        return config_id
    if config_id is not None:
        raise ValueError("Pass the configuration UUID as config_id= or config= — not both.")
    warnings.warn(
        "The 'config' keyword is deprecated; use config_id= for a configuration "
        "UUID. ('config' elsewhere in this API means the configuration field map, "
        "not a UUID — the rename removes that collision.)",
        DeprecationWarning,
        stacklevel=3,  # 3: caller -> public method -> here
    )
    return config


def pack_connector(source_dir: str, output: str | None = None) -> str:
    """Bundle a connector source folder into a SOAR-importable ``.tgz``.

    FortiSOAR expects a connector archive to contain exactly **one top-level
    directory** (named for the connector) holding ``info.json``, ``connector.py``,
    ``operations.py``, etc. — e.g. ``flatten-json/info.json``. This packs
    ``source_dir`` as that top-level directory, preserving its own name.

    ``__pycache__`` directories and ``*.pyc`` files are excluded so a freshly
    built bundle doesn't smuggle stale bytecode onto the appliance.

    Args:
        source_dir: path to the connector folder (the one containing ``info.json``).
        output: destination ``.tgz`` path. Defaults to ``<source_dir>.tgz``
            alongside the folder.

    Returns:
        The path to the written ``.tgz``.

    Raises:
        FileNotFoundError: if ``source_dir`` doesn't exist.
        ValueError: if ``source_dir`` has no ``info.json`` (not a connector).
    """
    src = Path(source_dir).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"connector source folder not found: {src}")
    if not (src / "info.json").exists():
        raise ValueError(f"{src} has no info.json — not a connector source folder")
    out = Path(output) if output else src.with_suffix(".tgz")

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = Path(info.name).name
        if "__pycache__" in Path(info.name).parts or name.endswith(".pyc"):
            return None
        return info

    with tarfile.open(out, "w:gz") as tar:
        # arcname == folder name so the archive has a single top-level dir.
        tar.add(src, arcname=src.name, filter=_filter)
    return str(out)


#: Import-job statuses that mean a Content-Hub install has stopped running.
_INSTALL_TERMINAL = frozenset({"import complete", "completed", "failed", "error"})

#: Fields worth fetching when polling an install/import job's progress.
_INSTALL_FIELDS = "errorMessage,status,progressPercent,file,currentlyImporting,options"


def _field_label(field: dict[str, Any]) -> str:
    """Human label for a config field — its ``title``, falling back to ``name``."""
    return field.get("title") or field.get("name") or "?"


def _option_values(field: dict[str, Any]) -> list[Any]:
    """The accepted values of a ``select`` field. Options are usually plain
    strings, but tolerate ``{"value"/"title": ...}`` dict forms too."""
    out: list[Any] = []
    for opt in field.get("options") or []:
        if isinstance(opt, dict):
            out.append(opt.get("value", opt.get("title")))
        else:
            out.append(opt)
    return out


def _value_fits_type(ftype: str, value: Any) -> bool:
    """Best-effort type check for a *present* config value. Lenient on purpose —
    FortiSOAR stores most values as strings — flagging only clearly-wrong ones.
    Emptiness is handled separately by the required check."""
    if ftype == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, str) and value.strip().lstrip("+-").isdigit()
    if ftype == "checkbox":
        return isinstance(value, bool) or (
            isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0", "yes", "no"}
        )
    if ftype == "json":
        if isinstance(value, (dict, list)):
            return True
        if isinstance(value, str):
            try:
                json.loads(value)
                return True
            except (ValueError, TypeError):
                return False
        return False
    # text / password / select(no options) / email / etc. — no value constraint here.
    return True


def _onchange_key(value: Any) -> str | None:
    """Coerce a config value to its ``onchange`` map key. Checkbox values are
    keyed as the strings ``"true"`` / ``"false"``; everything else by ``str``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _type_default(ftype: str | None) -> Any:
    """A type-appropriate empty default for a field with no declared ``value``."""
    if ftype == "checkbox":
        return False
    if ftype == "integer":
        return 0
    if ftype == "json":
        return {}
    return ""


def _missing_message(field: dict[str, Any], condition: dict[str, Any] | None) -> str:
    """Guidance for a missing required field, naming the selection that requires
    it when the field lives in a conditional ``onchange`` branch."""
    msg = f"{_field_label(field)} is required"
    if condition:
        msg += f" when {condition['label']} = {condition['value']!r}"
    return msg


def _format_validation_error(connector: str, check: ConfigValidationResult) -> str:
    """Render a :meth:`ConnectorsAPI.validate_config` result as a multi-line,
    user-facing error for the create/update raise path."""
    lines = [f"{connector!r} configuration is invalid:"]
    for err in check.errors or []:
        if err.code == "unknown_field":
            continue  # non-fatal; don't fail the write on extra keys
        suffix = ""
        if err.valid_options is not None:
            suffix = f" (valid: {', '.join(map(str, err.valid_options))})"
        lines.append(f"  - {err.message}{suffix}")
    lines.append("(see client.connectors.config_schema(name) for the full schema)")
    return "\n".join(lines)


class ConnectorsAPI(BaseAPI):
    """Live connector listing, healthcheck, and operation execution."""

    def __init__(self, client):
        super().__init__(client)
        self._configured: list[InstalledConnector] | None = None

    def clear_cache(self) -> None:
        """Drop the cached configured-connector listing."""
        self._configured = None

    # ------------------------------------------------------------- install
    def install(
        self,
        name: str,
        version: str,
        *,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Install a connector from Content Hub by ``name`` + ``version``.

        Posts ``{"name", "version"}`` to ``POST /api/3/solutionpacks/install`` —
        the same call the Content Hub *Install* button makes. The install runs
        asynchronously as an *import job*; the response carries that job's id
        (poll it with :meth:`install_status`). Discover installable
        ``name``/``version`` pairs via
        ``client.content_hub.search_available_connectors(...)``.

        Args:
            name: connector name (e.g. ``"fortinet-fortisiem"``).
            version: the Content Hub version to install (e.g. ``"6.1.0"``).
            wait: block until the import job reaches a terminal status.
            interval: seconds between polls when ``wait`` (default 3).
            timeout: give up waiting after this many seconds (default 300).

        Returns:
            With ``wait=False``, the install response (carrying the import-job
            id). With ``wait=True``, the final :meth:`install_status` payload —
            check its ``status`` (``"Import Complete"`` means success). The
            configured-connector cache is dropped on a successful wait.
        """
        resp = self.client.post("/api/3/solutionpacks/install", data={"name": name, "version": version})
        resp = resp if isinstance(resp, dict) else {"result": resp}
        if not wait:
            return resp
        job_id = _import_job_id(resp)
        if not job_id:
            return resp
        final = self.wait_for_install(job_id, interval=interval, timeout=timeout)
        if str(final.status or "").strip().lower() in _INSTALL_TERMINAL:
            self.clear_cache()
        return final

    def install_from_file(
        self,
        path: str,
        *,
        replace: bool = False,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Install a connector by uploading its ``.tgz`` bundle.

        The multipart-upload form of ``POST /api/3/solutionpacks/install`` (the
        same endpoint :meth:`install` posts a name to). Sends the archive as
        ``file`` with the required ``$type=connector`` query parameter; pass
        ``replace=True`` to re-install over an existing version (``$replace=true``).
        The response carries the full connector record — including the integer
        ``id`` other calls need.

        Use this for connectors not in Content Hub (a locally built or
        custom ``.tgz``); use :meth:`install` to pull a published one by name.

        Args:
            path: filesystem path to the connector ``.tgz``.
            replace: overwrite an already-installed version of the same name.
            wait: block until the import job reaches a terminal status.
            interval: seconds between polls when ``wait`` (default 3).
            timeout: give up waiting after this many seconds (default 300).

        Returns:
            With ``wait=False``, the install response (the connector record,
            carrying any import-job id). With ``wait=True``, the final
            :meth:`install_status` payload. The configured-connector cache is
            dropped on a successful upload/wait.

        Raises:
            FileNotFoundError: if ``path`` doesn't exist.
        """
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        # NOTE: kept on the mimetypes-guessed content-type (unlike widgets, which
        # default to the live-verified "application/gzip") to avoid changing this
        # long-working path's wire behavior without re-verifying live against a
        # real connector bundle — see upload_solutionpack's docstring.
        resp = upload_solutionpack(self.client, path, type_="connector", replace=replace, content_type=mime_type)
        self.clear_cache()
        if not wait:
            return resp
        job_id = _import_job_id(resp)
        if not job_id:
            return resp
        return self.wait_for_install(job_id, interval=interval, timeout=timeout)

    def install_from_dir(
        self,
        source_dir: str,
        *,
        replace: bool = True,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Pack a connector source folder and upload it in one step.

        Bundles ``source_dir`` with :func:`pack_connector` into a temporary
        ``.tgz`` and hands it to :meth:`install_from_file`. Convenience for the
        build-test loop on a locally edited connector; defaults to
        ``replace=True`` since you're almost always re-pushing the same name.

        Args mirror :meth:`install_from_file` plus ``source_dir`` (the connector
        folder containing ``info.json``). Returns the install response.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tgz = pack_connector(source_dir, output=str(Path(tmp) / "bundle.tgz"))
            return self.install_from_file(tgz, replace=replace, wait=wait, interval=interval, timeout=timeout)

    def install_status(self, job_id: str) -> InstallJobStatus:
        """Fetch a connector install's import-job progress.

        ``GET /api/3/import_jobs/{job_id}`` (selecting just the progress fields).
        ``status == "Import Complete"`` means the install finished.
        """
        resp = self.client.get(f"/api/3/import_jobs/{job_id}", params={"__selectFields": _INSTALL_FIELDS})
        return InstallJobStatus.model_validate(resp if isinstance(resp, dict) else {"result": resp})

    def wait_for_install(self, job_id: str, *, interval: float = 3.0, timeout: float = 300.0) -> InstallJobStatus:
        """Poll an install import job until it reaches a terminal status.

        Returns the latest :meth:`install_status` payload. On timeout, returns
        the last poll with a non-terminal ``status`` rather than raising.
        """
        deadline = time.monotonic() + timeout
        status = self.install_status(job_id)
        while str(status.status or "").strip().lower() not in _INSTALL_TERMINAL and time.monotonic() < deadline:
            time.sleep(interval)
            status = self.install_status(job_id)
        return status

    def uninstall(self, connector: str, *, refresh: bool = True) -> None:
        """Uninstall a connector from the **appliance** (its self-agent).

        ``DELETE /api/integration/connectors/{id}/`` — the integer install id is
        resolved from ``connector`` (a name-only call won't work). The trailing
        slash is mandatory; the endpoint returns 204 on success. To remove a
        connector from a remote *agent* instead, use
        :meth:`~pyfsr.api.agents.AgentsAPI.uninstall_connector`.

        Raises ``ValueError`` if the connector isn't installed.
        """
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        self.client.delete(f"/api/integration/connectors/{connector_id}/")
        if refresh:
            self.clear_cache()

    def ensure_version(
        self,
        name: str,
        version: str,
        *,
        bundle_path: str | None = None,
        auto_fetch: bool = True,
        backup_dir: str | None = None,
        allow_uninstall_fallback: bool = False,
        wait: bool = True,
        interval: float = 3.0,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Make ``name`` be installed at exactly ``version``, preserving configs.

        The safe way to change a connector's version — including a **downgrade** —
        without losing its saved configurations. An in-place install (upgrade or
        downgrade) preserves configs on its own; this method additionally takes a
        Configuration-Export backup first and, if the version swap drops or
        shrinks the config set, restores it from that backup (re-creating configs
        with their original ``config_id`` so playbook references survive).

        Steps:

        1. If already at ``version``, no-op.
        2. If installed *and* configured, export a backup ``.zip`` (configs +
           encrypted secrets) via ``client.export_config.export_connector``.
        3. Install ``version`` in place — from ``bundle_path`` if given (a local
           ``.tgz``/zip), else by name from Content Hub; if Content Hub won't
           serve that version and ``auto_fetch`` is set (default), the exact-
           version ``.tgz`` is downloaded from the public repo and installed.
        4. Verify. If configs survived, done. If they didn't (downgrade schema
           drift, or a forced replace), re-import the backup.
        5. Only if the in-place install didn't reach ``version`` *and*
           ``allow_uninstall_fallback`` is set: uninstall (destroys configs),
           reinstall, then restore configs from the backup.

        Args:
            name: connector machine name (e.g. ``"code-snippet"``).
            version: target version (e.g. ``"2.1.5"``).
            bundle_path: optional local connector archive to install instead of
                pulling ``version`` from Content Hub. Usually unnecessary now —
                when Content Hub won't serve the target, ``auto_fetch`` downloads
                the exact-version ``.tgz`` from the public repo for you.
            auto_fetch: when no ``bundle_path`` is given and the by-name Content
                Hub install fails, download ``version`` from the public content
                repository (:mod:`pyfsr.repo`) and install that. On by default;
                set False to require Content Hub / an explicit bundle.
            backup_dir: directory to write the backup ``.zip`` into (default cwd).
            allow_uninstall_fallback: permit the destructive uninstall→reinstall
                path if an in-place install can't reach ``version``. Off by
                default — leaving the connector untouched is safer than a wipe.
            wait: block on installs/imports (default True).
            interval: poll interval for the install wait.
            timeout: per-install/-import timeout in seconds.

        Returns:
            A summary dict::

                {"action": "noop"|"in_place"|"restored"|"reinstalled"|"failed",
                 "from": <old version or None>, "to": <resolved version>,
                 "target": version, "backup": <path or None>,
                 "configs_before": N, "configs_after": M}
        """
        import os

        cur = self.resolve_version(name)
        if cur == version:
            n = len(self.configurations(name))
            return {
                "action": "noop",
                "from": cur,
                "to": cur,
                "backup": None,
                "configs_before": n,
                "configs_after": n,
            }

        installed = cur is not None
        configs_before = self.configurations(name) if installed else []

        backup_path: str | None = None
        if configs_before:
            out = os.path.join(backup_dir, f"{name}-{cur}-backup.zip") if backup_dir else None
            backup_path = self.client.export_config.export_connector(name, output_path=out)

        def _do_install() -> None:
            if bundle_path:
                self.install_from_file(bundle_path, replace=True, wait=wait, interval=interval, timeout=timeout)
                return
            try:
                self.install(name, version, wait=wait, interval=interval, timeout=timeout)
            except Exception:
                # Content Hub wouldn't serve ``version`` in place — fall back to
                # downloading the exact-version .tgz from the public repo and
                # installing that, so the caller doesn't have to fetch by hand.
                if not auto_fetch:
                    raise
                from .. import repo as _repo

                fetched = _repo.download_connector(name, version, backup_dir)
                self.install_from_file(fetched, replace=True, wait=wait, interval=interval, timeout=timeout)

        _do_install()
        self.clear_cache()
        new = self.resolve_version(name)
        configs_after = self.configurations(name)

        # In-place install reached the target — restore configs only if the swap
        # lost some (a clean in-place change keeps them).
        if new == version:
            if backup_path and len(configs_after) < len(configs_before):
                self.client.import_config.import_file(backup_path, wait=True)
                self.clear_cache()
                configs_after = self.configurations(name)
                return self._ensure_summary("restored", cur, version, backup_path, configs_before, configs_after)
            return self._ensure_summary("in_place", cur, version, backup_path, configs_before, configs_after)

        # In-place didn't take — destructive fallback, only if allowed.
        if allow_uninstall_fallback:
            if self.resolve_connector_id(name) is not None:
                self.uninstall(name)
            _do_install()
            self.clear_cache()
            new = self.resolve_version(name)
            if backup_path:
                self.client.import_config.import_file(backup_path, wait=True)
                self.clear_cache()
            configs_after = self.configurations(name)
            action = "reinstalled" if new == version else "failed"
            return self._ensure_summary(action, cur, new, backup_path, configs_before, configs_after)

        return self._ensure_summary("failed", cur, new, backup_path, configs_before, configs_after)

    @staticmethod
    def _ensure_summary(
        action: str,
        old: str | None,
        new: str | None,
        backup: str | None,
        before: list,
        after: list,
    ) -> EnsureVersionResult:
        return EnsureVersionResult.model_validate(
            {
                "action": action,
                "from": old,
                "to": new,
                "backup": backup,
                "configs_before": len(before),
                "configs_after": len(after),
            }
        )

    def connector_detail(self, connector: str) -> dict[str, Any]:
        """Fetch a connector's full record by id (operations-discovery endpoint).

        ``POST /api/integration/connectors/{id}/`` with a ``{}`` body — the
        spec-canonical way to enumerate a connector's installed operations.
        Returns the full record: ``operations[]`` (each with ``operation``,
        ``title``, ``description``, ``parameters[]``, ``output_schema``),
        ``configuration[]`` (each with ``config_id``, ``name``, ``config``,
        ``agent``), and ``config_schema``. GET is forbidden and an empty body
        415s, so this always POSTs ``{}``.

        Prefer this over :meth:`definition` when you have an installed connector
        and want exactly what the appliance reports for it. Raises ``ValueError``
        if the connector isn't installed.
        """
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        resp = self.client.post(f"/api/integration/connectors/{connector_id}/", data={})
        return resp if isinstance(resp, dict) else {"result": resp}

    # ------------------------------------------------------------- discovery
    def list_configured(self, *, refresh: bool = False) -> list[InstalledConnector]:
        """Installed + configured connectors.

        Cached after the first call; pass ``refresh=True`` to re-fetch.
        """
        if self._configured is not None and not refresh:
            return self._configured
        # The endpoint pages at ``page_size`` (default 30) and ignores ``$limit``
        # — walk every page so a connector past the first 30 isn't silently
        # dropped (which would make resolve_version/healthcheck miss it).
        out: list[InstalledConnector] = []
        page = 1
        page_size = 100
        while True:
            env = IntegrationListEnvelope.parse(
                self.client.get(
                    "/api/integration/connectors/",
                    params={"page": page, "page_size": page_size},
                )
            )
            for m in env.data:
                # Some versions label the field "title" rather than "label".
                if isinstance(m, dict) and "label" not in m and "title" in m:
                    m = dict(m, label=m["title"])
                out.append(InstalledConnector.model_validate(m))
            if not env.data:
                break
            if env.totalItems is not None:
                if len(out) >= env.totalItems:
                    break
            elif not env.has_next and len(env.data) < page_size:
                break
            page += 1
        self._configured = out
        return out

    def list_configurations(
        self,
        *,
        name: str | None = None,
        connector: str | int | None = None,
        active: bool | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[ConnectorConfig]:
        """List connector configuration records via ``GET /api/integration/configuration/``.

        The dedicated, filterable configurations endpoint (distinct from the
        connector-derived view of :meth:`configurations`). Each entry carries
        ``id`` (int), ``config_id`` (uuid), ``connector`` (int connector id),
        ``agent`` (set when remote), and ``config`` (the field map). Returns the
        ``data[]`` array (this endpoint is the custom ``{status, totalItems,
        data[]}`` envelope, not Hydra).

        Filters:

        * ``name`` — the **configuration's** name (e.g. ``"Branch FortiManager"``),
          i.e. what you passed as ``name`` to :meth:`upsert_configuration`.
        * ``connector`` — every configuration of one connector, by machine name
          (``"fortinet-fortimanager-json-rpc"``) or integer install id.
        * ``active`` — active configurations only.

        .. warning::
           ``name`` filters the CONFIGURATION name, **not** the connector name.
           This docstring claimed "connector name" until it was checked against a
           live appliance. The mistake is invisible at runtime: a connector name
           in ``name`` returns ``[]`` rather than raising, because the endpoint
           silently ignores filters it doesn't understand and this one simply
           matches nothing. Use ``connector=`` for that.

        ``connector`` resolves a name to its install id before querying — the
        endpoint's ``connector`` filter is the numeric id, and a name passed
        straight through errors ("Unknown error occurred"). A not-installed
        connector yields ``[]``: it cannot have configurations.
        """
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if name is not None:
            params["name"] = name
        if connector is not None:
            # bool is an int subclass — connector=True would query id 1.
            if isinstance(connector, bool):
                raise TypeError("connector must be a machine name or install id, not a bool")
            if isinstance(connector, int):
                connector_id: int | None = connector
            else:
                connector_id = self.resolve_connector_id(connector)
                if connector_id is None:
                    return []
            params["connector"] = connector_id
        if active is not None:
            params["active"] = active
        env = IntegrationListEnvelope.parse(self.client.get("/api/integration/configuration/", params=params))
        return [ConnectorConfig.model_validate(r) for r in env.data]

    def _find_configured(self, connector: str) -> InstalledConnector | None:
        return next((c for c in self.list_configured() if c.name == connector), None)

    def find_installed_connectors(self, query: str) -> list[InstalledConnector]:
        """Search *installed* connectors by partial, case-insensitive match.

        Scoped to connectors installed on this appliance (the
        :meth:`list_configured` set) — it does **not** see the Content Hub
        catalog of installable-but-not-installed connectors. For that, use
        ``client.content_hub.search_available_connectors(...)``.

        Matches ``query`` as a substring of either the connector ``name`` or its
        ``label`` — so ``"fortigate"`` finds ``fortigate-firewall`` (label
        ``"Fortinet FortiGate"``) regardless of hyphen/underscore or casing.
        Returns the matching :meth:`list_configured` entries (possibly empty),
        ordered with exact ``name`` matches first.

        Useful when you don't know a connector's exact machine name — note that
        :meth:`resolve_version` and friends require the exact ``name``, while the
        human-facing label differs (``"Fortinet FortiGate"`` vs
        ``"fortigate-firewall"``).
        """

        def norm(s: str | None) -> str:
            # fold case and treat '-', '_', and whitespace as interchangeable so
            # 'fortigate_firewall', 'FortiGate', and 'forti gate' all match.
            return re.sub(r"[-_\s]+", "-", (s or "").strip().lower())

        q = norm(query)
        hits = [c for c in self.list_configured() if q in norm(c.name) or q in norm(c.label)]
        hits.sort(key=lambda c: norm(c.name) != q)
        return hits

    def configurations(self, connector: str) -> list[ConnectorConfigSummary]:
        """List a connector's configurations (``[{config_id, name, default}]``)."""
        hit = self._find_configured(connector)
        return hit.configurations if hit else []

    def resolve_version(self, connector: str) -> str | None:
        """The configured version of ``connector`` (``None`` if not configured)."""
        hit = self._find_configured(connector)
        return hit.version if hit else None

    def resolve_connector_id(self, connector: str) -> int | None:
        """The integer install id of ``connector`` (``None`` if not installed).

        Required by :meth:`create_configuration` — the
        ``/api/integration/configuration/`` endpoint 500s on a name-only body
        and needs this numeric id.
        """
        hit = self._find_configured(connector)
        return hit.id if hit else None

    def resolve_config(self, connector: str, config_name: str | None = None) -> str | None:
        """Return a config UUID for ``connector``.

        With ``config_name`` given, matches by name; otherwise picks the
        configuration flagged default (falling back to the first one).
        """
        configs = self.configurations(connector)
        if not configs:
            return None
        chosen = None
        if config_name:
            chosen = next((c for c in configs if c.name == config_name), None)
        if chosen is None:
            chosen = next((c for c in configs if c.default), None) or configs[0]
        return chosen.config_id if chosen else None

    # ------------------------------------------------------------- health
    def healthcheck(
        self,
        connector: str,
        *,
        version: str | None = None,
        config_id: str | None = None,
        config: str | None = None,
    ) -> HealthcheckResult:
        """Live-check whether a connector configuration is reachable.

        ``status="Available"`` is green. A 404 is normalized to
        ``status="no-config"`` meaning the connector isn't configured.

        ``config_id`` is a configuration **UUID** — the ``config_id`` of the
        :class:`~pyfsr.models.ConnectorConfig` that :meth:`upsert_configuration`
        returns. Omit it to check the connector's *default* configuration; pass it
        for any non-default config, or the call fails with a "Could not find a
        configuration matching the id get_default_config" error.

        .. deprecated::
           The ``config`` keyword is deprecated in favour of ``config_id``. It
           took a UUID, while ``config`` on :meth:`upsert_configuration` /
           :meth:`create_configuration` / :meth:`validate_config` takes the field
           **map** — one name, two types. ``config`` still works and wins nothing:
           passing both raises.
        """
        config_id = _resolve_config_id_kwarg(config_id, config)
        version = version or self.resolve_version(connector)
        if not version:
            return HealthcheckResult(
                name=connector,
                status="no-config",
                message=f"{connector!r} is not configured on this instance",
            )
        path = f"/api/integration/connectors/healthcheck/{connector}/{version}/"
        params = {"config": config_id} if config_id else None
        try:
            return HealthcheckResult.model_validate(self.client.get(path, params=params))
        except Exception as e:  # noqa: BLE001 - normalize "not configured" to data
            resp = getattr(e, "response", None)
            if resp is not None and getattr(resp, "status_code", None) == 404:
                return HealthcheckResult(
                    name=connector,
                    version=version,
                    status="no-config",
                    message="no configuration on this instance",
                    http_status=404,
                )
            raise

    def healthcheck_all(
        self, connectors: list[str] | None = None, *, max_workers: int = 8
    ) -> dict[str, HealthcheckResult]:
        """Healthcheck many connectors **concurrently**, keyed by connector name.

        With ``connectors=None`` (default), checks every configured connector
        (the :meth:`list_configured` set with a resolvable version). Each check is
        an independent ``GET``, so they run in a bounded thread pool — a fleet
        status sweep that was N round-trips becomes roughly one. A connector whose
        check raises lands as a ``status="error"``
        :class:`~pyfsr.models._integration.HealthcheckResult` so
        one failure never sinks the whole sweep.
        """
        from .._concurrency import map_threaded

        names = (
            connectors if connectors is not None else [c.name for c in self.list_configured() if c.name and c.version]
        )

        def _one(name: str) -> tuple[str, HealthcheckResult]:
            try:
                return name, self.healthcheck(name)
            except Exception as e:  # noqa: BLE001 - report, don't abort the sweep
                return name, HealthcheckResult(name=name, status="error", message=str(e))

        return dict(map_threaded(_one, names, max_workers=max_workers, on_error="raise"))

    # ------------------------------------------------------------- definition
    def definition(self, connector: str, *, version: str | None = None) -> ConnectorDefinition:
        """Fetch a connector's full definition (config schema + operations).

        ``POST /api/integration/connectors/<name>/<version>/?format=json`` (the
        endpoint forbids GET). ``version`` is resolved from the configured
        connector when omitted. The returned
        :class:`~pyfsr.models._integration.ConnectorDefinition` carries
        ``config_schema``, ``configuration``, and typed ``operations`` (each an
        :class:`~pyfsr.models._integration.Operation` with ``operation``,
        ``title``, typed ``parameters``, ``output_schema``). Dict-compatible, so
        ``defn["operations"][0]["operation"]`` still works.

        Raises ``ValueError`` if the version can't be resolved.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} is not configured; pass version= to fetch its definition")
        resp = self.client.post(f"/api/integration/connectors/{connector}/{version}/?format=json", data={})
        return ConnectorDefinition.model_validate(resp if isinstance(resp, dict) else {})

    def operations(self, connector: str, *, version: str | None = None) -> list[Operation]:
        """List a connector's operations (the ``operations`` of :meth:`definition`).

        Returns typed, dict-compatible
        :class:`~pyfsr.models._integration.Operation` objects — each carries
        ``operation`` (the api name), ``title``, ``description``, typed
        ``parameters`` (:class:`~pyfsr.models._integration.OperationParam`), and
        ``output_schema``.
        """
        defn = self.definition(connector, version=version)
        return defn.operations

    def action_ui_schema(
        self,
        connector: str,
        operation: str,
        *,
        version: str | None = None,
        required_only: bool = False,
        selections: dict[str, Any] | None = None,
    ) -> list[OperationParam]:
        """The input params a UI/agent must render to stage one connector action.

        Resolves ``connector``'s definition, finds ``operation`` by its api name,
        and returns its :meth:`~pyfsr.models.Operation.ui_params` —
        the visible params, required-first, deduped across conditional groups,
        each carrying its ``type``/``title``/``required`` and (for a ``select``)
        its :meth:`~pyfsr.models.OperationParam.select_options`.

        This is the "connector action UI schema" widget and tooling authors were
        re-deriving by hand from the raw definition.

        Pass ``selections`` (a ``{param_name: chosen_value}`` map of what the
        user has picked so far) to also include the sub-params those choices
        reveal via each ``select``'s ``onchange`` map — so you render only the
        fields needed for the current state. Without it, only the base form is
        returned. Pass ``required_only=True`` for just the required inputs. See
        :meth:`~pyfsr.models.Operation.ui_params` for the reveal
        semantics. Raises :class:`ValueError` if the operation is not found on
        the connector.
        """
        for op in self.operations(connector, version=version):
            if op.operation == operation:
                return op.ui_params(required_only=required_only, selections=selections)
        raise ValueError(f"connector {connector!r} has no operation {operation!r}")

    def config_schema(self, connector: str, *, version: str | None = None) -> list[dict[str, Any]]:
        """Return a connector's configuration field schema (its ``config_schema``).

        Each field carries ``name``, ``type`` (``text``/``password``/``select``/
        ``checkbox``/…), ``title``, ``required``, a default ``value``, and — for
        ``select`` fields — an ``onchange`` map whose keys are option values and
        whose values are the *sub-fields* that become active when that option is
        chosen (e.g. FortiSIEM's ``fsm_type`` reveals ``server``/``username``/
        ``password`` only when set to ``"FortiSIEM"``). Feed the same shape to
        :meth:`validate_config` to check a config before saving.
        """
        defn = self.definition(connector, version=version)
        schema = defn.get("config_schema") or {}
        return schema.get("fields") or []

    def default_config(self, connector: str, *, version: str | None = None) -> dict[str, Any]:
        """Build a schema-complete **default** configuration dict for ``connector``.

        Walks the config schema and fills every field with its declared default
        ``value`` (or a type-appropriate empty default — ``False`` for checkbox,
        ``0`` for integer, ``""`` otherwise), **including the conditional
        sub-fields that** ``onchange`` **reveals for those defaults**. That last
        part is the point: a connector like ``code-snippet`` requires
        ``restrict_imports`` only when ``allow_imports`` is unchecked, and that
        requirement is invisible until the *playbook run* fails with a
        ``KeyError``. Start from this dict, override what you need, and pass it to
        :meth:`create_configuration` / :meth:`upsert_configuration`.

        Example:
            >>> cfg = client.connectors.default_config("code-snippet")  # doctest: +SKIP
            >>> cfg                                   # doctest: +SKIP
            {'allow_imports': False, 'restrict_imports': ''}

        Args:
            connector: connector name (e.g. ``"code-snippet"``).
            version: connector version (resolved from configured instance if omitted).

        Returns:
            A dict with every config field populated with its default value,
            including onchange-revealed sub-fields for the default selections.
        """
        return self._materialize_config(self.config_schema(connector, version=version), {})

    def _materialize_config(self, fields: list[dict[str, Any]], overrides: dict[str, Any]) -> dict[str, Any]:
        """Resolve ``fields`` to a value map, honoring ``overrides`` and walking
        each chosen value's ``onchange`` branch so revealed fields are filled too."""
        out: dict[str, Any] = {}
        for field in fields:
            name = field.get("name")
            if not name:
                continue
            if name in overrides:
                value = overrides[name]
            else:
                value = field.get("value")
                if value is None:
                    # For select fields with options, default to the first option
                    # when no explicit value is declared.
                    if field.get("type") == "select" and field.get("options"):
                        opts = _option_values(field)
                        value = opts[0] if opts else ""
                    else:
                        value = _type_default(field.get("type"))
            out[name] = value
            branch = (field.get("onchange") or {}).get(_onchange_key(value))
            if isinstance(branch, list):
                out.update(self._materialize_config(branch, overrides))
        return out

    def required_config_fields(
        self, connector: str, config: dict[str, Any], *, version: str | None = None
    ) -> list[str]:
        """The config field names *required* given the selections in ``config``.

        Resolves ``select`` / ``checkbox`` ``onchange`` branches against the
        values already in ``config`` (so for FortiSIEM with ``fsm_type="FortiSIEM"``
        you get ``server``/``username``/``password``, and for ``code-snippet`` with
        ``allow_imports=False`` you get ``restrict_imports``). Use it to know which
        fields a user must supply.
        """
        required: list[str] = []

        def walk(fields: list[dict[str, Any]]) -> None:
            for field in fields:
                fname = field.get("name")
                if fname and field.get("required"):
                    required.append(fname)
                # onchange keys are strings ("true"/"false" for checkboxes); coerce
                # the config value the same way so checkbox branches aren't missed.
                branch = (field.get("onchange") or {}).get(_onchange_key(config.get(fname)))
                if isinstance(branch, list):
                    walk(branch)

        walk(self.config_schema(connector, version=version))
        return required

    def validate_config(
        self, connector: str, config: dict[str, Any], *, version: str | None = None
    ) -> ConfigValidationResult:
        """Check ``config`` against a connector's schema *before* saving it.

        Returns a :class:`~pyfsr.models.ConfigValidationResult` with:

        - ``missing`` — required fields absent or blank in ``config``.
        - ``invalid`` — fields with wrong values (bad select option, wrong type).
        - ``unknown`` — keys in ``config`` not declared by the active schema.
        - ``errors`` — one structured entry per problem with ``field``, ``code``,
          ``message``, and (for select fields) ``valid_options``.

        ``valid`` is ``True`` only when ``missing`` and ``invalid`` are empty.
        ``unknown`` keys are reported but don't make the config invalid.
        """
        missing: list[str] = []
        invalid: list[str] = []
        known: set[str] = set()
        errors: list[dict[str, Any]] = []
        self._collect_field_problems(
            self.config_schema(connector, version=version),
            config,
            condition=None,
            missing=missing,
            invalid=invalid,
            known=known,
            errors=errors,
        )
        unknown = [k for k in config if k not in known]
        for key in unknown:
            errors.append(
                {
                    "field": key,
                    "code": "unknown_field",
                    "message": (
                        f"{key!r} is not a recognized configuration field (typo, or gated behind a different selection)"
                    ),
                }
            )
        return ConfigValidationResult(
            valid=not missing and not invalid,
            missing=missing,
            invalid=invalid,
            unknown=unknown,
            errors=errors,
        )

    def _collect_field_problems(
        self,
        fields: list[dict[str, Any]],
        config: dict[str, Any],
        *,
        condition: dict[str, Any] | None,
        missing: list[str],
        invalid: list[str],
        known: set[str],
        errors: list[dict[str, Any]],
    ) -> None:
        """Walk a config schema collecting required/invalid/known field info.

        Recurses only into a ``select`` field's ``onchange`` branch that matches
        the value currently in ``config`` — so conditionally-revealed fields are
        evaluated only when their controlling selection is active. ``condition``
        carries the controlling field that revealed the current branch, for
        guidance messages.
        """
        for field in fields:
            fname = field.get("name")
            if not fname:
                continue
            known.add(fname)
            ftype = (field.get("type") or "text").lower()
            value = config.get(fname)
            present = value is not None and value != ""

            if field.get("required") and not present:
                missing.append(fname)
                errors.append(
                    {
                        "field": fname,
                        "code": "missing_required",
                        "message": _missing_message(field, condition),
                    }
                )
            elif present:
                if ftype == "select" and field.get("options"):
                    allowed = _option_values(field)
                    if value not in allowed:
                        invalid.append(fname)
                        errors.append(
                            {
                                "field": fname,
                                "code": "invalid_option",
                                "message": (f"{_field_label(field)}: {value!r} is not a valid option"),
                                "valid_options": allowed,
                            }
                        )
                elif not _value_fits_type(ftype, value):
                    invalid.append(fname)
                    errors.append(
                        {
                            "field": fname,
                            "code": "wrong_type",
                            "message": (f"{_field_label(field)}: expected {ftype}, got {value!r}"),
                            "expected": ftype,
                        }
                    )

            branch = (field.get("onchange") or {}).get(_onchange_key(value))
            if isinstance(branch, list):
                self._collect_field_problems(
                    branch,
                    config,
                    condition={"name": fname, "label": _field_label(field), "value": value},
                    missing=missing,
                    invalid=invalid,
                    known=known,
                    errors=errors,
                )

    # ------------------------------------------------------------- configure
    def create_configuration(
        self,
        connector: str,
        config: dict[str, Any],
        *,
        name: str,
        version: str | None = None,
        default: bool = False,
        config_id: str | None = None,
        agent: str | None = None,
        validate: bool = True,
        autofill: bool = True,
        exist_ok: bool = False,
        refresh: bool = True,
    ) -> ConnectorConfig:
        """Create (or update) a connector configuration — write its credentials.

        Persists a named configuration for ``connector`` via
        ``POST /api/integration/configuration/`` (the same endpoint the UI's
        connector-config form uses). ``config`` is the connector's own field
        map — for ``fortinet-fortisiem`` that's
        ``{"server", "username", "password", "organization", "verify_ssl"}``;
        inspect :meth:`definition` (its ``config_schema``) for any connector's
        fields. Secrets (e.g. ``password``) are encrypted server-side, so always
        create configs through this API rather than writing the table directly.

        Args:
            connector: connector *name* (e.g. ``"fortinet-fortisiem"``).
            config: the connector's configuration field values.
            name: a label for this configuration (required; what the UI shows).
            version: connector version; resolved from an already-configured
                connector when omitted. Pass it explicitly the first time a
                connector is configured (``resolve_version`` only sees
                already-configured connectors). If invalid, the appliance falls
                back to the latest installed version.
            default: mark this the connector's default configuration.
            config_id: reuse a specific UUID — passing an existing config's id
                **updates** that configuration instead of creating a new one
                (the endpoint upserts on ``config_id``); omit to mint a new one.
            agent: run the connector on a remote *agent* (its uuid); omit to use
                the appliance's self-agent.
            validate: structurally check ``config`` against the connector's
                schema first (via :meth:`validate_config`) and raise on a missing
                required field — turns the server's opaque 500 into a clear
                error. Pass ``False`` to skip (default ``True``).
            autofill: fill any schema-defaulted fields ``config`` omits — including
                the ``onchange``-revealed sub-fields that are otherwise required
                only at *playbook runtime* (see :meth:`default_config`). Your
                explicit values always win. Pass ``False`` to send ``config``
                verbatim (default ``True``).
            exist_ok: when ``True``, if a configuration with the same ``name``
                already exists for this connector/agent pair, delegate to
                :meth:`upsert_configuration` instead of raising
                :exc:`~pyfsr.exceptions.ConfigurationExistsError` (default ``False``).
            refresh: drop the cached configured-connector listing afterwards so
                the new config is visible to :meth:`resolve_config` etc.
                (default ``True``).

        The integer ``connector`` id the endpoint requires (a name-only body
        500s) is resolved automatically from ``connector``.

        Returns:
            The persisted configuration record (including its ``config_id``).

        Raises:
            ValueError: if the connector isn't installed or ``version`` can't be
                resolved.
            ConfigValidationError: when ``validate=True`` and the configuration
                fails structural validation (missing required fields, invalid
                option values, or wrong field types). Includes field-level error
                details so callers can programmatically handle them.
            ConfigurationExistsError: when ``exist_ok=False`` (the default) and
                the server rejects the write with a unique constraint violation
                on ``(name, connector, agent)``.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} version unknown (not yet configured); pass version=")
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(
                f"{connector!r} is not installed; install it before configuring "
                "(client.connectors.install(name, version))"
            )
        if autofill:
            config = self._materialize_config(self.config_schema(connector, version=version), config)
        if validate:
            check = self.validate_config(connector, config, version=version)
            if not check.valid:
                # Convert to the new ConfigValidationError with structured errors
                msg = _format_validation_error(connector, check)
                raise ConfigValidationError(msg, errors=check.errors)
        body: dict[str, Any] = {
            "connector": connector_id,
            "connector_name": connector,
            "connector_version": version,
            "name": name,
            "default": default,
            "config": config,
        }
        if config_id is not None:
            body["config_id"] = config_id
        if agent is not None:
            body["agent"] = agent
        try:
            resp = self.client.post("/api/integration/configuration/", data=body)
        except APIError as e:
            # Catch unique constraint violations and offer exist_ok hint
            error_msg = (e.message or "").lower()
            if "unique" in error_msg and ("name" in error_msg or "must" in error_msg):
                if not exist_ok:
                    raise ConfigurationExistsError(connector, name, response=e.response, error_type=e.error_type) from e
                # exist_ok=True: delegate to upsert
                return self.upsert_configuration(
                    connector,
                    config,
                    name=name,
                    version=version,
                    default=default,
                    agent=agent,
                    validate=False,  # already validated
                    autofill=False,  # already autofilled
                )
            raise
        if refresh:
            self.clear_cache()
        raw = resp if isinstance(resp, dict) else {"result": resp}
        return ConnectorConfig.model_validate(raw)

    def update_configuration(
        self,
        connector: str,
        config_id: str,
        config: dict[str, Any],
        *,
        name: str,
        version: str | None = None,
        default: bool = False,
        agent: str | None = None,
        validate: bool = True,
        autofill: bool = True,
        refresh: bool = True,
    ) -> ConnectorConfig:
        """Update an existing connector configuration by ``config_id``.

        ``PUT /api/integration/configuration/{config_id}/`` (the POST create path
        *rejects* a known ``config_id`` rather than upserting). Use this to
        rotate credentials on a configured connector — e.g. re-stamp a FortiSIEM
        ``password`` or a refreshed token. ``config`` is sent whole, so include
        every field, not just the changed one.

        Like :meth:`create_configuration`, the integer ``connector`` id is
        resolved automatically, and ``config`` is structurally validated first
        unless ``validate=False``.

        Args:
            connector: connector name.
            config_id: the UUID of the configuration to update.
            config: the new connector configuration field values.
            name: the configuration's label.
            version: connector version (resolved if omitted).
            default: mark this the connector's default configuration.
            agent: run the connector on a remote agent (omit to keep existing).
            validate: structurally check ``config`` against the schema first
                (default ``True``).
            autofill: fill any schema-defaulted fields ``config`` omits (default ``True``).
            refresh: drop the cached configured-connector listing afterwards
                (default ``True``).

        Returns:
            The updated :class:`~pyfsr.models.ConnectorConfig`.

        Raises:
            ValueError: if the connector isn't installed or version can't be resolved.
            ConfigValidationError: when ``validate=True`` and the configuration
                fails structural validation.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} version unknown; pass version=")
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        if autofill:
            config = self._materialize_config(self.config_schema(connector, version=version), config)
        if validate:
            check = self.validate_config(connector, config, version=version)
            if not check.valid:
                msg = _format_validation_error(connector, check)
                raise ConfigValidationError(msg, errors=check.errors)
        body: dict[str, Any] = {
            "connector": connector_id,
            "connector_name": connector,
            "connector_version": version,
            "name": name,
            "default": default,
            "config_id": config_id,
            "config": config,
        }
        if agent is not None:
            body["agent"] = agent
        resp = self.client.put(f"/api/integration/configuration/{config_id}/", data=body)
        if refresh:
            self.clear_cache()
        raw = resp if isinstance(resp, dict) else {"result": resp}
        # NB: FortiSOAR 8.0's PUT echoes the saved row but puts an async
        # op-envelope in ``status`` (``{"status":"finished","message":...}``)
        # instead of 7.x's int active-flag. ``ConnectorConfig.status`` tolerates
        # this (coerced to None) — see its validator in models/_integration.py.
        return ConnectorConfig.model_validate(raw)

    def delete_configuration(self, config_id: str, *, refresh: bool = True) -> None:
        """Delete a connector configuration by id
        (``DELETE /api/integration/configuration/{config_id}/``).

        The trailing slash is mandatory — without it the gateway rejects the
        call with ``403 Could not validate HMAC fingerprint``.
        """
        self.client.delete(f"/api/integration/configuration/{config_id}/")
        if refresh:
            self.clear_cache()

    # --------------------------------------------------------- connector studio
    # The Connector Studio development workspace: list checked-out connectors,
    # open one for editing, read/write its source files, then publish to land
    # the changes on the running appliance. ``entity_id`` is the dev-workspace
    # entity id (from :meth:`dev_list`), not the integer install id.
    _DEV_BASE = "/api/integration/connector/development/entity"

    def dev_list(self) -> list[dict[str, Any]]:
        """List connectors checked out into the Connector Studio dev workspace.

        ``GET /api/integration/connector/development/entity/`` — the same set
        shown in the Studio's left-hand tree. Returns the ``data[]`` entries.
        """
        resp = self.client.get(f"{self._DEV_BASE}/") or {}
        if isinstance(resp, dict) and resp.get("data"):
            return resp["data"]
        return extract_members(resp)

    def dev_edit(self, entity_id: str) -> dict[str, Any]:
        """Open a dev-workspace connector for editing (Studio's *Edit* action).

        ``POST .../entity/{id}/`` with ``{"edit_repo_connector": true}``. Returns
        the entity's full operations + configuration schema + file tree. Follow
        with :meth:`dev_read_file`/:meth:`dev_write_file`, then :meth:`dev_publish`.
        """
        resp = self.client.post(f"{self._DEV_BASE}/{entity_id}/", data={"edit_repo_connector": True})
        return resp if isinstance(resp, dict) else {"result": resp}

    def dev_read_file(self, entity_id: str, xpath: str) -> dict[str, Any]:
        """Read one source file from a dev-workspace connector.

        ``POST .../entity/{id}/files/`` with ``{"xpath": ...}``. ``xpath`` is
        relative to the connector's dev-workspace root and starts with
        ``/<name>_<vtag>_dev/...``. Returns the file payload.
        """
        resp = self.client.post(f"{self._DEV_BASE}/{entity_id}/files/", data={"xpath": xpath})
        return resp if isinstance(resp, dict) else {"result": resp}

    def dev_write_file(self, entity_id: str, file_data: dict[str, Any]) -> dict[str, Any]:
        """Write one source file in a dev-workspace connector (Studio *Save*).

        ``PUT .../entity/{id}/files/`` with ``{"fileData": ...}``. ``file_data``
        is the editor's file object (path + contents). Saved changes are staged
        in the workspace and do **not** affect playbook execution until
        :meth:`dev_publish` is called.
        """
        resp = self.client.put(f"{self._DEV_BASE}/{entity_id}/files/", data={"fileData": file_data})
        return resp if isinstance(resp, dict) else {"result": resp}

    def dev_publish(
        self,
        entity_id: str,
        *,
        replace: bool = False,
        discard: bool = False,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Publish a dev-workspace connector onto the running appliance.

        ``POST .../entity/{id}/publish/``. Lands the workspace contents into the
        live installed-connectors area and refreshes the integrations service so
        subsequent playbook runs pick up the new code immediately. ``replace``
        overwrites an existing installed version of the same name + version.
        ``discard`` controls the dev-workspace twin's lifecycle (not whether
        edits are published). This is also the supported escape hatch when a
        same-version tgz upload left stale code cached in the integrations
        service (the standard ``$replace=true`` install path does not refresh it).
        """
        resp = self.client.post(
            f"{self._DEV_BASE}/{entity_id}/publish/",
            data={"replace": replace, "discard": discard},
        )
        if refresh:
            self.clear_cache()
        return resp if isinstance(resp, dict) else {"result": resp}

    def dev_delete(self, entity_id: str, *, refresh: bool = True) -> None:
        """Delete a dev-workspace connector twin (Studio *discard*).

        ``DELETE .../entity/{id}/``. Use to tear down an orphaned ``_dev``
        workspace left by a failed :meth:`dev_publish` — an unreadable file in
        an orphaned ``_dev`` dir can wedge DAS's HA file-sync, so cleanup matters.
        """
        self.client.delete(f"{self._DEV_BASE}/{entity_id}/")
        if refresh:
            self.clear_cache()

    def republish(self, connector: str, *, replace: bool = True, discard: bool = True) -> dict[str, Any]:
        """Recycle the integrations workers onto a connector's installed code.

        A same-version ``$replace`` tgz install (see :meth:`install_from_file`)
        writes the new files but does **not** refresh the long-lived integrations
        uwsgi workers, so they keep serving the previously-imported module object
        from ``sys.modules`` (the "ghost bytecode" bug: a request randomly hits a
        stale worker and same-named-module edits silently don't take). This is the
        supported, SSH-free recycle: open the installed connector in the Connector
        Studio dev workspace (cloning the current installed state), then publish
        that twin back — which copies it into the live dir **and** touches the dev
        config ini, recycling every worker within ~5s.

        ``discard`` makes a *successful* publish destroy the dev twin server-side
        so no orphan ``_dev`` dir is left; on a publish failure the twin is
        deleted explicitly here (an unreadable file in an orphaned ``_dev`` can
        wedge HA file-sync). ``replace`` overwrites the same name+version.

        Run this after a same-version ``install_from_file(..., replace=True)`` so
        every worker imports the new code. Returns ``{"ok": True, "dev_id": ...}``.

        Raises ``ValueError`` if ``connector`` isn't installed, or re-raises the
        publish error (after cleaning up the twin) if the publish itself fails.
        """
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        # Entering edit mode clones the installed tree into the dev workspace.
        dev = self.dev_edit(connector_id)
        dev_id = dev.get("id")
        # Defensive: edit-mode sometimes echoes the installed id rather than the
        # twin's — find the real dev twin (development=true) by name.
        if dev_id == connector_id or not dev.get("development"):
            twin = next(
                (m for m in self.dev_list() if m.get("name") == connector and m.get("development")),
                None,
            )
            dev_id = (twin or {}).get("id", dev_id)
        if not dev_id:
            raise RuntimeError(f"could not resolve the dev-workspace twin for {connector!r}")
        try:
            self.dev_publish(dev_id, replace=replace, discard=discard)
        except Exception:
            # Publish didn't run its discard (failed/timed out) — delete the twin
            # explicitly so no `_dev` dir is left to wedge HA file-sync.
            try:
                self.dev_delete(dev_id)
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
            raise
        return {"ok": True, "dev_id": dev_id}

    def _find_configuration_by_name(self, connector: str, name: str) -> dict[str, Any] | None:
        """The connector's configuration row matching ``name`` (carrying
        ``config_id`` + ``agent``), or ``None``. Reads :meth:`connector_detail`,
        the only view that lists full config rows."""
        try:
            detail = self.connector_detail(connector)
        except ValueError:
            return None
        return next((c for c in (detail.get("configuration") or []) if c.get("name") == name), None)

    def upsert_configuration(
        self,
        connector: str,
        config: dict[str, Any],
        *,
        name: str,
        version: str | None = None,
        default: bool = False,
        agent: str | None = None,
        validate: bool = True,
        autofill: bool = True,
    ) -> ConnectorConfig:
        """Create a named configuration, or update it in place if one already
        exists with the same ``name`` — the idempotent write the UI's *Save*
        button performs, safe to re-run from a deploy script.

        Finds an existing config by ``name`` (via :meth:`connector_detail`) and
        ``PUT``s to its ``config_id`` (preserving the existing ``agent`` unless
        ``agent`` is given), else ``POST``s a new one. Unlike calling
        :meth:`create_configuration` twice — which 400s on
        ``"name, connector, agent must be unique"`` — this updates the second time.

        Tolerates the platform's *persisted-despite-500* case: a connector's own
        ``on_add_config`` / ``on_update_config`` hook can raise **after** the row
        is written (e.g. a post-save warmup), surfacing a 500 even though the
        config saved. On a write error this re-fetches by ``name`` and returns the
        row if it landed, rather than failing a re-runnable deploy.

        Args:
            connector: connector name.
            config: the connector's configuration field values.
            name: a label for this configuration (required).
            version: connector version (resolved if omitted).
            default: mark this the connector's default configuration.
            agent: run the connector on a remote agent (omit for self-agent).
            validate: structurally check ``config`` against the schema first
                (default ``True``).
            autofill: fill any schema-defaulted fields ``config`` omits,
                including onchange-revealed sub-fields (default ``True``).

        Returns:
            The persisted :class:`~pyfsr.models.ConnectorConfig`.

        Raises:
            ValueError: if the connector isn't installed or version can't be resolved.
            ConfigValidationError: when ``validate=True`` and the configuration
                fails structural validation.
        """
        version = version or self.resolve_version(connector)
        existing = self._find_configuration_by_name(connector, name)

        def _write() -> ConnectorConfig:
            if existing:
                return self.update_configuration(
                    connector,
                    existing.get("config_id") or existing.get("id"),
                    config,
                    name=name,
                    version=version,
                    default=default,
                    agent=agent if agent is not None else existing.get("agent"),
                    validate=validate,
                    autofill=autofill,
                )
            return self.create_configuration(
                connector,
                config,
                name=name,
                version=version,
                default=default,
                agent=agent,
                validate=validate,
                autofill=autofill,
            )

        try:
            return _write()
        except Exception:
            # The write may have persisted before a post-save hook raised — verify
            # by re-fetch rather than trusting the status code.
            confirmed = self._find_configuration_by_name(connector, name)
            if confirmed is not None:
                self.clear_cache()
                return ConnectorConfig.model_validate(confirmed)
            raise

    def ensure_configured(
        self,
        connector: str,
        config: dict[str, Any],
        *,
        config_name: str,
        version: str | None = None,
        default: bool = True,
        agent: str | None = None,
        validate: bool = True,
        autofill: bool = True,
        wait: bool = True,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> ConnectorConfig:
        """Ensure ``connector`` is installed **and** has the named configuration.

        Consolidates the common setup sequence — "install from Content Hub if it
        isn't here yet, then create-or-update the config" — into one idempotent
        call, joining the existing :meth:`ensure_version` in the ``ensure_*``
        family. Re-running it is safe: an already-installed connector is not
        reinstalled, and :meth:`upsert_configuration` updates the named config in
        place rather than duplicating it.

        ``version`` is only needed to *install* a missing connector (it is passed
        to :meth:`install`); when the connector is already installed it may be
        omitted and the configuration is applied against the installed version. If
        the connector is absent and ``version`` is ``None``, a clear ``ValueError``
        is raised rather than guessing.

        ``config_name`` is the configuration's display name (passed through as
        ``name=`` to :meth:`upsert_configuration`); ``default=True`` (the default)
        makes it the connector's default config so a config-less connector step
        picks it up. Returns the resulting :class:`~pyfsr.models.ConnectorConfig`.

        Example::

            cfg = client.connectors.ensure_configured(
                "servicenow",
                {"server_url": "...", "username": "...", "password": "..."},
                config_name="pilot",
                version="1.0.0",
            )
        """
        if self.resolve_connector_id(connector) is None:
            if not version:
                raise ValueError(
                    f"connector {connector!r} is not installed; pass version= to install it from the Content Hub"
                )
            self.install(connector, version, wait=wait, interval=interval, timeout=timeout)
        return self.upsert_configuration(
            connector,
            config,
            name=config_name,
            version=version,
            default=default,
            agent=agent,
            validate=validate,
            autofill=autofill,
        )

    # ------------------------------------------------------------- execute
    def execute(
        self,
        connector: str,
        operation: str,
        *,
        version: str | None = None,
        config_id: str | None = None,
        config_name: str | None = None,
        params: dict[str, Any] | None = None,
        config: str | None = None,
    ) -> ExecuteResult:
        """Run a single connector operation via ``POST /api/integration/execute/``.

        ``version`` and ``config_id`` are resolved from the configured connector
        when omitted (``config_name`` selects a non-default configuration by
        name). ``config_id`` is a configuration **UUID** — the ``config_id`` of
        the :class:`~pyfsr.models.ConnectorConfig` that
        :meth:`upsert_configuration` returns.

        .. deprecated::
           The ``config`` keyword is deprecated in favour of ``config_id``. It
           took a UUID, while ``config`` on :meth:`upsert_configuration` /
           :meth:`create_configuration` / :meth:`validate_config` takes the field
           **map** — one name, two types. ``config`` still works; passing both
           raises.

        Returns a typed :class:`~pyfsr.models.ExecuteResult` — dict-compatible
        (``result["data"]`` still works), with a ``.ok`` property for the
        recurring ``status == "Success"`` check.

        See the module-level warning: for agent-bound connectors this call is
        fire-and-forget and ``data`` comes back empty — that is not a failure.

        Live-verified on FortiSOAR 8.0.0-6034 against ``cisa-advisory``'s
        ``get_known_exploited_vulnerability_cves`` (a public, read-only,
        parameter-less feed lookup — safe to demo against a real connector,
        no side effect beyond an outbound GET to CISA's public catalog):

            >>> client = demo_client()
            >>> result = client.connectors.execute(
            ...     "cisa-advisory", "get_known_exploited_vulnerability_cves"
            ... )
            >>> result.ok
            True
            >>> result.data["title"]
            'CISA Catalog of Known Exploited Vulnerabilities'
            >>> result.data["vulnerabilities"][0]["cveID"]
            'CVE-2026-45659'
        """
        config_id = _resolve_config_id_kwarg(config_id, config)
        version = version or self.resolve_version(connector)
        if config_id is None and (config_name is not None or self._configured is not None):
            config_id = self.resolve_config(connector, config_name)
        body = {
            "connector": connector,
            "operation": operation,
            "version": version or "",
            # The wire field stays "config" — this rename is client-side only.
            "config": config_id or "",
            "params": params or {},
        }
        resp = self.client.post("/api/integration/execute/", data=body)
        return ExecuteResult.model_validate(resp if isinstance(resp, dict) else {"result": resp})


def _import_job_id(resp: dict[str, Any]) -> str | None:
    """Pull the import-job id out of a Content-Hub install response.

    The install reply is the ``SolutionPack`` record; its async install job is
    the nested ``importJob`` object (``{"@id": "/api/3/import_jobs/<uuid>",
    "uuid": ...}``). Falls back to a top-level ``import_jobs`` IRI if present.
    Note: the response's *top-level* ``uuid`` is the solution pack's, not the
    job's — don't use it.
    """
    job = resp.get("importJob")
    if isinstance(job, dict):
        uuid = job.get("uuid")
        if isinstance(uuid, str) and uuid:
            return uuid
        iri = job.get("@id")
        if isinstance(iri, str) and iri:
            return iri.rstrip("/").split("/")[-1]
    iri = resp.get("@id")
    if isinstance(iri, str) and "import_jobs" in iri:
        return iri.rstrip("/").split("/")[-1]
    return None
