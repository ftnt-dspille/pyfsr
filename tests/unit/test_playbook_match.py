"""Unit tests for the client-side playbook structural matcher."""

from pyfsr.playbook_match import (
    all_of,
    any_of,
    count,
    has,
    join_parent_child,
    none_of,
    parse_playbook,
    step,
    trigger,
)


def _wf(name, trigger_raw, steps, uuid="u-" + "x"):
    """Build a workflow-definition dict like /api/3/workflows returns."""
    full_steps = [{"name": "Start", "stepType": {"name": trigger_raw}, "arguments": {}}]
    for s in steps:
        full_steps.append(
            {
                "name": s.get("name", "step"),
                "stepType": {"name": s["type"]},
                "arguments": {k: v for k, v in s.items() if k not in ("name", "type")},
            }
        )
    return {"name": name, "uuid": uuid, "steps": full_steps}


def test_parse_playbook_extracts_trigger_and_steps():
    pb = parse_playbook(
        _wf(
            "PB",
            "cybersponse.action",
            [{"type": "Connectors", "connector": "fortigate-firewall", "operation": "block_ip"}],
        )
    )
    assert pb.name == "PB"
    assert pb.trigger_type == "manual"  # cybersponse.action -> friendly
    # the Start trigger step plus the one connector step
    conn = [s for s in pb.steps if s.step_type_raw == "Connectors"][0]
    assert conn.connector == "fortigate-firewall"
    assert conn.operation == "block_ip"


def test_step_same_step_precision():
    # connector and operation must be on the SAME step
    pb = parse_playbook(
        _wf(
            "split",
            "cybersponse.action",
            [
                {"type": "Connectors", "connector": "fortigate-firewall", "operation": "lookup_ip"},
                {"type": "Connectors", "connector": "carbonblack", "operation": "block_ip"},
            ],
        )
    )
    # fortigate + block_ip live in different steps -> no single step matches
    assert not has(step(connector="fortigate", operation="block_ip"))(pb)
    # but each holds on its own step
    assert has(step(connector="fortigate", operation="lookup_ip"))(pb)
    assert has(step(connector="carbonblack", operation="block_ip"))(pb)


def test_count_exact_min_max():
    pb = parse_playbook(
        _wf(
            "q",
            "cybersponse.action",
            [
                {"type": "SetVariable"},
                {"type": "SetVariable"},
                {"type": "CodeSnippet"},
            ],
        )
    )
    assert count(step(step_type="set_variable"), n=2)(pb)
    assert not count(step(step_type="set_variable"), n=3)(pb)
    assert count(step(step_type="set_variable"), min=2)(pb)
    assert count(step(step_type="code_snippet"), max=1)(pb)
    # the headline quantity query: 2 set-variable AND 1 code-snippet
    pred = all_of(count(step(step_type="set_variable"), n=2), count(step(step_type="code_snippet"), n=1))
    assert pred(pb)


def test_trigger_and_combinators():
    pb = parse_playbook(
        _wf("m", "cybersponse.action", [{"type": "Connectors", "connector": "fortigate", "operation": "block_ip"}])
    )
    assert trigger("manual")(pb)
    assert not trigger("on_create")(pb)
    assert all_of(trigger("manual"), has(step(operation="block_ip")))(pb)
    assert any_of(trigger("on_create"), has(step(operation="block_ip")))(pb)
    assert none_of(trigger("on_create"))(pb)
    assert not none_of(trigger("manual"))(pb)


def test_join_parent_child():
    child = _wf(
        "Block Child",
        "cybersponse.abstract_trigger",
        [{"type": "Connectors", "connector": "fortigate", "operation": "block_ip"}],
        uuid="c1",
    )
    # WorkflowReference targets the child by IRI in `workflowReference` (live shape)
    parent = _wf(
        "Manual Parent",
        "cybersponse.action",
        [{"type": "WorkflowReference", "workflowReference": "/api/3/workflows/c1"}],
        uuid="p1",
    )
    other = _wf(
        "Manual Other",
        "cybersponse.action",
        [{"type": "WorkflowReference", "workflowReference": "/api/3/workflows/zzz"}],
        uuid="p2",
    )
    corpus = [parse_playbook(parent), parse_playbook(child), parse_playbook(other)]

    matched = join_parent_child(corpus, trigger("manual"), has(step(operation="block_ip")))
    names = {p.name for p in matched}
    assert names == {"Manual Parent"}  # only the parent whose referenced child blocks


def test_count_requires_a_bound():
    import pytest

    with pytest.raises(ValueError):
        count(step(step_type="set_variable"))
