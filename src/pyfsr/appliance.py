"""Ergonomic Python facade for the ``pyfsr appliance`` operations.

The appliance verbs (DB queries, service control, queue management, log tails,
Elasticsearch/HA health, …) are implemented as plain functions under
:mod:`pyfsr.cli.appliance`, grouped per module — fine for the CLI, awkward to
call by hand.

:class:`Appliance` wraps a single connection and exposes those verbs as grouped
methods (``appliance.db.query(...)``, ``appliance.service.status()``,
``appliance.mq.queues()``) so the Python API mirrors the CLI:

>>> from pyfsr import Appliance
>>> box = Appliance(host="10.0.0.1", user="csadmin", key_path="~/.ssh/id_rsa")
>>> box.info()                                  # doctest: +SKIP
{'target': 'csadmin@10.0.0.1', 'fsr_version': '7.6.0', 'content_db': 'venom',
 'device_uuid': '0123…cdef', 'db_user': 'cyberpgsql'}
>>> dbname, headers, rows = box.db.query("SELECT count(*) FROM alerts")   # doctest: +SKIP
>>> (dbname, headers, rows)                      # doctest: +SKIP
('venom', ['count'], [['42']])

Or reuse a client's host (SSH still needs its own credentials):

>>> from pyfsr import FortiSOAR                  # doctest: +SKIP
>>> client = FortiSOAR("https://10.0.0.1", token="...")             # doctest: +SKIP
>>> box = client.appliance(key_path="~/.ssh/id_rsa")               # doctest: +SKIP

The same ``--yes`` / ``--write`` gating as the CLI applies: mutating methods
take ``yes=True`` and SQL writes go through ``box.db.execute(..., yes=True)``.
For any command not surfaced as a named method, :meth:`Appliance.run` is the
escape hatch.
"""

from __future__ import annotations

from .cli.appliance import certs, db, host, info, logs, mq, service
from .cli.appliance import es as es_mod
from .cli.appliance import ha as ha_mod
from .cli.appliance import license as license_mod
from .cli.appliance.db import DatabaseInfo, DataClassSize, OrphanTable
from .cli.appliance.es import ESHealth
from .cli.appliance.facts import Facts
from .cli.appliance.ha import HaHealth, HaNode
from .cli.appliance.host import DiskUsage, HostSnapshot, LoadAvg, MemInfo, ProcRss
from .cli.appliance.license import DriftReport, LicenseDetails
from .cli.appliance.mq import Consumer, Permission, PurgeResult, QueueInfo, WorkflowPurgeReport
from .cli.appliance.service import Listener, ProbeResult, ServiceActionResult
from .cli.appliance.transport import Transport, make_transport

__all__ = [
    "Appliance",
    "DbNamespace",
    "ServiceNamespace",
    "MqNamespace",
    "HostNamespace",
    "LicenseNamespace",
    "LogsNamespace",
    "EsNamespace",
    "HaNamespace",
    "CertsNamespace",
]


class DbNamespace:
    """Postgres verbs (``appliance.db``). Reads are open; writes need ``yes=True``."""

    def __init__(self, facts: Facts) -> None:
        self._facts = facts

    def query(
        self, sql: str, *, role: str | None = None, db_name: str | None = None
    ) -> tuple[str, list[str], list[list[str]]]:
        """Run a **read-only** SELECT. Returns ``(dbname, headers, rows)``."""
        return db.query(self._facts, sql, role=role, db=db_name)

    def execute(
        self, sql: str, *, role: str | None = None, db_name: str | None = None, yes: bool = False
    ) -> tuple[str, str]:
        """Run a mutating statement (``UPDATE``/``DELETE``/``DROP``…). Refuses unless ``yes=True``."""
        return db.exec_write(self._facts, sql, role=role, db=db_name, yes=yes)

    def tables(
        self, pattern: str | None = None, *, role: str | None = None, db_name: str | None = None
    ) -> tuple[str, list[str], list[list[str]]]:
        """List tables, optionally filtered by a ``LIKE``/glob pattern. Returns ``(dbname, headers, rows)``."""
        return db.tables(self._facts, pattern, role=role, db=db_name)

    def indexes(
        self, pattern: str | None = None, *, role: str | None = None, db_name: str | None = None
    ) -> tuple[str, list[str], list[list[str]]]:
        """List indexes, optionally filtered by a ``LIKE``/glob pattern. Returns ``(dbname, headers, rows)``."""
        return db.indexes(self._facts, pattern, role=role, db=db_name)

    def sizes(self, *, timeout: float = 60.0) -> list[DataClassSize]:
        """``csadm db --getsize`` — footprint by data class."""
        return db.getsize(self._facts, timeout=timeout)

    def databases(self) -> list[DatabaseInfo]:
        """Enumerate databases with sizes and roles."""
        return db.list_databases(self._facts)

    def find_module_tables(self, base_table: str) -> list[str]:
        """Find the physical tables belonging to a module's ``base_table``."""
        return db.find_module_tables(self._facts, base_table)

    def drop_module_tables(self, base_table: str, *, yes: bool = False) -> dict:
        """Drop orphaned module tables (``DROP ... CASCADE``). Refuses unless ``yes=True``."""
        return db.drop_module_tables(self._facts, base_table, yes=yes)

    def find_orphan_module_tables(self) -> list[OrphanTable]:
        """Sweep the content DB for physical tables left behind by deleted modules.

        Non-destructive. Reclaim a reported base with :meth:`drop_module_tables`.
        """
        return db.find_orphan_module_tables(self._facts)

    def resolve_db(self, *, role: str | None = None, db_name: str | None = None) -> str:
        """Resolve the target DB name from an explicit name or a logical role.

        ``db_name`` wins outright. A fixed role (``das``, ``gateway``, …) maps
        directly. The default ``content`` role is discovered by fingerprinting
        for ``model_metadatas``.
        """
        return self._facts.resolve_db(role=role, db=db_name)


class ServiceNamespace:
    """systemd / cyops service verbs (``appliance.service``)."""

    def __init__(self, t: Transport) -> None:
        self._t = t

    def status(self, name: str | None = None) -> str:
        """Parsed ``csadm services --status`` (optionally one service)."""
        return service.status(self._t, name)

    def services(self, name: str | None = None) -> list[service.ServiceState]:
        """Typed per-service states (name, running, status, since)."""
        return service.services(self._t, name)

    def liveness(self, *, base: str = "https://127.0.0.1", timeout: float = 6.0) -> list[ProbeResult]:
        """Probe endpoints for active-but-wedged services."""
        return service.liveness(self._t, base=base, timeout=timeout)

    def restart(self, name: str, *, yes: bool = False) -> ServiceActionResult:
        """Restart one cyops service. Refuses unless ``yes=True``."""
        return service.restart(self._t, name, yes=yes)

    def start(self, name: str) -> ServiceActionResult:
        """Start one cyops service."""
        return service.start(self._t, name)

    def stop(self, name: str, *, yes: bool = False) -> ServiceActionResult:
        """Stop one cyops service. Refuses unless ``yes=True``."""
        return service.stop(self._t, name, yes=yes)

    def restart_all(self, *, yes: bool = False) -> ServiceActionResult:
        """Restart the whole service stack in order. Refuses unless ``yes=True``."""
        return service.restart_all(self._t, yes=yes)

    def start_all(self) -> ServiceActionResult:
        """Start the whole service stack in order."""
        return service.start_all(self._t)

    def stop_all(self, *, yes: bool = False) -> ServiceActionResult:
        """Stop the whole service stack in order. Refuses unless ``yes=True``."""
        return service.stop_all(self._t, yes=yes)

    def listeners(self) -> list[Listener]:
        """Listening TCP ports + owning process (``ss -tlnp``)."""
        return service.listeners(self._t)


class MqNamespace:
    """RabbitMQ verbs (``appliance.mq``)."""

    def __init__(self, t: Transport) -> None:
        self._t = t

    def status(self) -> str:
        """``rabbitmqctl status``."""
        return mq.status(self._t)

    def queues(self) -> list[QueueInfo]:
        """Queues with depth + consumer counts."""
        return mq.queues(self._t)

    def consumers(self) -> list[Consumer]:
        """List consumers (queue ↔ channel)."""
        return mq.consumers(self._t)

    def vhosts(self) -> list[str]:
        """List virtual hosts."""
        return mq.vhosts(self._t)

    def permissions(self, *, all_vhosts: bool = False) -> list[Permission]:
        """Per-vhost permissions (default vhost ``/``, or all)."""
        return mq.permissions(self._t, all_vhosts=all_vhosts)

    def purge_queue(self, queue: str, *, vhost: str | None = None, yes: bool = False) -> PurgeResult:
        """Purge all messages from a queue (irreversible). Refuses unless ``yes=True``."""
        return mq.purge_queue(self._t, queue, vhost=vhost, yes=yes)

    def purge_workflows(
        self, *, graceful: bool = False, sweep_data_queues: bool = True, yes: bool = False
    ) -> WorkflowPurgeReport:
        """Clear the stuck-worker backlog: purge queues + recycle celeryd. Refuses unless ``yes=True``."""
        return mq.purge_workflows(self._t, graceful=graceful, sweep_data_queues=sweep_data_queues, yes=yes)


class HostNamespace:
    """OS resource metrics (``appliance.host``). All read-only, no sudo."""

    def __init__(self, t: Transport) -> None:
        self._t = t

    def meminfo(self) -> MemInfo:
        """Memory + swap usage (MB)."""
        return host.meminfo(self._t)

    def loadavg(self) -> LoadAvg:
        """System load averages."""
        return host.loadavg(self._t)

    def process_rss(self, pattern: str) -> ProcRss:
        """Summed/peak RSS for processes matching ``pattern``."""
        return host.process_rss(self._t, pattern)

    def disk(self, path: str = "/opt/cyops") -> DiskUsage:
        """Disk usage for ``path``."""
        return host.disk(self._t, path)

    def snapshot(self, *, disk_path: str = "/opt/cyops") -> HostSnapshot:
        """One coherent sample: mem, swap, load, worker RSS, disk."""
        return host.snapshot(self._t, disk_path=disk_path)


class LicenseNamespace:
    """Licensing / identity (``appliance.license``)."""

    def __init__(self, t: Transport) -> None:
        self._t = t

    def show(self) -> str:
        """Raw ``csadm license --show-details``."""
        return license_mod.show(self._t)

    def details(self) -> LicenseDetails:
        """Parsed license details."""
        return license_mod.details(self._t)

    def device_uuid(self) -> str:
        """Resolved device UUID (file first, csadm fallback)."""
        return license_mod.device_uuid(self._t)

    def drift(self) -> DriftReport:
        """File vs csadm entitlement-UUID drift report."""
        return license_mod.drift(self._t)


class LogsNamespace:
    """Log tail / error scan (``appliance.logs``)."""

    def __init__(self, t: Transport) -> None:
        self._t = t

    def tail(self, service_name: str, *, lines: int = 100) -> str:
        """Tail a cyops service log."""
        return logs.tail(self._t, service_name, lines=lines)

    def scan(self, *, minutes: int = 30) -> str:
        """Roll up recent journal errors."""
        return logs.scan(self._t, minutes=minutes)

    def bundle(self, *, timeout: float = 300.0) -> str:
        """``csadm log --collect`` → tarball path (slow)."""
        return logs.bundle(self._t, timeout=timeout)


class EsNamespace:
    """Elasticsearch health / shards (``appliance.es``)."""

    def __init__(self, facts: Facts) -> None:
        self._facts = facts

    def health(self) -> ESHealth:
        """Cluster health (green/yellow/red + shard counts)."""
        return es_mod.health(self._facts)

    def shards(self) -> tuple[list[str], list[list[str]]]:
        """Unassigned-shard allocation explain."""
        return es_mod.shards(self._facts)


class HaNamespace:
    """HA cluster verbs (``appliance.ha``)."""

    def __init__(self, t: Transport) -> None:
        self._t = t

    def nodes(self) -> list[HaNode]:
        """HA nodes (current node marked)."""
        return ha_mod.nodes(self._t)

    def health(self) -> HaHealth:
        """HA cluster health."""
        return ha_mod.health(self._t)

    def replication(self) -> str:
        """``csadm ha get-replication-stat``."""
        return ha_mod.replication(self._t)


class CertsNamespace:
    """Appliance TLS certificate verbs (``appliance.certs``)."""

    def __init__(self, t: Transport) -> None:
        self._t = t

    def regenerate(self, hostname: str, *, yes: bool = False, timeout: float = 120.0) -> str:
        """Regenerate the self-signed cert (restart services afterward). Refuses unless ``yes=True``."""
        return certs.regenerate(self._t, hostname, yes=yes, timeout=timeout)


class Appliance:
    """A connection to a FortiSOAR appliance, exposing the ``pyfsr appliance`` verbs.

    Construct it with SSH connection details (or run it on-box with no ``host`` to
    use a local transport), then reach the grouped verbs:

    - :attr:`db` — Postgres queries, table cleanup
    - :attr:`service` — start/stop/restart, liveness
    - :attr:`mq` — RabbitMQ queues, purges
    - :attr:`host` — memory/load/disk/RSS
    - :attr:`license` — device UUID, drift
    - :attr:`logs` — tail, scan, bundle
    - :attr:`es` — Elasticsearch health/shards
    - :attr:`ha` — cluster nodes/health/replication
    - :attr:`certs` — TLS cert regeneration

    plus :meth:`info`, :meth:`diagnose`, and :meth:`run` (the escape hatch for
    arbitrary shell commands).

    Connection args fall back to ``PYFSR_APPLIANCE_HOST`` / ``_USER`` / ``_PASSWORD``
    when omitted. Pass ``instance="<alias>"`` to resolve a named SSH profile from
    ``~/.pyfsr/instances.toml`` (the same file
    :class:`~pyfsr.instances.InstanceRegistry` uses for the REST client) — this
    takes precedence over the explicit host/user/password kwargs and is the SDK
    counterpart of ``pyfsr appliance --instance <alias>``.

    .. note::

       Each grouped verb returns a typed result defined in the corresponding
       ``pyfsr.cli.appliance`` submodule rather than here, so those dataclasses
       are documented on their own pages, not under this module's contents:

       - :mod:`pyfsr.cli.appliance.db` — :class:`~pyfsr.cli.appliance.db.DatabaseInfo`,
         :class:`~pyfsr.cli.appliance.db.DataClassSize`, :class:`~pyfsr.cli.appliance.db.OrphanTable`
       - :mod:`pyfsr.cli.appliance.service` — :class:`~pyfsr.cli.appliance.service.ServiceState`,
         :class:`~pyfsr.cli.appliance.service.Listener`, :class:`~pyfsr.cli.appliance.service.ProbeResult`,
         :class:`~pyfsr.cli.appliance.service.ServiceActionResult`
       - :mod:`pyfsr.cli.appliance.mq` — :class:`~pyfsr.cli.appliance.mq.QueueInfo`,
         :class:`~pyfsr.cli.appliance.mq.Consumer`, :class:`~pyfsr.cli.appliance.mq.Permission`,
         :class:`~pyfsr.cli.appliance.mq.PurgeResult`, :class:`~pyfsr.cli.appliance.mq.WorkflowPurgeReport`
       - :mod:`pyfsr.cli.appliance.host` — :class:`~pyfsr.cli.appliance.host.MemInfo`,
         :class:`~pyfsr.cli.appliance.host.LoadAvg`, :class:`~pyfsr.cli.appliance.host.ProcRss`,
         :class:`~pyfsr.cli.appliance.host.DiskUsage`, :class:`~pyfsr.cli.appliance.host.HostSnapshot`
       - :mod:`pyfsr.cli.appliance.license` — :class:`~pyfsr.cli.appliance.license.LicenseDetails`,
         :class:`~pyfsr.cli.appliance.license.DriftReport`
       - :mod:`pyfsr.cli.appliance.es` — :class:`~pyfsr.cli.appliance.es.ESHealth`
       - :mod:`pyfsr.cli.appliance.ha` — :class:`~pyfsr.cli.appliance.ha.HaNode`,
         :class:`~pyfsr.cli.appliance.ha.HaHealth`
    """

    db: DbNamespace
    """Postgres verbs — queries, table listings, orphan-table cleanup. See :class:`DbNamespace`."""

    service: ServiceNamespace
    """systemd / cyops service verbs — status, liveness, start/stop/restart. See :class:`ServiceNamespace`."""

    mq: MqNamespace
    """RabbitMQ verbs — queues, consumers, permissions, purges. See :class:`MqNamespace`."""

    host: HostNamespace
    """OS resource metrics — memory, load, disk, process RSS. See :class:`HostNamespace`."""

    license: LicenseNamespace
    """Licensing / identity — device UUID, license details, entitlement drift. See :class:`LicenseNamespace`."""

    logs: LogsNamespace
    """Log verbs — tail a service log, scan recent errors, collect a bundle. See :class:`LogsNamespace`."""

    es: EsNamespace
    """Elasticsearch verbs — cluster health, unassigned-shard explain. See :class:`EsNamespace`."""

    ha: HaNamespace
    """HA cluster verbs — nodes, health, replication status. See :class:`HaNamespace`."""

    certs: CertsNamespace
    """Appliance TLS certificate verbs — regenerate the self-signed cert. See :class:`CertsNamespace`."""

    def __init__(
        self,
        host: str | None = None,
        *,
        user: str = "csadmin",
        password: str | None = None,
        port: int = 22,
        key_path: str | None = None,
        sudo_password: str | None = None,
        insecure_skip_host_key_check: bool = False,
        instance: str | None = None,
        _facts: Facts | None = None,
    ) -> None:
        if _facts is not None:
            self._facts = _facts
        elif instance is not None:
            # Named SSH profile from ~/.pyfsr/instances.toml — the SDK counterpart
            # of `pyfsr appliance --instance <alias>`. Takes precedence over the
            # explicit host/user/password kwargs. Requires an
            # [instances.<alias>.appliance] subtable.
            from .instances import InstanceRegistry

            transport = InstanceRegistry.load().transport(instance)
            self._facts = Facts(transport)
        else:
            transport = make_transport(
                host=host,
                user=user,
                password=password,
                port=port,
                key_path=key_path,
                sudo_password=sudo_password,
                insecure_skip_host_key_check=insecure_skip_host_key_check,
            )
            self._facts = Facts(transport)

        t = self._facts.transport
        self.db = DbNamespace(self._facts)
        self.service = ServiceNamespace(t)
        self.mq = MqNamespace(t)
        self.host = HostNamespace(t)
        self.license = LicenseNamespace(t)
        self.logs = LogsNamespace(t)
        self.es = EsNamespace(self._facts)
        self.ha = HaNamespace(t)
        self.certs = CertsNamespace(t)

    def info(self) -> dict[str, str]:
        """Identity card: host, FortiSOAR version, content DB, device UUID."""
        return info.identity(self._facts)

    def diagnose(self, *, path: str | None = None, timeout: float = 120.0) -> str:
        """Run ``fsr_diagnose.sh`` on the appliance; returns its output."""
        from .cli.appliance import diagnose as diagnose_mod

        if path is None:
            return diagnose_mod.run(self._facts.transport, timeout=timeout)
        return diagnose_mod.run(self._facts.transport, path=path, timeout=timeout)

    def run(
        self,
        argv: list[str],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        sudo: bool = False,
    ) -> str:
        """Run an arbitrary command on the appliance; returns ``stdout``.

        Raises ``RuntimeError`` on non-zero exit.  Use this as the escape hatch
        for commands not covered by a namespace method.

        >>> box = Appliance(host="10.0.0.1", key_path="~/.ssh/id_rsa")  # doctest: +SKIP
        >>> box.run(["uname", "-r"])                                      # doctest: +SKIP
        '5.15.0-101-generic\\n'
        """
        res = self._facts.transport.run(argv, input_text=input_text, env=env, timeout=timeout, sudo=sudo)
        if not res.ok:
            raise RuntimeError(f"command failed ({res.returncode}): {' '.join(argv)}\n{res.stderr.strip()}")
        return res.stdout
