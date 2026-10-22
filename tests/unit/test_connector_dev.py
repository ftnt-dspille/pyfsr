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
