"""Unit tests for the client-side playbook structural matcher."""

from pyfsr.playbook_match import (
    all_of,
    any_of,
    count,
    has,
    join_parent_child,
    manual_on,
    none_of,
    parse_playbook,
    step,
    trigger,
    trigger_label,
    trigger_resources,
    trigger_step,
)


def _wf(name, trigger_raw, steps, uuid="u-" + "x", trigger_args=None):
    """Build a workflow-definition dict like /api/3/workflows returns."""
    full_steps = [{"name": "Start", "stepType": {"name": trigger_raw}, "arguments": trigger_args or {}}]
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


# ----------------------------------------------------------- trigger metadata
def test_trigger_step_extracts_start_step():
    pb = parse_playbook(_wf("PB", "cybersponse.action", [], trigger_args={"resources": ["alerts"], "title": "Run Me"}))
    ts = trigger_step(pb)
    assert ts is not None
    assert ts.step_type_raw == "cybersponse.action"
    assert ts.arguments["resources"] == ["alerts"]
    assert ts.arguments["title"] == "Run Me"


def test_trigger_step_none_when_no_cybersponse_step():
    # A definition fetched without relationships has no stepType dicts.
    pb = parse_playbook({"name": "x", "uuid": "u", "steps": []})
    assert trigger_step(pb) is None


def test_trigger_label_uses_title_then_name():
    titled = parse_playbook(
        _wf("Get IP Reputation", "cybersponse.action", [], trigger_args={"title": "VirusTotal: Get IP Reputation"})
    )
    assert trigger_label(titled) == "VirusTotal: Get IP Reputation"
    # No title -> falls back to the playbook name (live shape: "Get Industry List").
    untitled = parse_playbook(_wf("Get Industry List", "cybersponse.action", []))
    assert trigger_label(untitled) == "Get Industry List"


def test_trigger_resources_returns_module_slugs():
    pb = parse_playbook(_wf("x", "cybersponse.action", [], trigger_args={"resources": ["alerts", "incidents"]}))
    assert trigger_resources(pb) == ["alerts", "incidents"]
    # no resources key -> []
    assert trigger_resources(parse_playbook(_wf("y", "cybersponse.action", []))) == []
    # non-string entries filtered out
    pb2 = parse_playbook(_wf("z", "cybersponse.action", [], trigger_args={"resources": ["alerts", 7]}))
    assert trigger_resources(pb2) == ["alerts"]


def test_manual_on_matches_module_case_insensitively():
    pb = parse_playbook(_wf("PB", "cybersponse.action", [], trigger_args={"resources": ["alerts"]}))
    assert manual_on("alerts")(pb)
    assert manual_on("Alerts")(pb)  # case-insensitive
    assert not manual_on("incidents")(pb)


def test_manual_on_rejects_non_manual_trigger():
    # An on_create trigger tied to alerts is NOT a manual playbook.
    pb = parse_playbook(_wf("PB", "cybersponse.post_create", [], trigger_args={"resources": ["alerts"]}))
    assert not manual_on("alerts")(pb)


def test_manual_on_rejects_manual_without_resources():
    # A manual playbook started only from the playbook page has no resources.
    pb = parse_playbook(_wf("PB", "cybersponse.action", []))
    assert not manual_on("alerts")(pb)
