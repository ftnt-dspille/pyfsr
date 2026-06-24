"""Catalog freshness — the Phase 9 Level-1 cheap probe.

The ``fsr_playbooks`` compiler runs against a cached reference catalog warmed
from one live SOAR. That cache drifts as the SOAR changes. This module runs the
**Level-1** check: a handful of cheap GETs (~1.5 KB total) that catch every
publish-driven and add/delete drift, then compares them against the provenance
stamped in the catalog's ``_catalog_meta`` table.

Signals (measured live; see the Phase 9 plan):

- ``GET /api/version`` — FSR build (Tier-0: changes only on an upgrade). Public.
- ``GET /api/publish/error`` -> ``last_publish_time`` — appliance-wide publish
  watermark (Tier-1: any module/field/connector publish bumps it).
- ``GET /api/3/<coll>?$limit=0`` -> ``hydra:totalItems`` — per-collection row
  count (catches add/delete drift, incl. picklist value adds with no publish).

Counts MISS in-place edits (rename a value, rebind a field's picklist) that keep
the row count constant — those need a Level-2 ETag re-pull, out of scope here.

The comparison logic (:func:`compare`) takes plain dicts so it is unit-testable
without a live SOAR.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .models import ApiResult

#: Collections we cheap-count, mapped to the catalog table whose drift they
#: signal. ``model_metadatas`` == published modules (equals staging when there's
#: no pending publish).
COUNT_COLLECTIONS: dict[str, str] = {
    "model_metadatas": "modules",
    "attribute_metadatas": "module_fields",
    "picklists": "picklists",
    "picklist_names": "picklists",
    "tags": "tags",
}


class FreshnessProbe(ApiResult):
    """Result of the Level-1 cheap probe against a live SOAR (:func:`probe_live`).

    Dict-compatible (``probe["version"]`` / ``probe.version``). A failed signal is
    recorded as ``None`` rather than aborting the probe.
    """

    version: str | None = None
    last_publish_time: Any | None = None
    counts: dict[str, int | None] = Field(default_factory=dict)


class FreshnessReport(ApiResult):
    """Outcome of comparing stamped provenance against a live probe.

    Dict-compatible (``report["drift"]`` / ``report.drift``)."""

    instance_label: str = ""
    stored: dict[str, Any] = Field(default_factory=dict)
    live: dict[str, Any] = Field(default_factory=dict)
    #: Human-readable drift lines (empty ⇒ fresh).
    drift: list[str] = Field(default_factory=list)
    #: True when the catalog carries no provenance stamp (never warmed).
    unstamped: bool = False

    @property
    def is_fresh(self) -> bool:
        return not self.unstamped and not self.drift


def _total_items(resp: object) -> int | None:
    if isinstance(resp, dict):
        v = resp.get("hydra:totalItems")
        if isinstance(v, int):
            return v
    return None


def probe_live(client) -> FreshnessProbe:
    """Run the Level-1 cheap probe against a live client. Best-effort: a failed
    signal is recorded as ``None`` rather than aborting the whole probe."""
    out = FreshnessProbe()
    try:
        v = client.get("/api/version")
        if isinstance(v, dict):
            out.version = v.get("version")
    except Exception:  # noqa: BLE001
        pass
    try:
        p = client.get("/api/publish/error")
        if isinstance(p, dict):
            out.last_publish_time = p.get("last_publish_time")
    except Exception:  # noqa: BLE001
        pass
    for coll in COUNT_COLLECTIONS:
        try:
            r = client.get(f"/api/3/{coll}?$limit=0")
            out.counts[coll] = _total_items(r)
        except Exception:  # noqa: BLE001
            out.counts[coll] = None
    return out


def compare(stored: dict, live: dict | FreshnessProbe) -> FreshnessReport:
    """Diff stamped provenance (``_catalog_meta`` key/value dict) against a live
    probe result. Returns a :class:`FreshnessReport`."""
    rep = FreshnessReport(
        instance_label=stored.get("instance_label", "") or "",
        stored=stored,
        live=live,
    )
    if not stored.get("base_url_hash"):
        rep.unstamped = True
        return rep

    sv = stored.get("fsr_version")
    lv = live.get("version")
    if sv and lv and sv != lv:
        rep.drift.append(f"FSR upgraded: catalog {sv} -> live {lv} (Tier-0 rebuild)")

    sp = stored.get("last_publish_time")
    lp = live.get("last_publish_time")
    if sp is not None and lp is not None and str(sp) != str(lp):
        rep.drift.append(f"publish watermark advanced: {sp} -> {lp} (structural drift — re-warm Tier-1)")

    for coll, n in (live.get("counts") or {}).items():
        s = stored.get(f"count:{coll}")
        if s is None or n is None:
            continue  # no baseline recorded, or probe failed — can't compare
        if str(s) != str(n):
            delta = n - int(s) if str(s).lstrip("-").isdigit() else "?"
            sign = f"{'+' if isinstance(delta, int) and delta >= 0 else ''}{delta}"
            rep.drift.append(f"{coll}: {s} -> {n} ({sign})")
    return rep
