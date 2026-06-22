"""``pyfsr appliance`` — generic FortiSOAR appliance commands (shell / DB / csadm).

P1 surface: transport (local + SSH), fact resolution (device UUID / content DB /
version), and the ``db`` verbs incl. ``db exec --write`` and module-table cleanup.
See ``docs/plans/APPLIANCE_CLI_PLAN.md`` for the full intended surface.
"""

from __future__ import annotations

from . import certs, db, facts, host, info, logs, mq, service, transport
from .facts import Facts
from .transport import (
    CommandResult,
    LocalTransport,
    SSHTransport,
    Transport,
    TransportError,
    make_transport,
)

__all__ = [
    "certs",
    "db",
    "facts",
    "host",
    "info",
    "logs",
    "mq",
    "service",
    "transport",
    "Facts",
    "Transport",
    "LocalTransport",
    "SSHTransport",
    "TransportError",
    "CommandResult",
    "make_transport",
]
