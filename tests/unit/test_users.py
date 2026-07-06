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


def test_deactivate_returns_typed_user():
    updated = dict(_PERSON, csActive=False)
    api = UsersAPI(_Rec(put_response=updated))
    out = api.deactivate("3451141c-bac6-467c-8d72-85e0fab569ce")
    assert isinstance(out, User)
    assert out.csActive is False


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
