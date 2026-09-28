"""Unit tests for agent uninstall and the investigation planner's tool table."""

from types import SimpleNamespace

import pytest

from pyfsr.api.ai import DEFAULT_INVESTIGATION_TOOLS, AIApi
from pyfsr.models import InvestigationTool

# ------------------------------------------------------------------ uninstall


class _DeleteClient:
    def __init__(self):
        self.deleted = []

    def delete(self, endpoint, **kw):
        self.deleted.append(endpoint)


def _api(agents):
    client = _DeleteClient()
    api = AIApi(client)
    api.list_agents = lambda **kw: agents
    return api, client


def test_uninstall_agent_deletes_by_name_and_version():
    api, client = _api([SimpleNamespace(name="my-agent", version="1.2.0", system=False)])
    api.uninstall_agent("my-agent")
    assert client.deleted == ["/api/ai/agent/my-agent/1.2.0"]


def test_uninstall_agent_unknown_name_raises():
    api, client = _api([])
    with pytest.raises(ValueError, match="no installed agent"):
        api.uninstall_agent("ghost")
    assert client.deleted == []


def test_uninstall_agent_refuses_builtin_without_force():
    api, client = _api([SimpleNamespace(name="siem", version="1.0.0", system=True)])
    with pytest.raises(ValueError, match="built-in"):
        api.uninstall_agent("siem")
    api.uninstall_agent("siem", force=True)
    assert client.deleted == ["/api/ai/agent/siem/1.0.0"]


# ------------------------------------------------------------------ tool table

_STOCK = """
        | Avenue                 | Source            | Required field        | Active |
        | -- | -- | -- | -- |
        | Approved activity      | Org Context       | IOCs + activity       |   Yes  |
        | Ticketing              | ITSM              | -                     |   No   |
"""


def test_parse_table_reads_stock_layout():
    rows = InvestigationTool.parse_table(_STOCK)
    assert [(r.avenue, r.source, r.required_field, r.active) for r in rows] == [
        ("Approved activity", "Org Context", "IOCs + activity", True),
        ("Ticketing", "ITSM", "-", False),
    ]


def test_render_parse_roundtrip():
    rows = list(DEFAULT_INVESTIGATION_TOOLS) + [
        InvestigationTool(avenue="Authorized security testing", source="Pentest Registry", required_field="host")
    ]
    assert InvestigationTool.parse_table(InvestigationTool.render_table(rows)) == rows


class _Records:
    def __init__(self, store):
        self.store = store

    def query(self, q):
        return [self.store["rec"]] if self.store.get("rec") else []

    def create(self, data):
        self.store["rec"] = SimpleNamespace(uuid="new", get=lambda k, d=None: data.get(k, d))
        self.store["created"] = data

    def update(self, uuid, data):
        self.store["updated"] = (uuid, data)

    def delete(self, uuid):
        self.store["deleted"] = uuid
        self.store["rec"] = None


class _RecordsClient:
    def __init__(self, rec=None):
        self.store = {"rec": rec}

    def records(self, module):
        assert module == "organizational_contexts"
        return _Records(self.store)


def test_investigation_tools_defaults_without_record():
    assert AIApi(_RecordsClient()).investigation_tools() == list(DEFAULT_INVESTIGATION_TOOLS)


def test_set_investigation_tool_seeds_record_from_defaults():
    client = _RecordsClient()
    rows = AIApi(client).set_investigation_tool("Authorized security testing", "Pentest Registry", "host")
    created = client.store["created"]
    assert (created["category"], created["subCategory"]) == ("INFRA_INFO", "TOOL_LIST")
    parsed = InvestigationTool.parse_table(created["content"])
    assert parsed == rows and len(rows) == len(DEFAULT_INVESTIGATION_TOOLS) + 1


def test_set_investigation_tool_replaces_same_avenue():
    rec = SimpleNamespace(uuid="r1", get=lambda k, d=None: _STOCK if k == "content" else d)
    client = _RecordsClient(rec)
    rows = AIApi(client).set_investigation_tool("ticketing", "ITSM", "host", active=True)
    uuid, data = client.store["updated"]
    assert uuid == "r1"
    assert [(r.avenue, r.active) for r in InvestigationTool.parse_table(data["content"])] == [
        ("Approved activity", True),
        ("ticketing", True),
    ]
    assert len(rows) == 2


def test_remove_and_reset():
    rec = SimpleNamespace(uuid="r1", get=lambda k, d=None: _STOCK if k == "content" else d)
    client = _RecordsClient(rec)
    api = AIApi(client)
    with pytest.raises(ValueError, match="no tool-table row"):
        api.remove_investigation_tool("nope")
    assert [r.avenue for r in api.remove_investigation_tool("Ticketing")] == ["Approved activity"]
    api.reset_investigation_tools()
    assert client.store["deleted"] == "r1"
