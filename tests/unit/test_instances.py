"""Unit tests for the multi-instance registry (pyfsr.instances)."""

from pathlib import Path

import pytest

from pyfsr.instances import InstanceRegistry, default_search_path


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


# -- config-file parsing ----------------------------------------------------
def test_from_config_file_inline_and_env_file(tmp_path):
    _write(
        tmp_path / ".env.206",
        "FSR_BASE_URL=https://10.0.0.206\nFSR_USERNAME=admin\nFSR_PASSWORD=secret\nFSR_VERIFY_SSL=false\n",
    )
    _write(
        tmp_path / "instances.toml",
        """
        default = "206"

        [instances.206]
        env_file = ".env.206"

        [instances.ga]
        base_url = "https://ga.example.com"
        verify_ssl = false
        [instances.ga.auth]
        type = "api_key"
        key = "k-123"
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")

    assert reg.names() == ["206", "ga"]
    assert reg.default == "206"
    # env_file form: creds + host resolved, relative path resolved against the toml dir.
    assert reg.configs["206"].base_url == "https://10.0.0.206"
    assert reg.configs["206"].auth == ("admin", "secret")
    assert reg.configs["206"].verify_ssl is False
    # inline form: api-key auth resolves to a bare string.
    assert reg.configs["ga"].base_url == "https://ga.example.com"
    assert reg.configs["ga"].auth == "k-123"


def test_env_file_does_not_leak_process_environ(tmp_path, monkeypatch):
    # A stray FSR_* in the process must NOT override an instance's own env_file.
    monkeypatch.setenv("FSR_BASE_URL", "https://leaked.example.com")
    _write(tmp_path / ".env.a", "FSR_BASE_URL=https://a.example.com\nFSR_API_KEY=ka\n")
    _write(
        tmp_path / "instances.toml",
        '[instances.a]\nenv_file = ".env.a"\n',
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    assert reg.configs["a"].base_url == "https://a.example.com"


def test_single_instance_gets_implicit_default(tmp_path):
    _write(
        tmp_path / "instances.toml",
        '[instances.only]\nbase_url = "https://x"\n[instances.only.auth]\ntype = "api_key"\nkey = "k"\n',
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    assert reg.default == "only"


def test_bad_default_raises(tmp_path):
    _write(
        tmp_path / "instances.toml",
        'default = "nope"\n[instances.a]\nbase_url = "https://x"\n[instances.a.auth]\ntype="api_key"\nkey="k"\n',
    )
    with pytest.raises(ValueError, match="default"):
        InstanceRegistry.from_config_file(tmp_path / "instances.toml")


def test_no_instances_table_raises(tmp_path):
    _write(tmp_path / "instances.toml", 'default = "x"\n')
    with pytest.raises(ValueError, match="no \\[instances"):
        InstanceRegistry.from_config_file(tmp_path / "instances.toml")


# -- resolution + caching ---------------------------------------------------
def _two_instance_registry():
    from pyfsr.config import EnvConfig

    return InstanceRegistry(
        configs={
            "a": EnvConfig(base_url="https://a", auth="ka"),
            "b": EnvConfig(base_url="https://b", auth="kb"),
        },
        default="a",
    )


def test_resolve_default_and_explicit():
    reg = _two_instance_registry()
    assert reg.resolve(None) == "a"
    assert reg.resolve("") == "a"
    assert reg.resolve("b") == "b"


def test_resolve_unknown_raises():
    reg = _two_instance_registry()
    with pytest.raises(ValueError, match="unknown instance 'zzz'"):
        reg.resolve("zzz")


def test_resolve_no_default_requires_explicit():
    reg = _two_instance_registry()
    reg.default = None
    with pytest.raises(ValueError, match="no default"):
        reg.resolve(None)


class _FakeCfg:
    """A config whose ``client()`` returns a fresh sentinel and counts calls."""

    def __init__(self, tag):
        self.tag = tag
        self.calls = 0

    def client(self):
        self.calls += 1
        return (self.tag, self.calls)


def test_client_is_cached_per_alias():
    cfgs = {"a": _FakeCfg("a"), "b": _FakeCfg("b")}
    reg = InstanceRegistry(configs=cfgs, default="a")

    c_a1 = reg.client("a")
    c_a2 = reg.client("a")
    c_b = reg.client("b")
    assert c_a1 is c_a2  # same alias -> same cached client, built once
    assert cfgs["a"].calls == 1
    assert c_b is not c_a1
    assert reg.client(None) is c_a1  # default routes to "a", still cached


def test_describe_hides_secrets():
    reg = _two_instance_registry()
    desc = reg.describe()
    assert {d["instance"] for d in desc} == {"a", "b"}
    dumped = str(desc)
    assert "ka" not in dumped and "kb" not in dumped
    assert any(d["default"] for d in desc if d["instance"] == "a")


# -- load() fallback --------------------------------------------------------
def test_load_falls_back_to_env_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PYFSR_INSTANCES", str(tmp_path / "missing.toml"))
    reg = InstanceRegistry.load(env={"FSR_BASE_URL": "https://env.example.com", "FSR_API_KEY": "k"})
    assert reg.names() == ["default"]
    assert reg.default == "default"
    assert reg.configs["default"].base_url == "https://env.example.com"


def test_default_search_path_honors_override(monkeypatch):
    monkeypatch.setenv("PYFSR_INSTANCES", "/custom/path.toml")
    assert str(default_search_path()) == "/custom/path.toml"
    monkeypatch.delenv("PYFSR_INSTANCES", raising=False)
    assert default_search_path().name == "instances.toml"


# -- appliance SSH profile (C4) ---------------------------------------------
def test_appliance_subtable_inline_fields(tmp_path):
    _write(
        tmp_path / "instances.toml",
        """
        default = "206"

        [instances.206]
        base_url = "https://10.0.0.206"
        [instances.206.auth]
        type = "api_key"
        key = "k-123"

        [instances.206.appliance]
        user = "admin"
        password = "secret"
        port = 13000
        key_path = "~/.ssh/id_ed25519"
        sudo_password = "your-password"
        insecure_skip_host_key_check = true
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    assert reg.appliance_names() == ["206"]
    spec = reg.appliance_specs["206"]
    # host defaults to the hostname parsed from base_url (no repetition needed).
    assert spec.host == "10.0.0.206"
    assert spec.user == "admin"
    assert spec.password == "secret"
    assert spec.port == 13000
    assert spec.key_path == str(Path("~/.ssh/id_ed25519").expanduser())
    assert spec.sudo_password == "your-password"
    assert spec.insecure_skip_host_key_check is True


def test_appliance_subtable_host_override(tmp_path):
    _write(
        tmp_path / "instances.toml",
        """
        [instances.jump]
        base_url = "https://rest.example.com"
        [instances.jump.auth]
        type = "api_key"
        key = "k"

        [instances.jump.appliance]
        host = "10.0.0.60"
        user = "csadmin"
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    # Explicit host wins over the base_url-derived default (jump host scenario).
    assert reg.appliance_specs["jump"].host == "10.0.0.60"
    assert reg.appliance_specs["jump"].user == "csadmin"


def test_appliance_subtable_env_file(tmp_path):
    _write(
        tmp_path / ".env.206.ssh",
        "PYFSR_APPLIANCE_HOST=10.0.0.206\nPYFSR_APPLIANCE_USER=admin\n"
        "PYFSR_APPLIANCE_PASSWORD=secret\nPYFSR_APPLIANCE_PORT=2222\n"
        "PYFSR_APPLIANCE_KEY_PATH=/home/admin/.ssh/key\n"
        "PYFSR_APPLIANCE_SUDO_PASSWORD=sudo\n"
        "PYFSR_APPLIANCE_INSECURE_SKIP_HOST_KEY_CHECK=true\n",
    )
    _write(
        tmp_path / "instances.toml",
        """
        [instances.206]
        base_url = "https://10.0.0.206"
        [instances.206.auth]
        type = "api_key"
        key = "k"

        [instances.206.appliance]
        env_file = ".env.206.ssh"
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    spec = reg.appliance_specs["206"]
    assert spec.host == "10.0.0.206"
    assert spec.user == "admin"
    assert spec.password == "secret"
    assert spec.port == 2222
    assert spec.key_path == "/home/admin/.ssh/key"
    assert spec.sudo_password == "sudo"
    assert spec.insecure_skip_host_key_check is True


def test_appliance_subtable_inline_overrides_env_file(tmp_path):
    _write(
        tmp_path / ".env.ssh",
        "PYFSR_APPLIANCE_HOST=file-host\nPYFSR_APPLIANCE_USER=file-user\n",
    )
    _write(
        tmp_path / "instances.toml",
        """
        [instances.x]
        base_url = "https://x"
        [instances.x.auth]
        type = "api_key"
        key = "k"

        [instances.x.appliance]
        env_file = ".env.ssh"
        user = "inline-user"
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    spec = reg.appliance_specs["x"]
    # Inline field wins over the env_file's var for the same key.
    assert spec.user == "inline-user"
    # host comes from the env_file (no inline host given, no base_url default needed).
    assert spec.host == "file-host"


def test_appliance_subtable_env_file_relative_to_toml_dir(tmp_path, monkeypatch):
    # A relative env_file resolves against the toml's dir, not the CWD.
    monkeypatch.chdir(tmp_path)  # ensure CWD is *not* where the file lives
    sub = tmp_path / "sub"
    sub.mkdir()
    _write(sub / ".env.ssh", "PYFSR_APPLIANCE_HOST=10.0.0.206\nPYFSR_APPLIANCE_PASSWORD=p\n")
    _write(
        sub / "instances.toml",
        """
        [instances.206]
        base_url = "https://10.0.0.206"
        [instances.206.auth]
        type = "api_key"
        key = "k"

        [instances.206.appliance]
        env_file = ".env.ssh"
        """,
    )
    reg = InstanceRegistry.from_config_file(sub / "instances.toml")
    assert reg.appliance_specs["206"].host == "10.0.0.206"


def test_appliance_subtable_no_host_no_base_url_raises(tmp_path):
    # An appliance subtable without host AND an instance whose base_url has no
    # hostname (IP-less) → can't derive one → raise. base_url here is a bare
    # scheme-less string with no host component.
    _write(
        tmp_path / "instances.toml",
        """
        [instances.x]
        base_url = "https://"
        [instances.x.auth]
        type = "api_key"
        key = "k"

        [instances.x.appliance]
        user = "csadmin"
        """,
    )
    with pytest.raises(ValueError, match="no `host`"):
        InstanceRegistry.from_config_file(tmp_path / "instances.toml")


def test_instance_without_appliance_subtable(tmp_path):
    _write(
        tmp_path / "instances.toml",
        """
        [instances.x]
        base_url = "https://x"
        [instances.x.auth]
        type = "api_key"
        key = "k"
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    assert reg.appliance_names() == []
    assert reg.appliance_specs == {}


def test_appliance_subtable_invalid_port_raises(tmp_path):
    _write(
        tmp_path / "instances.toml",
        """
        [instances.x]
        base_url = "https://x"
        [instances.x.auth]
        type = "api_key"
        key = "k"

        [instances.x.appliance]
        port = "not-an-int"
        """,
    )
    with pytest.raises(ValueError, match="port must be an int"):
        InstanceRegistry.from_config_file(tmp_path / "instances.toml")


def test_transport_builds_ssh_transport(tmp_path):
    _write(
        tmp_path / "instances.toml",
        """
        default = "206"

        [instances.206]
        base_url = "https://10.0.0.206"
        [instances.206.auth]
        type = "api_key"
        key = "k"

        [instances.206.appliance]
        password = "secret"
        port = 13000
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    t = reg.transport("206")
    # transport() builds an SSHTransport when a host is present.
    from pyfsr.cli.appliance.transport import SSHTransport

    assert isinstance(t, SSHTransport)
    assert t.host == "10.0.0.206"  # derived from base_url
    assert t.port == 13000
    assert t.password == "secret"
    assert t.target == "csadmin@10.0.0.206"


def test_transport_resolves_default(tmp_path):
    _write(
        tmp_path / "instances.toml",
        """
        default = "206"

        [instances.206]
        base_url = "https://10.0.0.206"
        [instances.206.auth]
        type = "api_key"
        key = "k"

        [instances.206.appliance]
        password = "secret"
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    t = reg.transport()  # alias=None → default "206"
    from pyfsr.cli.appliance.transport import SSHTransport

    assert isinstance(t, SSHTransport)
    assert t.host == "10.0.0.206"


def test_transport_unknown_alias_raises(tmp_path):
    _write(
        tmp_path / "instances.toml",
        """
        [instances.206]
        base_url = "https://10.0.0.206"
        [instances.206.auth]
        type = "api_key"
        key = "k"

        [instances.206.appliance]
        password = "secret"
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    with pytest.raises(ValueError, match="unknown instance 'zzz'"):
        reg.transport("zzz")


def test_transport_alias_without_appliance_subtable_raises(tmp_path):
    _write(
        tmp_path / "instances.toml",
        """
        [instances.x]
        base_url = "https://x"
        [instances.x.auth]
        type = "api_key"
        key = "k"
        """,
    )
    reg = InstanceRegistry.from_config_file(tmp_path / "instances.toml")
    with pytest.raises(ValueError, match="no \\[instances.x.appliance\\] subtable"):
        reg.transport("x")


def test_single_env_registry_has_no_appliance_profiles():
    # from_single_env builds a REST-only registry — no appliance subtables.
    reg = InstanceRegistry.from_single_env(env={"FSR_BASE_URL": "https://env.example.com", "FSR_API_KEY": "k"})
    assert reg.appliance_names() == []
    with pytest.raises(ValueError, match="no \\[instances.default.appliance\\] subtable"):
        reg.transport()
