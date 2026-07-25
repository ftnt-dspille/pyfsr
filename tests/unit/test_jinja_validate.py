"""Unit tests for static Jinja validation of playbook step arguments."""

from pyfsr.jinja_validate import (
    FSR_JINJA_FILTERS,
    JinjaIssue,
    validate_jinja_expressions,
)


def _step(name, args):
    return {"name": name, "arguments": args}


def test_clean_expressions_produce_no_issues():
    steps = [
        _step("s1", {"ip": "{{ vars.input.records[0].ip }}"}),
        _step("s2", {"sev": '{{ "Severity" | picklist("High", "@id") }}'}),
        _step("s3", {"plain": "no jinja here"}),
    ]
    assert validate_jinja_expressions(steps) == []


def test_picklist_without_key_flagged_as_dict_return():
    steps = [_step("map sev", {"severity": '{{ "Severity" | picklist("High") }}'})]
    issues = validate_jinja_expressions(steps)
    kinds = [i.kind for i in issues]
    assert "picklist_returns_dict" in kinds
    issue = next(i for i in issues if i.kind == "picklist_returns_dict")
    assert issue.severity == "warning"
    assert issue.step == "map sev"
    assert issue.path == "arguments.severity"


def test_picklist_with_key_is_clean():
    steps = [_step("s", {"x": '{{ "P" | picklist("V", "uuid") }}'})]
    assert [i for i in validate_jinja_expressions(steps) if i.kind == "picklist_returns_dict"] == []


def test_picklist_embedded_in_larger_string_is_not_flagged():
    # Not a whole-value expression -> Jinja stringifies it; fine.
    steps = [_step("s", {"msg": 'sev is {{ "P" | picklist("V") }} today'})]
    assert [i for i in validate_jinja_expressions(steps) if i.kind == "picklist_returns_dict"] == []


def test_unknown_filter_flagged():
    steps = [_step("s", {"x": "{{ value | notarealfilter }}"})]
    issues = validate_jinja_expressions(steps)
    assert any(i.kind == "unknown_filter" and "notarealfilter" in i.message for i in issues)


def test_known_fsr_filter_not_flagged():
    steps = [_step("s", {"x": "{{ value | fromIRI }}"})]
    assert [i for i in validate_jinja_expressions(steps) if i.kind == "unknown_filter"] == []
    assert "picklist" in FSR_JINJA_FILTERS
    assert "getRelativeDate" in FSR_JINJA_FILTERS


def test_syntax_error_flagged_and_short_circuits_other_checks():
    # Unclosed brace: an error, and we should not also emit filter warnings for it.
    steps = [_step("s", {"x": "{{ value | notarealfilter "})]
    issues = validate_jinja_expressions(steps)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].kind == "syntax"


def test_nested_list_and_dict_paths_are_reported():
    steps = [_step("s", {"req": {"ips": ['{{ "P" | picklist("V") }}']}})]
    issues = validate_jinja_expressions(steps)
    assert issues[0].path == "arguments.req.ips[0]"


def test_custom_known_filters_override_catalog():
    steps = [_step("s", {"x": "{{ value | picklist }}"})]
    # With an empty catalog even picklist is "unknown".
    issues = validate_jinja_expressions(steps, known_filters=frozenset())
    assert any(i.kind == "unknown_filter" for i in issues)


def test_returns_typed_issue_models():
    steps = [_step("s", {"x": "{{ a | nope }}"})]
    issues = validate_jinja_expressions(steps)
    assert all(isinstance(i, JinjaIssue) for i in issues)
    assert issues[0].expression == "{{ a | nope }}"


# -- PlaybooksAPI.validate_jinja wrapper -------------------------------------
def test_playbooks_validate_jinja_wrapper(monkeypatch):
    from pyfsr.api.playbooks import PlaybooksAPI
    from pyfsr.models import Workflow

    api = PlaybooksAPI(object())
    wf = Workflow(
        **{
            "@id": "/api/3/workflows/abc",
            "name": "PB",
            "steps": [{"name": "map", "arguments": {"sev": '{{ "S" | picklist("High") }}'}}],
        }
    )
    monkeypatch.setattr(api, "_resolve_playbook_uuid", lambda p, op: "abc")
    monkeypatch.setattr(api, "get_definition", lambda uuid: wf)

    issues = api.validate_jinja("PB")
    assert any(i.kind == "picklist_returns_dict" for i in issues)
