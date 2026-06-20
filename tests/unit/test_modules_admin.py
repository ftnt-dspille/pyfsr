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
    # record_uniqueness is built into the platform's named-constraint object shape,
    # NOT a flat field-name list (a flat list is silently ignored by FortiSOAR).
    assert data["uniqueConstraint"] == [{"widgets_unique": {"columns": ["name"]}}]
    assert data["defaultSort"] == [{"field": "createDate", "direction": "DESC"}]


def test_create_module_uniqueness_off_when_empty():
    c = RecordingClient()
    ModulesAdminAPI(c).create_module("widgets", create_view_templates=False)
    _, _, data = c.calls[-1]
    assert data["uniqueConstraint"] == []


def test_set_module_settings_record_uniqueness_builds_constraint(monkeypatch):
    c = RecordingClient()
    api = ModulesAdminAPI(c)
    expected = [{"alerts_unique": {"columns": ["name", "source"]}}]
    # set_module_settings verifies by re-reading staging; reflect the applied value.
    monkeypatch.setattr(
        api, "get_staging", lambda module: {"uuid": "u-1", "uniqueConstraint": expected}
    )
    api.set_module_settings("alerts", record_uniqueness=["name", "source"])
    method, endpoint, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/3/staging_model_metadatas/u-1"
    assert data["uniqueConstraint"] == expected


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
    # default many-to-many owns the relationship (its reverse auto-creates on the target)
    assert f["ownsRelationship"] is True


def test_typed_builders_map_widget_to_storage_type():
    # widgets that all store "string"
    for builder, widget in [
        (ModulesAdminAPI.text_field, "text"),
        (ModulesAdminAPI.email_field, "email"),
        (ModulesAdminAPI.url_field, "url"),
        (ModulesAdminAPI.phone_field, "phone"),
        (ModulesAdminAPI.password_field, "password"),
    ]:
        f = builder("x")
        assert f["type"] == "string" and f["formType"] == widget
    # text variants
    assert ModulesAdminAPI.text_field("x", area=True)["formType"] == "textarea"
    assert ModulesAdminAPI.text_field("x", rich=True)["formType"] == "richtext"
    assert ModulesAdminAPI.text_field("x", html=True)["formType"] == "html"
    # non-string storage types
    assert ModulesAdminAPI.integer_field("x")["type"] == "integer"
    # datetime is stored as an integer behind a datetime widget
    dt = ModulesAdminAPI.datetime_field("x")
    assert dt["type"] == "integer" and dt["formType"] == "datetime"
    cb = ModulesAdminAPI.checkbox_field("x")
    assert cb["type"] == "boolean" and cb["formType"] == "checkbox"
    assert ModulesAdminAPI.object_field("x")["type"] == "object"


def test_full_field_type_coverage():
    # every non-relationship widget the in-product editor offers maps to a builder
    cases = {
        "decimal_field": ("float", "decimal"),
        "domain_field": ("string", "domain"),
        "ipv4_field": ("string", "ipv4"),
        "ipv6_field": ("string", "ipv6"),
        "filehash_field": ("string", "filehash"),
        "file_field": ("string", "file"),
        "json_field": ("object", "json"),
        "object_field": ("object", "object"),
        "password_field": ("string", "password"),
    }
    for builder_name, (storage, widget) in cases.items():
        f = getattr(ModulesAdminAPI, builder_name)("x")
        assert f["type"] == storage and f["formType"] == widget, builder_name


def test_typed_field_rejects_relationship_widgets():
    import pytest

    with pytest.raises(ValueError):
        ModulesAdminAPI.typed_field("x", "manyToMany")


def test_lookup_field_is_single_ref_with_no_reverse():
    f = ModulesAdminAPI.lookup_field("owner", "people")
    assert f["type"] == "people" and f["formType"] == "lookup"
    # a lookup is a single many-to-one ref: not a collection, owns nothing
    assert f["collection"] is False and f["ownsRelationship"] is False
    assert f["dataSource"] == {"model": "people"}


def test_relationship_field_custom_inverse_and_one_to_many():
    f = ModulesAdminAPI.relationship_field("agents", "agents", many=False, inversed_field="router")
    assert f["formType"] == "oneToMany" and f["collection"] is True
    assert f["inversedField"] == "router"
    # a non-owning mirror of an existing relationship
    g = ModulesAdminAPI.relationship_field("x", "alerts", owns_relationship=False)
    assert g["ownsRelationship"] is False


def test_field_rejects_invalid_names_and_types():
    import pytest

    # invalid API keys (would only fail at publish on the appliance)
    for bad in ["bad name", "1leading", "has-hyphen", "has.dot", ""]:
        with pytest.raises(ValueError):
            ModulesAdminAPI.text_field(bad)
    # non-existent storage types people reach for by habit
    for bad_type in ["text", "json", "datetime", "bool"]:
        with pytest.raises(ValueError):
            ModulesAdminAPI.field("x", db_type=bad_type)
    # encrypted and searchable are mutually exclusive
    with pytest.raises(ValueError):
        ModulesAdminAPI.text_field("secret", encrypted=True, searchable=True)
    # valid names/types pass (camelCase, underscores, digits after a letter)
    assert ModulesAdminAPI.text_field("goodName")["name"] == "goodName"
    assert ModulesAdminAPI.field("x_1", db_type="string")["type"] == "string"


def test_create_module_rejects_invalid_module_names():
    import pytest

    c = RecordingClient()
    admin = ModulesAdminAPI(c)
    for bad in ["BadCase", "bad name", "9mod", "bad-mod"]:
        with pytest.raises(ValueError):
            admin.create_module(bad)
    with pytest.raises(ValueError):
        admin.create_module("goodmod", fields=[])  # empty field list


def test_find_invalid_drafts_and_publish_precheck():
    import pytest

    class BadDraftClient(RecordingClient):
        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            if endpoint == "/api/3/staging_model_metadatas":
                return {
                    "hydra:member": [
                        {"type": "widgets", "uuid": "u-ok"},  # valid
                        {"type": "9probe", "uuid": "u-bad"},  # invalid: leading digit
                    ]
                }
            return super().get(endpoint, params=params, **kw)

    admin = ModulesAdminAPI(BadDraftClient())
    bad = admin.find_invalid_drafts()
    assert bad == [{"module": "9probe", "uuid": "u-bad", "problem": "invalid module name"}]
    # publish must refuse before issuing the destructive appliance-wide PUT
    with pytest.raises(ValueError, match="9probe"):
        admin.publish()
    assert not any(c[0] == "PUT" for c in admin.client.calls)


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
    class PublishClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self._ts = 100

        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            # /api/publish/error: advance last_publish_time on each read so the post-PUT
            # poll sees a fresh, successful publish and publish() confirms completion.
            if endpoint == "/api/publish/error":
                self._ts += 1
                return {"status": "Success", "last_publish_time": self._ts}
            return super().get(endpoint, params=params, **kw)

    c = PublishClient()
    result = ModulesAdminAPI(c).publish(poll_interval=0)
    # PUT to the global endpoint, then confirmation read of /api/publish/error.
    assert ("PUT", "/api/publish", {}) in c.calls
    assert result["status"] == "Success"


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


# --------------------------------------------------------------- delete_module


class DeleteClient:
    """Fake client modelling staging/published lists for delete_module tests.

    ``referrer`` toggles whether another module ('alerts') has a relationship field
    pointing at the target ('widgets').
    """

    def __init__(self, *, published=True, staging=True, referrer=False):
        self.calls = []
        self._published = published
        self._staging = staging
        widget_rec = {
            "uuid": "w-1",
            "type": "widgets",
            "module": "widgets",
            "tableName": "widgets",
            "attributes": [{"name": "name", "type": "string"}],
        }
        alerts_attrs = [{"name": "name", "type": "string"}]
        if referrer:
            alerts_attrs.append({"name": "widgets", "type": "widgets", "formType": "manyToMany"})
        self._alerts = {
            "uuid": "a-1",
            "type": "alerts",
            "module": "alerts",
            "tableName": "alerts",
            "attributes": alerts_attrs,
        }
        self._widget = widget_rec

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        if endpoint.endswith("/w-1"):
            return self._widget
        if endpoint.endswith("/a-1"):
            return self._alerts
        members = [self._alerts]
        if endpoint.startswith("/api/3/staging_model_metadatas") and self._staging:
            members = [self._widget, self._alerts]
        if endpoint.startswith("/api/3/model_metadatas") and self._published:
            members = [self._widget, self._alerts]
        elif endpoint.startswith("/api/3/model_metadatas"):
            members = [self._alerts]
        return {"hydra:member": members}

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return {"ok": True, **(data or {})}

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"uuid": "x", **(data or {})}


def test_remove_field_drops_attribute_and_puts_remainder():
    c = RecordingClient()
    ModulesAdminAPI(c).remove_field("widgets", "payload")
    put = next(call for call in c.calls if call[0] == "PUT")
    names = [a["name"] for a in put[2]["attributes"]]
    assert names == ["name"]


def test_remove_field_missing_raises_unless_ok():
    c = RecordingClient()
    api = ModulesAdminAPI(c)
    try:
        api.remove_field("widgets", "ghost")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "ghost" in str(e)
    # missing_ok swallows it
    api.remove_field("widgets", "ghost", missing_ok=True)


def test_find_relationship_referrers_locates_reverse_field():
    c = DeleteClient(referrer=True)
    refs = ModulesAdminAPI(c).find_relationship_referrers("widgets")
    assert refs == [("alerts", ["widgets"])]


def test_find_relationship_referrers_empty_when_none():
    c = DeleteClient(referrer=False)
    assert ModulesAdminAPI(c).find_relationship_referrers("widgets") == []


def test_delete_module_refuses_when_referrers_and_no_detach():
    from pyfsr.exceptions import FortiSOARException

    c = DeleteClient(referrer=True)
    api = ModulesAdminAPI(c)
    try:
        api.delete_module("widgets", publish=False)
        assert False, "expected refusal"
    except FortiSOARException as e:
        assert "detach_relationships=True" in str(e)
        assert "alerts" in str(e)
    # nothing was deleted
    assert not any(call[0] == "DELETE" for call in c.calls)


def test_delete_module_detaches_then_deletes(monkeypatch):
    c = DeleteClient(referrer=True)
    api = ModulesAdminAPI(c)
    published = {"sentinel": "published"}
    monkeypatch.setattr(api, "publish", lambda **kw: published)
    # avoid view-template lookups during discard
    monkeypatch.setattr(api, "get_view_templates", lambda module: [])

    res = api.delete_module("widgets", detach_relationships=True)
    assert res["detached"] == ["alerts.widgets"]
    assert res["orphan_table"] == "widgets"
    assert res["published"] is published
    # the reverse field was PUT-removed from alerts, and the target staging DELETEd
    assert any(call[0] == "PUT" and "a-1" in call[1] for call in c.calls)
    assert any(call[0] == "DELETE" and "w-1" in call[1] for call in c.calls)


def test_delete_module_not_found_raises():
    c = DeleteClient(published=False, staging=False)
    try:
        ModulesAdminAPI(c).delete_module("widgets", publish=False)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not found" in str(e)
