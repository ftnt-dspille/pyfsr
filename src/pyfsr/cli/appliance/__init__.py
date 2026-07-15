"""``pyfsr appliance`` — generic FortiSOAR appliance commands (shell / DB / csadm).

P1 surface: transport (local + SSH), fact resolution (device UUID / content DB /
version), and the ``db`` verbs incl. ``db exec --write`` and module-table cleanup.
See ``docs/plans/APPLIANCE_CLI_PLAN.md`` for the full intended surface.

The transport classes (``Transport``, ``SSHTransport``, ``make_transport``, …)
live in :mod:`pyfsr.cli.appliance.transport` and are intentionally **not**
re-exported here — users should construct an :class:`pyfsr.Appliance` with
connection kwargs instead.  Import from ``pyfsr.cli.appliance.transport`` only
when you need the low-level transport directly (e.g. for testing).
"""

from __future__ import annotations

from . import certs, content_hub, db, facts, host, info, logs, mq, service, transport

__all__ = [
    "certs",
    "content_hub",
    "db",
    "facts",
    "host",
    "info",
    "logs",
    "mq",
    "service",
    "transport",
]
