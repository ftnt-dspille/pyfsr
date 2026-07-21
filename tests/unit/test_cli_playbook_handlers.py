"""Unit tests for the ``pyfsr playbook`` CLI command handlers.

Mocks the compiler, library, and step-catalog layers so the handlers'
argument wiring, exit codes, and output branches are exercised offline.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

from pyfsr.cli import playbook as pb


def _args(**kw) -> argparse.Namespace:
    base = dict(
        file="x.yaml",
        refresh_catalog=False,
        server=None,
        token=None,
        username=None,
        password=None,
        port=None,
        no_verify_ssl=False,
        out=None,
        replace=False,
        dry_run=False,
        check_connectors=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _result(ok=True, **kw):
    base = dict(
        ok=ok,
        errors=[],
        fsr_json={
            "type": "workflow_collections",
            "data": [{"name": "Pack", "workflows": [{"name": "wf1"}, {"name": "wf2"}]}],
        },
        collection_names=["Pack"],
        playbook_names=["wf1", "wf2"],
        blocking=[],
        warnings=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


# -- cmd_compile -------------------------------------------------------------
def test_cmd_compile_to_stdout(capfd):
    with patch.object(pb, "_compile", return_value=_result()):
        assert pb.cmd_compile(_args()) == 0
    assert '"type"' in capfd.readouterr().out


def test_cmd_compile_to_file(tmp_path):
    out = tmp_path / "compiled.json"
    with patch.object(pb, "_compile", return_value=_result()):
        assert pb.cmd_compile(_args(out=str(out))) == 0
    assert out.exists() and '"workflow_collections"' in out.read_text()


def test_cmd_compile_failure_returns_1(capfd):
    with patch.object(pb, "_compile", return_value=_result(ok=False)):
        assert pb.cmd_compile(_args()) == 1
    assert "compilation failed" in capfd.readouterr().err


# -- cmd_validate ------------------------------------------------------------
def test_cmd_validate_ok():
    with patch.object(pb, "_compile", return_value=_result()):
        assert pb.cmd_validate(_args()) == 0


def test_cmd_validate_failed_returns_1():
    with patch.object(pb, "_compile", return_value=_result(ok=False, blocking=[{"m": 1}])):
        assert pb.cmd_validate(_args()) == 1


def test_cmd_validate_check_connectors_runs_preflight():
    with (
        patch.object(pb, "_compile", return_value=_result()),
        patch.object(pb, "_make_client") as mk,
        patch.object(pb, "_connector_findings", return_value=[]) as cf,
        patch.object(pb, "_print_findings") as pf,
    ):
        assert pb.cmd_validate(_args(check_connectors=True)) == 0
        cf.assert_called_once()
        pf.assert_called_once()
        mk.assert_called_once()


# -- cmd_deploy --------------------------------------------------------------
def test_cmd_deploy_dry_run_posts_nothing(capfd):
    with patch.object(pb, "_compile", return_value=_result()):
        assert pb.cmd_deploy(_args(dry_run=True)) == 0
    out = capfd.readouterr()
    assert "dry-run" in out.err


def test_cmd_deploy_posts_and_reports(capfd):
    client = SimpleNamespace(
        workflow_collections=SimpleNamespace(import_export=lambda fsr_json, replace: [{"name": "Pack", "uuid": "u1"}])
    )
    with (
        patch.object(pb, "_make_client", return_value=client),
        patch.object(pb, "_compile", return_value=_result()),
    ):
        assert pb.cmd_deploy(_args(dry_run=False)) == 0


def test_cmd_deploy_compile_failure_returns_1(capfd):
    with (
        patch.object(pb, "_make_client", return_value=SimpleNamespace()),
        patch.object(pb, "_compile", return_value=_result(ok=False)),
    ):
        assert pb.cmd_deploy(_args(dry_run=False)) == 1


# -- cmd_lint ----------------------------------------------------------------
def test_cmd_lint_clean_returns_0():
    with (
        patch.object(pb, "_compile", return_value=_result()),
        patch.object(pb, "_make_client"),
        patch.object(pb, "_connector_findings", return_value=[]),
    ):
        assert pb.cmd_lint(_args()) == 0


def test_cmd_lint_with_findings_returns_2():
    finding = SimpleNamespace(connector="c", code="NO_CONFIG", message="m", fix_hint="fix")
    with (
        patch.object(pb, "_compile", return_value=_result()),
        patch.object(pb, "_make_client"),
        patch.object(pb, "_connector_findings", return_value=[finding]),
    ):
        assert pb.cmd_lint(_args()) == 2


def test_cmd_lint_compile_failure_returns_1():
    with patch.object(pb, "_compile", return_value=_result(ok=False)):
        assert pb.cmd_lint(_args()) == 1


# -- cmd_steps ---------------------------------------------------------------
def test_cmd_steps_lists_types(capfd):
    infos = [SimpleNamespace(short="set_var", canonical="SetVariable", modeled=True, purpose="set a var")]
    with patch("pyfsr.playbook_catalog.list_step_types", return_value=infos):
        assert pb.cmd_steps(_args()) == 0


# -- cmd_step_help -----------------------------------------------------------
def _help(**kw):
    base = dict(
        short="set_var",
        canonical="SetVariable",
        label="Set Variable",
        purpose="p",
        modeled=True,
        pitfalls="watch out",
        arg_schema={"type": "object"},
        example_yaml="type: set_var\n",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_step_help_full(capfd):
    with patch("pyfsr.playbook_catalog.step_help", return_value=_help()):
        assert pb.cmd_step_help(_args(type="set_var", schema=True)) == 0
    out = capfd.readouterr().out
    assert "example" in out and "arguments JSON schema" in out


def test_cmd_step_help_no_example(capfd):
    with patch("pyfsr.playbook_catalog.step_help", return_value=_help(example_yaml=None)):
        assert pb.cmd_step_help(_args(type="set_var", schema=False)) == 0
    assert "no bundled example" in capfd.readouterr().err


def test_cmd_step_help_unknown_type(capfd):
    with patch("pyfsr.playbook_catalog.step_help", side_effect=KeyError("no such type")):
        assert pb.cmd_step_help(_args(type="bogus", schema=False)) == 1
    assert "error:" in capfd.readouterr().err


# -- cmd_examples ------------------------------------------------------------
def _entry(**kw):
    base = dict(
        stage="triage",
        slug="s1",
        name="Ex",
        goal="do a thing",
        step_types=["set_var"],
        compiles_ok=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_examples_manifest(capfd):
    with patch("pyfsr.playbook_library.library_manifest", return_value={"items": []}):
        assert pb.cmd_examples(_args(manifest=True)) == 0
    assert '"items"' in capfd.readouterr().out


def test_cmd_examples_table(capfd):
    with (
        patch("pyfsr.playbook_library.library_manifest", return_value={}),
        patch("pyfsr.playbook_library.list_library", return_value=[_entry()]),
    ):
        assert pb.cmd_examples(_args(manifest=False, stage=None, intent=None)) == 0


def test_cmd_examples_empty_returns_1(capfd):
    with (
        patch("pyfsr.playbook_library.library_manifest", return_value={}),
        patch("pyfsr.playbook_library.list_library", return_value=[]),
    ):
        assert pb.cmd_examples(_args(manifest=False, stage=None, intent=None)) == 1
    assert "no library found" in capfd.readouterr().err


def test_cmd_examples_filters_stage_and_intent():
    entries = [_entry(slug="a", stage="triage", goal="phish"), _entry(slug="b", stage="respond", goal="malware")]
    with (
        patch("pyfsr.playbook_library.library_manifest", return_value={}),
        patch("pyfsr.playbook_library.list_library", return_value=entries),
    ):
        # stage filter narrows to triage; intent filter to "phish"
        assert pb.cmd_examples(_args(manifest=False, stage="triage", intent="phish")) == 0


# -- cmd_show ----------------------------------------------------------------
def test_cmd_show_not_found_returns_1(capfd):
    with patch("pyfsr.playbook_library.library_show", return_value=None):
        assert pb.cmd_show(_args(slug="ghost")) == 1
    assert "no library playbook" in capfd.readouterr().err


def test_cmd_show_prints_entry(capfd, tmp_path):
    pb_file = tmp_path / "pb.yaml"
    pb_file.write_text("name: demo\n")
    entry = SimpleNamespace(
        slug="s1",
        stage="triage",
        name="Ex",
        goal="g",
        source="src",
        path="pb.yaml",
        step_types=["set_var"],
        connectors=[],
        jinja_filters=[],
        triggers=[],
        compiles_ok=True,
    )
    fake_default = SimpleNamespace(parents=[None, None, tmp_path])
    with (
        patch("pyfsr.playbook_library.library_show", return_value=entry),
        patch("pyfsr.playbook_library._LIBRARY_DEFAULT", fake_default),
    ):
        assert pb.cmd_show(_args(slug="s1")) == 0
    assert "name: demo" in capfd.readouterr().out


# -- _read -------------------------------------------------------------------
def test_read_returns_file_text(tmp_path):
    f = tmp_path / "pb.yaml"
    f.write_text("name: x\n")
    assert pb._read(str(f)) == "name: x\n"


# -- _make_client ------------------------------------------------------------
def test_make_client_full_flags_skips_env():
    with patch("pyfsr.client.FortiSOAR") as FSR:
        pb._make_client(_args(server="https://h:443", token="tok", no_verify_ssl=True))
        FSR.assert_called_once()
        kw = FSR.call_args.kwargs
        assert kw["base_url"] == "https://h:443"
        assert kw["auth"] == "tok"
        assert kw["verify_ssl"] is False


def test_make_client_userpass_falls_back_to_env():
    with patch("pyfsr.config.EnvConfig") as Env:
        Env.from_env.return_value.client.return_value = "CLIENT"
        out = pb._make_client(_args(username="u", password="p", port=13000))
        assert out == "CLIENT"
        # port override threaded through to client()
        assert Env.from_env.return_value.client.call_args.kwargs["port"] == 13000


# -- _workflows_of -----------------------------------------------------------
def test_workflows_of_returns_names():
    assert pb._workflows_of(_result(), "Pack") == ["wf1", "wf2"]


def test_workflows_of_unknown_collection_empty():
    assert pb._workflows_of(_result(), "Nope") == []


# -- cmd_deploy check_connectors ---------------------------------------------
def test_cmd_deploy_check_connectors_preflight_runs():
    client = SimpleNamespace(workflow_collections=SimpleNamespace(import_export=lambda fsr_json, replace: []))
    with (
        patch.object(pb, "_make_client", return_value=client),
        patch.object(pb, "_compile", return_value=_result()),
        patch.object(pb, "_connector_findings", return_value=[]) as cf,
        patch.object(pb, "_print_findings"),
    ):
        assert pb.cmd_deploy(_args(dry_run=False, check_connectors=True)) == 0
        cf.assert_called_once()
