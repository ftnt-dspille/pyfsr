"""``pyfsr appliance logs`` — log tail / error scan for cyops services.

Encodes the log paths so the caller doesn't re-derive them, and rolls up recent
application errors + the journal for a quick "what just broke" view.
"""

from __future__ import annotations

from .transport import Transport

# Canonical log paths per cyops service alias (plan: "knows the paths").
LOG_PATHS: dict[str, str] = {
    "auth": "/var/log/cyops/cyops-auth/cyops-auth.log",
    "api": "/var/log/cyops/cyops-api/cyops-api.log",
    "postman": "/var/log/cyops/cyops-postman/cyops-postman.log",
    "integrations": "/var/log/cyops/cyops-integrations/cyops-integrations.log",
    "workflow": "/var/log/cyops/cyops-workflow/cyops-workflow.log",
    "nginx": "/var/log/nginx/error.log",
}

# systemd units to roll up in `logs scan`.
_SCAN_UNITS = ["cyops-auth", "cyops-api", "cyops-workflow", "cyops-integrations", "celeryd"]


def tail(transport: Transport, service: str, *, lines: int = 100) -> str:
    """Tail the log for ``service`` (alias from :data:`LOG_PATHS`) or a raw path."""
    path = LOG_PATHS.get(service, service)
    if service not in LOG_PATHS and "/" not in service:
        raise ValueError(f"unknown service {service!r}; known: {', '.join(LOG_PATHS)} (or pass a full path)")
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
