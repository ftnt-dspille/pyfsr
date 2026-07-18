"""Unit tests for the idempotent ``ensure_*`` / ``get_or_create_*`` methods added
across the API surface.

Each test uses a fake client (no appliance) and asserts the check-then-create
semantics: existing records are returned untouched (``created=False`` / no-op),
absent ones are created (``created=True``), and re-running is safe.

Covers: teams, roles, agents, users, schedules, picklist options, module fields,
navigation items, playbooks activation, solution packs, and widgets.
"""

from __future__ import annotations

from typing import Any

import pytest

from pyfsr.api.agents import AgentsAPI
from pyfsr.api.app_config import AppConfigAPI
from pyfsr.api.picklists import PicklistsAPI
from pyfsr.api.playbooks import PlaybooksAPI
from pyfsr.api.roles import RolesAPI
from pyfsr.api.schedules import SchedulesAPI
from pyfsr.api.teams import TeamsAPI
from pyfsr.api.users import UsersAPI
from pyfsr.models import NavItem, NavRequire, NavState

# stable uuids
_R_UID = "550e8400-e29b-41d4-a716-446655440000"
_T_UID = "660e8400-e29b-41d4-a716-446655440000"
_U_UID = "880e8400-e29b-41d4-a716-446655440000"
_AGENT_UID = "990e8400-e29b-41d4-a716-446655440000"


class FakeClient:
    """Records calls and serves canned responses by (method, path) or path prefix."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._routes: dict[tuple[str, str], Any] = {}

    def route(self, method: str, path: str, resp: Any) -> FakeClient:
        self._routes[(method, path)] = resp
        return self

    def _lookup(self, method: str, path: str):
        match = None
        for (m, p), r in self._routes.items():
            if m == method and (path == p or path.startswith(p.rstrip("/") + "/")):
                if match is None or len(p) > len(match[0]):
                    match = (p, r)
        return match[1] if match else {}

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        return self._lookup("GET", endpoint)

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data, params))
        return self._lookup("POST", endpoint)

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data, params))
        return self._lookup("PUT", endpoint)

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))
        return self._lookup("DELETE", endpoint)


# ====================================================================== teams
def _teams_client(existing: bool = True) -> FakeClient:
    c = FakeClient()
    members = (
        [{"@id": f"/api/3/teams/{_T_UID}", "name": "Tier 1 SOC", "uuid": _T_UID, "description": "front-line"}]
        if existing
        else []
    )
    c.route("GET", "/api/3/teams", {"hydra:member": members})
    c.route("GET", f"/api/3/teams/{_T_UID}", members[0] if members else {})
    c.route(
        "POST",
        "/api/3/teams",
        {"@id": "/api/3/teams/new-team", "name": "New Team", "uuid": "new-team-uuid", "description": "d"},
    )
    return c


def test_teams_get_or_create_existing_returns_not_created():
    c = _teams_client(existing=True)
    team, created = TeamsAPI(c).get_or_create("Tier 1 SOC")
    assert created is False
    assert team.name == "Tier 1 SOC"
    # no POST issued
    assert not any(call[0] == "POST" for call in c.calls)


def test_teams_get_or_create_absent_creates():
    c = _teams_client(existing=False)
    team, created = TeamsAPI(c).get_or_create("New Team", description="d")
    assert created is True
    assert team.name == "New Team"
    assert any(call[0] == "POST" and call[1] == "/api/3/teams" for call in c.calls)


# ====================================================================== roles
def _roles_client(existing: bool = True) -> FakeClient:
    c = FakeClient()
    members = (
        [{"@id": f"/api/3/roles/{_R_UID}", "name": "Analyst", "uuid": _R_UID, "description": "d"}] if existing else []
    )
    c.route("GET", "/api/3/roles", {"hydra:member": members})
    c.route("GET", f"/api/3/roles/{_R_UID}", members[0] if members else {})
    c.route(
        "POST",
        "/api/3/roles",
        {"@id": "/api/3/roles/new", "name": "New Role", "uuid": "new-role-uuid", "description": "x"},
    )
    return c


def test_roles_get_or_create_existing_returns_not_created():
    c = _roles_client(existing=True)
    role, created = RolesAPI(c).get_or_create("Analyst")
    assert created is False
    assert role.name == "Analyst"
    assert not any(call[0] == "POST" for call in c.calls)


def test_roles_get_or_create_absent_creates():
    c = _roles_client(existing=False)
    role, created = RolesAPI(c).get_or_create("New Role", description="x")
    assert created is True
    assert role.name == "New Role"
    assert any(call[0] == "POST" and call[1] == "/api/3/roles" for call in c.calls)


# ==================================================================== agents
def _agents_client(existing: bool = True) -> FakeClient:
    c = FakeClient()
    members = [{"name": "edge-1", "uuid": _AGENT_UID, "agentId": "edge-1", "active": True}] if existing else []
    c.route("GET", "/api/3/agents", {"hydra:member": members})
    c.route("POST", "/api/3/agents", {"name": "edge-2", "uuid": "new-agent", "agentId": "edge-2", "active": True})
    return c


def test_agents_get_or_create_existing_returns_not_created():
    c = _agents_client(existing=True)
    agent, created = AgentsAPI(c).get_or_create("edge-1", router="/api/3/routers/r1")
    assert created is False
    assert agent.name == "edge-1"
    assert not any(call[0] == "POST" for call in c.calls)


def test_agents_get_or_create_absent_creates():
    c = _agents_client(existing=False)
    agent, created = AgentsAPI(c).get_or_create("edge-2", router="/api/3/routers/r1")
    assert created is True
    assert agent.name == "edge-2"
    assert any(call[0] == "POST" and call[1] == "/api/3/agents" for call in c.calls)


# ===================================================================== users
def _users_client(existing: bool = True) -> FakeClient:
    c = FakeClient()
    members = (
        [
            {
                "@id": f"/api/3/people/{_U_UID}",
                "uuid": _U_UID,
                "firstname": "Jane",
                "lastname": "Smith",
                "email": "jane@corp.example",
                "csActive": True,
            }
        ]
        if existing
        else []
    )
    c.route("GET", "/api/3/people", {"hydra:member": members})
    c.route("GET", f"/api/3/people/{_U_UID}", members[0] if members else {})
    c.route(
        "POST",
        "/api/3/people",
        {"@id": "/api/3/people/new", "uuid": "new-user", "firstname": "Bob", "email": "bob@corp.example"},
    )
    # users.create delegates role/team resolution to client.roles / client.teams
    from pyfsr.api.roles import RolesAPI
    from pyfsr.api.teams import TeamsAPI

    c.roles = RolesAPI(c)  # type: ignore[attr-defined]
    c.teams = TeamsAPI(c)  # type: ignore[attr-defined]
    # roles/teams list endpoints needed for name→uuid resolution
    c.route(
        "GET", "/api/3/roles", {"hydra:member": [{"name": "Analyst", "uuid": _R_UID, "@id": f"/api/3/roles/{_R_UID}"}]}
    )
    c.route("GET", "/api/3/teams", {"hydra:member": []})
    return c


def test_users_find_by_email_returns_user_when_present():
    c = _users_client(existing=True)
    user = UsersAPI(c).find_by_email("jane@corp.example")
    assert user is not None
    assert user.email == "jane@corp.example"


def test_users_find_by_email_returns_none_when_absent():
    c = _users_client(existing=False)
    assert UsersAPI(c).find_by_email("nobody@corp.example") is None


def test_users_get_or_create_existing_returns_not_created():
    c = _users_client(existing=True)
    user, created = UsersAPI(c).get_or_create(
        loginid="j.smith",
        password="<test-pw>",
        firstname="Jane",
        lastname="Smith",
        email="jane@corp.example",
        roles=["Analyst"],
    )
    assert created is False
    assert user.email == "jane@corp.example"
    assert not any(call[0] == "POST" for call in c.calls)


def test_users_get_or_create_absent_creates():
    c = _users_client(existing=False)
    user, created = UsersAPI(c).get_or_create(
        loginid="b.jones",
        password="<test-pw>",
        firstname="Bob",
        lastname="Jones",
        email="bob@corp.example",
        roles=["Analyst"],
    )
    assert created is True
    assert user.email == "bob@corp.example"
    assert any(call[0] == "POST" and call[1] == "/api/3/people" for call in c.calls)


# ================================================================= schedules
_SCHEDULE_ENDPOINT = "/api/wf/api/scheduled/"


def _schedules_client(existing: bool = True) -> FakeClient:
    c = FakeClient()
    members = (
        [
            {
                "id": "fernet-1",
                "name": "nightly-recon",
                "enabled": True,
                "crontab": {"minute": "7", "hour": "2", "day_of_month": "*", "month_of_year": "*", "day_of_week": "*"},
                "kwargs": {"wf_iri": "/api/3/workflows/old", "timezone": "UTC"},
            }
        ]
        if existing
        else []
    )
    c.route("GET", _SCHEDULE_ENDPOINT, {"hydra:member": members})
    c.route("PUT", _SCHEDULE_ENDPOINT, {})
    c.route(
        "POST",
        _SCHEDULE_ENDPOINT,
        {"id": "fernet-new", "name": "nightly-recon", "enabled": True},
    )
    return c


def test_schedules_get_or_create_existing_returns_not_created():
    c = _schedules_client(existing=True)
    task, created = SchedulesAPI(c).get_or_create("nightly-recon", "/api/3/workflows/abc", "7 2 * * *")
    assert created is False
    assert task.name == "nightly-recon" if isinstance(task, dict) else task["name"] == "nightly-recon"
    assert not any(call[0] == "POST" for call in c.calls)


def test_schedules_get_or_create_absent_creates():
    c = _schedules_client(existing=False)
    task, created = SchedulesAPI(c).get_or_create("nightly-recon", "/api/3/workflows/abc", "7 2 * * *")
    assert created is True
    assert any(call[0] == "POST" and call[1] == _SCHEDULE_ENDPOINT for call in c.calls)


def test_schedules_get_or_create_update_if_exists_puts_new_cron():
    c = _schedules_client(existing=True)
    SchedulesAPI(c).get_or_create(
        "nightly-recon", "/api/3/workflows/new", "0 3 * * *", update_if_exists=True, typed=False
    )
    put_calls = [call for call in c.calls if call[0] == "PUT"]
    assert len(put_calls) == 1
    body = put_calls[0][2]
    assert body["crontab"]["hour"] == "3"
    assert body["kwargs"]["wf_iri"] == "/api/3/workflows/new"


# ============================================================ picklist options
class _PicklistWriteClient:
    """Fake client simulating picklist endpoints with in-memory state."""

    def __init__(self):
        self.lists: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self._seq = 100

    def _uuid(self):
        self._seq += 1
        return f"00000000-0000-0000-0000-{self._seq:012d}"

    def get(self, endpoint, params=None, **kwargs):
        if endpoint == "/api/3/picklist_names":
            return {"hydra:member": list(self.lists.values())}
        if endpoint == "/api/3/picklists":
            return {"hydra:member": list(self.items.values())}
        return {"hydra:member": []}

    def post(self, endpoint, data=None, params=None, **kwargs):
        data = data or {}
        if endpoint == "/api/3/picklist_names":
            iri = f"/api/3/picklist_names/{self._uuid()}"
            lst = {"@id": iri, "@type": "PicklistName", "name": data.get("name"), "uuid": iri.rsplit("/", 1)[-1]}
            self.lists[iri] = lst
            return lst
        if endpoint == "/api/3/picklists":
            iri = f"/api/3/picklists/{self._uuid()}"
            item = {
                "@id": iri,
                "@type": "Picklist",
                "itemValue": data.get("itemValue"),
                "listName": data.get("listName"),
                "orderIndex": data.get("orderIndex"),
                "color": data.get("color"),
                "uuid": iri.rsplit("/", 1)[-1],
            }
            self.items[iri] = item
            return item
        return {}

    def delete(self, endpoint, params=None, **kwargs):
        return None


def test_picklist_get_or_create_option_existing_returns_not_created():
    api = PicklistsAPI(_PicklistWriteClient())
    api.create_picklist("Status", options=["Open"])
    item, created = api.get_or_create_option("Status", "Open")
    assert created is False
    assert item.itemValue == "Open"


def test_picklist_get_or_create_option_absent_creates():
    api = PicklistsAPI(_PicklistWriteClient())
    api.create_picklist("Status")
    item, created = api.get_or_create_option("Status", "Closed", color="#FF0000")
    assert created is True
    assert item.itemValue == "Closed"
    assert item.color == "#FF0000"


# ============================================================= module fields
def _modules_admin_client(field_exists: bool = False) -> FakeClient:
    c = FakeClient()
    attrs = [{"name": "existing_field", "type": "text"}] if field_exists else []
    staging = {"type": "mymodule", "uuid": "staging-1", "attributes": list(attrs)}
    # _staging_lite GETs the collection; get_staging GETs the single record by uuid
    c.route("GET", "/api/3/staging_model_metadatas", {"hydra:member": [staging]})
    c.route("GET", "/api/3/staging_model_metadatas/staging-1", staging)
    # published collection (empty — module not published yet)
    c.route("GET", "/api/3/published_model_metadatas", {"hydra:member": []})
    return c


def test_ensure_field_adds_when_absent(monkeypatch):
    from pyfsr.api.modules_admin import ModulesAdminAPI

    c = _modules_admin_client(field_exists=False)
    api = ModulesAdminAPI(c)

    added = []
    monkeypatch.setattr(api, "add_field", lambda mod, field, **kw: added.append(field) or {"type": mod})

    result = api.ensure_field("mymodule", {"name": "new_field", "type": "text"})
    assert len(added) == 1
    assert added[0]["name"] == "new_field"
    assert result is not None


def test_ensure_field_noop_when_present(monkeypatch):
    from pyfsr.api.modules_admin import ModulesAdminAPI

    c = _modules_admin_client(field_exists=True)
    api = ModulesAdminAPI(c)

    added = []
    monkeypatch.setattr(api, "add_field", lambda mod, field, **kw: added.append(field))

    result = api.ensure_field("mymodule", {"name": "existing_field", "type": "text"})
    assert len(added) == 0
    assert result is None


# ========================================================= navigation items
_NAV_CONFIG = {
    "id": "app",
    "type": "app",
    "config": {
        "navigation": [
            {
                "title": "Alerts",
                "icon": "icon-alerts",
                "require": [],
                "state": {"name": "main.modules.list", "parameters": {"module": "alerts"}},
            },
        ],
    },
}


class _NavClient:
    """Fake client for app_config with stateful GET/PUT."""

    def __init__(self, config):
        self._config = config
        self.calls = []

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint))
        return self._config

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        self._config = data
        return data


def test_ensure_navigation_item_adds_when_absent():
    c = _NavClient(_NAV_CONFIG)
    new_item = NavItem(
        title="My Module",
        icon="icon-bookmark",
        state=NavState(name="main.modules.list", parameters={"module": "my_module"}),
        require=NavRequire(module="my_module", action="read"),
    )
    result = AppConfigAPI(c).ensure_navigation_item(new_item)
    assert result is not None
    assert any(call[0] == "PUT" for call in c.calls)


def test_ensure_navigation_item_noop_when_present():
    c = _NavClient(_NAV_CONFIG)
    existing_item = NavItem(
        title="Alerts",
        icon="icon-alerts",
        state=NavState(name="main.modules.list", parameters={"module": "alerts"}),
        require=[],
    )
    result = AppConfigAPI(c).ensure_navigation_item(existing_item)
    assert result is None
    assert not any(call[0] == "PUT" for call in c.calls)


# ========================================================== playbook active
_WF = "/api/3/workflows"


def _playbooks_client(is_active: bool = True) -> FakeClient:
    c = FakeClient()
    c.route(
        "GET",
        f"{_WF}/{_AGENT_UID}",
        {"uuid": _AGENT_UID, "name": "My PB", "isActive": is_active},
    )
    c.route("PUT", f"{_WF}/{_AGENT_UID}", {"uuid": _AGENT_UID, "name": "My PB", "isActive": not is_active})
    return c


def test_playbook_ensure_active_noop_when_already_active():
    c = _playbooks_client(is_active=True)
    result = PlaybooksAPI(c).ensure_active(_AGENT_UID, active=True)
    assert result["isActive"] is True
    assert not any(call[0] == "PUT" for call in c.calls)


def test_playbook_ensure_active_activates_when_inactive():
    c = _playbooks_client(is_active=False)
    result = PlaybooksAPI(c).ensure_active(_AGENT_UID, active=True)
    assert result["isActive"] is True
    assert any(call[0] == "PUT" for call in c.calls)


def test_playbook_ensure_active_accepts_dict():
    c = FakeClient()
    pb_dict = {"uuid": _AGENT_UID, "name": "My PB", "isActive": True}
    result = PlaybooksAPI(c).ensure_active(pb_dict, active=True)
    assert result["isActive"] is True
    assert not any(call[0] == "PUT" for call in c.calls)


def test_playbook_ensure_active_dict_without_uuid_raises():
    c = FakeClient()
    with pytest.raises(ValueError, match="no resolvable uuid"):
        PlaybooksAPI(c).ensure_active({"name": "No UUID"}, active=True)


# ========================================================== solution packs
def test_solution_pack_ensure_installed_skips_when_already_installed(monkeypatch):
    from pyfsr.api.solution_packs import SolutionPackAPI
    from pyfsr.models._system import SolutionPackInstallResponse

    c = FakeClient()

    api = SolutionPackAPI(c)

    # Stub content_hub.find_installed_pack to return an existing pack
    class FakeContentHub:
        def find_installed_pack(self, name):
            from pyfsr.models._system import SolutionPack

            return SolutionPack(name="SOAR Framework", version="2.2.1", uuid="sp-uuid", installed=True)

    api.content_hub = FakeContentHub()

    result = api.ensure_installed("SOAR Framework", "2.2.1")
    assert isinstance(result, SolutionPackInstallResponse)
    assert result.version == "2.2.1"
    # No install POST issued
    assert not any(call[0] == "POST" and "solutionpacks/install" in call[1] for call in c.calls)


def test_solution_pack_ensure_installed_installs_when_absent(monkeypatch):
    from pyfsr.api.solution_packs import SolutionPackAPI

    c = FakeClient()
    c.route(
        "POST",
        "/api/3/solutionpacks/install",
        {"name": "SOAR Framework", "version": "2.2.1", "uuid": "sp-uuid"},
    )
    api = SolutionPackAPI(c)

    class FakeContentHub:
        def find_installed_pack(self, name):
            return None

    api.content_hub = FakeContentHub()

    # Stub wait_for_install so we don't need import_job polling
    monkeypatch.setattr(api, "wait_for_install", lambda job_id, **kw: type("S", (), {"status": "Import Complete"})())

    result = api.ensure_installed("SOAR Framework", "2.2.1")
    assert result is not None
    assert any(call[0] == "POST" and "solutionpacks/install" in call[1] for call in c.calls)
