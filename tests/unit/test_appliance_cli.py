"""Unit tests for the ``pyfsr appliance`` CLI (P1: transport / facts / db).

All tests drive a :class:`FakeTransport` — no live appliance, ssh, or psql.
The fake answers psql-shaped queries by pattern so facts resolution and the db
verbs can be exercised offline.
"""

from __future__ import annotations

import pytest

from pyfsr.cli.appliance import db as db_cmds
from pyfsr.cli.appliance.facts import Facts
from pyfsr.cli.appliance.transport import (
    CommandResult,
    SSHTransport,
    Transport,
    TransportError,
    make_transport,
)

UUID = "0123456789abcdef0123456789abcdef"
US = "\x1f"  # the unit-separator field delimiter Facts.psql uses


class FakeTransport(Transport):
    """Transport that fabricates psql/csadm output by matching on the command."""

    target = "fake"

    def __init__(self, *, tables=None, databases=None):
        self.commands = []
        self._tables = tables or ["widgets", "widgets_alerts", "widgets_team", "gadgets"]
        self._databases = databases or {"venom": "7 GB", "das": "200 MB", "postgres": "8 MB"}

    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        self.commands.append((argv, env, sudo))
        out = self._dispatch(argv, env)
        return CommandResult(argv, 0, out, "")

    def _dispatch(self, argv, env) -> str:
        if argv[:3] == ["csadm", "license", "--get-device-uuid"]:
            return f"Device UUID: {UUID}\n"
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


def test_ssh_password_never_in_argv(monkeypatch):
    monkeypatch.setattr("pyfsr.cli.appliance.transport.shutil.which", lambda x: "/usr/bin/sshpass")
    t = SSHTransport(host="h", user="u", password="secret")
    prefix = t._ssh_prefix()
    assert "secret" not in " ".join(prefix)
    assert prefix[0] == "sshpass"
