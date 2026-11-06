"""Version bumps for bundled installers: the widget rewrite (ported from the widget
harness's rewriteForVersion tests), the free-version picker, and the import-time
bump of a pack whose widget/connector version is already on the box."""

import io
import json
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyfsr.api.import_config import ImportConfigAPI
from pyfsr.widget_dev import (
    next_free_version,
    pack_widget,
    rebundle_widget_with_version,
    rewrite_widget_text,
    widget_controller_problems,
    widget_info_from_tgz,
)

# -- rewrite_widget_text: the harness's rewriteForVersion cases ------------------


def test_rewrites_view_and_edit_controllers_keeping_dev_suffix():
    src = 'controller("myWidget100DevCtrl"); controller("editmyWidget100DevCtrl"); controller("editMyWidget100Ctrl")'
    out = rewrite_widget_text(src, "myWidget", "2.0.0")
    assert "myWidget200DevCtrl" in out
    assert "editmyWidget200DevCtrl" in out
    assert "editMyWidget200Ctrl" in out  # no Dev stays no Dev (published form)
    assert "100" not in out


def test_rewrites_paths_and_bare_ids_but_keeps_extensions():
    src = "a='myWidget-1.0.0/widgetAssets/x.js'; b='myWidget-1.0.0'; c='myWidget-1.0.0.tgz'"
    out = rewrite_widget_text(src, "myWidget", "1.0.1")
    assert out == "a='myWidget-1.0.1/widgetAssets/x.js'; b='myWidget-1.0.1'; c='myWidget-1.0.1.tgz'"


def test_leaves_non_version_words_and_other_widgets_alone():
    src = "see myWidget-utils.md; myWidgetExtra100Ctrl; otherWidget-1.0.0"
    assert rewrite_widget_text(src, "myWidget", "1.0.1") == src


def test_bare_controller_gets_the_version():
    assert rewrite_widget_text('ng-controller="myWidgetCtrl"', "myWidget", "1.0.1") == 'ng-controller="myWidget101Ctrl"'


def test_rewrite_is_idempotent():
    src = 'controller("myWidget100Ctrl"); "myWidget-1.0.0/x"'
    once = rewrite_widget_text(src, "myWidget", "2.0.0")
    assert rewrite_widget_text(once, "myWidget", "2.0.0") == once


# -- next_free_version ----------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "taken", "must_exceed", "expected"),
    [
        ("1.0.0", ["1.0.0"], None, "1.0.1"),
        ("1.0.0", ["1.0.0", "1.0.1"], None, "1.0.2"),
        ("1.0.0", [], ["1.1.0"], "1.1.1"),  # start from the newest, not 1.0.11
        ("1.0.9", ["1.0.9", "1.0.10"], None, "1.0.11"),
        ("1.0.0", ["1.0.0", "10.1.0"], None, "10.1.1"),
        ("1.0", ["1.0"], None, "1.0.1"),  # missing segment padded
    ],
)
def test_next_free_version(current, taken, must_exceed, expected):
    assert next_free_version(current, taken, must_exceed=must_exceed) == expected


def test_next_free_version_skips_a_dotless_collision():
    # 1.0.11 and 10.1.1 both register ...1011Ctrl
    assert next_free_version("1.0.10", ["1.0.10", "10.1.1"]) not in {"1.0.11", "10.1.1"}


# -- rebundle + controller check ------------------------------------------------


def _widget_dir(root: Path, name="mapDemo", version="1.0.0", view_ctrl=None) -> Path:
    d = root / f"{name}-{version}"
    (d / "widgetAssets" / "js").mkdir(parents=True)
    num = version.replace(".", "")
    cap = name[0].upper() + name[1:]
    (d / "info.json").write_text(json.dumps({"name": name, "version": version, "title": "Map Demo"}))
    (d / "view.controller.js").write_text(
        f"angular.module('cybersponse').controller('{view_ctrl or name + num + 'Ctrl'}', c);"
    )
    (d / "edit.controller.js").write_text(f"angular.module('cybersponse').controller('edit{cap}{num}Ctrl', c);")
    (d / "view.html").write_text(f'<link href="{name}-{version}/widgetAssets/css/a.css">')
    (d / "edit.html").write_text("<div></div>")
    (d / "widgetAssets" / "js" / "svc.js").write_text(f"var id='{name}-{version}';")
    return d


def test_rebundle_rewrites_everything_and_renames_root(tmp_path: Path):
    tgz = pack_widget(str(_widget_dir(tmp_path)), str(tmp_path / "in"))
    out = rebundle_widget_with_version(tgz, "1.0.1", tmp_path / "out")
    assert Path(out).name == "mapDemo-1.0.1.tgz"
    assert widget_info_from_tgz(out)["version"] == "1.0.1"
    with tarfile.open(out) as tf:
        files = {m.name: tf.extractfile(m).read().decode() for m in tf.getmembers() if m.isfile()}
    assert all(n.startswith("mapDemo-1.0.1/") for n in files)
    assert "mapDemo101Ctrl" in files["mapDemo-1.0.1/view.controller.js"]
    assert "editMapDemo101Ctrl" in files["mapDemo-1.0.1/edit.controller.js"]
    assert "mapDemo-1.0.1/widgetAssets" in files["mapDemo-1.0.1/view.html"]
    assert "mapDemo-1.0.1" in files["mapDemo-1.0.1/widgetAssets/js/svc.js"]


def test_controller_check_flags_a_desync(tmp_path: Path):
    d = _widget_dir(tmp_path, view_ctrl="mapDemo099Ctrl")
    problems = widget_controller_problems(d)
    assert any("digits 099 != 100" in p for p in problems)


# -- import-time bump -----------------------------------------------------------


class _Client:
    def __init__(self, widgets=(), connectors=()):
        self._widgets = list(widgets)
        self.widgets = SimpleNamespace(
            list=lambda name=None: [SimpleNamespace(version=v) for n, v in self._widgets if n == name]
        )
        self.connectors = SimpleNamespace(
            list_configured=lambda refresh=False: [SimpleNamespace(name=n, version=v) for n, v in connectors]
        )


def _connector_tgz(name: str, version: str) -> bytes:
    data = json.dumps({"name": name, "version": version}).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(f"{name}/info.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _pack(tmp_path: Path) -> Path:
    wtgz = Path(pack_widget(str(_widget_dir(tmp_path / "src")), str(tmp_path / "w")))
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(
            "export_x/widgets/data.json",
            json.dumps([{"name": "mapDemo", "version": "1.0.0", "install_mode": "tgz", "installer_path": wtgz.name}]),
        )
        zf.writestr(f"export_x/widgets/{wtgz.name}", wtgz.read_bytes())
        zf.writestr(
            "export_x/connectors/data.json",
            json.dumps([{"name": "acme", "version": "1.0.0", "install_mode": "tgz", "installer_path": "acme.tgz"}]),
        )
        zf.writestr("export_x/connectors/acme.tgz", _connector_tgz("acme", "1.0.0"))
        zf.writestr("export_x/dashboards/Demo.json", json.dumps({"type": "mapDemo-1.0.0", "other": "otherW-1.0.0"}))
    return zp


def test_nothing_on_the_box_means_no_bump(tmp_path: Path):
    zp = _pack(tmp_path)
    path, bumps = ImportConfigAPI(_Client()).bump_bundled_installers(str(zp))
    assert (path, bumps) == (str(zp), [])


def test_same_versions_on_the_box_are_bumped_and_references_follow(tmp_path: Path):
    zp = _pack(tmp_path)
    api = ImportConfigAPI(_Client(widgets=[("mapDemo", "1.0.0")], connectors=[("acme", "1.2.0")]))
    path, bumps = api.bump_bundled_installers(str(zp))
    assert {(b["kind"], b["from"], b["to"]) for b in bumps} == {
        ("widget", "1.0.0", "1.0.1"),
        ("connector", "1.0.0", "1.2.1"),
    }
    with zipfile.ZipFile(path) as zf:
        rows = json.loads(zf.read("export_x/widgets/data.json"))
        assert rows[0]["version"] == "1.0.1" and rows[0]["installer_path"] == "mapDemo-1.0.1.tgz"
        assert "export_x/widgets/mapDemo-1.0.1.tgz" in zf.namelist()
        assert "export_x/widgets/mapDemo-1.0.0.tgz" not in zf.namelist()
        dash = json.loads(zf.read("export_x/dashboards/Demo.json"))
        assert dash == {"type": "mapDemo-1.0.1", "other": "otherW-1.0.0"}
        assert json.loads(zf.read("export_x/connectors/data.json"))[0]["version"] == "1.2.1"
    assert zipfile.ZipFile(zp).read("export_x/dashboards/Demo.json")  # source zip untouched
    assert json.loads(zipfile.ZipFile(zp).read("export_x/dashboards/Demo.json"))["type"] == "mapDemo-1.0.0"


def test_older_connector_on_the_box_installs_without_a_bump(tmp_path: Path):
    zp = _pack(tmp_path)
    _, bumps = ImportConfigAPI(_Client(connectors=[("acme", "0.9.0")])).bump_bundled_installers(str(zp))
    assert bumps == []


# -- retiring the superseded widget version -------------------------------------


class _RetireClient:
    def __init__(self, records, dashboards):
        self.removed: list[str] = []
        self._records = records
        self._dashboards = dashboards
        self.widgets = SimpleNamespace(
            list=lambda name=None: [SimpleNamespace(**r) for r in self._records],
            remove=self.removed.append,
        )

    def get(self, endpoint, params=None, **_):
        return {"hydra:member": self._dashboards}


_RECORDS = [
    {"uuid": "old-uuid", "version": "1.0.0", "installed": True},
    {"uuid": "new-uuid", "version": "1.0.1", "installed": True},
]


def _bump():
    return [{"kind": "widget", "name": "mapDemo", "from": "1.0.0", "to": "1.0.1"}]


def test_retires_the_old_version_when_nothing_uses_it():
    client = _RetireClient(_RECORDS, [{"displayName": "Demo", "config": {"type": "mapDemo-1.0.1"}}])
    bumps = _bump()
    ImportConfigAPI(client)._retire_superseded_widgets(bumps)
    assert client.removed == ["old-uuid"] and bumps[0]["retired"] == ["1.0.0"]


def test_keeps_the_old_version_a_dashboard_still_uses():
    client = _RetireClient(_RECORDS, [{"displayName": "Legacy", "config": {"type": "mapDemo-1.0.0"}}])
    bumps = _bump()
    ImportConfigAPI(client)._retire_superseded_widgets(bumps)
    assert client.removed == [] and "Legacy" in bumps[0]["kept"]["1.0.0"]


def test_keeps_the_old_version_when_the_new_one_did_not_install():
    client = _RetireClient(_RECORDS[:1], [])
    bumps = _bump()
    ImportConfigAPI(client)._retire_superseded_widgets(bumps)
    assert client.removed == [] and "not installed" in bumps[0]["kept"]["1.0.0"]


def test_retires_every_unused_older_version_not_just_the_one_bumped_from():
    records = [*_RECORDS, {"uuid": "newest-uuid", "version": "1.0.2", "installed": True}]
    client = _RetireClient(records, [{"displayName": "Demo", "config": {"type": "mapDemo-1.0.2"}}])
    bumps = [{"kind": "widget", "name": "mapDemo", "from": "1.0.0", "to": "1.0.2"}]
    ImportConfigAPI(client)._retire_superseded_widgets(bumps)
    assert sorted(client.removed) == ["new-uuid", "old-uuid"] and sorted(bumps[0]["retired"]) == ["1.0.0", "1.0.1"]
