"""Round-trip probes for the pydantic-converted models (pass 1 + pass 2).

These tests pull REAL captured wire shapes from the replay fixture table
(:mod:`pyfsr._testing.replay_http._FIXTURES`) and run each through the
converted pydantic model via ``model_validate`` (the strict entry path, not
just keyword construction), then round-trip via ``model_dump`` to surface any
coercion gap that the keyword-construction unit tests would miss.

The fixtures are real wire captured from a live 8.0 box, so a pydantic
coercion gap surfaces here the same way G9's "live-grounded by probing a real
appliance" works — just offline (the captured wire IS the live shape). If a
future pydantic version tightens validation, or a model field type is
narrowed, these tests fail before the change ships.

Scope: the pass-2 public-return-type models that were dataclasses
yesterday — :class:`~pyfsr.concurrency.ConcurrencyResult`,
:class:`~pyfsr.playbook_lint.ConnectorRef`/:class:`~pyfsr.playbook_lint.LintFinding`,
:class:`~pyfsr.playbook_match.StepInfo`/:class:`~pyfsr.playbook_match.ParsedPlaybook`,
:class:`~pyfsr.playbook_library.LibraryEntry`. Pass-1 models are exercised
transitively via :func:`demo_client` and the existing doctest suite; this
file locks the pass-2 layer explicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pyfsr._testing.replay_http import _FIXTURES, demo_client
from pyfsr.concurrency import ConcurrencyResult, compute_overlap
from pyfsr.playbook_library import LibraryEntry, library_manifest, list_library
from pyfsr.playbook_lint import ConnectorRef, LintFinding, check_connector_configs
from pyfsr.playbook_match import ParsedPlaybook, StepInfo, parse_playbook


def _fixture(method: str, path: str):
    """Real captured wire body for (method, path); KeyError if absent."""
    return _FIXTURES[(method, path)]["body"]


# -- ParsedPlaybook / StepInfo ---------------------------------------------- #


def test_parsed_playbook_round_trips_real_workflow_definition():
    """parse_playbook over a real /api/3/workflows/<uuid> capture (steps inlined).

    The fixture is a 2-step playbook: a Manual Trigger start + a Connectors
    step that blocks an IP via fortigate. Pydantic must accept the parsed
    shape via model_validate and reproduce it byte-for-byte on model_dump.
    """
    body = _fixture("GET", "api/3/workflows/00000000-0000-0000-0000-0000000000aa")
    pb = parse_playbook(body)

    # The fixture is a real playbook: a manual trigger + a connector step.
    assert pb.name == "Block IP (test fixture)"
    assert pb.uuid == "00000000-0000-0000-0000-0000000000aa"
    assert len(pb.steps) == 2

    # The connector step extracts connector + operation from arguments.
    conn_step = next(s for s in pb.steps if s.connector is not None)
    assert conn_step.connector == "fortigate"
    assert conn_step.operation == "block_ip"
    # arguments dict is preserved (StepInfo.arguments, default_factory=dict)
    assert "connector" in conn_step.arguments and "operation" in conn_step.arguments

    # Round-trip via model_dump -> model_validate reproduces the parsed shape.
    rt = ParsedPlaybook.model_validate(pb.model_dump())
    assert rt.name == pb.name and rt.uuid == pb.uuid
    assert len(rt.steps) == len(pb.steps)
    for a, b in zip(rt.steps, pb.steps):
        assert a.name == b.name
        assert a.step_type_raw == b.step_type_raw
        assert a.connector == b.connector
        assert a.operation == b.operation
        assert a.arguments == b.arguments


def test_step_info_frozen_rejects_mutation():
    """StepInfo has ConfigDict(frozen=True) — pass 2 preserved the frozen dataclass."""
    si = StepInfo(name="x", step_type_raw=None, connector=None, operation=None, arguments={})
    with pytest.raises(Exception):  # pydantic raises ValidationError on frozen-field write
        si.name = "mutated"


# -- ConcurrencyResult ----------------------------------------------------- #


def test_concurrency_result_round_trips_real_execution_history():
    """compute_overlap over a real /api/wf/api/workflows/ run capture.

    The fixture run uses ``created``/``modified`` (FSR's actual field names),
    not ``startDate``/``endDate`` — so this exercises the default-field-name
    fallback list in compute_overlap. Real wire + pydantic round-trip must hold.
    """
    body = _fixture("GET", "api/wf/api/workflows/")
    runs = body.get("hydra:member") if isinstance(body, dict) else body
    assert runs, "fixture should have at least one run"

    result = compute_overlap(runs)
    assert result.run_count >= 1
    assert result.max_concurrent >= 1
    assert len(result.events) >= 2  # at least one start + one end

    # to_dict() == model_dump() — pass 2 kept to_dict() as a model_dump alias.
    assert result.to_dict() == result.model_dump()

    # Round-trip
    rt = ConcurrencyResult.model_validate(result.model_dump())
    assert rt == result
    assert rt.events == result.events


def test_concurrency_result_stress_100_concurrent():
    """Control: 100 truly-concurrent runs (same window) reach max_concurrent=100.

    Not a wire-shape probe — confirms the sweep reaches the full peak and the
    model holds the volume without truncation.
    """
    base = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
    runs = [{"startDate": base.isoformat(), "endDate": (base + timedelta(seconds=60)).isoformat()} for _ in range(100)]
    big = compute_overlap(runs)
    assert big.max_concurrent == 100


# -- ConnectorRef / LintFinding --------------------------------------------- #


def test_connector_ref_and_lint_finding_round_trip_real_wire():
    """check_connector_configs over the real /api/integration/connectors/ capture.

    The fixture has 4 configured connectors (smtp, mitre-attack, ...). We
    synthesize three ConnectorRefs to exercise two of the three LintFinding
    branches: ``not-installed`` (a connector absent from the fixture) and
    ``config-missing`` (a real connector with a dead config UUID pinned).
    The third branch, ``no-config``, needs a connector with ``config_count=0``
    (none in the fixture) and is exercised by unit tests in test_playbook_lint.
    """
    body = _fixture("GET", "api/integration/connectors/")
    connectors = body.get("data") or []
    assert len(connectors) >= 1, "fixture should have at least one connector"
    first_name = connectors[0].get("name")
    assert first_name, "fixture connector should have a name"

    client = demo_client()
    refs = [
        # installed + (likely) configured — no finding expected
        ConnectorRef(
            connector=first_name, operation=None, version=None, config=None, workflow="probe_pb", step="installed"
        ),
        # not installed — not-installed finding
        ConnectorRef(
            connector="this-connector-does-not-exist",
            operation=None,
            version=None,
            config=None,
            workflow="probe_pb",
            step="missing",
        ),
        # installed + dead config pinned — config-missing finding
        ConnectorRef(
            connector=first_name,
            operation=None,
            version=None,
            config="deadbeef-0000-0000-0000-000000000000",
            workflow="probe_pb",
            step="pins_dead_config",
        ),
    ]

    findings = check_connector_configs(client, refs)
    by_code = {f.code: f for f in findings}
    assert "not-installed" in by_code, "expected not-installed finding for the missing connector"
    assert "config-missing" in by_code, "expected config-missing finding for the dead config pin"

    # LintFinding round-trips cleanly.
    for f in findings:
        rt = LintFinding.model_validate(f.model_dump())
        assert rt == f
        assert rt.severity == "warn"  # lint never blocks a deploy
        assert rt.connector and rt.code and rt.message and rt.fix_hint

    # ConnectorRef frozen-ness preserved from the pre-pydantic frozen dataclass.
    with pytest.raises(Exception):
        refs[0].connector = "mutated"
    # LintFinding frozen-ness preserved.
    with pytest.raises(Exception):
        findings[0].severity = "error"


# -- LibraryEntry ---------------------------------------------------------- #


def test_library_entry_round_trips_and_manifest_shape_contract():
    """list_library over the in-repo library + manifest shape contract.

    Pass 2 simplified library_manifest() from a hand-rolled per-field dict to
    a model_dump() subset that drops ``summary``. This locks the output shape:
    no ``summary`` key leaks, and the 11 keys the manifest has always exposed
    are all present. A future field added to LibraryEntry will surface here
    before it leaks into the manifest.
    """
    entries = list_library()
    if not entries:
        pytest.skip("no library present (examples/playbooks/library/ missing)")
    e0 = entries[0]

    # Round-trip
    rt = LibraryEntry.model_validate(e0.model_dump())
    assert rt == e0

    # Manifest shape contract: 11 keys, no `summary`.
    manifest = library_manifest()
    pb0 = manifest["playbooks"][0]
    assert "summary" not in pb0, f"manifest leaked `summary` key (subset filter regressed): {sorted(pb0.keys())}"
    expected_keys = {
        "slug",
        "stage",
        "path",
        "name",
        "goal",
        "step_types",
        "connectors",
        "jinja_filters",
        "triggers",
        "compiles_ok",
        "source",
    }
    assert set(pb0.keys()) == expected_keys, (
        f"manifest shape drift: extra={set(pb0.keys()) - expected_keys}, missing={expected_keys - set(pb0.keys())}"
    )
