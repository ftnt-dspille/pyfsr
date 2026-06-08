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
