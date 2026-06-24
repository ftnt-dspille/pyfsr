"""Unit tests for the typed Pydantic record models."""

from pyfsr import Alert, BaseRecord, Incident, RecordSet, model_for
from pyfsr.models import MODEL_REGISTRY


class _NoopPicklists:
    def resolve_record_fields(self, module, fields, **kwargs):
        return fields


class FakeClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        self.picklists = _NoopPicklists()

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
def test_dict_field_preserves_expanded_relationship():
    # modifyUser is typed RecordIRI | dict; expanded dicts are kept as-is so
    # _as_actor can read @type and dispatch to User vs Appliance correctly.
    alert = Alert.model_validate({"uuid": "a1", "modifyUser": {"@id": "/api/3/people/u-1", "name": "Ann"}})
    assert alert.modifyUser == {"@id": "/api/3/people/u-1", "name": "Ann"}


def test_str_picklist_field_collapses_to_iri():
    # severity is typed str — the collapse validator flattens the expanded picklist
    # object to its @id IRI; callers use picklist_uuid() to extract the UUID.
    alert = Alert.model_validate({"uuid": "a1", "severity": {"@id": "/api/3/picklists/p-1", "itemValue": "High"}})
    assert alert.severity == "/api/3/picklists/p-1"


def test_str_field_plain_iri_unchanged():
    alert = Alert.model_validate({"uuid": "a1", "modifyUser": "/api/3/people/u-1"})
    assert alert.modifyUser == "/api/3/people/u-1"


# -- integration models: status field 7.x<->8.0 tolerance --------------------


def test_connector_config_status_as_int_7x_active():
    """ConnectorConfig.status tolerates 7.x int active-flag (1 = active)."""
    from pyfsr.models import ConnectorConfig

    cfg = ConnectorConfig.model_validate(
        {
            "id": 37,
            "config_id": "cfg-7",
            "name": "prod",
            "default": True,
            "status": 1,
            "config": {"k": "v"},
            "connector": 16,
        }
    )
    assert cfg.status == 1
    assert cfg.config_id == "cfg-7"


def test_connector_config_status_as_int_7x_inactive():
    """ConnectorConfig.status tolerates 7.x int status 0 (inactive)."""
    from pyfsr.models import ConnectorConfig

    cfg = ConnectorConfig.model_validate(
        {
            "config_id": "cfg-7",
            "status": 0,
        }
    )
    assert cfg.status == 0


def test_connector_config_status_as_8x_nested_op_envelope():
    """FortiSOAR 8.0 PUT echoes row with async op-envelope in status; coerces to None."""
    from pyfsr.models import ConnectorConfig

    cfg = ConnectorConfig.model_validate(
        {
            "id": 37,
            "config_id": "cfg-7",
            "name": "prod",
            "default": True,
            "status": {"status": "finished", "message": "Configuration prod has been updated successfully"},
            "config": {"k": "v"},
            "connector": 16,
        }
    )
    # Op-envelope coerced to None (no active-flag conveyed)
    assert cfg.status is None
    assert cfg.config_id == "cfg-7"
    assert cfg.name == "prod"
    assert cfg.default is True


def test_connector_config_status_string_coerces_to_none():
    """Non-int, non-dict status (e.g. unexpected string) coerces to None."""
    from pyfsr.models import ConnectorConfig

    cfg = ConnectorConfig.model_validate(
        {
            "config_id": "cfg-7",
            "status": "finished",
        }
    )
    assert cfg.status is None


def test_connector_config_status_bool_coerces_to_none():
    """Bool status coerces to None to avoid True->1 surprises."""
    from pyfsr.models import ConnectorConfig

    cfg = ConnectorConfig.model_validate(
        {
            "config_id": "cfg-7",
            "status": True,
        }
    )
    assert cfg.status is None


def test_connector_config_status_missing_defaults_to_none():
    """Missing status defaults to None."""
    from pyfsr.models import ConnectorConfig

    cfg = ConnectorConfig.model_validate(
        {
            "config_id": "cfg-7",
        }
    )
    assert cfg.status is None


def test_connector_config_7x_vs_8x_normalization():
    """7.x int(1) and 8.0 op-envelope normalize consistently (int vs None respectively)."""
    from pyfsr.models import ConnectorConfig

    cfg_7x = ConnectorConfig.model_validate(
        {
            "config_id": "cfg-7",
            "name": "prod",
            "status": 1,
        }
    )
    cfg_8x = ConnectorConfig.model_validate(
        {
            "config_id": "cfg-7",
            "name": "prod",
            "status": {"status": "finished", "message": "..."},
        }
    )
    # Both parse successfully; 7.x preserves active flag, 8.x coerces to None
    assert cfg_7x.config_id == cfg_8x.config_id
    assert cfg_7x.name == cfg_8x.name
    assert cfg_7x.status == 1
    assert cfg_8x.status is None
