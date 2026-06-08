"""Connector discovery, health, and operation execution.

Wraps FortiSOAR's ``/api/integration`` surface so callers don't hand-build
execute payloads or hunt for a connector's configured version / config UUID.

Accessed as ``client.connectors``.

Example:
    >>> client.connectors.list_configured()           # what's installed + configured
    >>> client.connectors.healthcheck("virustotal")    # is the upstream reachable?
    >>> client.connectors.execute(
    ...     "virustotal", "get_reputation_ip", params={"ip": "8.8.8.8"})
    {'operation': 'get_reputation_ip', 'status': 'Success', 'data': {...}}

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

from typing import Any

from .base import BaseAPI


class ConnectorsAPI(BaseAPI):
    """Live connector listing, healthcheck, and operation execution."""

    def __init__(self, client):
        super().__init__(client)
        self._configured: list[dict[str, Any]] | None = None

    def clear_cache(self) -> None:
        """Drop the cached configured-connector listing."""
        self._configured = None

    # ------------------------------------------------------------- discovery
    def list_configured(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Installed + configured connectors as
        ``[{name, version, label, configurations:[{config_id, name, default}]}, ...]``.

        Cached after the first call; pass ``refresh=True`` to re-fetch.
        """
        if self._configured is not None and not refresh:
            return self._configured
        resp = self.client.get("/api/integration/connectors/", params={"$limit": 300})
        out: list[dict[str, Any]] = []
        for m in (resp or {}).get("data") or []:
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
