"""pyfsr.authoring.data_ingestion_template delegates to fsr_playbooks.ingestion_template."""

from __future__ import annotations

import pytest
import yaml

pytest.importorskip("fsr_playbooks.ingestion_template")

from pyfsr.authoring import CompiledPlaybook, data_ingestion_template  # noqa: E402


def test_returns_yaml_with_rest_standin():
    text = data_ingestion_template("acme-edr", "Acme EDR", "https://edr.example.com/x", records_path="items")
    doc = yaml.safe_load(text)
    fetch = {s["name"]: s for s in doc["playbooks"][1]["steps"]}["Fetch"]
    assert (fetch["connector"], fetch["operation"]) == ("cyops_utilities", "api_call")
    assert fetch["params"]["url"] == "https://edr.example.com/x"


def test_compile_returns_compiled_playbook():
    res = data_ingestion_template("acme-edr", "Acme EDR", "https://edr.example.com/x", compile=True)
    assert isinstance(res, CompiledPlaybook)
    assert res.ok, res.errors


def test_bad_spec_raises():
    with pytest.raises(ValueError):
        data_ingestion_template("Bad Slug", "X", "https://x")


def test_nvd_example_runs_offline(tmp_path):
    import runpy
    import sys
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "examples" / "nvd_data_ingestion_template.py"
    out = tmp_path / "nvd.yaml"
    argv = sys.argv
    sys.argv = [str(example), "--out", str(out)]
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(example), run_name="__main__")
    finally:
        sys.argv = argv
    assert exc.value.code == 0
    assert "services.nvd.nist.gov" in out.read_text()
