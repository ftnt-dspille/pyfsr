"""Unknown write keys and write read-back (live proof: test_record_write_guards_integration.py)."""

import pytest

from pyfsr.exceptions import ValidationError
from pyfsr.field_values import unknown_fields_problem
from pyfsr.records import RecordSet

FIELDS = {"name": "text", "description": "richtext", "severity": "picklist"}


def test_a_misspelt_field_names_the_closest_one():
    problem = unknown_fields_problem(FIELDS, {"name": "x", "descriptoin": "y"})
    assert "'descriptoin' is not a field" in problem and "did you mean 'description'" in problem


@pytest.mark.parametrize("key", ["@id", "__link", "uuid", "recordTags", "owners", "tenant"])
def test_system_keys_are_allowed(key):
    assert unknown_fields_problem(FIELDS, {"name": "x", key: "v"}) is None


class _Modules:
    def describe(self, module):
        return {"fields": [{"name": n, "form_type": f} for n, f in FIELDS.items()]}


class _Client:
    modules = _Modules()

    def __init__(self, stored=None):
        self.stored = stored

    def get(self, path, params=None, **kw):
        return self.stored


def test_prepare_values_refuses_an_unknown_field_before_sending():
    with pytest.raises(ValidationError, match="refused before writing for alerts: 'nmae'"):
        RecordSet(_Client(), "alerts").prepare_values({"nmae": "x"})


def _verify(stored, sent, returned=None, monkeypatch=None):
    rs = RecordSet(_Client(stored), "alerts")
    rs.get = lambda ref, **kw: stored
    return rs.verify_write("/api/3/alerts/1", sent, returned)


def test_verify_write_reports_a_dropped_key():
    out = _verify({"name": "x"}, {"name": "x", "nosuch": 1})
    assert out["verified"] is False and "nosuch" in out["mismatches"]


def test_verify_write_compares_picklists_by_value():
    out = _verify(
        {"severity": {"itemValue": "High", "@id": "/p/1"}}, {"severity": "High"}, {"severity": {"@id": "/p/1"}}
    )
    assert out == {"verified": True}


def test_verify_write_reports_a_changed_value():
    out = _verify({"name": "stored"}, {"name": "sent"}, {"name": "sent"})
    assert out["mismatches"]["name"] == {"returned": "sent", "stored": "stored"}


@pytest.mark.parametrize(("pw", "why"), [("abcdefgh123!", "capital"), ("Abcdefgh123", "special")])
def test_a_password_das_refuses_is_caught_first(pw, why):
    from pyfsr.api.users import password_problem

    assert why in password_problem(pw)


def test_generated_passwords_pass_the_policy():
    from pyfsr.api.users import PASSWORD_SPECIALS, generate_password, password_problem

    for _ in range(200):
        pw = generate_password()
        assert password_problem(pw) is None and len(pw) == 20
        assert all(ch.isalnum() or ch in PASSWORD_SPECIALS for ch in pw)


def test_identify_falls_back_to_the_api_key_actor():
    from pyfsr.api.users import UsersAPI
    from pyfsr.exceptions import UnsupportedAuthOperationError

    api = UsersAPI.__new__(UsersAPI)

    def refuse():
        raise UnsupportedAuthOperationError("whoami", "api_key")

    api.whoami = refuse
    api.client = type("C", (), {"get": lambda self, path: {"@type": "ApiKey", "name": "k", "uuid": "u"}})()
    out = api.identify()
    assert out["auth_mode"] == "api_key" and out["principal_type"] == "ApiKey" and out["loginid"] is None
