"""Unit tests for the pyfsr-native playbook-build surface (authoring.py):
``verify_playbook_yaml`` (the verify gate + skip flags), ``build_and_deploy``
(verify → compile → push), and ``find_operation`` (catalog discovery).

These wrap the fsr_playbooks compiler/verify gate, so they skip when the
optional ``fsr_playbooks`` extra is absent.
"""

from __future__ import annotations

import importlib.util

import pytest

requires_compiler = pytest.mark.skipif(
    importlib.util.find_spec("fsr_playbooks") is None,
    reason="fsr_playbooks (playbooks extra) not installed",
)

# Clean playbook (compiles + verifies).
GOOD_YAML = """collection: PyfsrTest Pack
visible: true
playbooks:
  - name: PyfsrTest PB
    steps:
      - name: Start
        type: start
        next: Set Var
      - name: Set Var
        type: set_variable
        vars:
          foo: bar
"""

# Hard Jinja syntax error (missing endif) → blocks the verify gate.
BAD_JINJA_YAML = """collection: PyfsrTest Pack
visible: true
playbooks:
  - name: PyfsrTest PB
    steps:
      - name: Start
        type: start
        next: Set Var
      - name: Set Var
        type: set_variable
        vars:
          foo: "{% if x %}hi"
"""


def _slim_db() -> str:
    from fsr_playbooks._db import default_db_path

    return str(default_db_path())


class _FakeDeployClient:
    """Minimal client exposing the one method build_and_deploy needs."""

    def __init__(self):
        self.imported = []
        self.workflow_collections = self._WC(self)

    class _WC:
        def __init__(self, parent):
            self.parent = parent

        def import_export(self, data, *, replace=False):
            self.parent.imported.append((data, replace))
            return [{"name": "PyfsrTest Pack", "uuid": "col-1"}]


# --- verify_playbook_yaml -------------------------------------------------


@requires_compiler
def test_verify_good_is_ready():
    from pyfsr.authoring import verify_playbook_yaml

    v = verify_playbook_yaml(GOOD_YAML, db_path=_slim_db())
    assert v.ready and bool(v) and v.ok
    assert v.required_fixes == []


@requires_compiler
def test_verify_bad_jinja_blocks():
    from pyfsr.authoring import verify_playbook_yaml

    v = verify_playbook_yaml(BAD_JINJA_YAML, db_path=_slim_db())
    assert not v.ready
    assert "jinja_syntax_error" in [f["code"] for f in v.required_fixes]


@requires_compiler
def test_verify_skip_suppresses_and_unblocks():
    from pyfsr.authoring import verify_playbook_yaml

    v = verify_playbook_yaml(BAD_JINJA_YAML, db_path=_slim_db(), skip=["jinja"])
    assert v.ready
    assert [s["code"] for s in v.suppressed] == ["jinja_syntax_error"]


# --- build_and_deploy -----------------------------------------------------


@requires_compiler
def test_deploy_good_pushes():
    from pyfsr.authoring import build_and_deploy

    client = _FakeDeployClient()
    d = build_and_deploy(GOOD_YAML, client=client, db_path=_slim_db())
    assert d.deployed and bool(d)
    assert d.stopped_at is None
    assert len(client.imported) == 1


@requires_compiler
def test_deploy_blocked_by_verify_does_not_push():
    from pyfsr.authoring import build_and_deploy

    client = _FakeDeployClient()
    d = build_and_deploy(BAD_JINJA_YAML, client=client, db_path=_slim_db())
    assert not d.deployed
    assert d.stopped_at == "verify"
    assert client.imported == []  # never pushed


@requires_compiler
def test_deploy_force_overrides_verify_gate():
    from pyfsr.authoring import build_and_deploy

    client = _FakeDeployClient()
    d = build_and_deploy(BAD_JINJA_YAML, client=client, db_path=_slim_db(), force=True)
    # force pushes past the verify gate; compile still has to succeed for a push.
    assert d.stopped_at in (None, "compile")


# --- find_operation -------------------------------------------------------


@requires_compiler
def test_find_operation_returns_matches_shape():
    from pyfsr.authoring import find_operation

    # Wiring test: returns the discovery envelope regardless of catalog contents.
    res = find_operation("nist-nvd", "cve", db_path=_slim_db())
    assert isinstance(res, dict)
    assert "matches" in res or "error" in res or "code" in res
