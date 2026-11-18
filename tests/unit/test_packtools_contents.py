"""Pack.sync_contents: the info.json claims the importer actually reads."""

import io
import json
import tarfile
from pathlib import Path

from pyfsr.utils.packtools import Pack


def _widget_tgz(tmp_path: Path, name: str, title: str) -> Path:
    tgz = tmp_path / f"{name}-1.0.0.tgz"
    data = json.dumps({"name": name, "title": title, "version": "1.0.0"}).encode()
    with tarfile.open(tgz, "w:gz") as tf:
        info = tarfile.TarInfo(f"{name}-1.0.0/info.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return tgz


def test_widget_claim_carries_api_name_and_title(tmp_path: Path) -> None:
    pack = Pack.init(tmp_path / "pack", name="demo", label="Demo", version="1.0.0")
    pack.add_installer("widgets", _widget_tgz(tmp_path, "mapDemo", "Map (Demo)"))
    contents = pack.sync_contents()
    # A bare {"name": ...} leaves the importer's widget list empty.
    assert contents["widgets"] == [{"apiName": "mapDemo", "name": "Map (Demo)"}]


def test_widget_claim_falls_back_to_api_name_without_title(tmp_path: Path) -> None:
    pack = Pack.init(tmp_path / "pack", name="demo", label="Demo", version="1.0.0")
    pack.add_installer("widgets", _widget_tgz(tmp_path, "mapDemo", ""))
    assert pack.sync_contents()["widgets"] == [{"apiName": "mapDemo", "name": "mapDemo"}]


def test_bundled_widget_is_written_in_the_published_form(tmp_path: Path) -> None:
    """A development-form widget must reach the pack ready (Ctrl, development false), never as a draft."""
    src = tmp_path / "myw-2.0.0.tgz"
    members = {
        "myw-2.0.0/info.json": json.dumps({"name": "myw", "version": "2.0.0", "development": True}).encode(),
        "myw-2.0.0/view.controller.js": b".controller('myw200DevCtrl', function(){});",
        "myw-2.0.0/edit.controller.js": b".controller('editMyw200DevCtrl', function(){});",
    }
    with tarfile.open(src, "w:gz") as tf:
        for path, blob in members.items():
            entry = tarfile.TarInfo(path)
            entry.size = len(blob)
            tf.addfile(entry, io.BytesIO(blob))
    pack = Pack.init(tmp_path / "pack", name="demo", label="Demo", version="1.0.0")
    pack.add_installer("widgets", src)
    with tarfile.open(tmp_path / "pack" / "widgets" / src.name) as tf:
        assert json.loads(tf.extractfile("myw-2.0.0/info.json").read())["development"] is False
        assert b"myw200Ctrl" in tf.extractfile("myw-2.0.0/view.controller.js").read()
        assert b"DevCtrl" not in tf.extractfile("myw-2.0.0/edit.controller.js").read()
    assert src.read_bytes()  # the caller's source package is left alone
    with tarfile.open(src) as tf:
        assert json.loads(tf.extractfile("myw-2.0.0/info.json").read())["development"] is True
