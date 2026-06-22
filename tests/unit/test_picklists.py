"""Unit tests for the PicklistsAPI (live discovery + value -> IRI resolution)."""

from pyfsr.api.picklists import PicklistsAPI
from pyfsr.records import RecordSet

# --- canned server payloads ------------------------------------------------

_NAMES = {
    "hydra:member": [
        {"name": "Severity"},
        {"name": "AlertStatus"},
    ]
}

_SEVERITY_ITEMS = {
    "hydra:member": [
        {"itemValue": "High", "uuid": "sev-high", "ordinal": 1},
        {"itemValue": "Low", "uuid": "sev-low", "ordinal": 2},
    ]
}

_STATUS_ITEMS = {
    "hydra:member": [
        {"itemValue": "Open", "uuid": "st-open", "ordinal": 1},
    ]
}

# staging_model_metadatas?$relationships=true shape
_META = {
    "hydra:member": [
        {
            "type": "alerts",
            "module": "alerts",
            "attributes": [
                {"name": "name"},
                {
                    "name": "severity",
                    "dataSource": {"query": {"filters": [{"field": "listName__name", "value": "Severity"}]}},
                },
                {
                    "name": "status",
                    "dataSource": {"query": {"filters": [{"field": "listName__name", "value": "AlertStatus"}]}},
                },
            ],
        }
    ]
}


class FakeClient:
    """Routes get() by endpoint substring; counts calls per endpoint."""

    def __init__(self):
        self.get_calls = []
        self.post_calls = []
        self.put_calls = []
        self.picklists = None  # set after construction for RecordSet tests

    def get(self, endpoint, params=None, **kwargs):
        self.get_calls.append((endpoint, params))
        if endpoint.startswith("/api/3/picklist_names"):
            return _NAMES
        if endpoint.startswith("/api/3/staging_model_metadatas"):
            return _META
        if "listName.name=Severity" in endpoint:
            return _SEVERITY_ITEMS
        if "listName.name=AlertStatus" in endpoint:
            return _STATUS_ITEMS
        return {"hydra:member": []}

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data))
        return data or {}

    def put(self, endpoint, data=None, **kwargs):
        self.put_calls.append((endpoint, data))
        return data or {}


def _api():
    client = FakeClient()
    api = PicklistsAPI(client)
    client.picklists = api
    return api, client


# --- names / values --------------------------------------------------------
def test_list_names_sorted_and_cached():
    api, client = _api()
    assert api.list() == ["AlertStatus", "Severity"]
    api.list()  # second call hits cache
    name_calls = [c for c in client.get_calls if c[0].startswith("/api/3/picklist_names")]
    assert len(name_calls) == 1


def test_values_shape_and_iri():
    api, _ = _api()
    items = api.values("Severity")
    assert items[0]["itemValue"] == "High"
    assert items[0]["iri"] == "/api/3/picklists/sev-high"
    assert items[0]["ordinal"] == 1


def test_values_cached():
    api, client = _api()
    api.values("Severity")
    api.values("Severity")
    pl_calls = [c for c in client.get_calls if "listName.name=Severity" in c[0]]
    assert len(pl_calls) == 1


# --- (module, field) discovery --------------------------------------------
def test_for_field_from_metadata():
    api, _ = _api()
    assert api.for_field("alerts", "severity") == "Severity"
    assert api.for_field("alerts", "status") == "AlertStatus"
    assert api.for_field("alerts", "name") is None  # not picklist-backed
    assert api.for_field("alerts", "nonexistent") is None


def test_field_map_cached():
    api, client = _api()
    api.for_field("alerts", "severity")
    api.for_field("alerts", "status")
    meta_calls = [c for c in client.get_calls if c[0].startswith("/api/3/staging_model_metadatas")]
    assert len(meta_calls) == 1


# --- resolve ---------------------------------------------------------------
def test_resolve_by_explicit_picklist():
    api, _ = _api()
    assert api.resolve("High", picklist="Severity") == "/api/3/picklists/sev-high"


def test_resolve_case_insensitive():
    api, _ = _api()
    assert api.resolve("high", picklist="Severity") == "/api/3/picklists/sev-high"


def test_resolve_iri_passthrough():
    api, client = _api()
    assert api.resolve("/api/3/picklists/x") == "/api/3/picklists/x"
    assert client.get_calls == []  # no lookup needed


def test_resolve_by_module_field():
    api, _ = _api()
    assert api.resolve("Open", module="alerts", field="status") == "/api/3/picklists/st-open"


def test_resolve_unknown_value_returns_none():
    api, _ = _api()
    assert api.resolve("Bogus", picklist="Severity") is None


def test_resolve_needs_picklist_or_module_field():
    api, _ = _api()
    assert api.resolve("High") is None


def test_resolve_caches_iri():
    api, client = _api()
    api.resolve("High", picklist="Severity")
    api.resolve("High", picklist="Severity")
    pl_calls = [c for c in client.get_calls if "listName.name=Severity" in c[0]]
    assert len(pl_calls) == 1


# --- resolve_record_fields -------------------------------------------------
def test_resolve_record_fields():
    api, _ = _api()
    out = api.resolve_record_fields(
        "alerts",
        {"name": "x", "severity": "High", "status": "Open"},
    )
    assert out == {
        "name": "x",
        "severity": "/api/3/picklists/sev-high",
        "status": "/api/3/picklists/st-open",
    }


def test_resolve_record_fields_leaves_unresolvable():
    api, _ = _api()
    out = api.resolve_record_fields("alerts", {"severity": "Nope", "name": "x"})
    assert out == {"severity": "Nope", "name": "x"}  # unchanged


def test_resolve_record_fields_iri_passthrough():
    api, _ = _api()
    out = api.resolve_record_fields("alerts", {"severity": "/api/3/picklists/sev-low"})
    assert out == {"severity": "/api/3/picklists/sev-low"}


def test_validate_record_fields_all_resolve():
    api, _ = _api()
    misses = api.validate_record_fields("alerts", {"name": "x", "severity": "High", "status": "Open"})
    assert misses == []  # every picklist field resolves cleanly


def test_validate_record_fields_reports_misses():
    api, _ = _api()
    misses = api.validate_record_fields("alerts", {"severity": "Nope", "name": "x"})
    assert len(misses) == 1
    assert misses[0]["field"] == "severity"
    assert misses[0]["value"] == "Nope"
    assert "valid_values" in misses[0]


def test_clear_cache():
    api, client = _api()
    api.list()
    api.clear_cache()
    api.list()
    name_calls = [c for c in client.get_calls if c[0].startswith("/api/3/picklist_names")]
    assert len(name_calls) == 2


# --- RecordSet opt-in integration -----------------------------------------
def test_recordset_create_resolves_picklists_opt_in():
    api, client = _api()
    RecordSet(client, "alerts", typed=False).create({"name": "x", "severity": "High"}, resolve_picklists=True)
    endpoint, data = client.post_calls[0]
    assert endpoint == "/api/3/alerts"
    assert data["severity"] == "/api/3/picklists/sev-high"


def test_recordset_create_resolves_picklists_by_default():
    api, client = _api()
    RecordSet(client, "alerts", typed=False).create({"severity": "High"})
    _, data = client.post_calls[0]
    assert data["severity"] == "/api/3/picklists/sev-high"  # resolved by default


def test_recordset_create_resolution_opt_out():
    api, client = _api()
    RecordSet(client, "alerts", typed=False).create({"severity": "High"}, resolve_picklists=False)
    _, data = client.post_calls[0]
    assert data["severity"] == "High"  # untouched when opted out


def test_recordset_update_resolves_picklists_opt_in():
    api, client = _api()
    RecordSet(client, "alerts", typed=False).update("u1", {"status": "Open"}, resolve_picklists=True)
    endpoint, data = client.put_calls[0]
    assert endpoint == "/api/3/alerts/u1"
    assert data["status"] == "/api/3/picklists/st-open"
