"""Log forwarding -- ``client.log_forwarding``.

Wraps the *Settings → Log Forwarding* page, which sends appliance logs to an
external syslog server. The UI talks to the gateway config store at
``/api/gateway/config/syslog``:

* ``GET``            -- list of configs (``[]`` when none is set up)
* ``POST``           -- create; the body carries a client-generated ``uuid``
* ``PUT``            -- update; the body is the whole config object
* ``DELETE /{uuid}`` -- remove (the UI does this when you untick *enabled*)

The appliance renders each config into rsyslog (``/etc/rsyslog.d``), the same
mechanism as ``csadm log forward``; the UI and CLI configs share one store.

Field rules (from the gateway's ``SyslogConfig`` bean validation, 8.0.1)::

    field                 required  constraint
    uuid                  yes       UUID; the client generates it on create
    config_name           yes       non-blank, ^[a-zA-Z0-9]*$ (no spaces, dashes or dots)
    server                yes       FQDN, "localhost" or IPv4; no IPv6, no single-label names
    port                  yes       1-65535
    protocol              no        tcp | udp | relp
    syslog_filters        no        list of application | audit
    enabled               no        bool
    tls                   no        bool; csadm then needs ca_cert
    ca_cert/client_cert/
      client_key          no        PEM text, max 10000 chars each
    audit_filter_setting  no        the UI's audit-rule object

A body that fails validation returns "One or more required inputs are missing."

``syslog_filters`` picks what is forwarded:

* ``"application"`` -- FortiSOAR service logs **and** the OS syslog stream
  (sshd logins, sudo, ...). Commands typed in an interactive shell are not in
  the OS syslog stream unless shell/exec auditing is enabled on the appliance.
* ``"audit"`` -- the FortiSOAR application audit log (record/playbook/user
  activity), optionally narrowed by ``audit_filter_setting``.

Example:
    >>> cfg = client.log_forwarding.create("10.0.0.5", 514, config_name="siem",
    ...                                    protocol="tcp", filters=["application", "audit"])
    >>> client.log_forwarding.delete(cfg["uuid"])
"""

from __future__ import annotations

import re
import uuid as uuid_mod
from typing import Any

from .base import BaseAPI

LOG_FILTERS = ("application", "audit")
PROTOCOLS = ("udp", "tcp", "relp")
# Mirrors the gateway's @Pattern constraints so bad input fails here, with a reason.
_CONFIG_NAME_RE = re.compile(r"^[a-zA-Z0-9]+$")
_SERVER_RE = re.compile(r"^(([a-zA-Z0-9][-a-zA-Z0-9]*\.)+[a-zA-Z]{2,}|localhost|\d{1,3}(\.\d{1,3}){3})$")


class LogForwardingAPI(BaseAPI):
    """Create, read, update, and delete syslog forwarding configs."""

    _ENDPOINT = "/api/gateway/config/syslog"

    def list(self) -> list[dict[str, Any]]:
        """Return every forwarding config (``[]`` when none exist)."""
        resp = self.client.get(self._ENDPOINT)
        return resp if isinstance(resp, list) else []

    def get(self, config_uuid: str) -> dict[str, Any] | None:
        """Return the config with ``config_uuid``, or ``None``."""
        return next((c for c in self.list() if c.get("uuid") == config_uuid), None)

    def create(
        self,
        server: str,
        port: int = 514,
        *,
        config_name: str,
        protocol: str = "udp",
        filters: list[str] | None = None,
        enabled: bool = True,
        tls: bool = False,
        ca_cert: str = "",
        client_cert: str = "",
        client_key: str = "",
        audit_filter_setting: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a forwarding destination.

        Args:
            server: Syslog server FQDN, ``localhost`` or IPv4 address (required).
            port: Server port, 1-65535 (required).
            config_name: Name for the config; letters and digits only (required).
            protocol: ``udp``, ``tcp`` or ``relp``.
            filters: Any of ``application`` / ``audit``; defaults to ``["application"]``
                like the UI.
            enabled: Whether forwarding is active.
            tls: Use TLS (requires ``ca_cert``).
            ca_cert: CA chain PEM text, for TLS.
            client_cert: Client certificate PEM text, for mutual TLS.
            client_key: Client key PEM text, for mutual TLS.
            audit_filter_setting: The UI's audit-rule object
                (``{"auditFilter": {"filters": [...]}}``); omit to forward all audit events.

        Returns:
            The created config as stored by the appliance.
        """
        if not _CONFIG_NAME_RE.match(config_name or ""):
            raise ValueError(f"config_name must be non-empty letters/digits only, got {config_name!r}")
        if not _SERVER_RE.match(server or ""):
            raise ValueError(f"server must be an FQDN, 'localhost' or an IPv4 address, got {server!r}")
        if not 1 <= int(port) <= 65535:
            raise ValueError(f"port must be 1-65535, got {port!r}")
        if tls and not ca_cert:
            raise ValueError("tls=True requires ca_cert")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol must be one of {PROTOCOLS}, got {protocol!r}")
        filters = list(filters) if filters else ["application"]
        bad = [f for f in filters if f not in LOG_FILTERS]
        if bad:
            raise ValueError(f"unknown filters {bad}; valid: {LOG_FILTERS}")
        payload: dict[str, Any] = {
            "uuid": str(uuid_mod.uuid4()),
            "config_name": config_name,
            "enabled": enabled,
            "server": server,
            "port": port,
            "protocol": protocol,
            "tls": tls,
            "ca_cert": ca_cert,
            "client_cert": client_cert,
            "client_key": client_key,
            "syslog_filters": filters,
        }
        if audit_filter_setting is not None:
            payload["audit_filter_setting"] = audit_filter_setting
        resp = self.client.post(self._ENDPOINT, data=payload)
        if isinstance(resp, list) and resp:
            return resp[0]
        return self.get(payload["uuid"]) or payload

    def update(self, config_uuid: str, **changes: Any) -> dict[str, Any]:
        """Change fields on an existing config (read, merge, PUT the whole object).

        Raises:
            KeyError: no config has ``config_uuid``.
        """
        current = self.get(config_uuid)
        if current is None:
            raise KeyError(f"no log-forwarding config with uuid {config_uuid!r}")
        if "filters" in changes:
            changes["syslog_filters"] = changes.pop("filters")
        body = {**current, **changes, "uuid": config_uuid}
        return self.client.put(self._ENDPOINT, data=body)

    def delete(self, config_uuid: str) -> Any:
        """Remove a forwarding config."""
        return self.client.delete(f"{self._ENDPOINT}/{config_uuid}")
