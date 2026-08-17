"""Unit tests for the ``pyfsr instances`` command group.

Every test drives a throwaway TOML through ``--config``, so nothing here reads
the developer's real ``~/.pyfsr/instances.toml`` or touches a live appliance.
"""

import json

import pytest

from pyfsr.cli.__main__ import main

TOML = """
default = "206"

[instances.206]
base_url = "https://206.example.com"
verify_ssl = false
[instances.206.auth]
type = "api_key"
key = "k-206-super-secret"

[instances.206.appliance]
user = "csadmin"
port = 2222
password = "<ssh-password>"

[instances.ga]
base_url = "https://ga.example.com"
[instances.ga.auth]
type = "credentials"
username = "dspille"
password = "<user-password>"
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "instances.toml"
    path.write_text(TOML, encoding="utf-8")
    return str(path)


def test_list_names_both_instances_and_marks_the_default(config, capfd):
    assert main(["instances", "list", "--config", config, "--json"]) == 0
    rows = json.loads(capfd.readouterr().out)
    by_alias = {r["instance"]: r for r in rows}
    assert set(by_alias) == {"206", "ga"}
    assert by_alias["206"]["default"] == "*"
    assert by_alias["ga"]["default"] == ""
    # The SSH column answers "can I `pyfsr appliance` this box".
    assert by_alias["206"]["ssh"] == "yes"
    assert by_alias["ga"]["ssh"] == "-"
    assert by_alias["ga"]["verify_ssl"] == "yes"


def test_list_reports_the_auth_kind_never_the_credential(config, capfd):
    assert main(["instances", "list", "--config", config]) == 0
    out = capfd.readouterr().out
    assert "api_key" in out
    assert "user:dspille" in out
    assert "k-206-super-secret" not in out
    assert "<user-password>" not in out


def test_show_defaults_to_the_registry_default(config, capfd):
    assert main(["instances", "show", "--config", config]) == 0
    out = capfd.readouterr().out
    assert "https://206.example.com" in out
    # The appliance subtable is the other half of "which box" -- show it.
    assert "ssh_port" in out and "2222" in out
    assert "<ssh-password>" not in out


def test_show_says_so_when_an_instance_has_no_ssh_profile(config, capfd):
    assert main(["instances", "show", "ga", "--config", config]) == 0
    assert "no [appliance] subtable" in capfd.readouterr().out


def test_unknown_alias_names_the_valid_ones(config, capfd):
    assert main(["instances", "show", "nope", "--config", config]) == 1
    err = capfd.readouterr().err
    assert "unknown instance" in err
    assert "'206'" in err and "'ga'" in err


# -- check ------------------------------------------------------------------
class _FakeClient:
    def __init__(self, version="8.0.0-6034", get_exc=None):
        self._version = version
        self._get_exc = get_exc

    def version(self):
        return self._version

    def get(self, _endpoint):
        if self._get_exc is not None:
            raise self._get_exc
        return {"hydra:member": []}


def _patch_clients(monkeypatch, mapping):
    """Route ``InstanceRegistry.client`` to a per-alias fake or exception."""
    from pyfsr.instances import InstanceRegistry

    def fake_client(self, alias=None):
        result = mapping[self.resolve(alias)]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(InstanceRegistry, "client", fake_client)


def test_check_all_ok_exits_zero(config, capfd, monkeypatch):
    _patch_clients(monkeypatch, {"206": _FakeClient(), "ga": _FakeClient("7.6.5-5662")})
    assert main(["instances", "check", "--config", config, "--json"]) == 0
    rows = {r["instance"]: r for r in json.loads(capfd.readouterr().out)}
    assert rows["206"]["status"] == "ok"
    assert rows["ga"]["version"] == "7.6.5-5662"


def test_check_separates_a_dead_box_from_a_dead_credential(config, capfd, monkeypatch):
    # The distinction the private alias->env-file maps could not make: 'ga' is up
    # and answering, but the credential the registry holds for it no longer works.
    _patch_clients(
        monkeypatch,
        {
            "206": ConnectionError("Failed to establish a new connection"),
            "ga": _FakeClient(get_exc=PermissionError("401 Unauthorized")),
        },
    )
    assert main(["instances", "check", "--config", config, "--json"]) == 1
    rows = {r["instance"]: r for r in json.loads(capfd.readouterr().out)}
    assert rows["206"]["status"] == "unreachable"
    assert rows["ga"]["status"] == "auth-failed"
    assert rows["ga"]["version"] == "8.0.0-6034"
    assert "401" in rows["ga"]["detail"]


def test_check_accepts_an_explicit_subset(config, capfd, monkeypatch):
    _patch_clients(monkeypatch, {"206": _FakeClient(), "ga": _FakeClient()})
    assert main(["instances", "check", "ga", "--config", config, "--json"]) == 0
    rows = json.loads(capfd.readouterr().out)
    assert [r["instance"] for r in rows] == ["ga"]


def test_check_rejects_an_unknown_alias_before_connecting(config, capfd, monkeypatch):
    _patch_clients(monkeypatch, {})  # any connection attempt would KeyError
    assert main(["instances", "check", "nope", "--config", config]) == 1
    assert "unknown instance(s)" in capfd.readouterr().err


def test_check_json_keeps_the_full_detail_the_table_truncates(config, capfd, monkeypatch):
    long = "x" * 400
    _patch_clients(monkeypatch, {"206": _FakeClient(), "ga": ConnectionError(long)})

    assert main(["instances", "check", "ga", "--config", config]) == 1
    assert long not in capfd.readouterr().out  # table: abridged

    assert main(["instances", "check", "ga", "--config", config, "--json"]) == 1
    assert long in json.loads(capfd.readouterr().out)[0]["detail"]
