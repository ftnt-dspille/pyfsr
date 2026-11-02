"""Length rules on module fields, and the roles module map going stale.

A ``maxlength`` other than the platform's "no limit" value (10485761, or -1, or
none) makes FortiSOAR add a *string* length rule. On a datetime, integer or
checkbox field that rule refuses every value with "This value should be of type
string", so the builders must never emit one and the write path must refuse it.
"""

import pytest

from pyfsr.api.modules_admin import ModulesAdminAPI
from pyfsr.api.roles import RolesAPI
from pyfsr.models import NO_LENGTH_LIMIT, AttributeMetadata, length_limit_problem

MA = ModulesAdminAPI


@pytest.mark.parametrize(
    "builder",
    ["datetime_field", "integer_field", "checkbox_field", "decimal_field", "json_field", "object_field", "text_field"],
)
def test_builders_default_to_no_length_limit(builder):
    f = MA._to_field_dict(getattr(MA, builder)("f"))
    assert f["validation"]["maxlength"] == NO_LENGTH_LIMIT
    assert length_limit_problem(f["type"], f["validation"]) is None


def test_a_real_limit_on_a_string_field_is_allowed():
    f = MA._to_field_dict(MA.text_field("f", maxlength=5))
    assert f["validation"]["maxlength"] == 5


@pytest.mark.parametrize("kw", [{"maxlength": 10485760}, {"maxlength": 100}, {"minlength": 1}])
def test_a_length_rule_on_a_datetime_is_refused(kw):
    with pytest.raises(ValueError, match="string length rule"):
        MA._to_field_dict(MA.datetime_field("seen", **kw))


def test_a_hand_built_dict_is_checked_too():
    raw = {
        "name": "seen",
        "type": "integer",
        "formType": "datetime",
        "validation": {"required": False, "minlength": 0, "maxlength": 10485760},
    }
    with pytest.raises(ValueError, match="seen"):
        MA._guard_field_shape(raw)


@pytest.mark.parametrize("maxlength", [None, -1, NO_LENGTH_LIMIT])
def test_platform_no_limit_values_pass(maxlength):
    assert length_limit_problem("integer", {"maxlength": maxlength}) is None


def test_model_reports_rather_than_raises():
    # Platform records are parsed with this model; a read must never fail.
    attr = AttributeMetadata.model_validate(
        {"name": "n", "type": "integer", "formType": "integer", "validation": {"maxlength": 2147483647}}
    )
    assert "string length rule" in attr.length_problem
    ok = AttributeMetadata.model_validate(
        {"name": "n", "type": "integer", "validation": {"maxlength": NO_LENGTH_LIMIT}}
    )
    assert ok.length_problem is None


class _Client:
    def __init__(self):
        self.modules = [{"type": "alerts", "@id": "/api/3/modules/a"}]
        self.fetches = 0

    def get(self, path, params=None):
        self.fetches += 1
        return {"hydra:member": list(self.modules)}


def test_module_published_after_the_first_lookup_is_found():
    client = _Client()
    roles = RolesAPI(client)
    assert roles._resolve_module_iri("alerts") == "/api/3/modules/a"
    client.modules.append({"type": "new_mod", "@id": "/api/3/modules/n"})
    assert roles._resolve_module_iri("new_mod") == "/api/3/modules/n"


def test_a_truly_absent_module_still_raises():
    roles = RolesAPI(_Client())
    with pytest.raises(ValueError, match="not found"):
        roles._resolve_module_iri("nope")


def test_create_refuses_while_a_delete_is_unpublished(monkeypatch):
    ma = ModulesAdminAPI(object())
    monkeypatch.setattr(ma, "get_staging", lambda m: None)
    monkeypatch.setattr(ma, "get_published", lambda m: {"uuid": "old"})
    with pytest.raises(ValueError, match="still published"):
        ma.create_module("probe_mod", fields=[ma.text_field("name")])
