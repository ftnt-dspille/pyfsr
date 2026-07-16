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
from pathlib import Path
from typing import Any

from .client import FortiSOAR

_FALSEY = {"0", "false", "no", "off", ""}


def _load_toml(path: str | Path) -> dict[str, Any]:
    """Parse a TOML file, tolerating Python 3.10 (no stdlib ``tomllib``)."""
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
        try:
            import tomli as tomllib  # type: ignore[no-redef,unused-ignore]
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError(
                "reading a TOML config on Python 3.10 needs the 'tomli' package "
                "(`pip install tomli`); on 3.11+ it is built in"
            ) from exc
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _parse_env_text(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines (``#`` comments, blanks ignored) into a dict."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _flag(env: dict[str, str], name: str, default: str) -> bool:
    """Interpret an ``FSR_*`` env flag as a bool (falsey-ish strings → False)."""
    return env.get(name, default).strip().lower() not in _FALSEY


@dataclass
class EnvConfig:
    """Resolved client configuration (host, auth, transport knobs).

    Build it with :meth:`from_env`, then call :meth:`client` for a
    :class:`~pyfsr.client.FortiSOAR`. ``auth`` is the value passed straight to
    the client: an API-key string, or a ``(username, password)`` tuple.

    >>> cfg = EnvConfig.from_env({
    ...     "FSR_BASE_URL": "https://soar.example.com",
    ...     "FSR_API_KEY": "key-123",
    ... })
    >>> cfg.base_url, cfg.auth, cfg.verify_ssl, cfg.timeout
    ('https://soar.example.com', 'key-123', True, 30)
    >>> type(cfg.auth).__name__          # a lone key resolves to a str
    'str'
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

        Raises ``ValueError`` naming **every** missing required key at once
        (host and/or auth) so a misconfigured environment is fixed in one pass.
        Pass ``env`` to read from a dict instead of ``os.environ``.
        """
        env = env if env is not None else dict(os.environ)
        base_url = (env.get("FSR_BASE_URL") or env.get("FSR_HOST") or "").strip()

        api_key = (env.get("FSR_API_KEY") or "").strip()
        username = (env.get("FSR_USERNAME") or "").strip()
        password = env.get("FSR_PASSWORD") or ""

        missing: list[str] = []
        if not base_url:
            missing.append("FSR_BASE_URL (or FSR_HOST)")
        if not (api_key or (username and password)):
            missing.append("FSR_API_KEY, or both FSR_USERNAME and FSR_PASSWORD")
        if missing:
            raise ValueError("missing required configuration: " + "; ".join(missing))

        auth: str | tuple[str, str] = api_key if api_key else (username, password)

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

    @classmethod
    def from_env_file(cls, path: str | Path, *, override: bool = False) -> EnvConfig:
        """Build configuration from a ``KEY=VALUE`` env file plus ``os.environ``.

        Real environment variables win over the file unless ``override=True``.
        This loads only the ``FSR_*`` keys this class understands; it is **not** a
        general ``.env`` loader — for that, use ``python-dotenv`` and then call
        :meth:`from_env`.
        """
        file_vars = _parse_env_text(Path(path).read_text(encoding="utf-8"))
        env = dict(os.environ)
        for key, value in file_vars.items():
            if override or key not in env:
                env[key] = value
        return cls.from_env(env)

    @classmethod
    def from_config_file(cls, path: str | Path) -> EnvConfig:
        """Build configuration from a TOML file (the ``[fortisoar]`` layout).

        Mirrors the ``config.toml`` used by the examples::

            [fortisoar]
            base_url = "https://soar.example.com"
            verify_ssl = false

            [fortisoar.auth]
            type = "user_pass"   # or "api_key"
            username = "csadmin"
            password = "..."
            # key = "..."        # when type = "api_key"
        """
        return cls.from_mapping(_load_toml(path), source=str(path))

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, source: str = "<mapping>") -> EnvConfig:
        """Build configuration from an already-parsed ``[fortisoar]`` mapping.

        Accepts either the full document (with a top-level ``fortisoar`` table) or
        the inner table directly, so callers holding a sub-table — e.g. one entry
        of an instance registry — can reuse the same auth/host parsing as
        :meth:`from_config_file` without re-reading a file. ``source`` only labels
        error messages.
        """
        fsr = data.get("fortisoar", data)
        base_url = str(fsr.get("base_url") or fsr.get("host") or "").strip()
        if not base_url:
            raise ValueError(f"{source}: [fortisoar].base_url is required")

        auth_cfg = fsr.get("auth", {}) or {}
        auth_type = str(auth_cfg.get("type") or "").strip().lower()
        key = (auth_cfg.get("key") or auth_cfg.get("api_key") or "").strip()
        username = (auth_cfg.get("username") or "").strip()
        password = auth_cfg.get("password") or ""
        if auth_type == "api_key" or (key and not username):
            if not key:
                raise ValueError(f"{source}: [fortisoar.auth].key is required for api_key auth")
            auth: str | tuple[str, str] = key
        elif username and password:
            auth = (username, password)
        else:
            raise ValueError(f"{source}: set [fortisoar.auth] key (api_key), or both username and password")

        port = fsr.get("port")
        timeout = fsr.get("timeout")
        return cls(
            base_url=base_url,
            auth=auth,
            verify_ssl=bool(fsr.get("verify_ssl", True)),
            suppress_insecure_warnings=bool(fsr.get("suppress_insecure_warnings", False)),
            port=int(port) if port is not None else None,
            timeout=int(timeout) if timeout is not None else 30,
        )

    def client(self, **overrides) -> FortiSOAR:
        """Construct a :class:`~pyfsr.client.FortiSOAR` from this config.

        Any keyword in ``overrides`` is passed through to the client constructor,
        taking precedence over the resolved values.

        The resolved ``auth`` union is translated into the client's
        non-deprecated keyword form (``token`` for an API-key string,
        ``username``/``password`` for a credential tuple) rather than the legacy
        ``auth`` parameter, so the env/config-file convenience path doesn't trip
        the ``auth`` deprecation warning.
        """
        kwargs = {
            "base_url": self.base_url,
            "verify_ssl": self.verify_ssl,
            "suppress_insecure_warnings": self.suppress_insecure_warnings,
            "port": self.port,
            "timeout": self.timeout,
        }
        if isinstance(self.auth, str):
            kwargs["token"] = self.auth
        else:
            kwargs["username"], kwargs["password"] = self.auth
        kwargs.update(overrides)
        return FortiSOAR(**kwargs)
