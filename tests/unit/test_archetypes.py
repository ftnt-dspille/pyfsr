"""Unit tests for the archetype framework (record round-trip, store CRUD, harvester).

Parsing is exercised against a **synthetic minimal pack** built in ``tmp_path`` that mirrors
the real FortiSOAR layout (``info.json`` + ``modules/<mod>/mmd.json`` + ``playbooks/*.json``),
so the committed tests are portable and CI-safe. A ground-truth test against the real on-disk
corpus packs runs when that machine-local path is present and skips otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyfsr.archetypes import (
    Archetype,
    ArchetypeStore,
    harvest_from_dir,
    harvest_from_zip,
    map_use_case,
)
from pyfsr.archetypes.harvest import _dedupe_manifest, _picklist_name, _uuid_tail
from pyfsr.archetypes.record import ConnectorUse

# Machine-local corpus root (skipped when absent).
_CORPUS = Path("/Users/dylanspille/PycharmProjects/Miscellaneous/fortisoar/corpus_builder/repos/fortisoar")


# --------------------------------------------------------------------- fixtures
def _mmd_security_incidents() -> dict:
    """A minimal mmd.json with a string field, a picklist field, and a relationship field.

    Mirrors the real ``security_incidents/mmd.json`` attribute shape (only the keys the
    harvester reads): ``type``/``module`` on the root, and per-attribute
    ``name``/``type``/``formType``/``validation.required``/``descriptions``/``dataSource``.
    """
    return {
        "@type": "StagingModelMetadata",
        "type": "security_incidents",
        "module": "security_incidents",
        "attributes": [
            {
                "name": "name",
                "type": "string",
                "formType": "text",
                "validation": {"required": True},
                "descriptions": {"singular": "Name"},
            },
            {
                "name": "status",
                "type": "picklists",
                "formType": "picklist",
                "validation": {"required": True},
                "descriptions": {"singular": "Status"},
                "dataSource": {
                    "model": "picklists",
                    "query": {
                        "logic": "AND",
                        "filters": [{"field": "listName__name", "operator": "eq", "value": "IncidentStatus"}],
                    },
                },
            },
            {
                "name": "assets",
                "type": "assets",
                "formType": "manyToMany",
                "validation": {"required": False},
                "descriptions": {"singular": "Assets"},
                "dataSource": {"model": "assets"},
            },
        ],
    }


def _playbook_create_ticket() -> dict:
    """A minimal playbook with a connector step and a non-connector step."""
    return {
        "@type": "Workflow",
        "name": "Create Ticket",
        "description": "Create a ticket in the external system",
        "steps": [
            {
                "name": "Start",
                "stepType": "/api/3/workflow_step_types/f414d039-bb0d-4e59-9c39-a8f1e880b18a",
                "arguments": {"route": "abc", "resources": ["alerts"]},
            },
            {
                "name": "Create Ticket",
                "stepType": "/api/3/workflow_step_types/0bfed618-0316-11e7-93ae-92361f002671",
                "arguments": {
                    "connector": "jira",
                    "operation": "create_ticket",
                    "params": {"summary": "{{vars.input.name}}"},
                },
            },
        ],
    }


def _playbook_with_only_operation() -> dict:
    """A step whose arguments carry ``operation`` but no ``connector`` (a record step).

    Such steps must appear in the skeleton but NOT in the connector manifest.
    """
    return {
        "@type": "Workflow",
        "name": "Update Record",
        "description": None,
        "steps": [
            {
                "name": "Append Note",
                "stepType": "/api/3/workflow_step_types/b593663d-0000-0000-0000-000000000000",
                "arguments": {"operation": "Append", "module": "alerts"},
            }
        ],
    }


def _info() -> dict:
    return {
        "name": "syntheticIntegration",
        "version": "2.0.0",
        "label": "Synthetic Integration",
        "description": "A synthetic pack for testing the harvester.",
    }


def _build_synthetic_pack(root: Path) -> Path:
    """Lay out a minimal pack mirroring the real solution-pack directory structure."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "info.json").write_text(json.dumps(_info()), encoding="utf-8")
    mod = root / "modules" / "security_incidents"
    mod.mkdir(parents=True)
    (mod / "mmd.json").write_text(json.dumps(_mmd_security_incidents()), encoding="utf-8")
    pbs = root / "playbooks" / "Use Case"
    pbs.mkdir(parents=True)
    (pbs / "Create Ticket.json").write_text(json.dumps(_playbook_create_ticket()), encoding="utf-8")
    (pbs / "Update Record.json").write_text(json.dumps(_playbook_with_only_operation()), encoding="utf-8")
    # Non-playbook JSON files that must be skipped.
    (root / "playbooks" / "globalVariables.json").write_text("{}", encoding="utf-8")
    (pbs / "collection.metadata.json").write_text("{}", encoding="utf-8")
    return root


# ------------------------------------------------------------------- record tests
def test_archetype_json_round_trip_is_identity():
    arch = Archetype(
        name="x",
        when_to_use="compare two sources",
        description="d",
        module_schema=[],
        connector_manifest=[ConnectorUse("jira", "create_ticket", "Create Ticket")],
        playbook_skeletons=[],
        parameters=[{"name": "recipients", "from": "prompt"}],
        source={"pack_name": "p"},
    )
    rebuilt = Archetype.from_json(arch.to_json())
    assert rebuilt == arch
    assert rebuilt.parameters == arch.parameters
    assert rebuilt.source == arch.source


def test_archetype_to_json_is_valid_json_and_sorted():
    arch = Archetype(name="x", source={"b": 2, "a": 1})
    text = arch.to_json()
    loaded = json.loads(text)
    # full content round-trips
    assert loaded == {
        "name": "x",
        "when_to_use": "",
        "description": "",
        "module_schema": [],
        "connector_manifest": [],
        "playbook_skeletons": [],
        "parameters": [],
        "source": {"a": 1, "b": 2},
    }
    # sort_keys=True: top-level "description" precedes "name" precedes "source"
    assert text.index('"description"') < text.index('"name"') < text.index('"source"')
    # nested source dict is also sorted
    assert text.index('"a"') < text.index('"b"')


# ----------------------------------------------------------------- store tests
def test_store_crud_round_trip(tmp_path):
    store = ArchetypeStore(tmp_path / "arch.db")
    assert store.list() == []

    arch = Archetype(name="recon", when_to_use="compare two sources", description="d")
    got = store.put(arch)
    assert got is arch
    assert store.list() == ["recon"]
    assert store.get("recon") == arch

    assert store.delete("recon") is True
    assert store.delete("recon") is False  # already gone
    assert store.get("recon") is None
    assert store.list() == []


def test_store_put_upserts_does_not_duplicate(tmp_path):
    store = ArchetypeStore(tmp_path / "arch.db")
    store.put(Archetype(name="recon", description="first"))
    store.put(Archetype(name="recon", description="second"))
    assert store.list() == ["recon"]
    assert store.get("recon").description == "second"


def test_store_seed_if_empty_loads_json_dir(tmp_path):
    store = ArchetypeStore(tmp_path / "arch.db")
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "a.json").write_text(Archetype(name="a", when_to_use="wa").to_json(), encoding="utf-8")
    (seed / "b.json").write_text(Archetype(name="b", when_to_use="wb").to_json(), encoding="utf-8")
    (seed / "not-json.txt").write_text("ignore me", encoding="utf-8")

    assert store.seed_if_empty(seed) == 2
    assert sorted(store.list()) == ["a", "b"]
    # second call is a no-op (store is non-empty)
    assert store.seed_if_empty(seed) == 0


def test_store_seed_if_empty_noop_on_empty_seed_dir(tmp_path):
    store = ArchetypeStore(tmp_path / "arch.db")
    assert store.seed_if_empty(tmp_path / "no-seeds") == 0
    assert store.list() == []


def test_store_seed_if_empty_loads_default_package_seed(tmp_path):
    """The shipped package seed dir carries the curated ``reconcile-and-report`` archetype."""
    store = ArchetypeStore(tmp_path / "arch.db")
    assert store.seed_if_empty() == 1
    assert store.list() == ["reconcile-and-report"]
    # second call is a no-op (store is non-empty)
    assert store.seed_if_empty() == 0


def test_connector_use_role_round_trips():
    # a harvested connector use carries no role (defaults to None) -- absent key is tolerated
    assert (
        ConnectorUse.from_dict({"connector": "jira", "operation": "create_ticket", "step_name": "Create Ticket"}).role
        is None
    )
    # a curated connector use carries its role through from_dict and an Archetype round-trip
    curated = ConnectorUse.from_dict(
        {"connector": "smtp", "operation": "send_email", "step_name": "Email", "role": "notify"}
    )
    assert curated.role == "notify"
    arch = Archetype(
        name="x",
        connector_manifest=[ConnectorUse("smtp", "send_email", "Email", role="notify")],
    )
    rebuilt = Archetype.from_json(arch.to_json())
    assert rebuilt.connector_manifest[0].role == "notify"


def test_default_seed_reconcile_and_report_is_curated(tmp_path):
    """The shipped seed is a curated archetype (not a harvested draft).

    Curation (step 3) populates ``when_to_use`` + ``parameters``, assigns connector *roles*
    (``source_a`` / ``source_b`` / ``notify``), and parameterizes the module schema with
    ``{{param}}`` slots -- none of which a harvested draft has.
    """
    store = ArchetypeStore(tmp_path / "arch.db")
    assert store.seed_if_empty() == 1
    arch = store.get("reconcile-and-report")
    assert arch is not None

    # curated, not a harvested draft (which leaves these empty)
    assert arch.when_to_use != ""
    assert arch.source.get("curated") is True
    assert arch.parameters, "curated archetype must declare its parameter slots"

    # the router/agent-facing parameter slots
    names = {p["name"] for p in arch.parameters}
    assert {"results_module", "source_a_label", "source_b_label", "join_key", "recipients", "cron"} <= names

    # connector roles assigned to the plan-verified pilot connectors
    roles = {c.role: (c.connector, c.operation) for c in arch.connector_manifest}
    assert roles["source_a"] == ("forticloud-asset-management", "list_assets")
    assert roles["source_b"] == ("servicenow-cmdb", "get_configuration_items")
    assert roles["notify"] == ("smtp", "send_email")

    # module_schema parameterized with the {{results_module}} slot; picklist fields grounded
    assert all(f.module == "{{results_module}}" for f in arch.module_schema)
    assert any(f.picklist == "MismatchType" and f.type == "picklists" for f in arch.module_schema)


def test_store_default_db_path_under_cache(monkeypatch):
    from pyfsr.archetypes.store import _default_db_path

    monkeypatch.setenv("XDG_CACHE_HOME", str(__import__("pathlib").Path("/tmp/pyfsr-cache-test")))
    p = _default_db_path()
    assert p == Path("/tmp/pyfsr-cache-test/pyfsr/archetypes.db")


# -------------------------------------------------------------- harvester tests
def test_uuid_tail_extracts_trailing_segment():
    assert _uuid_tail("/api/3/workflow_step_types/0bfed618-0316-11e7-93ae-92361f002671") == (
        "0bfed618-0316-11e7-93ae-92361f002671"
    )
    assert _uuid_tail(None) is None
    assert _uuid_tail("") is None
    assert _uuid_tail("/api/3/x/") == "x"  # trailing slash tolerated


def test_picklist_name_resolves_listname_filter():
    attr = {
        "dataSource": {"query": {"filters": [{"field": "listName__name", "operator": "eq", "value": "IncidentStatus"}]}}
    }
    assert _picklist_name(attr) == "IncidentStatus"


def test_picklist_name_none_when_no_filter():
    assert _picklist_name({"dataSource": {"query": {"filters": []}}}) is None
    assert _picklist_name({}) is None


def test_dedupe_manifest_keeps_first_step_per_pair():
    uses = [
        ConnectorUse("jira", "create_ticket", "Create Ticket"),
        ConnectorUse("jira", "create_ticket", "Duplicate"),
        ConnectorUse("jira", "update_ticket", "Update"),
    ]
    out = _dedupe_manifest(uses)
    assert out == [
        ConnectorUse("jira", "create_ticket", "Create Ticket"),
        ConnectorUse("jira", "update_ticket", "Update"),
    ]


def test_harvest_from_dir_on_synthetic_pack(tmp_path):
    root = _build_synthetic_pack(tmp_path / "pack")
    a = harvest_from_dir(root, name="syn-draft")

    # provenance + description from info.json
    assert a.name == "syn-draft"
    assert a.description == "A synthetic pack for testing the harvester."
    assert a.source["pack_name"] == "syntheticIntegration"
    assert a.source["pack_version"] == "2.0.0"
    assert a.source["pack_label"] == "Synthetic Integration"
    assert "T" in a.source["harvested_at"] and a.source["harvested_at"].endswith("Z")

    # draft fields are empty (curation is step 3)
    assert a.when_to_use == ""
    assert a.parameters == []

    # module_schema: string + picklist + relationship fields
    fields = {f.name: f for f in a.module_schema}
    assert fields["name"].module == "security_incidents"
    assert fields["name"].type == "string"
    assert fields["name"].required is True
    assert fields["name"].display_name == "Name"
    assert fields["name"].picklist is None and fields["name"].relationship is None

    assert fields["status"].type == "picklists"
    assert fields["status"].picklist == "IncidentStatus"
    assert fields["status"].required is True
    assert fields["status"].relationship is None

    assert fields["assets"].type == "assets"
    assert fields["assets"].relationship == "assets"
    assert fields["assets"].picklist is None

    # connector manifest: only the connector+operation step, deduped
    assert a.connector_manifest == [ConnectorUse("jira", "create_ticket", "Create Ticket")]

    # skeletons: both playbooks; the operation-only step appears in skeleton but not manifest
    assert len(a.playbook_skeletons) == 2
    create = next(s for s in a.playbook_skeletons if s.name == "Create Ticket")
    assert create.description == "Create a ticket in the external system"
    assert [s.name for s in create.steps] == ["Start", "Create Ticket"]
    create_step = create.steps[1]
    assert create_step.connector == "jira"
    assert create_step.operation == "create_ticket"
    assert create_step.step_type == "0bfed618-0316-11e7-93ae-92361f002671"
    # the operation-only "Append Note" step is in the skeleton, absent from the manifest
    update = next(s for s in a.playbook_skeletons if s.name == "Update Record")
    assert update.steps[0].operation == "Append"
    assert update.steps[0].connector is None
    assert all(u.operation != "Append" for u in a.connector_manifest)


def test_harvest_from_dir_name_defaults_to_pack_name(tmp_path):
    root = _build_synthetic_pack(tmp_path / "pack")
    a = harvest_from_dir(root)
    assert a.name == "syntheticIntegration"


def test_harvest_from_zip_matches_harvest_from_dir(tmp_path):
    root = _build_synthetic_pack(tmp_path / "pack")
    # build a zip of the pack
    zip_path = tmp_path / "pack.zip"
    import zipfile

    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in root.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(root))

    from_dir = harvest_from_dir(root, name="z")
    from_zip = harvest_from_zip(zip_path, name="z")

    # harvested_at differs (wall clock); compare everything else
    from_dir.source.pop("harvested_at", None)
    from_zip.source.pop("harvested_at", None)
    assert from_zip == from_dir


def test_harvest_from_dir_on_pack_with_no_playbooks(tmp_path):
    """A pack with modules but no playbooks harvests fields and empty skeletons."""
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    (root / "info.json").write_text(json.dumps(_info()), encoding="utf-8")
    mod = root / "modules" / "security_incidents"
    mod.mkdir(parents=True)
    (mod / "mmd.json").write_text(json.dumps(_mmd_security_incidents()), encoding="utf-8")

    a = harvest_from_dir(root, name="no-pbs")
    assert len(a.module_schema) == 3
    assert a.playbook_skeletons == []
    assert a.connector_manifest == []


# ------------------------------------------------- ground-truth (real corpus) ---
def _corpus_pack(name: str) -> Path:
    return _CORPUS / f"solution-pack-{name}"


@pytest.mark.skipif(not _CORPUS.exists(), reason="corpus packs not present on this machine")
def test_ground_truth_servicenow_sir_pack():
    a = harvest_from_dir(_corpus_pack("servicenow-security-incident-response-integration"), name="snow-sir-draft")
    assert a.source["pack_name"] == "serviceNowSecurityIncidentResponseIntegration"
    fields = {f.name: f for f in a.module_schema}
    # status -> IncidentStatus picklist, required
    assert fields["status"].picklist == "IncidentStatus"
    assert fields["status"].required is True
    # assets -> relationship to "assets"
    assert fields["assets"].relationship == "assets"
    # that pack ships no playbooks
    assert a.playbook_skeletons == []
    assert a.connector_manifest == []


@pytest.mark.skipif(not _CORPUS.exists(), reason="corpus packs not present on this machine")
def test_ground_truth_jira_pack_has_playbooks_and_manifest():
    a = harvest_from_dir(_corpus_pack("jira-bi-directional-integration"), name="jira-draft")
    assert len(a.playbook_skeletons) >= 1
    assert ("jira", "create_ticket") in [(u.connector, u.operation) for u in a.connector_manifest]
    create = next(s for s in a.playbook_skeletons if s.name == "Create Jira Ticket")
    assert any(s.connector == "jira" and s.operation == "create_ticket" for s in create.steps)


# ------------------------------------------------------ end-to-end harvest+store
def test_harvest_then_put_then_get_round_trips(tmp_path):
    root = _build_synthetic_pack(tmp_path / "pack")
    store = ArchetypeStore(tmp_path / "arch.db")
    draft = harvest_from_dir(root, name="syn")
    store.put(draft)
    got = store.get("syn")
    assert got.module_schema == draft.module_schema
    assert got.connector_manifest == draft.connector_manifest
    assert got.playbook_skeletons == draft.playbook_skeletons
    assert got.source["pack_name"] == draft.source["pack_name"]


# ------------------------------------------------------------------------- router
def _seeded_store(tmp_path):
    """A hermetic store seeded with the shipped default archetype (reconcile-and-report)."""
    store = ArchetypeStore(tmp_path / "arch.db")
    assert store.seed_if_empty() == 1
    return store


def test_map_use_case_matches_reconcile_pilot(tmp_path):
    """The pilot use case classifies to reconcile-and-report with parameters filled.

    Labels + join_key are inferred from the archetype's own manifest/schema; results_module +
    cron take their shipped defaults; recipients stays pending (a deployment value the use
    case text cannot supply).
    """
    store = _seeded_store(tmp_path)
    r = map_use_case(
        "compare FortiCloud assets vs ServiceNow CMDB, email a CSV on mismatches",
        store=store,
    )
    assert r["archetype"] == "reconcile-and-report"
    assert r["confidence"] >= 0.5
    assert "forticloud" in r["rationale"] and "servicenow" in r["rationale"]

    params = r["parameters"]
    # <role>_label params inferred from the connector manifest's roles
    assert params["source_a_label"]["value"] == "FortiCloud assets"
    assert params["source_a_label"]["source"] == "inferred"
    assert params["source_b_label"]["value"] == "ServiceNow CMDB"
    assert params["source_b_label"]["source"] == "inferred"
    # join_key inferred from the module schema (the field literally named join_key)
    assert params["join_key"]["value"] == "join_key"
    assert params["join_key"]["source"] == "inferred"
    # prompt params with shipped defaults
    assert params["results_module"]["value"] == "reconciliation_results"
    assert params["results_module"]["source"] == "default"
    assert params["cron"]["value"] == "7 2 * * *"
    assert params["cron"]["source"] == "default"
    # recipients cannot be inferred -- still pending
    assert params["recipients"]["value"] is None
    assert params["recipients"]["source"] == "pending"

    # required-ness passes through; cron is the one optional slot
    assert params["cron"]["required"] is False
    assert all(
        params[n]["required"] is True
        for n in ("results_module", "source_a_label", "source_b_label", "join_key", "recipients")
    )

    assert r["pending"] == ["recipients"]
    assert "5/6 parameters filled" in r["notes"]


def test_map_use_case_no_fit_returns_candidates(tmp_path):
    """An unrelated use case reports no confident match + the ranked candidates."""
    store = _seeded_store(tmp_path)
    r = map_use_case("organize my music library collection", store=store)
    assert r["archetype"] is None
    assert "no archetype confidently matches" in r["notes"]
    # the sole archetype is still listed as a candidate, but with zero overlap
    assert r["candidates"][0]["name"] == "reconcile-and-report"
    assert r["candidates"][0]["score"] < 0.2
    assert r["candidates"][0]["matched"] == []


def test_map_use_case_empty_store_no_fit(tmp_path):
    """An empty store yields no match + no candidates (does not raise)."""
    store = ArchetypeStore(tmp_path / "arch.db")  # not seeded -> empty
    r = map_use_case("compare two inventories", store=store)
    assert r["archetype"] is None
    assert r["candidates"] == []
    assert "no archetype confidently matches" in r["notes"]
