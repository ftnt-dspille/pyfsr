"""Unit tests for module/schema discovery."""

from pyfsr.api.modules import ModulesAPI

_META = {
    "hydra:member": [
        {
            "type": "incidents",
            "module": "incidents",
            "displayName": "Incidents",
            "attributes": [
                {"name": "name", "title": "Name", "type": "text", "validation": {"required": True}},
                {
                    "name": "severity",
                    "title": "Severity",
                    "type": "picklist",
                    "dataSource": {
                        "query": {"filters": [{"field": "listName__name", "value": "Severity"}]}
                    },
                },
            ],
        },
        {"type": "alerts", "module": "alerts", "displayName": "Alerts", "attributes": []},
    ]
}


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, endpoint, params=None, **kwargs):
        self.calls.append(endpoint)
        return _META


def test_list_modules_sorted_and_shaped():
    api = ModulesAPI(FakeClient())
    mods = api.list()
    assert [m["type"] for m in mods] == ["alerts", "incidents"]  # sorted
    assert mods[0] == {"type": "alerts", "label": "Alerts", "plural": "alerts"}


def test_list_modules_cached():
    client = FakeClient()
    api = ModulesAPI(client)
    api.list()
    api.list()
    assert len(client.calls) == 1  # cached after first fetch


def test_describe_module_fields():
    api = ModulesAPI(FakeClient())
    out = api.describe("incidents")
    assert out["module"] == "incidents"
    assert out["field_count"] == 2
    name_field = next(f for f in out["fields"] if f["name"] == "name")
    assert name_field["required"] is True
    sev_field = next(f for f in out["fields"] if f["name"] == "severity")
    assert sev_field["picklist_name"] == "Severity"


def test_describe_module_not_found_lists_available():
    out = ModulesAPI(FakeClient()).describe("nope")
    assert "error" in out
    assert out["available"] == ["alerts", "incidents"]


def test_clear_cache_refetches():
    client = FakeClient()
    api = ModulesAPI(client)
    api.list()
    api.clear_cache()
    api.list()
    assert len(client.calls) == 2


def test_search_modules_by_substring():
    api = ModulesAPI(FakeClient())
    assert [m["type"] for m in api.search("incid")] == ["incidents"]
    assert api.search("zzz") == []


def test_fields_shortcut():
    api = ModulesAPI(FakeClient())
    names = {f["name"] for f in api.fields("incidents")}
    assert names == {"name", "severity"}


def test_find_field_by_name_across_modules():
    api = ModulesAPI(FakeClient())
    hits = api.find_field(name="sever")
    assert [(h["module"], h["field"]["name"]) for h in hits] == [("incidents", "severity")]


def test_find_field_by_type():
    api = ModulesAPI(FakeClient())
    hits = api.find_field(type="picklist")
    assert all(h["field"]["type"] == "picklist" for h in hits)
    assert ("incidents", "severity") in [(h["module"], h["field"]["name"]) for h in hits]


def test_format_module_is_readable():
    out = ModulesAPI(FakeClient()).format_module("incidents")
    assert "Module: Incidents" in out
    assert "severity" in out and "picklist" in out


_TEMPLATED = {
    "hydra:member": [
        {
            "type": "widgets",
            "module": "widgets",
            "displayName": "{{ name }}",
            "descriptions": {"singular": "Widget"},
            "attributes": [],
        }
    ]
}


def test_friendly_label_skips_jinja_template():
    class C:
        def get(self, endpoint, params=None, **kwargs):
            return _TEMPLATED

    mods = ModulesAPI(C()).list()
    assert mods[0]["label"] == "Widget"  # not the "{{ name }}" template
