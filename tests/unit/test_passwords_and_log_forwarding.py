"""Unit tests for application/OS password helpers and the log-forwarding API.

Covers ``UsersAPI.reset_password`` / ``change_password`` (UI payload shapes),
``LogForwardingAPI`` CRUD against ``/api/gateway/config/syslog``, and the
appliance ``set_os_password`` / ``os_password_status`` wrappers.
"""

from __future__ import annotations

import pytest

from pyfsr.api.log_forwarding import LogForwardingAPI
from pyfsr.api.users import UsersAPI
from pyfsr.appliance import HostNamespace
from pyfsr.cli.appliance import host as host_cmds
from pyfsr.cli.appliance.transport import CommandResult, Transport

_ACTOR = {
    "uuid": "3451141c-bac6-467c-8d72-85e0fab569ce",
    "userId": "6d463fb1-463a-426f-afff-95990e53c4d5",
    "firstname": "CS",
    "lastname": "Admin",
    "email": "admin@example.com",
}
_DAS_USERS = {"usersresp": [{"loginid": "csadmin", "uuid": _ACTOR["userId"]}]}


def _identity_get(endpoint):
    return _DAS_USERS if endpoint == "/api/auth/users" else _ACTOR


class _Auth:
    def __init__(self, username, password):
        self.username = username
        self.password = password


class _Rec:
    """Records every call; returns canned responses per verb."""

    def __init__(self, *, get=None, post=None, put=None, delete=None, auth=None):
        self._resp = {"get": get, "post": post, "put": put, "delete": delete}
        self.calls: list[tuple[str, str, object]] = []
        self.auth = auth

    def _call(self, verb, endpoint, data):
        self.calls.append((verb, endpoint, data))
        r = self._resp[verb]
        return r(endpoint) if callable(r) else r

    def get(self, endpoint, params=None, **kw):
        return self._call("get", endpoint, params)

    def post(self, endpoint, data=None, params=None, **kw):
        return self._call("post", endpoint, data)

    def put(self, endpoint, data=None, params=None, **kw):
        return self._call("put", endpoint, data)

    def delete(self, endpoint, params=None, **kw):
        return self._call("delete", endpoint, params)


# --- application passwords ---------------------------------------------------


def _lookup_get(endpoint):
    if endpoint == "/api/auth/users":
        return {"usersresp": [{"loginid": "test", "uuid": "das-1"}]}
    return {"hydra:member": [{"uuid": "person-1", "userId": "das-1"}]}


def test_lookup_resolves_both_ids():
    rec = _Rec(get=_lookup_get)
    assert UsersAPI(rec).lookup("test") == {"loginid": "test", "user_id": "das-1", "person_uuid": "person-1"}
    assert rec.calls[0] == ("get", "/api/auth/users", {"loginid": "test"})
    assert rec.calls[1] == ("get", "/api/3/people", {"userId": "das-1"})


def test_lookup_unknown_login_raises():
    with pytest.raises(LookupError):
        UsersAPI(_Rec(get={"usersresp": []})).lookup("nobody")


def test_reset_password_posts_ui_payload():
    rec = _Rec(get=_lookup_get, post="Password Changed sucessfully")
    out = UsersAPI(rec).reset_password("test", "<your-password>")
    verb, endpoint, body = rec.calls[-1]
    assert (verb, endpoint) == ("post", "/api/3/resetpassword")
    assert body == {
        "uuid": "person-1",
        "currentUUID": "das-1",
        "loginId": "test",
        "password": "<your-password>",
        "sendEmail": False,
    }
    assert out == "Password Changed sucessfully"


def test_whoami_joins_actor_and_das_user():
    rec = _Rec(get=_identity_get)
    me = UsersAPI(rec).whoami()
    assert me == {
        "loginid": "csadmin",
        "person_uuid": _ACTOR["uuid"],
        "user_id": _ACTOR["userId"],
        "email": "admin@example.com",
        "name": "CS Admin",
    }
    assert rec.calls[1] == ("get", "/api/auth/users", {"uuid": _ACTOR["userId"]})


def test_whoami_raises_when_das_has_no_user():
    with pytest.raises(LookupError):
        UsersAPI(_Rec(get=lambda ep: {"usersresp": []} if ep == "/api/auth/users" else _ACTOR)).whoami()


def test_change_password_needs_only_passwords_and_updates_stored_auth():
    auth = _Auth("csadmin", "old")
    rec = _Rec(get=_identity_get, put={}, auth=auth)
    UsersAPI(rec).change_password("old", "<your-password>")
    verb, endpoint, body = rec.calls[-1]
    assert (verb, endpoint) == ("put", "/api/3/changepassword")
    assert body == {
        "uuid": _ACTOR["uuid"],
        "loginId": "csadmin",
        "oldPassword": "old",
        "newPassword": "<your-password>",
    }
    assert auth.password == "<your-password>"


def test_change_password_works_without_username_auth():
    rec = _Rec(get=_identity_get, put={})
    UsersAPI(rec).change_password("old", "<your-password>")
    assert rec.calls[-1][2]["loginId"] == "csadmin"


def test_change_password_rejects_same_password():
    with pytest.raises(ValueError, match="same"):
        UsersAPI(_Rec(get=_identity_get, put={})).change_password("a", "a")


# --- log forwarding ------------------------------------------------------------

_CFG = {
    "uuid": "u-1",
    "config_name": "lab",
    "enabled": True,
    "server": "10.0.0.5",
    "port": 514,
    "protocol": "tcp",
    "syslog_filters": ["application"],
}


def test_list_tolerates_non_list():
    assert LogForwardingAPI(_Rec(get={})).list() == []


def test_create_builds_ui_payload_and_returns_stored_config():
    rec = _Rec(post=lambda ep: [dict(_CFG)])
    out = LogForwardingAPI(rec).create(
        "10.0.0.5", 514, config_name="lab", protocol="tcp", filters=["application", "audit"]
    )
    verb, endpoint, body = rec.calls[0]
    assert (verb, endpoint) == ("post", "/api/gateway/config/syslog")
    assert body["server"] == "10.0.0.5" and body["port"] == 514 and body["protocol"] == "tcp"
    assert body["syslog_filters"] == ["application", "audit"]
    assert body["enabled"] is True and body["tls"] is False
    assert len(body["uuid"]) == 36
    assert "audit_filter_setting" not in body
    assert out == _CFG


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"config_name": ""}, "config_name"),
        ({"config_name": "my-siem"}, "config_name"),
        ({"server": "syslog"}, "server"),
        ({"server": "fe80::1"}, "server"),
        ({"port": 0}, "port"),
        ({"tls": True}, "ca_cert"),
        ({"protocol": "http"}, "protocol"),
        ({"filters": ["os"]}, "filters"),
    ],
)
def test_create_enforces_gateway_constraints(kwargs, match):
    args = {"server": "10.0.0.5", "port": 514, "config_name": "lab", **kwargs}
    with pytest.raises(ValueError, match=match):
        LogForwardingAPI(_Rec()).create(**args)


def test_update_merges_and_puts_whole_object():
    rec = _Rec(get=[dict(_CFG)], put={})
    LogForwardingAPI(rec).update("u-1", port=6514, filters=["audit"])
    verb, endpoint, body = rec.calls[-1]
    assert (verb, endpoint) == ("put", "/api/gateway/config/syslog")
    assert body["port"] == 6514 and body["syslog_filters"] == ["audit"]
    assert body["server"] == "10.0.0.5" and body["uuid"] == "u-1"


def test_update_unknown_uuid_raises():
    with pytest.raises(KeyError):
        LogForwardingAPI(_Rec(get=[])).update("nope", port=1)


def test_delete_targets_uuid():
    rec = _Rec(delete={})
    LogForwardingAPI(rec).delete("u-1")
    assert rec.calls[0][:2] == ("delete", "/api/gateway/config/syslog/u-1")


# --- OS account passwords ----------------------------------------------------


class _Box(Transport):
    target = "box"

    def __init__(self, stdout="", user="csadmin", password="old"):
        self.stdout = stdout
        self.user = user
        self.password = password
        self.sudo_password = password
        self.calls: list[dict] = []

    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        self.calls.append({"argv": argv, "input_text": input_text, "sudo": sudo})
        return CommandResult(argv=argv, returncode=0, stdout=self.stdout, stderr="")


def test_set_os_password_uses_chpasswd_stdin_with_sudo():
    box = _Box()
    host_cmds.set_os_password(box, "csadmin", "<your-password>", yes=True)
    call = box.calls[0]
    assert call["argv"] == ["chpasswd"]
    assert call["input_text"] == "csadmin:<your-password>\n"
    assert call["sudo"] is True


@pytest.mark.parametrize("user,pw", [("bad user", "x"), ("csadmin", "a:b"), ("csadmin", "a\nb"), ("csadmin", "")])
def test_set_os_password_validates(user, pw):
    with pytest.raises(ValueError):
        host_cmds.set_os_password(_Box(), user, pw, yes=True)


def test_set_os_password_requires_yes():
    with pytest.raises(PermissionError):
        host_cmds.set_os_password(_Box(), "csadmin", "x")


def test_namespace_updates_transport_creds_for_own_account():
    box = _Box(user="csadmin", password="old")
    HostNamespace(box).set_os_password("csadmin", "<your-password>", yes=True)
    assert box.password == "<your-password>" and box.sudo_password == "<your-password>"

    other = _Box(user="admin", password="old")
    HostNamespace(other).set_os_password("csadmin", "<your-password>", yes=True)
    assert other.password == "old" and other.sudo_password == "old"


def test_os_password_status_parses_chage():
    box = _Box(stdout="Last password change\t\t\t\t\t: Sep 26, 2026\nPassword expires\t\t\t\t\t: never\n")
    status = host_cmds.os_password_status(box, "csadmin")
    assert status == {"Last password change": "Sep 26, 2026", "Password expires": "never"}
