"""``pyfsr appliance logs`` — log tail / error scan for cyops services.

Encodes the log paths so the caller doesn't re-derive them, and rolls up recent
application errors + the journal for a quick "what just broke" view.
"""

from __future__ import annotations

from .transport import Transport

# Canonical log paths per cyops service alias (plan: "knows the paths").
# Verified live against FSR 7.6.5 (the dir name and the file name differ: the
# auth service logs to ``das.log``, the api app log is ``prod.log``, the workflow
# engine is ``fsr-workflow.log``, and postman moved under ``cyops-routing-agent``).
LOG_PATHS: dict[str, str] = {
    "auth": "/var/log/cyops/cyops-auth/das.log",
    "api": "/var/log/cyops/cyops-api/prod.log",
    "postman": "/var/log/cyops/cyops-routing-agent/postman.log",
    "integrations": "/var/log/cyops/cyops-integrations/integrations.log",
    "connectors": "/var/log/cyops/cyops-integrations/connectors.log",
    "workflow": "/var/log/cyops/cyops-workflow/fsr-workflow.log",
    "celery": "/var/log/cyops/cyops-workflow/celeryd.log",
    "gateway": "/var/log/cyops/cyops-gateway/gateway.log",
    "notifier": "/var/log/cyops/cyops-notifier/notifier.log",
    "nginx": "/var/log/nginx/error.log",
}

# systemd units to roll up in `logs scan` (real unit names per `csadm services`).
_SCAN_UNITS = ["cyops-auth", "fsr-api-consumer", "fsr-workflow", "cyops-integrations-agent", "celeryd"]


def tail(transport: Transport, service: str, *, lines: int = 100) -> str:
    """Tail the log for ``service`` (alias from :data:`LOG_PATHS`) or a raw path.

    Raises ``FileNotFoundError`` if the target log does not exist on the box,
    rather than returning an empty string — a missing path is almost always a
    stale alias or a version mismatch, not a genuinely empty log.
    """
    path = LOG_PATHS.get(service, service)
    if service not in LOG_PATHS and "/" not in service:
        raise ValueError(f"unknown service {service!r}; known: {', '.join(LOG_PATHS)} (or pass a full path)")
    if transport.run(["test", "-f", path], sudo=True).returncode != 0:
        raise FileNotFoundError(f"log not found on appliance: {path}")
    return transport.run(["tail", "-n", str(lines), path], sudo=True).stdout


def scan(transport: Transport, *, minutes: int = 30) -> str:
    """Roll up recent errors from the cyops units' journals (last ``minutes``)."""
    out: list[str] = []
    for unit in _SCAN_UNITS:
        res = transport.run(
            [
                "journalctl",
                "-u",
                unit,
                "--since",
                f"-{minutes}min",
                "-p",
                "err",
                "--no-pager",
            ],
            sudo=True,
        )
        body = res.stdout.strip()
        if body and "No entries" not in body:
            out.append(f"=== {unit} (last {minutes}min, errors) ===\n{body}")
    return "\n\n".join(out) if out else f"(no journal errors in the last {minutes} min)"
