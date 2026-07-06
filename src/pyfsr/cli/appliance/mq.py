"""``pyfsr appliance mq`` — RabbitMQ verbs (thin ``rabbitmqctl`` wrappers).

Surfaces the auth/vhost/queue-depth/consumer checks from the diagnoser, and flags
the two stuck-worker tells: deep queues (backlog) and queues with **zero
consumers**. ``rabbitmqctl`` needs root on the appliance, so every call runs sudo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import service
from .transport import CommandResult, Transport

# A queue depth at/above this is called out as a backlog.
_DEEP_QUEUE = 1000

# FortiSOAR's primary workflow task broker: the `celery` queue in vhost
# `fsr-cluster`. Queued tasks survive a celeryd restart and re-dispatch the moment
# workers return — so draining the backlog means purge, not just restart.
_WORKFLOW_VHOST = "fsr-cluster"
_WORKFLOW_QUEUE = "celery"
# Secondary data/status queues live in this vhost; sweep any that are non-empty.
_DATA_VHOST = "intra-cyops"


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


@dataclass
class QueueInfo:
    """One queue's depth and consumer count, with a stuck-worker ``flag``."""

    name: str
    messages: int
    consumers: int
    flag: str  # "" | "NO CONSUMERS" | "BACKLOG (>N)"


@dataclass
class Consumer:
    """A consumer binding (``queue_name`` ↔ ``channel_pid``)."""

    queue: str
    channel: str


@dataclass
class Permission:
    """A user's ``configure``/``write``/``read`` regexes on a vhost."""

    vhost: str
    user: str
    configure: str
    write: str
    read: str


def queues(transport: Transport) -> list[QueueInfo]:
    """List queues with depth + consumer count; flag backlogs and zero-consumer queues."""
    res = _list(transport, "list_queues", "name", "messages", "consumers")
    out: list[QueueInfo] = []
    for parts in _tabular(res.stdout):
        if len(parts) < 3:
            continue
        messages, consumers = _to_int(parts[1]), _to_int(parts[2])
        flag = ""
        if consumers == 0:
            flag = "NO CONSUMERS"
        elif messages >= _DEEP_QUEUE:
            flag = f"BACKLOG (>{_DEEP_QUEUE})"
        out.append(QueueInfo(name=parts[0], messages=messages, consumers=consumers, flag=flag))
    return out


def consumers(transport: Transport) -> list[Consumer]:
    """List consumers (``queue_name`` ↔ ``channel_pid``)."""
    out: list[Consumer] = []
    for parts in _tabular(_list(transport, "list_consumers").stdout):
        out.append(Consumer(queue=parts[0], channel=parts[1] if len(parts) > 1 else ""))
    return out


def vhosts(transport: Transport) -> list[str]:
    """List virtual host names."""
    return [parts[0] for parts in _tabular(_list(transport, "list_vhosts").stdout) if parts]


def permissions(transport: Transport, *, all_vhosts: bool = False) -> list[Permission]:
    """Permissions for the default vhost, or every vhost with ``all_vhosts``.

    ``rabbitmqctl list_permissions`` is scoped to a single vhost (the default
    ``/``). With ``all_vhosts=True`` this enumerates the vhosts and runs the
    per-vhost query for each — the multi-vhost permission matrix the single-vhost
    call can't give you.
    """
    out: list[Permission] = []
    targets = vhosts(transport) if all_vhosts else ["/"]
    for vhost in targets:
        args = ["list_permissions", "-p", vhost] if all_vhosts else ["list_permissions"]
        for parts in _tabular(_list(transport, *args).stdout):
            if len(parts) >= 4:
                out.append(Permission(vhost=vhost, user=parts[0], configure=parts[1], write=parts[2], read=parts[3]))
    return out


def queue_depth(transport: Transport, queue: str, *, vhost: str | None = None) -> int:
    """Depth (pending message count) of a single ``queue``, or ``-1`` if absent.

    Scoped to ``vhost`` when given. Cleanly typed: parses ``rabbitmqctl
    list_queues`` and matches the named queue rather than awk-ing a tab field.
    """
    args = ["list_queues", "name", "messages"]
    if vhost:
        args += ["-p", vhost]
    res = _list(transport, *args)
    for parts in _tabular(res.stdout):
        if len(parts) >= 2 and parts[0] == queue:
            return _to_int(parts[1])
    return -1


def nonempty_queues(transport: Transport, *, vhost: str) -> list[tuple[str, int]]:
    """``(name, depth)`` for every queue in ``vhost`` with a non-zero depth."""
    res = _list(transport, "list_queues", "name", "messages", "-p", vhost)
    out: list[tuple[str, int]] = []
    for parts in _tabular(res.stdout):
        if len(parts) < 2:
            continue
        depth = _to_int(parts[1])
        if depth > 0:
            out.append((parts[0], depth))
    return out


@dataclass
class PurgeResult:
    """Outcome of purging one queue: ``purged`` is the depth measured just before."""

    queue: str
    vhost: str
    purged: int
    output: str

    def __str__(self) -> str:
        return f"{self.vhost}/{self.queue}: {self.purged} purged ({self.output})"


def purge_queue(transport: Transport, queue: str, *, vhost: str | None = None, yes: bool = False) -> PurgeResult:
    """Purge all pending messages from ``queue`` (irreversible). Gated by ``yes``.

    Measures depth first so the result reports how many messages were dropped, then
    runs ``rabbitmqctl purge_queue``.
    """
    if not yes:
        raise PermissionError(f"refusing to purge {vhost or '/'}/{queue} without confirmation (pass yes=True)")
    before = queue_depth(transport, queue, vhost=vhost)
    args = ["purge_queue", queue]
    if vhost:
        args += ["-p", vhost]
    res = _ctl(transport, *args)
    return PurgeResult(queue, vhost or "/", max(before, 0), (res.stdout or res.stderr).strip())


@dataclass
class WorkflowPurgeReport:
    """Result of :func:`purge_workflows`: the service actions taken plus the purges done."""

    steps: list[service.ServiceActionResult] = field(default_factory=list)
    purges: list[PurgeResult] = field(default_factory=list)

    @property
    def total_purged(self) -> int:
        """Total messages purged across every queue in this report."""
        return sum(p.purged for p in self.purges)

    @property
    def ok(self) -> bool:
        """True if every service action succeeded."""
        return all(s.ok for s in self.steps)


def purge_workflows(
    transport: Transport,
    *,
    yes: bool = False,
    graceful: bool = False,
    sweep_data_queues: bool = True,
) -> WorkflowPurgeReport:
    """Release a stuck-worker backlog: purge queued workflows and recycle ``celeryd``.

    A ``celeryd`` restart alone does NOT clear the backlog — queued tasks sit in the
    ``celery`` queue (vhost ``fsr-cluster``) and re-dispatch the moment workers
    return. So the workflow queue must be **purged**, not just the pool bounced.

    Two strategies for recycling the pool:

    * **hard (default)** — purge first, then ``systemctl kill -s SIGKILL celeryd``.
      ``celeryd`` has ``Restart=always``, so systemd respawns a clean pool in ~1s
      against the now-empty queue. This is the fast, live-proven path; a graceful
      ``csadm`` stop blocks for *minutes* on in-flight workers.
    * **graceful** (``graceful=True``) — ``csadm services --stop-service celeryd``
      → purge → ``--start-service``. Slower but lets in-flight tasks finish.

    Either way ``cyops-integrations-agent`` is restarted last (clears ballooned uwsgi
    RSS). With ``sweep_data_queues`` (default) any non-empty queues in
    ``intra-cyops`` are purged too.

    Irreversible (drops queued tasks) — gated by ``yes``.
    """
    if not yes:
        raise PermissionError("purge_workflows discards queued tasks; pass yes=True to confirm")
    report = WorkflowPurgeReport()

    if graceful:
        # Quiesce celeryd gracefully so it can't re-drain mid-purge, then purge, then start.
        report.steps.append(service.stop(transport, "celeryd", yes=True))
        _purge_workflow_queues(transport, report, sweep_data_queues)
        report.steps.append(service.start(transport, "celeryd"))
    else:
        # Purge FIRST so the auto-respawned pool comes back to an empty queue, THEN
        # SIGKILL — systemd's Restart=always brings a clean pool back in ~1s.
        _purge_workflow_queues(transport, report, sweep_data_queues)
        report.steps.append(service.systemctl(transport, "kill", "celeryd", signal="SIGKILL", yes=True))

    report.steps.append(service.restart(transport, "cyops-integrations-agent", yes=True))
    return report


def _purge_workflow_queues(transport: Transport, report: WorkflowPurgeReport, sweep_data_queues: bool) -> None:
    """Purge the primary workflow queue, then optionally sweep non-empty data queues."""
    report.purges.append(purge_queue(transport, _WORKFLOW_QUEUE, vhost=_WORKFLOW_VHOST, yes=True))
    if sweep_data_queues:
        for name, _depth in nonempty_queues(transport, vhost=_DATA_VHOST):
            report.purges.append(purge_queue(transport, name, vhost=_DATA_VHOST, yes=True))


def _to_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return -1
