"""Connector discovery, health, and operation execution.

Wraps FortiSOAR's ``/api/integration`` surface so callers don't hand-build
execute payloads or hunt for a connector's configured version / config UUID.
Covers discovery, healthcheck, operation execution, and writing a connector's
*configuration* (its credentials) — see :meth:`ConnectorsAPI.create_configuration`.

Accessed as ``client.connectors``.

Example:
    >>> client.connectors.list_configured()           # what's installed + configured
    >>> client.connectors.create_configuration(        # write FortiSIEM creds
    ...     "fortinet-fortisiem",
    ...     {"server": "https://siem.example.com", "username": "admin",
    ...      "password": "secret", "organization": "Super", "verify_ssl": True},
    ...     name="prod", version="5.2.1", default=True)
    {'config_id': 'a7c7df29-...', ...}
    >>> client.connectors.healthcheck("fortinet-fortisiem")  # is the upstream reachable?
    >>> client.connectors.execute(
    ...     "virustotal", "get_reputation_ip", params={"ip": "8.8.8.8"})
    {'operation': 'get_reputation_ip', 'status': 'Success', 'data': {...}}

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

import time
from typing import Any

from .base import BaseAPI

#: Import-job statuses that mean a Content-Hub install has stopped running.
_INSTALL_TERMINAL = frozenset({"import complete", "completed", "failed", "error"})

#: Fields worth fetching when polling an install/import job's progress.
_INSTALL_FIELDS = "errorMessage,status,progressPercent,file,currentlyImporting,options"


class ConnectorsAPI(BaseAPI):
    """Live connector listing, healthcheck, and operation execution."""

    def __init__(self, client):
        super().__init__(client)
        self._configured: list[dict[str, Any]] | None = None

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
        resp = self.client.post(
            "/api/3/solutionpacks/install", data={"name": name, "version": version}
        )
        resp = resp if isinstance(resp, dict) else {"result": resp}
        if not wait:
            return resp
        job_id = _import_job_id(resp)
        if not job_id:
            return resp
        final = self.wait_for_install(job_id, interval=interval, timeout=timeout)
        if str(final.get("status", "")).strip().lower() in _INSTALL_TERMINAL:
            self.clear_cache()
        return final

    def install_status(self, job_id: str) -> dict[str, Any]:
        """Fetch a connector install's import-job progress.

        ``GET /api/3/import_jobs/{job_id}`` (selecting just the progress fields).
        Returns ``{status, progressPercent, errorMessage, currentlyImporting,
        ...}``; ``status == "Import Complete"`` means the install finished.
        """
        resp = self.client.get(
            f"/api/3/import_jobs/{job_id}", params={"__selectFields": _INSTALL_FIELDS}
        )
        return resp if isinstance(resp, dict) else {"result": resp}

    def wait_for_install(
        self, job_id: str, *, interval: float = 3.0, timeout: float = 300.0
    ) -> dict[str, Any]:
        """Poll an install import job until it reaches a terminal status.

        Returns the latest :meth:`install_status` payload. On timeout, returns
        the last poll with a non-terminal ``status`` rather than raising.
        """
        deadline = time.monotonic() + timeout
        status = self.install_status(job_id)
        while (
            str(status.get("status", "")).strip().lower() not in _INSTALL_TERMINAL
            and time.monotonic() < deadline
        ):
            time.sleep(interval)
            status = self.install_status(job_id)
        return status

    # ------------------------------------------------------------- discovery
    def list_configured(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Installed + configured connectors as
        ``[{name, version, label, configurations:[{config_id, name, default}]}, ...]``.

        Cached after the first call; pass ``refresh=True`` to re-fetch.
        """
        if self._configured is not None and not refresh:
            return self._configured
        # The endpoint pages at ``page_size`` (default 30) and ignores ``$limit``
        # — walk every page so a connector past the first 30 isn't silently
        # dropped (which would make resolve_version/healthcheck miss it).
        out: list[dict[str, Any]] = []
        page = 1
        page_size = 100
        while True:
            resp = (
                self.client.get(
                    "/api/integration/connectors/",
                    params={"page": page, "page_size": page_size},
                )
                or {}
            )
            data = resp.get("data") or []
            for m in data:
                out.append(
                    {
                        "name": m.get("name"),
                        "version": m.get("version"),
                        "label": m.get("label") or m.get("title"),
                        "configurations": [
                            {
                                "config_id": c.get("config_id"),
                                "name": c.get("name"),
                                "default": bool(c.get("default")),
                            }
                            for c in (m.get("configuration") or [])
                        ],
                    }
                )
            total = resp.get("totalItems")
            if not data:
                break
            if total is not None:
                if len(out) >= total:
                    break
            elif len(data) < page_size:
                break
            page += 1
        self._configured = out
        return out

    def _find_configured(self, connector: str) -> dict[str, Any] | None:
        return next((c for c in self.list_configured() if c.get("name") == connector), None)

    def configurations(self, connector: str) -> list[dict[str, Any]]:
        """List a connector's configurations (``[{config_id, name, default}]``)."""
        hit = self._find_configured(connector)
        return hit["configurations"] if hit else []

    def resolve_version(self, connector: str) -> str | None:
        """The configured version of ``connector`` (``None`` if not configured)."""
        hit = self._find_configured(connector)
        return hit.get("version") if hit else None

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
            chosen = next((c for c in configs if c.get("name") == config_name), None)
        if chosen is None:
            chosen = next((c for c in configs if c.get("default")), None) or configs[0]
        return chosen.get("config_id") if chosen else None

    # ------------------------------------------------------------- health
    def healthcheck(
        self, connector: str, *, version: str | None = None, config: str | None = None
    ) -> dict[str, Any]:
        """Live-check whether a connector configuration is reachable.

        Returns the server's healthcheck payload (typically
        ``{status, message, ...}``); ``status="Available"`` is green. A 404 is
        normalized to ``{status: "no-config", http_status: 404}`` meaning the
        connector isn't configured on this instance.
        """
        version = version or self.resolve_version(connector)
        if not version:
            return {
                "name": connector,
                "status": "no-config",
                "message": f"{connector!r} is not configured on this instance",
            }
        path = f"/api/integration/connectors/healthcheck/{connector}/{version}/"
        params = {"config": config} if config else None
        try:
            return self.client.get(path, params=params)
        except Exception as e:  # noqa: BLE001 - normalize "not configured" to data
            resp = getattr(e, "response", None)
            if resp is not None and getattr(resp, "status_code", None) == 404:
                return {
                    "name": connector,
                    "version": version,
                    "status": "no-config",
                    "http_status": 404,
                    "message": "no configuration on this instance",
                }
            raise

    # ------------------------------------------------------------- definition
    def definition(self, connector: str, *, version: str | None = None) -> dict[str, Any]:
        """Fetch a connector's full definition (config schema + operations).

        ``POST /api/integration/connectors/<name>/<version>/?format=json`` (the
        endpoint forbids GET). ``version`` is resolved from the configured
        connector when omitted. The returned dict includes ``config_schema``,
        ``configuration``, and ``operations`` (each with ``operation``,
        ``title``, ``parameters``, ``output_schema``).

        Raises ``ValueError`` if the version can't be resolved.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(
                f"{connector!r} is not configured; pass version= to fetch its definition"
            )
        return self.client.post(
            f"/api/integration/connectors/{connector}/{version}/?format=json", data={}
        )

    def operations(self, connector: str, *, version: str | None = None) -> list[dict[str, Any]]:
        """List a connector's operations (the ``operations`` of :meth:`definition`).

        Each entry carries ``operation`` (the api name), ``title``,
        ``description``, ``parameters``, and ``output_schema``.
        """
        defn = self.definition(connector, version=version)
        return defn.get("operations") or []

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
        refresh: bool = True,
    ) -> dict[str, Any]:
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
            refresh: drop the cached configured-connector listing afterwards so
                the new config is visible to :meth:`resolve_config` etc.
                (default ``True``).

        Returns:
            The persisted configuration record (including its ``config_id``).

        Raises:
            ValueError: if ``version`` is omitted and can't be resolved.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} version unknown (not yet configured); pass version=")
        body: dict[str, Any] = {
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
        resp = self.client.post("/api/integration/configuration/", data=body)
        if refresh:
            self.clear_cache()
        return resp if isinstance(resp, dict) else {"result": resp}

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
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Update an existing connector configuration by ``config_id``.

        Thin wrapper over :meth:`create_configuration` with ``config_id`` set —
        the ``POST /api/integration/configuration/`` endpoint upserts on it. Use
        this to rotate credentials on a configured connector (e.g. re-stamp a
        FortiSIEM ``password`` or refreshed token). ``config`` is sent whole, so
        include every field, not just the changed one.
        """
        return self.create_configuration(
            connector,
            config,
            name=name,
            version=version,
            default=default,
            config_id=config_id,
            agent=agent,
            refresh=refresh,
        )

    def delete_configuration(self, config_id: str, *, refresh: bool = True) -> None:
        """Delete a connector configuration by id
        (``DELETE /api/integration/configuration/{config_id}/``).

        The trailing slash is mandatory — without it the gateway rejects the
        call with ``403 Could not validate HMAC fingerprint``.
        """
        self.client.delete(f"/api/integration/configuration/{config_id}/")
        if refresh:
            self.clear_cache()

    def files(self, connector_id: str) -> dict[str, Any]:
        """Fetch a connector's source files (dev) via
        ``GET /api/integration/connector/<id>/files/``.

        ``connector_id`` is the connector's dev/install id (the ``id`` field of
        :meth:`definition`). Dev-only; raises on connectors without file access.
        """
        return self.client.get(f"/api/integration/connector/{connector_id}/files/")

    # ------------------------------------------------------------- execute
    def execute(
        self,
        connector: str,
        operation: str,
        *,
        version: str | None = None,
        config: str | None = None,
        config_name: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a single connector operation via ``POST /api/integration/execute/``.

        ``version`` and ``config`` are resolved from the configured connector
        when omitted (``config_name`` selects a non-default configuration by
        name). Returns the server payload, typically
        ``{operation, status, message, data}``.

        See the module-level warning: for agent-bound connectors this call is
        fire-and-forget and ``data`` comes back empty — that is not a failure.
        """
        version = version or self.resolve_version(connector)
        if config is None and (config_name is not None or self._configured is not None):
            config = self.resolve_config(connector, config_name)
        body = {
            "connector": connector,
            "operation": operation,
            "version": version or "",
            "config": config or "",
            "params": params or {},
        }
        return self.client.post("/api/integration/execute/", data=body)


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
