"""``pyfsr instances`` command group -- inspect the named-instance registry.

:class:`~pyfsr.instances.InstanceRegistry` already answers "which box, and where
are its credentials" for every pyfsr caller. It had no command-line surface,
though, so the answer was only visible to code that already knew the registry
existed -- and consumers that did not know kept their own ``alias -> env file``
maps, which drifted (one such map could not reach three boxes that were
registered for every other tool).

These subcommands make the registry inspectable without writing a script:

- ``list`` -- every configured alias with its base URL, auth kind and whether it
  carries an SSH profile. This is the answer to "what can I pass to
  ``--instance``".
- ``show <alias>`` -- one instance's resolved settings as an identity card,
  including the SSH profile when present.
- ``check [alias ...]`` -- actually connect: probe each box's version endpoint
  and then an authenticated read, so a registered-but-broken alias is visible
  *before* a demo script fails on it.

Nothing here prints a credential. Values are rendered through
:mod:`pyfsr.cli._output`, which masks anything under a secret-looking key, and
the auth column reports only the *kind* of credential in use.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..instances import InstanceRegistry, default_search_path
from . import _output

if TYPE_CHECKING:
    from ..config import EnvConfig

# The cheapest read that proves the *credential* works, not just that the box is
# up: /cyops_version.json answers unauthenticated, so it can never distinguish a
# live appliance from a valid login.
_AUTH_PROBE = "/api/3/appliances?$limit=1"

# Longest failure detail rendered in the table format; --json/--csv are unabridged.
_DETAIL_MAX = 90


def _auth_kind(cfg: EnvConfig) -> str:
    """Describe how an instance authenticates without revealing the credential."""
    if isinstance(cfg.auth, tuple):
        return f"user:{cfg.auth[0]}"
    return "api_key"


def _registry(args: argparse.Namespace) -> InstanceRegistry:
    """Load the registry the CLI is meant to inspect.

    ``--config`` overrides the search path; without it this is exactly what
    every library caller gets from ``InstanceRegistry.load()``.
    """
    return InstanceRegistry.load(getattr(args, "config", None))


def _config_path(args: argparse.Namespace) -> Path:
    override = getattr(args, "config", None)
    return Path(override).expanduser() if override else default_search_path()


def cmd_list(args: argparse.Namespace) -> int:
    """Print every configured alias -- the valid values for ``--instance``."""
    reg = _registry(args)
    ssh = set(reg.appliance_names())
    rows = [
        [
            alias,
            cfg.base_url,
            _auth_kind(cfg),
            "yes" if cfg.verify_ssl else "no",
            "yes" if alias in ssh else "-",
            "*" if alias == reg.default else "",
        ]
        for alias, cfg in sorted(reg.configs.items())
    ]
    _output.render(rows, ["instance", "base_url", "auth", "verify_ssl", "ssh", "default"], fmt=args.fmt)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print one instance's resolved settings, including its SSH profile."""
    reg = _registry(args)
    alias = reg.resolve(args.instance)
    cfg = reg.configs[alias]
    card: dict[str, Any] = {
        "instance": alias,
        "default": alias == reg.default,
        "config": str(_config_path(args)),
        "base_url": cfg.base_url,
        "auth": _auth_kind(cfg),
        "verify_ssl": cfg.verify_ssl,
        "timeout": cfg.timeout,
    }
    if cfg.port is not None:
        card["port"] = cfg.port
    spec = reg.appliance_specs.get(alias)
    if spec is None:
        card["ssh"] = "(no [appliance] subtable)"
    else:
        card["ssh_host"] = spec.host
        card["ssh_user"] = spec.user
        card["ssh_port"] = spec.port
        card["ssh_key_path"] = spec.key_path or "(password auth)"
    _output.kv(card, fmt=args.fmt)
    return 0


def _detail(exc: BaseException) -> str:
    """One-line rendering of a probe failure (collapsed whitespace, full text)."""
    return " ".join(f"{type(exc).__name__}: {exc}".split())


def _connect_status(exc: BaseException) -> str:
    """``unreachable`` for transport failures, ``config-error`` for the rest."""
    names = {type(e).__name__ for e in _causes(exc)}
    if names & {"ConnectionError", "ConnectTimeout", "Timeout", "ReadTimeout", "SSLError"}:
        return "unreachable"
    return "config-error"


def _causes(exc: BaseException) -> list[BaseException]:
    """``exc`` and its ``__cause__``/``__context__`` chain (bounded)."""
    seen: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and len(seen) < 10:
        seen.append(cur)
        cur = cur.__cause__ or cur.__context__
    return seen


def _probe(reg: InstanceRegistry, alias: str) -> tuple[str, str, str]:
    """Return ``(status, version, detail)`` for one instance.

    Two steps, reported separately because they fail for different reasons: the
    version probe says the appliance is up, the authenticated read says this
    alias's stored credential still works. A box that answers the first and not
    the second is the exact case a private ``alias -> env file`` map hid.
    """
    try:
        client = reg.client(alias)
    except Exception as exc:  # noqa: BLE001 -- a bad config must not abort the sweep
        # A username/password instance logs in eagerly, so construction is where
        # an unplugged box surfaces. Reporting that as a config error would send
        # the reader to the TOML for a network problem.
        return _connect_status(exc), "", _detail(exc)

    version = ""
    try:
        raw = client.version()
        version = raw if isinstance(raw, str) else str(raw.get("version", raw))
    except Exception as exc:  # noqa: BLE001
        return "unreachable", "", _detail(exc)

    try:
        client.get(_AUTH_PROBE)
    except Exception as exc:  # noqa: BLE001
        return "auth-failed", version, _detail(exc)
    return "ok", version, ""


def cmd_check(args: argparse.Namespace) -> int:
    """Connect to each named instance (default: all) and report what answered.

    Exits nonzero if any checked instance is not ``ok``, so this is usable as a
    pre-flight step in a demo script or CI job.
    """
    reg = _registry(args)
    aliases = list(args.instances) if args.instances else reg.names()
    unknown = [a for a in aliases if a not in reg.configs]
    if unknown:
        raise ValueError(f"unknown instance(s) {unknown}; known instances: {reg.names()}")

    rows = []
    failed = 0
    # Sequential on purpose: each client() is a fresh login, and a burst of them
    # exhausts cyops-auth's DB pool, which makes healthy boxes report 400.
    for alias in aliases:
        status, version, detail = _probe(reg, alias)
        if status != "ok":
            failed += 1
        if args.fmt == "table" and len(detail) > _DETAIL_MAX:
            # A requests ConnectionError repr runs to several hundred characters
            # and smears the table. The head carries host, port and errno -- the
            # part a reader acts on. --json/--csv still get the whole thing.
            detail = detail[: _DETAIL_MAX - 1] + "…"
        rows.append([alias, reg.configs[alias].base_url, status, version, detail])
    _output.render(rows, ["instance", "base_url", "status", "version", "detail"], fmt=args.fmt)
    return 1 if failed else 0


def _add_fmt(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_const", const="json", dest="fmt", default="table")
    p.add_argument("--csv", action="store_const", const="csv", dest="fmt")


def _add_config(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config",
        help="instances TOML to read (default: $PYFSR_INSTANCES or ~/.pyfsr/instances.toml)",
    )


def build_subparser(sub: argparse._SubParsersAction) -> None:
    """Register the ``instances`` subcommands on ``sub``."""
    p_list = sub.add_parser("list", help="configured aliases -- the valid values for --instance")
    _add_config(p_list)
    _add_fmt(p_list)
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="one instance's resolved settings (no secrets)")
    p_show.add_argument("instance", nargs="?", help="alias (default: the registry's default)")
    _add_config(p_show)
    p_show.add_argument("--json", action="store_const", const="json", dest="fmt", default="table")
    p_show.set_defaults(func=cmd_show)

    p_check = sub.add_parser("check", help="connect to each instance; exit 1 if any fails")
    p_check.add_argument("instances", nargs="*", help="aliases to check (default: all)")
    _add_config(p_check)
    _add_fmt(p_check)
    p_check.set_defaults(func=cmd_check)
