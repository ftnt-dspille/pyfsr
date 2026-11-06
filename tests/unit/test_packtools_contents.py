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
