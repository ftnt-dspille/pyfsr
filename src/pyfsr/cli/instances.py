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

- ``env <alias>`` -- the resolved ``FSR_*`` / ``PYFSR_APPLIANCE_*`` settings as
  ``KEY=VALUE`` (or ``export`` lines, or JSON) for make, shell and JS callers.
  Secrets only with ``--with-secrets``; ``--env-file-path`` hands out a *file*
  holding them instead, so no credential ever rides on a command line.
- ``ssh-target <alias>`` -- host/user/port/key for ``ssh``, from the appliance
  subtable (falling back to the ``base_url`` host), so callers need no
  ``~/.ssh/config`` alias.

Recommended make pattern (no secret in ``ps``, no ``~/.ssh/config`` alias)::

    BOX ?=
    ifeq ($(BOX),)
    $(error BOX=<alias> required; see `pyfsr instances list`)
    endif
    # non-secret settings, safe in make variables and logs
    $(eval $(shell pyfsr instances env $(BOX) --format env | sed 's/^/export /'))
    # secrets stay in a file the recipe sources (backing env_file, or a 0600 temp)
    BOX_ENV_FILE := $(shell pyfsr instances env $(BOX) --env-file-path)
    target:
        set -a; . "$(BOX_ENV_FILE)"; set +a; ./do_thing.sh
        ssh $(shell pyfsr instances ssh-target $(BOX) --format ssh) 'uname -a'

Nothing here prints a credential unless ``--with-secrets`` is passed. Values are rendered through
:mod:`pyfsr.cli._output`, which masks anything under a secret-looking key, and
the auth column reports only the *kind* of credential in use.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import _load_toml
from ..instances import InstanceRegistry, _hostname_from_base_url, default_search_path
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
    return _probe_with(lambda: reg.client(alias))


def _probe_with(make_client: Any) -> tuple[str, str, str]:
    """:func:`_probe` for any client factory (``instances add`` probes before it saves)."""
    try:
        client = make_client()
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
    aliases = list(args.instances) if args.instances and not args.all else reg.names()
    unknown = [a for a in aliases if a not in reg.configs]
    if unknown:
        raise ValueError(f"unknown instance(s) {unknown}; known instances: {reg.names()}")

    rows = []
    failed = 0
    # Sequential on purpose: each client() is a fresh login, and a burst of them
    # exhausts cyops-auth's DB pool, which makes healthy boxes report 400.
    for alias in aliases:
        if args.offline:
            status, version, detail = "ok", "", "(offline: not connected)"
        else:
            status, version, detail = _probe(reg, alias)
        if status != "ok":
            failed += 1
        if args.fmt == "table" and len(detail) > _DETAIL_MAX:
            # A requests ConnectionError repr runs to several hundred characters
            # and smears the table. The head carries host, port and errno -- the
            # part a reader acts on. --json/--csv still get the whole thing.
            detail = detail[: _DETAIL_MAX - 1] + "…"
        ssh = "yes" if alias in reg.appliance_specs else "-"
        rows.append([alias, reg.configs[alias].base_url, status, version, ssh, detail])
    _output.render(rows, ["instance", "base_url", "status", "version", "ssh", "detail"], fmt=args.fmt)
    return 1 if failed else 0


# -- add ----------------------------------------------------------------------

_ALIAS_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TRUE = {"1", "true", "yes", "y", "on"}


def _ask(prompt: str, default: str | None = None) -> str:
    """One answer from the terminal; ``default`` when the reader just presses Enter."""
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or (default or "")


def _ask_yes(prompt: str, default: bool) -> bool:
    answer = _ask(f"{prompt} (y/n)", "y" if default else "n").lower()
    return answer in _TRUE


def _secret(prompt: str) -> str:
    """A secret without echo on a terminal; one line of stdin when piped (scripts, CI)."""
    import getpass
    import sys

    if sys.stdin.isatty():
        return getpass.getpass(f"{prompt}: ")
    return sys.stdin.readline().rstrip("\n")


def _secret_from(var: str, prompt: str, interactive: bool) -> str:
    """``$var`` if set, else a no-echo prompt, else a piped stdin line; never an argv value."""
    import sys

    if os.environ.get(var):
        return os.environ[var]
    if sys.stdin.isatty() and not interactive:
        return ""  # --no-input on a terminal: refuse rather than prompt
    return _secret(prompt)


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


def _toml_quote(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def _existing_aliases(path: Path) -> tuple[set[str], str | None]:
    if not path.exists():
        return set(), None
    data = _load_toml(path)
    return set((data.get("instances") or {}).keys()), data.get("default")


def _register(path: Path, alias: str, env_file: Path, *, make_default: bool) -> None:
    """Append ``[instances.<alias>]`` to the registry; set ``default`` when asked.

    Appends rather than re-serialising so the reader's comments and layout in an
    existing file survive. The env file is stored relative to the registry when
    it sits beside it, so the pair can be moved together.
    """
    try:
        ref = str(env_file.relative_to(path.parent))
    except ValueError:
        ref = str(env_file)
    block = f"\n[instances.{alias}]\nenv_file = {_toml_quote(ref)}\n"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if make_default:
        line = f"default = {_toml_quote(alias)}"
        if re.search(r"(?m)^default\s*=.*$", text):
            text = re.sub(r"(?m)^default\s*=.*$", line, text, count=1)
        else:
            text = line + "\n" + text
    text = text.rstrip("\n") + "\n" + block if text.strip() else text + block.lstrip("\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pyfsr-instances-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def cmd_add(args: argparse.Namespace) -> int:
    """Register a box under an alias, guided: ask for what is missing, prove it, save it.

    Credentials go to a 0600 env file beside the registry (``<alias>.env``); the
    TOML only points at it, so ``instances.toml`` never holds a secret and a
    coding agent's MCP config needs no ``FSR_*`` values at all. ``--env-file``
    registers an env file you already have instead of writing one.
    """
    from ..config import EnvConfig, _parse_env_text

    path = _config_path(args)
    aliases, current_default = _existing_aliases(path)
    interactive = not args.no_input

    alias = args.alias or (interactive and _ask("Name for this box (the alias you will pass as --instance)")) or ""
    if not _ALIAS_OK.match(alias):
        raise ValueError(f"alias {alias!r}: use letters, digits, '.', '_' or '-', starting with a letter or digit")
    if alias in aliases:
        raise ValueError(f"{path}: instance {alias!r} already exists; pick another name or edit the file")

    if args.env_file:
        env_file = Path(args.env_file).expanduser().resolve()
        cfg = EnvConfig.from_env(_parse_env_text(env_file.read_text(encoding="utf-8")))
        env: dict[str, str] | None = None
    else:
        url = args.url or (interactive and _ask("FortiSOAR or FortiSOC URL (e.g. fortisoar.example.com)")) or ""
        url = _normalize_url(url)
        if not url:
            raise ValueError("a URL is required (--url)")
        auth = args.auth
        if auth is None:
            if args.username:
                auth = "password"
            elif interactive:
                use_key = _ask_yes("Sign in with an API key? (FortiSOC uses an API key)", True)
                auth = "api-key" if use_key else "password"
            else:
                auth = "api-key"
        env = {"FSR_BASE_URL": url}
        if auth == "api-key":
            key = _secret_from("FSR_API_KEY", "API key", interactive)
            if not key.strip():
                raise ValueError("an API key is required (prompted, piped on stdin, or $FSR_API_KEY)")
            env["FSR_API_KEY"] = key.strip()
        else:
            user = args.username or (interactive and _ask("Username")) or ""
            if not user:
                raise ValueError("a username is required (--username)")
            pw = _secret_from("FSR_PASSWORD", f"Password for {user}", interactive)
            if not pw:
                raise ValueError("a password is required (prompted, piped on stdin, or $FSR_PASSWORD)")
            env["FSR_USERNAME"] = user
            env["FSR_PASSWORD"] = pw
        verify = not args.insecure
        if interactive and not args.insecure:
            verify = _ask_yes("Verify the TLS certificate? Answer n for a lab box with a self-signed certificate", True)
        if not verify:
            env["FSR_VERIFY_SSL"] = "false"
            env["FSR_SUPPRESS_INSECURE_WARNINGS"] = "true"
        cfg = EnvConfig.from_env(env)
        env_file = path.parent / f"{alias}.env"
        if env_file.exists():
            raise ValueError(f"{env_file} already exists; remove it or pass --env-file {env_file}")

    if not args.no_check:
        status, version, detail = _probe_with(cfg.client)
        if status != "ok":
            print(f"{alias}: {status} at {cfg.base_url}: {detail}")
            print("nothing was saved; fix the URL or credential and run it again (or pass --no-check)")
            return 1
        print(f"{alias}: connected to {cfg.base_url} (FortiSOAR {version}, {_auth_kind(cfg)})")

    if env is not None:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        _write_env_file(env_file, env)
    make_default = args.default or current_default is None
    _register(path, alias, env_file, make_default=make_default)
    print(f"saved {alias} to {path}" + (" as the default" if make_default else ""))
    print(f"credentials: {env_file} (mode 600)" if env is not None else f"credentials: {env_file} (yours, unchanged)")
    print(f"use it: --instance {alias}" + ("" if make_default else f", or make it the default: edit default in {path}"))
    return 0


# -- env / ssh-target ---------------------------------------------------------

_ENV_FORMATS = ("env", "shell", "json")


def _ssh_target(reg: InstanceRegistry, alias: str) -> dict[str, Any]:
    """Resolve the SSH target for ``alias``: appliance subtable, else the base_url host.

    ``source`` says which, so a caller can tell a deliberate profile from a guess.
    The password fields are returned separately by :func:`_ssh_secrets`.
    """
    spec = reg.appliance_specs.get(alias)
    if spec is not None:
        return {
            "host": spec.host,
            "user": spec.user,
            "port": spec.port,
            "key_path": spec.key_path or "",
            "insecure_skip_host_key_check": spec.insecure_skip_host_key_check,
            "source": "appliance",
        }
    host = _hostname_from_base_url(reg.configs[alias].base_url)
    if not host:
        raise ValueError(
            f"instance {alias!r}: no appliance subtable and no host in base_url {reg.configs[alias].base_url!r}"
        )
    return {
        "host": host,
        "user": "csadmin",
        "port": 22,
        "key_path": "",
        "insecure_skip_host_key_check": False,
        "source": "base_url",
    }


def _ssh_secrets(reg: InstanceRegistry, alias: str) -> dict[str, str]:
    spec = reg.appliance_specs.get(alias)
    out: dict[str, str] = {}
    if spec is not None:
        if spec.password:
            out["PYFSR_APPLIANCE_PASSWORD"] = spec.password
        if spec.sudo_password:
            out["PYFSR_APPLIANCE_SUDO_PASSWORD"] = spec.sudo_password
    return out


def _fsr_secrets(cfg: EnvConfig) -> dict[str, str]:
    if isinstance(cfg.auth, tuple):
        return {"FSR_USERNAME": cfg.auth[0], "FSR_PASSWORD": cfg.auth[1]}
    return {"FSR_API_KEY": cfg.auth}


def _env_vars(reg: InstanceRegistry, alias: str, *, with_secrets: bool) -> dict[str, str]:
    """The ordered KEY->VALUE map ``instances env`` prints (secrets only on request)."""
    cfg = reg.configs[alias]
    tgt = _ssh_target(reg, alias)
    env: dict[str, str] = {
        "PYFSR_INSTANCE": alias,
        "FSR_BASE_URL": cfg.base_url,
        "FSR_VERIFY_SSL": "true" if cfg.verify_ssl else "false",
        "FSR_TIMEOUT": str(cfg.timeout),
        "FSR_AUTH_KIND": _auth_kind(cfg).split(":")[0],
    }
    if cfg.port is not None:
        env["FSR_PORT"] = str(cfg.port)
    backing = reg.env_files.get(alias)
    if backing is not None:
        env["PYFSR_ENV_FILE"] = str(backing)
    env["PYFSR_APPLIANCE_HOST"] = tgt["host"]
    env["PYFSR_APPLIANCE_USER"] = tgt["user"]
    env["PYFSR_APPLIANCE_PORT"] = str(tgt["port"])
    if tgt["key_path"]:
        env["PYFSR_APPLIANCE_KEY_PATH"] = tgt["key_path"]
    if tgt["insecure_skip_host_key_check"]:
        env["PYFSR_APPLIANCE_INSECURE_SKIP_HOST_KEY_CHECK"] = "true"
    env["PYFSR_APPLIANCE_SOURCE"] = tgt["source"]
    if with_secrets:
        env.update(_fsr_secrets(cfg))
        env.update(_ssh_secrets(reg, alias))
    return env


def _write_env_file(path: Path, env: dict[str, str]) -> None:
    """Write ``env`` as a sourceable file that is 0600 before any secret lands in it.

    The secrets go into a FRESH temp file in the target directory (mkstemp: created
    O_EXCL at 0600, never an existing file or a symlink), which then atomically
    replaces ``path``. Writing into an existing ``--out`` file would keep its old,
    possibly world-readable mode while the secrets are written, and would follow a
    symlink planted at that path.
    """
    import tempfile

    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".pyfsr-env-", dir=str(path.parent or Path(".")))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for k, v in env.items():
                fh.write(f"{k}={shlex.quote(v)}\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _pick_field(values: dict[str, Any], name: str) -> str:
    """Look ``name`` up case-insensitively, accepting a bare name (``base_url``) or the full key."""
    wanted = name.lower()
    for key, val in values.items():
        k = key.lower()
        if wanted in (k, k.removeprefix("pyfsr_appliance_").removeprefix("fsr_")):
            return str(val)
    raise ValueError(f"no field {name!r}; available: {sorted(values)}")


def cmd_env(args: argparse.Namespace) -> int:
    """Print an instance's resolved settings for make / shell / JS consumption."""
    reg = _registry(args)
    alias = reg.resolve(args.instance)

    if args.env_file_path:
        backing = reg.env_files.get(alias)
        if backing is not None and not args.out:
            print(backing)
            return 0
        # Inline entry (or an explicit --out): materialize a 0600 file with secrets.
        env = _env_vars(reg, alias, with_secrets=True)
        if args.out:
            target = Path(args.out).expanduser()
        else:
            fd, tmp = tempfile.mkstemp(prefix=f"pyfsr-{alias}-", suffix=".env")
            os.close(fd)
            target = Path(tmp)
        _write_env_file(target, env)
        print(target)
        return 0

    env = _env_vars(reg, alias, with_secrets=args.with_secrets)
    if args.field:
        print(_pick_field(env, args.field))
        return 0
    if args.env_format == "json":
        print(json.dumps(env, indent=2))
    else:
        prefix = "export " if args.env_format == "shell" else ""
        for k, v in env.items():
            print(f"{prefix}{k}={shlex.quote(v)}")
    return 0


def cmd_ssh_target(args: argparse.Namespace) -> int:
    """Print the ssh host/user/port/key for an instance (no ``~/.ssh/config`` alias needed)."""
    reg = _registry(args)
    alias = reg.resolve(args.instance)
    tgt = _ssh_target(reg, alias)
    if args.field:
        print(_pick_field(tgt, args.field))
        return 0
    fmt = args.ssh_format
    if fmt == "json":
        print(json.dumps(tgt, indent=2))
    elif fmt == "env":
        print(f"SSH_HOST={shlex.quote(str(tgt['host']))}")
        print(f"SSH_USER={shlex.quote(str(tgt['user']))}")
        print(f"SSH_PORT={tgt['port']}")
        print(f"SSH_KEY={shlex.quote(str(tgt['key_path']))}")
    else:  # ssh: ready-to-splice argument string for `ssh $(...) cmd`
        parts = ["-p", str(tgt["port"])]
        if tgt["key_path"]:
            parts += ["-i", str(tgt["key_path"]), "-o", "IdentitiesOnly=yes"]
        if tgt["insecure_skip_host_key_check"]:
            parts += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
        parts.append(f"{tgt['user']}@{tgt['host']}")
        print(" ".join(shlex.quote(x) for x in parts))
    return 0


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
    p_check.add_argument("--all", action="store_true", help="check every alias (the default when none are named)")
    p_check.add_argument(
        "--offline",
        action="store_true",
        help="only prove each alias resolves (config parses, env file readable) and report its ssh profile; no network",
    )
    _add_config(p_check)
    _add_fmt(p_check)
    p_check.set_defaults(func=cmd_check)

    p_add = sub.add_parser(
        "add",
        help="register a box: asks for the URL and an API key or login, proves it connects, "
        "keeps the secret in a 0600 env file",
    )
    p_add.add_argument("alias", nargs="?", help="name to pass as --instance (asked when omitted)")
    p_add.add_argument("--url", help="FortiSOAR or FortiSOC URL; https:// is assumed")
    p_add.add_argument("--auth", choices=("api-key", "password"), help="how to sign in (asked when omitted)")
    p_add.add_argument("--username", help="login id for --auth password; the password is prompted, never an argument")
    p_add.add_argument("--insecure", action="store_true", help="skip TLS verification (self-signed lab box)")
    p_add.add_argument("--env-file", help="register an existing FSR_* env file instead of writing one")
    p_add.add_argument("--default", action="store_true", help="make this the default instance")
    p_add.add_argument("--no-check", action="store_true", help="save without connecting first")
    p_add.add_argument(
        "--no-input", action="store_true", help="never prompt; take secrets from $FSR_API_KEY / $FSR_PASSWORD or stdin"
    )
    _add_config(p_add)
    p_add.set_defaults(func=cmd_add)

    p_env = sub.add_parser(
        "env",
        help="resolved FSR_*/PYFSR_APPLIANCE_* settings as KEY=VALUE for make/shell (no secrets unless asked)",
    )
    p_env.add_argument("instance", nargs="?", help="alias (default: the registry's default)")
    _add_config(p_env)
    p_env.add_argument(
        "--format",
        dest="env_format",
        choices=_ENV_FORMATS,
        default="env",
        help="env (default), shell (export lines) or json",
    )
    p_env.add_argument("--field", help="print one value only (e.g. base_url, FSR_BASE_URL, host, user)")
    p_env.add_argument(
        "--with-secrets", action="store_true", help="include API key / passwords (prefer --env-file-path)"
    )
    p_env.add_argument(
        "--env-file-path",
        action="store_true",
        help="print the path of a sourceable env file holding the secrets: the backing env_file, "
        "or a new 0600 temp file for an inline entry",
    )
    p_env.add_argument(
        "--out", help="with --env-file-path: write the 0600 env file here instead of reusing/creating one"
    )
    p_env.set_defaults(func=cmd_env)

    p_ssh = sub.add_parser(
        "ssh-target", help="ssh host/user/port/key for an instance (appliance subtable, else base_url host)"
    )
    p_ssh.add_argument("instance", nargs="?", help="alias (default: the registry's default)")
    _add_config(p_ssh)
    p_ssh.add_argument(
        "--format",
        dest="ssh_format",
        choices=("ssh", "env", "json"),
        default="ssh",
        help="ssh (default: args for `ssh`), env or json",
    )
    p_ssh.add_argument("--field", help="print one value only: host, user, port, key_path")
    p_ssh.set_defaults(func=cmd_ssh_target)
