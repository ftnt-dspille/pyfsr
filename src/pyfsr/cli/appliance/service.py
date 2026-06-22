"""``pyfsr appliance service`` — systemd / cyops service verbs.

Wraps ``csadm services`` plus a **liveness** probe that catches the failure
``systemctl status`` misses: a service that is *active* but **wedged** (accepting
connections but never responding). The probe curls canonical endpoints on-box with
a hard timeout; HTTP 000 / a hang ⇒ "active but wedged → restart".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._text import strip_ansi
from .transport import Transport

# A `csadm services --status` line: "name......[Status]   since <when>". The name
# is dot-padded out to the status bracket; the trailing "since ..." is optional.
_STATUS_LINE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+?)\.*\[(?P<status>[^\]]+)\]\s*(?:since\s+(?P<since>.*\S))?\s*$")

# csadm's `--{start,stop,restart}-service` verbs validate the name against the
# box's service set (`modify_service_state` in /opt/cyops/adm/subcmd/services) and,
# when it doesn't match, print this rejection and **exit 0** — a silent no-op. The
# exit code is therefore meaningless for these verbs; the only reliable failure
# signal is this text. (Live-verified on a 7.6.x appliance, June 2026.)
_CSADM_REJECT = re.compile(r"can not be modified|can be used for following services", re.IGNORECASE)

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


@dataclass
class ServiceState:
    """One row of ``csadm services --status``, parsed and ANSI-stripped."""

    name: str
    running: bool
    status: str  # the bracketed word verbatim, e.g. "Running" / "Stopped"
    since: str | None  # uptime anchor, e.g. "Thu 2026-05-07 14:10:35 UTC"


def status(transport: Transport, name: str | None = None) -> str:
    """Raw ``csadm services --status`` output (optionally for one service).

    Free-form, ANSI-coloured text — for a typed result use :func:`services`.

    NB: ``csadm services --status`` has **no** ``--name`` flag; passing one is
    silently ignored and the full list comes back (live-verified). So ``name``
    filters the lines client-side here rather than being handed to csadm.
    """
    out = transport.run(["csadm", "services", "--status"], sudo=True).stdout.strip()
    if not name:
        return out
    # Filter on the parsed (ANSI-stripped) service name so the dot-padding and
    # colour codes in the raw line don't defeat a plain substring match.
    kept = []
    for line in out.splitlines():
        m = _STATUS_LINE.match(strip_ansi(line).strip())
        if m and m.group("name") == name:
            kept.append(line)
    return "\n".join(kept)


def services(transport: Transport, name: str | None = None) -> list[ServiceState]:
    """Parsed ``csadm services --status`` — a typed :class:`ServiceState` per service.

    ``running`` is the useful boolean (``status == "Running"``); ``since`` is the
    start time when present. Filter to one service with ``name``.
    """
    out: list[ServiceState] = []
    for line in strip_ansi(status(transport, name)).splitlines():
        m = _STATUS_LINE.match(line.strip())
        if not m:
            continue
        st = m.group("status").strip()
        out.append(
            ServiceState(name=m.group("name"), running=st.lower() == "running", status=st, since=m.group("since"))
        )
    return out


def liveness(transport: Transport, *, base: str = "https://127.0.0.1", timeout: float = 6.0) -> list[ProbeResult]:
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


@dataclass
class ServiceActionResult:
    """Outcome of a service control action (restart/stop/start/kill).

    ``ok`` is the useful field — the command exited 0. ``output`` keeps the raw
    csadm/systemctl text (often empty on success) for diagnostics.
    """

    service: str
    action: str
    ok: bool
    output: str

    def __str__(self) -> str:
        verdict = "ok" if self.ok else "FAILED"
        detail = f" — {self.output}" if self.output else ""
        return f"{self.action} {self.service}: {verdict}{detail}"


@dataclass
class Listener:
    """A listening TCP socket and its owning process (a row of ``ss -tlnp``)."""

    local_address: str
    process: str


# Map a single-service action onto its csadm `--*-service` flag. Whole-stack
# actions (restart/stop/start ALL) use the bare `--restart`/`--stop`/`--start`
# flags instead — see :func:`restart_all` and friends.
_SERVICE_FLAG = {"restart": "--restart-service", "stop": "--stop-service", "start": "--start-service"}
_ALL_FLAG = {"restart": "--restart", "stop": "--stop", "start": "--start"}


def _service_action(transport: Transport, action: str, name: str) -> ServiceActionResult:
    """Run one csadm `--*-service` verb and decode its (unreliable) exit code.

    csadm exits 0 even when it rejects the name as unmodifiable, so success is
    ``res.ok`` AND the output not matching ``_CSADM_REJECT``.
    """
    res = transport.run(["csadm", "services", _SERVICE_FLAG[action], name], sudo=True, timeout=120)
    text = (res.stdout or res.stderr).strip()
    ok = res.ok and not _CSADM_REJECT.search(text)
    return ServiceActionResult(name, action, ok, text)


def _all_action(transport: Transport, action: str) -> ServiceActionResult:
    """Run a whole-stack csadm verb (``--restart``/``--stop``/``--start``)."""
    # A full-stack bounce is serial (stop → sleep 5 → start, per service) so it can
    # run minutes; give it a generous ceiling.
    res = transport.run(["csadm", "services", _ALL_FLAG[action]], sudo=True, timeout=600)
    return ServiceActionResult("ALL", action, res.ok, (res.stdout or res.stderr).strip())


def restart(transport: Transport, name: str, *, yes: bool = False) -> ServiceActionResult:
    """Restart a single cyops service via ``csadm services --restart-service``. Gated by ``yes``.

    Use this — NOT :func:`restart_all` — for one service. The bare ``--restart``
    flag (``restart_all``) bounces the WHOLE stack in order; ``--restart-service``
    touches just ``name`` (stop → sleep 5 → start, per csadm).

    csadm exits 0 even when it refuses an unknown/unmodifiable name (it prints an
    ``ERROR: ... can not be modified`` hint and no-ops), so the returned ``ok``
    folds that rejection text in — a typo'd name yields ``ok=False``, not a false
    success. (live-verified, see ``_CSADM_REJECT``.)
    """
    if not yes:
        raise PermissionError(f"refusing to restart {name!r} without confirmation (pass --yes)")
    return _service_action(transport, "restart", name)


def stop(transport: Transport, name: str, *, yes: bool = False) -> ServiceActionResult:
    """Stop a single cyops service via ``csadm services --stop-service``. Gated by ``yes``.

    Used to quiesce a worker (e.g. ``celeryd``) so a queue can be purged without it
    re-draining mid-operation — see :func:`pyfsr.cli.appliance.mq.purge_workflows`.
    Like :func:`restart`, a rejected name surfaces as ``ok=False`` despite csadm's
    exit 0.
    """
    if not yes:
        raise PermissionError(f"refusing to stop {name!r} without confirmation (pass --yes)")
    return _service_action(transport, "stop", name)


def start(transport: Transport, name: str) -> ServiceActionResult:
    """Start a single cyops service via ``csadm services --start-service`` (recovery — not gated)."""
    return _service_action(transport, "start", name)


def restart_all(transport: Transport, *, yes: bool = False) -> ServiceActionResult:
    """Restart the WHOLE service stack in order via ``csadm services --restart``. Gated by ``yes``.

    This is the full-stack bounce (stop-all → start-all), distinct from
    :func:`restart` which restarts a single named service. Disruptive — every
    service drops — so it is gated behind ``yes``.
    """
    if not yes:
        raise PermissionError("refusing to restart ALL services without confirmation (pass --yes)")
    return _all_action(transport, "restart")


def stop_all(transport: Transport, *, yes: bool = False) -> ServiceActionResult:
    """Stop the WHOLE service stack in order via ``csadm services --stop``. Gated by ``yes``."""
    if not yes:
        raise PermissionError("refusing to stop ALL services without confirmation (pass --yes)")
    return _all_action(transport, "stop")


def start_all(transport: Transport) -> ServiceActionResult:
    """Start the WHOLE service stack in order via ``csadm services --start`` (recovery — not gated)."""
    return _all_action(transport, "start")


# systemctl actions that mutate a unit's run state — gated behind ``yes``. Read-only
# actions (status/is-active/show) are always allowed.
_SYSTEMCTL_MUTATING = frozenset({"stop", "kill", "restart", "start", "reload"})


def systemctl(
    transport: Transport,
    action: str,
    unit: str,
    *,
    signal: str | None = None,
    yes: bool = False,
) -> ServiceActionResult:
    """Drive systemd directly: ``systemctl <action> <unit>`` (sudo).

    This is the forceful path that bypasses ``csadm`` orchestration — use it when a
    unit is wedged and the graceful :func:`stop`/:func:`restart` won't take. ``kill``
    sends ``SIGTERM`` by default; pass ``signal`` (e.g. ``"SIGKILL"``, ``"9"``) to
    escalate. Mutating actions are gated by ``yes``; read-only ones (``status``,
    ``is-active``, ``show``) run unconditionally.

    Returns a :class:`ServiceActionResult`; for read-only actions ``output`` carries
    the queried value (e.g. ``"active"`` from ``is-active``). Note ``unit`` is the
    *systemd* unit name (e.g. ``celeryd.service``), not always the csadm label.
    """
    if action in _SYSTEMCTL_MUTATING and not yes:
        raise PermissionError(f"refusing to {action} {unit!r} without confirmation (pass --yes)")
    argv = ["systemctl", action]
    if action == "kill" and signal:
        # systemd takes the signal as --signal=SIG; accepts names or numbers.
        argv.append(f"--signal={signal}")
    argv.append(unit)
    res = transport.run(argv, sudo=True, timeout=120)
    # `systemctl stop/kill` emit nothing on success; surface stderr so failures
    # (no such unit, permission denied) aren't swallowed into an empty string.
    return ServiceActionResult(unit, action, res.ok, (res.stdout or res.stderr).strip())


def listeners(transport: Transport) -> list[Listener]:
    """Listening TCP sockets with the owning process (parsed ``ss -tlnp``)."""
    res = transport.run(["ss", "-tlnp"], sudo=True)
    out: list[Listener] = []
    for line in res.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 5:
            continue
        proc = parts[-1] if "users:" in parts[-1] else ""
        out.append(Listener(local_address=parts[3], process=proc))
    return out


def _parse_code(stdout: str) -> int:
    try:
        return int(stdout.strip() or "0")
    except ValueError:
        return 0
