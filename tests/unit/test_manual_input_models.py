"""Typed-model tests for a retrieved Manual Input prompt.

Both fixtures are real ``retrieve_wfinput`` responses captured live (8.0 box):

* ``retrieve_multi.json`` -- a friendly multi-field prompt (textarea + select +
  text), one field required: exercises the full typed form.
* ``retrieve_button.json`` -- a button-only / DecisionBased prompt: empty
  ``inputVariables`` with a single ``Continue`` option.

The models must (1) type the form schema + response options, (2) stay
dict-compatible (``mi.input["schema"]`` etc.), and (3) round-trip losslessly
(internal keys like ``_expanded`` ride through ``extra="allow"``).
"""

import json
from pathlib import Path

import pytest

from pyfsr.models import (
    ManualInput,
    ManualInputForm,
    ManualInputOption,
    ManualInputSchema,
    ManualInputVariable,
    ResponseMapping,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "manual_input"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_multi_field_form_is_typed():
    mi = ManualInput.model_validate(_load("retrieve_multi.json"))
    assert isinstance(mi.input, ManualInputForm)
    schema = mi.input.schema_
    assert isinstance(schema, ManualInputSchema)
    assert schema.title == "E2E multi gate"
    ivars = schema.inputVariables
    assert ivars and all(isinstance(v, ManualInputVariable) for v in ivars)
    by_name = {v.name: v for v in ivars}
    assert set(by_name) == {"comment", "severity", "ticket_id"}
    # required flag + widget typing survive
    assert by_name["comment"].required is True
    assert by_name["comment"].formType == "textarea"
    assert by_name["severity"].required is True
    assert by_name["severity"].formType == "dynamicList"
    assert by_name["severity"].options == ["Low", "Medium", "High"]
    assert by_name["ticket_id"].required is False


def test_response_mapping_is_typed():
    mi = ManualInput.model_validate(_load("retrieve_multi.json"))
    assert isinstance(mi.response_mapping, ResponseMapping)
    opts = mi.response_mapping.options
    assert opts and isinstance(opts[0], ManualInputOption)
    assert opts[0].option == "Continue"
    assert opts[0].step_iri and opts[0].step_iri.startswith("/api/3/workflow_steps/")


def test_button_only_prompt_has_empty_form():
    mi = ManualInput.model_validate(_load("retrieve_button.json"))
    schema = mi.input.schema_
    # Button-only: a real, present schema with NO collected fields.
    assert schema.title == "E2E test gate"
    assert schema.inputVariables == []
    opt = mi.response_mapping.options[0]
    assert opt.option == "Continue"
    assert opt.primary is True


def test_dict_access_still_works():
    # The reserved `schema` name is reachable by its wire key via subscripting.
    mi = ManualInput.model_validate(_load("retrieve_multi.json"))
    assert mi["input"]["schema"]["title"] == "E2E multi gate"
    assert mi["input"]["schema"]["inputVariables"][0]["name"] == "comment"
    assert mi["response_mapping"]["options"][0]["option"] == "Continue"


def test_roundtrip_preserves_internal_keys():
    raw = _load("retrieve_multi.json")
    mi = ManualInput.model_validate(raw)
    dumped = json.dumps(mi.model_dump(by_alias=True), default=str)
    # internal editor keys ride through extra="allow"
    assert "_expanded" in dumped
    assert "_previousName" in dumped


@pytest.mark.parametrize("fixture", ["retrieve_multi.json", "retrieve_button.json"])
def test_both_scenarios_parse_without_loss(fixture):
    raw = _load(fixture)
    mi = ManualInput.model_validate(raw)
    # every top-level wire key remains reachable
    for key in raw:
        assert key in mi, f"{key!r} dropped from typed ManualInput"
