"""agents.create: the SOC Team must own the record or the router never configures it."""

import logging

from pyfsr.api.agents import AgentsAPI

SOC = "/api/3/teams/t-soc"
OWN = "/api/3/teams/t-own"
ROUTER = "/api/3/routers/r-1"


class _Teams:
    def __init__(self, known):
        self.known = known

    def _resolve_teams(self, teams):
        out = []
        for t in teams:
            if t.startswith("/api/3/teams/"):
                out.append(t)
            elif t in self.known:
                out.append(self.known[t])
            else:
                raise ValueError(f"unknown team {t!r}")
        return out


class _Client:
    """post() answers from a script; the first reply is what the server made of the default owners."""

    def __init__(self, replies, known=None):
        self.replies, self.calls = list(replies), []
        self.teams = _Teams(known if known is not None else {"SOC Team": SOC, "Own": OWN})

    def post(self, endpoint, data=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"uuid": f"ag-{len(self.calls)}", **(data or {}), **self.replies.pop(0)}

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, None))


def test_agent_model_exposes_owners():
    c = _Client([{"owners": [OWN, {"@id": SOC}]}])
    assert AgentsAPI(c).create("a", router=ROUTER).owners == [OWN, {"@id": SOC}]


def test_owned_by_the_soc_team_already_means_a_single_create():
    c = _Client([{"owners": [OWN, SOC]}])
    AgentsAPI(c).create("a", router=ROUTER)
    assert [m for m, *_ in c.calls] == ["POST"]


def test_missing_soc_team_recreates_with_it_added_and_the_creators_team_kept(caplog):
    c = _Client([{"owners": [OWN]}, {"owners": [OWN, SOC]}])
    with caplog.at_level(logging.WARNING):
        agent = AgentsAPI(c).create("a", router=ROUTER)
    assert [m for m, *_ in c.calls] == ["POST", "DELETE", "POST"]
    assert c.calls[1][1] == "/api/3/agents/ag-1"  # the stuck record is removed
    assert c.calls[2][2]["owners"] == [OWN, SOC]  # creator's team kept, SOC added
    assert agent.uuid == "ag-3" and "recreating" in caplog.text


def test_explicit_teams_are_sent_as_given_and_never_second_guessed():
    c = _Client([{"owners": [OWN]}])
    AgentsAPI(c).create("a", router=ROUTER, teams=["Own"])
    assert [m for m, *_ in c.calls] == ["POST"] and c.calls[0][2]["owners"] == [OWN]


def test_ensure_team_none_opts_out():
    c = _Client([{"owners": [OWN]}])
    AgentsAPI(c).create("a", router=ROUTER, ensure_team=None)
    assert [m for m, *_ in c.calls] == ["POST"]


def test_a_box_without_the_team_warns_instead_of_failing(caplog):
    c = _Client([{"owners": [OWN]}], known={"Own": OWN})
    with caplog.at_level(logging.WARNING):
        AgentsAPI(c).create("a", router=ROUTER)
    assert [m for m, *_ in c.calls] == ["POST"] and "does not exist here" in caplog.text


def test_a_response_that_reports_no_owners_is_left_alone():
    c = _Client([{}])
    AgentsAPI(c).create("a", router=ROUTER)
    assert [m for m, *_ in c.calls] == ["POST"]
