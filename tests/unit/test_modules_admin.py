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
    ModulesAdminAPI(c).create_module("widgets", label="Widget", create_view_templates=False)
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/3/staging_model_metadatas"
    assert data["type"] == "widgets" and data["tableName"] == "widgets"
    # default single 'name' field added when none supplied
    assert [a["name"] for a in data["attributes"]] == ["name"]


def test_create_module_settings_map_to_metadata_keys():
    c = RecordingClient()
    ModulesAdminAPI(c).create_module(
        "widgets",
        label="Widget",
        ownable=True,
        recycle_bin=True,
        multi_tenancy=True,
        record_uniqueness=["name"],
        default_sort=[{"field": "createDate", "direction": "DESC"}],
        create_view_templates=False,
    )
    _, _, data = c.calls[-1]
    assert data["ownable"] is True and data["userOwnable"] is True
    assert data["softDeleteable"] is True  # Enable Recycle Bin
    assert data["peerReplicable"] is True  # Enable Multi-Tenancy
    assert data["uniqueConstraint"] == ["name"]
    assert data["defaultSort"] == [{"field": "createDate", "direction": "DESC"}]


def test_create_module_also_creates_view_templates():
    c = RecordingClient()
    ModulesAdminAPI(c).create_module("widgets", label="Widget")
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/3/bulkupsert/system_view_templates"
    layouts = {vt["viewOptions"] for vt in data["__data"]}
    assert layouts == {"list", "detail", "form"}


def test_field_options_map_to_metadata_keys():
    f = ModulesAdminAPI.field(
        "secret",
        editable=False,
        grid_column=True,
        encrypted=True,
        tooltip="hush",
        maxlength=1024,
        enable_range=True,
        bulk_edit=True,
    )
    assert f["writeable"] is False  # editable -> writeable
    assert f["gridColumn"] is True and f["encrypted"] is True
    assert f["tooltip"] == "hush"
    assert f["validation"]["maxlength"] == 1024 and f["validation"]["_enableRange"] is True
    assert f["bulkAction"]["allow"] is True


def test_picklist_field_binds_datasource():
    f = ModulesAdminAPI.picklist_field("severity", "AlertSeverity", multi=True)
    assert f["type"] == "picklists" and f["formType"] == "multiselectpicklist"
    assert f["collection"] is True
    flt = f["dataSource"]["query"]["filters"][0]
    assert flt == {"field": "listName__name", "operator": "eq", "value": "AlertSeverity"}


def test_relationship_field_targets_module():
    f = ModulesAdminAPI.relationship_field("relatedalerts", "alerts")
    assert f["type"] == "alerts" and f["formType"] == "manyToMany"
    assert f["collection"] is True and f["dataSource"] == {"model": "alerts"}


def test_pending_changes_diffs_staging_vs_published():
    class DiffClient(RecordingClient):
        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            if "staging_model_metadatas" in endpoint:
                return {
                    "hydra:member": [
                        {"type": "alerts", "taggable": True},  # modified vs published
                        {"type": "widgets"},  # created (not in published)
                    ]
                }
            return {
                "hydra:member": [
                    {"type": "alerts", "taggable": False},
                    {"type": "legacy"},  # deleted (only in published)
                ]
            }

    changes = {c["module"]: c["change"] for c in ModulesAdminAPI(DiffClient()).pending_changes()}
    assert changes == {"alerts": "modified", "widgets": "created", "legacy": "deleted"}


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


def test_set_module_settings_maps_keys_and_syncs_owner():
    class SettingsClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.applied = {}

        def put(self, endpoint, data=None, params=None, **kw):
            self.calls.append(("PUT", endpoint, data))
            self.applied.update(data or {})  # simulate staging accepting the write
            return {"ok": True}

        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            if endpoint.endswith("/u-1"):
                return {**self.staging_full, **self.applied}
            return {"hydra:member": [{"type": "widgets", "module": "widgets", "uuid": "u-1"}]}

    c = SettingsClient()
    ModulesAdminAPI(c).set_module_settings("widgets", ownable=True, recycle_bin=True)
    put = next(d for m, e, d in c.calls if m == "PUT")
    assert put["softDeleteable"] is True  # recycle_bin -> softDeleteable
    assert put["ownable"] is True and put["userOwnable"] is True  # owner synced


def test_set_module_settings_unknown_key_raises():
    c = RecordingClient()
    try:
        ModulesAdminAPI(c).set_module_settings("widgets", bogus=True)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "bogus" in str(e)


def test_set_field_type_unknown_field_raises():
    c = RecordingClient()
    try:
        ModulesAdminAPI(c).set_field_type("widgets", "nope", db_type="object")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "nope" in str(e)
