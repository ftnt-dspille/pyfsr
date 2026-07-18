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

Appliance SSH profiles
----------------------

Each ``[instances.<alias>]`` table may optionally carry an ``[instances.<alias>.
appliance]`` subtable with SSH transport fields, so the ``pyfsr appliance`` CLI
(and :meth:`InstanceRegistry.transport`) can reach the same box over SSH
without re-stating host/auth on every call::

    [instances.206]
    base_url = "https://10.0.0.206"
    [instances.206.auth]
    type = "api_key"
    key = "..."

    [instances.206.appliance]
    # host defaults to the hostname parsed from instances.206.base_url
    user = "csadmin"
    password = "..."                # or use env_file / key_path
    port = 22
    key_path = "~/.ssh/id_ed25519"
    sudo_password = "..."
    insecure_skip_host_key_check = false

    # Or keep SSH creds in a separate file (PYFSR_APPLIANCE_* keys):
    # env_file = ".env.206.ssh"

``host`` defaults to the hostname parsed from the instance's ``base_url`` so a
box whose REST and SSH endpoints share an IP needs no repetition. The subtable
is optional — an instance without one still resolves REST clients; the
appliance CLI simply has no named SSH profile for it (fall back to
``--host``/``PYFSR_APPLIANCE_*`` env).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .client import FortiSOAR
from .config import EnvConfig, _load_toml, _parse_env_text

if TYPE_CHECKING:
    from .cli.appliance.transport import Transport


def default_search_path() -> Path:
    """Return the config path to try: ``$PYFSR_INSTANCES`` or ``~/.pyfsr/instances.toml``."""
    override = (os.environ.get("PYFSR_INSTANCES") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pyfsr" / "instances.toml"


# -- appliance SSH profile --------------------------------------------------
# Keys recognized in an [instances.<alias>.appliance] subtable. ``host`` defaults
# to the hostname parsed from the instance's base_url, so a box whose REST and
# SSH endpoints share an IP needs no repetition. ``env_file`` points at a
# KEY=VALUE file of PYFSR_APPLIANCE_* vars (creds stay out of the TOML).
_APPLIANCE_ENV_KEYS = (
    "PYFSR_APPLIANCE_HOST",
    "PYFSR_APPLIANCE_USER",
    "PYFSR_APPLIANCE_PASSWORD",
    "PYFSR_APPLIANCE_PORT",
    "PYFSR_APPLIANCE_KEY_PATH",
    "PYFSR_APPLIANCE_SUDO_PASSWORD",
    "PYFSR_APPLIANCE_INSECURE_SKIP_HOST_KEY_CHECK",
)
_APPLIANCE_TRUTHY = {"1", "true", "yes", "y", "on"}


def _hostname_from_base_url(base_url: str) -> str | None:
    """Extract the hostname from a REST ``base_url`` (``https://10.0.0.206`` → ``10.0.0.206``)."""
    if not base_url:
        return None
    parsed = urlsplit(base_url if "://" in base_url else f"//{base_url}", scheme="")
    return parsed.hostname


@dataclass
class ApplianceSpec:
    """SSH transport profile for one appliance, resolved from the config file.

    Built internally from an ``[instances.<alias>.appliance]`` subtable, and
    handed to :meth:`InstanceRegistry.transport`, which turns it into a
    :class:`~pyfsr.cli.appliance.transport.Transport`. ``host`` is always set
    when the spec resolves — :meth:`InstanceRegistry.transport` raises if it
    can't get one.
    """

    host: str
    user: str = "csadmin"
    password: str | None = None
    port: int = 22
    key_path: str | None = None
    sudo_password: str | None = None
    insecure_skip_host_key_check: bool = False


def _appliance_spec_from_table(
    spec: dict[str, Any],
    *,
    default_host: str | None,
    base_dir: Path,
    source: str,
) -> ApplianceSpec | None:
    """Build an :class:`ApplianceSpec` from an ``[instances.<alias>.appliance]`` table.

    Returns ``None`` when the subtable is absent. ``default_host`` is the hostname
    parsed from the instance's ``base_url`` — used when the subtable omits ``host``.
    An ``env_file`` key (relative to ``base_dir``) points at a ``PYFSR_APPLIANCE_*``
    KEY=VALUE file; the subtable's inline fields take precedence over the file's.
    """
    if not spec:
        return None
    env_file = spec.get("env_file")
    file_vars: dict[str, str] = {}
    if env_file:
        ef = Path(str(env_file)).expanduser()
        if not ef.is_absolute():
            ef = base_dir / ef
        file_vars = _parse_env_text(ef.read_text(encoding="utf-8"))

    def pick(key_toml: str, key_env: str) -> Any:
        # Subtable field wins over the env_file's var.
        val = spec.get(key_toml)
        if val is None or (isinstance(val, str) and val == ""):
            val = file_vars.get(key_env)
        return val

    host = pick("host", "PYFSR_APPLIANCE_HOST") or default_host
    if not host:
        raise ValueError(
            f"{source}: [appliance] has no `host` and the instance has no "
            f"base_url to derive one from — set `host` explicitly"
        )
    user = pick("user", "PYFSR_APPLIANCE_USER") or "csadmin"
    password = pick("password", "PYFSR_APPLIANCE_PASSWORD") or None
    port_raw = pick("port", "PYFSR_APPLIANCE_PORT")
    try:
        port = int(port_raw) if port_raw not in (None, "") else 22
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source}: [appliance].port must be an int, got {port_raw!r}") from exc
    key_path = pick("key_path", "PYFSR_APPLIANCE_KEY_PATH") or None
    key_path = str(Path(key_path).expanduser()) if key_path else None
    sudo_password = pick("sudo_password", "PYFSR_APPLIANCE_SUDO_PASSWORD") or None
    insecure_raw = pick("insecure_skip_host_key_check", "PYFSR_APPLIANCE_INSECURE_SKIP_HOST_KEY_CHECK")
    insecure = bool(insecure_raw and str(insecure_raw).strip().lower() in _APPLIANCE_TRUTHY)
    return ApplianceSpec(
        host=str(host),
        user=str(user),
        password=password,
        port=port,
        key_path=key_path,
        sudo_password=sudo_password,
        insecure_skip_host_key_check=insecure,
    )


@dataclass
class InstanceRegistry:
    """A set of named :class:`~pyfsr.config.EnvConfig`, with lazy client caching.

    Build it with :meth:`load` (the usual entry point), :meth:`from_config_file`,
    or :meth:`from_single_env`. :meth:`client` resolves an alias to a live client,
    constructing it once and reusing it thereafter. :meth:`transport` resolves an
    alias to an SSH :class:`~pyfsr.cli.appliance.transport.Transport` when the
    instance carries an ``[instances.<alias>.appliance]`` subtable.
    """

    configs: dict[str, EnvConfig]
    default: str | None = None
    appliance_specs: dict[str, ApplianceSpec] = field(default_factory=dict)
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
        appliance_specs: dict[str, ApplianceSpec] = {}
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

            # Optional [instances.<alias>.appliance] SSH subtable. Parse after the
            # EnvConfig is resolved so `default_host` can come from base_url even
            # when the instance uses env_file (host not inline in the TOML).
            app_spec = _appliance_spec_from_table(
                spec.get("appliance") or {},
                default_host=_hostname_from_base_url(configs[alias].base_url),
                base_dir=base_dir,
                source=f"{path} [instances.{alias}.appliance]",
            )
            if app_spec is not None:
                appliance_specs[alias] = app_spec

        default = data.get("default")
        if default is not None and default not in configs:
            raise ValueError(f"{path}: default = {default!r} is not a defined instance")
        if default is None and len(configs) == 1:
            default = next(iter(configs))
        return cls(configs=configs, default=default, appliance_specs=appliance_specs)

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

    def appliance_names(self) -> list[str]:
        """Aliases that carry an ``[instances.<alias>.appliance]`` SSH subtable."""
        return sorted(self.appliance_specs)

    def transport(self, alias: str | None = None) -> Transport:
        """Return an SSH :class:`~pyfsr.cli.appliance.transport.Transport` for ``alias``.

        Requires the instance to carry an ``[instances.<alias>.appliance]`` subtable
        in the config file (inline fields or an ``env_file`` of ``PYFSR_APPLIANCE_*``
        vars). Raises :class:`ValueError` if the alias is unknown or has no appliance
        subtable — callers without a named SSH profile should fall back to
        :func:`~pyfsr.cli.appliance.transport.transport_from_env` / ``--host`` flags.

        ``alias=None`` resolves to the registry's ``default`` (same rule as
        :meth:`client`); the default must have an appliance subtable for this to work.
        """
        from .cli.appliance.transport import make_transport

        name = self.resolve(alias)
        spec = self.appliance_specs.get(name)
        if spec is None:
            raise ValueError(
                f"instance {name!r} has no [instances.{name}.appliance] subtable; "
                f"appliance profiles configured for {self.appliance_names() or '(none)'}"
            )
        return make_transport(
            host=spec.host,
            user=spec.user,
            password=spec.password,
            port=spec.port,
            key_path=spec.key_path,
            sudo_password=spec.sudo_password,
            insecure_skip_host_key_check=spec.insecure_skip_host_key_check,
        )
