"""Unit tests for the step-type catalog (pyfsr playbook steps / step-help).

These require the ``pyfsr[playbooks]`` extra (the fsr_playbooks reference DB);
skipped otherwise.
"""

import pytest

pytest.importorskip("fsr_playbooks")

from pyfsr.playbook_catalog import list_step_types, step_help  # noqa: E402


def test_list_step_types_covers_core_keywords():
    infos = list_step_types()
    shorts = {i.short for i in infos}
    # the keywords the do-until authoring flow needs
    assert {"set_variable", "decision", "manual_input", "workflow_reference"} <= shorts
    # sorted, and every entry carries a canonical name + purpose
    assert shorts == set(sorted(shorts))
    assert all(i.canonical and i.purpose for i in infos)


def test_modeled_flag_set_for_typed_types():
    by_short = {i.short: i for i in list_step_types()}
    assert by_short["set_variable"].modeled is True
    assert by_short["decision"].modeled is True


def test_step_help_resolves_friendly_and_canonical():
    a = step_help("set_variable")
    b = step_help("SetVariable")
    assert a.canonical == b.canonical == "SetVariable"
    assert a.short == b.short == "set_variable"


def test_step_help_has_example_and_schema_for_modeled():
    h = step_help("decision")
    assert h.example_yaml and "type: decision" in h.example_yaml
    assert h.modeled and h.arg_schema is not None


def test_step_help_uses_curated_example_for_manual_input():
    h = step_help("manual_input")
    # curated friendly form, not the verbose decompiled dynamicList wire shape
    assert "inputs:" in (h.example_yaml or "")
    assert "dynamicList" not in (h.example_yaml or "")
    assert "manual_input.answer" in (h.example_yaml or "")


def test_step_help_unknown_suggests_near_matches():
    with pytest.raises(KeyError) as exc:
        step_help("set_var")
    assert "set_variable" in str(exc.value)
