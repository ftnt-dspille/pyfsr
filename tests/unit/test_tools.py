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
        return HydraPage(
            members=[{"uuid": "a", "name": "x", "junk": 1}], total=1, page=1, limit=limit, raw={}
        )

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


class FakePlaybooks:
    def runs(self, playbook=None, limit=20):
        return [{"name": playbook or "any", "status": "finished"}]

    def get(self, run_pk):
        return {"pk": run_pk, "status": "finished"}


class FakeClient:
    def __init__(self):
        self.store = {}
        self.picklists = FakePicklists()
        self.connectors = FakeConnectors()
        self.playbooks = FakePlaybooks()

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
