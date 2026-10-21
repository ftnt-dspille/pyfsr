"""Live tests for the 8.0.0 run-control / agent fixes (opt-in: pytest -m integration).

Every assertion here mirrors a live-verified finding from 8.0.0-6034:

- ``playbooks.retry()`` is a GET (the API guide's POST answers 405) and the
  response is a queue acknowledgement ``{task_id, result}``.
- ``playbooks.resume()`` works without an explicit ``user`` (the client
  resolves a people IRI; the route 500s without one).
- ``manual_input.pending_for_run(task_id, wait=)`` absorbs the run-registration
  race right after ``trigger()``.
- ``agents.heartbeat(agentId)`` answers a health-check request row.
- ``agents.upgrade_connector`` / ``uninstall_connector`` bodies pass the 8.0.0
  rpm-name validation (the old ``{name, version, agent_id}`` body 400s).

Everything the tests create is named ``live-pyfsr-it-...`` and deleted before
the test exits; the resume test drives a clone of the appliance's own
"E2E - Manual Input" template playbook, whose post-input branch is a single
SetVariable step.
"""

import uuid as _uuid

import pytest

from pyfsr.exceptions import APIError

pytestmark = pytest.mark.integration


def _find_playbook_uuid(client, name):
    rows = client.get("/api/3/workflows", params={"name": name, "$limit": 10}).get("hydra:member") or []
    return next((r.get("uuid") for r in rows if isinstance(r, dict) and r.get("name") == name), None)


def test_retry_is_a_get_and_answers_a_queue_ack(client):
    rows = client.playbooks.log_list(status="failed", limit=1).get("hydra:member") or []
    if not rows:
        pytest.skip("no failed run on this appliance to retry")
    pk = rows[0]["@id"].rstrip("/").rsplit("/", 1)[-1]
    out = client.playbooks.retry(pk)
    assert out.get("task_id"), out


def test_heartbeat_answers_health_check_row(client):
    rows = client.get("/api/3/agents", params={"$limit": 1}).get("hydra:member") or []
    if not rows:
        pytest.skip("no agent records on this appliance")
    agent_id = rows[0].get("agentId")
    if not agent_id:
        pytest.skip("agent record carries no agentId")
    row = client.agents.heartbeat(agent_id)
    assert row.get("action") == "health-check"
    assert row.get("agent") == agent_id


def test_connector_write_bodies_pass_rpm_validation(client):
    """The rpm-name validation must not 400 anymore.

    Against a disconnected agent the appliance proxies and 500s (documented);
    the regression this guards is the old ``{name, version, agent_id}`` body
    answering 400 ``No connector rpm name ... was provided``.
    """
    rows = client.get("/api/3/agents", params={"$limit": 50}).get("hydra:member") or []
    if not rows:
        pytest.skip("no agent records on this appliance")
    agent_id = rows[0].get("agentId")
    if not agent_id:
        pytest.skip("agent record carries no agentId")
    cat = client.get("/api/integration/connectors/", params={"name": "cyops_utilities", "format": "json"})
    version = ((cat or {}).get("data") or [{}])[0].get("version")
    if not version:
        pytest.skip("cyops_utilities not in the connector catalog")
    for fn in (client.agents.upgrade_connector, client.agents.uninstall_connector):
        try:
            fn(agent_id, name="cyops_utilities", version=version)
        except APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            assert status != 400, (
                "install-connector write rejected for a missing rpm name -- the 8.0.0 body shape regressed"
            )


def test_resume_lifecycle_on_own_clone(client):
    """Clone -> trigger -> pending_for_run(wait=) -> resume WITHOUT user -> delete."""
    src_uuid = _find_playbook_uuid(client, "E2E - Manual Input")
    if not src_uuid:
        pytest.skip("E2E - Manual Input template playbook not on this appliance")
    clone = client.playbooks.clone(src_uuid, f"live-pyfsr-it-{_uuid.uuid4().hex[:8]}", is_active=True)
    clone_uuid = clone.get("uuid")
    try:
        trig = client.playbooks.trigger(clone_uuid)
        task_id = trig.task_ids[0] if isinstance(trig.task_ids, list) else trig.task_id
        pending = client.manual_input.pending_for_run(task_id, wait=25)
        assert pending, "no pending manual input after wait=25s"
        row = pending[0]
        opt = row.response_mapping["options"][0]
        out = client.playbooks.resume(
            str(row.workflow), manual_input_id=row.id, step_iri=opt["step_iri"], step_id=str(row.step_id)
        )
        assert "resumed successfully" in (out.get("message") or "").lower(), out
    finally:
        if clone_uuid:
            client.delete(f"/api/3/workflows/{clone_uuid}")
