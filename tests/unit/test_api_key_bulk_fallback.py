"""An API-key client transparently services the bulk routes via single-record
routes, while a JWT client still hits the real bulk endpoint.

The ``/api/3/{bulkupsert,insert,update,delete}/*`` family rejects API-key auth
(product bug -- see troubleshooting/BUG-bulkupsert-api-key-invalid-credentials.md).
Rather than fail, the client decomposes the batch into the single-record routes
the key *can* use (``POST /api/3/upsert/<module>``, ``POST /api/3/<module>``,
``PUT``/``DELETE /api/3/<module>/<uuid>``) and rebuilds the same
``{"success": [...], "failure": [...]}`` envelope. A JWT client never enters that
branch. These tests pin both halves of the contract -- single AND bulk must work.
"""

from __future__ import annotations

import pytest

from pyfsr._testing import demo_client, demo_client_jwt
from pyfsr.exceptions import UnsupportedAuthOperationError
from pyfsr.records import BulkUpsertFailure


def _spy_endpoints(client) -> list[tuple[str, str]]:
    """Record every (METHOD, url) that actually reaches the wire (the session).

    Spying at the session boundary -- not ``client.request`` -- is deliberate:
    the API-key fallback intercepts a bulk call inside ``client.request`` and
    never sends it, so only the single-record requests it fans out to reach the
    session. That is exactly the "what went on the wire?" question these tests ask.
    """
    calls: list[tuple[str, str]] = []
    real = client.session.request

    def recording(method, url, *args, **kwargs):
        calls.append((method.upper(), url))
        return real(method, url, *args, **kwargs)

    client.session.request = recording  # type: ignore[method-assign]
    return calls


ROWS = [{"name": "fallback-row-a"}, {"name": "fallback-row-b"}]


def test_api_key_bulk_upsert_falls_back_to_single_upsert_routes():
    client = demo_client()  # API-key replay client
    calls = _spy_endpoints(client)

    result = client.records("alerts").bulk_upsert(ROWS)  # must NOT raise

    hit = [ep for _, ep in calls]
    assert not any("/bulkupsert/" in ep for ep in hit), "API key must not touch the bulk route"
    assert sum(ep.endswith("/api/3/upsert/alerts") for ep in hit) == len(ROWS), (
        "each row should become a single-record natural-key upsert"
    )
    assert isinstance(result, dict) and "success" in result
    assert len(result["success"]) == len(ROWS)
    assert result.get("failure") == []


def test_api_key_bulk_upsert_parse_true_reports_ok():
    client = demo_client()
    parsed = client.records("alerts").bulk_upsert(ROWS, parse=True)
    assert parsed.ok
    assert len(parsed.succeeded) == len(ROWS)
    assert parsed.failed == []


def test_jwt_bulk_upsert_still_uses_the_real_bulk_route():
    client = demo_client_jwt()
    calls = _spy_endpoints(client)

    client.records("alerts").bulk_upsert(ROWS)

    hit = [ep for _, ep in calls]
    assert any(ep.endswith("/api/3/bulkupsert/alerts") for ep in hit), "JWT must hit the bulk route"
    assert not any(ep.endswith("/api/3/upsert/alerts") for ep in hit), "JWT must not be decomposed"


def test_api_key_bulk_insert_falls_back_to_single_create():
    client = demo_client()
    calls = _spy_endpoints(client)

    client.records("alerts").bulk_insert([{"name": "insert-row"}])

    hit = [ep for _, ep in calls]
    assert not any("/insert/" in ep for ep in hit), "API key must not touch the bulk insert route"
    assert any(ep.endswith("/api/3/alerts") for m, ep in calls if m == "POST"), (
        "a pure bulk insert row becomes a single POST /api/3/<module>"
    )


def test_api_key_create_playbooks_does_not_hit_bulk_workflows():
    client = demo_client()
    calls = _spy_endpoints(client)

    # upsert_playbooks (the single-record path) may lack a replay fixture and
    # raise a bare RuntimeError; what matters here is that it got PAST the
    # API-key guard and never posted to the create-only bulk workflows route.
    try:
        client.playbooks.create_playbooks([{"name": "pb", "uuid": "00000000-0000-0000-0000-000000000001"}])
    except UnsupportedAuthOperationError:
        pytest.fail("create_playbooks must no longer refuse an API-key client")
    except Exception:
        pass

    assert not any(ep.endswith("/api/3/bulkupsert/workflows") for _, ep in calls), (
        "workflows must route through the single-record playbook path, not the create-only bulk route"
    )


def test_bulk_failure_line_round_trips_through_from_raw():
    # One bad row must fail exactly that row, in the index-bearing shape
    # BulkUpsertFailure.from_raw parses -- so partial-failure handling survives.
    client = demo_client()
    line = client._bulk_failure_line(3, ValueError("picklist 'severity' value rejected"))
    parsed = BulkUpsertFailure.from_raw(line)
    assert parsed.index == 3
    assert "severity" in parsed.message
