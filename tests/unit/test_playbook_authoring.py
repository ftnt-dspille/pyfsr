"""Unit tests for the YAML → FortiSOAR playbook bridge.

Covers ``pyfsr.authoring`` (the compile bridge) and the
``WorkflowCollectionsAPI.compile_yaml`` / ``import_from_yaml`` methods.
"""

from __future__ import annotations

import importlib.util

import pytest

from pyfsr.api.workflow_collections import WorkflowCollectionsAPI

# The YAML compiler lives in the optional ``fsr_playbooks`` extra, which requires
# Python >=3.12. Tests that exercise real compilation skip when it is absent;
# the missing-extra test below stubs the import and always runs.
requires_compiler = pytest.mark.skipif(
    importlib.util.find_spec("fsr_playbooks") is None,
    reason="fsr_playbooks (playbooks extra) not installed",
)

# A minimal playbook that compiles cleanly against the packaged reference catalog.
GOOD_YAML = """collection: PyfsrTest Pack
description: unit test
visible: true
playbooks:
  - name: PyfsrTest PB
    is_active: false
    steps:
      - name: Start
        type: start
        next: Set Var
      - name: Set Var
        type: set_variable
        vars:
          foo: bar
"""

BAD_YAML = """collection: PyfsrTest Pack
playbooks:
  - name: PyfsrTest PB
    steps:
      - name: Start
        type: not_a_real_step_type
"""


class RecordingClient:
    def __init__(self):
        self.calls = []

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"@type": "WorkflowCollection", "name": "PyfsrTest Pack", "uuid": "col-1"}

    def get(self, endpoint, params=None, **kw):  # for exists() during replace
        self.calls.append(("GET", endpoint, params))
        return {"uuid": "col-1"}

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))


def api():
    c = RecordingClient()
    return WorkflowCollectionsAPI(c), c


# --- pyfsr.authoring -----------------------------------------------------
@requires_compiler
def test_compile_good_yaml_produces_envelope():
    from pyfsr.authoring import compile_playbook_yaml

    result = compile_playbook_yaml(GOOD_YAML)
    assert result.ok
    assert result.fsr_json["type"] == "workflow_collections"
    assert result.collection_names == ["PyfsrTest Pack"]
    assert result.playbook_names == ["PyfsrTest PB"]
    assert result.blocking == []


@requires_compiler
def test_compile_bad_yaml_reports_blocking_errors():
    from pyfsr.authoring import compile_playbook_yaml

    result = compile_playbook_yaml(BAD_YAML)
    assert not result.ok
    assert result.blocking
    assert all(d.get("severity") != "warning" for d in result.blocking)


def test_missing_extra_raises_friendly_error(monkeypatch):
    import builtins

    from pyfsr import authoring

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fsr_playbooks" or name.startswith("fsr_playbooks."):
            raise ImportError("No module named 'fsr_playbooks'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(authoring.PlaybooksExtraNotInstalled):
        authoring.compile_playbook_yaml(GOOD_YAML)


# --- WorkflowCollectionsAPI.compile_yaml / import_from_yaml --------------
@requires_compiler
def test_api_compile_yaml_accepts_text():
    a, _ = api()
    result = a.compile_yaml(GOOD_YAML)
    assert result.ok
    assert result.collection_names == ["PyfsrTest Pack"]


@requires_compiler
def test_import_from_yaml_posts_compiled_envelope():
    a, c = api()
    out = a.import_from_yaml(GOOD_YAML)
    posts = [call for call in c.calls if call[0] == "POST"]
    assert len(posts) == 1
    # The posted body is the bare collection extracted from the compiled envelope.
    assert posts[0][2]["name"] == "PyfsrTest Pack"
    assert out[0]["uuid"] == "col-1"


@requires_compiler
def test_import_from_yaml_forwards_replace(monkeypatch):
    a, _ = api()
    seen = {}

    def fake_import_export(data, *, replace=False):
        seen["replace"] = replace
        seen["type"] = data["type"]
        return []

    monkeypatch.setattr(a, "import_export", fake_import_export)
    a.import_from_yaml(GOOD_YAML, replace=True)
    assert seen == {"replace": True, "type": "workflow_collections"}


@requires_compiler
def test_import_from_yaml_raises_on_compile_error():
    a, c = api()
    with pytest.raises(ValueError, match="failed to compile"):
        a.import_from_yaml(BAD_YAML)
    assert [call for call in c.calls if call[0] == "POST"] == []


@requires_compiler
def test_read_yaml_source_reads_file(tmp_path):
    f = tmp_path / "pb.yaml"
    f.write_text(GOOD_YAML, encoding="utf-8")
    a, _ = api()
    # A path string ending in .yaml is read from disk.
    result = a.compile_yaml(str(f))
    assert result.ok


def test_read_yaml_source_missing_file():
    a, _ = api()
    with pytest.raises(FileNotFoundError):
        a.compile_yaml("/no/such/path/playbook.yaml")
