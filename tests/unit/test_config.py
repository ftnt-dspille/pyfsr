"""Unit tests for env-driven client configuration."""

import pytest

from pyfsr import config as config_mod
from pyfsr.config import EnvConfig


class RecordingFortiSOAR:
    last = None

    def __init__(self, **kwargs):
        RecordingFortiSOAR.last = kwargs


@pytest.fixture
def recorder(monkeypatch):
    RecordingFortiSOAR.last = None
    monkeypatch.setattr(config_mod, "FortiSOAR", RecordingFortiSOAR)
    return RecordingFortiSOAR


# -- from_env ---------------------------------------------------------------
def test_from_env_api_key():
    cfg = EnvConfig.from_env({"FSR_BASE_URL": "soar.example.com", "FSR_API_KEY": "k"})
    assert cfg.base_url == "soar.example.com"
    assert cfg.auth == "k"
    assert cfg.verify_ssl is True
    assert cfg.port is None
    assert cfg.timeout == 30


def test_from_env_userpass_and_port_and_timeout():
    cfg = EnvConfig.from_env(
        {
            "FSR_HOST": "h",
            "FSR_USERNAME": "u",
            "FSR_PASSWORD": "p",
            "FSR_PORT": "8443",
            "FSR_TIMEOUT": "5",
        }
    )
    assert cfg.auth == ("u", "p")
    assert cfg.port == 8443
    assert cfg.timeout == 5


def test_from_env_verify_ssl_disabled():
    cfg = EnvConfig.from_env({"FSR_BASE_URL": "h", "FSR_API_KEY": "k", "FSR_VERIFY_SSL": "no"})
    assert cfg.verify_ssl is False


def test_from_env_api_key_takes_precedence_over_userpass():
    cfg = EnvConfig.from_env({"FSR_BASE_URL": "h", "FSR_API_KEY": "k", "FSR_USERNAME": "u", "FSR_PASSWORD": "p"})
    assert cfg.auth == "k"


def test_from_env_missing_base_url_raises():
    with pytest.raises(ValueError, match="FSR_BASE_URL"):
        EnvConfig.from_env({"FSR_API_KEY": "k"})


def test_from_env_missing_auth_raises():
    with pytest.raises(ValueError, match="FSR_API_KEY"):
        EnvConfig.from_env({"FSR_BASE_URL": "h"})


# -- client() ---------------------------------------------------------------
def test_client_passes_config_through(recorder):
    EnvConfig(base_url="h", auth="k", port=9000, timeout=12).client()
    assert recorder.last["base_url"] == "h"
    assert recorder.last["auth"] == "k"
    assert recorder.last["port"] == 9000
    assert recorder.last["timeout"] == 12


def test_client_overrides_win(recorder):
    EnvConfig(base_url="h", auth="k").client(verbose=True, timeout=99)
    assert recorder.last["verbose"] is True
    assert recorder.last["timeout"] == 99
