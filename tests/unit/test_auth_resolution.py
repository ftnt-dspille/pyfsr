"""Constructor auth-resolution: explicit kwargs, lone-secret inference, legacy path."""

import warnings

import pytest
from requests import Session

from pyfsr import FortiSOAR
from pyfsr.auth.api_key import APIKeyAuth
from pyfsr.auth.user_pass import UserPasswordAuth

URL = "https://test.fortisoar.com"


@pytest.fixture
def mock_session(monkeypatch):
    """Stub out the network so auth init (token fetch / key validation) succeeds."""

    class _Resp:
        status_code = 200
        ok = True
        headers = {"Content-Type": "application/json"}

        def json(self):
            return {"token": "mock-token-123"}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(Session, "request", lambda *a, **k: _Resp())
    # Auth backends call module-level requests.get/post, not the session.
    import pyfsr.auth.api_key as ak
    import pyfsr.auth.user_pass as up

    monkeypatch.setattr(ak.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(up.requests, "post", lambda *a, **k: _Resp())


def _client(**kwargs):
    return FortiSOAR(URL, verify_ssl=False, suppress_insecure_warnings=True, **kwargs)


def test_username_password_kwargs(mock_session):
    assert isinstance(_client(username="u", password="p").auth, UserPasswordAuth)


def test_token_kwarg(mock_session):
    assert isinstance(_client(token="k").auth, APIKeyAuth)


def test_api_key_alias(mock_session):
    assert isinstance(_client(api_key="k").auth, APIKeyAuth)


def test_lone_password_inferred_as_api_key(mock_session):
    # The behavior the user asked for: a single secret with no username is a key.
    assert isinstance(_client(password="k").auth, APIKeyAuth)


def test_legacy_tuple_warns_but_works(mock_session):
    with pytest.warns(DeprecationWarning):
        client = _client(auth=("u", "p"))
    assert isinstance(client.auth, UserPasswordAuth)


def test_legacy_str_warns_but_works(mock_session):
    with pytest.warns(DeprecationWarning):
        client = _client(auth="k")
    assert isinstance(client.auth, APIKeyAuth)


def test_no_auth_raises(mock_session):
    with pytest.raises(ValueError):
        _client()


def test_username_without_password_raises(mock_session):
    with pytest.raises(ValueError):
        _client(username="u")


def test_positional_and_keyword_conflict_raises(mock_session):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(ValueError):
            _client(auth="k", token="t")
