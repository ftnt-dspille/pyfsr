"""``pyfsr appliance mq`` — RabbitMQ verbs (thin ``rabbitmqctl`` wrappers).

Surfaces the auth/vhost/queue-depth/consumer checks from the diagnoser, and flags
the two stuck-worker tells: deep queues (backlog) and queues with **zero
consumers**. ``rabbitmqctl`` needs root on the appliance, so every call runs sudo.
"""

from __future__ import annotations

from .transport import CommandResult, Transport

# A queue depth at/above this is called out as a backlog.
_DEEP_QUEUE = 1000


def _ctl(transport: Transport, *args: str, timeout: float = 30.0) -> CommandResult:
    """Run ``rabbitmqctl -q <args>`` (quiet mode)."""
    return transport.run(["rabbitmqctl", "-q", *args], sudo=True, timeout=timeout)


def _list(transport: Transport, *args: str, timeout: float = 30.0) -> CommandResult:
    """Run a ``rabbitmqctl`` *listing* command without the column-header row.

    ``-q`` alone does NOT suppress headers on modern RabbitMQ (verified live on
    3.13.2: ``list_permissions`` still emits ``user configure write read`` and
    ``list_vhosts`` emits ``name``). ``--no-table-headers`` drops that row so the
    tab-split below yields only data — without it the header leaks in as a bogus
    record (e.g. a vhost literally named ``name``). Supported since RabbitMQ 3.8,
    which is the floor FortiSOAR ships.
    """
    return transport.run(["rabbitmqctl", "-q", "--no-table-headers", *args], sudo=True, timeout=timeout)


def _tabular(stdout: str) -> list[list[str]]:
    """Split rabbitmqctl's tab-separated output into rows (headerless — see :func:`_list`)."""
    rows = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        rows.append(line.split("\t"))
    return rows


def status(transport: Transport) -> str:
    """Raw ``rabbitmqctl status``."""
    return _ctl(transport, "status").stdout.strip()


def queues(transport: Transport) -> tuple[list[str], list[list[str]]]:
    """List queues with depth + consumer count; flag backlogs and zero-consumer
    queues. Returns ``(headers, rows)`` where each row gains a ``flag`` column."""
    res = _list(transport, "list_queues", "name", "messages", "consumers")
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


def consumers(transport: Transport) -> tuple[list[str], list[list[str]]]:
    """List consumers (``queue_name`` ↔ ``channel_pid``)."""
    res = _list(transport, "list_consumers")
    return ["consumer"], _tabular(res.stdout)


def vhosts(transport: Transport) -> tuple[list[str], list[list[str]]]:
    """List virtual hosts."""
    res = _list(transport, "list_vhosts")
    return ["vhost"], _tabular(res.stdout)


def permissions(transport: Transport, *, all_vhosts: bool = False) -> tuple[list[str], list[list[str]]]:
    """List permissions for the default vhost, or every vhost with ``all_vhosts``.

    ``rabbitmqctl list_permissions`` is scoped to a single vhost (the default
    ``/``). With ``all_vhosts=True`` this enumerates the vhosts and runs the
    per-vhost query for each, prepending a ``vhost`` column so rows from different
    vhosts stay distinguishable — the multi-vhost permission matrix the
    single-vhost call can't give you.
    """
    if not all_vhosts:
        res = _list(transport, "list_permissions")
        return ["user", "configure", "write", "read"], _tabular(res.stdout)

    _, vhost_rows = vhosts(transport)
    matrix: list[list[str]] = []
    for vrow in vhost_rows:
        if not vrow:
            continue
        vhost = vrow[0]
        res = _list(transport, "list_permissions", "-p", vhost)
        for parts in _tabular(res.stdout):
            matrix.append([vhost, *parts])
    return ["vhost", "user", "configure", "write", "read"], matrix


def _to_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return -1
