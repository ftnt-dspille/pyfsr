"""``pyfsr appliance mq`` — RabbitMQ verbs (thin ``rabbitmqctl`` wrappers).

Surfaces the auth/vhost/queue-depth/consumer checks from the diagnoser, and flags
the two stuck-worker tells: deep queues (backlog) and queues with **zero
consumers**. ``rabbitmqctl`` needs root on the appliance, so every call runs sudo.
"""

from __future__ import annotations

from .transport import Transport

# A queue depth at/above this is called out as a backlog.
_DEEP_QUEUE = 1000


def _ctl(transport: Transport, *args: str, timeout: float = 30.0):
    """Run ``rabbitmqctl -q <args>`` (quiet = no decorative table headers)."""
    return transport.run(["rabbitmqctl", "-q", *args], sudo=True, timeout=timeout)


def _tabular(stdout: str) -> list[list[str]]:
    """Split rabbitmqctl's tab-separated quiet output into rows."""
    rows = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        rows.append(line.split("\t"))
    return rows


def status(transport: Transport) -> str:
    """Raw ``rabbitmqctl status``."""
    return _ctl(transport, "status").stdout.strip()


def queues(transport: Transport):
    """List queues with depth + consumer count; flag backlogs and zero-consumer
    queues. Returns ``(headers, rows)`` where each row gains a ``flag`` column."""
    res = _ctl(transport, "list_queues", "name", "messages", "consumers")
    rows = []
    for parts in _tabular(res.stdout):
        if len(parts) < 3:
            continue
        name, messages, consumers = parts[0], parts[1], parts[2]
        flag = ""
        if _to_int(consumers) == 0:
            flag = "NO CONSUMERS"
        elif _to_int(messages) >= _DEEP_QUEUE:
            flag = f"BACKLOG (>{_DEEP_QUEUE})"
        rows.append([name, messages, consumers, flag])
    return ["queue", "messages", "consumers", "flag"], rows


def consumers(transport: Transport):
    """List consumers (``queue_name`` ↔ ``channel_pid``)."""
    res = _ctl(transport, "list_consumers")
    return ["consumer"], _tabular(res.stdout)


def vhosts(transport: Transport):
    """List virtual hosts."""
    res = _ctl(transport, "list_vhosts")
    return ["vhost"], _tabular(res.stdout)


def permissions(transport: Transport):
    """List per-vhost permissions (``user  conf  write  read``)."""
    res = _ctl(transport, "list_permissions")
    return ["user", "configure", "write", "read"], _tabular(res.stdout)


def _to_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return -1
