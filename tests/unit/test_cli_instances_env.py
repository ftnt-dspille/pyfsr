"""Offline tests for ``pyfsr instances env`` / ``ssh-target`` / ``check --offline``.

Temp TOML + env files only; no network, never the developer's real registry.
"""

import json
import os
import stat

import pytest

from pyfsr.cli.__main__ import main

SECRET_KEY = "k-envfile-SECRET"
SECRET_PW = "inline-pw-SECRET"
SSH_PW = "ssh-pw-SECRET"


@pytest.fixture
def config(tmp_path):
    (tmp_path / ".env.box").write_text(
        f"FSR_BASE_URL=https://box.example.com\nFSR_API_KEY={SECRET_KEY}\nFSR_VERIFY_SSL=false\nEXTRA_FOR_MAKE=1\n",
        encoding="utf-8",
    )
    path = tmp_path / "instances.toml"
    path.write_text(
        f"""
default = "box"
[instances.box]
env_file = "{tmp_path / ".env.box"}"
[instances.box.appliance]
host = "ssh.example.com"
user = "admin"
port = 11000
key_path = "/keys/k"
password = "{SSH_PW}"

[instances.inline]
base_url = "https://inline.example.com"
[instances.inline.auth]
type = "credentials"
username = "u"
password = "{SECRET_PW}"
""",
        encoding="utf-8",
    )
    return str(path)


def _run(capfd, *argv):
    rc = main(["instances", *argv])
    return rc, capfd.readouterr().out


def test_env_prints_key_value_without_secrets(config, capfd):
    rc, out = _run(capfd, "env", "box", "--config", config)
    assert rc == 0
    lines = dict(line.split("=", 1) for line in out.splitlines())
    assert lines["FSR_BASE_URL"] == "https://box.example.com"
    assert lines["FSR_VERIFY_SSL"] == "false"
    assert lines["PYFSR_APPLIANCE_HOST"] == "ssh.example.com"
    assert lines["PYFSR_APPLIANCE_PORT"] == "11000"
    assert lines["PYFSR_ENV_FILE"].endswith(".env.box")
    assert SECRET_KEY not in out and SSH_PW not in out
    assert "FSR_API_KEY" not in out


def test_env_with_secrets_includes_them(config, capfd):
    _, out = _run(capfd, "env", "box", "--config", config, "--with-secrets")
    assert f"FSR_API_KEY={SECRET_KEY}" in out
    assert f"PYFSR_APPLIANCE_PASSWORD={SSH_PW}" in out


def test_env_shell_format_exports_and_quotes(config, capfd):
    _, out = _run(capfd, "env", "inline", "--config", config, "--format", "shell")
    assert "export FSR_BASE_URL=https://inline.example.com" in out
    assert "FSR_USERNAME" not in out


def test_env_json_and_field(config, capfd):
    _, out = _run(capfd, "env", "box", "--config", config, "--format", "json")
    assert json.loads(out)["FSR_BASE_URL"] == "https://box.example.com"
    _, out = _run(capfd, "env", "box", "--config", config, "--field", "base_url")
    assert out.strip() == "https://box.example.com"
    _, out = _run(capfd, "env", "box", "--config", config, "--field", "host")
    assert out.strip() == "ssh.example.com"


def test_env_unknown_field_and_alias_fail(config, capfd):
    assert main(["instances", "env", "box", "--config", config, "--field", "nope"]) != 0
    assert "no field" in capfd.readouterr().err
    assert main(["instances", "env", "ghost", "--config", config]) != 0
    assert "unknown instance" in capfd.readouterr().err


def test_env_file_path_reuses_backing_file(config, capfd, tmp_path):
    _, out = _run(capfd, "env", "box", "--config", config, "--env-file-path")
    assert out.strip() == str(tmp_path / ".env.box")


def test_env_file_path_materializes_0600_for_inline_entry(config, capfd, tmp_path):
    target = tmp_path / "out.env"
    _, out = _run(capfd, "env", "inline", "--config", config, "--env-file-path", "--out", str(target))
    assert out.strip() == str(target)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    text = target.read_text()
    assert f"FSR_PASSWORD={SECRET_PW}" in text and "FSR_USERNAME=u" in text


def test_ssh_target_from_appliance_subtable(config, capfd):
    _, out = _run(capfd, "ssh-target", "box", "--config", config, "--format", "json")
    tgt = json.loads(out)
    assert (tgt["host"], tgt["user"], tgt["port"], tgt["key_path"]) == ("ssh.example.com", "admin", 11000, "/keys/k")
    assert tgt["source"] == "appliance"
    assert SSH_PW not in out
    _, out = _run(capfd, "ssh-target", "box", "--config", config)
    assert out.strip() == "-p 11000 -i /keys/k -o IdentitiesOnly=yes admin@ssh.example.com"


def test_ssh_target_falls_back_to_base_url_host(config, capfd):
    _, out = _run(capfd, "ssh-target", "inline", "--config", config, "--format", "json")
    tgt = json.loads(out)
    assert (tgt["host"], tgt["user"], tgt["port"], tgt["source"]) == ("inline.example.com", "csadmin", 22, "base_url")


def test_check_offline_reports_resolves_and_ssh_profile(config, capfd):
    rc, out = _run(capfd, "check", "--all", "--offline", "--config", config, "--json")
    assert rc == 0
    rows = {r["instance"]: r for r in json.loads(out)}
    assert rows["box"]["status"] == "ok" and rows["box"]["ssh"] == "yes"
    assert rows["inline"]["ssh"] == "-"


def test_check_offline_fails_when_env_file_missing(config, tmp_path, capfd):
    (tmp_path / ".env.box").unlink()
    assert main(["instances", "check", "--offline", "--config", config]) != 0
    assert "No such file" in capfd.readouterr().err
