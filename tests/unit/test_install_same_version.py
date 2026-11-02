"""install_from_file's ghost-connector guard (live proof: test_connector_ghost_integration.py)."""

import io
import json
import tarfile

import pytest

from pyfsr.api import connectors as mod
from pyfsr.api.connectors import ConnectorPackageError, ConnectorsAPI, _tgz_identity


def _bundle(tmp_path, version="1.0.0"):
    tgz = tmp_path / "demo.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        for name, data in (
            ("demo/info.json", json.dumps({"name": "demo", "version": version}).encode()),
            ("demo/operations.py", b"x = 1\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return tgz


@pytest.fixture
def api(monkeypatch):
    sent = []

    def upload(client, path, **kw):
        sent.append((path, kw["replace"]))
        return {"name": "demo"}

    monkeypatch.setattr(mod, "upload_solutionpack", upload)
    a = ConnectorsAPI.__new__(ConnectorsAPI)
    a.client = None
    a.clear_cache = lambda: None
    a.installed = None
    a.resolve_version = lambda name: a.installed
    return a, sent


def test_same_version_is_bumped_in_a_copy(api, tmp_path):
    a, sent = api
    a.installed = "1.0.0"
    src = _bundle(tmp_path)
    out = a.install_from_file(str(src), replace=True)
    assert out["bumped"] == "1.0.1"
    [(path, replace)] = sent
    assert _tgz_identity(path) == ("demo", "1.0.1") and replace is False
    assert _tgz_identity(src) == ("demo", "1.0.0")  # the source bundle is untouched
    with tarfile.open(path) as tar:
        assert tar.extractfile("demo/operations.py").read() == b"x = 1\n"


def test_same_version_can_be_refused(api, tmp_path):
    a, sent = api
    a.installed = "1.0.0"
    with pytest.raises(ConnectorPackageError, match="already installed"):
        a.install_from_file(str(_bundle(tmp_path)), on_existing_version="error")
    assert sent == []


def test_allow_keeps_the_old_behaviour(api, tmp_path):
    a, sent = api
    a.installed = "1.0.0"
    out = a.install_from_file(str(_bundle(tmp_path)), replace=True, on_existing_version="allow")
    assert "bumped" not in out and sent[0][1] is True


@pytest.mark.parametrize("installed", [None, "0.9.0"])
def test_a_new_version_goes_up_unchanged(api, tmp_path, installed):
    a, sent = api
    a.installed = installed
    src = _bundle(tmp_path)
    assert "bumped" not in a.install_from_file(str(src))
    assert sent[0][0] == str(src)


def test_unknown_mode_is_refused(api, tmp_path):
    with pytest.raises(ValueError, match="on_existing_version"):
        api[0].install_from_file(str(_bundle(tmp_path)), on_existing_version="bump")
