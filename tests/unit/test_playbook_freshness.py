"""Phase 9 Level-1 freshness comparison (``pyfsr.playbook_freshness``).

The comparison logic takes plain dicts so it runs without a live SOAR. A tiny
fake client exercises :func:`probe_live` end-to-end.
"""

from __future__ import annotations

from pyfsr.playbook_freshness import COUNT_COLLECTIONS, compare, probe_live


def _stamped(**over):
    base = {
        "base_url_hash": "abc123",
        "instance_label": "dev",
        "fsr_version": "7.6.5-622",
        "last_publish_time": "1700000000",
        "count:model_metadatas": "43",
        "count:picklists": "697",
    }
    base.update(over)
    return base


def test_unstamped_catalog_reported():
    rep = compare({}, {"version": "7.6.5", "counts": {}})
    assert rep.unstamped is True
    assert rep.is_fresh is False


def test_fresh_when_everything_matches():
    live = {
        "version": "7.6.5-622",
        "last_publish_time": "1700000000",
        "counts": {"model_metadatas": 43, "picklists": 697},
    }
    rep = compare(_stamped(), live)
    assert rep.is_fresh is True
    assert rep.drift == []


def test_publish_watermark_drift():
    live = {
        "version": "7.6.5-622",
        "last_publish_time": "1700009999",
        "counts": {},
    }
    rep = compare(_stamped(), live)
    assert not rep.is_fresh
    assert any("publish watermark" in d for d in rep.drift)


def test_version_upgrade_drift():
    live = {"version": "7.7.0-1", "last_publish_time": "1700000000", "counts": {}}
    rep = compare(_stamped(), live)
    assert any("upgraded" in d for d in rep.drift)


def test_count_delta_drift_with_sign():
    live = {
        "version": "7.6.5-622",
        "last_publish_time": "1700000000",
        "counts": {"picklists": 700},
    }
    rep = compare(_stamped(), live)
    assert any("picklists: 697 -> 700 (+3)" in d for d in rep.drift)


def test_missing_baseline_count_is_ignored():
    # No count:tags stored — live tags count can't be compared, no false drift.
    live = {
        "version": "7.6.5-622",
        "last_publish_time": "1700000000",
        "counts": {"tags": 14657},
    }
    rep = compare(_stamped(), live)
    assert rep.is_fresh is True


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses

    def get(self, path):
        for key, val in self._responses.items():
            if path.startswith(key):
                if isinstance(val, Exception):
                    raise val
                return val
        raise KeyError(path)


def test_probe_live_collects_all_signals():
    responses = {
        "/api/version": {"version": "7.6.5-622"},
        "/api/publish/error": {"last_publish_time": 1700000000},
    }
    for coll in COUNT_COLLECTIONS:
        responses[f"/api/3/{coll}"] = {"hydra:totalItems": 5}
    out = probe_live(_FakeClient(responses))
    assert out["version"] == "7.6.5-622"
    assert out["last_publish_time"] == 1700000000
    assert all(out["counts"][c] == 5 for c in COUNT_COLLECTIONS)


def test_probe_live_tolerates_failures():
    client = _FakeClient({"/api/version": RuntimeError("boom")})
    out = probe_live(client)
    assert out["version"] is None
    # counts all fail → recorded as None, not crash
    assert set(out["counts"]) == set(COUNT_COLLECTIONS)
