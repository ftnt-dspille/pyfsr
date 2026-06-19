"""Live integration tests for ConnectorsAPI + PlaybooksAPI (P5).

Read-only: lists configured connectors, healthchecks one, and reads playbook
run history. Connector *execution* has side effects, so it's covered by unit
tests rather than exercised live here.
"""

import pytest

pytestmark = pytest.mark.integration


def test_list_configured_connectors(client):
    configured = client.connectors.list_configured()
    assert isinstance(configured, list) and configured
    sample = configured[0]
    assert "name" in sample and "version" in sample and "configurations" in sample


def test_healthcheck_a_configured_connector(client):
    configured = client.connectors.list_configured()
    hit = next((c for c in configured if c["configurations"]), None)
    if hit is None:
        pytest.skip("no connector with a configuration on this box")
    res = client.connectors.healthcheck(hit["name"])
    assert "status" in res  # Available / Disconnected / no-config
    assert client.connectors.resolve_config(hit["name"])  # default config resolves


def test_playbook_runs_and_get(client):
    runs = client.playbooks.execution_history(limit=5)
    assert isinstance(runs, list)
    if not runs:
        pytest.skip("no playbook runs on this box")
    first = runs[0]
    assert {"task_id", "name", "status", "pk", "source"} <= set(first)
    fetched = client.playbooks.get_execution(first["pk"])
    assert fetched["pk"] == first["pk"]
