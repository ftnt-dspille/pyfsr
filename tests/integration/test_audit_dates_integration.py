"""Audit window filters, in every form a caller sends (opt-in: pytest -m integration).

Live: an epoch passed as a string used to reach the API as no filter at all, so a
"since" query returned the whole history. Read-only.
"""

import pytest

pytestmark = pytest.mark.integration
FUTURE = ["2100-01-01", "2100-01-01T00:00:00Z", "4102444800", 4102444800]


@pytest.fixture(scope="module")
def busy_record(client):
    for alert in client.get("/api/3/alerts", params={"$limit": 20, "$orderby": "-modifyDate"})["hydra:member"]:
        if client.audit.activities(entity_uuid=alert["uuid"], limit=5).get("content"):
            return alert["uuid"]
    pytest.skip("no alert with audit events")


@pytest.mark.parametrize("start", FUTURE)
def test_a_future_start_matches_nothing(client, busy_record, start):
    assert client.audit.activities(entity_uuid=busy_record, limit=5, start_date=start)["content"] == []
