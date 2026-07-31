"""Typed models for TAXII 2.1 server responses and STIX bundle ingest.

FortiSOAR ships a native TAXII 2.1 server (``/api/taxii/1``) that serves
outgoing threat-intel feeds. These models wrap the server's response envelopes
so callers get typed fields while unknown keys are preserved (``extra="allow"``)
and dict-style access (``.get()``, ``obj["key"]``) still works.

Note that FortiSOAR's object endpoints return a non-standard
``{totalItems, objects: []}`` envelope instead of the standard TAXII 2.1
``more`` / ``next`` cursor -- paginate with ``limit`` + ``added_after``.

The STIX bundle ingest endpoint (``POST /api/ingest-feeds/stix-bundle``)
returns a distinct shape from the other feed endpoints: it carries
``message`` and ``objects_processed`` instead of ``uuids``, so it gets its own
:class:`StixBundleResult` rather than reusing :class:`FeedIngestResult`.

Shapes are live-verified on 8.0.0.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from ._stix import StixObject, _Lenient, parse_stix_object


class TaxiiDiscovery(_Lenient):
    """TAXII discovery response (``GET /api/taxii/1/``).

    The server descriptor clients call first to confirm protocol compatibility.

    Attributes:
        title: server title (e.g. ``"FortiSOAR TAXII Server"``).
        description: server description.
        default: default collection URL (``/api/taxii/1/collections/``).
        versions: supported TAXII versions.
        max_content_length: max content length the server accepts (bytes).
    """

    title: str | None = None
    description: str | None = None
    default: str | None = None
    versions: list[str] = Field(default_factory=list)
    max_content_length: int | None = None


class TaxiiCollection(_Lenient):
    """One TAXII collection entry.

    Served both in the collection list and as a single-collection response.

    Attributes:
        id: collection identifier (maps to a :class:`~pyfsr.models.SystemQuery`
            uuid on FortiSOAR -- a dataset *is* a TAXII collection).
        title: human-readable title.
        description: longer description.
        can_read: caller may read objects from this collection.
        can_write: caller may add objects to this collection (typically
            ``False`` on FortiSOAR -- publishing is via ``system_queries``).
        media_types: accepted media types (``["application/stix+json;version=2.1"]``).
    """

    id: str | None = None
    title: str | None = None
    description: str | None = None
    can_read: bool | None = None
    can_write: bool | None = None
    media_types: list[str] = Field(default_factory=list)


class TaxiiManifestEntry(_Lenient):
    """One entry in a collection manifest -- metadata only, no object body.

    Attributes:
        id: STIX object id (``"malware--<uuid>"``).
        date_added: when the object was added to the collection (ISO 8601).
        version: object version timestamp (ISO 8601).
        media_type: media type (``"application/stix+json;version=2.1"``).
    """

    id: str | None = None
    date_added: str | None = None
    version: str | None = None
    media_type: str | None = None


class TaxiiManifest(_Lenient):
    """Collection manifest response (``GET .../collections/{id}/manifest``).

    One entry per object, no bodies. Cheap "what's new since X" poll.

    Attributes:
        objects: manifest entries (metadata for each object in the collection).
        total_items: total count (FortiSOAR-specific, may be absent).
    """

    objects: list[TaxiiManifestEntry] = Field(default_factory=list)
    total_items: int | None = Field(default=None, alias="totalItems")


class TaxiiObjectsEnvelope(_Lenient):
    """STIX objects envelope from a TAXII collection.

    FortiSOAR's non-standard ``{totalItems, objects: []}`` wrapper (no TAXII
    2.1 ``more`` / ``next`` cursor). Paginate with ``limit`` + ``added_after``.

    Attributes:
        total_items: total object count in the collection.
        objects: STIX objects, parsed into typed subclasses when possible.
    """

    total_items: int | None = Field(default=None, alias="totalItems")
    objects: list[StixObject] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _parse_objects(cls, data: Any) -> Any:
        if isinstance(data, dict):
            objs = data.get("objects")
            if isinstance(objs, list):
                data["objects"] = [parse_stix_object(o) if isinstance(o, dict) else o for o in objs]
        return data


class StixBundleResult(_Lenient):
    """Response of ``POST /api/ingest-feeds/stix-bundle``.

    Distinct from :class:`~pyfsr.api.feeds.FeedIngestResult`: the STIX bundle
    endpoint returns ``message`` and ``objects_processed`` instead of a
    ``uuids`` list, because the bundle fans out into multiple record types.

    Attributes:
        status: ``"success"`` or an error indicator.
        message: human-readable status message.
        objects_processed: number of STIX objects ingested from the bundle.
    """

    status: str | None = None
    message: str | None = None
    objects_processed: int | None = None

    @property
    def ok(self) -> bool:
        """``True`` iff the server reported ``status == "success"``."""
        return self.status == "success"


__all__ = [
    "TaxiiDiscovery",
    "TaxiiCollection",
    "TaxiiManifestEntry",
    "TaxiiManifest",
    "TaxiiObjectsEnvelope",
    "StixBundleResult",
]
