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


# --- pyfsr.authoring.warm_catalog ---------------------------------------
class _FakeUsers:
    def __init__(self, teams):
        self._teams = teams

    def list_teams(self, params=None):
        return self._teams


class _FakePicklists:
    def __init__(self, data):
        from pyfsr.models import PicklistItem

        # {name: [PicklistItem]} — warm_catalog reads typed items via .all().
        self._data = {
            name: [PicklistItem(itemValue=it["itemValue"], **{"@id": it["iri"]}) for it in items]
            for name, items in data.items()
        }

    def list(self):
        return list(self._data)

    def all(self, *, refresh=False):
        return self._data


class _FakeTags:
    def __init__(self, tags_resp):
        # tags_resp uses the legacy {hydra:member:[{name, @id}]} shape; expose it
        # as the {name: iri} map warm_catalog now consumes.
        self._map = {
            m["name"]: m["@id"] for m in (tags_resp or {}).get("hydra:member", []) if m.get("name") and m.get("@id")
        }

    def map_names(self, *, limit=None):
        return self._map


class _FakeConnectors:
    """Returns one configured connector + a typed definition for warm_catalog."""

    def __init__(self, definitions):
        from pyfsr.models import ConnectorDefinition, InstalledConnector

        self._defs = {name: ConnectorDefinition.model_validate(d) for name, d in (definitions or {}).items()}
        self._configured = [
            InstalledConnector(name=name, version=d.version or "1.0.0") for name, d in self._defs.items()
        ]

    def list_configured(self, *, refresh=False):
        return self._configured

    def definition(self, name, *, version=None):
        return self._defs[name]


class _WarmFakeClient:
    """Minimal client for warm_catalog: users + picklists + tags + connectors."""

    base_url = "https://box.example.com:443"

    def __init__(self, teams, picklists, tags_resp, definitions=None):
        self.users = _FakeUsers(teams)
        self.picklists = _FakePicklists(picklists)
        self.tags = _FakeTags(tags_resp)
        self.connectors = _FakeConnectors(definitions)

    def get(self, endpoint, params=None, **kw):
        return {"hydra:member": []}


@requires_compiler
def test_warm_catalog_populates_teams_picklists_tags(tmp_path):
    from pyfsr.authoring import warm_catalog

    client = _WarmFakeClient(
        teams=[{"name": "TeamA", "uuid": "t-1"}, {"name": "TeamB", "uuid": "t-2"}],
        picklists={"Severity": [{"itemValue": "High", "iri": "/api/3/picklists/p-1"}]},
        tags_resp={
            "hydra:member": [
                {"name": "phishing", "@id": "/api/3/tags/g-1"},
            ]
        },
    )
    db = tmp_path / "warmed.db"
    summary = warm_catalog(client, db)
    assert db.exists()
    assert summary["teams"] == 2
    assert summary["picklist_items"] == 1
    assert summary["tags"] == 1
    # Per-section + total timing is tracked (ints, milliseconds).
    for key in ("teams_ms", "picklists_ms", "tags_ms", "total_ms"):
        assert key in summary and isinstance(summary[key], int)

    import sqlite3

    conn = sqlite3.connect(db)
    teams = dict(conn.execute("SELECT name, iri FROM teams").fetchall())
    assert teams == {
        "TeamA": "/api/3/teams/t-1",
        "TeamB": "/api/3/teams/t-2",
    }
    assert conn.execute(
        "SELECT item_iri FROM picklists WHERE list_name='Severity' AND item_value='High'"
    ).fetchone() == ("/api/3/picklists/p-1",)
    assert conn.execute("SELECT iri FROM tags WHERE name='phishing'").fetchone() == ("/api/3/tags/g-1",)
    conn.close()


@requires_compiler
def test_warm_catalog_incremental_skips_fresh_sections(tmp_path):
    """With max_age set, a second warm skips sections warmed within the window —
    no client calls, cached counts reported, <section>_skipped flagged."""
    from pyfsr.authoring import warm_catalog

    class _CountingTeams(_FakeUsers):
        def __init__(self, teams):
            super().__init__(teams)
            self.calls = 0

        def list_teams(self, params=None):
            self.calls += 1
            return super().list_teams(params)

    client = _WarmFakeClient(
        teams=[{"name": "TeamA", "uuid": "t-1"}],
        picklists={"Severity": [{"itemValue": "High", "iri": "/api/3/picklists/p-1"}]},
        tags_resp={"hydra:member": []},
    )
    client.users = _CountingTeams([{"name": "TeamA", "uuid": "t-1"}])
    db = tmp_path / "incr.db"

    first = warm_catalog(client, db, connectors=False, max_age=3600)
    assert first["teams"] == 1
    assert "teams_skipped" not in first
    assert client.users.calls == 1

    second = warm_catalog(client, db, connectors=False, max_age=3600)
    assert second["teams"] == 1  # cached count preserved
    assert second["teams_skipped"] == 1
    assert second["teams_ms"] == 0
    assert client.users.calls == 1  # NOT re-fetched

    # max_age=None (default) always re-pulls
    third = warm_catalog(client, db, connectors=False)
    assert "teams_skipped" not in third
    assert client.users.calls == 2


@requires_compiler
def test_warm_catalog_writes_connector_ops_and_params(tmp_path):
    """The connector path writes operations AND params from a typed definition,
    and stamps provenance. Guards the typed-OperationParam + source regressions
    that the live run surfaced."""
    from pyfsr.authoring import warm_catalog

    client = _WarmFakeClient(
        teams=[],
        picklists={},
        tags_resp={"hydra:member": []},
        definitions={
            "code-runner": {
                "name": "code-runner",
                "version": "1.2.0",
                "label": "Code Runner",
                "operations": [
                    {
                        "operation": "run",
                        "title": "Run",
                        # visible/enabled omitted -> default True
                        "parameters": [
                            {"name": "code", "type": "text", "required": True},
                            {"name": "timeout", "type": "integer", "value": 30},
                        ],
                    }
                ],
            }
        },
    )
    db = tmp_path / "warm_conn.db"
    summary = warm_catalog(client, db)
    assert summary["connectors"] == 1
    assert summary["operations"] == 1
    assert summary["operation_params"] == 2  # both params written (not skipped)

    import sqlite3

    conn = sqlite3.connect(db)
    # provenance stamped (P3) + NOT NULL source satisfied
    assert conn.execute("SELECT source, source_path FROM connectors WHERE name='code-runner'").fetchone() == (
        "live",
        "https://box.example.com:443",
    )
    # omitted visible/enabled default to visible=1 on the op
    assert conn.execute("SELECT visible, enabled FROM operations WHERE op_name='run'").fetchone() == (1, 1)
    params = dict(conn.execute("SELECT param_name, required FROM operation_params WHERE op_name='run'").fetchall())
    assert params == {"code": 1, "timeout": 0}
    conn.close()


@requires_compiler
@pytest.mark.xfail(
    strict=False,
    reason=(
        "TDD for an in-progress authoring feature: the YAML `owners:` field is not "
        "yet emitted (the fsr_playbooks emitter hardcodes owners=[]/isPrivate=False, "
        "and the IR doesn't model ownership), and `api_endpoint` is not a registered "
        "step type (the SHORT_TYPE_TO_FSR friendly-type map ships in the installed "
        "fsr_playbooks wheel). Both need fsr_playbooks compiler support; remove this "
        "xfail once owner-resolution + the api_endpoint step type land there."
    ),
)
def test_warm_catalog_enables_name_based_owner_resolution(tmp_path, monkeypatch):
    """Seamless: compile_playbook_yaml(client=...) warms + resolves `owners: [TeamA]`."""
    from pyfsr.authoring import compile_playbook_yaml

    client = _WarmFakeClient(
        teams=[{"name": "TeamA", "uuid": "d34aff9d-3b61-413e-8ced-854743e8ddcc"}],
        picklists={},
        tags_resp={"hydra:member": []},
    )
    # Keep the seamless warm off the real ~/.cache — point it at tmp_path.
    monkeypatch.setattr("pyfsr.authoring._default_cache_db", lambda: tmp_path / "cache.db")

    yaml = """
collection: 00-test
playbooks:
  - name: Lookup IP
    owners: ["TeamA"]
    steps:
      - name: Start
        type: api_endpoint
        arguments:
          route: lookup_ip
          authentication_methods: [""]
"""
    result = compile_playbook_yaml(yaml, client=client)
    assert result.ok, result.blocking
    wf = result.fsr_json["data"][0]["workflows"][0]
    assert wf["isPrivate"] is True
    assert wf["owners"] == ["/api/3/teams/d34aff9d-3b61-413e-8ced-854743e8ddcc"]
