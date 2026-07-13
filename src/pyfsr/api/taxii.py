"""TAXII 2.1 threat-intel sharing server (``/api/taxii/1``).

Read access to FortiSOAR's TAXII collections — the discovery descriptor, the
collection list, per-collection manifests, and the STIX objects themselves.
Accessed as ``client.taxii``.

Note the object endpoints return a FortiSOAR-specific ``{totalItems, objects:[]}``
envelope, **not** the standard TAXII 2.1 ``more``/``next`` cursor — paginate with
``limit`` + ``added_after``.

Example:
    >>> client = demo_client()
    >>> client.taxii.discovery()["title"]
    'FortiSOAR TAXII Server'
    >>> cols = client.taxii.collections()
    >>> cols[0]["id"]
    'malware-samples'
    >>> client.taxii.objects(cols[0]["id"], limit=50)["totalItems"]
    1
"""

from __future__ import annotations

from typing import Any

from .base import BaseAPI

_BASE = "/api/taxii/1"


class TaxiiAPI(BaseAPI):
    """Read FortiSOAR's TAXII 2.1 collections and STIX objects."""

    def discovery(self) -> dict[str, Any]:
        """Fetch the TAXII server descriptor (``GET /api/taxii/1/``).

        Returns title, supported TAXII versions, and the max content length the
        server accepts — clients call this first to confirm protocol compatibility.

        Example:
            >>> client = demo_client()
            >>> client.taxii.discovery()["max_content_length"]
            10485760
        """
        return self.client.get(f"{_BASE}/")

    def collections(self) -> list[dict[str, Any]]:
        """List TAXII collections the caller may see (``GET .../collections``).

        Each entry carries ``id``, ``can_read``/``can_write``, and accepted media
        types. Returns the ``collections`` array.

        Example:
            >>> client = demo_client()
            >>> cols = client.taxii.collections()
            >>> [c["id"] for c in cols]
            ['malware-samples', 'threat-actors']
            >>> cols[0]["can_read"]
            True
        """
        resp = self.client.get(f"{_BASE}/collections") or {}
        if isinstance(resp, dict):
            return resp.get("collections") or []
        return resp if isinstance(resp, list) else []

    def collection(self, uuid: str) -> dict[str, Any]:
        """Fetch one collection's metadata (``GET .../collections/{uuid}``).

        Example:
            >>> client = demo_client()
            >>> client.taxii.collection("malware-samples")["title"]
            'Malware Samples'
        """
        return self.client.get(f"{_BASE}/collections/{uuid}")

    def manifest(self, uuid: str, *, limit: int | None = None, added_after: str | None = None) -> dict[str, Any]:
        """Fetch a collection manifest — one entry per object, no bodies.

        ``GET .../collections/{uuid}/manifest``. Cheap "what's new since X" poll:
        each entry has id, date added, version, media type. ``added_after`` is an
        ISO timestamp; ``limit`` caps the page.

        Example:
            >>> client = demo_client()
            >>> manifest = client.taxii.manifest("malware-samples")
            >>> manifest["objects"][0]["media_type"]
            'application/stix+json;version=2.1'
        """
        params = _page_params(limit, added_after)
        return self.client.get(f"{_BASE}/collections/{uuid}/manifest", params=params or None)

    def objects(self, uuid: str, *, limit: int | None = None, added_after: str | None = None) -> dict[str, Any]:
        """Fetch STIX 2.1 objects from a collection.

        ``GET .../collections/{uuid}/objects``. Returns the FortiSOAR
        ``{totalItems, objects: []}`` envelope; paginate with ``limit`` +
        ``added_after`` (there is no TAXII ``more``/``next`` cursor here).

        Example:
            >>> client = demo_client()
            >>> objs = client.taxii.objects("malware-samples", limit=50)
            >>> objs["totalItems"]
            1
            >>> objs["objects"][0]["type"]
            'malware'
        """
        params = _page_params(limit, added_after)
        return self.client.get(f"{_BASE}/collections/{uuid}/objects", params=params or None)

    def object(self, uuid: str, stix_id: str) -> dict[str, Any]:
        """Fetch a single STIX object by id.

        ``GET .../collections/{uuid}/objects/{stix_id}`` — same
        ``{totalItems, objects: []}`` envelope, filtered to the one object.

        .. note::
            Known issue on FSR 7.6.5: this route can return an empty envelope;
            fall back to :meth:`objects` and filter client-side if so.

        Example:
            >>> client = demo_client()
            >>> stix_id = "malware--31b7aa16-6a19-4d5e-9e1a-3a5c9f6a2b40"
            >>> client.taxii.object("malware-samples", stix_id)["objects"][0]["name"]
            'example-malware'
        """
        return self.client.get(f"{_BASE}/collections/{uuid}/objects/{stix_id}")


def _page_params(limit: int | None, added_after: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if limit is not None:
        params["limit"] = limit
    if added_after is not None:
        params["added_after"] = added_after
    return params
