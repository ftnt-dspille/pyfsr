"""Unit tests for UsersAPI typed (Pydantic User) return values."""

from pyfsr.api.users import UsersAPI
from pyfsr.models import User

_PERSON = {
    "@id": "/api/3/people/3451141c-bac6-467c-8d72-85e0fab569ce",
    "@type": "Person",
    "firstname": "CS",
    "lastname": "Admin",
    "email": "admin@example.com",
    "csActive": True,
    "accessType": "Named",
    "uuid": "3451141c-bac6-467c-8d72-85e0fab569ce",
    "id": 3,
}
_PEOPLE_ENVELOPE = {
    "@context": "/api/3/contexts/Person",
    "@id": "/api/3/people",
    "@type": "hydra:Collection",
    "hydra:member": [_PERSON],
    "hydra:totalItems": 1,
}


class _Rec:
    def __init__(self, *, get_response=None, post_response=None, put_response=None):
        self._get = get_response
        self._post = post_response
        self._put = put_response

    def get(self, endpoint, params=None, **kw):
        return self._get

    def post(self, endpoint, data=None, params=None, **kw):
        return self._post

    def put(self, endpoint, data=None, params=None, **kw):
        return self._put


def test_list_returns_typed_users_by_default():
    api = UsersAPI(_Rec(get_response=_PEOPLE_ENVELOPE))
    out = api.list()
    assert len(out) == 1
    assert isinstance(out[0], User)
    assert out[0].name == "CS Admin"
    assert out[0]["email"] == "admin@example.com"  # dict-compat still works


def test_list_typed_false_returns_raw_envelope():
    api = UsersAPI(_Rec(get_response=_PEOPLE_ENVELOPE))
    out = api.list(typed=False)
    assert out == _PEOPLE_ENVELOPE


def test_get_returns_typed_user():
    api = UsersAPI(_Rec(get_response=_PERSON))
    out = api.get("3451141c-bac6-467c-8d72-85e0fab569ce")
    assert isinstance(out, User)
    assert out.firstname == "CS"


def test_get_typed_false_returns_raw_dict():
    api = UsersAPI(_Rec(get_response=_PERSON))
    out = api.get("3451141c-bac6-467c-8d72-85e0fab569ce", typed=False)
    assert out == _PERSON


def test_update_returns_typed_user():
    updated = dict(_PERSON, csActive=False)
    api = UsersAPI(_Rec(put_response=updated))
    out = api.update("3451141c-bac6-467c-8d72-85e0fab569ce", csActive=False)
    assert isinstance(out, User)
    assert out.csActive is False


_USER_ID = "edf7fe38-0759-451b-9d79-793671632ede"
_DAS_USER = {"uuid": _USER_ID, "loginid": "admin", "status": 1, "user_type": 2, "access_type": "Named"}


class _Router:
    """Answers by endpoint and records every PUT."""

    def __init__(self, person):
        self.person = person
        self.puts = []

    def get(self, endpoint, params=None, **kw):
        if endpoint == "/api/auth/users":
            hit = params in ({"uuid": _USER_ID}, {"loginid": _DAS_USER["loginid"]})
            return {"usersresp": [_DAS_USER] if hit else []}
        if endpoint == "/api/3/people":  # lookup(): People by userId
            return {"hydra:member": [self.person]}
        return self.person

    def put(self, endpoint, data=None, params=None, **kw):
        self.puts.append((endpoint, data))
        if endpoint == "/api/auth/users":
            return {"response": "Success"}
        return dict(self.person, **data)


def test_deactivate_blocks_login_then_marks_inactive():
    rec = _Router(dict(_PERSON, userId=_USER_ID))
    out = UsersAPI(rec).deactivate(_PERSON["uuid"])
    assert rec.puts == [
        ("/api/auth/users",
         {"update": {"uuid": _USER_ID, "status": 2, "user_type": 2, "access_type": "Named"}}),
        (f"/api/3/people/{_PERSON['uuid']}", {"csActive": False}),
    ]  # fmt: skip
    assert isinstance(out, User)
    assert out.csActive is False


def test_activate_restores_login_then_marks_active():
    rec = _Router(dict(_PERSON, userId=_USER_ID, csActive=False))
    out = UsersAPI(rec).activate(_PERSON["uuid"])
    assert rec.puts[0][1]["update"]["status"] == 1
    assert rec.puts[1] == (f"/api/3/people/{_PERSON['uuid']}", {"csActive": True})
    assert out.csActive is True


def test_deactivate_without_linked_login_raises_before_any_write():
    rec = _Router(dict(_PERSON))  # no userId
    try:
        UsersAPI(rec).deactivate(_PERSON["uuid"])
    except LookupError as e:
        assert "no linked login" in str(e)
    else:
        raise AssertionError("expected LookupError")
    assert rec.puts == []


def test_create_returns_typed_user(mocker):
    api = UsersAPI(_Rec(post_response=_PERSON))
    mocker.patch.object(api, "_resolve_roles", return_value=["role-uuid"])
    out = api.create(
        loginid="j.smith",
        password="Str0ng!Pass",
        firstname="Jane",
        lastname="Smith",
        email="j.smith@corp.example",
        roles=["SOC Analyst"],
    )
    assert isinstance(out, User)
    assert out.email == "admin@example.com"


def test_create_typed_false_returns_raw_dict(mocker):
    api = UsersAPI(_Rec(post_response=_PERSON))
    mocker.patch.object(api, "_resolve_roles", return_value=["role-uuid"])
    out = api.create(
        loginid="j.smith",
        password="Str0ng!Pass",
        firstname="Jane",
        lastname="Smith",
        email="j.smith@corp.example",
        roles=["SOC Analyst"],
        typed=False,
    )
    assert out == _PERSON


class _RecCapture:
    """Records every post so a test can assert on the payloads."""

    def __init__(self, post_response=None):
        self._post = post_response
        self.posts = []

    def get(self, endpoint, params=None, **kw):
        return None

    def post(self, endpoint, data=None, params=None, **kw):
        self.posts.append((endpoint, data))
        return self._post

    def put(self, endpoint, data=None, params=None, **kw):
        return None


def test_create_marks_login_active_and_sets_password(mocker):
    """create() must send user.status=1 (das defaults omitted -> 2/inactive) and
    set the password via the admin reset endpoint (the People POST leaves it
    unset), else the created user cannot log in."""
    rec = _RecCapture(post_response=_PERSON)
    api = UsersAPI(rec)
    mocker.patch.object(api, "_resolve_roles", return_value=["role-uuid"])
    api.create(
        loginid="j.smith",
        password="Str0ng!Pass",
        firstname="Jane",
        lastname="Smith",
        email="j.smith@corp.example",
        roles=["SOC Analyst"],
        typed=False,
    )
    people = next(d for e, d in rec.posts if e == "/api/3/people")
    assert people["user"]["status"] == 1
    reset = next(d for e, d in rec.posts if e == "/api/3/resetpassword")
    assert reset == {"loginId": "j.smith", "password": "Str0ng!Pass", "confirmPassword": "Str0ng!Pass"}


def test_create_inactive_sets_status_2(mocker):
    rec = _RecCapture(post_response=_PERSON)
    api = UsersAPI(rec)
    mocker.patch.object(api, "_resolve_roles", return_value=["role-uuid"])
    api.create(
        loginid="j.smith",
        password="p",
        firstname="J",
        lastname="S",
        email="j@x.example",
        roles=["SOC Analyst"],
        active=False,
        typed=False,
    )
    people = next(d for e, d in rec.posts if e == "/api/3/people")
    assert people["user"]["status"] == 2
    assert people["csActive"] is False


def test_deactivate_by_login_id():
    rec = _Router(dict(_PERSON, userId=_USER_ID))
    out = UsersAPI(rec).deactivate("admin")
    assert rec.puts[0][1]["update"] == {"uuid": _USER_ID, "status": 2, "user_type": 2, "access_type": "Named"}
    assert rec.puts[1] == (f"/api/3/people/{_PERSON['uuid']}", {"csActive": False})
    assert out.csActive is False


def test_deactivate_unknown_login_id_raises_before_any_write():
    rec = _Router(dict(_PERSON, userId=_USER_ID))
    try:
        UsersAPI(rec).deactivate("nobody")
    except LookupError as e:
        assert "nobody" in str(e)
    else:
        raise AssertionError("expected LookupError")
    assert rec.puts == []


class _Resp:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body
        self.ok = status_code < 400

    def json(self):
        return self._body


class _DasStatus:
    """das answers an unknown login with 400, not an empty list."""

    def get(self, endpoint, params=None, **kw):
        return _Resp(400, {"reason": "Invalid credentials or account locked"})


def test_lookup_unknown_login_400_raises_lookup_error():
    try:
        UsersAPI(_DasStatus()).lookup("nobody")
    except LookupError as e:
        assert "nobody" in str(e)
    else:
        raise AssertionError("expected LookupError")
