"""Connector discovery, health, and operation execution.

Wraps FortiSOAR's ``/api/integration`` surface so callers don't hand-build
execute payloads or hunt for a connector's configured version / config UUID.
Covers discovery, healthcheck, operation execution, and writing a connector's
*configuration* (its credentials) — see :meth:`ConnectorsAPI.create_configuration`.

Accessed as ``client.connectors``.

Example:
    >>> client.connectors.list_configured()           # what's installed + configured
    >>> client.connectors.install("fortinet-fortisiem", "6.1.0", wait=True)
    >>> client.connectors.create_configuration(        # write FortiSIEM creds
    ...     "fortinet-fortisiem",
    ...     {"fsm_type": "FortiSIEM", "server": "https://siem.example.com",
    ...      "username": "admin", "password": "secret",
    ...      "organization": "Super", "verify_ssl": True},
    ...     name="prod", version="6.1.0", default=True)
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

import mimetypes
import re
import time
from pathlib import Path
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
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"connector bundle not found: {file_path}")
        params = {"$type": "connector"}
        if replace:
            params["$replace"] = "true"
        mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        with open(file_path, "rb") as f:
            resp = self.client.post(
                "/api/3/solutionpacks/install",
                files={"file": (file_path.name, f, mime_type)},
                params=params,
                headers={"Content-Type": None},
            )
        resp = resp if isinstance(resp, dict) else {"result": resp}
        self.clear_cache()
        if not wait:
            return resp
        job_id = _import_job_id(resp)
        if not job_id:
            return resp
        return self.wait_for_install(job_id, interval=interval, timeout=timeout)

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
                        "id": m.get("id"),
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

    def list_configurations(
        self,
        *,
        name: str | None = None,
        active: bool | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """List connector configuration records via ``GET /api/integration/configuration/``.

        The dedicated, filterable configurations endpoint (distinct from the
        connector-derived view of :meth:`configurations`). Each entry carries
        ``id`` (int), ``config_id`` (uuid), ``connector`` (int connector id),
        ``agent`` (set when remote), and ``config`` (the field map). Filter with
        ``name`` (connector name) and/or ``active``. Returns the ``data[]`` array
        (this endpoint is the custom ``{status, totalItems, data[]}`` envelope,
        not Hydra).
        """
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if name is not None:
            params["name"] = name
        if active is not None:
            params["active"] = active
        resp = self.client.get("/api/integration/configuration/", params=params) or {}
        if isinstance(resp, dict):
            return resp.get("data") or []
        return resp if isinstance(resp, list) else []

    def _find_configured(self, connector: str) -> dict[str, Any] | None:
        return next((c for c in self.list_configured() if c.get("name") == connector), None)

    def find_installed_connectors(self, query: str) -> list[dict[str, Any]]:
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

        def norm(s: str) -> str:
            # fold case and treat '-', '_', and whitespace as interchangeable so
            # 'fortigate_firewall', 'FortiGate', and 'forti gate' all match.
            return re.sub(r"[-_\s]+", "-", (s or "").strip().lower())

        q = norm(query)
        hits = [
            c
            for c in self.list_configured()
            if q in norm(c.get("name")) or q in norm(c.get("label"))
        ]
        hits.sort(key=lambda c: norm(c.get("name")) != q)
        return hits

    def configurations(self, connector: str) -> list[dict[str, Any]]:
        """List a connector's configurations (``[{config_id, name, default}]``)."""
        hit = self._find_configured(connector)
        return hit["configurations"] if hit else []

    def resolve_version(self, connector: str) -> str | None:
        """The configured version of ``connector`` (``None`` if not configured)."""
        hit = self._find_configured(connector)
        return hit.get("version") if hit else None

    def resolve_connector_id(self, connector: str) -> int | None:
        """The integer install id of ``connector`` (``None`` if not installed).

        Required by :meth:`create_configuration` — the
        ``/api/integration/configuration/`` endpoint 500s on a name-only body
        and needs this numeric id.
        """
        hit = self._find_configured(connector)
        return hit.get("id") if hit else None

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

    def required_config_fields(
        self, connector: str, config: dict[str, Any], *, version: str | None = None
    ) -> list[str]:
        """The config field names *required* given the selections in ``config``.

        Resolves ``select`` ``onchange`` branches against the values already in
        ``config`` (so for FortiSIEM with ``fsm_type="FortiSIEM"`` you get
        ``server``/``username``/``password``, and with ``"FortiSOC"`` you get
        ``server``/``is_fsoc``). Use it to know which fields a user must supply.
        """
        required: list[str] = []

        def walk(fields: list[dict[str, Any]]) -> None:
            for field in fields:
                fname = field.get("name")
                if fname and field.get("required"):
                    required.append(fname)
                branch = (field.get("onchange") or {}).get(config.get(fname))
                if isinstance(branch, list):
                    walk(branch)

        walk(self.config_schema(connector, version=version))
        return required

    def validate_config(
        self, connector: str, config: dict[str, Any], *, version: str | None = None
    ) -> dict[str, Any]:
        """Check ``config`` against a connector's schema *before* saving it.

        Returns ``{"valid": bool, "missing": [...], "unknown": [...]}``:

        - ``missing`` — required fields (after resolving ``select`` ``onchange``
          branches) absent or blank in ``config``.
        - ``unknown`` — keys in ``config`` that no active schema field declares
          (often a typo or a field gated behind a different ``select`` value).

        This is a *structural* check (presence of required fields), not a live
        credential test — follow a clean result with :meth:`healthcheck`. Catches
        exactly the class of error that makes ``create_configuration`` 500
        (e.g. omitting FortiSIEM's ``fsm_type``).
        """
        missing: list[str] = []
        known: set[str] = set()
        self._walk_fields(self.config_schema(connector, version=version), config, missing, known)
        unknown = [k for k in config if k not in known]
        return {"valid": not missing, "missing": missing, "unknown": unknown}

    def _walk_fields(
        self,
        fields: list[dict[str, Any]],
        config: dict[str, Any],
        missing: list[str],
        known: set[str] | None,
    ) -> None:
        """Walk a config schema, collecting required-but-missing field names.

        Recurses into a ``select`` field's ``onchange`` branch that matches the
        value currently in ``config``. When ``known`` is given, every field name
        encountered along active branches is recorded there.
        """
        for field in fields:
            fname = field.get("name")
            if not fname:
                continue
            if known is not None:
                known.add(fname)
            value = config.get(fname)
            if field.get("required") and (value is None or value == ""):
                missing.append(fname)
            onchange = field.get("onchange") or {}
            branch = onchange.get(value)
            if isinstance(branch, list):
                self._walk_fields(branch, config, missing, known)

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
            validate: structurally check ``config`` against the connector's
                schema first (via :meth:`validate_config`) and raise on a missing
                required field — turns the server's opaque 500 into a clear
                error. Pass ``False`` to skip (default ``True``).
            refresh: drop the cached configured-connector listing afterwards so
                the new config is visible to :meth:`resolve_config` etc.
                (default ``True``).

        The integer ``connector`` id the endpoint requires (a name-only body
        500s) is resolved automatically from ``connector``.

        Returns:
            The persisted configuration record (including its ``config_id``).

        Raises:
            ValueError: if the connector isn't installed, ``version`` can't be
                resolved, or (when ``validate``) a required field is missing.
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
        if validate:
            check = self.validate_config(connector, config, version=version)
            if not check["valid"]:
                raise ValueError(
                    f"{connector!r} config is missing required field(s): "
                    f"{', '.join(check['missing'])} "
                    "(see client.connectors.config_schema(name))"
                )
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
        validate: bool = True,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Update an existing connector configuration by ``config_id``.

        ``PUT /api/integration/configuration/{config_id}/`` (the POST create path
        *rejects* a known ``config_id`` rather than upserting). Use this to
        rotate credentials on a configured connector — e.g. re-stamp a FortiSIEM
        ``password`` or a refreshed token. ``config`` is sent whole, so include
        every field, not just the changed one.

        Like :meth:`create_configuration`, the integer ``connector`` id is
        resolved automatically, and ``config`` is structurally validated first
        unless ``validate=False``.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} version unknown; pass version=")
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        if validate:
            check = self.validate_config(connector, config, version=version)
            if not check["valid"]:
                raise ValueError(
                    f"{connector!r} config is missing required field(s): "
                    f"{', '.join(check['missing'])} "
                    "(see client.connectors.config_schema(name))"
                )
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
        return resp if isinstance(resp, dict) else {"result": resp}

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
        if isinstance(resp, dict):
            return resp.get("data") or resp.get("hydra:member") or []
        return resp if isinstance(resp, list) else []

    def dev_edit(self, entity_id: str) -> dict[str, Any]:
        """Open a dev-workspace connector for editing (Studio's *Edit* action).

        ``POST .../entity/{id}/`` with ``{"edit_repo_connector": true}``. Returns
        the entity's full operations + configuration schema + file tree. Follow
        with :meth:`dev_read_file`/:meth:`dev_write_file`, then :meth:`dev_publish`.
        """
        resp = self.client.post(
            f"{self._DEV_BASE}/{entity_id}/", data={"edit_repo_connector": True}
        )
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
