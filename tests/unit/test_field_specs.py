"""Simple field specs -> field metadata (live proof: test_fields_from_specs_integration.py)."""

import pytest

from pyfsr.api.modules_admin import ModulesAdminAPI

MA = ModulesAdminAPI


@pytest.mark.parametrize(
    ("spec_type", "form", "storage"),
    [
        ("string", "text", "string"),
        ("int", "integer", "integer"),
        ("bool", "checkbox", "boolean"),
        ("date", "datetime", "integer"),
        ("datetime", "datetime", "integer"),
        ("email", "email", "string"),
    ],
)
def test_aliases_map_to_display_types(spec_type, form, storage):
    f = MA.field_from_spec({"name": "f", "type": spec_type})
    assert (f["formType"], f["type"]) == (form, storage)


def test_options_pass_through():
    f = MA.field_from_spec({"name": "tag", "type": "text", "required": True, "label": "Tag"})
    assert f["validation"]["required"] is True and f["descriptions"]["singular"] == "Tag"


def test_unknown_type_is_refused_with_the_valid_list():
    with pytest.raises(ValueError, match="unknown type 'widget'.*datetime"):
        MA.field_from_spec({"name": "f", "type": "widget"})


@pytest.mark.parametrize("picklist", [None, ["Open", "Closed"]])
def test_picklist_without_a_list_name_is_refused(picklist):
    # It used to default to "Severity", or take a list of values as the list name.
    with pytest.raises(ValueError, match="picklist name"):
        MA.field_from_spec({"name": "status", "type": "picklist", "picklist": picklist})


class _Picklists:
    def __init__(self, existing=None):
        self.existing = existing or {}
        self.created, self.added = [], []

    def get_or_create_picklist(self, name, options=None):
        class PL:
            iri, uuid = f"/api/3/picklist_names/{name}", name

        if name in self.existing:
            return PL(), False
        self.created.append((name, options))
        return PL(), True

    def values(self, name):
        return [{"itemValue": v} for v in self.existing.get(name, [])]

    def add_option(self, ref, value):
        self.added.append(value)


class _Client:
    def __init__(self, picklists):
        self.picklists = picklists


def test_values_create_a_named_picklist_and_bind_the_field():
    pl = _Picklists()
    ma = ModulesAdminAPI(_Client(pl))
    [f] = ma.fields_from_specs("lost_devices", [{"name": "status", "type": "picklist", "picklist": ["A", "B"]}])
    assert pl.created == [("lost_devices status", ["A", "B"])]
    assert f["formType"] == "picklist" and "lost_devices status" in str(f["dataSource"])


def test_an_existing_picklist_gets_only_the_missing_values():
    pl = _Picklists({"m status": ["A"]})
    spec = {"name": "status", "type": "picklist", "picklist": ["A", "B"]}
    ModulesAdminAPI(_Client(pl)).fields_from_specs("m", [spec])
    assert pl.created == [] and pl.added == ["B"]


def test_a_bad_spec_creates_no_picklist():
    pl = _Picklists()
    with pytest.raises(ValueError):
        ModulesAdminAPI(_Client(pl)).fields_from_specs(
            "m",
            [
                {"name": "status", "type": "picklist", "picklist": ["A"]},
                {"name": "x", "type": "widget"},
            ],
        )
    assert pl.created == []


def test_a_list_as_the_picklist_name_is_refused_before_staging():
    # Live: this field stopped every publish on the box until the module was removed.
    raw = {
        "name": "status",
        "type": "picklists",
        "formType": "picklist",
        "dataSource": {
            "model": "picklists",
            "query": {"filters": [{"field": "listName__name", "operator": "eq", "value": ["Open", "Closed"]}]},
        },
    }
    with pytest.raises(ValueError, match="picklist name must be a string"):
        MA._guard_field_shape(raw)


def test_a_spec_without_a_type_is_text():
    assert MA.field_from_spec({"name": "name"})["formType"] == "text"


def test_attribute_metadata_reads_its_picklist_name():
    from pyfsr.models import AttributeMetadata

    f = MA._to_field_dict(MA.picklist_field("status", "AlertStatus"))
    assert AttributeMetadata.model_validate(f).picklist_name == "AlertStatus"
    assert AttributeMetadata.model_validate(MA.field_from_spec({"name": "t"})).picklist_name is None
