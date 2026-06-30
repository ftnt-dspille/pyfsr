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
        self.roles = FakeRolesAPI()
        self.app_config = FakeAppConfigAPI()

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


class FakeRolesAPI:
    """Fake roles API for testing grant_module_permissions calls."""

    def __init__(self):
        self.grant_calls = []

    def grant_module_permissions(
        self,
        role,
        *,
        module,
        can_read=True,
        can_create=True,
        can_update=True,
        can_delete=True,
        can_execute=True,
    ):
        """Record grant calls for inspection in tests."""
        self.grant_calls.append(
            {
                "role": role,
                "module": module,
                "can_read": can_read,
                "can_create": can_create,
                "can_update": can_update,
                "can_delete": can_delete,
                "can_execute": can_execute,
            }
        )
        return {"uuid": "r-1", "name": role}


class FakeAppConfigAPI:
    """Fake app_config API recording add_navigation_item calls for inspection."""

    def __init__(self):
        self.nav_calls = []
        self.nav_removed = []

    def add_navigation_item(self, item, *, parent=None, position="bottom"):
        self.nav_calls.append({"item": item, "parent": parent, "position": position})
        return {"ok": True}

    def remove_navigation_item(self, module=None, title=None, *, missing_ok=True):
        self.nav_removed.append({"module": module, "title": title})
        return {"ok": True}


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
    monkeypatch.setattr(api, "get_staging", lambda module: {"uuid": "u-1", "uniqueConstraint": expected})
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


def test_required_accepts_query_as_condition():
    from pyfsr import Query

    f = ModulesAdminAPI.email_field("emailFrom", required=Query(module="alerts").eq("type", "Phishing"))
    cond = f["validation"]["required"]
    assert cond["logic"] == "AND"
    # the module-bound Query auto-resolves the picklist to .itemValue
    assert cond["filters"] == [{"field": "type.itemValue", "operator": "eq", "value": "Phishing"}]


def test_visibility_accepts_query_as_condition():
    from pyfsr import Query

    g = ModulesAdminAPI.text_field("notes", visibility=Query().eq("status.itemValue", "Open"))
    assert g["visibility"]["filters"][0]["field"] == "status.itemValue"


def test_required_bool_and_dict_pass_through_unchanged():
    assert ModulesAdminAPI.text_field("a", required=True)["validation"]["required"] is True
    raw = {"logic": "OR", "filters": []}
    assert ModulesAdminAPI.text_field("b", required=raw)["validation"]["required"] == raw


def test_lookup_field_is_single_ref_with_no_reverse():
    f = ModulesAdminAPI.lookup_field("owner", "people")
    assert f["type"] == "people" and f["formType"] == "lookup"
    # a lookup is a single many-to-one ref: not a collection, owns nothing
    assert f["collection"] is False and f["ownsRelationship"] is False
    assert f["dataSource"] == {"model": "people"}


class FakeTeamsAPI:
    """Fake teams API: resolves a fixed set of team names to uuids."""

    _UUIDS = {
        "TeamA": "11111111-1111-1111-1111-111111111111",
        "TeamB": "22222222-2222-2222-2222-222222222222",
        "SOC Team": "33333333-3333-3333-3333-333333333333",
    }

    def team_uuid_by_name(self, name):
        return self._UUIDS.get(name)


class TeamScopeClient(RecordingClient):
    """RecordingClient that also answers version() and exposes a teams API."""

    def __init__(self, version="8.0.0-6034"):
        super().__init__()
        self._version = version
        self.teams = FakeTeamsAPI()

    def version(self):
        return self._version


def test_lookup_field_team_scope_sets_datasource_filters():
    # Pure builder: stores the raw identifiers under dataSourceFilters; no resolution yet.
    f = ModulesAdminAPI.lookup_field("approver", "people", team_scope=["TeamA"])
    assert f["dataSourceFilters"]["showTeams"] is True
    assert f["dataSourceFilters"]["teams"] == ["TeamA"]


def test_team_scope_merges_with_ownable_filter():
    f = ModulesAdminAPI.lookup_field(
        "approver", "people", ownable_filter=True, owning_module="widgets", team_scope=["TeamA"]
    )
    assert f["dataSourceFilters"]["isOwnable"] is True
    assert f["dataSourceFilters"]["showTeams"] is True
    assert f["dataSourceFilters"]["teams"] == ["TeamA"]


def test_relationship_field_team_scope():
    f = ModulesAdminAPI.relationship_field("members", "people", team_scope=["TeamA", "TeamB"])
    assert f["formType"] == "manyToMany"
    assert f["dataSourceFilters"]["teams"] == ["TeamA", "TeamB"]


def test_team_scope_resolves_names_to_iris_on_8_0():
    c = TeamScopeClient(version="8.0.0-6034")
    api = ModulesAdminAPI(c)
    field = api.relationship_field("members", "people", team_scope=["TeamA", "TeamB"])
    api.create_module("widgets", fields=[api.text_field("name"), field], create_view_templates=False)
    _, _, data = c.calls[-1]
    members = next(a for a in data["attributes"] if a["name"] == "members")
    assert members["dataSourceFilters"]["teams"] == [
        "/api/3/teams/11111111-1111-1111-1111-111111111111",
        "/api/3/teams/22222222-2222-2222-2222-222222222222",
    ]


def test_team_scope_accepts_iri_and_uuid_unchanged():
    c = TeamScopeClient(version="8.0.0")
    api = ModulesAdminAPI(c)
    field = api.lookup_field(
        "approver",
        "people",
        team_scope=["/api/3/teams/abc", "33333333-3333-3333-3333-333333333333"],
    )
    api.create_module("widgets", fields=[api.text_field("name"), field], create_view_templates=False)
    _, _, data = c.calls[-1]
    approver = next(a for a in data["attributes"] if a["name"] == "approver")
    assert approver["dataSourceFilters"]["teams"] == [
        "/api/3/teams/abc",
        "/api/3/teams/33333333-3333-3333-3333-333333333333",
    ]


def test_team_scope_rejected_below_8_0():
    import pytest

    from pyfsr.exceptions import FortiSOARException

    c = TeamScopeClient(version="7.6.5-1234")
    api = ModulesAdminAPI(c)
    field = api.relationship_field("members", "people", team_scope=["TeamA"])
    with pytest.raises(FortiSOARException, match="8.0"):
        api.create_module("widgets", fields=[api.text_field("name"), field], create_view_templates=False)
    # nothing was POSTed — the guard runs before any staging write
    assert not any(m == "POST" for m, _, _ in c.calls)


def test_team_scope_unknown_team_name_raises():
    import pytest

    from pyfsr.exceptions import FortiSOARException

    c = TeamScopeClient(version="8.0.0")
    api = ModulesAdminAPI(c)
    field = api.lookup_field("approver", "people", team_scope=["NoSuchTeam"])
    with pytest.raises(FortiSOARException, match="NoSuchTeam"):
        api.create_module("widgets", fields=[field], create_view_templates=False)


def test_team_scope_proceeds_when_version_unknown():
    # version() failing must not block the feature — the appliance is the backstop.
    from pyfsr.exceptions import FortiSOARException

    class NoVersionClient(TeamScopeClient):
        def version(self):
            raise FortiSOARException("all endpoints failed")

    c = NoVersionClient()
    api = ModulesAdminAPI(c)
    field = api.lookup_field("approver", "people", team_scope=["TeamA"])
    api.create_module("widgets", fields=[field], create_view_templates=False)
    _, _, data = c.calls[-1]
    approver = next(a for a in data["attributes"] if a["name"] == "approver")
    assert approver["dataSourceFilters"]["teams"] == ["/api/3/teams/11111111-1111-1111-1111-111111111111"]


def test_scope_field_to_teams_helper_matches_kwarg():
    built = ModulesAdminAPI.relationship_field("members", "people", team_scope=["TeamA"])
    api = ModulesAdminAPI(TeamScopeClient())
    manual = api.scope_field_to_teams(api.relationship_field("members", "people"), ["TeamA"])
    assert built["dataSourceFilters"] == manual["dataSourceFilters"]


def test_parse_version_tolerates_shapes():
    from pyfsr.api.modules_admin import _parse_version

    assert _parse_version("8.0.0-6034") == (8, 0, 0)
    assert _parse_version("7.6.5") == (7, 6, 5)
    assert _parse_version("8.0") == (8, 0, 0)
    assert _parse_version({"version": "8.0.0-6034"}) == (8, 0, 0)
    assert _parse_version({"build": "7.4.2"}) == (7, 4, 2)
    assert _parse_version("unknown") is None


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


def test_create_module_orphan_table_collision_precheck():
    import pytest

    from pyfsr._testing.replay import ReplayTransport
    from pyfsr.cli.appliance.facts import Facts
    from pyfsr.exceptions import FortiSOARException

    c = RecordingClient()
    admin = ModulesAdminAPI(c)
    # The module is NOT live (no metadata) but its tableName already has leftover
    # physical tables from a prior delete → publishing would wedge on 42P07.
    admin.get_published = lambda module: None
    admin.get_staging = lambda module: None
    facts = Facts(ReplayTransport(tables=["leftovermod", "leftovermod_team", "leftovermod_actor"]))

    with pytest.raises(FortiSOARException, match="orphaned physical table"):
        admin.create_module("leftovermod", facts=facts)
    # Nothing staged — the guard fired before any POST.
    assert not any(m == "POST" for m, _e, _d in c.calls)


def test_create_module_no_collision_when_tables_clean():
    from pyfsr._testing.replay import ReplayTransport
    from pyfsr.cli.appliance.facts import Facts

    c = RecordingClient()
    admin = ModulesAdminAPI(c)
    admin.get_published = lambda module: None
    admin.get_staging = lambda module: None
    facts = Facts(ReplayTransport(tables=["widgets", "gadgets"]))  # no 'freshmod*' tables
    admin.create_module("freshmod", facts=facts, create_view_templates=False)
    assert any(m == "POST" for m, _e, _d in c.calls)  # creation proceeded


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
    assert [d.to_dict(exclude_none=True) for d in bad] == [
        {"module": "9probe", "uuid": "u-bad", "problem": "invalid module name"}
    ]
    assert bad[0]["module"] == "9probe" and bad[0].problem == "invalid module name"
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


def test_pending_changes_requests_relationships():
    """pending_changes must list with $relationships=true, else the attributes (fields)
    relationship is omitted and field-only changes are invisible (false-empty)."""
    c = RecordingClient()
    ModulesAdminAPI(c).pending_changes()
    list_gets = [p for (m, e, p) in c.calls if m == "GET" and "model_metadatas" in e]
    assert list_gets, "expected list GETs against the metadata stores"
    assert all((p or {}).get("$relationships") == "true" for p in list_gets)


def _meta(store, attr_store, *, visibility):
    """Build a staging/published module record whose only store-relative differences are
    the hypermedia @id/@type and the attribute back-reference IRIs (which must NOT count
    as a semantic change)."""
    return {
        "@id": f"/api/3/{store}/u-1",
        "@type": "ModelMetadata",
        "type": "widgets",
        "taggable": True,
        "attributes": [
            {
                "@id": f"/api/3/{attr_store}/a-1",
                "@type": "AttributeMetadata",
                "name": "payload",
                "visibility": visibility,
                "sattrib": f"/api/3/{store}/u-1",
            }
        ],
    }


def test_pending_changes_ignores_store_relative_iris():
    """Identical modules whose attribute @id/@type/sattrib differ only by store
    (staging vs published) must NOT be reported as modified."""

    class IdenticalClient(RecordingClient):
        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            if "staging_model_metadatas" in endpoint:
                return {"hydra:member": [_meta("staging_model_metadatas", "attribute_metadatas", visibility=True)]}
            return {"hydra:member": [_meta("model_metadatas", "attrib_model_metadatas", visibility=True)]}

    assert ModulesAdminAPI(IdenticalClient()).pending_changes() == []


def test_pending_changes_detects_field_only_change():
    """A field-only difference (visibility flip) nested inside attributes must be detected
    even though every top-level scalar is identical."""

    class FieldDiffClient(RecordingClient):
        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            if "staging_model_metadatas" in endpoint:
                return {"hydra:member": [_meta("staging_model_metadatas", "attribute_metadatas", visibility=False)]}
            return {"hydra:member": [_meta("model_metadatas", "attrib_model_metadatas", visibility=True)]}

    changes = ModulesAdminAPI(FieldDiffClient()).pending_changes()
    assert [c.to_dict(exclude_none=True) for c in changes] == [{"module": "widgets", "change": "modified"}]


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


def test_publish_treats_put_timeout_as_transient():
    # On some boxes the publish PUT blocks through the migrate and raises a raw
    # requests timeout instead of returning {"status":"started"}. publish() must
    # treat that as "migrate started" and confirm via /api/publish/error, not
    # propagate the timeout.
    import requests

    class TimeoutPutClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self._ts = 100

        def put(self, endpoint, data=None, params=None, **kw):
            self.calls.append(("PUT", endpoint, data))
            raise requests.exceptions.ReadTimeout("Read timed out.")

        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            if endpoint == "/api/publish/error":
                self._ts += 1
                return {"status": "Success", "last_publish_time": self._ts}
            return super().get(endpoint, params=params, **kw)

    c = TimeoutPutClient()
    result = ModulesAdminAPI(c).publish(poll_interval=0)
    assert ("PUT", "/api/publish", {}) in c.calls
    assert result["status"] == "Success"


def test_revert_puts_to_revert_endpoint():
    c = RecordingClient()
    result = ModulesAdminAPI(c).revert()
    assert ("PUT", "/api/publish/revert", {}) in c.calls
    assert result["ok"] is True


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
        self.app_config = FakeAppConfigAPI()
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


def test_delete_module_drops_orphan_tables_when_appliance_given(monkeypatch):
    c = DeleteClient(referrer=False)
    api = ModulesAdminAPI(c)
    monkeypatch.setattr(api, "publish", lambda **kw: {"ok": True})
    monkeypatch.setattr(api, "get_view_templates", lambda module: [])

    dropped_for = {}

    def fake_drop(facts, base_table, *, yes):
        dropped_for["table"] = base_table
        dropped_for["yes"] = yes
        return {"db": "venom", "dropped": [base_table, f"{base_table}_team"], "planned": []}

    monkeypatch.setattr("pyfsr.cli.appliance.db.drop_module_tables", fake_drop)

    sentinel_facts = object()
    res = api.delete_module("widgets", drop_orphan_tables=sentinel_facts)
    assert dropped_for == {"table": "widgets", "yes": True}
    assert res["dropped_tables"] == ["widgets", "widgets_team"]


def test_delete_module_remove_from_nav(monkeypatch):
    c = DeleteClient(referrer=False)
    api = ModulesAdminAPI(c)
    monkeypatch.setattr(api, "publish", lambda **kw: {"ok": True})
    monkeypatch.setattr(api, "get_view_templates", lambda module: [])
    res = api.delete_module("widgets", remove_from_nav=True)
    assert res["nav_removed"] is True
    assert c.app_config.nav_removed == [{"module": "widgets", "title": None}]


def test_delete_module_no_nav_removal_by_default(monkeypatch):
    c = DeleteClient(referrer=False)
    api = ModulesAdminAPI(c)
    monkeypatch.setattr(api, "publish", lambda **kw: {"ok": True})
    monkeypatch.setattr(api, "get_view_templates", lambda module: [])
    res = api.delete_module("widgets")
    assert res["nav_removed"] is None
    assert c.app_config.nav_removed == []


def test_delete_module_skips_table_drop_when_no_appliance(monkeypatch):
    c = DeleteClient(referrer=False)
    api = ModulesAdminAPI(c)
    monkeypatch.setattr(api, "publish", lambda **kw: {"ok": True})
    monkeypatch.setattr(api, "get_view_templates", lambda module: [])
    res = api.delete_module("widgets")
    assert res["dropped_tables"] is None


def test_delete_module_not_found_raises():
    c = DeleteClient(published=False, staging=False)
    try:
        ModulesAdminAPI(c).delete_module("widgets", publish=False)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not found" in str(e)


class MultiModuleClient:
    """In-memory staging store across several modules, for reverse-field tests."""

    def __init__(self, modules):
        # modules: {name: [attribute dicts]}
        self.staging = {
            name: {"uuid": f"u-{name}", "type": name, "module": name, "attributes": list(attrs)}
            for name, attrs in modules.items()
        }
        self.put_calls = []

    def get(self, endpoint, params=None, **kw):
        if endpoint == "/api/3/staging_model_metadatas":
            return {"hydra:member": list(self.staging.values())}
        if endpoint == "/api/3/model_metadatas":
            return {"hydra:member": []}  # nothing published
        uuid = endpoint.rsplit("/", 1)[-1]
        return next((m for m in self.staging.values() if m["uuid"] == uuid), None)

    def put(self, endpoint, data=None, params=None, **kw):
        self.put_calls.append((endpoint, data))
        # uuid is in the URL (the body is just {"attributes": [...]}); persist back
        # so a follow-up reverse add sees prior state.
        uuid = endpoint.rsplit("/", 1)[-1]
        for m in self.staging.values():
            if m["uuid"] == uuid and "attributes" in (data or {}):
                m["attributes"] = data["attributes"]
        return {"ok": True, **(data or {})}

    def post(self, endpoint, data=None, params=None, **kw):
        return {"uuid": "new", **(data or {})}


def _attrs(client, module):
    return {a["name"]: a for a in client.staging[module]["attributes"]}


def test_add_field_one_to_many_creates_target_lookup():
    c = MultiModuleClient({"incidents": [{"name": "name", "type": "string", "formType": "text"}], "alerts": []})
    api = ModulesAdminAPI(c)
    rel = api.relationship_field("relatedAlerts", "alerts", many=False, inversed_field="incident")
    api.add_field("incidents", rel)
    # the oneToMany is on incidents...
    assert "relatedAlerts" in _attrs(c, "incidents")
    # ...and pyfsr auto-created the required lookup on the alerts target
    rev = _attrs(c, "alerts")["incident"]
    assert rev["formType"] == "lookup" and rev["type"] == "incidents"
    assert rev["inversedField"] == "relatedAlerts"


def test_add_field_custom_inverse_many_to_many_creates_mirror():
    c = MultiModuleClient({"incidents": [], "alerts": []})
    api = ModulesAdminAPI(c)
    rel = api.relationship_field("relatedAlerts", "alerts", inversed_field="parentIncidents")
    api.add_field("incidents", rel)
    rev = _attrs(c, "alerts")["parentIncidents"]
    assert rev["formType"] == "manyToMany" and rev["type"] == "incidents"
    assert rev["ownsRelationship"] is False and rev["inversedField"] == "relatedAlerts"


def test_add_field_default_inverse_many_to_many_adds_no_reverse():
    c = MultiModuleClient({"incidents": [], "alerts": []})
    api = ModulesAdminAPI(c)
    api.add_field("incidents", api.relationship_field("relatedAlerts", "alerts"))
    # default inverse: platform mirrors it, so pyfsr leaves the target untouched
    assert c.staging["alerts"]["attributes"] == []


def test_add_field_lookup_adds_no_reverse():
    c = MultiModuleClient({"incidents": [], "people": []})
    api = ModulesAdminAPI(c)
    api.add_field("incidents", api.lookup_field("owner", "people"))
    assert c.staging["people"]["attributes"] == []


def test_add_field_create_reverse_false_skips_reverse():
    c = MultiModuleClient({"incidents": [], "alerts": []})
    api = ModulesAdminAPI(c)
    rel = api.relationship_field("relatedAlerts", "alerts", many=False, inversed_field="incident")
    api.add_field("incidents", rel, create_reverse=False)
    assert c.staging["alerts"]["attributes"] == []


def test_add_field_reverse_missing_target_raises():
    c = MultiModuleClient({"incidents": []})  # no 'alerts' module
    api = ModulesAdminAPI(c)
    rel = api.relationship_field("relatedAlerts", "alerts", many=False, inversed_field="incident")
    try:
        api.add_field("incidents", rel)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "does not exist" in str(e)


def test_add_field_reverse_is_idempotent():
    c = MultiModuleClient({"incidents": [], "alerts": []})
    api = ModulesAdminAPI(c)
    rel = api.relationship_field("relatedAlerts", "alerts", many=False, inversed_field="incident")
    api.add_field("incidents", rel)
    # re-adding (e.g. a retry) must not duplicate the reverse lookup
    api._ensure_reverse_field(*api._reverse_attr_for("incidents", rel), source_module="incidents", source_field=rel)
    assert sum(1 for a in c.staging["alerts"]["attributes"] if a["name"] == "incident") == 1


# --------------------------------------------------------------- grant_to / RBAC


class PublishGrantClient(RecordingClient):
    """RecordingClient whose /api/publish/error always reports a fresh Success, so publish()
    completes and its deferred-grant flush runs."""

    def __init__(self):
        super().__init__()
        self._ts = 100

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        if endpoint == "/api/publish/error":
            self._ts += 1
            return {"status": "Success", "last_publish_time": self._ts}
        return super().get(endpoint, params=params, **kw)


def test_create_module_grant_to_is_deferred_until_publish():
    # A brand-new module is staging-only, so the grant must NOT fire at create time (it would
    # 404 against /api/3/modules) — it is recorded and applied on the next publish().
    c = PublishGrantClient()
    admin = ModulesAdminAPI(c)
    admin.create_module("widgets", label="Widget", grant_to="Full App Permissions", create_view_templates=False)
    assert c.roles.grant_calls == []  # nothing granted yet
    assert admin._pending_grants == {"widgets": ["Full App Permissions"]}

    admin.publish(poll_interval=0)
    assert len(c.roles.grant_calls) == 1
    grant = c.roles.grant_calls[0]
    assert grant["role"] == "Full App Permissions" and grant["module"] == "widgets"
    assert all(grant[k] for k in ("can_read", "can_create", "can_update", "can_delete", "can_execute"))
    assert admin._pending_grants == {}  # cleared after flushing


def test_create_module_grant_to_multiple_roles_flush_on_publish():
    c = PublishGrantClient()
    admin = ModulesAdminAPI(c)
    admin.create_module(
        "widgets", label="Widget", grant_to=["Full App Permissions", "SOC Analyst"], create_view_templates=False
    )
    assert c.roles.grant_calls == []
    admin.publish(poll_interval=0)
    assert {g["role"] for g in c.roles.grant_calls} == {"Full App Permissions", "SOC Analyst"}
    assert {g["module"] for g in c.roles.grant_calls} == {"widgets"}


def test_publish_with_no_pending_grants_grants_nothing():
    c = PublishGrantClient()
    ModulesAdminAPI(c).publish(poll_interval=0)
    assert c.roles.grant_calls == []


def test_create_module_without_grant_to_no_pending():
    c = RecordingClient()
    admin = ModulesAdminAPI(c)
    admin.create_module("widgets", create_view_templates=False)
    assert admin._pending_grants == {}
    assert len(c.roles.grant_calls) == 0


def test_create_module_with_empty_grant_to_list_no_pending():
    c = RecordingClient()
    admin = ModulesAdminAPI(c)
    admin.create_module("widgets", grant_to=[], create_view_templates=False)
    # Empty list means no grant intent recorded
    assert admin._pending_grants == {}
    assert len(c.roles.grant_calls) == 0


def test_create_module_add_to_nav_is_deferred_until_publish():
    # A new module is staging-only; the nav entry routes to a live module and gates on a
    # permission that only resolves post-publish, so it must be deferred like grants.
    c = PublishGrantClient()
    admin = ModulesAdminAPI(c)
    admin.create_module("widgets", label="Widget", add_to_nav=True, create_view_templates=False)
    assert c.app_config.nav_calls == []  # nothing added yet
    assert admin._pending_nav == {"widgets": {"title": "Widget", "icon": None, "parent": None, "position": "bottom"}}

    admin.publish(poll_interval=0)
    assert len(c.app_config.nav_calls) == 1
    call = c.app_config.nav_calls[0]
    # Default: new top-level section at the bottom, gated by read on the module.
    assert call["parent"] is None and call["position"] == "bottom"
    item = call["item"]
    assert item.title == "Widget"
    assert item.icon == "icon icon-bookmark"
    assert item.state.parameters == {"module": "widgets"}
    assert item.require.module == "widgets" and item.require.action == "read"
    assert admin._pending_nav == {}  # cleared after flushing


def test_create_module_add_to_nav_custom_placement():
    c = PublishGrantClient()
    admin = ModulesAdminAPI(c)
    admin.create_module(
        "widgets",
        label="Widget",
        add_to_nav=True,
        nav_title="My Widgets",
        nav_icon="icon icon-star",
        nav_parent="Incident Response",
        nav_position="top",
        create_view_templates=False,
    )
    admin.publish(poll_interval=0)
    call = c.app_config.nav_calls[0]
    assert call["parent"] == "Incident Response" and call["position"] == "top"
    assert call["item"].title == "My Widgets" and call["item"].icon == "icon icon-star"


def test_create_module_without_add_to_nav_no_pending():
    c = RecordingClient()
    admin = ModulesAdminAPI(c)
    admin.create_module("widgets", create_view_templates=False)
    assert admin._pending_nav == {}
    assert len(c.app_config.nav_calls) == 0


def test_permission_error_includes_rbac_hint():
    """Test that 403 PermissionError messages are enriched with RBAC guidance."""
    from pyfsr.exceptions import PermissionError, handle_api_error

    # Mock a 403 response from /api/3/records/widgets/
    class MockResponse:
        status_code = 403
        url = "/api/3/records/widgets/create"

        def json(self):
            return {"message": "Access Denied"}

    response = MockResponse()
    try:
        handle_api_error(response)
        assert False, "expected PermissionError"
    except PermissionError as e:
        # Message should contain both the original error and the RBAC hint
        msg = str(e)
        assert "Access Denied" in msg
        assert "newly created module" in msg
        assert "grant_module_permissions" in msg
        assert "grant_to=" in msg
        # The hint should extract the module name from the URL
        assert "widgets" in msg


def test_permission_error_hint_without_module_in_url():
    """Test RBAC hint is included even when module can't be extracted from URL."""
    from pyfsr.exceptions import PermissionError, handle_api_error

    # Mock a 403 response from a URL without a clear module name
    class MockResponse:
        status_code = 403
        url = "/api/3/roles"

        def json(self):
            return {"message": "Access Denied"}

    response = MockResponse()
    try:
        handle_api_error(response)
        assert False, "expected PermissionError"
    except PermissionError as e:
        # Hint should still be present, but without module name extraction
        msg = str(e)
        assert "Access Denied" in msg
        assert "newly created module" in msg
        assert "grant_module_permissions(role, module='<module>')" in msg


def test_publish_status_reads_400_body():
    """`/api/publish/error` returns HTTP 400 (with a usable body) when a prior publish
    error is on record. _publish_status must read it via raise_on_status=False, not
    swallow it the way a plain client.get (raise_on_status=True) would."""

    class FourHundredResp:
        status_code = 400

        def json(self):
            return {"status": "Fail", "last_publish_time": 777}

    class Client400(RecordingClient):
        def get(self, endpoint, params=None, raise_on_status=True, **kw):
            self.calls.append(("GET", endpoint, params))
            if endpoint == "/api/publish/error":
                # Mimic the client contract: a 4xx raises unless raise_on_status=False,
                # in which case the raw Response is returned for the caller to inspect.
                if raise_on_status:
                    raise RuntimeError("400 Bad Request")
                return FourHundredResp()
            return super().get(endpoint, params=params, **kw)

    api = ModulesAdminAPI(Client400())
    assert api._publish_status() == {"status": "Fail", "last_publish_time": 777}
    assert api._last_publish_time() == 777


def test_publish_noop_skips_wait_when_nothing_pending():
    """With no pending changes, publish() still PUTs but must NOT enter the poll loop
    (which would block for the full timeout waiting for a migrate that never runs)."""

    class NoopClient(RecordingClient):
        def get(self, endpoint, params=None, raise_on_status=True, **kw):
            self.calls.append(("GET", endpoint, params))
            if endpoint == "/api/publish/error":
                return {"status": "Success", "last_publish_time": 1}
            return super().get(endpoint, params=params, **kw)

    c = NoopClient()
    # staging == published (RecordingClient mirrors widgets in both) → pending_changes == []
    result = ModulesAdminAPI(c).publish(poll_interval=0)
    assert ("PUT", "/api/publish", {}) in c.calls
    pe_reads = sum(1 for m, ep, _ in c.calls if m == "GET" and ep == "/api/publish/error")
    # short-circuit path reads publish/error at most twice (prev_time + final status),
    # never the unbounded poll loop.
    assert pe_reads <= 2
    assert result["status"] == "Success"


def test_publish_completes_metadata_only_change_without_timeout():
    """A field-only publish (visibility/required) commits with NO 503 outage and WITHOUT
    advancing last_publish_time. The wait must still detect completion via staging clearing
    (pending_changes going empty), not block until timeout."""

    class MetaOnlyClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.published = False  # flips True after the publish PUT

        def get(self, endpoint, params=None, raise_on_status=True, **kw):
            self.calls.append(("GET", endpoint, params))
            if endpoint == "/api/publish/error":
                # status Success but last_publish_time NEVER advances (metadata-only).
                return {"status": "Success", "last_publish_time": 5}
            if "staging_model_metadatas" in endpoint:
                vis = self.published  # before publish staging differs; after it matches
                return {"hydra:member": [{"type": "widgets", "visibility": vis}]}
            if "model_metadatas" in endpoint:
                return {"hydra:member": [{"type": "widgets", "visibility": True}]}
            return super().get(endpoint, params=params, **kw)

        def put(self, endpoint, data=None, params=None, **kw):
            self.calls.append(("PUT", endpoint, data))
            if endpoint == "/api/publish":
                self.published = True
            return {"ok": True, **(data or {})}

    c = MetaOnlyClient()
    result = ModulesAdminAPI(c).publish(precheck=False, timeout=5, poll_interval=0)
    assert result["status"] == "Success"
    assert ("PUT", "/api/publish", {}) in c.calls


class _JsonResp:
    def __init__(self, body, status_code=400):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


def test_wait_for_publish_treats_unchanged_error_log_as_success():
    """A box with a persistent stale Fail log: after riding the outage and advancing
    last_publish_time, an UNCHANGED errors text means the logged failure is stale → success."""
    from pyfsr.exceptions import FortiSOARException

    class StaleClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.n = 0

        def get(self, endpoint, params=None, raise_on_status=True, **kw):
            self.calls.append(("GET", endpoint, params))
            if endpoint == "/api/publish/error":
                self.n += 1
                if self.n <= 2:
                    raise RuntimeError("503 migrate outage")  # -> _publish_status None -> saw_outage
                return _JsonResp({"status": "Fail", "last_publish_time": 200, "errors": "OLD"})
            return super().get(endpoint, params=params, **kw)

    api = ModulesAdminAPI(StaleClient())
    body = api._wait_for_publish(prev_time=100, timeout=5, poll_interval=0, prev_errors="OLD")
    assert body["last_publish_time"] == 200  # returned as success despite status=Fail
    # sanity: a fresh (changed) error log on the same shape DOES raise
    try:
        api2 = ModulesAdminAPI(StaleClient())
        api2._wait_for_publish(prev_time=100, timeout=5, poll_interval=0, prev_errors="DIFFERENT-OLD")
        raised = False
    except FortiSOARException:
        raised = True
    assert raised


def test_wait_for_publish_raises_on_fresh_error_log():
    """A changed errors text (vs. before the PUT) is a real, fresh failure → raise."""
    from pyfsr.exceptions import FortiSOARException

    class FreshFailClient(RecordingClient):
        def get(self, endpoint, params=None, raise_on_status=True, **kw):
            self.calls.append(("GET", endpoint, params))
            if endpoint == "/api/publish/error":
                return _JsonResp({"status": "Fail", "last_publish_time": 200, "errors": "NEW ERROR"})
            return super().get(endpoint, params=params, **kw)

    api = ModulesAdminAPI(FreshFailClient())
    try:
        api._wait_for_publish(prev_time=100, timeout=5, poll_interval=0, prev_errors="OLD")
        raised = False
    except FortiSOARException:
        raised = True
    assert raised


# -- get_or_create_module (idempotent ensure-state) -------------------------
class _FakeStub:
    """Minimal client; get_or_create_module is exercised via monkeypatched API methods."""

    def __init__(self):
        self.roles = FakeRolesAPI()


def _api_with_tracking(monkeypatch, *, published, staging):
    api = ModulesAdminAPI(_FakeStub())
    calls = {"create": 0, "publish": 0}
    monkeypatch.setattr(api, "get_published", lambda m, **k: published)
    monkeypatch.setattr(api, "get_staging", lambda m, **k: staging)

    def _create(m, **kw):
        calls["create"] += 1
        return {"uuid": "new", "type": m}

    def _publish(**kw):
        calls["publish"] += 1
        return {"status": "started"}

    monkeypatch.setattr(api, "create_module", _create)
    monkeypatch.setattr(api, "publish", _publish)
    return api, calls


def test_get_or_create_module_existing_published_no_side_effects(monkeypatch):
    api, calls = _api_with_tracking(monkeypatch, published={"type": "widgets", "uuid": "p"}, staging=None)
    meta, created = api.get_or_create_module("widgets")
    assert created is False
    assert meta["uuid"] == "p"
    assert calls == {"create": 0, "publish": 0}  # nothing created, nothing published


def test_get_or_create_module_existing_staging_only_not_force_published(monkeypatch):
    api, calls = _api_with_tracking(monkeypatch, published=None, staging={"type": "widgets", "uuid": "s"})
    meta, created = api.get_or_create_module("widgets")
    assert created is False
    assert meta["uuid"] == "s"
    assert calls["publish"] == 0  # appliance-wide publish not triggered for an existing draft


def test_get_or_create_module_creates_and_publishes_when_absent(monkeypatch):
    # absent on first look; after create+publish, published metadata is available.
    states = {"published": None}
    api = ModulesAdminAPI(_FakeStub())
    calls = {"create": 0, "publish": 0}
    monkeypatch.setattr(api, "get_published", lambda m, **k: states["published"])
    monkeypatch.setattr(api, "get_staging", lambda m, **k: None)

    def _create(m, **kw):
        calls["create"] += 1

    def _publish(**kw):
        calls["publish"] += 1
        states["published"] = {"type": "widgets", "uuid": "live"}

    monkeypatch.setattr(api, "create_module", _create)
    monkeypatch.setattr(api, "publish", _publish)

    meta, created = api.get_or_create_module("widgets", fields=[{"name": "name"}])
    assert created is True
    assert meta["uuid"] == "live"
    assert calls == {"create": 1, "publish": 1}


def test_get_or_create_module_no_publish_returns_staging(monkeypatch):
    states = {"staging": None}
    api = ModulesAdminAPI(_FakeStub())
    calls = {"create": 0, "publish": 0}
    monkeypatch.setattr(api, "get_published", lambda m, **k: None)
    monkeypatch.setattr(api, "get_staging", lambda m, **k: states["staging"])

    def _create(m, **kw):
        calls["create"] += 1
        states["staging"] = {"type": "widgets", "uuid": "stg"}

    monkeypatch.setattr(api, "create_module", _create)
    monkeypatch.setattr(api, "publish", lambda **k: calls.__setitem__("publish", calls["publish"] + 1))

    meta, created = api.get_or_create_module("widgets", publish=False)
    assert created is True
    assert meta["uuid"] == "stg"
    assert calls["publish"] == 0


# ---------------------------------------------------------------------------
# T3.7 — builders return typed AttributeMetadata (dict-compatible), consumers
# still POST plain wire dicts (byte-identical to the pre-typing behavior).
# ---------------------------------------------------------------------------

from pyfsr.models import AttributeMetadata  # noqa: E402


def test_builders_return_typed_attribute_metadata():
    assert isinstance(ModulesAdminAPI.field("a"), AttributeMetadata)
    assert isinstance(ModulesAdminAPI.text_field("b"), AttributeMetadata)
    assert isinstance(ModulesAdminAPI.integer_field("c"), AttributeMetadata)
    assert isinstance(ModulesAdminAPI.picklist_field("d", "AlertSeverity"), AttributeMetadata)
    assert isinstance(ModulesAdminAPI.lookup_field("e", "people"), AttributeMetadata)
    assert isinstance(ModulesAdminAPI.relationship_field("f", "alerts"), AttributeMetadata)


def test_typed_field_is_dict_compatible_for_reads():
    f = ModulesAdminAPI.text_field("name", required=True, label="Name")
    # __getitem__, get, __contains__ all behave like the old dict
    assert f["type"] == "string"
    assert f["formType"] == "text"
    assert f.get("name") == "name"
    assert "validation" in f
    assert f["validation"]["required"] is True


def test_create_module_posts_plain_dict_attributes():
    c = RecordingClient()
    api = ModulesAdminAPI(c)
    api.create_module(
        "widgets",
        fields=[api.text_field("name", required=True), api.picklist_field("sev", "AlertSeverity")],
        create_view_templates=False,
    )
    _, _, data = c.calls[-1]
    attrs = data["attributes"]
    # every posted attribute is a plain dict, not a model
    assert all(type(a) is dict for a in attrs)
    assert [a["name"] for a in attrs] == ["name", "sev"]


def test_add_field_accepts_typed_field():
    c = RecordingClient()
    # seed a staging module so add_field can append
    ModulesAdminAPI(c).create_module("widgets", create_view_templates=False)
    api = ModulesAdminAPI(c)
    # staging lookup is via get; RecordingClient returns {} so stub get_staging
    api.get_staging = lambda m: {"@id": "/api/3/staging_model_metadatas/x", "attributes": []}
    api._put_attributes = lambda mod, attrs: {"attributes": attrs}
    result = api.add_field("widgets", api.integer_field("count"), create_reverse=False)
    posted = result["attributes"]
    assert all(type(a) is dict for a in posted)
    assert posted[-1]["name"] == "count" and posted[-1]["type"] == "integer"


def test_scope_field_to_teams_accepts_and_returns_typed():
    api = ModulesAdminAPI(TeamScopeClient())
    f = api.relationship_field("approvers", "people")
    scoped = api.scope_field_to_teams(f, ["TeamA", "SOC Team"])
    assert isinstance(scoped, AttributeMetadata)
    assert scoped["dataSourceFilters"]["showTeams"] is True
    assert scoped["dataSourceFilters"]["teams"] == ["TeamA", "SOC Team"]


def test_grid_column_default_is_per_type():
    # scalar / lookup / picklist fields are grid columns by default (visible in the list view)
    visible = [
        ModulesAdminAPI.text_field("a"),
        ModulesAdminAPI.integer_field("b"),
        ModulesAdminAPI.decimal_field("c"),
        ModulesAdminAPI.datetime_field("d"),
        ModulesAdminAPI.checkbox_field("e"),
        ModulesAdminAPI.email_field("f"),
        ModulesAdminAPI.picklist_field("g", "SomePicklist"),
        ModulesAdminAPI.lookup_field("h", "alerts"),
    ]
    assert all(f["gridColumn"] is True for f in visible), [f["name"] for f in visible if f["gridColumn"] is not True]

    # password, blob, and collection-relationship types are never grid columns by default
    hidden = [
        ModulesAdminAPI.password_field("p"),
        ModulesAdminAPI.object_field("o"),
        ModulesAdminAPI.json_field("j"),
        ModulesAdminAPI.relationship_field("m2m", "alerts", many=True),
        ModulesAdminAPI.relationship_field("o2m", "alerts", many=False),
    ]
    assert all(f["gridColumn"] is False for f in hidden), [f["name"] for f in hidden if f["gridColumn"] is not False]

    # an explicit grid_column always wins, in either direction
    assert ModulesAdminAPI.text_field("x", grid_column=False)["gridColumn"] is False
    assert ModulesAdminAPI.password_field("y", grid_column=True)["gridColumn"] is True
