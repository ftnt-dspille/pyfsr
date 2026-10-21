"""Safety gate + new agent tools: readonly/allow/deny policy, confirm gating,
error enrichment, and the whoami/create_user/list_connector_operations/
install_connector handlers. All offline (fake client, env monkeypatching)."""

from __future__ import annotations

import pytest

from pyfsr.agent import tools
from pyfsr.exceptions import FortiSOARException

# --------------------------------------------------------------------------- fakes


class _FakeUsers:
    def whoami(self):
        return {"loginid": "csadmin", "email": "csadmin@example.com"}

    def get_or_create(self, loginid, password, firstname, lastname, email, roles, **kwargs):
        self.created = {"loginid": loginid, "email": email, "roles": roles, "kwargs": kwargs}
        return ({"email": email, "loginid": loginid}, True)


class _FakeSystem:
    def version(self):
        return {"version": "8.0.0"}


class _Op:
    def __init__(self, name):
        self._d = {
            "operation": name,
            "title": name.replace("_", " ").title(),
            "description": f"does {name}",
            "parameters": [{"name": "ip", "title": "IP", "type": "text", "required": True}],
        }

    def model_dump(self):
        return self._d


class _FakeConnectors:
    def __init__(self):
        self.installed = []

    def operations(self, connector, *, version=None):
        self.ops_asked = (connector, version)
        return [_Op("get_reputation_ip"), _Op("submit_url")]

    def install(self, name, *, version=None, wait=True, timeout=300.0):
        self.installed.append(("hub", name, version))
        return {"name": name, "status": "finished"}

    def install_from_file(self, path, *, replace=False, wait=True, timeout=300.0):
        self.installed.append(("file", path, replace))
        return {"name": "custom-conn", "status": "finished"}

    def healthcheck(self, name, config=None):
        return {"status": "Available", "connector": name}


class _FakeClient:
    base_url = "https://fortisoar.example.com"

    def __init__(self):
        self.users = _FakeUsers()
        self.system = _FakeSystem()
        self.connectors = _FakeConnectors()


@pytest.fixture
def client():
    return _FakeClient()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("FSR_MCP_READONLY", "FSR_MCP_ALLOW_TOOLS", "FSR_MCP_DENY_TOOLS"):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- classification


def test_destructive_tools_are_exactly_the_documented_set():
    destructive = {t.name for t in tools.list_tools() if t.destructive}
    assert destructive == {
        "delete_record",
        "delete_module",
        "delete_schedule",
        "publish",
        # privilege-raising, so confirm-gated like the deletes:
        "create_user",
        "install_connector",
    }


def test_every_destructive_tool_is_mutating():
    for t in tools.list_tools():
        if t.destructive:
            assert t.mutating, f"{t.name} is destructive but not mutating"


def test_write_shaped_names_are_flagged_mutating():
    # Relationship assertion, not a parallel list: any tool whose NAME says it
    # writes must carry mutating=True, so a new write tool can't ship unflagged.
    write_prefixes = (
        "create_",
        "update_",
        "delete_",
        "upsert_",
        "install_",
        "publish",
        "set_default",
        "schedule_playbook",
        "trigger_",
        "run_connector",
    )
    for t in tools.list_tools():
        if t.name.startswith(write_prefixes):
            assert t.mutating, f"{t.name} looks like a write tool but mutating=False"


def test_readonly_tools_are_not_flagged():
    for name in ("list_modules", "get_record", "search_records", "whoami", "list_connector_operations"):
        assert not tools.get_tool(name).mutating


# --------------------------------------------------------------------------- policy filtering


def test_readonly_hides_mutating_tools(monkeypatch):
    monkeypatch.setenv("FSR_MCP_READONLY", "1")
    names = {t["name"] for t in tools.tool_schemas()}
    assert "get_record" in names and "whoami" in names
    for t in tools.list_tools():
        if t.mutating:
            assert t.name not in names


def test_readonly_refuses_dispatch(monkeypatch, client):
    monkeypatch.setenv("FSR_MCP_READONLY", "true")
    out = tools.dispatch(client, "create_record", {"module": "alerts", "data": {}})
    assert out["error"]["type"] == "ToolDisabled"
    assert "read-only" in out["error"]["message"]


def test_allow_globs_narrow_the_surface(monkeypatch, client):
    monkeypatch.setenv("FSR_MCP_ALLOW_TOOLS", "list_*,get_record,whoami")
    names = {t["name"] for t in tools.tool_schemas()}
    assert names and all(n.startswith("list_") or n in {"get_record", "whoami"} for n in names)
    out = tools.dispatch(client, "create_record", {"module": "alerts", "data": {}})
    assert out["error"]["type"] == "ToolDisabled"


def test_deny_globs_hide_tools(monkeypatch, client):
    monkeypatch.setenv("FSR_MCP_DENY_TOOLS", "appliance_*,delete_*")
    names = {t["name"] for t in tools.tool_schemas()}
    assert not any(n.startswith("appliance_") or n.startswith("delete_") for n in names)
    out = tools.dispatch(client, "delete_record", {"module": "alerts", "ref": "u1", "confirm": True})
    assert out["error"]["type"] == "ToolDisabled"


def test_policy_filters_anthropic_and_openai_exports(monkeypatch):
    monkeypatch.setenv("FSR_MCP_READONLY", "yes")
    assert all(not tools.get_tool(t["name"]).mutating for t in tools.to_anthropic_tools())
    assert all(not tools.get_tool(t["function"]["name"]).mutating for t in tools.to_openai_tools())


# --------------------------------------------------------------------------- confirm gating


def test_destructive_without_confirm_is_refused(client):
    called = {}
    client.records = lambda module: called.setdefault("records", module)  # must never run
    out = tools.dispatch(client, "delete_record", {"module": "alerts", "ref": "u1"})
    assert out["error"]["type"] == "confirmation_required"
    assert out["error"]["hint"] == "retry with confirm=true"
    assert "records" not in called


def test_destructive_with_confirm_runs_and_strips_confirm(client):
    class _Recs:
        def delete(self, ref, hard=False):
            self.deleted = (ref, hard)

    recs = _Recs()
    client.records = lambda module: recs
    out = tools.dispatch(client, "delete_record", {"module": "alerts", "ref": "u1", "confirm": True})
    assert out == {"deleted": "u1", "module": "alerts", "hard": False}
    assert recs.deleted == ("u1", False)


def test_confirm_truthy_string_is_not_enough(client):
    out = tools.dispatch(client, "delete_record", {"module": "alerts", "ref": "u1", "confirm": "yes"})
    assert out["error"]["type"] == "confirmation_required"


def test_destructive_schema_advertises_confirm():
    for t in tools.list_tools():
        has_confirm = "confirm" in t.rendered_schema()["properties"]
        assert has_confirm == t.destructive, t.name


def test_rendered_schema_does_not_mutate_the_spec():
    spec = tools.get_tool("delete_record")
    spec.rendered_schema()
    assert "confirm" not in spec.input_schema["properties"]


# --------------------------------------------------------------------------- error enrichment


class _FakeRequest:
    method = "POST"
    url = "https://fortisoar.example.com/api/3/alerts?x=1"


class _FakeResponse:
    status_code = 400
    request = _FakeRequest()
    url = "https://fortisoar.example.com/api/3/alerts?x=1"
    text = '{"hydra:description": "Invalid severity IRI"}'


def test_agent_error_carries_method_url_and_body(client):
    def boom(module):
        raise FortiSOARException("Bad Request", response=_FakeResponse())

    client.records = boom
    out = tools.dispatch(client, "create_record", {"module": "alerts", "data": {}})
    err = out["error"]
    assert err["status_code"] == 400
    assert err["method"] == "POST"
    assert err["url"] == "/api/3/alerts?x=1"
    assert "Invalid severity IRI" in err["body"]


def test_agent_error_body_is_truncated(client):
    class _BigResponse(_FakeResponse):
        text = "x" * 5000

    def boom(module):
        raise FortiSOARException("Bad Request", response=_BigResponse())

    client.records = boom
    out = tools.dispatch(client, "create_record", {"module": "alerts", "data": {}})
    assert len(out["error"]["body"]) <= 620


# --------------------------------------------------------------------------- new tools


def test_whoami_returns_user_base_url_and_version(client):
    out = tools.dispatch(client, "whoami", {})
    assert out["user"]["loginid"] == "csadmin"
    assert out["base_url"] == "https://fortisoar.example.com"
    assert out["version"] == {"version": "8.0.0"}


def test_whoami_version_failure_is_soft(client):
    client.system = None
    out = tools.dispatch(client, "whoami", {})
    assert out["user"]["loginid"] == "csadmin"
    assert "version_error" in out


def test_create_user_is_get_or_create(client):
    out = tools.dispatch(
        client,
        "create_user",
        {
            "loginid": "analyst1",
            "password": "pw",
            "firstname": "Ana",
            "lastname": "Lyst",
            "email": "ana@example.com",
            "roles": ["Security Analyst"],
            "options": {"access_type": "concurrent"},
            "confirm": True,
        },
    )
    assert out["created"] is True
    assert out["user"]["email"] == "ana@example.com"
    assert client.users.created["kwargs"] == {"access_type": "concurrent"}


def test_list_connector_operations_slims_live_ops(client):
    out = tools.dispatch(client, "list_connector_operations", {"connector": "virustotal"})
    assert out["count"] == 2
    op = out["operations"][0]
    assert op["operation"] == "get_reputation_ip"
    assert op["parameters"] == [{"name": "ip", "title": "IP", "type": "text", "required": True}]
    assert client.connectors.ops_asked == ("virustotal", None)


def test_install_connector_from_hub_with_healthcheck(client):
    out = tools.dispatch(client, "install_connector", {"name": "virustotal", "confirm": True})
    assert client.connectors.installed == [("hub", "virustotal", None)]
    assert out["healthcheck"]["status"] == "Available"


def test_install_connector_from_file(client):
    out = tools.dispatch(
        client, "install_connector", {"path": "/tmp/custom-conn.tgz", "replace": True, "confirm": True}
    )
    assert client.connectors.installed == [("file", "/tmp/custom-conn.tgz", True)]
    assert out["install"]["name"] == "custom-conn"


def test_install_connector_requires_name_or_path(client):
    out = tools.dispatch(client, "install_connector", {"confirm": True})
    assert out["error"]["type"] == "ValueError"


def test_privileged_tools_require_confirm(client):
    for name, args in (
        ("install_connector", {"name": "virustotal"}),
        (
            "create_user",
            {
                "loginid": "x",
                "password": "p",
                "firstname": "F",
                "lastname": "L",
                "email": "x@example.com",
                "roles": ["Application Administrator"],
            },
        ),
    ):
        out = tools.dispatch(client, name, args)
        assert out["error"]["type"] == "confirmation_required", name
    assert client.connectors.installed == []


_LAB_YAML = """
collection: Agent Lab Pack
visible: true
playbooks:
  - name: Agent Lab Hello
    steps:
      - name: Start
        type: start
        next: Set Var
      - name: Set Var
        type: set_variable
        next: Done
        vars:
          greeting: "hello from the agent lab"
      - name: Done
        type: end
"""


def _has_playbooks_extra() -> bool:
    try:
        import fsr_playbooks  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _has_playbooks_extra(), reason="fsr_playbooks (pyfsr[playbooks]) not installed")
def test_compile_playbook_yaml_offline(client):
    out = tools.dispatch(client, "compile_playbook_yaml", {"yaml_text": _LAB_YAML})
    assert out["ok"] is True
    assert out["playbooks"] == ["Agent Lab Hello"]
    assert out["collection"] == "Agent Lab Pack"


@pytest.mark.skipif(not _has_playbooks_extra(), reason="fsr_playbooks (pyfsr[playbooks]) not installed")
def test_publish_playbook_compiles_then_deploys_a_collection(client):
    # deploy(), not a bare workflow upsert: upserted workflows alone run but
    # appear in no visible collection (live-verified on 8.0.0).
    class _WC:
        def deploy(self, data, *, overwrite_changed=False):
            self.envelope = data
            self.overwrite_changed = overwrite_changed
            return {"collection": "Agent Lab Pack", "created": 1, "updated": 0}

    client.workflow_collections = _WC()
    out = tools.dispatch(client, "publish_playbook", {"yaml_text": _LAB_YAML})
    assert out["ok"] and out["published"]
    assert out["collection"] == "Agent Lab Pack"
    env = client.workflow_collections.envelope
    rows = env["data"][0]["workflows"]
    assert rows and "uuid" in rows[0] and len(rows[0]["steps"]) == 3
    assert client.workflow_collections.overwrite_changed is False


@pytest.mark.skipif(not _has_playbooks_extra(), reason="fsr_playbooks (pyfsr[playbooks]) not installed")
def test_publish_playbook_blocks_on_compile_errors(client):
    called = {}

    class _PB:
        def upsert_playbooks(self, rows):
            called["rows"] = rows

    client.playbooks = _PB()
    bad_yaml = "collection: X\nplaybooks:\n  - name: Y\n    steps: []\n"
    out = tools.dispatch(client, "publish_playbook", {"yaml_text": bad_yaml})
    assert out["ok"] is False and out["published"] is False
    assert out["errors"]
    assert "rows" not in called


def test_compile_playbook_yaml_without_extra_gives_install_hint(client, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_fsr_playbooks(name, *args, **kwargs):
        if name.startswith("fsr_playbooks"):
            raise ImportError("No module named 'fsr_playbooks'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_fsr_playbooks)
    out = tools.dispatch(client, "compile_playbook_yaml", {"yaml_text": "name: x"})
    assert "pyfsr[playbooks]" in out["error"]["message"]
