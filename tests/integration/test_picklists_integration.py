"""Live integration tests for PicklistsAPI (opt-in: pytest -m integration)."""

import pytest

pytestmark = pytest.mark.integration


def test_list_names(client):
    names = client.picklists.list()
    assert isinstance(names, list)
    assert names, "expected at least one picklist on the appliance"
    assert all(isinstance(n, str) for n in names)


def test_for_field_alert_severity(client):
    picklist = client.picklists.for_field("alerts", "severity")
    assert picklist, "alerts.severity should be picklist-backed"
    items = client.picklists.values(picklist)
    assert items, f"picklist {picklist!r} has no items"
    assert all("iri" in it for it in items)


def test_resolve_roundtrip(client):
    picklist = client.picklists.for_field("alerts", "severity")
    label = next(
        (it["itemValue"] for it in client.picklists.values(picklist) if it["itemValue"]),
        None,
    )
    assert label
    iri = client.picklists.resolve(label, picklist=picklist)
    assert iri and iri.startswith("/api/3/picklists/")
    # IRI passthrough
    assert client.picklists.resolve(iri) == iri
