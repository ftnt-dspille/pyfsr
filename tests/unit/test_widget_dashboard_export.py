"""Dashboard export follows nested widgets and reports connectors."""

import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

from pyfsr.api.widgets import WidgetsAPI
from pyfsr.widget_dev import dashboard_widget_refs, widget_code_refs


def _tgz(name: str, version: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    entries = {"info.json": json.dumps({"name": name, "version": version}), **files}
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, text in entries.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{name}-{version}/{rel}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


LANDSCAPE_JS = """
  threatLandscapeViewService.executeAction('fortinet-fortiguard-threat-intelligence', 'get_industry_list', {});
  widgetService.getInstalledWidgets().then(widgets => {
    const widget = widgets.find(w => w.name === 'killchainphases');
  });
  var asset = 'widgets/installed/fortiGuardThreatLandscape-1.0.0/x.png';
"""

ARCHIVES = {
    "u-land": _tgz("fortiGuardThreatLandscape", "1.0.0", {"view.controller.js": LANDSCAPE_JS}),
    "u-kill": _tgz("killchainphases", "2.0.0", {"view.controller.js": "var a = 1;"}),
    "u-map": _tgz("mapDemo", "1.0.2", {"view.controller.js": "svc.executeAction('not-installed', 'x', {});"}),
}

DASHBOARD = {
    "config": {
        "tabs": [
            {"rows": [{"columns": [{"widgets": [{"type": "fortiGuardThreatLandscape-1.0.0"}]}]}]},
            {"rows": [{"columns": [{"widgets": [{"type": "mapDemo-1.0.0"}, {"type": "gone-3.0.0"}]}]}]},
        ]
    }
}


class _Client:
    def __init__(self):
        self.widgets = WidgetsAPI(self)
        self.connectors = SimpleNamespace(
            list_configured=lambda: [SimpleNamespace(name="fortinet-fortiguard-threat-intelligence")]
        )
        self.exported = []

    def get(self, endpoint, params=None, **_):
        if endpoint.startswith("/api/3/dashboard/"):
            return DASHBOARD
        return {
            "hydra:member": [
                {"uuid": "u-land", "name": "fortiGuardThreatLandscape", "version": "1.0.0", "installed": True},
                {"uuid": "u-kill", "name": "killchainphases", "version": "2.0.0", "installed": True},
                {"uuid": "u-map", "name": "mapDemo", "version": "1.0.2", "installed": True},
            ]
        }

    def request(self, method, endpoint, **_):
        uuid = endpoint.rsplit("/", 1)[-1]
        self.exported.append(uuid)
        return SimpleNamespace(content=ARCHIVES[uuid])


def test_dashboard_refs_walk_tabs():
    assert dashboard_widget_refs(DASHBOARD) == {
        ("fortiGuardThreatLandscape", "1.0.0"),
        ("mapDemo", "1.0.0"),
        ("gone", "3.0.0"),
    }


def test_code_refs_skip_own_name(tmp_path: Path):
    p = tmp_path / "l.tgz"
    p.write_bytes(ARCHIVES["u-land"])
    refs = widget_code_refs(p)
    assert refs["widgets"] == {"killchainphases"}
    assert refs["widget_types"] == set()
    assert refs["connectors"] == {"fortinet-fortiguard-threat-intelligence"}


def test_export_dashboard_follows_nested(tmp_path: Path):
    client = _Client()
    res = client.widgets.export_dashboard("dash-uuid", tmp_path / "out")
    got = {(w.name, w.version, w.via) for w in res.widgets}
    assert got == {
        ("fortiGuardThreatLandscape", "1.0.0", "dashboard"),
        ("mapDemo", "1.0.2", "dashboard"),
        ("killchainphases", "2.0.0", "fortiGuardThreatLandscape"),
    }
    assert all(Path(w.path).is_file() for w in res.widgets)
    assert res.connectors == {"fortinet-fortiguard-threat-intelligence": ["fortiGuardThreatLandscape"]}
    assert res.missing == ["gone-3.0.0"]
    assert res.version_mismatch == ["mapDemo: dashboard wants 1.0.0, box has 1.0.2"]
    assert sorted(client.exported) == ["u-kill", "u-land", "u-map"]


def test_no_nested(tmp_path: Path):
    res = _Client().widgets.export_dashboard("d", tmp_path, nested=False)
    assert "killchainphases" not in {w.name for w in res.widgets}


def test_pack_add_dashboard_bundles_dependencies(tmp_path: Path, monkeypatch):
    from pyfsr.utils import packtools

    monkeypatch.setattr(packtools, "_resolve_dashboard", lambda client, v: ("dash-uuid", "Demo"))
    monkeypatch.setattr(packtools, "export_live", lambda client, t, tag: (tmp_path / "x.zip", {"contents": {}}))
    monkeypatch.setattr(packtools.Pack, "absorb_export", lambda self, z, s: {"dashboards": 1})

    client = _Client()

    def export_connector(name, dest):
        out = Path(dest) / f"{name}-4_2_0.tgz"
        out.write_bytes(_tgz(name, "4.2.0", {}))
        return str(out)

    client.connectors.export = export_connector
    pack = packtools.Pack.init(tmp_path / "pack", name="demo", label="Demo", version="1.0.0")
    res = packtools.add_dashboard(client, pack, "Demo")

    widgets = {r["name"] for r in pack.data_manifest("widgets")}
    assert widgets == {"fortiGuardThreatLandscape", "mapDemo", "killchainphases"}
    assert [r["name"] for r in pack.data_manifest("connectors")] == ["fortinet-fortiguard-threat-intelligence"]
    assert (tmp_path / "pack/widgets/killchainphases-2.0.0.tgz").is_file()
    assert res["missing"] == ["gone-3.0.0"]

    bare = packtools.Pack.init(tmp_path / "bare", name="d", label="D", version="1.0.0")
    assert packtools.add_dashboard(_Client(), bare, "Demo", dependencies=False)["widgets"] == []
    assert bare.data_manifest("widgets") == []
