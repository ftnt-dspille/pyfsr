"""Command transport for the ``pyfsr appliance`` CLI.

Two impls share one API (:meth:`Transport.run`):

* :class:`LocalTransport` — exec directly when the CLI runs *on* the appliance.
* :class:`SSHTransport` — wrap each command in ``ssh`` when run from a laptop.

Auto-detect with :func:`make_transport`: ``/opt/cyops`` present locally → local,
otherwise an SSH host is required.

Secret hygiene (plan §Safety): secrets (the device UUID used as DB/ES password)
are passed via environment, never via argv — so they never show in ``ps`` and are
not part of the command string a transport logs.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

DEFAULT_TIMEOUT = 60.0

# Marker file that means "this host *is* a FortiSOAR appliance".
_ONBOX_MARKER = "/opt/cyops"

# Env var that carries the SSH password into the ssh child process. The askpass
# helper prints it; it is never placed in argv.
_ASKPASS_PASSWORD_VAR = "PYFSR_SSH_PASSWORD"
# Static helper script: holds no secret, only echoes the env var above.
_ASKPASS_SCRIPT = "#!/bin/sh\nprintf '%s\\n' \"$" + _ASKPASS_PASSWORD_VAR + '"\n'


@contextlib.contextmanager
def _askpass_env(password: str) -> Iterator[dict[str, str]]:
    """Yield env vars that make OpenSSH take ``password`` from an SSH_ASKPASS helper.

    Native replacement for ``sshpass``: plain ``ssh`` asks the helper for the
    password (``SSH_ASKPASS_REQUIRE=force``), so no terminal output is scraped.
    The helper lives in a private 0700 temp dir that is removed when the context
    exits, including on error. The password only travels in the child's env.
    """
    tmp_dir = tempfile.mkdtemp(prefix="pyfsr-askpass-")
    try:
        os.chmod(tmp_dir, 0o700)
        helper = os.path.join(tmp_dir, "askpass")
        fd = os.open(helper, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, "w") as fh:
            fh.write(_ASKPASS_SCRIPT)
        yield {
            "SSH_ASKPASS": helper,
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": os.environ.get("DISPLAY") or ":0",
            _ASKPASS_PASSWORD_VAR: password,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _sudo_wrap(argv: list[str], env: dict[str, str] | None) -> list[str]:
    """Wrap ``argv`` in ``sudo -S`` for privileged appliance commands (``csadm``,
    ``rabbitmqctl``, ``journalctl``, …).

    ``-S`` reads the password from stdin; ``-p ''`` suppresses the prompt so it
    doesn't pollute captured stderr. Because sudo runs with ``env_reset`` by
    default, any ``env`` vars the caller needs are re-applied **inside** the
    privileged context via a leading ``env K=V`` so they actually reach the
    target command (a plain shell ``export`` would be stripped by sudo).
    """
    prefix = ["sudo", "-S", "-p", ""]
    if env:
        prefix += ["env", *[f"{k}={v}" for k, v in env.items()]]
    return [*prefix, *argv]


_DEBUG_VAR = "PYFSR_SSH_DEBUG"
_TRUTHY_DEBUG = {"1", "true", "yes", "y", "on"}


def debug_from_env(env: Mapping[str, str] | None = None) -> bool:
    """``PYFSR_SSH_DEBUG`` as a bool (off unless set to 1/true/yes/on)."""
    e = os.environ if env is None else env
    return (e.get(_DEBUG_VAR) or "").strip().lower() in _TRUTHY_DEBUG


# (pattern in ssh/sudo stderr, plain-language reason). First match wins.
_SSH_REASONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Could not resolve hostname", re.I), "host name does not resolve (check the host / DNS)"),
    (re.compile(r"Connection refused", re.I), "nothing is listening on that SSH port (wrong port, or sshd is down)"),
    (
        re.compile(r"(Connection timed out|Operation timed out|No route to host)", re.I),
        "cannot reach the host on that port (network, VPN or firewall)",
    ),
    (
        re.compile(r"REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed", re.I),
        "the host key changed or is not trusted (box rebuilt? remove the old key from ~/.ssh/known_hosts)",
    ),
    (re.compile(r"Permission denied \(", re.I), "SSH login rejected: wrong user, password or key"),
    (
        re.compile(r"Too many authentication failures", re.I),
        "SSH gave up after offering too many keys (pass --key, or use password auth)",
    ),
    (re.compile(r"Connection (closed|reset) by", re.I), "the server closed the connection during login"),
]
_SUDO_REASONS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"incorrect password|Sorry, try again", re.I),
        "sudo password rejected (set the right sudo password; it may differ from the SSH password)",
    ),
    (
        re.compile(r"no password was provided|a password is required|no tty present", re.I),
        "sudo needs a password and none was given (set a sudo password)",
    ),
    (
        re.compile(r"not in the sudoers file|is not allowed to (execute|run)|may not run sudo", re.I),
        "this user is not allowed to use sudo for that command",
    ),
]


def diagnose_failure(returncode: int, stderr: str, *, ssh: bool, sudo: bool) -> str | None:
    """A plain-language reason for a failed command, or ``None`` if it is the command's own failure."""
    if returncode == 0:
        return None
    if sudo:
        for rx, why in _SUDO_REASONS:
            if rx.search(stderr):
                return why
    if ssh and returncode == 255:
        for rx, why in _SSH_REASONS:
            if rx.search(stderr):
                return why
        return "ssh itself failed before the command ran (exit 255)"
    return None


def _redact(cmd: list[str], secrets: list[str]) -> list[str]:
    out = []
    for part in cmd:
        for sec in secrets:
            if sec:
                part = part.replace(sec, "***")
        out.append(part)
    return out


def _debug_line(target: str, cmd: list[str], result: CommandResult | None, seconds: float, note: str = "") -> None:
    head = f"[pyfsr ssh] {target} ({seconds:.2f}s)"
    if result is None:
        print(f"{head} {note}: {shlex.join(cmd)}", file=sys.stderr)
        return
    print(f"{head} exit {result.returncode}: {shlex.join(cmd)}", file=sys.stderr)
    if result.diagnosis:
        print(f"[pyfsr ssh]   reason: {result.diagnosis}", file=sys.stderr)
    if result.returncode != 0 and result.stderr.strip():
        tail = result.stderr.strip().splitlines()[-8:]
        for line in tail:
            print(f"[pyfsr ssh]   stderr: {line}", file=sys.stderr)


class CommandResult(BaseModel):
    """Outcome of a single transport command."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    #: Why it failed, in plain words, when the cause is SSH or sudo rather than the command.
    diagnosis: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def check(self) -> CommandResult:
        """Raise :class:`TransportError` unless the command exited 0."""
        if not self.ok:
            reason = f"{self.diagnosis}\n" if self.diagnosis else ""
            raise TransportError(
                f"{reason}command failed ({self.returncode}): {shlex.join(self.argv)}\n{self.stderr.strip()}"
            )
        return self


class TransportError(RuntimeError):
    """A transport command failed or could not be dispatched."""


class Transport:
    """Abstract command runner. Subclasses implement :meth:`run`."""

    #: Human label for the target, echoed before mutating ops.
    target: str = "<unknown>"
    #: Log each command to stderr (see :func:`debug_from_env`).
    debug: bool = False

    def run(
        self,
        argv: list[str],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        sudo: bool = False,
    ) -> CommandResult:
        raise NotImplementedError


@dataclass
class LocalTransport(Transport):
    """Run commands directly on the local appliance.

    ``sudo=True`` uses ``sudo -S`` and pipes ``sudo_password`` on stdin (the
    appliance convention) unless passwordless sudo is configured.
    """

    sudo_password: str | None = None
    target: str = "localhost"
    debug: bool = False

    def __init__(self, sudo_password: str | None = None, *, debug: bool = False) -> None:
        self.sudo_password = sudo_password
        self.debug = debug

    def run(
        self,
        argv: list[str],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        sudo: bool = False,
    ) -> CommandResult:
        full_env = {**os.environ, **(env or {})}
        stdin = input_text
        if sudo:
            cmd = _sudo_wrap(list(argv), env)
            # -S reads the password from stdin; prepend it if we have one. (With
            # NOPASSWD sudo this extra line is ignored by our env/csadm targets,
            # which don't read stdin.)
            if self.sudo_password is not None:
                stdin = f"{self.sudo_password}\n{input_text or ''}"
        else:
            cmd = list(argv)
        shown = _redact(cmd, list((env or {}).values()))
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:  # pragma: no cover - env dependent
            raise TransportError(f"executable not found: {cmd[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            if self.debug:
                _debug_line(self.target, shown, None, time.perf_counter() - t0, f"timed out after {timeout}s")
            raise TransportError(f"command timed out after {timeout}s: {shlex.join(shown)}") from exc
        result = CommandResult(
            argv=shown,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            diagnosis=diagnose_failure(proc.returncode, proc.stderr, ssh=False, sudo=sudo),
        )
        if self.debug:
            _debug_line(self.target, shown, result, time.perf_counter() - t0)
        return result


@dataclass
class SSHTransport(Transport):
    """Run commands on a remote appliance over ``ssh``.

    Uses key-based auth by default. If ``password`` is set (and no ``key_path``),
    the password is supplied through OpenSSH's SSH_ASKPASS hook, so no external
    helper binary is needed. Each command is sent as a
    single remote shell string; ``env`` vars are exported remotely (never placed
    in local argv) so secrets stay off the local process table.
    """

    host: str = ""
    user: str = "csadmin"
    password: str | None = None
    port: int = 22
    key_path: str | None = None
    sudo_password: str | None = None
    #: When True, disable SSH host-key checking entirely (StrictHostKeyChecking=no +
    #: a throwaway known_hosts). Off by default — opt in only for ephemeral lab boxes
    #: whose host keys churn, accepting the MITM risk. The secure default
    #: (accept-new) trusts a key on first sight and pins it in ~/.ssh/known_hosts.
    insecure_skip_host_key_check: bool = False
    extra_ssh_opts: list[str] = field(default_factory=list)
    #: Print each command, its exit code, time and (on failure) stderr and reason to
    #: stderr, and let ssh report its own connection steps. Secrets are masked.
    debug: bool = False

    def __post_init__(self) -> None:
        if not self.host:
            raise TransportError("SSHTransport requires a host")
        self.target = f"{self.user}@{self.host}"
        # Fall back to the login password for sudo if none given explicitly.
        if self.sudo_password is None:
            self.sudo_password = self.password

    def _ssh_prefix(self) -> list[str]:
        if self.insecure_skip_host_key_check:
            # Explicit opt-in: trust any key, persist nothing. MITM-exposed.
            opts = [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
            ]
        else:
            # Secure default: trust-on-first-use, pinned in the user's known_hosts;
            # a changed key thereafter aborts the connection.
            opts = ["-o", "StrictHostKeyChecking=accept-new"]
        opts += [
            "-o",
            "LogLevel=VERBOSE" if self.debug else "LogLevel=ERROR",
            "-p",
            str(self.port),
        ]
        if self.key_path:
            opts += ["-i", self.key_path]
        opts += self.extra_ssh_opts
        # Password auth needs no argv change: run() supplies it via _askpass_env.
        return ["ssh", *opts, f"{self.user}@{self.host}"]

    def _askpass(self) -> contextlib.AbstractContextManager[dict[str, str]]:
        """Askpass env for password auth; a no-op context for key/agent auth."""
        if self.password and not self.key_path:
            return _askpass_env(self.password)
        return contextlib.nullcontext({})

    def run(
        self,
        argv: list[str],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        sudo: bool = False,
    ) -> CommandResult:
        # Build the remote command string. For sudo, _sudo_wrap re-applies env
        # *inside* the privileged context (sudo's env_reset strips a shell
        # export); for non-sudo we export env into the remote shell so it reaches
        # the command (e.g. PGPASSWORD for psql).
        if sudo:
            remote_cmd = shlex.join(_sudo_wrap(list(argv), env))
        else:
            exports = "".join(f"export {k}={shlex.quote(v)}; " for k, v in (env or {}).items())
            remote_cmd = exports + shlex.join(argv)

        stdin = input_text
        if sudo and self.sudo_password is not None:
            stdin = f"{self.sudo_password}\n{input_text or ''}"

        cmd = [*self._ssh_prefix(), remote_cmd]
        # The password never appears in argv (it is only in the env); env values
        # (DB passwords and the like) are masked in what is echoed or logged.
        echoed = _redact(cmd, [shlex.quote(v) for v in (env or {}).values()] + list((env or {}).values()))
        t0 = time.perf_counter()
        # The askpass helper's temp dir exists only for the duration of ssh.
        with self._askpass() as askpass_env:
            local_env = {**os.environ, **askpass_env}
            try:
                proc = subprocess.run(
                    cmd,
                    input=stdin,
                    env=local_env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except FileNotFoundError as exc:  # pragma: no cover - env dependent
                raise TransportError(f"executable not found: {cmd[0]}") from exc
            except subprocess.TimeoutExpired as exc:
                if self.debug:
                    _debug_line(self.target, echoed, None, time.perf_counter() - t0, f"timed out after {timeout}s")
                raise TransportError(
                    f"ssh command timed out after {timeout}s on {self.target}:{self.port} "
                    "(host unreachable, or the command hung; run `pyfsr appliance doctor` to see which)"
                ) from exc
        result = CommandResult(
            argv=echoed,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            diagnosis=diagnose_failure(proc.returncode, proc.stderr, ssh=True, sudo=sudo),
        )
        if self.debug:
            _debug_line(f"{self.target}:{self.port}", echoed, result, time.perf_counter() - t0)
        return result


def is_onbox() -> bool:
    """True if the local host looks like a FortiSOAR appliance."""
    return Path(_ONBOX_MARKER).is_dir()


def make_transport(
    *,
    host: str | None = None,
    user: str = "csadmin",
    password: str | None = None,
    port: int = 22,
    key_path: str | None = None,
    sudo_password: str | None = None,
    insecure_skip_host_key_check: bool = False,
    debug: bool | None = None,
) -> Transport:
    """Pick a transport: explicit ``host`` → SSH; else local if on-box.

    Env fallbacks (used when the matching arg is None): ``PYFSR_APPLIANCE_HOST``,
    ``PYFSR_APPLIANCE_USER``, ``PYFSR_APPLIANCE_PASSWORD``, ``PYFSR_SSH_DEBUG``
    (``debug``; off by default).
    """
    if debug is None:
        debug = debug_from_env()
    host = host or os.environ.get("PYFSR_APPLIANCE_HOST")
    if host:
        return SSHTransport(
            host=host,
            user=user or os.environ.get("PYFSR_APPLIANCE_USER", "csadmin"),
            password=password or os.environ.get("PYFSR_APPLIANCE_PASSWORD"),
            port=port,
            key_path=key_path,
            sudo_password=sudo_password,
            insecure_skip_host_key_check=insecure_skip_host_key_check,
            debug=debug,
        )
    if is_onbox():
        return LocalTransport(sudo_password=sudo_password or password, debug=debug)
    raise TransportError(
        "no appliance target: not running on a FortiSOAR box (/opt/cyops absent) and no "
        "--host / PYFSR_APPLIANCE_HOST given"
    )


_TRUTHY = {"1", "true", "yes", "y", "on"}


def _env_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def transport_from_env(env: dict[str, str] | None = None) -> Transport:
    """Build an appliance :class:`Transport` from ``PYFSR_APPLIANCE_*`` env vars.

    Mirrors fsr-agentic-toolkit's ``client_from_env`` but for the SSH/local
    command transport the appliance verbs use (not the REST client). Recognized
    vars: ``PYFSR_APPLIANCE_HOST`` (required for SSH; absent → local if on-box),
    ``PYFSR_APPLIANCE_USER`` (default ``csadmin``), ``PYFSR_APPLIANCE_PASSWORD``,
    ``PYFSR_APPLIANCE_PORT`` (default 22), ``PYFSR_APPLIANCE_KEY_PATH``,
    ``PYFSR_APPLIANCE_SUDO_PASSWORD``, ``PYFSR_APPLIANCE_INSECURE_SKIP_HOST_KEY_CHECK``.

    Pass an explicit ``env`` dict for tests; otherwise ``os.environ`` is used.
    Raises :class:`TransportError` when no host is configured and this process
    isn't running on a FortiSOAR appliance.
    """
    e = os.environ if env is None else env
    host = (e.get("PYFSR_APPLIANCE_HOST") or "").strip()
    port_raw = (e.get("PYFSR_APPLIANCE_PORT") or "").strip()
    return make_transport(
        host=host or None,
        user=(e.get("PYFSR_APPLIANCE_USER") or "").strip() or "csadmin",
        password=e.get("PYFSR_APPLIANCE_PASSWORD") or None,
        port=int(port_raw) if port_raw.isdigit() else 22,
        key_path=(e.get("PYFSR_APPLIANCE_KEY_PATH") or "").strip() or None,
        sudo_password=e.get("PYFSR_APPLIANCE_SUDO_PASSWORD") or None,
        insecure_skip_host_key_check=_env_truthy(e.get("PYFSR_APPLIANCE_INSECURE_SKIP_HOST_KEY_CHECK")),
        debug=debug_from_env(e),
    )
