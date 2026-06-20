"""``pyfsr appliance service`` — systemd / cyops service verbs.

Wraps ``csadm services`` plus a **liveness** probe that catches the failure
``systemctl status`` misses: a service that is *active* but **wedged** (accepting
connections but never responding). The probe curls canonical endpoints on-box with
a hard timeout; HTTP 000 / a hang ⇒ "active but wedged → restart".
"""

from __future__ import annotations

from dataclasses import dataclass

from .transport import Transport

# Canonical endpoints whose health reflects the core services (plan §service
# liveness). Each is curled on-box against the local nginx with a short timeout.
# (method, path, expected-up codes, label)
_PROBES: list[tuple[str, str, set[int], str]] = [
    ("POST", "/auth/authenticate", {400, 401, 200}, "auth (cyops-auth)"),
    ("GET", "/auth/license?param=eula", {200, 401}, "license (das)"),
    ("GET", "/api/3", {200, 401, 503}, "api entrypoint"),
]

# A probe is "wedged" when curl returns 000 (no HTTP response / timeout) rather
# than any real status code.
_NO_RESPONSE = 0


@dataclass
class ProbeResult:
    label: str
    method: str
    path: str
    code: int
    verdict: str


def status(transport: Transport, name: str | None = None) -> str:
    """Raw ``csadm services --status`` output (optionally for one service)."""
    argv = ["csadm", "services", "--status"]
    if name:
        argv += ["--name", name]
    return transport.run(argv, sudo=True).stdout.strip()


def liveness(transport: Transport, *, base: str = "https://127.0.0.1", timeout: float = 6.0):
    """Probe canonical endpoints; flag *active-but-wedged* services.

    Returns a list of :class:`ProbeResult`. ``code == 0`` (curl's ``000``) means
    no HTTP response within the timeout — the wedge signal.
    """
    results: list[ProbeResult] = []
    for method, path, up_codes, label in _PROBES:
        url = f"{base}{path}"
        # -s silent, -k skip TLS verify (self-signed appliance cert), -o discard
        # body, -w print only the status code, --max-time bounds a wedged box.
        argv = [
            "curl",
            "-sk",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            str(int(timeout)),
            "-X",
            method,
            url,
        ]
        res = transport.run(argv, timeout=timeout + 5)
        code = _parse_code(res.stdout)
        if code == _NO_RESPONSE:
            verdict = "WEDGED (no response — restart candidate)"
        elif code in up_codes:
            verdict = "ok"
        else:
            verdict = f"unexpected ({code})"
        results.append(ProbeResult(label, method, path, code, verdict))
    return results


def restart(transport: Transport, name: str, *, yes: bool = False) -> str:
    """Restart a cyops service via ``csadm services --restart``. Gated by ``yes``."""
    if not yes:
        raise PermissionError(f"refusing to restart {name!r} without confirmation (pass --yes)")
    return transport.run(["csadm", "services", "--restart", "--name", name], sudo=True, timeout=120).stdout.strip()


def listeners(transport: Transport):
    """Listening TCP ports with the owning process (``ss -tlnp``).

    Returns ``(headers, rows)``. Falls back to raw lines if parsing fails.
    """
    res = transport.run(["ss", "-tlnp"], sudo=True)
    rows = []
    for line in res.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[3]
        proc = parts[-1] if "users:" in parts[-1] else ""
        rows.append([local, proc])
    return ["local_address", "process"], rows


def _parse_code(stdout: str) -> int:
    try:
        return int(stdout.strip() or "0")
    except ValueError:
        return 0
