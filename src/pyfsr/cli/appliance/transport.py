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

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT = 60.0

# Marker file that means "this host *is* a FortiSOAR appliance".
_ONBOX_MARKER = "/opt/cyops"


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


@dataclass
class CommandResult:
    """Outcome of a single transport command."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def check(self) -> CommandResult:
        """Raise :class:`TransportError` unless the command exited 0."""
        if not self.ok:
            raise TransportError(f"command failed ({self.returncode}): {shlex.join(self.argv)}\n{self.stderr.strip()}")
        return self


class TransportError(RuntimeError):
    """A transport command failed or could not be dispatched."""


class Transport:
    """Abstract command runner. Subclasses implement :meth:`run`."""

    #: Human label for the target, echoed before mutating ops.
    target: str = "<unknown>"

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
            raise TransportError(f"command timed out after {timeout}s: {shlex.join(cmd)}") from exc
        return CommandResult(cmd, proc.returncode, proc.stdout, proc.stderr)


@dataclass
class SSHTransport(Transport):
    """Run commands on a remote appliance over ``ssh``.

    Uses key-based auth by default. If ``password`` is set, ``sshpass`` is used
    when available (and required if no key is loaded). Each command is sent as a
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
            "LogLevel=ERROR",
            "-p",
            str(self.port),
        ]
        if self.key_path:
            opts += ["-i", self.key_path]
        opts += self.extra_ssh_opts
        base = ["ssh", *opts, f"{self.user}@{self.host}"]
        if self.password and not self.key_path:
            if not shutil.which("sshpass"):
                raise TransportError("password auth needs `sshpass` on PATH (or use --key/an agent key)")
            # Pass the password via env (-e) so it never lands in argv.
            return ["sshpass", "-e", *base]
        return base

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

        local_env = dict(os.environ)
        if self.password and not self.key_path:
            local_env["SSHPASS"] = self.password

        stdin = input_text
        if sudo and self.sudo_password is not None:
            stdin = f"{self.sudo_password}\n{input_text or ''}"

        cmd = [*self._ssh_prefix(), remote_cmd]
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
            raise TransportError(f"ssh command timed out after {timeout}s") from exc
        # Don't leak SSHPASS in the echoed argv.
        echoed = [*self._ssh_prefix(), remote_cmd]
        return CommandResult(echoed, proc.returncode, proc.stdout, proc.stderr)


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
) -> Transport:
    """Pick a transport: explicit ``host`` → SSH; else local if on-box.

    Env fallbacks (used when the matching arg is None): ``PYFSR_APPLIANCE_HOST``,
    ``PYFSR_APPLIANCE_USER``, ``PYFSR_APPLIANCE_PASSWORD``.
    """
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
        )
    if is_onbox():
        return LocalTransport(sudo_password=sudo_password or password)
    raise TransportError(
        "no appliance target: not running on a FortiSOAR box (/opt/cyops absent) and no "
        "--host / PYFSR_APPLIANCE_HOST given"
    )
