"""Environment-driven client configuration.

``EnvConfig.from_env()`` reads the ``FSR_*`` environment variables and
``EnvConfig.client()`` builds a ready :class:`~pyfsr.client.FortiSOAR` from them,
so an app (or the bundled MCP server) never hand-wires host/auth/port::

    from pyfsr.config import EnvConfig
    client = EnvConfig.from_env().client()

Recognized variables:

- ``FSR_BASE_URL`` — appliance host or URL (required; ``FSR_HOST`` also accepted).
- ``FSR_API_KEY`` — API-key auth, or ``FSR_USERNAME`` + ``FSR_PASSWORD``.
- ``FSR_PORT`` — optional port override.
- ``FSR_VERIFY_SSL`` — ``false``/``0``/``no``/``off`` disables TLS verification.
- ``FSR_SUPPRESS_INSECURE_WARNINGS`` — silence urllib3 warnings when SSL is off.
- ``FSR_TIMEOUT`` — per-request timeout in seconds (default 30).

This is the generic, infra-free counterpart of fsrpb's ``probes/_env.py`` (which
also carries SSH/e2e knobs that don't belong in the transport SDK).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .client import FortiSOAR

_FALSEY = {"0", "false", "no", "off", ""}


def _flag(env: dict[str, str], name: str, default: str) -> bool:
    """Interpret an ``FSR_*`` env flag as a bool (falsey-ish strings → False)."""
    return env.get(name, default).strip().lower() not in _FALSEY


@dataclass
class EnvConfig:
    """Resolved client configuration (host, auth, transport knobs).

    Build it with :meth:`from_env`, then call :meth:`client` for a
    :class:`~pyfsr.client.FortiSOAR`. ``auth`` is the value passed straight to
    the client: an API-key string, or a ``(username, password)`` tuple.
    """

    base_url: str
    auth: str | tuple[str, str]
    verify_ssl: bool = True
    suppress_insecure_warnings: bool = False
    port: int | None = None
    timeout: int = 30

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> EnvConfig:
        """Build configuration from ``FSR_*`` environment variables.

        Raises ``ValueError`` with an actionable message when the host or auth
        is missing. Pass ``env`` to read from a dict instead of ``os.environ``.
        """
        env = env if env is not None else dict(os.environ)
        base_url = (env.get("FSR_BASE_URL") or env.get("FSR_HOST") or "").strip()
        if not base_url:
            raise ValueError("FSR_BASE_URL (or FSR_HOST) is required")

        api_key = (env.get("FSR_API_KEY") or "").strip()
        username = (env.get("FSR_USERNAME") or "").strip()
        password = env.get("FSR_PASSWORD") or ""
        if api_key:
            auth: str | tuple[str, str] = api_key
        elif username and password:
            auth = (username, password)
        else:
            raise ValueError("set FSR_API_KEY, or both FSR_USERNAME and FSR_PASSWORD")

        port_raw = (env.get("FSR_PORT") or "").strip()
        timeout_raw = (env.get("FSR_TIMEOUT") or "").strip()
        return cls(
            base_url=base_url,
            auth=auth,
            verify_ssl=_flag(env, "FSR_VERIFY_SSL", "true"),
            suppress_insecure_warnings=_flag(env, "FSR_SUPPRESS_INSECURE_WARNINGS", "false"),
            port=int(port_raw) if port_raw else None,
            timeout=int(timeout_raw) if timeout_raw else 30,
        )

    def client(self, **overrides) -> FortiSOAR:
        """Construct a :class:`~pyfsr.client.FortiSOAR` from this config.

        Any keyword in ``overrides`` is passed through to the client constructor,
        taking precedence over the resolved values.
        """
        kwargs = {
            "base_url": self.base_url,
            "auth": self.auth,
            "verify_ssl": self.verify_ssl,
            "suppress_insecure_warnings": self.suppress_insecure_warnings,
            "port": self.port,
            "timeout": self.timeout,
        }
        kwargs.update(overrides)
        return FortiSOAR(**kwargs)
