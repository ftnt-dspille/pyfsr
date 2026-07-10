"""Unit tests for the branching appliance ``cmd_*`` handlers in cli/__main__.py.

Focuses on the handlers with real logic — exit-code health gates, write guards,
and empty/drop branches — rather than pure passthrough glue (whose underlying
appliance command modules are covered separately). ``_make_facts`` /
``_make_transport`` are patched so no SSH/transport is opened.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

from pyfsr.cli import __main__ as m


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(fmt="table", **kw)


# -- cmd_db_exec write guard -------------------------------------------------
def test_db_exec_requires_write_flag(capfd):
    assert m.cmd_db_exec(_args(write=False, sql="DELETE", role=None, db=None, yes=True)) == 2
    assert "pass --write" in capfd.readouterr().err


def test_db_exec_runs_with_write(capfd):
    facts = SimpleNamespace(resolve_db=lambda role, db: "content_db")
    with (
        patch.object(m, "_make_facts", return_value=facts),
        patch.object(m.db_cmds, "exec_write", return_value=("content_db", "OK")),
    ):
        assert m.cmd_db_exec(_args(write=True, sql="UPDATE x", role=None, db=None, yes=True)) == 0
    assert "content_db: OK" in capfd.readouterr().out


# -- cmd_db_orphans branches -------------------------------------------------
def _orphan(base="mod", table="t", kind="table"):
    return SimpleNamespace(base=base, table=table, kind=kind)


def test_db_orphans_none_returns_0():
    facts = SimpleNamespace(content_db=lambda: "cdb")
    with (
        patch.object(m, "_make_facts", return_value=facts),
        patch.object(m, "_emit_target"),
        patch.object(m.db_cmds, "find_orphan_module_tables", return_value=[]),
    ):
        assert m.cmd_db_orphans(_args(drop=False, yes=False)) == 0


def test_db_orphans_found_without_drop_returns_1(capfd):
    facts = SimpleNamespace(content_db=lambda: "cdb")
    with (
        patch.object(m, "_make_facts", return_value=facts),
        patch.object(m, "_emit_target"),
        patch.object(m.db_cmds, "find_orphan_module_tables", return_value=[_orphan()]),
    ):
        assert m.cmd_db_orphans(_args(drop=False, yes=False)) == 1
    assert "orphan table" in capfd.readouterr().err


def test_db_orphans_drop_returns_0(capfd):
    facts = SimpleNamespace(content_db=lambda: "cdb")
    with (
        patch.object(m, "_make_facts", return_value=facts),
        patch.object(m, "_emit_target"),
        patch.object(m.db_cmds, "find_orphan_module_tables", return_value=[_orphan(base="modA")]),
        patch.object(m.db_cmds, "drop_module_tables", return_value={"db": "cdb", "dropped": ["modA_t"]}),
    ):
        assert m.cmd_db_orphans(_args(drop=True, yes=True)) == 0
    assert "dropped modA_t" in capfd.readouterr().out


# -- cmd_service_status / liveness health gates ------------------------------
def test_service_status_raw():
    with (
        patch.object(m, "_make_transport"),
        patch.object(m.service_cmds, "status", return_value="raw dump"),
    ):
        assert m.cmd_service_status(_args(raw=True, name=None)) == 0


def test_service_status_all_up_returns_0():
    states = [SimpleNamespace(name="a", running=True, status="running", since="1h")]
    with (
        patch.object(m, "_make_transport"),
        patch.object(m.service_cmds, "services", return_value=states),
    ):
        assert m.cmd_service_status(_args(raw=False, name=None)) == 0


def test_service_status_any_down_returns_1():
    states = [
        SimpleNamespace(name="a", running=True, status="running", since="1h"),
        SimpleNamespace(name="b", running=False, status="dead", since=None),
    ]
    with (
        patch.object(m, "_make_transport"),
        patch.object(m.service_cmds, "services", return_value=states),
    ):
        assert m.cmd_service_status(_args(raw=False, name=None)) == 1


def test_service_liveness_wedged_returns_1():
    probes = [SimpleNamespace(label="api", method="GET", path="/", code=0, verdict="wedged")]
    with (
        patch.object(m, "_make_transport"),
        patch.object(m.service_cmds, "liveness", return_value=probes),
    ):
        assert m.cmd_service_liveness(_args()) == 1


def test_service_liveness_healthy_returns_0():
    probes = [SimpleNamespace(label="api", method="GET", path="/", code=200, verdict="ok")]
    with (
        patch.object(m, "_make_transport"),
        patch.object(m.service_cmds, "liveness", return_value=probes),
    ):
        assert m.cmd_service_liveness(_args()) == 0


# -- cmd_license_show / drift ------------------------------------------------
def test_license_show_raw():
    with (
        patch.object(m, "_make_transport"),
        patch.object(m.license_cmds, "show", return_value="LICENSE BLOB"),
    ):
        assert m.cmd_license_show(_args(raw=True)) == 0


def test_license_drift_returns_1_when_drifted():
    report = SimpleNamespace(file_uuid="a", csadm_uuid="b", drifted=True, verdict="DRIFT")
    with (
        patch.object(m, "_make_transport"),
        patch.object(m.license_cmds, "drift", return_value=report),
    ):
        assert m.cmd_license_drift(_args()) == 1


def test_license_drift_returns_0_when_clean():
    report = SimpleNamespace(file_uuid="a", csadm_uuid="a", drifted=False, verdict="OK")
    with (
        patch.object(m, "_make_transport"),
        patch.object(m.license_cmds, "drift", return_value=report),
    ):
        assert m.cmd_license_drift(_args()) == 0


# -- cmd_es_health gate ------------------------------------------------------
def _health(status="green"):
    return SimpleNamespace(
        status=status,
        cluster_name="c",
        num_nodes=3,
        num_data_nodes=3,
        active_shards=10,
        unassigned_shards=0,
    )


def test_es_health_red_returns_1():
    with (
        patch.object(m, "_make_facts"),
        patch.object(m.es_cmds, "health", return_value=_health("red")),
    ):
        assert m.cmd_es_health(_args()) == 1


def test_es_health_green_returns_0():
    with (
        patch.object(m, "_make_facts"),
        patch.object(m.es_cmds, "health", return_value=_health("green")),
    ):
        assert m.cmd_es_health(_args()) == 0


# -- cmd_service_restart / stop exit codes -----------------------------------
def test_service_restart_ok_and_fail():
    with patch.object(m, "_make_transport"):
        with patch.object(m.service_cmds, "restart", return_value=SimpleNamespace(ok=True)):
            assert m.cmd_service_restart(_args(name="nginx", yes=True)) == 0
        with patch.object(m.service_cmds, "restart", return_value=SimpleNamespace(ok=False)):
            assert m.cmd_service_restart(_args(name="nginx", yes=True)) == 1
