"""install_from_file's ghost-connector guard (live proof: test_connector_ghost_integration.py)."""

import io
import json
import tarfile

import pytest

from pyfsr.api import connectors as mod
from pyfsr.api.connectors import ConnectorPackageError, ConnectorsAPI, _tgz_identity


def _info(version="1.0.0", description="Does a thing."):
    return {
        "name": "demo",
        "label": "Demo",
        "version": version,
        "configuration": {"fields": []},
        "operations": [{"operation": "ping", "title": "Ping", "description": description}],
    }


def _bundle(tmp_path, version="1.0.0", info=None, name="demo.tgz"):
    tgz = tmp_path / name
    info = _info(version) if info is None else info
    with tarfile.open(tgz, "w:gz") as tar:
        for member, data in (
            ("demo/info.json", json.dumps(info).encode()),
            ("demo/operations.py", b"x = 1\n"),
        ):
            ti = tarfile.TarInfo(member)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
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


def test_replace_refuses_a_bundle_the_importer_rejects_before_upload(api, tmp_path):
    # A replace removes the active connector on the server before it reads the bundle,
    # so a rejectable bundle must be refused locally with nothing sent.
    a, sent = api
    a.installed = "1.0.0"
    bad = _bundle(tmp_path, version="1.0.1", info=_info("1.0.1", description="x" * 1100), name="bad.tgz")
    with pytest.raises(ConnectorPackageError, match="1000"):
        a.install_from_file(str(bad), replace=True, on_existing_version="allow")
    assert sent == []


def test_replace_refuses_an_unreadable_bundle_before_upload(api, tmp_path):
    a, sent = api
    junk = tmp_path / "junk.tgz"
    junk.write_bytes(b"not a tarball")
    with pytest.raises(ConnectorPackageError, match="not a readable connector bundle"):
        a.install_from_file(str(junk), replace=True)
    assert sent == []


def test_replace_refuses_a_bundle_whose_folder_does_not_match_its_name(api, tmp_path):
    a, sent = api
    tgz = tmp_path / "wrong.tgz"
    data = json.dumps(_info()).encode()
    with tarfile.open(tgz, "w:gz") as tar:
        ti = tarfile.TarInfo("other/info.json")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
    with pytest.raises(ConnectorPackageError, match="must match exactly"):
        a.install_from_file(str(tgz), replace=True)
    assert sent == []


def test_a_bad_bundle_without_replace_is_still_uploaded(api, tmp_path):
    # Without a replace nothing is removed on the box, so the server stays the judge.
    a, sent = api
    bad = _bundle(tmp_path, info=_info(description="x" * 1100), name="bad.tgz")
    a.install_from_file(str(bad))
    assert sent[0][0] == str(bad)


def test_a_valid_replace_is_uploaded_unchanged(api, tmp_path):
    a, sent = api
    src = _bundle(tmp_path)
    assert "bumped" not in a.install_from_file(str(src), replace=True)
    assert sent == [(str(src), True)]
