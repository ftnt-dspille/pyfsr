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
