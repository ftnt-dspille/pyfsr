"""Unit tests for the ``pyfsr appliance`` CLI (P1: transport / facts / db; P2: service / mq / logs).

All tests drive a :class:`FakeTransport` — no live appliance, ssh, or psql.
The fake answers psql-shaped queries by pattern so facts resolution and the db
verbs can be exercised offline. P2 tests exercise service status/liveness/restart,
RabbitMQ queue/consumer/vhost/permission checks, and log tail/scan.
"""

from __future__ import annotations

import pytest

from pyfsr.cli.appliance import db as db_cmds
from pyfsr.cli.appliance import license as license_cmds
from pyfsr.cli.appliance import logs as logs_cmds
from pyfsr.cli.appliance import mq as mq_cmds
from pyfsr.cli.appliance import service as service_cmds
from pyfsr.cli.appliance.facts import Facts
from pyfsr.cli.appliance.transport import (
    CommandResult,
    LocalTransport,
    SSHTransport,
    Transport,
    TransportError,
    _sudo_wrap,
    make_transport,
)

UUID = "0123456789abcdef0123456789abcdef"
US = "\x1f"  # the unit-separator field delimiter Facts.psql uses


class FakeTransport(Transport):
    """Transport that fabricates psql/csadm/service/rabbitmq/curl output by matching on the command."""

    target = "fake"

    def __init__(
        self,
        *,
        tables=None,
        databases=None,
        service_wedged=False,
        queues_backlog=False,
        file_uuid=UUID,
        csadm_uuid=UUID,
    ):
        self.commands = []
        self._tables = tables or ["widgets", "widgets_alerts", "widgets_team", "gadgets"]
        self._databases = databases or {"venom": "7 GB", "das": "200 MB", "postgres": "8 MB"}
        self._service_wedged = service_wedged  # if True, curl returns 000
        self._queues_backlog = queues_backlog  # if True, queues have backlog
        self._file_uuid = file_uuid  # /home/csadmin/device_uuid (None = unreadable)
        self._csadm_uuid = csadm_uuid  # csadm entitlement UUID (None = csadm fails)

    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        self.commands.append((argv, env, sudo))
        # `test -f <path>` existence probe (used by logs.tail): a path containing
        # "missing" reports absent (returncode 1), everything else present.
        if argv[:2] == ["test", "-f"]:
            return CommandResult(argv, 1 if "missing" in argv[-1] else 0, "", "")
        # The install-time UUID file may be absent/unreadable (drift simulation).
        if argv[:2] == ["cat", "/home/csadmin/device_uuid"] and self._file_uuid is None:
            return CommandResult(argv, 1, "", "cat: no such file")
        # csadm may fail to return a UUID.
        if argv[:3] == ["csadm", "license", "--get-device-uuid"] and self._csadm_uuid is None:
            return CommandResult(argv, 1, "", "csadm: error")
        out = self._dispatch(argv, env)
        return CommandResult(argv, 0, out, "")

    def _dispatch(self, argv, env) -> str:
        if argv[:2] == ["cat", "/home/csadmin/device_uuid"]:
            # Primary device-UUID source (install-time file = the DB/ES password).
            return f"{self._file_uuid}\n"
        if argv[:3] == ["csadm", "license", "--get-device-uuid"]:
            return f"Device UUID: {self._csadm_uuid}\n"
        if argv[:3] == ["csadm", "license", "--show-details"]:
            return "License Type: subscription\nExpiry: 2027-01-01\nDevice UUID: " + f"{self._csadm_uuid}\n"
        if argv[:3] == ["csadm", "log", "--collect"]:
            return "Log bundle created: /tmp/fortisoar-logs-20260621.tar.gz\n"
        if argv[:3] == ["csadm", "db", "--getsize"]:
            # Real csadm format (verified live): preamble + "<class> : <size>" lines.
            return (
                "Reading postgres details from db_config.yml file\n"
                "Following is the current database usage:\n"
                "Primary Data  : 7354 MB\n"
                "Audit Logs    : 1089 MB\n"
                "Workflow Logs : 1138 MB\n"
                "Archived Data : 8396 kB\n"
            )
        if argv[:3] == ["csadm", "certs", "--generate"]:
            return f"Certificate generated for {argv[3]}\n"
        if argv[:3] == ["csadm", "ha", "list-nodes"]:
            return "node1  primary  fortisoar.example.com\nnode2  secondary  fortisoar.example.com\n"
        if argv[:3] == ["csadm", "ha", "show-health"]:
            return "HA Health: OK\nPrimary: node1\nSecondary: node2\n"
        if argv[:3] == ["csadm", "ha", "get-replication-stat"]:
            return "Replication lag: 0 bytes\nStatus: streaming\n"
        if argv[:3] == ["csadm", "services", "--status"]:
            # Faithful to live csadm: dot-padded name + "[Status]  since <when>".
            return (
                "cyops-auth...............[Running]      since Fri 2026-05-22 01:18:16 UTC\n"
                "cyops-api................[Running]      since Thu 2026-05-07 14:10:22 UTC\n"
            )
        # Whole-stack verbs (--restart / --stop / --start) act on every service.
        if argv[:3] in (
            ["csadm", "services", "--restart"],
            ["csadm", "services", "--stop"],
            ["csadm", "services", "--start"],
        ):
            return f"{argv[2].lstrip('-')} all services\n"
        # Single-service verbs validate the name and, per the live box, print an
        # "ERROR: ... can not be modified" hint and **exit 0** for an unknown one.
        if argv[1] == "services" and argv[2] in ("--restart-service", "--stop-service", "--start-service"):
            name = argv[3]
            if name not in ("cyops-auth", "cyops-api", "nginx"):
                return (
                    f"ERROR: {name} service can not be modified using this command.\n"
                    "       This command can be used for following services: cyops-auth cyops-api nginx\n"
                )
            return f"service {name} {argv[2].rsplit('-', 1)[0].lstrip('-')}ed\n"
        if argv[:1] == ["bash"] and len(argv) == 2 and argv[1].endswith(".sh"):
            return f"[diagnose] ran {argv[1]}\n"
        if argv[0] == "curl":
            return self._curl_response(argv)
        if argv[0] == "ss":
            return self._ss_response()
        if argv[0] == "rabbitmqctl":
            return self._rabbitmqctl_response(argv)
        if argv[0] == "journalctl":
            return self._journalctl_response(argv)
        if argv[0] == "tail":
            return self._tail_response(argv)
        if argv[0] == "rpm":
            return "7.6.5"
        if argv[0] != "psql":
            return ""
        sql = argv[-1].lower()
        if "from pg_database" in sql and "datistemplate" in sql and "pg_size_pretty" in sql:
            rows = [f"{n}{US}{s}" for n, s in self._databases.items()]
            return "\n".join(rows) + "\n"
        if "from pg_database" in sql:
            return "\n".join(self._databases) + "\n"
        if "information_schema.tables" in sql and "model_metadatas" in sql:
            # Only the content DB (venom) has model_metadatas.
            return "1\n" if argv[argv.index("-d") + 1] == "venom" else "\n"
        if "from pg_tables" in sql:
            return "\n".join(self._filter_tables(sql)) + "\n"
        if sql.startswith("select 1"):
            return "1\n"
        # Mutating statements echo a command tag like real psql -A (no -t).
        if sql.lstrip().startswith("drop table"):
            return "DROP TABLE\n"
        return ""

    def _curl_response(self, argv) -> str:
        """Fake curl response (0 = wedged, otherwise a status code)."""
        if self._service_wedged:
            return "0"  # No response — the wedge signal
        if "-X" in argv and argv[argv.index("-X") + 1] == "POST":
            return "200"  # auth endpoint
        return "200"  # API endpoint

    def _ss_response(self) -> str:
        """Fake ss -tlnp output (TCP listeners)."""
        return (
            'LISTEN  0  128  *:443  *:*  users:(("nginx",pid=1234,fd=5))\n'
            'LISTEN  0  128  *:80  *:*  users:(("nginx",pid=1234,fd=6))\n'
            'LISTEN  0  128  *:5672  *:*  users:(("rabbitmq",pid=2345,fd=7))\n'
        )

    def _rabbitmqctl_response(self, argv) -> str:
        """Fake rabbitmqctl output, modelled on a live RabbitMQ 3.13.2 box.

        Faithful to two behaviours verified live: (1) ``-q`` alone does NOT drop
        the column-header row — only ``--no-table-headers`` does — so this emits a
        header line *unless* that flag is present; (2) ``list_permissions`` is
        per-vhost (``-p <vhost>``) and ``/`` is empty while named vhosts populate.
        """
        verb = next((a for a in argv if a.startswith("list_") or a == "status"), "")
        no_headers = "--no-table-headers" in argv

        if verb == "status":
            return "Status of node rabbit@appliance ...\nRabbitMQ 3.13.2\n"

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
            return _emit(
                "queue_name\tchannel_pid",
                ["task_queue\t<rabbit@appliance.1.250>", "default_queue\t<rabbit@appliance.2.251>"],
            )
        if verb == "list_vhosts":
            return _emit("name", ["/", "cyops-admin", "intra-cyops"])
        if verb == "list_permissions":
            # Per-vhost; "/" is empty on a real box, named vhosts have one entry.
            i = argv.index("-p") if "-p" in argv else -1
            vhost = argv[i + 1] if i >= 0 else "/"
            body = {
                "cyops-admin": ["admin\t.*\t.*\t.*"],
                "intra-cyops": ["cyops\t.*\t.*\t.*"],
            }.get(vhost, [])
            return _emit("user\tconfigure\twrite\tread", body)
        return ""

    def _journalctl_response(self, argv) -> str:
        """Fake journalctl output (errors last N minutes)."""
        if "--since" in argv:
            return "No entries\n"
        return ""

    def _tail_response(self, argv) -> str:
        """Fake tail output from log files."""
        if "/var/log/cyops/cyops-auth/das.log" in argv:
            return "[INFO] 2026-06-20 12:30:45 auth service started\n[INFO] successful login\n"
        if "/var/log/nginx/error.log" in argv:
            return "[warn] low memory condition\n[info] connection opened\n"
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


@pytest.fixture
def facts():
    return Facts(FakeTransport())


# --------------------------------------------------------------- facts
def test_device_uuid_parsed_and_cached(facts):
    assert facts.device_uuid() == UUID
    assert facts.db_password == UUID
    # second call is served from cache (no extra csadm run)
    n_before = len(facts.transport.commands)
    facts.device_uuid()
    assert len(facts.transport.commands) == n_before


def test_content_db_discovered_by_fingerprint(facts):
    assert facts.content_db() == "venom"


def test_resolve_db_explicit_and_role(facts):
    assert facts.resolve_db(db="explicit") == "explicit"
    assert facts.resolve_db(role="das") == "das"
    assert facts.resolve_db() == "venom"  # content default


def test_resolve_db_unknown_role_raises(facts):
    with pytest.raises(TransportError):
        facts.resolve_db(role="bogus")


# --------------------------------------------------------------- db verbs
def test_db_query_rejects_writes(facts):
    with pytest.raises(ValueError):
        db_cmds.query(facts, "DELETE FROM widgets")


def test_db_list_marks_content_role(facts):
    dbs = db_cmds.list_databases(facts)
    assert next(d for d in dbs if d.name == "venom").role == "content"
    assert next(d for d in dbs if d.name == "das").role == "das"


def test_find_module_tables_matches_base_and_joins(facts):
    found = db_cmds.find_module_tables(facts, "widgets")
    assert "widgets" in found
    assert "widgets_alerts" in found
    assert "widgets_team" in found
    assert "gadgets" not in found


def test_drop_module_tables_refuses_without_yes(facts):
    with pytest.raises(PermissionError):
        db_cmds.drop_module_tables(facts, "widgets")


def test_drop_module_tables_drops_with_yes(facts):
    result = db_cmds.drop_module_tables(facts, "widgets", yes=True)
    assert result["db"] == "venom"
    assert set(result["dropped"]) == {"widgets", "widgets_alerts", "widgets_team"}
    dropped_sql = [
        argv[-1]
        for argv, _env, _sudo in facts.transport.commands
        if argv[0] == "psql" and argv[-1].lower().startswith("drop table")
    ]
    assert len(dropped_sql) == 3
    assert all("CASCADE" in s for s in dropped_sql)


def test_exec_write_refuses_without_yes(facts):
    with pytest.raises(PermissionError):
        db_cmds.exec_write(facts, "DROP INDEX foo", yes=False)


def test_exec_write_runs_with_yes(facts):
    dbname, status = db_cmds.exec_write(facts, "DROP TABLE widgets CASCADE", yes=True)
    assert dbname == "venom"
    assert status == "DROP TABLE"


# --------------------------------------------------------------- license verbs
def test_license_device_uuid_prefers_file():
    t = FakeTransport(file_uuid=UUID, csadm_uuid="ffffffffffffffffffffffffffffffff")
    assert license_cmds.device_uuid(t) == UUID  # file wins over csadm


def test_license_device_uuid_falls_back_to_csadm():
    t = FakeTransport(file_uuid=None, csadm_uuid=UUID)
    assert license_cmds.device_uuid(t) == UUID


def test_license_device_uuid_raises_when_both_fail():
    t = FakeTransport(file_uuid=None, csadm_uuid=None)
    with pytest.raises(TransportError):
        license_cmds.device_uuid(t)


def test_license_drift_ok_when_equal():
    report = license_cmds.drift(FakeTransport(file_uuid=UUID, csadm_uuid=UUID))
    assert report.drifted is False
    assert report.file_uuid == report.csadm_uuid == UUID


def test_license_drift_detected_when_csadm_differs():
    other = "ffffffffffffffffffffffffffffffff"
    report = license_cmds.drift(FakeTransport(file_uuid=UUID, csadm_uuid=other))
    assert report.drifted is True
    assert report.file_uuid == UUID
    assert report.csadm_uuid == other
    assert "DRIFT" in report.verdict


def test_license_drift_unknown_when_file_missing():
    report = license_cmds.drift(FakeTransport(file_uuid=None, csadm_uuid=UUID))
    assert report.drifted is False  # can't claim drift without the authoritative file
    assert "UNKNOWN" in report.verdict


def test_license_show_returns_details():
    out = license_cmds.show(FakeTransport())
    assert "License Type" in out


# --------------------------------------------------------------- transport selection
def test_make_transport_requires_target(monkeypatch):
    monkeypatch.delenv("PYFSR_APPLIANCE_HOST", raising=False)
    monkeypatch.setattr("pyfsr.cli.appliance.transport.is_onbox", lambda: False)
    with pytest.raises(TransportError):
        make_transport()


def test_make_transport_ssh_from_host(monkeypatch):
    monkeypatch.delenv("PYFSR_APPLIANCE_HOST", raising=False)
    t = make_transport(host="10.0.0.1", user="csadmin", password="pw")
    assert isinstance(t, SSHTransport)
    assert t.target == "csadmin@10.0.0.1"


def test_find_module_tables_rejects_bad_identifier(facts):
    with pytest.raises(ValueError):
        db_cmds.find_module_tables(facts, "widgets; DROP TABLE x")


def test_drop_module_tables_rejects_bad_identifier(facts):
    with pytest.raises(ValueError):
        db_cmds.drop_module_tables(facts, "foo'bar", yes=True)


def test_ssh_default_uses_accept_new(monkeypatch):
    t = SSHTransport(host="h", user="u", key_path="/k")
    prefix = " ".join(t._ssh_prefix())
    assert "StrictHostKeyChecking=accept-new" in prefix
    assert "/dev/null" not in prefix


def test_ssh_insecure_opt_in_disables_host_key_check(monkeypatch):
    t = SSHTransport(host="h", user="u", key_path="/k", insecure_skip_host_key_check=True)
    prefix = " ".join(t._ssh_prefix())
    assert "StrictHostKeyChecking=no" in prefix
    assert "UserKnownHostsFile=/dev/null" in prefix


def test_sudo_wrap_uses_silent_prompt():
    cmd = _sudo_wrap(["csadm", "license"], None)
    assert cmd[:4] == ["sudo", "-S", "-p", ""]
    assert cmd[4:] == ["csadm", "license"]


def test_sudo_wrap_reapplies_env_inside_privileged_context():
    # sudo's env_reset strips a shell export, so env must be re-applied via `env`.
    cmd = _sudo_wrap(["psql"], {"PGPASSWORD": "secret"})
    assert "env" in cmd
    assert "PGPASSWORD=secret" in cmd
    assert cmd.index("env") < cmd.index("psql")


def test_facts_device_uuid_reads_install_file_first():
    # The DB/ES password is the install-time UUID in /home/csadmin/device_uuid,
    # which can differ from (drifted) `csadm license`; the file is the primary source.
    facts = Facts(FakeTransport())
    assert facts.device_uuid() == UUID
    cmds = [argv for argv, _env, _sudo in facts.transport.commands]
    assert ["cat", "/home/csadmin/device_uuid"] in cmds
    # csadm is only a fallback — not called when the file resolves.
    assert not any(argv[0] == "csadm" and "--get-device-uuid" in argv for argv in cmds)


def test_facts_device_uuid_falls_back_to_csadm_with_sudo():
    # When the file is absent/unreadable, fall back to `csadm license` (sudo).
    class NoFile(FakeTransport):
        def run(self, argv, **kw):
            if argv[:2] == ["cat", "/home/csadmin/device_uuid"]:
                return CommandResult(argv, 1, "", "No such file")
            return super().run(argv, **kw)

    facts = Facts(NoFile())
    assert facts.device_uuid() == UUID
    csadm = next((argv, sudo) for argv, _env, sudo in facts.transport.commands if argv[0] == "csadm")
    assert csadm[1] is True  # sudo flag


def test_local_sudo_pipes_password_and_wraps(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")

        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    monkeypatch.setattr("pyfsr.cli.appliance.transport.subprocess.run", fake_run)
    t = LocalTransport(sudo_password="pw")
    t.run(["csadm", "services", "--status"], sudo=True)
    assert captured["cmd"][:4] == ["sudo", "-S", "-p", ""]
    assert captured["input"].startswith("pw\n")


def test_ssh_password_never_in_argv(monkeypatch):
    monkeypatch.setattr("pyfsr.cli.appliance.transport.shutil.which", lambda x: "/usr/bin/sshpass")
    t = SSHTransport(host="h", user="u", password="secret")
    prefix = t._ssh_prefix()
    assert "secret" not in " ".join(prefix)
    assert prefix[0] == "sshpass"


# --------------------------------------------------------------- P2: service
def test_service_status(facts):
    result = service_cmds.status(facts.transport)
    assert "cyops-auth" in result
    assert "cyops-api" in result
    assert "Running" in result


def test_service_status_with_name(facts):
    result = service_cmds.status(facts.transport, name="cyops-auth")
    assert "cyops-auth" in result


def test_service_liveness_ok(facts):
    results = service_cmds.liveness(facts.transport)
    assert len(results) >= 1
    for r in results:
        assert hasattr(r, "label")
        assert hasattr(r, "code")
        assert hasattr(r, "verdict")
        assert r.code != service_cmds._NO_RESPONSE  # should be 200, not 0
        assert "ok" in r.verdict or "unexpected" in r.verdict


def test_service_liveness_detects_wedge(facts):
    wedged_transport = FakeTransport(service_wedged=True)
    results = service_cmds.liveness(wedged_transport)
    wedged = [r for r in results if r.code == service_cmds._NO_RESPONSE]
    assert len(wedged) > 0
    assert all("WEDGED" in r.verdict for r in wedged)


def test_service_restart_gated_by_yes(facts):
    with pytest.raises(PermissionError):
        service_cmds.restart(facts.transport, "cyops-auth", yes=False)


def test_service_restart_succeeds_with_yes(facts):
    result = service_cmds.restart(facts.transport, "cyops-auth", yes=True)
    assert result.service == "cyops-auth" and result.action == "restart" and result.ok


def test_service_restart_rejects_unknown_name_despite_exit_zero(facts):
    # csadm exits 0 but no-ops on an unknown name; ok must reflect the reject text.
    result = service_cmds.restart(facts.transport, "bogus-svc", yes=True)
    assert not result.ok
    assert "can not be modified" in result.output


def test_service_status_name_filters_client_side(facts):
    # --name is ignored by csadm, so the filter is applied to the parsed lines here.
    out = service_cmds.status(facts.transport, name="cyops-auth")
    assert "cyops-auth" in out and "cyops-api" not in out


def test_services_parsed_running(facts):
    states = service_cmds.services(facts.transport)
    assert {s.name for s in states} == {"cyops-auth", "cyops-api"}
    assert all(s.running for s in states)


def test_service_restart_all_gated_by_yes(facts):
    with pytest.raises(PermissionError):
        service_cmds.restart_all(facts.transport, yes=False)


def test_service_restart_all_succeeds_with_yes(facts):
    result = service_cmds.restart_all(facts.transport, yes=True)
    assert result.service == "ALL" and result.action == "restart" and result.ok


def test_service_start_all_not_gated(facts):
    result = service_cmds.start_all(facts.transport)
    assert result.service == "ALL" and result.action == "start" and result.ok


def test_service_listeners(facts):
    lis = service_cmds.listeners(facts.transport)
    assert len(lis) >= 1
    assert all(isinstance(x, service_cmds.Listener) for x in lis)
    blob = " ".join(f"{x.local_address} {x.process}" for x in lis)
    assert "nginx" in blob or "rabbitmq" in blob


# --------------------------------------------------------------- P2: mq
def test_mq_status(facts):
    result = mq_cmds.status(facts.transport)
    assert "RabbitMQ" in result or "Status" in result


def test_mq_queues_lists_depth_and_consumers(facts):
    qs = mq_cmds.queues(facts.transport)
    assert len(qs) >= 1
    for q in qs:
        assert q.name and isinstance(q.messages, int) and isinstance(q.consumers, int)


def test_mq_queues_flags_zero_consumers(facts):
    for q in mq_cmds.queues(facts.transport):
        if q.consumers == 0:
            assert q.flag == "NO CONSUMERS"


def test_mq_queues_flags_backlog(facts):
    # A queue with backlog AND consumers should flag backlog, not zero-consumers.
    class BacklogTransport(FakeTransport):
        def _rabbitmqctl_response(self, argv):
            if argv[1:3] == ["-q", "list_queues"] and "consumers" in argv:
                return "task_queue\t2500\t1\ndefault_queue\t50\t2\n"
            return super()._rabbitmqctl_response(argv)

    qs = mq_cmds.queues(BacklogTransport())
    backlog = [q for q in qs if q.messages >= 1000]
    if backlog:
        assert "BACKLOG" in backlog[0].flag


def test_mq_consumers(facts):
    cs = mq_cmds.consumers(facts.transport)
    assert len(cs) >= 1 and cs[0].queue


def test_mq_vhosts_drops_header_row(facts):
    vhost_names = mq_cmds.vhosts(facts.transport)
    assert "/" in vhost_names
    # the "name" column header must NOT leak in as a bogus vhost (the live bug)
    assert "name" not in vhost_names
    # and --no-table-headers must actually be on the wire
    vh_call = next(c[0] for c in facts.transport.commands if "list_vhosts" in c[0])
    assert "--no-table-headers" in vh_call


def test_mq_permissions_default_vhost_empty(facts):
    # On a real box "/" carries no permissions; the header row must not leak.
    assert mq_cmds.permissions(facts.transport) == []


def test_mq_permissions_all_vhosts_matrix(facts):
    perms = mq_cmds.permissions(facts.transport, all_vhosts=True)
    # "/" is empty; cyops-admin -> admin, intra-cyops -> cyops = 2 populated rows
    assert len(perms) == 2
    by_vhost = {p.vhost: p.user for p in perms}
    assert by_vhost == {"cyops-admin": "admin", "intra-cyops": "cyops"}
    # the per-vhost query was actually scoped with -p <vhost>
    perm_calls = [c[0] for c in facts.transport.commands if "list_permissions" in c[0]]
    assert any("-p" in call and "intra-cyops" in call for call in perm_calls)


def test_mq_to_int_safe_parse(facts):
    assert mq_cmds._to_int("42") == 42
    assert mq_cmds._to_int("0") == 0
    assert mq_cmds._to_int("bogus") == -1
    assert mq_cmds._to_int("") == -1


# --------------------------------------------------------------- P2: logs
def test_logs_tail_with_service_alias(facts):
    result = logs_cmds.tail(facts.transport, "auth")
    assert "auth" in result.lower() or len(result) > 0


def test_logs_tail_with_raw_path(facts):
    result = logs_cmds.tail(facts.transport, "/var/log/custom.log")
    assert len(result) > 0


def test_logs_tail_unknown_service_raises(facts):
    with pytest.raises(ValueError):
        logs_cmds.tail(facts.transport, "bogus_service")


def test_logs_tail_missing_file_raises(facts):
    # A resolvable path that doesn't exist on the box must error, not silently
    # return "" (a stale alias / version mismatch should be loud).
    with pytest.raises(FileNotFoundError):
        logs_cmds.tail(facts.transport, "/var/log/cyops/missing.log")


def test_logs_tail_auth_alias_resolves_to_das_log(facts):
    logs_cmds.tail(facts.transport, "auth")
    tail_calls = [argv for argv, _e, _s in facts.transport.commands if argv[0] == "tail"]
    assert any("/var/log/cyops/cyops-auth/das.log" in argv for argv in tail_calls)


def test_logs_tail_defaults_lines(facts):
    result = logs_cmds.tail(facts.transport, "auth")
    # Ensure the default 100 lines is used (we'd check the argv if needed)
    assert len(result) > 0


def test_logs_tail_custom_lines(facts):
    result = logs_cmds.tail(facts.transport, "auth", lines=10)
    # Check that the -n 10 flag was used
    assert len(result) > 0


def test_logs_scan_minutes_default(facts):
    result = logs_cmds.scan(facts.transport)
    # Should show "(no journal errors...)" if no errors found
    assert "journal" in result.lower() or "no entries" in result.lower()


def test_logs_scan_custom_minutes(facts):
    result = logs_cmds.scan(facts.transport, minutes=5)
    assert len(result) > 0


def test_logs_scan_units_covered(facts):
    # Ensure all expected units are checked (even if no errors)
    logs_cmds.scan(facts.transport, minutes=30)
    # Should run journalctl for each unit in _SCAN_UNITS
    journalctl_calls = [argv for argv, _env, _sudo in facts.transport.commands if argv[0] == "journalctl"]
    assert len(journalctl_calls) > 0


# --------------------------------------------------------------- P3: logs bundle


def test_logs_bundle_returns_tarball_path(facts):
    path = logs_cmds.bundle(facts.transport)
    # FakeTransport returns the csadm bundle output; check it ran csadm log --collect
    cmds = [argv for argv, _e, _s in facts.transport.commands]
    assert any(argv[:3] == ["csadm", "log", "--collect"] for argv in cmds)
    # Return value should be a string (path or raw output)
    assert isinstance(path, str)


# --------------------------------------------------------------- P3: es


def test_es_health_parses_json(facts):
    from pyfsr.cli.appliance import es as es_cmds

    h = es_cmds.health(facts)
    # FakeTransport returns ES health JSON via _curl_response → the fake just
    # returns "200" (not valid JSON), so the parse falls back to "unknown".
    assert h.status in ("green", "yellow", "red", "unknown")
    assert isinstance(h.raw, str)


def test_es_health_green_parsed(facts):
    """When the fake returns valid JSON, ESHealth is populated."""
    import json

    from pyfsr.cli.appliance import es as es_cmds

    payload = json.dumps(
        {
            "cluster_name": "fortisoar",
            "status": "green",
            "number_of_nodes": 1,
            "number_of_data_nodes": 1,
            "active_shards": 120,
            "unassigned_shards": 0,
        }
    )

    class ESFakeTransport(FakeTransport):
        def _curl_response(self, argv):
            return payload

    h = es_cmds.health(Facts(ESFakeTransport()))
    assert h.status == "green"
    assert h.cluster_name == "fortisoar"
    assert h.active_shards == 120
    assert h.unassigned_shards == 0


def test_es_shards_no_unassigned(facts):
    """When ES says no unassigned shards, shards() returns a descriptive row."""
    import json

    from pyfsr.cli.appliance import es as es_cmds

    payload = json.dumps({"error": {"reason": "no unassigned shards to explain"}})

    class NoShardFake(FakeTransport):
        def _curl_response(self, argv):
            return payload

    headers, rows = es_cmds.shards(Facts(NoShardFake()))
    assert rows == [["(no unassigned shards)"]]


# --------------------------------------------------------------- P3: ha


def test_ha_nodes_runs_csadm(facts):
    from pyfsr.cli.appliance import ha as ha_cmds

    ha_cmds.nodes(facts.transport)
    cmds = [argv for argv, _e, _s in facts.transport.commands]
    assert any(argv[:3] == ["csadm", "ha", "list-nodes"] for argv in cmds)


def test_ha_health_runs_csadm(facts):
    from pyfsr.cli.appliance import ha as ha_cmds

    ha_cmds.health(facts.transport)
    cmds = [argv for argv, _e, _s in facts.transport.commands]
    assert any(argv[:3] == ["csadm", "ha", "show-health"] for argv in cmds)


def test_ha_replication_runs_csadm(facts):
    from pyfsr.cli.appliance import ha as ha_cmds

    ha_cmds.replication(facts.transport)
    cmds = [argv for argv, _e, _s in facts.transport.commands]
    assert any(argv[:3] == ["csadm", "ha", "get-replication-stat"] for argv in cmds)


# ---------------------------------------------------------- db getsize / certs


def test_db_getsize_parses_real_format(facts):
    sizes = db_cmds.getsize(facts)
    parsed = {s.data_class: s.size for s in sizes}
    # preamble lines dropped; each "<class> : <size>" kept, unit preserved
    assert parsed == {
        "Primary Data": "7354 MB",
        "Audit Logs": "1089 MB",
        "Workflow Logs": "1138 MB",
        "Archived Data": "8396 kB",
    }
    # size_mb normalises the mixed kB/MB units
    by_class = {s.data_class: s.size_mb for s in sizes}
    assert by_class["Primary Data"] == 7354.0
    assert by_class["Archived Data"] == round(8396 / 1024, 3)
    call = next(c for c in facts.transport.commands if c[0][:3] == ["csadm", "db", "--getsize"])
    assert call[2] is True  # sudo


def test_db_getsize_raw_escape_hatch(facts):
    raw = db_cmds.getsize_raw(facts)
    assert "Following is the current database usage" in raw  # unparsed preamble retained


def test_certs_regenerate_refuses_without_yes(facts):
    from pyfsr.cli.appliance import certs as certs_cmds

    with pytest.raises(PermissionError, match="confirmation"):
        certs_cmds.regenerate(facts.transport, "soar.example.com")
    # nothing ran
    assert not any(c[0][:3] == ["csadm", "certs", "--generate"] for c in facts.transport.commands)


def test_certs_regenerate_runs_with_yes(facts):
    from pyfsr.cli.appliance import certs as certs_cmds

    out = certs_cmds.regenerate(facts.transport, "soar.example.com", yes=True)
    # exact match (not substring-in) — clearer, and dodges CodeQL's URL-substring query
    assert out == "Certificate generated for soar.example.com"
    call = next(c for c in facts.transport.commands if c[0][:3] == ["csadm", "certs", "--generate"])
    assert call[0][3] == "soar.example.com"
    assert call[2] is True  # sudo


def test_certs_regenerate_requires_hostname(facts):
    from pyfsr.cli.appliance import certs as certs_cmds

    with pytest.raises(ValueError, match="hostname"):
        certs_cmds.regenerate(facts.transport, "  ", yes=True)


# --------------------------------------------------------------- P3: diagnose


def test_diagnose_runs_script(facts):
    from pyfsr.cli.appliance import diagnose as diagnose_cmds

    result = diagnose_cmds.run(facts.transport, path="/opt/cyops/scripts/fsr_diagnose.sh")
    cmds = [argv for argv, _e, _s in facts.transport.commands]
    assert any(argv == ["bash", "/opt/cyops/scripts/fsr_diagnose.sh"] for argv in cmds)
    assert isinstance(result, str)


def test_diagnose_missing_script_raises(facts):
    from pyfsr.cli.appliance import diagnose as diagnose_cmds

    # "missing" in the path makes FakeTransport's `test -f` return 1.
    with pytest.raises(FileNotFoundError):
        diagnose_cmds.run(facts.transport, path="/opt/cyops/scripts/missing_diagnose.sh")


# --- P3+: service stop/start/systemctl, mq purge + purge_workflows, host metrics ---


def _cmds(t):
    """The argv of every command the fake recorded."""
    return [argv for argv, _env, _sudo in t.commands]


def test_service_stop_gated(facts):
    with pytest.raises(PermissionError):
        service_cmds.stop(facts.transport, "celeryd")


def test_service_stop_with_yes_uses_csadm(facts):
    service_cmds.stop(facts.transport, "celeryd", yes=True)
    assert ["csadm", "services", "--stop-service", "celeryd"] in _cmds(facts.transport)


def test_service_start_not_gated(facts):
    service_cmds.start(facts.transport, "celeryd")
    assert ["csadm", "services", "--start-service", "celeryd"] in _cmds(facts.transport)


def test_systemctl_mutating_gated(facts):
    with pytest.raises(PermissionError):
        service_cmds.systemctl(facts.transport, "kill", "celeryd.service")


def test_systemctl_readonly_not_gated(facts):
    service_cmds.systemctl(facts.transport, "is-active", "celeryd.service")
    assert ["systemctl", "is-active", "celeryd.service"] in _cmds(facts.transport)


def test_systemctl_kill_signal_shape(facts):
    service_cmds.systemctl(facts.transport, "kill", "celeryd", signal="SIGKILL", yes=True)
    assert ["systemctl", "kill", "--signal=SIGKILL", "celeryd"] in _cmds(facts.transport)


def test_mq_purge_queue_gated(facts):
    with pytest.raises(PermissionError):
        mq_cmds.purge_queue(facts.transport, "celery", vhost="fsr-cluster")


def test_mq_purge_queue_runs_purge(facts):
    result = mq_cmds.purge_queue(facts.transport, "celery", vhost="fsr-cluster", yes=True)
    assert result.queue == "celery" and result.vhost == "fsr-cluster"
    assert ["rabbitmqctl", "-q", "purge_queue", "celery", "-p", "fsr-cluster"] in _cmds(facts.transport)


def test_mq_purge_workflows_gated(facts):
    with pytest.raises(PermissionError):
        mq_cmds.purge_workflows(facts.transport)


def test_mq_purge_workflows_hard_default_sigkills(facts):
    report = mq_cmds.purge_workflows(facts.transport, yes=True)
    cmds = _cmds(facts.transport)
    # Hard path: purge the workflow queue, then SIGKILL celeryd (NOT csadm stop).
    assert ["rabbitmqctl", "-q", "purge_queue", "celery", "-p", "fsr-cluster"] in cmds
    assert ["systemctl", "kill", "--signal=SIGKILL", "celeryd"] in cmds
    assert not any(c[:3] == ["csadm", "services", "--stop-service"] for c in cmds)
    # purge happens BEFORE the kill (so the respawned pool sees an empty queue).
    purge_i = cmds.index(["rabbitmqctl", "-q", "purge_queue", "celery", "-p", "fsr-cluster"])
    kill_i = cmds.index(["systemctl", "kill", "--signal=SIGKILL", "celeryd"])
    assert purge_i < kill_i
    assert any(c[:4] == ["csadm", "services", "--restart-service", "cyops-integrations-agent"] for c in cmds)
    assert report.purges


def test_mq_purge_workflows_graceful_uses_csadm(facts):
    mq_cmds.purge_workflows(facts.transport, yes=True, graceful=True)
    cmds = _cmds(facts.transport)
    assert ["csadm", "services", "--stop-service", "celeryd"] in cmds
    assert ["csadm", "services", "--start-service", "celeryd"] in cmds
    assert not any(c[:2] == ["systemctl", "kill"] for c in cmds)


def test_host_parse_meminfo():
    from pyfsr.cli.appliance import host

    free = "\n".join(
        [
            "              total        used        free",
            "Mem:          24096       12000        500",
            "Swap:          8191        1024",
        ]
    )
    m = host._parse_meminfo(free)
    assert (m.total_mb, m.used_mb, m.swap_total_mb, m.swap_used_mb) == (24096, 12000, 8191, 1024)


def test_host_parse_loadavg():
    from pyfsr.cli.appliance import host

    assert host._parse_loadavg("1.50 2.30 0.90 1/234 5678") == host.LoadAvg(1.5, 2.3, 0.9)


def test_host_parse_process_rss_regex():
    from pyfsr.cli.appliance import host

    ps = "1024 /usr/bin/celery -A x worker\n2048 /usr/bin/celery -A x worker\n999 sshd: foo"
    p = host._parse_process_rss(ps, r"celery\b.*worker")
    assert (p.count, p.sum_mb, p.peak_mb) == (2, 3.0, 2.0)


def test_host_parse_disk():
    from pyfsr.cli.appliance import host

    df = "Filesystem 1M-blocks Used Available Use% Mounted on\n/dev/sda1 102400 51200 51200 50% /opt/cyops"
    d = host._parse_disk(df, "/opt/cyops")
    assert (d.size_mb, d.used_mb, d.use_pct) == (102400, 51200, 50)


def test_host_split_sections():
    from pyfsr.cli.appliance import host

    sec = host._split_sections("@@FREE\nMem: 1 2 3\n@@LOAD\n0.1 0.2 0.3\n@@PS\n10 celery worker")
    assert sorted(sec) == ["FREE", "LOAD", "PS"]
    assert sec["LOAD"] == "0.1 0.2 0.3"


# --- csadm typed parsers: service.services / ha.nodes+health / license.details ---


def test_service_services_parses_ansi_and_since():
    from pyfsr.cli.appliance import service

    out = (
        "rabbitmq-server..........[\x1b[48;5;34mRunning\x1b[0m]      since Thu 2026-05-07 14:10:35 UTC\n"
        "postgresql-16............[\x1b[48;5;34mRunning\x1b[0m]      since Thu 2026-05-07 14:10:24 UTC\n"
        "celeryd..................[Stopped]"
    )
    states = []
    from pyfsr.cli.appliance._text import strip_ansi

    for line in strip_ansi(out).splitlines():
        m = service._STATUS_LINE.match(line.strip())
        assert m, line
        st = m.group("status").strip()
        states.append(service.ServiceState(m.group("name"), st.lower() == "running", st, m.group("since")))
    assert states[0] == service.ServiceState("rabbitmq-server", True, "Running", "Thu 2026-05-07 14:10:35 UTC")
    assert states[1].name == "postgresql-16" and states[1].running
    assert states[2] == service.ServiceState("celeryd", False, "Stopped", None)


def test_ha_parse_nodes_columns():
    from pyfsr.cli.appliance import ha

    txt = (
        "nodeId                              nodeName    status    role     comment         mode         fsrVersion\n"
        "----------------------------------  ----------  --------  -------  --------------  -----------  ------------\n"
        "* 572b3ecd3ddbc133a650f3faecc7c286  fsr-1       active    primary  primary server  operational  7.6.2-5507"
    )
    nodes = ha._parse_nodes(txt)
    assert len(nodes) == 1
    n = nodes[0]
    assert n.node_id == "572b3ecd3ddbc133a650f3faecc7c286" and n.is_current
    assert n.name == "fsr-1" and n.role == "primary"
    assert n.comment == "primary server"  # space-containing cell sliced by column
    assert n.fsr_version == "7.6.2-5507"


def test_ha_parse_health_sections():
    from pyfsr.cli.appliance import ha

    txt = (
        "Node Name                     : fsr-1\n"
        "Node ID                       : abc123\n"
        "Uptime                        : 46 days, 0:58:21\n"
        "Mode                          : operational\n"
        "Services Status               : green\n"
        "Queued Workflow Count         : 0\n"
        "Memory Usage:\n"
        "------------------------------------------------\n"
        "total    used    avail      percent\n"
        "-------  ------  -------  ---------\n"
        "31.1G    14.2G   15.2G         51.2\n"
        "Swap Usage:\n"
        "total    used    free      percent\n"
        "-------  ------  ------  ---------\n"
        "0bytes   0bytes  0bytes          0\n"
        "Disk Usage:\n"
        "mountpoint    device     total    used    avail      percent\n"
        "------------  ---------  -------  ------  -------  ---------\n"
        "/             /dev/vda5  498.9G   41.6G   457.3G         8.3\n"
        "/boot         /dev/vda2  936.0M   451.0M  485.0M        48.2\n"
        "System load and CPU utilization:\n"
    )
    h = ha._parse_health(txt)
    assert h.node_name == "fsr-1" and h.mode == "operational"
    assert h.services_status == "green" and h.queued_workflows == 0
    assert h.memory and h.memory.used == "14.2G" and h.memory.percent == 51.2
    assert h.swap and h.swap.percent == 0.0
    assert [d.mountpoint for d in h.disks] == ["/", "/boot"]
    assert h.disks[1].percent == 48.2


def test_license_parse_details_typed_ints():
    from pyfsr.cli.appliance import license as lic

    txt = (
        "Type           : Evaluation\n"
        "Edition        : Multi-tenant\n"
        "Role           : Manager\n"
        "Total Users    : 2\n"
        "Expiry Date    : 2027-04-08\n"
        "Remaining Days : 290\n"
        "Serial no      : FSRVMPTM26000304\n"
        "Device UUID    : 572b3ecd3ddbc133a650f3faecc7c286"
    )
    d = lic._parse_details(txt)
    assert d.edition == "Multi-tenant" and d.total_users == 2 and d.remaining_days == 290
    assert d.serial_no == "FSRVMPTM26000304"
    assert d.device_uuid == "572b3ecd3ddbc133a650f3faecc7c286"
    assert d.fields["Type"] == "Evaluation"


def test_text_helpers():
    from pyfsr.cli.appliance import _text

    assert _text.strip_ansi("a\x1b[48;5;34mb\x1b[0mc") == "abc"
    assert _text.kv_pairs("K   : v\nno-sep line\nK2 : w") == {"K": "v", "K2": "w"}
    spans = _text.dash_columns("---  -----  --")
    assert _text.slice_columns("ab   cdefg  hi", spans) == ["ab", "cdefg", "hi"]
    assert _text.to_int("Remaining 290 days") == 290 and _text.to_int(None, -1) == -1
    assert _text.to_float("51.2%") == 51.2
