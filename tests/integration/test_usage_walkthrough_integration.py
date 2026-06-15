"""End-to-end usage walkthrough against a live FortiSOAR.

A single, readable pass over *most* of the documented SDK surface — the same
calls shown in the README / docs / docstrings — so you can point it at a real
appliance and prove the whole client actually works, not just that the symbols
exist (the latter is covered offline by ``tests/unit/test_doc_examples.py``).

Run it (opt-in, read-only by default)::

    export FSR_BASE_URL=https://my-fsr:13002
    export FSR_USERNAME=csadmin FSR_PASSWORD=... FSR_VERIFY_SSL=false
    pytest -m integration tests/integration/test_usage_walkthrough_integration.py -v

Every check is read-only except :func:`test_walkthrough_alert_write_lifecycle`,
which creates one alert and deletes it again (guarded + self-cleaning, and
skipped unless ``FSR_ALLOW_WRITES=1``).
"""

from __future__ import annotations

import os

import pytest

from pyfsr.query import Query

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Core client + generic transport (README "Basic Usage")
# --------------------------------------------------------------------------- #
def test_walkthrough_generic_get(client):
    """The generic GET shown in the quickstart — note the path is /api/3/."""
    resp = client.get("/api/3/alerts", params={"$limit": 1})
    assert "hydra:member" in resp


def test_walkthrough_alerts_read(client):
    """client.alerts.list() / get() (read side of the alerts example)."""
    alerts = client.alerts.list(params={"$limit": 3})
    members = alerts.get("hydra:member", alerts if isinstance(alerts, list) else [])
    assert isinstance(members, list)
    if members:
        one = client.alerts.get(members[0]["uuid"])
        assert one.get("uuid") == members[0]["uuid"]


# --------------------------------------------------------------------------- #
# Module / field schema discovery (modules.* docstring examples)
# --------------------------------------------------------------------------- #
def test_walkthrough_modules_discovery(client):
    mods = client.list_modules()  # -> [{type, label, plural}, ...]
    types = {m["type"] for m in mods}
    assert {"alerts", "incidents"} & types, "expected core modules present"

    desc = client.describe_module("alerts")
    assert desc["module"] == "alerts" and desc["field_count"] > 0
    names = {f["name"] for f in desc["fields"]}
    assert {"name", "severity", "status"} <= names

    # with_values resolves a picklist field's accepted friendly vocabulary
    desc_v = client.modules.describe("alerts", with_values=True)
    sev = next(f for f in desc_v["fields"] if f["name"] == "severity")
    assert sev["picklist_name"] and isinstance(sev.get("picklist_values"), list)
    assert sev["picklist_values"], "severity should expose friendly values"

    # search + find_field
    assert client.modules.search("alert"), "search('alert') should match"
    assert client.modules.find_field(name="severity"), "severity should be locatable"


# --------------------------------------------------------------------------- #
# Picklists + friendly-value resolution (picklists.* examples; alertforge core)
# --------------------------------------------------------------------------- #
def test_walkthrough_picklists_and_resolution(client):
    names = client.picklists.list()
    assert "Severity" in names

    assert client.picklists.options("Severity"), "Severity should have item values"
    pick = client.picklists.for_field("alerts", "severity")
    assert pick  # the picklist backing the field

    # a known-good value resolves to an IRI
    iri = client.picklists.resolve("High", picklist="Severity")
    assert isinstance(iri, str) and "/api/3/picklists/" in iri

    # resolve_record_fields: good value -> IRI; bad value -> reported, not raised
    report: list = []
    out = client.picklists.resolve_record_fields(
        "alerts",
        {"name": "pyfsr walkthrough", "severity": "High", "status": "Open"},
        report=report,
    )
    assert out["severity"].startswith("/api/3/picklists/")
    assert not report, f"unexpected resolution misses: {report}"

    miss: list = []
    client.picklists.resolve_record_fields(
        "alerts", {"severity": "Definitely Not A Severity"}, report=miss
    )
    assert miss and miss[0]["field"] == "severity" and miss[0]["valid_values"]


# --------------------------------------------------------------------------- #
# Records + Query (client.records(...).query / iterate examples)
# --------------------------------------------------------------------------- #
def test_walkthrough_records_and_query(client):
    alerts = client.records("alerts")
    page = alerts.query(Query().limit(5))
    assert hasattr(page, "__iter__")

    count = 0
    for _ in alerts.iterate(query=Query().limit(3), max_records=3):
        count += 1
    assert count <= 3


# --------------------------------------------------------------------------- #
# Connectors + Playbooks (connectors.* / playbooks.* examples)
# --------------------------------------------------------------------------- #
def test_walkthrough_connectors(client):
    configured = client.connectors.list_configured()
    assert isinstance(configured, list)


def test_walkthrough_playbooks(client):
    runs = client.playbooks.runs(limit=3)
    assert isinstance(runs, list) or "hydra:member" in runs


# --------------------------------------------------------------------------- #
# Guarded write path — proves create/delete actually work, then cleans up.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("FSR_ALLOW_WRITES") != "1",
    reason="write test is opt-in; set FSR_ALLOW_WRITES=1 to run",
)
def test_walkthrough_alert_write_lifecycle(client):
    # Friendly picklist values must be resolved to IRIs before create() — the
    # appliance rejects raw "High"/"Open". This is the documented pattern.
    data = client.picklists.resolve_record_fields(
        "alerts",
        {
            "name": "pyfsr walkthrough — safe to delete",
            "severity": "High",
            "status": "Open",
            "recordTags": ["pyfsr-walkthrough"],
        },
        strict=True,
    )
    created = client.alerts.create(**data)
    uuid = created["uuid"]
    try:
        fetched = client.alerts.get(uuid)
        assert fetched["uuid"] == uuid
        assert fetched["name"].startswith("pyfsr walkthrough")
    finally:
        client.alerts.delete(uuid)
    # confirm it's gone
    with pytest.raises(Exception):
        client.alerts.get(uuid)
