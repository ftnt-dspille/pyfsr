"""Fail-fast guards for JWT-only operations (pyfsr.auth.guards)."""

from __future__ import annotations

import warnings
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pyfsr.api.ai import AIApi
from pyfsr.api.api_users import ApiKeyUsersAPI
from pyfsr.api.auth_config import AuthConfigAPI
from pyfsr.api.export_config import ExportConfigAPI
from pyfsr.api.playbooks import PlaybooksAPI
from pyfsr.api.system import SystemAPI
from pyfsr.api.view_templates import ViewTemplatesAPI
from pyfsr.auth.api_key import APIKeyAuth
from pyfsr.auth.base import BaseAuth
from pyfsr.auth.guards import ensure_jwt, jwt_only, requires_jwt
from pyfsr.auth.no_auth import NoAuth
from pyfsr.exceptions import UnsupportedAuthOperationError


class _JwtAuth(BaseAuth):
    def get_auth_headers(self) -> dict:
        return {"Authorization": "Bearer jwt-token"}


def _api_key_auth() -> APIKeyAuth:
    with patch.object(APIKeyAuth, "_validate_api_key"):
        return APIKeyAuth("https://soar.example.com", "key")


class _Recorder:
    """Stands in for the FortiSOAR client: records requests, never touches the network."""

    def __init__(self, auth: BaseAuth) -> None:
        self.auth = auth
        self.calls: list[tuple] = []

    def get(self, *a, **k):
        self.calls.append(("get", a, k))
        return {}

    post = put = delete = get


def test_api_key_lists_forwarded_bearer_unsupported() -> None:
    assert BaseAuth.OPERATION_FORWARDED_BEARER in _api_key_auth().unsupported_operations
    assert BaseAuth.OPERATION_FORWARDED_BEARER not in _JwtAuth().unsupported_operations
    assert BaseAuth.OPERATION_FORWARDED_BEARER in NoAuth().unsupported_operations


def test_requires_jwt_blocks_api_key_before_any_request() -> None:
    client = _Recorder(_api_key_auth())
    api = SystemAPI(client)  # type: ignore[arg-type]
    with pytest.raises(UnsupportedAuthOperationError) as exc:
        api.cluster_health()
    assert client.calls == []
    assert exc.value.operation == BaseAuth.OPERATION_AUTH
    assert "SystemAPI.cluster_health()" in str(exc.value)
    assert "username=" in str(exc.value)  # tells the caller how to fix it


def test_requires_jwt_allows_jwt() -> None:
    client = _Recorder(_JwtAuth())
    SystemAPI(client).cluster_health()  # type: ignore[arg-type]
    assert client.calls and client.calls[0][1][0] == "/api/auth/cluster/health"


def test_decorator_marks_function_and_docstring() -> None:
    assert SystemAPI.cluster_health.__requires_jwt__ == BaseAuth.OPERATION_AUTH
    assert "Requires JWT" in SystemAPI.cluster_health.__doc__


def test_decorator_works_on_async_methods() -> None:
    import asyncio

    class Thing:
        def __init__(self, auth):
            self.client = SimpleNamespace(auth=auth)

        @requires_jwt(BaseAuth.OPERATION_AUTH, "reason")
        async def go(self):
            return "ok"

    assert asyncio.run(Thing(_JwtAuth()).go()) == "ok"
    with pytest.raises(UnsupportedAuthOperationError):
        asyncio.run(Thing(_api_key_auth()).go())


def test_jwt_only_class_guards_public_methods_only() -> None:
    @jwt_only(BaseAuth.OPERATION_AUTH, "reason")
    class Api:
        def __init__(self, auth):
            self.client = SimpleNamespace(auth=auth)

        def public(self):
            return "ok"

        def _private(self):
            return "private"

    assert Api(_JwtAuth()).public() == "ok"
    with pytest.raises(UnsupportedAuthOperationError):
        Api(_api_key_auth()).public()
    assert Api(_api_key_auth())._private() == "private"


@pytest.mark.parametrize(
    ("api_cls", "call"),
    [
        (ApiKeyUsersAPI, lambda a: a.get("00000000-0000-0000-0000-000000000000")),
        (AuthConfigAPI, lambda a: a.get("TOKEN")),
        (SystemAPI, lambda a: a.license()),
        (SystemAPI, lambda a: a.deploy_license("x")),
        (ExportConfigAPI, lambda a: a.export_by_template_uuid("x")),
        (ExportConfigAPI, lambda a: a.export_by_template_name("x")),
        (ExportConfigAPI, lambda a: a.create_export_template("x", {})),
        (PlaybooksAPI, lambda a: a.create_playbooks([])),
        (ViewTemplatesAPI, lambda a: a.bulk_upsert_templates([])),
    ],
)
def test_known_jwt_only_surfaces_reject_api_key(api_cls, call) -> None:
    client = _Recorder(_api_key_auth())
    api = api_cls(client)
    with pytest.raises(UnsupportedAuthOperationError):
        call(api)
    assert client.calls == []


def test_ensure_jwt_accepts_client_api_or_auth() -> None:
    key = _api_key_auth()
    for obj in (key, SimpleNamespace(auth=key), SimpleNamespace(client=SimpleNamespace(auth=key))):
        with pytest.raises(UnsupportedAuthOperationError):
            ensure_jwt(obj, BaseAuth.OPERATION_FORWARDED_BEARER, "why")
    ensure_jwt(_JwtAuth(), BaseAuth.OPERATION_FORWARDED_BEARER, "why")


def _ai(auth: BaseAuth, record: dict, url: str = "https://localhost/mcp/utility/") -> AIApi:
    client = _Recorder(auth)
    client.base_url = "https://soar.example.com:13000"
    api = AIApi(client)  # type: ignore[arg-type]
    api.get_mcp_config = lambda name: SimpleNamespace(  # type: ignore[method-assign]
        url=url, authentication=record, get=lambda k, d=None: d
    )
    client.verify_ssl = False
    return api


def test_fsr_auth_mcp_server_needs_jwt_for_api_key_client() -> None:
    api = _ai(_api_key_auth(), {"type": "FSR"})
    with pytest.raises(UnsupportedAuthOperationError) as exc:
        api._resolve_registered_endpoint("Utility Tools", None)
    assert "Utility Tools" in str(exc.value)


def test_fsr_auth_mcp_server_uses_the_clients_jwt() -> None:
    api = _ai(_JwtAuth(), {"type": "FSR"})
    _url, headers, _verify = api._resolve_registered_endpoint("Utility Tools", None)
    assert headers == {"Authorization": "Bearer jwt-token"}


def test_fsr_auth_jwt_goes_only_to_the_appliance() -> None:
    # built-in servers register as localhost: rewritten to the appliance the client talks to
    api = _ai(_JwtAuth(), {"type": "FSR"})
    url, _h, verify = api._resolve_registered_endpoint("Utility Tools", None)
    assert url == "https://soar.example.com:13000/mcp/utility/" and verify is False
    # a record naming the appliance's host on another scheme/port is pinned to the client's own origin
    api = _ai(_JwtAuth(), {"type": "FSR"}, url="http://soar.example.com:8080/mcp/soc/?x=1")
    assert api._resolve_registered_endpoint("SOC", None)[0] == "https://soar.example.com:13000/mcp/soc/?x=1"


def test_fsr_auth_jwt_is_never_sent_to_another_host() -> None:
    api = _ai(_JwtAuth(), {"type": "FSR"}, url="https://attacker.example.net/mcp/")
    with pytest.raises(ValueError, match="refusing to send your JWT"):
        api._resolve_registered_endpoint("Evil", None)


def test_fsr_auth_jwt_header_is_always_authorization_bearer() -> None:
    api = _ai(_JwtAuth(), {"type": "FSR", "header_name": "X-Leak", "prefix": "Token"})
    _url, headers, _v = api._resolve_registered_endpoint("Utility Tools", None)
    assert headers == {"Authorization": "Bearer jwt-token"}


def test_fsr_auth_jwt_ignores_the_records_verify_false() -> None:
    api = _ai(_JwtAuth(), {"type": "FSR", "verify": False})
    api.client.verify_ssl = True
    assert api._resolve_registered_endpoint("Utility Tools", None)[2] is True


def test_fsr_auth_mcp_explicit_token_wins_even_for_api_key() -> None:
    api = _ai(_api_key_auth(), {"type": "FSR"})
    _url, headers, _verify = api._resolve_registered_endpoint("Utility Tools", "caller-jwt")
    assert headers == {"Authorization": "Bearer caller-jwt"}


def test_mcp_status_warns_when_api_key_gets_401() -> None:
    client = _Recorder(_api_key_auth())
    client.get = lambda *a, **k: [  # type: ignore[method-assign]
        {"uuid": "1", "name": "Utility Tools", "valid": False, "error": "401 Unauthorized"},
        {"uuid": "2", "name": "FortiSIEM", "valid": True, "error": None},
    ]
    with pytest.warns(UserWarning, match="Utility Tools"):
        AIApi(client).mcp_status()  # type: ignore[arg-type]


def test_mcp_status_silent_for_jwt() -> None:
    client = _Recorder(_JwtAuth())
    client.get = lambda *a, **k: [{"uuid": "1", "name": "X", "valid": False, "error": "401 Unauthorized"}]  # type: ignore[method-assign]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        AIApi(client).mcp_status()  # type: ignore[arg-type]
