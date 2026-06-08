"""Unit tests for the typed Pydantic record models."""

from pyfsr import Alert, BaseRecord, Incident, RecordSet, model_for
from pyfsr.models import MODEL_REGISTRY


class FakeClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def get(self, endpoint, params=None, **kwargs):
        self.calls.append(("GET", endpoint, params, None))
        return self.responses.get(endpoint, {})

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.calls.append(("POST", endpoint, params, data))
        return self.responses.get(endpoint, {})

    def put(self, endpoint, data=None, **kwargs):
        self.calls.append(("PUT", endpoint, None, data))
        return self.responses.get(endpoint, {})


# -- registry ---------------------------------------------------------------
def test_model_for_known_module():
    assert model_for("alerts") is Alert
    assert model_for("incidents") is Incident


def test_model_for_unknown_module_falls_back_to_baserecord():
    assert model_for("my_custom_module") is BaseRecord


def test_registry_covers_core_entities():
    assert set(MODEL_REGISTRY) >= {"alerts", "incidents", "tasks", "comments"}


# -- BaseRecord behavior ----------------------------------------------------
def test_baserecord_preserves_unknown_fields():
    rec = BaseRecord.model_validate({"uuid": "u1", "customField": "kept", "id": 7})
    assert rec.uuid == "u1"
    assert rec["customField"] == "kept"  # extra field, dict access
    assert "customField" in rec
    assert rec.get("missing", "dflt") == "dflt"


def test_baserecord_iri_and_type_aliases():
    rec = BaseRecord.model_validate({"@id": "/api/3/alerts/abc", "@type": "Alert"})
    assert rec.iri == "/api/3/alerts/abc"
    assert rec.record_type == "Alert"
    assert rec["@id"] == "/api/3/alerts/abc"  # round-trip via wire name


def test_picklist_uuid_helper():
    inc = Incident.model_validate({"severity": "/api/3/picklists/sev-uuid-9"})
    assert inc.picklist_uuid("severity") == "sev-uuid-9"
    assert inc.picklist_uuid("status") is None  # absent


def test_to_dict_round_trips_aliases():
    inc = Incident.model_validate({"@id": "/api/3/incidents/i1", "uuid": "i1", "name": "Breach"})
    d = inc.to_dict(exclude_none=True)
    assert d["@id"] == "/api/3/incidents/i1"
    assert d["name"] == "Breach"


def test_typed_alert_fields():
    alert = Alert.model_validate({"uuid": "a1", "name": "Phish", "severity": "/api/3/picklists/x"})
    assert alert.name == "Phish"
    assert alert.severity == "/api/3/picklists/x"


# -- RecordSet integration --------------------------------------------------
def test_recordset_returns_registered_model():
    client = FakeClient({"/api/3/incidents/i1": {"uuid": "i1", "name": "X"}})
    rec = RecordSet(client, "incidents").get("i1")
    assert isinstance(rec, Incident)


def test_recordset_unknown_module_returns_baserecord():
    client = FakeClient({"/api/3/widgets/w1": {"uuid": "w1", "anything": 1}})
    rec = RecordSet(client, "widgets").get("w1")
    assert isinstance(rec, BaseRecord) and not isinstance(rec, Incident)
    assert rec["anything"] == 1


def test_recordset_typed_false_returns_dict():
    client = FakeClient({"/api/3/incidents/i1": {"uuid": "i1"}})
    rec = RecordSet(client, "incidents", typed=False).get("i1")
    assert rec == {"uuid": "i1"}


def test_recordset_create_accepts_model_instance():
    client = FakeClient({"/api/3/incidents": {"uuid": "new"}})
    out = RecordSet(client, "incidents").create(Incident(name="New incident"))
    # model serialized to a dict body (exclude_none drops the empty fields)
    sent_body = client.calls[0][3]
    assert sent_body == {"name": "New incident"}
    assert isinstance(out, Incident) and out.uuid == "new"


# -- expanded-relationship collapse (P5 model-leniency fix) -----------------
def test_str_field_collapses_expanded_relationship_to_iri():
    # modifyUser is typed str; when the API expands it, collapse to its @id.
    alert = Alert.model_validate(
        {"uuid": "a1", "modifyUser": {"@id": "/api/3/people/u-1", "name": "Ann"}}
    )
    assert alert.modifyUser == "/api/3/people/u-1"


def test_any_picklist_field_keeps_expanded_object():
    # severity is typed Any (an "IRI to picklist" field); keep the full object.
    alert = Alert.model_validate(
        {"uuid": "a1", "severity": {"@id": "/api/3/picklists/p-1", "itemValue": "High"}}
    )
    assert alert.severity == {"@id": "/api/3/picklists/p-1", "itemValue": "High"}


def test_str_field_plain_iri_unchanged():
    alert = Alert.model_validate({"uuid": "a1", "modifyUser": "/api/3/people/u-1"})
    assert alert.modifyUser == "/api/3/people/u-1"
