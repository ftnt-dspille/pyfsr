"""``pyfsr appliance ha`` — FortiSOAR HA clustering verbs.

Thin wrappers over ``csadm ha`` subcommands. All require root (sudo).
"""

from __future__ import annotations

from .transport import Transport


def _ha(transport: Transport, *args: str, timeout: float = 30.0) -> str:
    """Run ``csadm ha <args>`` under sudo."""
    return transport.run(["csadm", "ha", *args], sudo=True, timeout=timeout).check().stdout.strip()


def nodes(transport: Transport) -> str:
    """``csadm ha list-nodes`` — list HA cluster members."""
    return _ha(transport, "list-nodes")


def health(transport: Transport) -> str:
    """``csadm ha show-health`` — HA node health summary."""
    return _ha(transport, "show-health")


def replication(transport: Transport) -> str:
    """``csadm ha get-replication-stat`` — DB replication lag and status."""
    return _ha(transport, "get-replication-stat")
