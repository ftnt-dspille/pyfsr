"""Bulk threat-intel / record ingestion (``/api/ingest-feeds`` + ``/api/insert-feeds``).

These endpoints bulk-insert records **without firing on-create playbook triggers** —
intentional for high-volume feed ingestion (the regular ``/api/3/<module>`` create
*does* fire triggers). Accessed as ``client.feeds``.

Example:
    >>> client.feeds.indicators([{"value": "8.8.8.8", "typeofindicator": "IP Address"}])
    >>> client.feeds.stix_bundle(bundle)          # a STIX 2.x bundle dict
    >>> client.feeds.insert("events", [{...}])    # any record type
"""

from __future__ import annotations

from typing import Any

from .base import BaseAPI


class IngestFeedsAPI(BaseAPI):
    """Trigger-bypassing bulk ingest for threat-intel and arbitrary record types."""

    def indicators(self, records: list[dict[str, Any]]) -> Any:
        """Bulk-insert TI indicators (``POST /api/ingest-feeds/indicators``).

        Field names follow the ``indicators`` module schema. Bypasses on-create
        playbook triggers (unlike ``POST /api/3/insert/indicators``).
        """
        return self.client.post("/api/ingest-feeds/indicators", data=records)

    def observables(self, records: list[dict[str, Any]]) -> Any:
        """Bulk-insert observables (``POST /api/ingest-feeds/observables``).

        Field names follow the ``observables`` module schema. Same trigger-bypass
        behavior as :meth:`indicators`.
        """
        return self.client.post("/api/ingest-feeds/observables", data=records)

    def reputation(self, records: list[dict[str, Any]]) -> Any:
        """Bulk-upsert reputation scores (``POST /api/ingest-feeds/reputation``).

        For enrichment pipelines writing scored IOCs back without firing triggers.
        """
        return self.client.post("/api/ingest-feeds/reputation", data=records)

    def threatintel(self, records: list[dict[str, Any]]) -> Any:
        """Bulk-insert ``threat_intel`` records (``POST /api/ingest-feeds/threatintel``).

        The top-level container linking indicators, campaigns, threat actors, and
        reports. Field names match the ``threat_intel`` module schema
        (``GET /api/3/contexts/ThreatIntel``).
        """
        return self.client.post("/api/ingest-feeds/threatintel", data=records)

    def stix_bundle(self, bundle: dict[str, Any]) -> Any:
        """Ingest a STIX 2.x bundle (``POST /api/ingest-feeds/stix-bundle``).

        Fans the bundle out into FortiSOAR's threat-intel record types
        (indicators, threat actors, campaigns, malware, attack patterns, …) by
        each object's STIX ``type``. Bypasses on-create playbook triggers.
        """
        return self.client.post("/api/ingest-feeds/stix-bundle", data=bundle)

    def insert(self, record_type: str, records: list[dict[str, Any]]) -> Any:
        """Generic trigger-bypassing bulk insert for any record type.

        ``POST /api/insert-feeds/{record_type}`` — the generalization of
        :meth:`indicators` for an arbitrary ``record_type`` (the module's plural
        api name, e.g. ``"events"``, ``"alerts"``). Same trigger-skipping behavior.
        """
        if not isinstance(record_type, str) or not record_type.strip():
            raise ValueError("insert() requires a non-empty record_type")
        return self.client.post(f"/api/insert-feeds/{record_type.strip('/ ')}", data=records)
