"""Unit tests for env-driven client configuration."""

import pytest

from pyfsr import config as config_mod
from pyfsr.config import EnvConfig

_USERPASS_TOML = """
[fortisoar]
base_url = "https://soar.example.com"
verify_ssl = false
port = 8443

[fortisoar.auth]
type = "user_pass"
username = "csadmin"
password = "pw"
"""

_APIKEY_TOML = """
[fortisoar]
base_url = "https://soar.example.com"

[fortisoar.auth]
type = "api_key"
key = "SEKRET"
"""


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


def test_from_env_missing_both_lists_all_keys():
    # T2.2: a fully-empty environment names host AND auth in one error.
    with pytest.raises(ValueError) as exc:
        EnvConfig.from_env({})
    msg = str(exc.value)
    assert "FSR_BASE_URL" in msg
    assert "FSR_API_KEY" in msg


# -- from_config_file -------------------------------------------------------
def test_from_config_file_userpass(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(_USERPASS_TOML)
    cfg = EnvConfig.from_config_file(p)
    assert cfg.base_url == "https://soar.example.com"
    assert cfg.auth == ("csadmin", "pw")
    assert cfg.verify_ssl is False
    assert cfg.port == 8443


def test_from_config_file_api_key(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(_APIKEY_TOML)
    cfg = EnvConfig.from_config_file(p)
    assert cfg.auth == "SEKRET"
    assert cfg.verify_ssl is True  # default when omitted


def test_from_config_file_missing_base_url_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[fortisoar]\n[fortisoar.auth]\nkey = "k"\n')
    with pytest.raises(ValueError, match="base_url"):
        EnvConfig.from_config_file(p)


def test_from_config_file_missing_auth_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[fortisoar]\nbase_url = "https://h"\n')
    with pytest.raises(ValueError, match="auth"):
        EnvConfig.from_config_file(p)


# -- from_env_file ----------------------------------------------------------
def test_from_env_file_parses_and_real_env_wins(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("# comment\nFSR_BASE_URL=https://from-file\nFSR_API_KEY=filekey\n\n")
    monkeypatch.setenv("FSR_API_KEY", "realkey")
    cfg = EnvConfig.from_env_file(p)
    assert cfg.base_url == "https://from-file"
    assert cfg.auth == "realkey"  # os.environ wins by default


def test_from_env_file_override(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("FSR_BASE_URL=https://from-file\nFSR_API_KEY=filekey\n")
    monkeypatch.setenv("FSR_API_KEY", "realkey")
    cfg = EnvConfig.from_env_file(p, override=True)
    assert cfg.auth == "filekey"  # file wins when override=True


# -- client() ---------------------------------------------------------------
def test_client_passes_config_through(recorder):
    EnvConfig(base_url="h", auth="k", port=9000, timeout=12).client()
    assert recorder.last["base_url"] == "h"
    assert recorder.last["token"] == "k"  # str auth -> token keyword (not the legacy auth=)
    assert "auth" not in recorder.last
    assert recorder.last["port"] == 9000
    assert recorder.last["timeout"] == 12


def test_client_translates_userpass_tuple(recorder):
    EnvConfig(base_url="h", auth=("u", "p")).client()
    assert recorder.last["username"] == "u"
    assert recorder.last["password"] == "p"
    assert "auth" not in recorder.last


def test_client_overrides_win(recorder):
    EnvConfig(base_url="h", auth="k").client(verbose=True, timeout=99)
    assert recorder.last["verbose"] is True
    assert recorder.last["timeout"] == 99
