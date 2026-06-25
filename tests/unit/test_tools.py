"""Unit tests for the agent tool registry and dispatch."""

import pytest

from pyfsr import tools
from pyfsr.exceptions import ResourceNotFoundError
from pyfsr.pagination import HydraPage


class FakeRecordSet:
    def __init__(self, module, store):
        self.module = module
        self.store = store

    def get(self, ref):
        return {"@id": f"/api/3/{self.module}/{ref}", "uuid": ref, "name": "rec", "junk": 1}

    def search(self, term="", limit=30):
        return HydraPage(members=[{"uuid": "a", "name": "x", "junk": 1}], total=1, page=1, limit=limit, raw={})

    def query(self, body):
        self.store["query_body"] = body
        return HydraPage(members=[{"uuid": "b", "name": "y"}], total=1, page=1, limit=30, raw={})

    def create(self, data, resolve_picklists=False):
        self.store["created"] = (data, resolve_picklists)
        return {"uuid": "new", **data}

    def update(self, ref, data, resolve_picklists=False):
        self.store["updated"] = (ref, data)
        return {"uuid": ref, **data}

    def delete(self, ref, hard=False):
        self.store["deleted"] = (ref, hard)

    def upsert(self, data, key=None, resolve_picklists=False):
        self.store["upserted"] = (data, key)
        return {"uuid": "ups", **data}

    def get_or_create(self, data, key="uuid", resolve_picklists=False):
        self.store["goc"] = (data, key)
        return ({"uuid": "goc", **data}, True)


class FakePicklists:
    def list(self):
        return ["Severity", "AlertStatus"]

    def values(self, name):
        return [{"itemValue": "High", "uuid": "h", "iri": "/api/3/picklists/h"}]

    def resolve(self, value, picklist=None, module=None, field=None):
        return "/api/3/picklists/h" if value == "High" else None


class FakeConnectors:
    def list_configured(self):
        return [{"name": "virustotal", "version": "1.0.0", "configurations": []}]

    def healthcheck(self, connector, config=None):
        return {"name": connector, "status": "Available"}

    def execute(self, connector, operation, params=None, config_name=None):
        return {"operation": operation, "status": "Success", "data": {"params": params}}

    def default_config(self, connector, version=None):
        return {"server": "", "verify_ssl": True}

    def validate_config(self, connector, config, version=None):
        class Result:
            valid = True
            missing = []
            invalid = []
            unknown = ["rogue_key"]
            errors = []

        return Result()

    def create_configuration(self, connector, config, *, name, **kwargs):
        return {"config_id": "c1", "name": name, "connector": connector}

    def update_configuration(self, connector, config_id, config, *, name, **kwargs):
        return {"config_id": config_id, "name": name, "connector": connector}

    def upsert_configuration(self, connector, config, *, name, **kwargs):
        return {"config_id": "c1", "name": name, "connector": connector}


class FakePlaybooks:
    def execution_history(self, playbook=None, limit=20):
        return [{"name": playbook or "any", "status": "finished"}]

    def get_execution(self, run_pk):
        return {"pk": run_pk, "status": "finished"}

    def last_run(self, playbook=None, playbook_uuid=None):
        return {"pk": "100", "name": playbook or "pb", "status": "finished"}

    def why_failed(self, playbook=None, playbook_uuid=None):
        return {"status": "failed", "failing_step": "enrich", "error_message": "boom", "pk": "100"}

    def wait_for_run(self, playbook=None, playbook_uuid=None, since=None, timeout=120, poll_interval=3):
        return {"pk": "100", "name": playbook or "pb", "status": "finished"}


class FakeModulesAdmin:
    def __init__(self, store):
        self.store = store

    def create_module(self, module, **kwargs):
        self.store["create_module"] = (module, kwargs)
        return {"module": module, "staging": True}

    def delete_module(self, module, **kwargs):
        self.store["delete_module"] = (module, kwargs)
        return {"module": module, "published": kwargs.get("publish", True)}

    def publish(self, **kwargs):
        self.store["publish"] = kwargs
        return {"status": "Success", "last_publish_time": "now"}


class FakeClient:
    def __init__(self):
        self.store = {}
        self.picklists = FakePicklists()
        self.connectors = FakeConnectors()
        self.playbooks = FakePlaybooks()
        self.modules_admin = FakeModulesAdmin(self.store)

    def records(self, module, **kwargs):
        return FakeRecordSet(module, self.store)

    def list_modules(self, refresh=False):
        return [{"type": "alerts", "label": "Alerts", "plural": "alerts"}]

    def describe_module(self, module, refresh=False):
        return {"module": module, "fields": []}


@pytest.fixture
def client():
    return FakeClient()


# -- registry / schema shape ------------------------------------------------
def test_registry_covers_core_ops():
    names = {t.name for t in tools.list_tools()}
    for expected in {
        "list_modules",
        "describe_module",
        "get_record",
        "search_records",
        "query_records",
        "create_record",
        "update_record",
        "delete_record",
        "list_picklists",
        "get_picklist_values",
        "resolve_picklist",
        "list_connectors",
        "healthcheck_connector",
        "run_connector_operation",
        "list_playbook_runs",
        "get_playbook_run",
        "create_module",
        "delete_module",
        "publish",
        "default_connector_config",
        "validate_connector_config",
        "create_connector_configuration",
        "update_connector_configuration",
        "upsert_connector_configuration",
        "last_playbook_run",
        "why_playbook_failed",
        "wait_for_playbook_run",
        "upsert_record",
        "get_or_create_record",
    }:
        assert expected in names


def test_every_tool_has_object_schema():
    for t in tools.list_tools():
        assert t.input_schema["type"] == "object"
        assert "properties" in t.input_schema


def test_get_tool_and_unknown():
    assert tools.get_tool("get_record").name == "get_record"
    with pytest.raises(KeyError):
        tools.get_tool("nope")


def test_anthropic_export_shape():
    defs = tools.to_anthropic_tools()
    assert all({"name", "description", "input_schema"} <= set(d) for d in defs)
    assert len(defs) == len(tools.list_tools())


def test_openai_export_shape():
    defs = tools.to_openai_tools()
    d = defs[0]
    assert d["type"] == "function"
    assert {"name", "description", "parameters"} <= set(d["function"])


def test_tool_schemas_generic():
    schemas = tools.tool_schemas()
    assert all({"name", "description", "input_schema"} <= set(s) for s in schemas)


# -- dispatch: reads --------------------------------------------------------
def test_dispatch_get_record_summary_trims(client):
    out = tools.dispatch(client, "get_record", {"module": "alerts", "ref": "u1", "summary": True})
    assert out["uuid"] == "u1" and out["name"] == "rec"
    assert "junk" not in out


def test_dispatch_search_records_fields(client):
    out = tools.dispatch(client, "search_records", {"module": "alerts", "fields": ["name"]})
    assert out["total"] == 1
    assert out["members"] == [{"name": "x", "uuid": "a"}]


def test_dispatch_query_builds_body(client):
    out = tools.dispatch(
        client,
        "query_records",
        {"module": "alerts", "filters": [{"field": "x", "operator": "eq", "value": 1}], "limit": 5},
    )
    body = client.store["query_body"]
    assert body["logic"] == "AND"
    assert body["filters"] == [{"field": "x", "operator": "eq", "value": 1}]
    assert body["limit"] == 5
    assert out["total"] == 1


# -- dispatch: writes -------------------------------------------------------
def test_dispatch_create_record(client):
    out = tools.dispatch(
        client,
        "create_record",
        {"module": "alerts", "data": {"name": "n"}, "resolve_picklists": True},
    )
    assert out["uuid"] == "new"
    assert client.store["created"] == ({"name": "n"}, True)


def test_dispatch_delete_record(client):
    out = tools.dispatch(client, "delete_record", {"module": "alerts", "ref": "u1", "hard": True})
    assert out == {"deleted": "u1", "module": "alerts", "hard": True}
    assert client.store["deleted"] == ("u1", True)


# -- dispatch: other surfaces ----------------------------------------------
def test_dispatch_resolve_picklist(client):
    out = tools.dispatch(client, "resolve_picklist", {"value": "High", "picklist": "Severity"})
    assert out == {"value": "High", "iri": "/api/3/picklists/h", "resolved": True}


def test_dispatch_run_connector_operation(client):
    out = tools.dispatch(
        client,
        "run_connector_operation",
        {"connector": "virustotal", "operation": "get_reputation_ip", "params": {"ip": "8.8.8.8"}},
    )
    assert out["status"] == "Success"
    assert out["data"]["params"] == {"ip": "8.8.8.8"}


def test_dispatch_list_playbook_runs(client):
    out = tools.dispatch(client, "list_playbook_runs", {"playbook": "Block IP"})
    assert out["runs"][0]["name"] == "Block IP"


# -- dispatch: module admin -------------------------------------------------
def test_dispatch_create_module_grant_to(client):
    out = tools.dispatch(
        client,
        "create_module",
        {"module": "crew", "fields": [{"name": "alias", "type": "text"}], "grant_to": ["Full App Permissions"]},
    )
    assert out["module"] == "crew"
    module, kwargs = client.store["create_module"]
    assert module == "crew"
    assert kwargs["grant_to"] == ["Full App Permissions"]
    assert kwargs["fields"] == [{"name": "alias", "type": "text"}]


def test_dispatch_create_module_drops_none_defaults(client):
    # Omitted optional kwargs must not be passed as None (let create_module defaults apply).
    tools.dispatch(client, "create_module", {"module": "crew"})
    _, kwargs = client.store["create_module"]
    assert "label" not in kwargs and "plural" not in kwargs and "grant_to" not in kwargs


def test_dispatch_delete_module(client):
    out = tools.dispatch(client, "delete_module", {"module": "crew", "drop_orphan_tables": "Facts"})
    assert out["module"] == "crew"
    module, kwargs = client.store["delete_module"]
    assert module == "crew"
    assert kwargs["drop_orphan_tables"] == "Facts"


def test_dispatch_publish(client):
    out = tools.dispatch(client, "publish", {"timeout": 30})
    assert out["status"] == "Success"
    assert client.store["publish"]["timeout"] == 30


# -- dispatch: connector configuration --------------------------------------
def test_dispatch_default_connector_config(client):
    out = tools.dispatch(client, "default_connector_config", {"connector": "code-snippet"})
    assert out == {"server": "", "verify_ssl": True}


def test_dispatch_validate_connector_config(client):
    out = tools.dispatch(
        client,
        "validate_connector_config",
        {"connector": "virustotal", "config": {"api_key": "x"}},
    )
    assert out["valid"] is True
    assert out["unknown"] == ["rogue_key"]


def test_dispatch_upsert_connector_configuration(client):
    out = tools.dispatch(
        client,
        "upsert_connector_configuration",
        {"connector": "virustotal", "config": {"api_key": "x"}, "name": "default"},
    )
    assert out["name"] == "default" and out["config_id"] == "c1"


def test_dispatch_create_connector_configuration_exist_ok(client):
    out = tools.dispatch(
        client,
        "create_connector_configuration",
        {"connector": "virustotal", "config": {"api_key": "x"}, "name": "default", "exist_ok": True},
    )
    assert out["name"] == "default"


def test_dispatch_update_connector_configuration(client):
    out = tools.dispatch(
        client,
        "update_connector_configuration",
        {"connector": "virustotal", "config_id": "c1", "config": {"api_key": "y"}, "name": "default"},
    )
    assert out["config_id"] == "c1" and out["name"] == "default"


# -- dispatch: playbook run debugging ---------------------------------------
def test_dispatch_last_playbook_run(client):
    out = tools.dispatch(client, "last_playbook_run", {"playbook": "Block IP"})
    assert out["pk"] == "100" and out["status"] == "finished"


def test_dispatch_why_playbook_failed(client):
    out = tools.dispatch(client, "why_playbook_failed", {"playbook": "Block IP"})
    assert out["failing_step"] == "enrich" and out["error_message"] == "boom"


def test_dispatch_wait_for_playbook_run(client):
    out = tools.dispatch(client, "wait_for_playbook_run", {"playbook": "Block IP", "timeout": 5})
    assert out["status"] == "finished"


# -- dispatch: record upsert ------------------------------------------------
def test_dispatch_upsert_record(client):
    out = tools.dispatch(client, "upsert_record", {"module": "alerts", "data": {"name": "n"}, "key": "name"})
    assert out["uuid"] == "ups"
    assert client.store["upserted"] == ({"name": "n"}, "name")


def test_dispatch_get_or_create_record(client):
    out = tools.dispatch(client, "get_or_create_record", {"module": "alerts", "data": {"name": "n"}, "key": "name"})
    assert out["created"] is True
    assert out["record"]["uuid"] == "goc"
    assert client.store["goc"] == ({"name": "n"}, "name")


# -- dispatch: error handling ----------------------------------------------
def test_dispatch_unknown_tool_returns_error(client):
    out = tools.dispatch(client, "nope", {})
    assert out["error"]["type"] == "UnknownTool"
    assert "nope" in out["error"]["message"]


def test_dispatch_api_error_is_structured(client):
    class Resp:
        status_code = 404

    def boom(module, **kwargs):
        class RS:
            def get(self, ref):
                raise ResourceNotFoundError("missing", Resp())

        return RS()

    client.records = boom
    out = tools.dispatch(client, "get_record", {"module": "alerts", "ref": "x"})
    assert out["error"]["type"] == "ResourceNotFoundError"
    assert out["error"]["status_code"] == 404
    assert out["error"]["tool"] == "get_record"


def test_dispatch_bad_arguments_is_structured(client):
    out = tools.dispatch(client, "get_record", {"module": "alerts"})  # missing ref
    assert out["error"]["type"] == "TypeError"
    assert out["error"]["tool"] == "get_record"
