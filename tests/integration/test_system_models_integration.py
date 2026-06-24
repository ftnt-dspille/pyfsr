"""Live integration tests for the stable system-entity models.

Requires a reachable FortiSOAR + examples/config.toml. Deselected by default;
run with: pytest -m integration
"""

import pytest

from pyfsr import RunSummary, SolutionPack, Workflow, WorkflowCollection

pytestmark = pytest.mark.integration


def test_records_workflows_returns_typed_model(client):
    page = client.records("workflows").list(limit=3)
    if not page.members:
        pytest.skip("no workflows on box")
    for rec in page.members:
        assert isinstance(rec, Workflow)
        assert rec.uuid
    # spot-check a typed field round-trips
    assert isinstance(page.members[0].isActive, (bool, type(None)))


def test_records_workflow_collections_returns_typed_model(client):
    page = client.records("workflow_collections").list(limit=3)
    if not page.members:
        pytest.skip("no workflow_collections on box")
    assert all(isinstance(r, WorkflowCollection) for r in page.members)


def test_playbook_runs_typed(client):
    runs = client.playbooks.execution_history(limit=3)
    if not runs:
        pytest.skip("no playbook runs on box")
    assert all(isinstance(r, RunSummary) for r in runs)
    assert runs[0].status is not None
    # dict-compatible access still works; full record preserved in extra
    assert runs[0]["status"] == runs[0].status


def test_content_hub_packs_typed(client):
    packs = client.content_hub.search_installed_packs(limit=3)
    if not packs:
        pytest.skip("no installed solution packs on box")
    assert all(isinstance(p, SolutionPack) for p in packs)
    assert packs[0].installed is True
