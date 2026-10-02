"""Unit tests for ``appliance.users``: userDelete driver and ``csadm user`` wrappers."""

import pytest

from pyfsr.cli.appliance import users as users_cmds
from pyfsr.cli.appliance.transport import CommandResult, Transport, TransportError

_ID = "edf7fe38-0759-451b-9d79-793671632ede"


class _Box(Transport):
    target = "box"

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[dict] = []

    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        self.calls.append({"argv": argv, "sudo": sudo})
        return CommandResult(argv=argv, returncode=self.returncode, stdout=self.stdout, stderr="")


class _Facts:
    def __init__(self, box, remaining=()):
        self.transport = box
        self.remaining = list(remaining)
        self.sql: list[tuple[str, str]] = []

    def psql(self, sql, *, db, **kw):
        self.sql.append((db, sql))
        return [[u] for u in self.remaining]


def test_delete_logins_runs_script_as_root_with_ids_as_args():
    facts = _Facts(_Box(stdout="DELETE 0\n"))
    out = users_cmds.delete_logins(facts, [_ID.upper()], yes=True)
    call = facts.transport.calls[0]
    assert call["sudo"] is True
    assert call["argv"][:2] == ["bash", "-c"]
    script = call["argv"][2]
    assert "echo y | ./userDelete" in script and "trap" in script
    assert call["argv"][3:] == ["userDelete", _ID]  # lower-cased, passed as $1..
    assert facts.sql == [("das", f"SELECT uuid FROM users WHERE uuid IN ('{_ID}')")]
    assert out == "DELETE 0\n"


def test_delete_logins_raises_if_login_remains():
    facts = _Facts(_Box(), remaining=[_ID])
    with pytest.raises(TransportError, match="remain"):
        users_cmds.delete_logins(facts, [_ID], yes=True)


def test_delete_logins_raises_if_script_fails():
    facts = _Facts(_Box(returncode=3))
    with pytest.raises(TransportError):
        users_cmds.delete_logins(facts, [_ID], yes=True)
    assert facts.sql == []


@pytest.mark.parametrize("ids", [[], ["jsmith"], [f"{_ID}'; DROP TABLE users; --"]])
def test_delete_logins_rejects_non_uuids(ids):
    facts = _Facts(_Box())
    with pytest.raises(ValueError):
        users_cmds.delete_logins(facts, ids, yes=True)
    assert facts.transport.calls == []


def test_delete_logins_requires_yes():
    facts = _Facts(_Box())
    with pytest.raises(PermissionError):
        users_cmds.delete_logins(facts, [_ID])
    assert facts.transport.calls == []


def test_logged_in_parses_names():
    box = _Box(stdout="Users with following usernames are logged in currently\ncsadmin, jsmith\n")
    assert users_cmds.logged_in(box, access_type="Named") == ["csadmin", "jsmith"]
    assert box.calls[0]["argv"][-4:] == ["--access-type", "Named", "--limit", "10"]


def test_logged_in_none():
    assert users_cmds.logged_in(_Box(stdout="No users logged in currently\n")) == []


def test_logout_unknown_user_raises_lookup_error():
    box = _Box(stdout="User nobody does not exist. Please provide valid username.\n")
    with pytest.raises(LookupError):
        users_cmds.logout(box, "nobody", yes=True)
    assert box.calls[0]["argv"] == ["csadm", "user", "logout-user", "--username", "nobody"]


def test_logout_requires_yes_and_valid_name():
    with pytest.raises(PermissionError):
        users_cmds.logout(_Box(), "jsmith")
    with pytest.raises(ValueError):
        users_cmds.logout(_Box(), "a b; rm -rf /", yes=True)


def test_logout_returns_csadm_message_without_warnings():
    warning = "usermanager.py:33: DeprecationWarning: utcnow()\n  status, message = ...\n"
    box = _Box(stdout=warning + "User jsmith logged out successfully\n")
    assert users_cmds.logout(box, "jsmith", yes=True) == "User jsmith logged out successfully"
