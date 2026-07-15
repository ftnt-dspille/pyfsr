"""Replay transport + ``demo_box()`` for doctests and tests.

:class:`ReplayTransport` is a :class:`pyfsr.cli.appliance.transport.Transport`
that answers ``run()`` by matching the argv against the verified-live captures
in :mod:`pyfsr._testing.appliance_captures` — no SSH, no ``psql``, no network.
It is the doctest/test analogue of the real ``LocalTransport``/``SSHTransport``:
same command shapes in, same real stdout out.

The happy-path fixtures are the defaults (a healthy box: services Running, ES
green, queues draining). The optional ``service_wedged`` / ``queues_backlog``
knobs reproduce failure modes the test suite needs to exercise; ``demo_box()``
does not touch them, so doctests see only the healthy shapes.

See :mod:`pyfsr._testing.appliance_captures` for provenance and the refresh
workflow.
"""

from __future__ import annotations

from ..cli.appliance.facts import Facts
from ..cli.appliance.transport import CommandResult, Transport

# Captures live in a sibling module so this one stays focused on replay logic.
from . import appliance_captures as cap

# Re-export for callers that import everything from one place.
__all__ = ["ReplayTransport", "demo_box"]


class ReplayTransport(Transport):
    """A :class:`Transport` that replays verified-live captures by argv match.

    Parameters mirror the failure-mode knobs the unit test suite needs;
    ``demo_box()`` (the doctest entry point) uses the all-healthy defaults.
    """

    target = "demo"

    def __init__(
        self,
        *,
        tables=None,
        databases=None,
        service_wedged: bool = False,
        queues_backlog: bool = False,
        file_uuid: str | None = cap.DEVICE_UUID,
        csadm_uuid: str | None = cap.DEVICE_UUID,
        es_health_payload: str | None = None,
        live_modules=None,
    ):
        self.commands: list[tuple[list[str], dict | None, bool]] = []
        self._tables = tables or ["widgets", "widgets_alerts", "widgets_team", "gadgets"]
        # Base tableNames carried by model_metadatas/staging (the "live" module set the
        # orphan sweep diffs against). Default keeps the stock tables orphan-free.
        self._live_modules = live_modules if live_modules is not None else ["widgets", "gadgets"]
        self._databases = databases or {"venom": "7 GB", "das": "200 MB", "postgres": "8 MB"}
        self._service_wedged = service_wedged
        self._queues_backlog = queues_backlog
        self._file_uuid = file_uuid
        self._csadm_uuid = csadm_uuid
        self._es_health_payload = es_health_payload

    # The dispatch table — one entry per command family the appliance verbs
    # issue. Each returns the matching capture. Order matters: probes first.
    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        self.commands.append((argv, env, sudo))
        # `test -f <path>` existence probe (logs.tail): a path containing
        # "missing" reports absent (returncode 1), everything else present.
        if argv[:2] == ["test", "-f"]:
            return CommandResult(argv, 1 if "missing" in argv[-1] else 0, "", "")
        # The install-time UUID file may be absent/unreadable (drift simulation).
        if argv[:2] == ["cat", "/home/csadmin/device_uuid"] and self._file_uuid is None:
            return CommandResult(argv, 1, "", "cat: no such file")
        # csadm may fail to return a UUID.
        if argv[:3] == ["csadm", "license", "--get-device-uuid"] and self._csadm_uuid is None:
            return CommandResult(argv, 1, "", "csadm: error")
        out = self._dispatch(argv)
        return CommandResult(argv, 0, out, "")

    def _dispatch(self, argv: list[str]) -> str:
        # --- identity / version ---
        if argv[:2] == ["cat", "/home/csadmin/device_uuid"]:
            return f"{self._file_uuid}\n"
        if argv[:3] == ["csadm", "license", "--get-device-uuid"]:
            return f"Device UUID: {self._csadm_uuid}\n"
        if argv[:3] == ["csadm", "license", "--show-details"]:
            return cap.LICENSE_DETAILS_RAW
        if argv[:1] == ["rpm"]:
            return cap.FSR_VERSION

        # --- csadm log / certs / ha ---
        if argv[:3] == ["csadm", "log", "--collect"]:
            return cap.LOG_BUNDLE_RAW
        if argv[:3] == ["csadm", "certs", "--generate"]:
            return f"Certificate generated for {argv[3]}\n"
        if argv[:3] == ["csadm", "ha", "list-nodes"]:
            return cap.HA_LIST_NODES_RAW
        if argv[:3] == ["csadm", "ha", "show-health"]:
            return cap.HA_SHOW_HEALTH_RAW
        if argv[:3] == ["csadm", "ha", "get-replication-stat"]:
            return cap.HA_REPLICATION_RAW

        # --- csadm db / services ---
        if argv[:3] == ["csadm", "db", "--getsize"]:
            return cap.DB_GETSIZE_RAW
        if argv[:3] == ["csadm", "services", "--status"]:
            return cap.SERVICES_STATUS_RAW
        if argv[:3] in (
            ["csadm", "services", "--restart"],
            ["csadm", "services", "--stop"],
            ["csadm", "services", "--start"],
        ):
            return f"{argv[2].lstrip('-')} all services\n"
        if argv[1] == "services" and argv[2] in ("--restart-service", "--stop-service", "--start-service"):
            name = argv[3]
            if name not in ("cyops-auth", "cyops-api", "nginx"):
                return cap.SERVICES_REJECT_UNKNOWN_RAW
            return f"service {name} {argv[2].rsplit('-', 1)[0].lstrip('-')}ed\n"

        # --- diagnose ---
        if argv[:1] == ["bash"] and len(argv) == 2 and argv[1].endswith(".sh"):
            return f"[diagnose] ran {argv[1]}\n"

        # --- network probes / listeners ---
        if argv[0] == "curl":
            return self._curl_response(argv)
        if argv[0] == "ss":
            return cap.SS_RAW
        # --- host metrics (free / loadavg / ps / df / snapshot) ---
        if argv[:2] == ["free", "-m"]:
            return cap.FREE_RAW
        if argv[:2] == ["cat", "/proc/loadavg"]:
            return cap.LOADAVG_RAW
        if argv[:3] == ["ps", "-e", "-o"] and "args=" in " ".join(argv):
            return cap.PS_CELERY_RAW
        if argv[0] == "df":
            return cap.DF_RAW
        if argv[:1] == ["sh"] and "-c" in argv:
            # snapshot() emits @@FREE / @@LOAD / @@PS sections, plus @@DF when a
            # disk_path is given (the facade default is /opt/cyops).
            script = argv[argv.index("-c") + 1]
            sections = [
                "@@FREE",
                cap.FREE_RAW,
                "@@LOAD",
                cap.LOADAVG_RAW.strip(),
                "@@PS",
                cap.PS_CELERY_RAW.strip(),
            ]
            if "@@DF" in script:
                sections += ["@@DF", cap.DF_RAW.strip()]
            return "\n".join(sections)

        # --- rabbitmq ---
        if argv[0] == "rabbitmqctl":
            return self._rabbitmqctl_response(argv)
        if argv[0] == "journalctl":
            return cap.JOURNALCTL_NO_ENTRIES_RAW if "--since" in argv else ""
        if argv[0] == "tail":
            return self._tail_response(argv)

        # --- psql ---
        if argv[0] != "psql":
            return ""
        sql = argv[-1].lower()
        if "from pg_database" in sql and "datistemplate" in sql and "pg_size_pretty" in sql:
            rows = [f"{n}{cap._US}{s}" for n, s in self._databases.items()]
            return "\n".join(rows) + "\n"
        if "from pg_database" in sql:
            return "\n".join(self._databases) + "\n"
        # Orphan sweep: column discovery + the live-module SELECT against model_metadatas.
        if "information_schema.columns" in sql and "model_metadatas" in sql:
            return "tableName\n"
        if "from public.model_metadatas" in sql:
            return "\n".join(self._live_modules) + "\n"
        if "from public.staging_model_metadatas" in sql:
            # Staging mirrors published in the fake (same live set).
            return "\n".join(self._live_modules) + "\n"
        if "information_schema.tables" in sql and "model_metadatas" in sql:
            # Only the content DB (venom) has model_metadatas.
            return (
                cap.CONTENT_FINGERPRINT_HIT
                if argv[argv.index("-d") + 1] == cap.CONTENT_DB
                else cap.CONTENT_FINGERPRINT_MISS
            )
        if "from pg_tables" in sql:
            return "\n".join(self._filter_tables(sql)) + "\n"
        if "select count(*) from widgets" in sql:
            return cap.COUNT_WIDGETS_RAW
        if "from widgets" in sql:
            return cap.WIDGETS_ROWS_RAW
        if sql.startswith("select 1"):
            return "1\n"
        # Mutating statements echo a command tag like real psql -A (no -t).
        if sql.lstrip().startswith("drop table"):
            return "DROP TABLE\n"
        return ""

    # --- response builders (kept as methods so tests can override one knob) ---
    def _curl_response(self, argv) -> str:
        if self._service_wedged:
            return "0"  # No response — the wedge signal
        # The ES verbs curl the local ES REST API (localhost:9200) and want the
        # JSON body; the liveness probe curls nginx with `-w %{http_code}` and
        # wants just the status code. Distinguish by URL.
        if any("localhost:9200" in a for a in argv):
            if self._es_health_payload is not None:
                return self._es_health_payload
            # The allocation/explain endpoint has no unassigned shards to report.
            if any(a.endswith("/_cluster/allocation/explain") for a in argv):
                return cap.ES_NO_UNASSIGNED_RAW
            return cap.ES_HEALTH_RAW
        return cap.CURL_200

    def _rabbitmqctl_response(self, argv) -> str:
        verb = next((a for a in argv if a.startswith("list_") or a == "status"), "")
        no_headers = "--no-table-headers" in argv

        if verb == "status":
            return cap.RMQ_STATUS_RAW

        def _emit(header: str, body: list[str]) -> str:
            lines = ([] if no_headers else [header]) + body
            return "\n".join(lines) + "\n" if lines else "\n"

        if verb == "list_queues" and "consumers" in argv:
            body = (
                ["task_queue\t2500\t0", "default_queue\t50\t2"]
                if self._queues_backlog
                else ["task_queue\t100\t1", "default_queue\t50\t2"]
            )
            return _emit("name\tmessages\tconsumers", body)
        if verb == "list_consumers":
            return _emit("queue_name\tchannel_pid", cap.RMQ_LIST_CONSUMERS_RAW.strip().splitlines()[1:])
        if verb == "list_vhosts":
            return (
                cap.RMQ_LIST_VHOSTS_RAW
                if not no_headers
                else "\n".join(cap.RMQ_LIST_VHOSTS_RAW.strip().splitlines()[1:]) + "\n"
            )
        if verb == "list_permissions":
            i = argv.index("-p") if "-p" in argv else -1
            vhost = argv[i + 1] if i >= 0 else "/"
            body = {
                "cyops-admin": ["admin\t.*\t.*\t.*"],
                "intra-cyops": ["cyops\t.*\t.*\t.*"],
            }.get(vhost, [])
            return _emit("user\tconfigure\twrite\tread", body)
        return ""

    def _tail_response(self, argv) -> str:
        if "/var/log/cyops/cyops-auth/das.log" in argv:
            return cap.TAIL_DAS_LOG_RAW
        if "/var/log/nginx/error.log" in argv:
            return cap.TAIL_NGINX_ERROR_RAW
        return "log tail data\n"

    def _filter_tables(self, sql: str):
        """Emulate the WHERE clause of find_module_tables/tables: exact match on
        ``tablename='X'`` plus prefix on ``tablename like 'X\\_%'``."""
        import re

        exact = re.search(r"tablename='([^']+)'", sql)
        like = re.search(r"tablename like '([^'\\]+)", sql)
        if not exact and not like:
            return self._tables
        out = []
        for t in self._tables:
            if exact and t == exact.group(1):
                out.append(t)
            elif like and t.startswith(like.group(1) + "_"):
                out.append(t)
        return out


def demo_box():
    """Return an :class:`pyfsr.appliance.Appliance` wired to a healthy replay box.

    The doctest entry point: guides and docstrings call ``box = demo_box()`` and
    get real return shapes with zero network. The box is healthy by default
    (services up, ES green, queues draining) — exactly the shape a reader wants
    to see in an example.
    """
    from ..appliance import Appliance

    return Appliance(_facts=Facts(ReplayTransport()))
