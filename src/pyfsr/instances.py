"""Named, multi-instance FortiSOAR client registry.

A single process often needs to reach several FortiSOAR appliances — a lab box,
a GA instance, a customer's demo box — without re-wiring host/auth each time.
:class:`InstanceRegistry` maps a short alias (``"206"``, ``"ga"``) to a resolved
:class:`~pyfsr.config.EnvConfig`, and hands out cached
:class:`~pyfsr.client.FortiSOAR` clients on demand::

    from pyfsr.instances import InstanceRegistry
    reg = InstanceRegistry.load()          # ~/.pyfsr/instances.toml, or FSR_* env
    client = reg.client("206")             # cached per alias
    client_ga = reg.client("ga")

It is the multi-instance counterpart of :class:`~pyfsr.config.EnvConfig` (which
resolves exactly one appliance) and is what the bundled MCP server
(:mod:`pyfsr.agent.mcp`) uses to route a tool call's ``instance`` argument to the
right box.

Config file (TOML, default ``~/.pyfsr/instances.toml``)::

    default = "206"

    [instances.206]
    # Point at an existing KEY=VALUE env file (FSR_* keys). Relative paths
    # resolve against the config file's directory. Creds stay in that file.
    env_file = "/path/to/.env.206"

    [instances.ga]
    # Or inline the same [fortisoar] shape EnvConfig.from_config_file understands.
    base_url = "https://ga.example.com"
    verify_ssl = false
    [instances.ga.auth]
    type = "api_key"
    key = "..."

When no config file is present, :meth:`InstanceRegistry.load` falls back to a
single ``"default"`` instance built from the ``FSR_*`` environment, so existing
single-box callers keep working unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import FortiSOAR
from .config import EnvConfig, _load_toml, _parse_env_text


def default_search_path() -> Path:
    """Return the config path to try: ``$PYFSR_INSTANCES`` or ``~/.pyfsr/instances.toml``."""
    override = (os.environ.get("PYFSR_INSTANCES") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pyfsr" / "instances.toml"


@dataclass
class InstanceRegistry:
    """A set of named :class:`~pyfsr.config.EnvConfig`, with lazy client caching.

    Build it with :meth:`load` (the usual entry point), :meth:`from_config_file`,
    or :meth:`from_single_env`. :meth:`client` resolves an alias to a live client,
    constructing it once and reusing it thereafter.
    """

    configs: dict[str, EnvConfig]
    default: str | None = None
    _clients: dict[str, FortiSOAR] = field(default_factory=dict, repr=False, compare=False)

    # -- constructors -------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None, env: dict[str, str] | None = None) -> InstanceRegistry:
        """Load from a config file if one exists, else a single ``FSR_*`` env instance.

        ``path`` defaults to :func:`default_search_path`. This is the entry point
        the MCP server uses: multi-instance when configured, single-box otherwise.
        """
        path = Path(path).expanduser() if path is not None else default_search_path()
        if path.exists():
            return cls.from_config_file(path)
        return cls.from_single_env(env)

    @classmethod
    def from_config_file(cls, path: str | Path) -> InstanceRegistry:
        """Parse a TOML instances file into a registry.

        Each ``[instances.<alias>]`` table is either an ``env_file`` indirection
        (a ``KEY=VALUE`` file of ``FSR_*`` vars, read in isolation from the process
        environment) or an inline ``[fortisoar]``-shaped mapping. A relative
        ``env_file`` resolves against the config file's directory.
        """
        path = Path(path).expanduser()
        data = _load_toml(path)
        raw = data.get("instances") or {}
        if not raw:
            raise ValueError(f"{path}: no [instances.<alias>] tables found")

        base_dir = path.parent
        configs: dict[str, EnvConfig] = {}
        for alias, spec in raw.items():
            if not isinstance(spec, dict):
                raise ValueError(f"{path}: [instances.{alias}] must be a table")
            env_file = spec.get("env_file")
            if env_file:
                ef = Path(env_file).expanduser()
                if not ef.is_absolute():
                    ef = base_dir / ef
                # Parse the file alone — do NOT merge os.environ, or a stray FSR_*
                # in the process would override every instance identically.
                file_vars = _parse_env_text(ef.read_text(encoding="utf-8"))
                configs[alias] = EnvConfig.from_env(file_vars)
            else:
                configs[alias] = EnvConfig.from_mapping(spec, source=f"{path} [instances.{alias}]")

        default = data.get("default")
        if default is not None and default not in configs:
            raise ValueError(f"{path}: default = {default!r} is not a defined instance")
        if default is None and len(configs) == 1:
            default = next(iter(configs))
        return cls(configs=configs, default=default)

    @classmethod
    def from_single_env(cls, env: dict[str, str] | None = None) -> InstanceRegistry:
        """Build a one-instance registry named ``"default"`` from ``FSR_*`` env vars."""
        return cls(configs={"default": EnvConfig.from_env(env)}, default="default")

    # -- access -------------------------------------------------------------

    def names(self) -> list[str]:
        """Configured instance aliases, sorted."""
        return sorted(self.configs)

    def describe(self) -> list[dict[str, Any]]:
        """Non-secret summary of each instance (alias, base_url, verify_ssl, default)."""
        return [
            {
                "instance": alias,
                "base_url": cfg.base_url,
                "verify_ssl": cfg.verify_ssl,
                "default": alias == self.default,
            }
            for alias, cfg in sorted(self.configs.items())
        ]

    def resolve(self, alias: str | None) -> str:
        """Resolve ``alias`` (or ``None`` → the default) to a known instance name.

        Raises ``ValueError`` naming the valid instances when the alias is unknown
        or when no default exists and none was given.
        """
        if alias is None or alias == "":
            if self.default is None:
                raise ValueError(f"no instance given and no default configured; choose one of {self.names()}")
            return self.default
        if alias not in self.configs:
            raise ValueError(f"unknown instance {alias!r}; known instances: {self.names()}")
        return alias

    def client(self, alias: str | None = None) -> FortiSOAR:
        """Return a cached :class:`~pyfsr.client.FortiSOAR` for ``alias`` (or the default)."""
        name = self.resolve(alias)
        if name not in self._clients:
            self._clients[name] = self.configs[name].client()
        return self._clients[name]
