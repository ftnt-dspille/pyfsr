"""``pyfsr appliance doctor``: check the SSH path one step at a time.

Each step needs the one before it, so the report stops at the first failure and
names it: the host cannot be reached, the SSH login is refused, the box is not a
FortiSOAR appliance, sudo refuses the password, or ``csadm`` is missing. Every
step is read-only (``true``, ``test -d``, ``command -v``).
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable

from pydantic import BaseModel

from .transport import CommandResult, LocalTransport, SSHTransport, Transport, TransportError

_ONBOX = "/opt/cyops"


class Step(BaseModel):
    name: str
    status: str  # ok | fail | skip
    detail: str = ""
    hint: str = ""
    seconds: float = 0.0


class DoctorReport(BaseModel):
    target: str
    steps: list[Step]

    @property
    def ok(self) -> bool:
        return all(s.status == "ok" for s in self.steps)

    @property
    def first_failure(self) -> Step | None:
        return next((s for s in self.steps if s.status == "fail"), None)

    def render(self) -> str:
        mark = {"ok": "ok  ", "fail": "FAIL", "skip": "skip"}
        lines = [f"appliance doctor: {self.target}"]
        for s in self.steps:
            timing = f" ({s.seconds:.1f}s)" if s.status != "skip" else ""
            lines.append(f"  {mark[s.status]} {s.name}{timing}" + (f": {s.detail}" if s.detail else ""))
            if s.hint:
                lines.append(f"       -> {s.hint}")
        bad = self.first_failure
        lines.append("all steps passed" if bad is None else f"stopped at: {bad.name}")
        return "\n".join(lines)


def _auth_kind(t: SSHTransport) -> str:
    if t.key_path:
        return f"key {t.key_path}"
    if t.password:
        return "password"
    return "ssh agent / default keys"


def _config_step(t: Transport) -> Step:
    if isinstance(t, SSHTransport):
        sudo = "not set"
        if t.sudo_password:
            sudo = "same as the SSH password" if t.sudo_password == t.password else "set separately"
        detail = f"{t.user}@{t.host}:{t.port}, auth {_auth_kind(t)}, sudo password {sudo}"
        if t.insecure_skip_host_key_check:
            detail += ", host-key check off"
        return Step(name="configuration", status="ok", detail=detail)
    return Step(name="configuration", status="ok", detail="running on the appliance (local commands)")


def _reach_step(t: SSHTransport, timeout: float) -> Step:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((t.host, t.port), timeout=timeout):
            pass
    except socket.gaierror as exc:
        return Step(
            name="reach host",
            status="fail",
            detail=f"{t.host} does not resolve ({exc})",
            hint="check the host name, DNS or VPN",
            seconds=time.perf_counter() - t0,
        )
    except ConnectionRefusedError:
        return Step(
            name="reach host",
            status="fail",
            detail=f"port {t.port} refused the connection",
            hint="wrong SSH port, or sshd is not running",
            seconds=time.perf_counter() - t0,
        )
    except OSError as exc:
        return Step(
            name="reach host",
            status="fail",
            detail=f"{t.host}:{t.port} unreachable ({exc})",
            hint="network, VPN or firewall between here and the box",
            seconds=time.perf_counter() - t0,
        )
    return Step(name="reach host", status="ok", detail=f"TCP {t.host}:{t.port} open", seconds=time.perf_counter() - t0)


def _cmd_step(
    name: str,
    t: Transport,
    argv: list[str],
    *,
    sudo: bool = False,
    timeout: float,
    ok_detail: str,
    fail_hint: Callable[[CommandResult], str],
) -> Step:
    t0 = time.perf_counter()
    try:
        r = t.run(argv, sudo=sudo, timeout=timeout)
    except TransportError as exc:
        return Step(name=name, status="fail", detail=str(exc), seconds=time.perf_counter() - t0)
    secs = time.perf_counter() - t0
    if r.ok:
        return Step(name=name, status="ok", detail=ok_detail, seconds=secs)
    last = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else f"exit {r.returncode}"
    return Step(name=name, status="fail", detail=r.diagnosis or last, hint=fail_hint(r), seconds=secs)


def run(t: Transport, *, timeout: float = 20.0) -> DoctorReport:
    """Run every step in order, stopping at the first failure. Read-only."""
    steps: list[Step] = [_config_step(t)]
    plan: list[Callable[[], Step]] = []
    if isinstance(t, SSHTransport):
        plan.append(lambda: _reach_step(t, min(timeout, 8.0)))
        plan.append(
            lambda: _cmd_step(
                "ssh login",
                t,
                ["true"],
                timeout=timeout,
                ok_detail=f"logged in as {t.user}",
                fail_hint=lambda r: "" if r.diagnosis else "rerun with --debug to see ssh's own messages",
            )
        )
    plan.append(
        lambda: _cmd_step(
            "FortiSOAR appliance",
            t,
            ["test", "-d", _ONBOX],
            timeout=timeout,
            ok_detail=f"{_ONBOX} present",
            fail_hint=lambda r: "this host is not a FortiSOAR appliance (or the wrong host/port)",
        )
    )
    plan.append(
        lambda: _cmd_step(
            "sudo",
            t,
            ["true"],
            sudo=True,
            timeout=timeout,
            ok_detail="sudo accepted",
            fail_hint=lambda r: (
                "set --sudo-password / PYFSR_APPLIANCE_SUDO_PASSWORD, or leave it unset to use the SSH password"
                if r.diagnosis and "password" in r.diagnosis
                else ""
            ),
        )
    )
    plan.append(
        lambda: _cmd_step(
            "csadm",
            t,
            ["sh", "-c", "command -v csadm"],
            sudo=True,
            timeout=timeout,
            ok_detail="csadm found",
            fail_hint=lambda r: "csadm is not on root's PATH; appliance verbs that use it will fail",
        )
    )
    names = ["reach host", "ssh login"] if isinstance(t, SSHTransport) else []
    names += ["FortiSOAR appliance", "sudo", "csadm"]
    for i, step in enumerate(plan):
        result = step()
        steps.append(result)
        if result.status == "fail":
            steps += [Step(name=n, status="skip") for n in names[i + 1 :]]
            break
    target = t.target if not isinstance(t, LocalTransport) else "localhost"
    if isinstance(t, SSHTransport):
        target = f"{t.target}:{t.port}"
    return DoctorReport(target=target, steps=steps)
