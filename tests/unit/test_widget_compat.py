"""Fail-fast on widget compatibility: the appliance's rule, upload refusal, import pre/post checks."""

import io
import json
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyfsr.api.import_config import ImportConfigAPI
from pyfsr.api.widgets import WidgetsAPI
from pyfsr.exceptions import WidgetIncompatible, WidgetNotInstalled
from pyfsr.widget_dev import widget_compatibility_error


@pytest.mark.parametrize(
    ("compat", "platform", "refused"),
    [
        (["8.0.0"], "8.0.0", False),
        (["7.6.0", "8.0.0"], "8.0.0", False),
        (["8.0.0", "8.0.1"], "8.0.0", True),  # the highest entry decides, not "any of"
        (["8.0.1"], "8.0.1-1234", False),  # build suffix ignored
        (["7.0.0"], "8.0.0", True),  # below 7.0.2
        ([], "8.0.0", False),  # nothing to judge
        ("8.0.0", "8.0.0", False),  # a bare string
        (["9.0.0"], "dev", False),  # unknown platform: not judged
    ],
)
def test_rule_matches_the_appliance(compat, platform, refused):
    assert (widget_compatibility_error(compat, platform) is not None) is refused


def _tgz_bytes(name: str, compat: list[str]) -> bytes:
    data = json.dumps({"name": name, "version": "1.0.0", "metadata": {"compatibility": compat}}).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(f"{name}-1.0.0/info.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _Client:
    def __init__(self, version=(8, 0, 0), installed=()):
        self._version = version
        self.uploads = []
        self.widgets = WidgetsAPI(self)
        self._installed = installed

    def version_tuple(self, **_):
        return self._version

    def get(self, endpoint, params=None, **_):
        return {"hydra:member": [{"name": n, "version": v, "installed": True} for n, v in self._installed]}


def test_upload_refuses_before_sending(tmp_path: Path):
    tgz = tmp_path / "mapDemo-1.0.0.tgz"
    tgz.write_bytes(_tgz_bytes("mapDemo", ["8.0.0", "8.0.1"]))
    with pytest.raises(WidgetIncompatible, match="8.0.1, newer than the box's 8.0.0"):
        _Client().widgets.check_compatible(tgz)


def _pack(tmp_path: Path, compat: list[str]) -> Path:
    zp = tmp_path / "pack.zip"
    row = [{"name": "mapDemo", "version": "1.0.0", "install_mode": "tgz", "installer_path": "mapDemo-1.0.0.tgz"}]
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("export_x/widgets/data.json", json.dumps(row))
        zf.writestr("export_x/widgets/mapDemo-1.0.0.tgz", _tgz_bytes("mapDemo", compat))
    return zp


def test_import_preflight_refuses_incompatible_bundled_widget(tmp_path: Path):
    api = ImportConfigAPI(_Client())
    with pytest.raises(WidgetIncompatible):
        api.check_bundled_widgets(str(_pack(tmp_path, ["8.0.0", "8.0.1"])))
    api.check_bundled_widgets(str(_pack(tmp_path, ["8.0.0"])))  # compatible: no raise


def _job(values):
    return SimpleNamespace(options={"widgets": {"include": True, "values": values}})


def test_import_postcheck_names_a_silently_skipped_widget():
    api = ImportConfigAPI(_Client(installed=()))
    wanted = [{"name": "mapDemo", "version": "1.0.0", "install_mode": "tgz", "include": True, "exists": False}]
    with pytest.raises(WidgetNotInstalled, match="mapDemo 1.0.0"):
        api._verify_bundled_widgets(_job(wanted))


def test_import_postcheck_passes_when_installed_or_not_requested():
    wanted = [{"name": "mapDemo", "version": "1.0.0", "install_mode": "tgz", "include": True, "exists": False}]
    ImportConfigAPI(_Client(installed=[("mapDemo", "1.0.0")]))._verify_bundled_widgets(_job(wanted))
    skipped = [{**wanted[0], "exists": True, "include": False}]
    ImportConfigAPI(_Client())._verify_bundled_widgets(_job(skipped))


# -- API-key upload path ----------------------------------------------------------


def test_api_key_upload_uses_widgets_import_and_deploy_skips_publish(tmp_path: Path):
    from pyfsr.auth.api_key import APIKeyAuth

    tgz = tmp_path / "mapDemo-1.0.0.tgz"
    tgz.write_bytes(_tgz_bytes("mapDemo", ["7.6.0"]))
    calls = []

    class _KeyClient:
        base_url = "https://fsoc.example.com"
        auth = APIKeyAuth.__new__(APIKeyAuth)

        def __init__(self):
            self.widgets = WidgetsAPI(self)

        def version_tuple(self, **_):
            return (7, 6, 6)

        def request(self, method, endpoint, data=None, files=None, **_):
            calls.append((method, endpoint, data, sorted(files or {})))

        def get(self, endpoint, params=None, **_):
            return {
                "hydra:member": [
                    {"uuid": "u1", "name": "mapDemo", "version": "1.0.0", "installed": True, "draft": False}
                ]
            }

        def put(self, *a, **k):
            raise AssertionError("publish must not run for an API-key upload")

    rec = _KeyClient().widgets.deploy(str(tgz))
    assert calls == [("POST", "/api/3/widgets/import", {"replace": "true"}, ["file"])]
    assert rec.published and rec.version == "1.0.0"
