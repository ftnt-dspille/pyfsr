"""Unit tests for SchedulesAPI (list/toggle/create/trigger) + the scheduling tools."""

import pytest

from pyfsr.agent.tools import dispatch
from pyfsr.api.schedules import SchedulesAPI, _parse_cron, _utc_offset

_ENDPOINT = "/api/wf/api/scheduled/"
_TRIGGER_NOW = "/api/wf/api/scheduled/trigger-now/"


# -- recorder (mirrors test_playbooks._Rec: records METHOD/endpoint/data/params) --
class _Rec:
    def __init__(self, *, get_response=None, post_response=None):
        self.calls = []
        self._get = get_response if get_response is not None else {"hydra:member": []}
        self._post = post_response if post_response is not None else {}

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, None, params))
        return self._get

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data, params))
        return self._post

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, None, params))
        return None


# -- _parse_cron / _utc_offset -------------------------------------------------
def test_parse_cron_splits_5_fields_into_crontab_map():
    assert _parse_cron("7 2 * * *") == {
        "minute": "7",
        "hour": "2",
        "day_of_month": "*",
        "month_of_year": "*",
        "day_of_week": "*",
    }


@pytest.mark.parametrize("cron", ["7 2 *", "7 2 * * * *", "", "7 2", "1 2 3 4 5 6 7"])
def test_parse_cron_rejects_non_5_field_expressions(cron):
    with pytest.raises(ValueError, match="5 fields"):
        _parse_cron(cron)


def test_utc_offset_for_utc_is_zero():
    assert _utc_offset("UTC") == "UTC+00:00"


def test_utc_offset_returns_none_for_unknown_timezone():
    assert _utc_offset("Not/A/Real_Tz") is None


# -- create() body construction -------------------------------------------------
def _create(cron="7 2 * * *", **kw):
    rec = _Rec(post_response={"id": "fernet-new", "name": "nightly-recon", "enabled": True})
    out = SchedulesAPI(rec).create("nightly-recon", "/api/3/workflows/abc", cron, **kw)
    assert out == {"id": "fernet-new", "name": "nightly-recon", "enabled": True}
    assert len(rec.calls) == 1
    return rec.calls[0]


def test_create_builds_periodic_task_body():
    method, endpoint, body, params = _create()
    assert method == "POST"
    assert endpoint == _ENDPOINT
    assert params == {"format": "json"}
    assert body == {
        "name": "nightly-recon",
        "crontab": {
            "minute": "7",
            "hour": "2",
            "day_of_month": "*",
            "month_of_year": "*",
            "day_of_week": "*",
            "timezone": "UTC",
        },
        "kwargs": {
            "exit_if_running": True,
            "wf_iri": "/api/3/workflows/abc",
            "timezone": "UTC",
            "utcOffset": "UTC+00:00",
        },
        "expires": None,
        "start_time": None,
        "enabled": True,
    }


def test_create_weekly_cron_maps_day_of_week():
    _, _, body, _ = _create(cron="0 0 * * 1")  # midnight Mondays
    assert body["crontab"]["day_of_week"] == "1"
    assert body["crontab"]["minute"] == "0"
    assert body["crontab"]["hour"] == "0"


def test_create_passes_through_enabled_and_exit_if_running():
    _, _, body, _ = _create(enabled=False, exit_if_running=False)
    assert body["enabled"] is False
    assert body["kwargs"]["exit_if_running"] is False


def test_create_omits_create_user_and_priority_by_default():
    _, _, body, _ = _create()
    assert "createUser" not in body["kwargs"]
    assert "priority" not in body["kwargs"]


def test_create_includes_create_user_and_priority_when_given():
    _, _, body, _ = _create(
        create_user="/api/3/people/3451141c-bac6-467c-8d72-85e0fab569ce",
        priority={"itemValue": "High", "@type": "Picklist"},
    )
    assert body["kwargs"]["createUser"] == "/api/3/people/3451141c-bac6-467c-8d72-85e0fab569ce"
    assert body["kwargs"]["priority"] == {"itemValue": "High", "@type": "Picklist"}


def test_create_bad_cron_raises_before_post():
    rec = _Rec()
    with pytest.raises(ValueError, match="5 fields"):
        SchedulesAPI(rec).create("x", "/api/3/workflows/abc", "7 2 *")
    assert rec.calls == []  # no request sent


# -- trigger_now() -------------------------------------------------------------
def test_trigger_now_by_name_resolves_id_then_posts():
    rec = _Rec(
        get_response={"hydra:member": [{"name": "nightly-recon", "id": "fernet-abc"}]},
        post_response={"message": "The associated workflow is successfully triggered"},
    )
    out = SchedulesAPI(rec).trigger_now(name="nightly-recon")
    assert out == {"message": "The associated workflow is successfully triggered"}
    # GET (list to resolve name->id), then POST trigger-now with the id.
    assert rec.calls[0][0] == "GET"
    assert rec.calls[1] == ("POST", _TRIGGER_NOW, {"id": "fernet-abc"}, {"format": "json"})


def test_trigger_now_by_task_id_skips_lookup():
    rec = _Rec(post_response={"message": "triggered"})
    SchedulesAPI(rec).trigger_now(task_id="fernet-direct")
    assert len(rec.calls) == 1
    assert rec.calls[0] == ("POST", _TRIGGER_NOW, {"id": "fernet-direct"}, {"format": "json"})


def test_trigger_now_requires_name_or_task_id():
    with pytest.raises(ValueError, match="name or task_id"):
        SchedulesAPI(_Rec()).trigger_now()


def test_trigger_now_unknown_name_raises():
    rec = _Rec(get_response={"hydra:member": []})
    with pytest.raises(ValueError, match="No scheduled task named"):
        SchedulesAPI(rec).trigger_now(name="nope")


# -- delete() -----------------------------------------------------------------
def test_delete_resolves_name_to_fresh_id_then_deletes():
    rec = _Rec(get_response={"hydra:member": [{"name": "nightly-recon", "id": "fernet-abc"}]})
    out = SchedulesAPI(rec).delete("nightly-recon")
    assert out is None
    # GET (resolve name->id), then DELETE the resolved id.
    assert rec.calls[0][0] == "GET"
    assert rec.calls[1] == ("DELETE", f"{_ENDPOINT}fernet-abc/", None, {"format": "json"})


def test_delete_unknown_name_raises_before_any_delete():
    rec = _Rec(get_response={"hydra:member": []})
    with pytest.raises(ValueError, match="No scheduled task named"):
        SchedulesAPI(rec).delete("nope")
    assert [c for c in rec.calls if c[0] == "DELETE"] == []  # no DELETE sent


# -- tools (dispatch) ----------------------------------------------------------
class _FakePlaybooks:
    def __init__(self, iri=None):
        self._iri = iri

    def resolve_iri(self, playbook):
        return self._iri


class _FakeSchedules:
    def __init__(self):
        self.create_calls = []
        self.trigger_calls = []
        self.delete_calls = []

    def create(self, name, workflow_iri, cron, **kw):
        self.create_calls.append((name, workflow_iri, cron, kw))
        return {"id": "fernet-new", "name": name, "enabled": kw.get("enabled", True)}

    def trigger_now(self, *, name=None, task_id=None):
        self.trigger_calls.append((name, task_id))
        return {"message": "The associated workflow is successfully triggered"}

    def delete(self, name):
        self.delete_calls.append(name)
        return None


class _FakeClient:
    def __init__(self, iri="/api/3/workflows/pb-uuid"):
        self.playbooks = _FakePlaybooks(iri)
        self.schedules = _FakeSchedules()


def test_tool_schedule_playbook_resolves_name_and_creates():
    c = _FakeClient(iri="/api/3/workflows/pb-uuid")
    out = dispatch(c, "schedule_playbook", {"name": "nightly-recon", "playbook": "Nightly Recon", "cron": "7 2 * * *"})
    assert out == {"id": "fernet-new", "name": "nightly-recon", "enabled": True}
    assert c.schedules.create_calls == [
        (
            "nightly-recon",
            "/api/3/workflows/pb-uuid",
            "7 2 * * *",
            {"timezone": "UTC", "enabled": True, "exit_if_running": True},
        )
    ]


def test_tool_schedule_playbook_with_uuid_skips_resolve():
    c = _FakeClient(iri=None)  # resolve_iri would return None; uuid path must not call it
    out = dispatch(c, "schedule_playbook", {"name": "x", "playbook_uuid": "abc-uuid", "cron": "7 2 * * *"})
    assert out["id"] == "fernet-new"
    assert c.schedules.create_calls[0][1] == "/api/3/workflows/abc-uuid"


def test_tool_schedule_playbook_unknown_playbook_returns_error():
    c = _FakeClient(iri=None)
    out = dispatch(c, "schedule_playbook", {"name": "x", "playbook": "Nope", "cron": "7 2 * * *"})
    assert "error" in out and "Nope" in out["error"]["message"]


def test_tool_schedule_playbook_requires_playbook_or_uuid():
    c = _FakeClient()
    out = dispatch(c, "schedule_playbook", {"name": "x", "cron": "7 2 * * *"})
    assert "error" in out


def test_tool_schedule_playbook_forwards_timezone_and_flags():
    c = _FakeClient()
    dispatch(
        c,
        "schedule_playbook",
        {
            "name": "x",
            "playbook": "P",
            "cron": "0 0 * * 1",
            "timezone": "America/Chicago",
            "enabled": False,
            "exit_if_running": False,
        },
    )
    _, _, _, kw = c.schedules.create_calls[0]
    assert kw == {"timezone": "America/Chicago", "enabled": False, "exit_if_running": False}


def test_tool_trigger_schedule_now_by_name():
    c = _FakeClient()
    out = dispatch(c, "trigger_schedule_now", {"name": "nightly-recon"})
    assert out == {"message": "The associated workflow is successfully triggered"}
    assert c.schedules.trigger_calls == [("nightly-recon", None)]


def test_tool_trigger_schedule_now_by_task_id():
    c = _FakeClient()
    dispatch(c, "trigger_schedule_now", {"task_id": "fernet-direct"})
    assert c.schedules.trigger_calls == [(None, "fernet-direct")]


def test_tool_delete_schedule_calls_delete_by_name():
    c = _FakeClient()
    out = dispatch(c, "delete_schedule", {"name": "nightly-recon"})
    assert out == {"deleted": True, "name": "nightly-recon"}
    assert c.schedules.delete_calls == ["nightly-recon"]
