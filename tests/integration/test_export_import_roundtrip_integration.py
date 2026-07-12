"""Live round-trip test for config export -> import (opt-in: ``pytest -m integration``).

The export path has live coverage (``test_export_records_integration``); the
*import* path had none. This closes that gap end to end: create a throwaway
record, export it to a ``.zip``, delete it, import the ``.zip`` back, and assert
the record lands again. It exercises the full import lifecycle
(``import_config.import_file``: upload -> job -> options -> trigger -> wait) against
a record bundle the SDK itself produced, proving the two halves compose.

Uses the ``alerts`` module: a plain record module that creates/deletes cheaply
(no user-account machinery). The only record it touches is one it creates and
removes, so the box is left as it was found.
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest

from pyfsr import Query


def _alerts_in_zip(path: str) -> list[dict]:
    """Return every alerts record inside an export ``.zip``."""
    recs: list[dict] = []
    with zipfile.ZipFile(path) as z:
        for entry in z.namelist():
            if "/records/alerts" in entry and entry.endswith(".json"):
                recs.extend(json.loads(z.read(entry)))
    return recs


@pytest.mark.integration
def test_export_import_record_roundtrip_live(client, tmp_path):
    alerts = client.records("alerts")

    # A name unique to this run so the export filter isolates our record and the
    # post-import lookup can't collide with a real alert.
    marker = f"pyfsr_rt_{os.getpid()}"
    created = alerts.create({"name": marker}, raw=True)
    uuid = created["uuid"]

    zip_path = str(tmp_path / "alert_roundtrip.zip")
    try:
        # 1. export just our record
        client.export_config.export_record_data(
            "alerts",
            query=Query(module="alerts").eq("name", marker),
            output_path=zip_path,
        )
        exported = _alerts_in_zip(zip_path)
        assert [r for r in exported if r.get("name") == marker], "export did not capture the throwaway record"

        # 2. remove it from the box (hard delete frees the uuid + unique name)
        alerts.delete(uuid, hard=True)
        assert not list(alerts.query(Query(module="alerts").eq("name", marker))), (
            "record still present after hard delete"
        )

        # 3. import the archive back -- a pure record bundle carries no schema
        #    changes, so the default (refuse-on-schema-change) path applies cleanly
        result = client.import_config.import_file(zip_path, wait=True)
        assert str(result.status or "").strip().lower() == "import complete", (
            f"import did not complete: status={result.status!r} error={result.errorMessage!r}"
        )

        # 4. the record is back
        restored = list(alerts.query(Query(module="alerts").eq("name", marker)))
        assert restored, "import did not restore the record"
    finally:
        # clean up whatever survived (post-import copy, or the original if we
        # failed before the delete)
        for rec in list(alerts.query(Query(module="alerts").eq("name", marker))):
            try:
                alerts.delete(rec["uuid"], hard=True)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        # if the original was never deleted (early failure), get it too
        try:
            alerts.delete(uuid, hard=True)
        except Exception:  # pragma: no cover
            pass
