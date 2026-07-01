"""Recorded ``/api/3`` REST responses, frozen as fixtures.

These are **real captures** (or faithful trimmed slices of real captures) of the
FortiSOAR REST API, not hand-authored shapes. They back the doctested return
examples in the API guides (records, querying, …) so those examples can't
silently drift from what the appliance actually returns — the same role
:mod:`pyfsr._testing.appliance_captures` plays for the appliance-CLI verbs.

How to read this module:

- Each ``*_RESPONSE`` constant is the decoded JSON body of one REST response.
  They are deliberately trimmed to the fields a doctest needs to show: volatile
  nested objects (full ``createUser``/``modifyUser`` people records, SLA
  picklist blocks) are dropped, but the shapes a reader cares about ���
  ``hydra:member`` / ``hydra:totalItems`` / ``hydra:view`` for collections,
  ``@id`` / ``@type`` / picklist ``itemValue`` for single records — are real.
- :class:`pyfsr._testing.replay_http.ReplaySession` answers ``Session.request``
  calls by matching ``(method, path)`` against these captures.
- :func:`pyfsr._testing.replay_http.demo_client` builds a :class:`pyfsr.FortiSOAR`
  whose ``session`` is a ``ReplaySession`` — the object the doctests call.

Refreshing on a version bump: the canonical raw captures live in
``tests/resources/mock_responses/``; this module trims them to a stable,
doctest-friendly slice. Re-capture the raw files from a live box (needs creds),
then re-trim here. Do **not** edit a capture by hand to "fix" a failing doctest
— that defeats the point; re-capture, or mask volatile fields with
``# doctest: +ELLIPSIS`` and a comment saying why.
"""

from __future__ import annotations

# Provenance — the lab box these were recorded from, so drift across FortiSOAR
# releases is visible at a glance. Updated when the raw captures are refreshed.
CAPTURE_HOST = "fortisoar.example.com"
CAPTURE_VERSION = "7.6.x"
CAPTURE_DATE = "2026-06-20"
# The connector discovery/health captures were refreshed 2026-07-01 (trimmed
# from a live 8.0 box: 3 of 32 configured connectors retained, base64 icons
# dropped). See CONNECTORS_LIST_RESPONSE / CONNECTOR_HEALTHCHECK_RESPONSE.

# A single Alert record (``GET /api/3/alerts/<uuid>``). Trimmed from the real
# capture to the fields a doctest shows; the picklist dicts keep their real
# ``@id`` IRI + ``itemValue`` shape so typed-model flattening behaves like live.
ALERT_GET_RESPONSE = {
    "@context": "/api/3/contexts/Alert",
    "@id": "/api/3/alerts/9f0eb603-ac1e-41c3-b47b-444589beed39",
    "@type": "Alert",
    "name": "Response Capture Test Alert",
    "description": "Test alert for capturing responses",
    "severity": {
        "@id": "/api/3/picklists/58d0753f-f7e4-403b-953c-b0f521eab759",
        "@type": "Picklist",
        "itemValue": "Low",
        "orderIndex": 1,
        "color": "#28B35C",
        "uuid": "58d0753f-f7e4-403b-953c-b0f521eab759",
        "id": 438,
    },
    "status": {
        "@id": "/api/3/picklists/7de816ff-7140-4ee5-bd05-93ce22002146",
        "@type": "Picklist",
        "itemValue": "Open",
        "orderIndex": 1,
        "uuid": "7de816ff-7140-4ee5-bd05-93ce22002146",
        "id": 194,
    },
    "uuid": "9f0eb603-ac1e-41c3-b47b-444589beed39",
    "id": 195,
    "createDate": 1735149051.451315,
    "modifyDate": 1735149051.451315,
}

# A created Alert (``POST /api/3/alerts``). Same shape as a get; the appliance
# echoes the created record with its new ``@id`` / ``uuid`` / ``id``.
ALERT_CREATE_RESPONSE = dict(ALERT_GET_RESPONSE)

# A collection page (``GET /api/3/alerts`` and ``POST /api/query/alerts``) — the
# Hydra envelope: ``hydra:member`` list + ``hydra:totalItems`` + ``hydra:view``.
ALERT_LIST_RESPONSE = {
    "@context": "/api/3/contexts/Alert",
    "@id": "/api/3/alerts",
    "@type": "hydra:Collection",
    "hydra:member": [ALERT_GET_RESPONSE],
    "hydra:totalItems": 1,
    "hydra:view": {
        "@id": "/api/3/alerts",
        "@type": "hydra:PartialCollectionView",
    },
}


# ---------------------------------------------------------------------------
# Connector discovery + health captures
# ---------------------------------------------------------------------------
# Real ``GET /api/integration/connectors/`` (page 1, page_size 100). Trimmed to
# the stable fields :class:`~pyfsr.models.InstalledConnector` types — the base64
# icons, help links, and verbose descriptions are dropped, but ``name``,
# ``version``, ``label``, ``config_count``, and ``configuration`` are real so
# ``resolve_version``/``resolve_connector_id``/``configurations`` behave like
# live. The 32 connectors are the real installed set on the capture box.

_CONNECTOR_ROWS = [
    {
        "id": 3,
        "name": "smtp",
        "version": "2.6.0",
        "label": "SMTP",
        "category": ["Notification"],
        "active": True,
        "system": False,
        "config_count": 1,
        "status": "Completed",
        "configuration": [
            {"id": 3, "config_id": "11111111-0000-0000-0000-000000000003", "name": "Demo", "default": True}
        ],
        "tags": [],
        "agent": "2215f975dd501e6f25f55568edf06af9",
    },
    {
        "id": 5,
        "name": "code-snippet",
        "version": "2.2.1",
        "label": "Code Snippet",
        "category": ["Utilities"],
        "active": True,
        "system": False,
        "config_count": 1,
        "status": "Completed",
        "configuration": [
            {"id": 5, "config_id": "11111111-0000-0000-0000-000000000005", "name": "Demo", "default": True}
        ],
        "tags": [],
        "agent": "2215f975dd501e6f25f55568edf06af9",
    },
    {
        "id": 21,
        "name": "mitre-attack",
        "version": "2.0.2",
        "label": "MITRE ATT&CK",
        "category": ["Information"],
        "active": True,
        "system": False,
        "config_count": 1,
        "status": "Completed",
        "configuration": [
            {"id": 7, "config_id": "01e4e6b4-c34e-4fc1-b692-bb08591f1fe5", "name": "Demo", "default": True}
        ],
        "tags": [],
        "agent": "2215f975dd501e6f25f55568edf06af9",
    },
]
CONNECTORS_LIST_RESPONSE = {
    "status": "success",
    "totalItems": len(_CONNECTOR_ROWS),
    "itemsPerPage": 100,
    "nextPage": None,
    "previousPage": None,
    "data": _CONNECTOR_ROWS,
}

# Real ``GET /api/integration/connectors/healthcheck/<name>/<version>/`` for a
# configured connector. ``status="Available"`` is the green path. Only
# ``config_id`` is box-specific (the recorded uuid is left real so the shape is
# honest; a doctest asserting the uuid would mask it with +ELLIPSIS).
CONNECTOR_HEALTHCHECK_RESPONSE = {
    "message": "Connector is available",
    "status": "Available",
    "name": "mitre-attack",
    "version": "2.0.2",
    "config_id": "01e4e6b4-c34e-4fc1-b692-bb08591f1fe5",
    "_status": True,
    "request_id": None,
}
