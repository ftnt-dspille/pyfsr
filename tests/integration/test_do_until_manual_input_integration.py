"""Live e2e for the do-until / manual-input validation loop (plan F1 + F2).

Closes two follow-ups from PLAYBOOK_AUTHORING_DX_PLAN.md against a real box:

* **F1** — runtime ``set_variable`` / jinja values are persisted into the
  retrievable run record only when *global workflow debug logging* is enabled.
  This test turns it on, then asserts the parent's final step ``result`` IS
  populated (the env-readability claim corrected in ``run_env``/``step_status``).
* **F2** — synchronous (``apply_async: false``) ``workflow_reference`` children
  are ``parent_wf``-linked, so ``child_runs(parent_pk)`` returns exactly the
  loop turns (asserted == wrong + 1).

Deploys ``examples/playbooks/do_until_validation_demo.yaml``, drives the prompt
with the shipped ``client.manual_input.answer`` helper, and self-cleans (restores
the prior debug setting and deletes the collection).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

YAML_PATH = Path(__file__).resolve().parents[2] / "examples" / "playbooks" / "do_until_validation_demo.yaml"
PARENT_NAME = "Loop Until Six Digits"
CHILD_NAME = "Validate Six Digit Number"
MI_STEP = "AskNumber"  # a pending input's .title is the STEP name
WRONG = ["123"]  # one failing answer (not 6 digits)
RIGHT = "654321"  # the valid 6-digit answer


def _pending_ids(client, handled: set[int]) -> object | None:
    """Return a demo manual input we have not handled yet (newest first)."""
    for mi in client.manual_input.list(assigned_to="all"):
        if (mi.title or "") == MI_STEP and mi.id not in handled:
            return mi
    return None


def _wait_for_new_input(client, handled: set[int], timeout: float = 90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        mi = _pending_ids(client, handled)
        if mi is not None:
            return mi
        time.sleep(2)
    return None


def _newest_top_level_parent_pk(client) -> str | None:
    resp = client.get(
        "/api/wf/api/workflows/",
        params={"limit": 50, "ordering": "-id", "format": "json"},
    )
    for m in resp.get("hydra:member") or resp.get("results") or []:
        if m.get("name") == PARENT_NAME and not m.get("parent_wf"):
            pk = (m.get("@id") or "").rstrip("/").rsplit("/", 1)[-1]
            if pk.isdigit():
                return pk
    return None


@pytest.fixture()
def deployed_demo(client):
    """Deploy the collection + enable global debug; restore + delete on teardown."""
    prior_debug = client.system_settings.get_public_values().get("workflow_log_config") or {}
    client.system_settings.set_playbook_debug_logging(enabled=True, allow_playbook_override=False)
    created = client.workflow_collections.import_from_yaml(str(YAML_PATH), replace=True)
    coll_uuid = created[0]["uuid"]
    try:
        yield coll_uuid
    finally:
        client.workflow_collections.delete(coll_uuid)
        client.system_settings.set_playbook_debug_logging(
            enabled=bool(prior_debug.get("debug")),
            allow_playbook_override=bool(prior_debug.get("allow_pb_to_override")),
        )


def test_do_until_loop_env_readable_with_debug(client, deployed_demo):
    handled = {mi.id for mi in client.manual_input.list(assigned_to="all") if (mi.title or "") == MI_STEP}

    resp = client.playbooks.trigger(PARENT_NAME)
    task_id = resp.task_id if hasattr(resp, "task_id") else resp["task_id"]

    # one wrong answer (loop should re-prompt), then the valid one (loop exits)
    for value in [*WRONG, RIGHT]:
        mi = _wait_for_new_input(client, handled)
        assert mi is not None, f"no manual input appeared (answering {value!r})"
        handled.add(mi.id)
        # Pass inputs= explicitly: on this box retrieve() doesn't echo the
        # prompt's inputVariables, so the scalar auto-map can't resolve the
        # single var. The keyed form is unambiguous either way.
        client.manual_input.answer(inputs={"my_number": int(value)}, input_id=mi.id)

    run = client.playbooks.wait(task_id, timeout=120)
    assert run.get("status") == "finished"
    time.sleep(3)  # let the final parent run + step detail settle

    parent_pk = _newest_top_level_parent_pk(client)
    assert parent_pk is not None, "could not locate the top-level parent run"

    # F1: with debug on, the final step's result IS populated (env readable).
    parent = client.playbooks.get_execution(parent_pk, step_detail=True)
    steps = {s.get("name"): s for s in (parent.get("steps") or [])}
    stamp = steps.get("StampResult") or {}
    assert stamp.get("status") == "finished"
    result = stamp.get("result") or {}
    assert result, "StampResult.result empty — debug logging did not persist env"
    assert result.get("final_valid") is True
    assert result.get("final_number") == int(RIGHT)

    # F2: sync workflow_reference children are parent_wf-linked -> one per turn.
    children = client.playbooks.child_runs(parent_pk)
    assert len(children) == len(WRONG) + 1
    assert all(c.get("name") == CHILD_NAME for c in children)
