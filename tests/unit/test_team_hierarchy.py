"""Team hierarchy writes: parents/children/siblings on create and update."""

from __future__ import annotations

import pytest

from pyfsr.api.teams import TeamsAPI

SOC = "6e569c09-3bd4-40f1-98b0-cc994464c3c5"
S00 = "0d2570c3-97c3-459c-98f3-427c21f788eb"
OTHER = "11111111-2222-3333-4444-555555555555"


class FakeClient:
    def __init__(self, record=None):
        self.posts, self.puts = [], []
        self.record = record or {"uuid": S00, "name": "s00", "parents": []}

    def get(self, endpoint, params=None, **kw):
        if endpoint == "/api/3/teams":
            return {"hydra:member": [{"name": "SOC Team", "uuid": SOC}, {"name": "s00", "uuid": S00}]}
        return self.record

    def post(self, endpoint, data=None, **kw):
        self.posts.append((endpoint, data))
        return {"uuid": OTHER, **data}

    def put(self, endpoint, data=None, **kw):
        self.puts.append((endpoint, data))
        return {"uuid": endpoint.rsplit("/", 1)[-1], **data}


def test_create_with_a_parent_by_name():
    client = FakeClient()
    TeamsAPI(client).create("s01", parents=["SOC Team"])
    assert client.posts[0][1] == {"name": "s01", "parents": [f"/api/3/teams/{SOC}"]}


def test_update_replaces_only_the_lists_given():
    client = FakeClient()
    api = TeamsAPI(client)
    api.update("s00", parents=[f"/api/3/teams/{SOC}"])
    assert client.puts[-1] == (f"/api/3/teams/{S00}", {"parents": [f"/api/3/teams/{SOC}"]})
    api.update("s00", siblings=[])
    assert client.puts[-1][1] == {"siblings": []}  # [] clears; parents untouched
    with pytest.raises(ValueError, match="at least one of name="):
        api.update("s00")


def test_add_parents_keeps_the_current_ones():
    client = FakeClient({"uuid": S00, "parents": [{"uuid": OTHER, "name": "x"}]})
    TeamsAPI(client).add_parents("s00", ["SOC Team"])
    assert client.puts[-1][1] == {"parents": [f"/api/3/teams/{OTHER}", f"/api/3/teams/{SOC}"]}


def test_get_or_create_adds_a_missing_parent_to_an_existing_team():
    client = FakeClient()
    team, created = TeamsAPI(client).get_or_create("s00", parents=["SOC Team"])
    assert not created
    assert client.puts[-1][1] == {"parents": [f"/api/3/teams/{SOC}"]}


def test_hierarchy_reads_names():
    client = FakeClient({"uuid": S00, "parents": [{"name": "SOC Team"}], "children": [], "siblings": []})
    team = TeamsAPI(client).hierarchy("s00")
    assert team.parent_names == ["SOC Team"] and team.child_names == [] and team.sibling_names == []


def test_team_model_parses_expanded_and_bare_relations():
    from pyfsr.models import Team, TeamRef

    team = Team.model_validate(
        {
            "uuid": S00,
            "name": "s00",
            "parents": [{"@id": f"/api/3/teams/{SOC}", "uuid": SOC, "name": "SOC Team"}],
            "children": [f"/api/3/teams/{OTHER}"],
            "siblings": [],
        }
    )
    assert isinstance(team.parents[0], TeamRef) and team.parents[0].uuid == SOC
    assert team.parent_names == ["SOC Team"]
    assert team.child_names == [f"/api/3/teams/{OTHER}"]


def test_connector_config_teams_are_uuids():
    from pyfsr.models import ConnectorConfig

    assert ConnectorConfig.model_validate({"config_id": "c", "teams": [S00]}).teams == [S00]
