"""Unit tests for pack_connector -- no appliance required."""

import tarfile
from pathlib import Path

import pytest

from pyfsr import pack_connector
from pyfsr.api.connectors import _PACK_EXCLUDE_DIRS, _PACK_EXCLUDE_SUFFIXES

SAMPLE = Path(__file__).parent.parent / "resources" / "sample_connector" / "result-probe"


def test_single_top_level_dir_and_info_json(tmp_path):
    out = pack_connector(str(SAMPLE), output=str(tmp_path / "probe.tgz"))
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert {n.split("/")[0] for n in names} == {"result-probe"}
    assert "result-probe/info.json" in names


def test_excludes_bytecode(tmp_path):
    # Plant a __pycache__ dir + .pyc next to a real source folder.
    src = tmp_path / "demo"
    src.mkdir()
    (src / "info.json").write_text('{"name": "demo"}')
    (src / "connector.py").write_text("# x\n")
    cache = src / "__pycache__"
    cache.mkdir()
    (cache / "connector.cpython-312.pyc").write_bytes(b"\x00")
    (src / "stale.pyc").write_bytes(b"\x00")

    out = pack_connector(str(src), output=str(tmp_path / "demo.tgz"))
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    assert "demo/connector.py" in names


def test_excludes_venv_tests_caches_and_cruft(tmp_path):
    """pack_connector must skip .venv, tests, .DS_Store, .pytest_cache, etc."""
    src = tmp_path / "demo"
    src.mkdir()
    (src / "info.json").write_text('{"name": "demo"}')
    (src / "connector.py").write_text("# x\n")

    # Plant every artifact that should NOT end up in the tgz.
    for d in (".venv", "tests", ".pytest_cache", ".git", "__pycache__", "build"):
        dpath = src / d
        dpath.mkdir()
        (dpath / "junk.py").write_text("# junk\n")
    (src / ".DS_Store").write_bytes(b"\x00")
    (src / "stale.pyc").write_bytes(b"\x00")
    (src / ".gitignore").write_text("*.pyc\n")

    out = pack_connector(str(src), output=str(tmp_path / "demo.tgz"))
    with tarfile.open(out) as tar:
        names = tar.getnames()
    bad = [n for n in names if any(p in _PACK_EXCLUDE_DIRS for p in Path(n).parts)]
    bad += [n for n in names if n.endswith(_PACK_EXCLUDE_SUFFIXES)]
    bad += [n for n in names if Path(n).name == ".DS_Store" or Path(n).name == ".gitignore"]
    assert bad == [], f"these should have been excluded: {bad}"
    assert "demo/connector.py" in names
    assert "demo/info.json" in names


def test_default_output_path(tmp_path):
    src = tmp_path / "demo"
    src.mkdir()
    (src / "info.json").write_text("{}")
    out = pack_connector(str(src))
    assert Path(out) == src.with_suffix(".tgz")
    assert Path(out).exists()


def test_missing_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        pack_connector(str(tmp_path / "nope"))


def test_not_a_connector_raises(tmp_path):
    src = tmp_path / "plain"
    src.mkdir()
    (src / "readme.txt").write_text("hi")
    with pytest.raises(ValueError, match="info.json"):
        pack_connector(str(src))
