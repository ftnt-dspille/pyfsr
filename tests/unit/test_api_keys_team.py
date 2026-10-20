"""API-key teams: agents created by a key on the wrong team are never configured.

A key's team decides whether the appliance's message router can read the agent
records it creates, so ``ensure_usable`` prefers SOC Team and ``grants`` /
``ensure_team`` let callers check and repair a key.
"""

import logging

import pytest

from pyfsr.api.api_keys import DEFAULT_TEAM, ApiKeysAPI

TEAMS = {"Content Development Team": "t-cd", "SOC Team": "t-soc", "NOC - Infrastructure": "t-noc"}
ROLES = {"Full App Permissions": "r-full"}


class _Teams:
    def __init__(self, names):
        self.names = list(names)

    def list(self):
        return [{"name": n, "uuid": TEAMS[n]} for n in self.names]

    def _resolve_teams(self, teams):
        return [f"/api/3/teams/{TEAMS[t]}" if t in TEAMS else t for t in teams]


class _Client:
    """Routes get() by path like the appliance: bindings, a binding with relationships, teams, roles."""

    def __init__(self, existing_teams, key_teams=(), roles=("Full App Permissions",), has_key=True):
        self.teams = _Teams(existing_teams)
        self.key_teams, self.key_roles, self.has_key, self.puts = list(key_teams), list(roles), has_key, []
        self.roles = type("R", (), {"_resolve_roles": lambda s, r: r})()

    def get(self, path, params=None):
        if path == "/api/3/api_keys":
            return {"hydra:member": [{"name": "k", "uuid": "b1", "userId": "u1"}] if self.has_key else []}
        if path == "/api/3/api_keys/b1":
            assert (params or {}).get("$relationships") == "true"
            return {
                "teams": [f"/api/3/teams/{TEAMS[t]}" for t in self.key_teams],
                "roles": [f"/api/3/roles/{ROLES[r]}" for r in self.key_roles],
            }
        table = TEAMS if path == "/api/3/teams" else ROLES
        return {"hydra:member": [{"uuid": v, "name": k} for k, v in table.items()]}

    def put(self, path, data=None, **kw):
        self.puts.append((path, data))
        self.key_teams = [t for t, v in TEAMS.items() if f"/api/3/teams/{v}" in data["teams"]]
        return {"uuid": "b1"}


def test_grants_resolves_team_and_role_names():
    c = _Client(TEAMS, ["Content Development Team"])
    assert ApiKeysAPI(c).grants("k") == {
        "uuid": "b1",
        "teams": ["Content Development Team"],
        "roles": ["Full App Permissions"],
    }


def test_grants_is_none_for_an_unknown_key():
    assert ApiKeysAPI(_Client(TEAMS, has_key=False)).grants("k") is None


def test_ensure_team_adds_soc_team_and_keeps_the_existing_team():
    c = _Client(TEAMS, ["Content Development Team"])
    out = ApiKeysAPI(c).ensure_team("k")
    assert out["teams"] == ["Content Development Team", DEFAULT_TEAM]
    assert c.puts[0][1]["teams"] == ["/api/3/teams/t-cd", "/api/3/teams/t-soc"]


def test_ensure_team_is_a_no_op_when_already_on_the_team():
    c = _Client(TEAMS, ["SOC Team"])
    ApiKeysAPI(c).ensure_team("k")
    assert c.puts == []


def test_ensure_team_reports_a_missing_key_or_team():
    with pytest.raises(LookupError):
        ApiKeysAPI(_Client(TEAMS, has_key=False)).ensure_team("k")
    with pytest.raises(ValueError, match="does not exist"):
        ApiKeysAPI(_Client(["Content Development Team"], ["Content Development Team"])).ensure_team("k")


class _Users:
    def create(self, *, api_key_validity, warn_unbound=True):
        return {"uuid": "u1", "api_key": {"key": "plain"}}


def _created_teams(c):
    c.api_users = _Users()
    c.post = lambda path, data=None, **kw: c.posts.append(data) or {"uuid": "b9", "name": "k"}
    c.posts = []
    c.roles = type("R", (), {"role_map": lambda s: ROLES, "_resolve_roles": lambda s, r: r})()
    ApiKeysAPI(c).ensure_usable(name="new")
    return c.posts[0]["teams"]


def test_ensure_usable_prefers_soc_team_over_the_first_team(caplog):
    c = _Client(["Content Development Team", "SOC Team"], has_key=False)
    assert _created_teams(c) == ["/api/3/teams/t-soc"]


def test_ensure_usable_falls_back_to_the_first_team_and_says_what_breaks(caplog):
    c = _Client(["Content Development Team"], has_key=False)
    with caplog.at_level(logging.WARNING):
        assert _created_teams(c) == ["/api/3/teams/t-cd"]
    assert "not be configured by the message router" in caplog.text
