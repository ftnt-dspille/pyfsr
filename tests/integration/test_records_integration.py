"""Live integration tests for the P1 backbone (records / query / pagination).

Requires a reachable FortiSOAR + examples/config.toml. Deselected by default;
run with: pytest -m integration
"""

import pytest

from pyfsr import HydraPage, Query

pytestmark = pytest.mark.integration


def test_records_list_returns_hydrapage(client):
    page = client.records("alerts").list(limit=3)
    assert isinstance(page, HydraPage)
    assert page.count <= 3
    # A populated dev box should report a total across all pages.
    assert page.total is None or page.total >= page.count


def test_records_query_with_select(client):
    q = Query().sort("createDate", "DESC").select("uuid", "name").limit(3)
    page = client.records("alerts").query(q)
    assert page.count <= 3
    for rec in page.members:
        assert "uuid" in rec


def test_records_get_by_uuid_roundtrip(client):
    page = client.records("alerts").list(limit=1)
    if not page.members:
        pytest.skip("no alerts on box to round-trip")
    uuid = page.members[0]["uuid"]
    rec = client.records("alerts").get(uuid)
    assert rec["uuid"] == uuid


def test_records_iterate_respects_max(client):
    got = list(client.records("alerts").iterate(page_size=2, max_records=5))
    assert len(got) <= 5


def test_records_query_pagination_total_stable(client):
    alerts = client.records("alerts")
    p1 = alerts.query(Query().sort("createDate", "DESC").limit(2))
    p2 = alerts.query(Query().sort("createDate", "DESC").limit(5))
    # Total item count is query-independent; page size only changes member count.
    if p1.total is not None and p2.total is not None:
        assert p1.total == p2.total
    assert p2.count >= p1.count


# -- P4: safe-delete + recycle lifecycle ------------------------------------
def test_safe_delete_lifecycle(client):
    """Create a throwaway alert, delete it, and confirm the record is gone.

    Whether a delete is soft (recycle bin) or permanent is a per-module FSR
    setting. This test validates the always-true contract — delete removes the
    record from normal reads — and, *if* the module has a recycle bin, exercises
    the full ``show_deleted`` → ``restore`` → ``delete(hard=True)`` path.
    """
    from pyfsr.exceptions import ResourceNotFoundError

    alerts = client.records("alerts")
    uuid = alerts.create({"name": "pyfsr-p4-safe-delete-test"})["uuid"]
    recycled_ok = False
    try:
        alerts.delete(uuid)  # soft-delete
        with pytest.raises(ResourceNotFoundError):
            alerts.get(uuid)  # gone from normal reads either way

        try:
            recycled = alerts.get(uuid, show_deleted=True)
        except ResourceNotFoundError:
            # Module has no recycle bin on this box — delete was permanent.
            return
        recycled_ok = True
        assert recycled["uuid"] == uuid
        assert recycled.get("deletedAt")  # carries a deletion timestamp

        restored = alerts.restore(uuid)
        assert restored.get("deletedAt") in (None, "")
        assert alerts.get(uuid)["uuid"] == uuid  # live again
    finally:
        if recycled_ok:
            alerts.delete(uuid, hard=True)  # permanent cleanup
            with pytest.raises(ResourceNotFoundError):
                alerts.get(uuid, show_deleted=True)


def test_delete_rejects_blank_ref_live(client):
    """The single-row guard fires before any network call."""
    with pytest.raises(ValueError):
        client.records("alerts").delete("")
