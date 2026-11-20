"""Unit tests for ``pyfsr instances add``.

Every test writes into a throwaway directory through ``--config``; the connection
probe is replaced, so nothing reaches a real appliance or ~/.pyfsr.
"""

import io
import stat

import pytest

from pyfsr.cli import instances as cli_instances
from pyfsr.cli.__main__ import main
from pyfsr.instances import InstanceRegistry


@pytest.fixture
def probe(monkeypatch):
    """Record what the probe saw and answer ``ok`` (tests flip ``result`` for failures)."""
    seen = {"result": ("ok", "8.0.1", ""), "calls": 0}

    def fake(make_client):
        seen["calls"] += 1
        return seen["result"]

    monkeypatch.setattr(cli_instances, "_probe_with", fake)
    return seen


@pytest.fixture
def stdin(monkeypatch):
    def feed(text):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    return feed


def _add(config, *argv):
    return main(["instances", "add", "--config", str(config), "--no-input", *argv])


def test_api_key_box_is_saved_with_the_secret_only_in_a_0600_env_file(tmp_path, probe, stdin, capfd):
    config = tmp_path / "instances.toml"
    stdin("k-secret-123\n")
    assert _add(config, "soc", "--url", "soc.example.com") == 0

    toml = config.read_text()
    assert "k-secret-123" not in toml
    assert '[instances.soc]\nenv_file = "soc.env"' in toml
    assert 'default = "soc"' in toml  # the first box becomes the default

    env_file = tmp_path / "soc.env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "FSR_API_KEY=k-secret-123" in env_file.read_text()

    reg = InstanceRegistry.load(config)
    assert reg.configs["soc"].base_url == "https://soc.example.com"
    assert reg.configs["soc"].auth == "k-secret-123"
    assert reg.default == "soc"
    out = capfd.readouterr().out
    assert "k-secret-123" not in out
    assert "--instance soc" in out


def test_password_login_reads_the_password_from_stdin_never_argv(tmp_path, probe, stdin):
    config = tmp_path / "instances.toml"
    stdin("hunter2\n")
    assert _add(config, "lab", "--url", "https://lab.example.com", "--username", "agentlab", "--insecure") == 0
    cfg = InstanceRegistry.load(config).configs["lab"]
    assert cfg.auth == ("agentlab", "hunter2")
    assert cfg.verify_ssl is False


def test_a_failed_connection_saves_nothing(tmp_path, probe, stdin, capfd):
    config = tmp_path / "instances.toml"
    probe["result"] = ("auth-failed", "8.0.1", "401 Unauthorized")
    stdin("wrong\n")
    assert _add(config, "soc", "--url", "soc.example.com") == 1
    assert not config.exists()
    assert not (tmp_path / "soc.env").exists()
    assert "nothing was saved" in capfd.readouterr().out


def test_no_check_saves_without_connecting(tmp_path, probe, stdin):
    stdin("k\n")
    assert _add(tmp_path / "instances.toml", "soc", "--url", "soc.example.com", "--no-check") == 0
    assert probe["calls"] == 0


def test_a_second_box_keeps_the_first_as_default_and_the_file_comments(tmp_path, probe, stdin):
    config = tmp_path / "instances.toml"
    config.write_text(
        '# my boxes\ndefault = "a"\n\n[instances.a]\nbase_url = "https://a.example.com"\n'
        '[instances.a.auth]\ntype = "api_key"\nkey = "ka"\n'
    )
    stdin("kb\n")
    assert _add(config, "b", "--url", "b.example.com") == 0
    text = config.read_text()
    assert text.startswith("# my boxes\n")
    reg = InstanceRegistry.load(config)
    assert set(reg.names()) == {"a", "b"}
    assert reg.default == "a"


def test_default_flag_moves_the_default(tmp_path, probe, stdin):
    config = tmp_path / "instances.toml"
    config.write_text(
        'default = "a"\n[instances.a]\nbase_url = "https://a.example.com"\n'
        '[instances.a.auth]\ntype = "api_key"\nkey = "ka"\n'
    )
    stdin("kb\n")
    assert _add(config, "b", "--url", "b.example.com", "--default") == 0
    assert InstanceRegistry.load(config).default == "b"


def test_an_existing_alias_is_refused(tmp_path, probe, stdin, capfd):
    config = tmp_path / "instances.toml"
    stdin("k\n")
    assert _add(config, "soc", "--url", "soc.example.com") == 0
    stdin("k\n")
    assert _add(config, "soc", "--url", "other.example.com") != 0
    assert "already exists" in capfd.readouterr().err


def test_env_file_registers_an_existing_file_without_copying_it(tmp_path, probe):
    existing = tmp_path / "elsewhere" / ".env.box"
    existing.parent.mkdir()
    existing.write_text("FSR_BASE_URL=https://box.example.com\nFSR_API_KEY=kbox\n")
    config = tmp_path / "reg" / "instances.toml"
    assert _add(config, "box", "--env-file", str(existing)) == 0
    assert not (config.parent / "box.env").exists()
    assert InstanceRegistry.load(config).configs["box"].auth == "kbox"


def test_a_bad_alias_is_refused(tmp_path, probe, capfd):
    assert _add(tmp_path / "instances.toml", "bad name", "--url", "x.example.com") != 0
    assert "alias" in capfd.readouterr().err


def test_api_key_comes_from_the_environment_when_set(tmp_path, probe, monkeypatch):
    monkeypatch.setenv("FSR_API_KEY", "k-env")
    config = tmp_path / "instances.toml"
    assert _add(config, "soc", "--url", "soc.example.com") == 0
    assert InstanceRegistry.load(config).configs["soc"].auth == "k-env"
