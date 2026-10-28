"""API-key clients fail fast on the routes FortiSOAR refuses API keys on.

Each guard mirrors a measured "No" (live-verified on 8.0.0 and 8.0.1): the
request never leaves the client, and the error says to use a JWT client.
"""

import pytest

from pyfsr._testing import demo_client, demo_client_jwt
from pyfsr.auth.base import BaseAuth
from pyfsr.exceptions import UnsupportedAuthOperationError


def _passes_guard(call) -> None:
    """The call got past the auth guard (the offline replay may still lack a fixture)."""
    try:
        call()
    except UnsupportedAuthOperationError:
        pytest.fail("blocked by the API-key guard")
    except RuntimeError as exc:
        assert "no replay fixture" in str(exc)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/3/people"),
        ("PUT", "/api/3/people/3451141c-bac6-467c-8d72-000000000001"),
        ("POST", "/api/3/appliances"),
        ("POST", "/api/3/api_keys"),
        ("PUT", "/api/3/api_keys/0a0b0c0d-0000-0000-0000-000000000001?$relationships=true"),
        ("PUT", "people/3451141c-bac6-467c-8d72-000000000001"),  # bare path gets /api/3
    ],
)
def test_api_key_actor_write_is_refused(method, path):
    with pytest.raises(UnsupportedAuthOperationError) as exc:
        demo_client().request(method, path, data={})
    assert exc.value.operation == BaseAuth.OPERATION_ACTOR_WRITE
    assert "JWT" in str(exc.value)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/3/people"),  # reads are fine
        ("GET", "/api/3/api_keys"),
        ("POST", "/api/3/people/3451141c-bac6-467c-8d72-000000000001/avatar"),  # sub-route
        ("POST", "/api/3/alerts"),  # not an Actor
    ],
)
def test_api_key_non_actor_write_passes(method, path):
    _passes_guard(lambda: demo_client().request(method, path, data={}))


def test_jwt_actor_write_passes():
    _passes_guard(lambda: demo_client_jwt().request("POST", "/api/3/api_keys", data={}))


def test_api_key_direct_ai_is_refused():
    with pytest.raises(UnsupportedAuthOperationError) as exc:
        demo_client().request("POST", "/ai/agent/import")
    assert exc.value.operation == BaseAuth.OPERATION_AI_DIRECT
    assert "/api/ai/" in str(exc.value)


def test_api_key_gateway_ai_passes():
    _passes_guard(lambda: demo_client().request("GET", "/api/ai/agent"))


@pytest.mark.parametrize("method", ["generate_options", "trigger"])
def test_api_key_config_import_is_refused(method):
    with pytest.raises(UnsupportedAuthOperationError) as exc:
        getattr(demo_client().import_config, method)("aa0e8400-e29b-41d4-a716-446655440013")
    assert exc.value.operation == BaseAuth.OPERATION_CONFIG_IMPORT


def test_api_key_import_file_is_refused_before_upload(tmp_path):
    bundle = tmp_path / "export.zip"
    bundle.write_bytes(b"PK")
    with pytest.raises(UnsupportedAuthOperationError):
        demo_client().import_config.import_file(str(bundle))


def test_api_key_solutionpack_install_is_refused():
    # Content Hub install's async connector-import step forwards the caller's
    # credential as a Bearer token; an API key is not a JWT, so the job stalls
    # at Draft forever. Live-verified on 8.0.1: JWT installs process, the same
    # install under an API key never leaves Draft. The guard fails fast instead.
    with pytest.raises(UnsupportedAuthOperationError) as exc:
        demo_client().solution_packs.install("http", "1.0.0")
    assert exc.value.operation == BaseAuth.OPERATION_SOLUTIONPACK_INSTALL
    assert "JWT" in str(exc.value)


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.connectors.install("http", "1.0.0"),
        lambda c: c.connectors.install_from_file("/tmp/does-not-matter.tgz"),
        lambda c: c.connectors.install_from_dir("/tmp/does-not-matter"),
    ],
)
def test_api_key_connector_install_is_refused(call):
    with pytest.raises(UnsupportedAuthOperationError) as exc:
        call(demo_client())
    assert exc.value.operation == BaseAuth.OPERATION_SOLUTIONPACK_INSTALL


def test_jwt_solutionpack_install_passes_guard():
    # The guard follows the auth class: a JWT client is not blocked by it.
    _passes_guard(lambda: demo_client_jwt().solution_packs.install("http", "1.0.0"))
