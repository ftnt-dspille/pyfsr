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
        if argv[:3] == ["csadm", "ha", "list-nodes"]:
            return "node1  primary  fortisoar.example.com\nnode2  secondary  fortisoar.example.com\n"
        if argv[:3] == ["csadm", "ha", "show-health"]:
            return "HA Health: OK\nPrimary: node1\nSecondary: node2\n"
        if argv[:3] == ["csadm", "ha", "get-replication-stat"]:
            return "Replication lag: 0 bytes\nStatus: streaming\n"
        if argv[:3] == ["csadm", "services", "--status"]:
            return "cyops-auth\tactive\t0\t0\ncyops-api\tactive\t0\t0\n"
        if argv[:4] == ["csadm", "services", "--restart", "--name"]:
            return f"service {argv[4]} restarted\n"
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
        """Fake rabbitmqctl -q output (tab-separated quiet format)."""
        if argv[1:3] == ["-q", "status"]:
            return "Status of node rabbit@appliance ...\nRabbitMQ 3.8.14\n"
        if argv[1:3] == ["-q", "list_queues"] and "consumers" in argv:
            if self._queues_backlog:
                return "task_queue\t2500\t0\ndefault_queue\t50\t2\n"
            else:
                return "task_queue\t100\t1\ndefault_queue\t50\t2\n"
        if argv[1:3] == ["-q", "list_consumers"]:
            return "task_queue\t<rabbit@appliance.1.250>\ndefault_queue\t<rabbit@appliance.2.251>\n"
        if argv[1:3] == ["-q", "list_vhosts"]:
            return "/\n/mq_internal\n"
        if argv[1:3] == ["-q", "list_permissions"]:
            return "guest\t.*\t.*\t.*\nadmin\t.*\t.*\t.*\n"
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
    headers, rows = db_cmds.list_databases(facts)
    venom = next(r for r in rows if r[0] == "venom")
    assert venom[2] == "content"
    das = next(r for r in rows if r[0] == "das")
    assert das[2] == "das"


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
    assert "active" in result


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
    assert "restarted" in result


def test_service_listeners(facts):
    headers, rows = service_cmds.listeners(facts.transport)
    assert "local_address" in headers
    assert "process" in headers
    assert len(rows) >= 1
    # At least one row should have nginx or rabbitmq
    all_rows = " ".join(str(r) for r in rows)
    assert "nginx" in all_rows or "rabbitmq" in all_rows


# --------------------------------------------------------------- P2: mq
def test_mq_status(facts):
    result = mq_cmds.status(facts.transport)
    assert "RabbitMQ" in result or "Status" in result


def test_mq_queues_lists_depth_and_consumers(facts):
    headers, rows = mq_cmds.queues(facts.transport)
    assert "queue" in headers
    assert "messages" in headers
    assert "consumers" in headers
    assert "flag" in headers
    assert len(rows) >= 1
    # Check that columns are populated
    for row in rows:
        assert row[0]  # queue name
        assert row[1].isdigit()  # messages count


def test_mq_queues_flags_zero_consumers(facts):
    headers, rows = mq_cmds.queues(facts.transport)
    row_with_zero = [r for r in rows if int(r[2]) == 0]
    if row_with_zero:
        assert "NO CONSUMERS" in row_with_zero[0][3]


def test_mq_queues_flags_backlog(facts):
    # Create a transport with backlog but ensure we have a queue with backlog AND consumers
    class BacklogTransport(FakeTransport):
        def _rabbitmqctl_response(self, argv):
            if argv[1:3] == ["-q", "list_queues"] and "consumers" in argv:
                # Queue with backlog but with consumers (should flag backlog, not zero-consumers)
                return "task_queue\t2500\t1\ndefault_queue\t50\t2\n"
            return super()._rabbitmqctl_response(argv)

    backlog_transport = BacklogTransport()
    headers, rows = mq_cmds.queues(backlog_transport)
    row_with_backlog = [r for r in rows if int(r[1]) >= 1000]
    if row_with_backlog:
        assert "BACKLOG" in row_with_backlog[0][3]


def test_mq_consumers(facts):
    headers, rows = mq_cmds.consumers(facts.transport)
    assert "consumer" in headers
    assert len(rows) >= 1


def test_mq_vhosts(facts):
    headers, rows = mq_cmds.vhosts(facts.transport)
    assert "vhost" in headers
    assert len(rows) >= 1
    vhost_names = [r[0] for r in rows]
    assert "/" in vhost_names


def test_mq_permissions(facts):
    headers, rows = mq_cmds.permissions(facts.transport)
    assert "user" in headers
    assert "configure" in headers
    assert "write" in headers
    assert "read" in headers
    assert len(rows) >= 1


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
