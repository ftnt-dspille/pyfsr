"""Unit tests for the :class:`pyfsr.Appliance` facade.

The facade just binds a connection to the per-module verb functions, so these
tests confirm the wiring (correct delegation, the ``yes`` gating survives, and
the ``client.appliance()`` host derivation) by driving the same offline
``FakeTransport`` the CLI tests use — no live appliance.
"""

from __future__ import annotations

import pytest
from test_appliance_cli import UUID, FakeTransport

from pyfsr import Appliance
from pyfsr.cli.appliance.facts import Facts


def _appliance(**kw) -> Appliance:
    return Appliance(_facts=Facts(FakeTransport(**kw)))


def test_namespaces_present():
    a = _appliance()
    for ns in ("db", "service", "mq", "host", "license", "logs", "es", "ha", "certs"):
        assert hasattr(a, ns)
    assert callable(a.info)
    assert callable(a.diagnose)


def test_db_query_delegates():
    a = _appliance()
    dbname, headers, rows = a.db.query("SELECT count(*) FROM widgets")
    assert dbname  # resolved content DB name echoed back
    assert isinstance(headers, list)
    assert isinstance(rows, list)


def test_db_execute_requires_yes():
    a = _appliance()
    with pytest.raises(PermissionError):
        a.db.execute("DELETE FROM widgets", yes=False)


def test_db_query_rejects_writes():
    a = _appliance()
    with pytest.raises(ValueError):
        a.db.query("DELETE FROM widgets")


def test_drop_module_tables_requires_yes():
    a = _appliance()
    with pytest.raises(PermissionError):
        a.db.drop_module_tables("widgets", yes=False)


def test_find_orphan_module_tables_empty_on_healthy_box():
    a = _appliance()  # stock tables are all backed by live modules
    assert a.db.find_orphan_module_tables() == []


def test_find_orphan_module_tables_detects_deleted_module():
    # A deleted module leaves <base> + its auto-created <base>_team/<base>_actor
    # join tables behind, with no model_metadatas row backing <base>.
    a = _appliance(
        tables=[
            "widgets",
            "widgets_team",
            "teamscoperepro",
            "teamscoperepro_team",
            "teamscoperepro_actor",
            "gadgets",
        ],
        live_modules=["widgets", "gadgets"],
    )
    orphans = a.db.find_orphan_module_tables()
    assert {o.table for o in orphans} == {
        "teamscoperepro",
        "teamscoperepro_team",
        "teamscoperepro_actor",
    }
    assert {o.base for o in orphans} == {"teamscoperepro"}
    kinds = {o.table: o.kind for o in orphans}
    assert kinds["teamscoperepro"] == "base"
    assert kinds["teamscoperepro_team"] == "join"


def test_license_device_uuid_delegates():
    a = _appliance()
    assert a.license.device_uuid() == UUID


def test_service_status_delegates():
    a = _appliance()
    out = a.service.status()
    assert "cyops-auth" in out


def test_mq_purge_requires_yes():
    a = _appliance()
    with pytest.raises(Exception):  # PermissionError or gating ValueError
        a.mq.purge_queue("some-queue", yes=False)


def test_facts_and_transport_exposed():
    fake = FakeTransport()
    a = Appliance(_facts=Facts(fake))
    assert a._facts.transport is fake
    assert isinstance(a._facts, Facts)


def test_client_appliance_derives_host(mock_client):
    # The appliance reuses the client's host but builds its own SSH transport;
    # no connection is attempted until a verb runs.
    box = mock_client.appliance(key_path="/tmp/none")
    assert getattr(box._facts.transport, "host", None) == "test.fortisoar.com"
