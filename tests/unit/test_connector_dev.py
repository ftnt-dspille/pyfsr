"""connector_dev: scaffold -> package -> deploy, all offline."""

from __future__ import annotations

import json
import tarfile

import pytest

from pyfsr.connector_dev import (
    REQUIRED_FILES,
    deploy_connector,
    package_connector,
    scaffold_connector,
)


def test_scaffold_writes_the_required_files(tmp_path):
    root = tmp_path / "acme-threat-feed"
    written = scaffold_connector("acme-threat-feed", root)
    names = {p.name for p in written}
    assert set(REQUIRED_FILES) <= names
    info = json.loads((root / "info.json").read_text())
    assert info["name"] == "acme-threat-feed"
    assert info["version"] == "1.0.0"
    # the config field must be visible or the UI shows an empty config form
    field = info["configuration"]["fields"][0]
    assert field["visible"] is True and field["editable"] is True
    assert info["operations"][0]["operation"] == "ping"


def test_scaffold_rejects_bad_names_and_overwrites(tmp_path):
    with pytest.raises(ValueError):
        scaffold_connector("Bad Name!", tmp_path / "x")
    root = tmp_path / "dup"
    scaffold_connector("dup", root)
    with pytest.raises(FileExistsError):
        scaffold_connector("dup", root)


def test_package_builds_single_dir_tgz(tmp_path):
    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("# excluded\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_text("x")

    tgz = package_connector(root)
    assert tgz.name == "acme-feed-1.0.0.tgz"
    with tarfile.open(tgz) as tar:
        names = tar.getnames()
    assert all(n.startswith("acme-feed/") for n in names)
    assert "acme-feed/info.json" in names
    assert not any("tests/" in n or ".pyc" in n for n in names)


def test_package_requires_core_files(tmp_path):
    root = tmp_path / "broken"
    scaffold_connector("broken", root)
    (root / "operations.py").unlink()
    with pytest.raises(FileNotFoundError, match="operations.py"):
        package_connector(root)


class _Entry:
    def __init__(self, name, active=True):
        self.name = name
        self.active = active


class _FakeConnectors:
    def __init__(self):
        self.calls = []

    def install_from_file(self, path, *, replace=False, wait=False):
        self.calls.append(("install", path, replace, wait))
        return {"status": "finished"}

    def list_configured(self, refresh=False):
        return [_Entry("acme-feed")]

    def upsert_configuration(self, connector, config, *, name=None, **kwargs):
        self.calls.append(("configure", connector, config, {"name": name, **kwargs}))
        return {"config_id": "cfg-1"}

    def healthcheck(self, name, config=None):
        return {"status": "Available"}


class _FakeClient:
    def __init__(self):
        self.connectors = _FakeConnectors()


# --- ghost-connector guard -------------------------------------------------

from pyfsr.api.connectors import ConnectorPackageError  # noqa: E402
from pyfsr.connector_dev import _bump_version  # noqa: E402


class _VersionedConnectors(_FakeConnectors):
    """A fake that reports an already-installed version, like the real box."""

    def __init__(self, installed_version):
        super().__init__()
        self._installed = installed_version

    def resolve_version(self, name):
        return self._installed


def _installed_info_version(tgz_path):
    with tarfile.open(tgz_path) as tar:
        name = tar.getnames()[0].split("/", 1)[0]
        return json.loads(tar.extractfile(f"{name}/info.json").read())["version"]


@pytest.mark.parametrize(
    "v,expected",
    [
        ("1.0.0", "1.0.1"),
        ("1.2.9", "1.2.10"),
        ("2.5.0", "2.5.1"),
        ("1.0", "1.1"),
        ("3", "4"),
        ("1.0.0-dev", "1.0.1"),
    ],
)
def test_bump_version(v, expected):
    assert _bump_version(v) == expected


def test_deploy_bumps_patch_when_same_version_already_installed(tmp_path):
    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)  # version 1.0.0
    client = _FakeClient()
    client.connectors = _VersionedConnectors("1.0.0")
    result = deploy_connector(client, root)
    assert result["bumped"] == "1.0.1"
    assert result["version"] == "1.0.1"
    # the bundle actually installed carries the bumped version (no ghost)
    installed_tgz = client.connectors.calls[0][1]
    assert installed_tgz.endswith("acme-feed-1.0.1.tgz")
    assert _installed_info_version(installed_tgz) == "1.0.1"
    # the SOURCE info.json is left untouched
    assert json.loads((root / "info.json").read_text())["version"] == "1.0.0"


def test_deploy_errors_on_same_version_when_requested(tmp_path):
    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)
    client = _FakeClient()
    client.connectors = _VersionedConnectors("1.0.0")
    with pytest.raises(ConnectorPackageError, match="already installed"):
        deploy_connector(client, root, on_existing_version="error")


def test_deploy_first_install_does_not_bump(tmp_path):
    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)
    client = _FakeClient()
    client.connectors = _VersionedConnectors(None)  # not installed yet
    result = deploy_connector(client, root)
    assert "bumped" not in result
    assert result["version"] == "1.0.0"


def test_scaffold_ops_builds_real_api_foundation(tmp_path):
    import ast

    root = tmp_path / "acme-edr"
    scaffold_connector(
        "acme-edr",
        root,
        label="Acme EDR",
        ops=[
            {"operation": "get_device", "method": "GET", "parameters": [{"name": "device_id", "required": True}]},
            {"operation": "isolate_device", "method": "POST", "parameters": [{"name": "device_id", "required": True}]},
        ],
    )
    info = json.loads((root / "info.json").read_text())
    assert [o["operation"] for o in info["operations"]] == ["get_device", "isolate_device"]
    assert {f["name"] for f in info["configuration"]["fields"]} == {"base_url", "api_key", "verify"}
    ops_src = (root / "operations.py").read_text()
    ast.parse(ops_src)  # valid python
    # antipattern-clean by construction: TLS verify from config, quoted path, no bypass
    assert 'verify=config.get("verify", True)' in ops_src
    assert "quote(" in ops_src
    assert "verify=False" not in ops_src
    # a handler + a dispatch entry per op
    assert "def get_device(config, params):" in ops_src
    assert "'isolate_device': isolate_device" in ops_src


def test_scaffold_default_is_still_minimal_ping(tmp_path):
    root = tmp_path / "plain"
    scaffold_connector("plain", root)
    info = json.loads((root / "info.json").read_text())
    assert [o["operation"] for o in info["operations"]] == ["ping"]


def test_package_version_override_injects_into_bundle(tmp_path):
    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)
    tgz = package_connector(root, version="9.9.9")
    assert tgz.name == "acme-feed-9.9.9.tgz"
    assert _installed_info_version(tgz) == "9.9.9"
    assert json.loads((root / "info.json").read_text())["version"] == "1.0.0"


def test_deploy_packages_installs_and_configures(tmp_path):
    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)
    client = _FakeClient()
    result = deploy_connector(client, root, config={"server": "https://svc.example.com"})
    assert result["installed"] == "acme-feed"
    assert result["configured"] == "Default"
    assert result["healthcheck"] == {"status": "Available"}
    kinds = [c[0] for c in client.connectors.calls]
    assert kinds == ["install", "configure"]
    install = client.connectors.calls[0]
    assert install[2] is True and install[3] is True  # replace + wait


def test_deploy_times_out_when_never_active(tmp_path):
    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)
    client = _FakeClient()
    client.connectors.list_configured = lambda refresh=False: []
    with pytest.raises(TimeoutError):
        deploy_connector(client, root, timeout=0.1)


_SECRETS = [
    "sub/.env",
    ".env.local",
    ".venv/lib/x.py",
    "lib/venv/y.py",
    "keys/server.pem",
    "deploy/id_rsa",
    "conf/.netrc",
    ".pyfsr/instances.toml",
    "creds/credentials",
    "app.key",
    "nested/__pycache__/m.pyc",
]


def _bundle_names(root):
    with tarfile.open(package_connector(root)) as tar:
        return tar.getnames()


def _plant(root, extra=()):
    for rel in (*_SECRETS, *extra):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("secret\n")


def test_package_never_ships_secrets_or_environments_untracked(tmp_path):
    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)
    _plant(root, extra=("notes.txt", "data/dump.csv"))
    names = {n.removeprefix("acme-feed/") for n in _bundle_names(root)}
    assert not names & {*_SECRETS, "notes.txt", "data/dump.csv"}, names
    assert "info.json" in names and "operations.py" in names


def test_package_never_ships_secrets_even_when_git_tracked(tmp_path):
    import subprocess

    root = tmp_path / "acme-feed"
    scaffold_connector("acme-feed", root)
    _plant(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-f", "."], cwd=root, check=True)
    names = {n.removeprefix("acme-feed/") for n in _bundle_names(root)}
    assert not names & set(_SECRETS), names
    assert "connector.py" in names
