"""Unit tests for the data-ingestion wizard (ConnectorsAPI.data_ingest_wizard).

The rewrite helpers are pure functions and are tested directly; the wizard
itself is driven against a fake client that records every write, so the test
asserts the *sequence and shape* of what the UI would have sent.
"""

import pytest

from pyfsr.api.connectors import (
    ConnectorsAPI,
    _patch_clone_references,
    _rewrite_ingestion_playbook,
)

CONFIG_ID = "e02562a9-a719-4081-ac83-3dfebbb66422"
SUFFIX = CONFIG_ID.replace("-", "_")

_CONFIGURED = {
    "data": [
        {
            "id": 33,
            "name": "fortinet-fortisiem",
            "version": "6.1.1",
            "label": "Fortinet FortiSIEM",
            "configuration": [
                {"id": 5, "config_id": CONFIG_ID, "name": "prod", "default": True},
                {"id": 6, "config_id": "other-cfg", "name": "staging", "default": False},
            ],
        }
    ]
}

_FETCH_PB = {
    "uuid": "81b1d18e-1188-46fd-bd63-158ce5513983",
    "name": "> FortiSIEM > Fetch",
    "@id": "/api/3/workflows/81b1d18e-1188-46fd-bd63-158ce5513983",
    "recordTags": ["dataingestion", "fetch", "fortinet-fortisiem"],
}
_INGEST_PB = {
    "uuid": "81b87e79-b43e-44da-9959-454f3cee348b",
    "name": "FortiSIEM > Ingest",
    "@id": "/api/3/workflows/81b87e79-b43e-44da-9959-454f3cee348b",
    "recordTags": ["create", "dataingestion", "fortinet-fortisiem", "ingest"],
}


# --------------------------------------------------------------- helpers
def test_rewrite_namespaces_global_vars_per_config():
    body = {
        "name": "Ingest",
        "steps": [
            {"name": "Get Macro", "arguments": {"value": "{{globalVars.LastPullTime}}"}},
        ],
    }
    out = _rewrite_ingestion_playbook(body, "fortinet-fortisiem", CONFIG_ID, SUFFIX, None)
    assert out["steps"][0]["arguments"]["value"] == f"{{{{globalVars.LastPullTime_{SUFFIX}}}}}"


def test_rewrite_also_renames_bare_variable_name_strings():
    """A Set-Variable step names the global as a bare string, not a globalVars ref."""
    body = {
        "steps": [
            {"name": "Read", "arguments": {"value": "{{globalVars.LastPullTime}}"}},
            {"name": "Set", "arguments": {"key": "LastPullTime"}},
        ]
    }
    out = _rewrite_ingestion_playbook(body, "fortinet-fortisiem", CONFIG_ID, SUFFIX, None)
    assert out["steps"][1]["arguments"]["key"] == f"LastPullTime_{SUFFIX}"


def test_rewrite_binds_only_this_connectors_steps():
    """cyops_utilities steps must keep config unset — matching a live collection."""
    body = {
        "steps": [
            {"name": "Fetch and Create", "arguments": {"connector": "fortinet-fortisiem"}},
            {"name": "Get Macro Value", "arguments": {"connector": "cyops_utilities"}},
            {"name": "Set Variable", "arguments": {}},
        ]
    }
    out = _rewrite_ingestion_playbook(body, "fortinet-fortisiem", CONFIG_ID, SUFFIX, None)
    assert out["steps"][0]["arguments"]["config"] == CONFIG_ID
    assert "config" not in out["steps"][1]["arguments"]
    assert "config" not in out["steps"][2]["arguments"]


def test_rewrite_stamps_agent_only_when_given():
    body = {"steps": [{"name": "Fetch", "arguments": {"connector": "fortinet-fortisiem"}}]}
    assert (
        "agent"
        not in _rewrite_ingestion_playbook(body, "fortinet-fortisiem", CONFIG_ID, SUFFIX, None)["steps"][0]["arguments"]
    )
    out = _rewrite_ingestion_playbook(body, "fortinet-fortisiem", CONFIG_ID, SUFFIX, "edge-1")
    assert out["steps"][0]["arguments"]["agent"] == "edge-1"


def test_patch_points_create_pb_id_at_the_clone():
    pb = {"steps": [{"name": "Fetch and Create", "arguments": {"params": {"create_pb_id": "old-create"}}}]}
    steps = _patch_clone_references(pb, {}, "new-create")
    assert steps[0]["arguments"]["params"]["create_pb_id"] == "new-create"


def test_patch_remaps_cross_playbook_references():
    pb = {"steps": [{"name": "Reference", "arguments": {"step_iri": "/api/3/workflows/old-uuid"}}]}
    steps = _patch_clone_references(pb, {"old-uuid": "new-uuid"}, None)
    assert steps[0]["arguments"]["step_iri"] == "/api/3/workflows/new-uuid"


def test_patch_returns_none_when_nothing_changes():
    pb = {"steps": [{"name": "Plain", "arguments": {}}]}
    assert _patch_clone_references(pb, {}, None) is None


# --------------------------------------------------------------- bucketing
def test_bucket_by_tag_lets_one_playbook_fill_several_roles():
    """FortiSIEM's Ingest playbook is tagged both `ingest` and `create`."""
    buckets = ConnectorsAPI._bucket_by_tag([_FETCH_PB, _INGEST_PB])
    assert buckets.fetch["name"] == "> FortiSIEM > Fetch"
    assert buckets.ingest["uuid"] == buckets.create["uuid"] == _INGEST_PB["uuid"]
    assert buckets.missing() == ["update"]


def test_bucket_by_tag_matches_hashtag_string_form():
    pb = {"uuid": "x", "tag": "#fortinet-fortisiem #ingest #dataingestion", "recordTags": []}
    assert ConnectorsAPI._bucket_by_tag([pb]).ingest["uuid"] == "x"


# --------------------------------------------------------------- the wizard
class FakeClient:
    """Records writes; serves just enough reads for the wizard to run."""

    def __init__(self, *, health="Available", existing_ingestion=None, collection_exists=False):
        self.posts = []
        self.puts = []
        self.gets = []
        self._health = health
        self._existing = existing_ingestion or []
        self._collection_exists = collection_exists
        self.playbooks = FakePlaybooks(self)
        self.schedules = FakeSchedules(self)

    def get(self, endpoint, params=None, **kw):
        self.gets.append((endpoint, params))
        if "healthcheck" in endpoint:
            return {"status": self._health}
        if endpoint.startswith("/api/integration/connectors/"):
            return _CONFIGURED
        if endpoint == "/api/integration/data-import/":
            return {"data": []}
        if endpoint.startswith("/api/3/workflow_collections/"):
            if self._collection_exists:
                return {"uuid": CONFIG_ID, "name": "existing"}
            raise RuntimeError("404")
        if endpoint == "/api/3/workflow_collections":
            return {
                "hydra:member": [
                    {"name": "Sample - Fortinet FortiSIEM - 6.1.1", "uuid": "sample-col"},
                    {"name": "Sample - VirusTotal - 3.2.1", "uuid": "vt-col"},
                ]
            }
        if endpoint == "/api/3/actors/current":
            return {"uuid": "actor-1"}
        return {}

    def post(self, endpoint, data=None, params=None, **kw):
        self.posts.append((endpoint, data))
        if endpoint.startswith("/api/query/workflows"):
            scoped = (data["filters"][0]["value"],)
            if scoped[0] == CONFIG_ID:
                return {"hydra:member": self._existing}
            return {"hydra:member": [_FETCH_PB, _INGEST_PB]}
        if endpoint == "/api/3/workflow_collections":
            return {"uuid": CONFIG_ID, "name": data["name"]}
        return {}

    def put(self, endpoint, data=None, params=None, **kw):
        self.puts.append((endpoint, data))
        return {}

    def delete(self, endpoint, params=None, **kw):
        return None


class FakePlaybooks:
    def __init__(self, client):
        self.client = client
        self.clones = []

    def clone(self, uuid, new_name, *, collection=None, is_active=False, transform=None):
        body = {"uuid": uuid, "name": new_name, "steps": [], "collection": collection}
        if transform:
            body = transform(body) or body
        created = dict(body, uuid=f"clone-of-{uuid}")
        self.clones.append(created)
        return created

    def update(self, uuid, **fields):
        self.client.puts.append((f"/api/3/workflows/{uuid}", fields))
        return {"uuid": uuid, **fields}


class FakeSchedules:
    def __init__(self, client):
        self.client = client
        self.created = []

    def create(self, name, workflow_iri, cron, **kw):
        self.created.append({"name": name, "wf_iri": workflow_iri, "cron": cron, **kw})
        return {"id": "sched-1", "name": name}


def _api(**kw):
    c = FakeClient(**kw)
    return ConnectorsAPI(c), c


def test_wizard_end_to_end_builds_collection_playbooks_schedule_and_metadata():
    api, client = _api()
    out = api.data_ingest_wizard("fortinet-fortisiem", config="prod", cron="*/15 * * * *")

    # collection uuid IS the config_id — the identity the UI relies on
    assert out.collection_uuid == CONFIG_ID
    created_col = next(d for e, d in client.posts if e == "/api/3/workflow_collections")
    assert created_col["uuid"] == CONFIG_ID and created_col["visible"] is False
    assert created_col["name"] == f"Fortinet FortiSIEM 6.1.1 prodIngestion({CONFIG_ID})"

    # both sample playbooks cloned (ingest/create are the same record -> one clone)
    assert len(client.playbooks.clones) == 2
    assert out.cloned is True

    # schedule fires the cloned ingest playbook
    sched = client.schedules.created[0]
    # the platform's own naming convention -- the UI matches ingestion on this string
    assert sched["name"] == f"Ingestion_fortinet-fortisiem_prod_{CONFIG_ID}"
    assert sched["cron"] == "*/15 * * * *"
    assert sched["wf_iri"] == f"/api/3/workflows/clone-of-{_INGEST_PB['uuid']}"
    assert out.schedule_id == "sched-1" and out.scheduled is True

    # data-import metadata links config -> schedule
    meta = next(d for e, d in client.posts if e == "/api/integration/data-import/")
    assert meta["configuration"] == CONFIG_ID
    assert meta["metadata"]["scheduleId"] == "sched-1"
    assert meta["metadata"]["scheduleStatus"] is True
    assert meta["connector"] == {"name": "fortinet-fortisiem", "version": "6.1.1"}


def test_wizard_activates_the_clones():
    api, client = _api()
    api.data_ingest_wizard("fortinet-fortisiem", config="prod", cron="*/15 * * * *")
    assert any(fields.get("isActive") is True for _, fields in client.puts)


def test_wizard_without_cron_builds_playbooks_but_no_schedule():
    api, client = _api()
    out = api.data_ingest_wizard("fortinet-fortisiem", config="prod")
    assert client.schedules.created == []
    assert out.scheduled is False and out.schedule_id is None
    assert out.cloned is True


def test_wizard_refuses_unhealthy_config_by_default():
    api, _ = _api(health="Disconnected")
    with pytest.raises(ValueError, match="not 'Available'"):
        api.data_ingest_wizard("fortinet-fortisiem", config="prod")


def test_wizard_health_gate_can_be_overridden():
    api, _ = _api(health="Disconnected")
    out = api.data_ingest_wizard("fortinet-fortisiem", config="prod", require_health=False)
    assert out.health_status == "Disconnected" and out.cloned is True


def test_wizard_rejects_unknown_config_name():
    api, _ = _api()
    with pytest.raises(ValueError, match="no configuration named"):
        api.data_ingest_wizard("fortinet-fortisiem", config="nope")


def test_wizard_accepts_a_config_id_as_well_as_a_name():
    api, _ = _api()
    out = api.data_ingest_wizard("fortinet-fortisiem", config=CONFIG_ID)
    assert out.config_name == "prod"


def test_wizard_defaults_to_the_default_configuration():
    api, _ = _api()
    assert api.data_ingest_wizard("fortinet-fortisiem").config_name == "prod"


def test_wizard_reuses_existing_ingestion_playbooks():
    api, client = _api(existing_ingestion=[_FETCH_PB, _INGEST_PB], collection_exists=True)
    out = api.data_ingest_wizard("fortinet-fortisiem", config="prod", cron="0 * * * *")
    assert out.cloned is False
    assert client.playbooks.clones == []
    assert client.schedules.created[0]["wf_iri"] == _INGEST_PB["@id"]


def test_wizard_reclones_when_reuse_disabled():
    api, client = _api(existing_ingestion=[_FETCH_PB, _INGEST_PB], collection_exists=True)
    out = api.data_ingest_wizard("fortinet-fortisiem", config="prod", reuse_existing=False)
    assert out.cloned is True and len(client.playbooks.clones) == 2


def test_wizard_dry_run_writes_nothing():
    api, client = _api()
    out = api.data_ingest_wizard("fortinet-fortisiem", config="prod", cron="0 * * * *", dry_run=True)
    assert out.dry_run is True
    assert client.playbooks.clones == [] and client.schedules.created == []
    assert not [e for e, _ in client.posts if e.startswith("/api/3/workflow_collections")]
    assert not [e for e, _ in client.posts if e == "/api/integration/data-import/"]


def test_wizard_errors_when_connector_has_no_configuration():
    api, client = _api()
    client.get = lambda endpoint, params=None, **kw: (  # type: ignore[method-assign]
        {"status": "Available"}
        if "healthcheck" in endpoint
        else {"data": [{"id": 1, "name": "fortinet-fortisiem", "version": "6.1.1", "configuration": []}]}
    )
    with pytest.raises(ValueError, match="has no configuration"):
        api.data_ingest_wizard("fortinet-fortisiem")


# --------------------------------------------------------------- small APIs
def test_dependencies_status():
    api, client = _api()
    client.get = lambda endpoint, params=None, **kw: (  # type: ignore[method-assign]
        {"dependencies_installed": True}
        if "dependencies_check" in endpoint
        else (_CONFIGURED if endpoint.startswith("/api/integration/connectors/") else {})
    )
    assert api.dependencies_status("fortinet-fortisiem", version="6.1.1").dependencies_installed is True


def test_ingestion_metadata_exposes_schedule_id():
    api, client = _api()
    client.get = lambda endpoint, params=None, **kw: {  # type: ignore[method-assign]
        "data": [{"configuration": CONFIG_ID, "metadata": {"scheduleId": "sched-9"}}]
    }
    assert api.ingestion_metadata(CONFIG_ID)[0].schedule_id == "sched-9"


def test_set_operation_roles_picks_verb_by_replace():
    api, client = _api()
    api.set_operation_roles("op-1", ["role-a"], replace=True)
    assert client.puts[-1][0] == "/api/integration/connectors/operations/op-1/roles/"
    api.set_operation_roles("op-1", ["role-a"], replace=False)
    assert client.posts[-1][0] == "/api/integration/connectors/operations/op-1/roles/"
