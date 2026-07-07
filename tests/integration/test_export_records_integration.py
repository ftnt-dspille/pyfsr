"""Live integration test for filtered record-data export (opt-in: ``pytest -m integration``).

Proves the record-set export path end-to-end against a live appliance: build a
throwaway template with a filtered record set, run the export, download + unzip
the archive, and confirm the emitted records match the filter. This is the test
that pins the live-discovered contract — a record set only emits records when its
query carries a ``limit`` (the export trigger), and the filter is honored.

Uses the ``people`` module: small, always present, and (unlike the UI wizard,
which hides record-set export for ``people``) fully exportable via the REST
engine. Read-only apart from the throwaway template, which is cleaned up.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from pyfsr import Query


def _firstnames_in_zip(path: str) -> list[str]:
    """Return the ``firstname`` of every people record inside an export ``.zip``."""
    names: list[str] = []
    with zipfile.ZipFile(path) as z:
        for entry in z.namelist():
            if "/records/people" in entry and entry.endswith(".json"):
                for rec in json.loads(z.read(entry)):
                    names.append(rec.get("firstname"))
    return names


@pytest.mark.integration
def test_export_record_data_filters_live(client, tmp_path):
    # baseline: read the people the records API returns, pick a value to filter on
    people = list(client.records("people").list(limit=50))
    if len(people) < 1:
        pytest.skip("no people records on this box")
    target = people[0]["firstname"]
    expected = [p["firstname"] for p in people if p["firstname"] == target]

    # filtered export -> only records matching firstname == target
    filtered_zip = str(tmp_path / "people_filtered.zip")
    client.export_config.export_record_data(
        "people",
        query=Query(module="people").eq("firstname", target),
        output_path=filtered_zip,
    )
    got = _firstnames_in_zip(filtered_zip)
    assert got, "filtered export emitted no records (limit trigger regression?)"
    assert set(got) == {target}, f"filter not honored: {got}"
    assert len(got) == len(expected)

    # unfiltered export -> at least as many records as the filtered subset
    all_zip = str(tmp_path / "people_all.zip")
    client.export_config.export_record_data("people", limit=1000, output_path=all_zip)
    all_names = _firstnames_in_zip(all_zip)
    assert len(all_names) >= len(got)

    # the throwaway templates were cleaned up
    tmpls = client.get("/api/3/export_templates", params={"$limit": 200}).get("hydra:member", [])
    assert not [t for t in tmpls if t.get("name", "").startswith("pyfsr_records_")]


@pytest.mark.integration
def test_export_record_data_without_limit_would_be_empty(client, tmp_path):
    """A record set whose query has no ``limit`` emits nothing — the SDK always
    injects a ``limit`` so this stays a *documented* engine quirk, not a footgun.

    Verifies the SDK's default limit is present (records come out); the negative
    case (no limit -> empty) is the reason the default exists.
    """
    out = str(tmp_path / "people_default.zip")
    client.export_config.export_record_data("people", output_path=out)
    assert _firstnames_in_zip(out), "default limit should make records export"
