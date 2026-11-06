"""Content Hub install refuses a pack whose bundled connectors/widgets it would skip."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyfsr.api import solution_packs as sp_mod
from pyfsr.api.solution_packs import SolutionPackAPI
from pyfsr.exceptions import BundledInstallersSkipped


def _pack_zip(path: Path, *, widgets: bool) -> Path:
    root = "export_x"
    info = {"type": "solutionpack", "name": "demo", "version": "1.0.0", "contents": {}}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{root}/info.json", json.dumps(info))
        if widgets:
            rows = [
                {"name": "mapDemo", "version": "1.0.0", "install_mode": "tgz", "installer_path": "mapDemo-1.0.0.tgz"}
            ]
            zf.writestr(f"{root}/widgets/data.json", json.dumps(rows))
            zf.writestr(f"{root}/widgets/mapDemo-1.0.0.tgz", b"x")
    return path


@pytest.fixture
def api(monkeypatch):
    calls = []
    monkeypatch.setattr(sp_mod, "upload_solutionpack", lambda client, path, **kw: calls.append(path) or {})
    a = SolutionPackAPI(SimpleNamespace())
    a.calls = calls
    return a


def test_refuses_bundled_installers_and_names_the_import_wizard(api, tmp_path):
    z = _pack_zip(tmp_path / "p.zip", widgets=True)
    with pytest.raises(BundledInstallersSkipped) as exc:
        api.install_from_file(str(z))
    assert "widget mapDemo 1.0.0" in str(exc.value)
    assert "import_config.import_file" in str(exc.value)
    assert exc.value.installers == ["widget mapDemo 1.0.0"]
    assert api.calls == []


def test_force_installs_anyway(api, tmp_path):
    z = _pack_zip(tmp_path / "p.zip", widgets=True)
    api.install_from_file(str(z), force=True)
    assert api.calls == [str(z)]


def test_plain_pack_installs(api, tmp_path):
    z = _pack_zip(tmp_path / "p.zip", widgets=False)
    api.install_from_file(str(z))
    assert api.calls == [str(z)]
