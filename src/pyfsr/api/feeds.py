"""Bulk threat-intel / record ingestion (``/api/ingest-feeds`` + ``/api/insert-feeds``).

These endpoints bulk-insert records **without firing on-create playbook triggers** —
intentional for high-volume feed ingestion (the regular ``/api/3/<module>`` create
*does* fire triggers). Existing records are internally upserted based on the
module's unique-constraint criterion (only changed fields are touched — unlike
``bulkupsert``, this does not overwrite the whole row). Accessed as ``client.feeds``.

Example:
    >>> client = demo_client()
    >>> result = client.feeds.indicators([{"value": "8.8.8.8", "typeofindicator": "IP Address"}])
    >>> result.ok
    True
    >>> len(result.uuids)
    2
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..models.base import BaseRecord
from .base import BaseAPI


class FeedIngestResult(BaseModel):
    """Response of a bulk feed-ingest call (``{"status": "success", "uuids": [...]}``).

    Live-verified shape: a flat ``status`` string plus the ``uuids`` of every
    row that was created-or-updated, in request order — there is no per-row
    success/failure split like :meth:`~pyfsr.records.RecordSet.bulk_upsert`
    gets; a row-level failure fails the whole call.
    """

    model_config = {"extra": "allow"}

    status: str
    uuids: list[str] = []

    @property
    def ok(self) -> bool:
        """``True`` iff the server reported ``status == "success"``."""
        return self.status == "success"


def _to_rows(records: list[dict[str, Any] | BaseRecord]) -> list[dict[str, Any]]:
    return [r.to_dict(exclude_none=True) if isinstance(r, BaseRecord) else r for r in records]


class IngestFeedsAPI(BaseAPI):
    """Trigger-bypassing bulk ingest for threat-intel and arbitrary record types."""

    def indicators(self, records: list[dict[str, Any] | BaseRecord]) -> FeedIngestResult:
        """Bulk-insert TI indicators (``POST /api/ingest-feeds/indicators``).

        Field names follow the ``indicators`` module schema. Bypasses on-create
        playbook triggers (unlike ``POST /api/3/insert/indicators``).

        Example:
            >>> client = demo_client()
            >>> result = client.feeds.indicators([
            ...     {"value": "8.8.8.8", "typeofindicator": "IP Address"},
            ...     {"value": "1.1.1.1", "typeofindicator": "IP Address"},
            ... ])
            >>> result.ok
            True
            >>> result.status
            'success'
        """
        resp = self.client.post("/api/ingest-feeds/indicators", data={"data": _to_rows(records)})
        return FeedIngestResult.model_validate(resp)

    def observables(self, records: list[dict[str, Any] | BaseRecord]) -> FeedIngestResult:
        """Bulk-insert observables (``POST /api/ingest-feeds/observables``).

        Field names follow the ``observables`` module schema. Same trigger-bypass
        behavior as :meth:`indicators`.

        Example:
            >>> client = demo_client()
            >>> result = client.feeds.observables([
            ...     {"name": "test-hash", "type": "File", "value": "abc123def456"}
            ... ])
            >>> result.ok
            True
        """
        resp = self.client.post("/api/ingest-feeds/observables", data={"data": _to_rows(records)})
        return FeedIngestResult.model_validate(resp)

    def reputation(self, records: list[dict[str, Any] | BaseRecord]) -> FeedIngestResult:
        """Bulk-upsert reputation scores (``POST /api/ingest-feeds/reputation``).

        For enrichment pipelines writing scored IOCs back without firing triggers.

        Example:
            >>> client = demo_client()
            >>> result = client.feeds.reputation([
            ...     {"value": "8.8.8.8", "score": 75},
            ...     {"value": "1.1.1.1", "score": 10},
            ... ])
            >>> result.ok
            True
        """
        resp = self.client.post("/api/ingest-feeds/reputation", data={"data": _to_rows(records)})
        return FeedIngestResult.model_validate(resp)

    def threatintel(self, records: list[dict[str, Any] | BaseRecord]) -> FeedIngestResult:
        """Bulk-insert ``threat_intel`` records (``POST /api/ingest-feeds/threatintel``).

        The top-level container linking indicators, campaigns, threat actors, and
        reports. Field names match the ``threat_intel`` module schema
        (``GET /api/3/contexts/ThreatIntel``).

        Example:
            >>> client = demo_client()
            >>> result = client.feeds.threatintel([
            ...     {"name": "APT28", "type": "Threat Actor"}
            ... ])
            >>> result.ok
            True
        """
        resp = self.client.post("/api/ingest-feeds/threatintel", data={"data": _to_rows(records)})
        return FeedIngestResult.model_validate(resp)

    def stix_bundle(self, bundle: dict[str, Any]) -> Any:
        """Ingest a STIX 2.x bundle (``POST /api/ingest-feeds/stix-bundle``).

        Fans the bundle out into FortiSOAR's threat-intel record types
        (indicators, threat actors, campaigns, malware, attack patterns, …) by
        each object's STIX ``type``. Bypasses on-create playbook triggers. This
        endpoint takes the STIX bundle as-is (not the ``{"data": [...]}``
        row-list envelope the other feed endpoints use), and its response shape
        has not been live-verified, so it is returned unparsed.

        Example:
            >>> client = demo_client()
            >>> bundle = {
            ...     "type": "bundle",
            ...     "id": "bundle--00000000-0000-0000-0000-000000000000",
            ...     "objects": [
            ...         {"type": "malware", "id": "malware--00000000-0000-0000-0000-000000000001"}
            ...     ]
            ... }
            >>> result = client.feeds.stix_bundle(bundle)
            >>> result["status"]
            'success'
        """
        return self.client.post("/api/ingest-feeds/stix-bundle", data=bundle)

    def insert(self, record_type: str, records: list[dict[str, Any] | BaseRecord]) -> FeedIngestResult:
        """Generic trigger-bypassing bulk insert for any record type.

        ``POST /api/ingest-feeds/{record_type}`` — the generalization of
        :meth:`indicators` for an arbitrary ``record_type`` (the module's
        short/type name from ``ModelMetadata``, e.g. ``"alerts"``, ``"events"``).
        Same trigger-skipping behavior.

        Backed by ``TypeAgnosticResourceController::createSqlRecords`` (a raw
        ``INSERT ... ON CONFLICT (<module>_unique or uuid) DO UPDATE``), so it
        works for any module registered in ``ModelMetadata``, not just the
        dedicated threat-intel types. Live-verified on a real FortiSOAR
        8.0.0-6034 appliance with ``record_type="alerts"``.

        Note:
            ``/api/insert-feeds/{record_type}`` (no "g") is a different,
            similarly-named path that does **not** exist in that build's
            routing config — it 404s at the router level for every
            ``record_type``, not because of a permissions/module restriction.
            Do not confuse the two.

        Example:
            >>> client = demo_client()
            >>> result = client.feeds.insert("alerts", [
            ...     {"name": "Alert from Feed", "severity": "Medium"}
            ... ])
            >>> result.ok
            True
        """
        if not isinstance(record_type, str) or not record_type.strip():
            raise ValueError("insert() requires a non-empty record_type")
        resp = self.client.post(f"/api/ingest-feeds/{record_type.strip('/ ')}", data={"data": _to_rows(records)})
        return FeedIngestResult.model_validate(resp)
