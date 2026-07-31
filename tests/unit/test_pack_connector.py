"""Unit tests for pack_connector and validate_connector_source.

No appliance required.
"""

import json
import tarfile
from pathlib import Path

import pytest

from pyfsr import ConnectorPackageError, pack_connector, validate_connector_source
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
    (src / "info.json").write_text('{"name": "demo", "version": "1.0.0", "label": "Demo", "operations": []}')
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
    (src / "info.json").write_text('{"name": "demo", "version": "1.0.0", "label": "Demo", "operations": []}')
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
    (src / "info.json").write_text('{"name": "demo", "version": "1.0.0", "label": "Demo", "operations": []}')
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


# --- validate_connector_source -------------------------------------------
# Every check below is a real import failure. FortiSOAR reports all of them
# as "Connector with same name is already active." -- even when no connector
# of that name exists -- so none of them can be diagnosed server-side.


def _source(tmp_path, name="demo", folder=None, **info_extra):
    src = tmp_path / (folder or name or "unnamed")
    src.mkdir()
    info = {"name": name, "version": "1.0.0", "label": "Demo", "operations": []}
    info.update(info_extra)
    (src / "info.json").write_text(json.dumps(info))
    (src / "connector.py").write_text("# connector\n")
    (src / "operations.py").write_text("# operations\n")
    (src / "__init__.py").touch()
    return src


def test_clean_source_has_no_problems(tmp_path):
    assert validate_connector_source(str(_source(tmp_path)), strict=False) == []


def test_folder_name_must_match_info_json_name(tmp_path):
    # The appliance's installed layout is <name>_<version> (aws_3_1_2). Packing
    # a folder named that way fails, and the server names no cause.
    src = _source(tmp_path, name="aws-extended", folder="aws-extended_1_0_0")
    problems = validate_connector_source(str(src), strict=False)
    assert any("must match exactly" in p for p in problems)
    # It is blocking, so pack_connector refuses it.
    assert validate_connector_source(str(src), strict=False, blocking_only=True)
    with pytest.raises(ConnectorPackageError, match="must match exactly"):
        pack_connector(str(src), output=str(tmp_path / "out.tgz"))


def test_duplicate_operation_is_blocking(tmp_path):
    src = _source(tmp_path, operations=[{"operation": "run"}, {"operation": "run"}])
    assert any(
        "duplicate operation" in p for p in validate_connector_source(str(src), strict=False, blocking_only=True)
    )


def test_missing_init_py_is_advisory_not_blocking(tmp_path):
    src = _source(tmp_path)
    (src / "__init__.py").unlink()
    assert any("__init__.py" in p for p in validate_connector_source(str(src), strict=False))
    assert validate_connector_source(str(src), strict=False, blocking_only=True) == []
    # Advisories must not stop a rough working folder from packing.
    pack_connector(str(src), output=str(tmp_path / "out.tgz"))


def test_cs_approved_true_is_flagged(tmp_path):
    src = _source(tmp_path, cs_approved=True)
    assert any("cs_approved" in p for p in validate_connector_source(str(src), strict=False))


def test_invalid_json_is_blocking(tmp_path):
    src = _source(tmp_path)
    (src / "info.json").write_text("{not json")
    with pytest.raises(ConnectorPackageError, match="not valid JSON"):
        validate_connector_source(str(src))


def test_missing_source_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_connector_source(str(tmp_path / "nope"))


# --- probed against a live 8.0.0 appliance --------------------------------
# Each rule below was established by installing one info.json mutation at a
# time and recording whether the importer took it. See
# fortisoar/connectors/probe_info_json.py in the Miscellaneous repo.


@pytest.mark.parametrize("version", ["1.0", "banana", 1, "v1.0.0"])
def test_version_must_have_three_numeric_parts(tmp_path, version):
    src = _source(tmp_path, version=version)
    assert any(
        "three numeric parts" in p for p in validate_connector_source(str(src), strict=False, blocking_only=True)
    )


@pytest.mark.parametrize("version", ["1.0.0", "3.1.2", "1.0.0_dev"])
def test_three_part_versions_are_accepted(tmp_path, version):
    src = _source(tmp_path, version=version)
    assert validate_connector_source(str(src), strict=False, blocking_only=True) == []


@pytest.mark.parametrize("key", ["name", "label", "version"])
def test_empty_required_key_is_blocking(tmp_path, key):
    src = _source(tmp_path, **{key: ""})
    assert any(
        f"{key!r} is missing or empty" in p
        for p in validate_connector_source(str(src), strict=False, blocking_only=True)
    )


def test_null_configuration_is_blocking_but_empty_dict_is_fine(tmp_path):
    bad = _source(tmp_path, folder="bad", name="bad", configuration=None)
    assert any(
        "configuration is null" in p for p in validate_connector_source(str(bad), strict=False, blocking_only=True)
    )
    ok = _source(tmp_path, folder="ok", name="ok", configuration={})
    assert validate_connector_source(str(ok), strict=False, blocking_only=True) == []


def test_null_operations_is_blocking_but_empty_list_is_fine(tmp_path):
    bad = _source(tmp_path, folder="bad", name="bad", operations=None)
    assert any("operations is null" in p for p in validate_connector_source(str(bad), strict=False, blocking_only=True))
    ok = _source(tmp_path, folder="ok", name="ok", operations=[])
    assert validate_connector_source(str(ok), strict=False, blocking_only=True) == []


def test_operation_without_title_is_blocking(tmp_path):
    src = _source(tmp_path, operations=[{"operation": "run"}])
    assert any("has no 'title'" in p for p in validate_connector_source(str(src), strict=False, blocking_only=True))


@pytest.mark.parametrize(
    "key,value",
    [
        ("description", ""),
        ("publisher", ""),
        ("cs_compatible", False),
        ("category", "Not A Real Category"),
        ("tags", "a-string"),
        ("ingestion_supported", None),
    ],
)
def test_keys_the_importer_does_not_enforce(tmp_path, key, value):
    # All of these installed cleanly when probed, so none may block a build.
    src = _source(tmp_path, **{key: value})
    assert validate_connector_source(str(src), strict=False, blocking_only=True) == []


def test_declared_operation_missing_from_operations_py_is_advisory(tmp_path):
    # The importer accepts this and it fails only at runtime, so it is worth
    # saying out loud but must not block the build.
    src = _source(tmp_path, operations=[{"operation": "ghost", "title": "Ghost"}])
    (src / "operations.py").write_text("def other(config, params):\n    return {}\n")
    assert any("does not appear" in p for p in validate_connector_source(str(src), strict=False))
    assert validate_connector_source(str(src), strict=False, blocking_only=True) == []
