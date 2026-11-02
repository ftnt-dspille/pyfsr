"""Live proof for building a module from simple field specs (opt-in: pytest -m integration).

Uses the spec shape an agent naturally writes (``"type": "string"``, a picklist given
as a list of values), so it proves:

- ``fields_from_specs`` creates the picklist and binds the field to it, and a record
  written with a friendly value reads back as that value;
- a datetime written as epoch seconds reads back unchanged;
- ``roles.grant_module_permissions`` finds a module published *after* the client
  first cached the module list (it used to raise "not found" until a new client).

Slow: one publish. The module and its picklist are removed afterwards.
"""

import time

import pytest

pytestmark = pytest.mark.integration

MODULE = f"pyfsr_specprobe_{int(time.time()) % 100000}"
ROLE = "Full App Permissions"
SPECS = [
    {"name": "assetTag", "type": "string", "required": True, "label": "Asset Tag"},
    {"name": "ownerEmail", "type": "email", "label": "Owner Email"},
    {"name": "dateReported", "type": "datetime", "label": "Date Reported"},
    {"name": "status", "type": "picklist", "picklist": ["Reported", "Investigating", "Closed"], "label": "Status"},
]


@pytest.fixture(scope="module")
def probe(client):
    ma = client.modules_admin
    client.roles._module_by_type()  # prime the cache before the module exists
    fields = ma.fields_from_specs(MODULE, SPECS)
    ma.create_module(MODULE, label="pyfsr Spec Probe", fields=fields)
    ma.publish(timeout=900)
    granted = None
    for _ in range(30):
        try:
            client.roles.grant_module_permissions(ROLE, module=MODULE)
            granted = True
            break
        except ValueError as exc:
            granted = str(exc)
            time.sleep(10)
    for _ in range(30):
        try:
            r = client.post(f"/api/3/{MODULE}", {"assetTag": "probe"})
            client.delete(r["@id"])
            break
        except Exception:  # noqa: BLE001 - Access Denied until the grant lands
            time.sleep(10)
    yield client, granted
    ma.delete_module(MODULE)
    client.picklists.remove_picklist(f"{MODULE} status")


def test_grant_finds_a_module_published_after_the_cache(probe):
    _, granted = probe
    assert granted is True, f"grant failed on a module published after the cache: {granted}"


def test_picklist_from_values_and_epoch_date_round_trip(probe):
    client, _ = probe
    rec = client.records(MODULE).create(
        {"assetTag": "LT-0042", "ownerEmail": "owner@example.com", "dateReported": 1773446400, "status": "Reported"},
        raw=True,
    )
    try:
        got = client.records(MODULE).get(rec["@id"].rsplit("/", 1)[-1], raw=True)
        assert got["status"] in ("Reported", {"itemValue": "Reported"}) or (
            isinstance(got["status"], dict) and got["status"].get("itemValue") == "Reported"
        ), got["status"]
        assert got["dateReported"] == 1773446400
    finally:
        client.delete(rec["@id"])


def test_picklist_holds_the_given_values(probe):
    client, _ = probe
    client.picklists.clear_cache()
    values = [v["itemValue"] for v in client.picklists.values(f"{MODULE} status")]
    assert values == ["Reported", "Investigating", "Closed"]
