"""Unit tests for module/schema administration (create, alter, publish)."""

from pyfsr.api.modules_admin import ModulesAdminAPI


class RecordingClient:
    """Records calls and returns canned staging data."""

    def __init__(self):
        self.calls = []
        self.staging_full = {
            "uuid": "u-1",
            "type": "widgets",
            "attributes": [
                {"name": "name", "type": "string", "formType": "text"},
                {"name": "payload", "type": "string", "formType": "textarea"},
            ],
        }

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        if endpoint.endswith("/u-1"):
            return self.staging_full
        return {"hydra:member": [{"type": "widgets", "module": "widgets", "uuid": "u-1"}]}

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"uuid": "u-1", **(data or {})}

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return {"ok": True, **(data or {})}

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))


def test_field_builder_defaults_and_overrides():
    f = ModulesAdminAPI.field("payload", db_type="object", form_type="object", required=True)
    assert f["name"] == "payload"
    assert f["type"] == "object" and f["formType"] == "object"
    assert f["validation"]["required"] is True
    # form_type defaults to db_type
    assert ModulesAdminAPI.field("x", db_type="string")["formType"] == "string"


def test_create_module_payload_shape():
    c = RecordingClient()
    ModulesAdminAPI(c).create_module("widgets", label="Widget")
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/3/staging_model_metadatas"
    assert data["type"] == "widgets" and data["tableName"] == "widgets"
    # default single 'name' field added when none supplied
    assert [a["name"] for a in data["attributes"]] == ["name"]


def test_set_field_type_puts_only_attributes():
    c = RecordingClient()
    ModulesAdminAPI(c).set_field_type("widgets", "payload", db_type="object", form_type="object")
    method, endpoint, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/3/staging_model_metadatas/u-1"
    # body carries ONLY attributes (full-record PUT is rejected by the platform)
    assert set(data.keys()) == {"attributes"}
    changed = next(a for a in data["attributes"] if a["name"] == "payload")
    assert changed["type"] == "object" and changed["formType"] == "object"


def test_publish_hits_global_endpoint():
    c = RecordingClient()
    # wait=False → fire-and-forget, no readiness polling after the PUT.
    ModulesAdminAPI(c).publish(wait=False)
    assert c.calls[-1] == ("PUT", "/api/publish", {})


def test_set_field_type_unknown_field_raises():
    c = RecordingClient()
    try:
        ModulesAdminAPI(c).set_field_type("widgets", "nope", db_type="object")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "nope" in str(e)
