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

import json as _json

# Provenance — captured from a live FortiSOAR appliance, trimmed to a stable,
# doctest-friendly slice. Drift across FortiSOAR releases is visible at a glance
# via CAPTURE_VERSION. Re-capture the raw files from a live box (needs creds),
# then re-trim here; never hand-edit a capture to "fix" a failing doctest.
CAPTURE_HOST = "fortisoar.example.com"
CAPTURE_VERSION = "8.0.x"
# The connector, module-admin, and picklist captures below were trimmed from a
# live 8.0 appliance: connectors (3 of 32 retained, base64 icons dropped) and
# the schema/picklist read-only envelopes (5 of 48 modules, 2 of 90 picklists).

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

# A mixed-outcome bulk upsert (``POST /api/3/bulkupsert/alerts``), captured live
# on 8.0.0-6034 from a two-row batch: one valid alert + one whose ``severity`` is
# not a real picklist value. FortiSOAR replies with a multi-status envelope —
# ``success`` holds each created/updated record, ``failure`` holds a bare error
# STRING per rejected row (not a structured object) with the 0-based input index
# embedded as ``index #<n>``. This backs the ``bulk_upsert(parse=True)`` ->
# ``BulkUpsertResult`` doctest (``.ok`` / ``.succeeded`` / ``.failed[].index``).
# The success record is trimmed to the doctest-relevant fields (same trim as the
# other alert captures); the failure string is the untouched server text.
BULK_UPSERT_ALERTS_MIXED_RESPONSE = {
    "success": [
        {
            "@id": "/api/3/alerts/95413fd0-164b-4ea9-bfd6-90718ccdd5d3",
            "@type": "Alert",
            "name": "pyfsr-bulk-doctest-ok",
            "uuid": "95413fd0-164b-4ea9-bfd6-90718ccdd5d3",
            "severity": {
                "@id": "/api/3/picklists/58d0753f-f7e4-403b-953c-b0f521eab759",
                "@type": "Picklist",
                "itemValue": "Low",
                "orderIndex": 1,
                "color": "#16A34A",
                "uuid": "58d0753f-f7e4-403b-953c-b0f521eab759",
                "id": 445,
            },
        }
    ],
    "failure": [
        "POST method for object at index #1 in the request payload failed with "
        'error: {"type":"UnexpectedValueException","message":"FSR_CH_0000001 : '
        "The \\u0022severity\\u0022 field is required to have a value from the "
        "\\u0022Severity\\u0022 picklist. However, the provided value does not "
        'match any of the options listed in the specified picklist."}'
    ],
}

# The staging record echoed by ``create_module`` (``POST /api/3/staging_model_metadatas``),
# captured live on 8.0.0-6034 from a throwaway ``doctestmod`` module that was created,
# then discarded + published-to-reconcile (box left with is_published()==False,
# pending_changes()==[]). Trimmed to the doctest-relevant surface: the module
# identity (``type``/``displayName``/``descriptions``) + its single seeded ``name``
# field under ``attributes`` (itself trimmed to the shape a reader inspects). The
# ``@type`` is ``StagingModelMetadata`` — the draft store, not yet ``model_metadatas``.
MODULE_CREATE_STAGING_RESPONSE = {
    "@context": "/api/3/contexts/StagingModelMetadata",
    "@id": "/api/3/staging_model_metadatas/5f8ba8e9-10e6-4acc-8b6a-9860f373e1c1",
    "@type": "StagingModelMetadata",
    "type": "doctestmod",
    "module": "doctestmod",
    "tableName": "doctestmod",
    "displayName": "{{ name }}",
    "descriptions": {"singular": "Doctest Module", "plural": "Doctest Modules"},
    "taggable": False,
    "trackable": True,
    "ownable": True,
    "attributes": [
        {
            "@id": "/api/3/attribute_metadatas/c5fdf26f-1d1b-40f3-8a2c-1513f4de410e",
            "@type": "AttributeMetadata",
            "name": "name",
            "type": "string",
            "formType": "text",
        }
    ],
    "uuid": "5f8ba8e9-10e6-4acc-8b6a-9860f373e1c1",
}

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


# A single Incident record (``GET``/``POST /api/3/incidents``), captured live
# from a throwaway record (created, fetched, deleted in the same session —
# box left with no extra incidents). Trimmed the same way as the Alert
# captures: real ``@id``/``uuid``/picklist shapes, volatile SLA/state/phase
# picklist blocks reduced to one representative each.
INCIDENT_GET_RESPONSE = {
    "@context": "/api/3/contexts/Incident",
    "@id": "/api/3/incidents/0740411d-e852-4eee-b33b-596210d09a9b",
    "@type": "Incident",
    "name": "pyfsr doctest incident",
    "description": "temporary, will be deleted",
    "severity": {
        "@id": "/api/3/picklists/7efa2220-39bb-44e4-961f-ac368776e3b0",
        "@type": "Picklist",
        "itemValue": "Critical",
        "orderIndex": 4,
        "color": "#B22222",
        "uuid": "7efa2220-39bb-44e4-961f-ac368776e3b0",
        "id": 446,
    },
    "state": {
        "@id": "/api/3/picklists/a1bac09b-1441-45aa-ad1b-c88744e48e72",
        "@type": "Picklist",
        "itemValue": "New",
        "orderIndex": 0,
        "uuid": "a1bac09b-1441-45aa-ad1b-c88744e48e72",
        "id": 198,
    },
    "uuid": "0740411d-e852-4eee-b33b-596210d09a9b",
}

INCIDENT_CREATE_RESPONSE = dict(INCIDENT_GET_RESPONSE)


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
    {
        "id": 16,
        "name": "virustotal",
        "version": "3.2.1",
        "label": "VirusTotal",
        "category": ["Threat Intelligence"],
        "active": True,
        "system": False,
        "config_count": 0,
        "status": "Completed",
        "configuration": [],
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


# Real ``POST /api/integration/connectors/<id>/`` (``connector_detail``) for the
# smtp connector (id=3 in ``_CONNECTOR_ROWS``). Trimmed to a doctest-friendly
# slice: the full ``operations[]`` (each carries ``parameters[]`` +
# ``output_schema``) is reduced to ``operation``/``title`` for the first four,
# and each ``configuration[]`` entry's ``config`` dict is dropped (it carries
# connection details — host/port/credentials — that aren't doctest material and
# must not ship). ``config_id`` is a box-specific uuid left real, matching the
# healthcheck capture's convention.
CONNECTOR_DETAIL_RESPONSE = {
    "name": "smtp",
    "version": "2.6.0",
    "category": ["Notification"],
    "config_count": 1,
    "operations": [
        {"operation": "send_email_new", "title": "Send Email (Advanced)"},
        {"operation": "send_email", "title": "Send Email"},
        {"operation": "send_richtext_email", "title": "Send Rich Text Email (Deprecated)"},
        {"operation": "get_users", "title": "Get Users"},
    ],
    "configuration": [
        {
            "id": 1,
            "config_id": "88c3d39c-2fa9-4731-b00d-29815008f17c",
            "status": 1,
            "name": "localhost-postfix",
            "default": True,
        },
    ],
}


# Real ``POST /api/integration/execute/`` for the ``cisa-advisory`` connector's
# ``get_known_exploited_vulnerability_cves`` operation — a public, read-only,
# parameter-less feed lookup (safe to demo against a real vendor connector; the
# only side effect is CISA's public catalog serving one GET). Trimmed from
# 1631 real entries to 2, keeping every field on both so the doctest shape is
# honest (only the CVE list is shortened, not any single entry's fields).
CONNECTOR_EXECUTE_CISA_ADVISORY_RESPONSE = {
    "operation": "get_known_exploited_vulnerability_cves",
    "status": "Success",
    "message": "",
    "data": {
        "title": "CISA Catalog of Known Exploited Vulnerabilities",
        "catalogVersion": "2026.07.01",
        "dateReleased": "2026-07-01T19:00:06.9016Z",
        "count": 1631,
        "vulnerabilities": [
            {
                "cveID": "CVE-2026-45659",
                "vendorProject": "Microsoft",
                "product": "SharePoint Server",
                "vulnerabilityName": "Microsoft SharePoint Server Deserialization of Untrusted Data Vulnerability",
                "dateAdded": "2026-07-01",
                "shortDescription": (
                    "Microsoft SharePoint Server contains a deserialization of untrusted data "
                    "vulnerability which allows an authorized attacker to execute code over a network."
                ),
                "requiredAction": (
                    "Apply mitigations in accordance with vendor instructions, ensuring compliance "
                    "with CISA’s BOD 26-04 Prioritizing Security Updates Based on Risk (see URL in "
                    "Notes) guidance and CISA’s “Forensics Triage Requirements” (see URL in Notes). "
                    "Follow applicable BOD 26-04 guidance for cloud services or discontinue use of the "
                    "product if mitigations are unavailable. Stakeholders are responsible for evaluating "
                    "each asset's internet exposure and ensuring adherence to BOD 26-04 patching guidelines."
                ),
                "dueDate": "2026-07-04",
                "knownRansomwareCampaignUse": "Unknown",
                "notes": (
                    "https://msrc.microsoft.com/update-guide/vulnerability/CVE-2026-45659 ; "
                    "BOD 26-04: https://www.cisa.gov/news-events/directives/"
                    "bod-26-04-prioritizing-security-updates-based-risk ; "
                    "Forensics Triage Requirements: https://www.cisa.gov/news-events/directives/"
                    "bod-26-04-implementation-guidance-prioritizing-security-updates-based-risk ; "
                    "https://nvd.nist.gov/vuln/detail/CVE-2026-45659"
                ),
                "cwes": ["CWE-502"],
            },
            {
                "cveID": "CVE-2026-48558",
                "vendorProject": "SimpleHelp ",
                "product": "SimpleHelp",
                "vulnerabilityName": "SimpleHelp Authentication Bypass Vulnerability",
                "dateAdded": "2026-06-29",
                "shortDescription": (
                    "SimpleHelp contains an authentication bypass vulnerability in the OIDC "
                    "authentication flow. When OIDC authentication is configured, identity tokens "
                    "submitted during login are accepted without verifying their cryptographic "
                    "signature. In a vulnerable configuration, a remote, unauthenticated attacker "
                    "can submit a forged token containing arbitrary identity claims to obtain a "
                    "fully authenticated technician session. In some configurations, this may also "
                    "allow bypass of multi-factor authentication."
                ),
                "requiredAction": (
                    "Apply mitigations in accordance with vendor instructions, ensuring compliance "
                    "with CISA’s BOD 26-04 Prioritizing Security Updates Based on Risk (see URL in "
                    "Notes) guidance and CISA’s “Forensics Triage Requirements” (see URL in Notes). "
                    "Follow applicable BOD 26-04 guidance for cloud services or discontinue use of the "
                    "product if mitigations are unavailable. Stakeholders are responsible for evaluating "
                    "each asset's internet exposure and ensuring adherence to BOD 26-04 patching guidelines."
                ),
                "dueDate": "2026-07-02",
                "knownRansomwareCampaignUse": "Unknown",
                "notes": (
                    "https://simple-help.com/security/simplehelp-security-update-2026-05 ; "
                    "BOD 26-04: https://www.cisa.gov/news-events/directives/"
                    "bod-26-04-prioritizing-security-updates-based-risk ; "
                    "Forensics Triage Requirements: https://www.cisa.gov/news-events/directives/"
                    "bod-26-04-implementation-guidance-prioritizing-security-updates-based-risk ; "
                    "https://nvd.nist.gov/vuln/detail/CVE-2026-48558"
                ),
                "cwes": ["CWE-347"],
            },
        ],
    },
}


# Real ``POST /api/integration/configuration/`` (``create_configuration``) then
# ``PUT /api/integration/configuration/<id>/`` (``update_configuration``) for a
# throwaway ``virustotal`` config, created/rotated/deleted live on a real
# appliance and left with 0 configs afterwards (round-trip verified via
# ``list_configured``). ``api_key`` was a placeholder value, never a real
# credential, so it's safe to keep verbatim; the server always echoes secrets
# back as the literal string ``"NULL"`` regardless of what was sent, which is
# itself the notable wire behavior this capture documents. ``config_id``/
# ``agent`` are real box-specific uuids left in place, matching this module's
# convention for other connector captures.
CONNECTOR_CREATE_CONFIG_RESPONSE = {
    "id": 269,
    "config_id": "0e75640a-ba4a-4bc2-be41-524a9e47fa3f",
    "name": "pyfsr-doctest-config",
    "default": False,
    "status": None,
    "config": {"server": "www.virustotal.com", "api_key": "NULL", "verify_ssl": True},
    "connector": 16,
    "agent": "2215f975dd501e6f25f55568edf06af9",
    "teams": [],
    "remote_status": {},
    "health_status": {},
    "connector_name": "virustotal",
    "connector_version": "3.2.1",
}

# Same config after ``update_configuration`` rotates its ``api_key``. Note the
# PUT response omits ``connector_name``/``connector_version`` — present on
# create, absent on update — an asymmetry callers should not rely on either
# field being there.
CONNECTOR_UPDATE_CONFIG_RESPONSE = {
    "id": 269,
    "config_id": "0e75640a-ba4a-4bc2-be41-524a9e47fa3f",
    "name": "pyfsr-doctest-config",
    "default": False,
    "status": None,
    "config": {"server": "www.virustotal.com", "api_key": "NULL", "verify_ssl": True},
    "connector": 16,
    "agent": "2215f975dd501e6f25f55568edf06af9",
    "teams": [],
    "remote_status": {},
    "health_status": {},
}


# ---------------------------------------------------------------------------
# Module-admin (staging/published schema) + picklist captures
# ---------------------------------------------------------------------------
# Real read-only captures trimmed from a live 8.0 appliance — no write ops.
# ``staging_model_metadatas`` is the editable draft; ``model_metadatas`` is the
# committed/published schema. Both *list* envelopes are trimmed to 5 modules,
# and only the ``alerts`` member carries ``attributes`` (the 3 fields the
# doctests show) so ``describe_module`` / ``get_field`` resolve; the other 4 are
# lite-only. The two lists are kept semantically identical (same modules, same
# alerts attributes) so ``pending_changes()`` — which diffs staging vs published
# after canonicalizing store-IRI segments and stripping ``@id``/``@type``/
# ``@context`` — reports ``[]`` (an honest fully-published box). Single-record
# captures back ``get_staging`` / ``get_published`` / ``get_field``.

# The ``alerts`` fields the doctests exercise. ``severity`` and ``status`` are
# kept full (``dataSource.query`` carries the picklist name ``describe_module``
# extracts, and ``picklists.for_field``/``resolve`` read); ``name``/``description``
# are lite (name/type/formType only). Real IRIs/uuids.
_ALERTS_ATTRIBUTES = [
    {
        "@id": "/api/3/attribute_metadatas/f6ffe98e-f56b-4d5b-bc2c-90edbfa8e618",
        "@type": "AttributeMetadata",
        "name": "name",
        "type": "string",
        "formType": "text",
        "orderIndex": 28,
    },
    {
        "@id": "/api/3/attribute_metadatas/1e61a7a2-b60f-4d72-8d73-76967a9fa6ef",
        "@type": "AttributeMetadata",
        "name": "description",
        "type": "string",
        "formType": "richtext",
        "orderIndex": 50,
    },
    {
        "@id": "/api/3/attribute_metadatas/e646b7ed-e4bc-4820-ade0-c6464d0ea75f",
        "@type": "AttributeMetadata",
        "name": "severity",
        "type": "picklists",
        "formType": "picklist",
        "orderIndex": 43,
        "displayName": "{{ severity }}",
        "descriptions": {"singular": "Severity"},
        "validation": {"_enableRange": False, "required": False, "minlength": 0, "maxlength": 10485761},
        "dataSource": {
            "model": "picklists",
            "query": {
                "filters": [{"field": "listName__name", "operator": "eq", "value": "Severity"}],
                "logic": "AND",
                "sort": [{"direction": "ASC", "field": "orderIndex"}],
            },
        },
        "uuid": "e646b7ed-e4bc-4820-ade0-c6464d0ea75f",
    },
    {
        "@id": "/api/3/attribute_metadatas/79b104b1-b68a-46df-baa1-a9e99e6a75f8",
        "@type": "AttributeMetadata",
        "name": "status",
        "type": "picklists",
        "formType": "picklist",
        "orderIndex": 40,
        "displayName": "{{ status }}",
        "descriptions": {"singular": "Status"},
        "validation": {"_enableRange": False, "required": False, "minlength": 0, "maxlength": 10485761},
        "dataSource": {
            "model": "picklists",
            "query": {
                "filters": [{"field": "listName__name", "operator": "eq", "value": "AlertStatus"}],
                "logic": "AND",
                "sort": [{"direction": "ASC", "field": "orderIndex"}],
            },
        },
        "uuid": "79b104b1-b68a-46df-baa1-a9e99e6a75f8",
    },
]

# The 5 modules both list envelopes carry. ``alerts`` carries
# ``_ALERTS_ATTRIBUTES``; the rest are lite-only (enough for ``list_modules`` and
# the ``pending_changes`` module-set comparison). Real uuids.
_MODULE_ROWS = [
    {
        "type": "agents",
        "module": "agents",
        "uuid": "266e4fbb-2bcd-47dd-9ba6-400b88d49a92",
        "displayName": "{{ name }}",
        "parentType": None,
        "tableName": "agents",
        "descriptions": {"singular": "Agent", "plural": "Agents"},
    },
    {
        "type": "alerts",
        "module": "alerts",
        "uuid": "7fdae59c-7de7-43d9-bf2a-dc2f00ed25b4",
        "displayName": "{{ name }}",
        "parentType": None,
        "tableName": "alerts",
        "descriptions": {"plural": "Alerts", "singular": "Alert"},
        "attributes": _ALERTS_ATTRIBUTES,
    },
    {
        "type": "announcements",
        "module": "announcements",
        "uuid": "9f907344-827d-4d29-99b4-e2f9717009b2",
        "displayName": "{{title}}",
        "parentType": None,
        "tableName": "announcements",
        "descriptions": {"singular": "Announcement", "plural": "Announcements"},
    },
    {
        "type": "incidents",
        "module": "incidents",
        "uuid": "ec515d53-dbfb-411a-89b5-e42bd17ad7c9",
        "displayName": "{{ name }}",
        "parentType": None,
        "tableName": "incidents",
        "descriptions": {"plural": "Incidents", "singular": "Incident"},
    },
    {
        "type": "tasks",
        "module": "tasks",
        "uuid": "5cb5a987-52d6-4df2-87cd-86a193dee71f",
        "displayName": "{{ name }}",
        "parentType": None,
        "tableName": "tasks",
        "descriptions": {"plural": "Tasks", "singular": "Task"},
    },
]


def _hydra_collection(path: str, context: str, members: list) -> dict:
    """Build a ``hydra:Collection`` envelope matching the live list shape."""
    return {
        "@context": context,
        "@id": path,
        "@type": "hydra:Collection",
        "hydra:member": members,
        "hydra:totalItems": len(members),
        "hydra:view": {"@id": path, "@type": "hydra:PartialCollectionView"},
    }


def _with_store(row: dict, store_path: str, store_type: str) -> dict:
    """Return a copy of a ``_MODULE_ROWS`` entry stamped for one store.

    ``staging_model_metadatas`` / ``StagingModelMetadata`` vs ``model_metadatas``
    / ``ModelMetadata`` — the only fields ``_differs`` strips, so both lists are
    semantically identical and ``pending_changes()`` stays empty.
    """
    out = dict(row)
    out["@id"] = f"/api/3/{store_path}/{row['uuid']}"
    out["@type"] = store_type
    return out


_STAGING_ROWS = [_with_store(r, "staging_model_metadatas", "StagingModelMetadata") for r in _MODULE_ROWS]
_PUBLISHED_ROWS = [_with_store(r, "model_metadatas", "ModelMetadata") for r in _MODULE_ROWS]

STAGING_MODULES_LIST_RESPONSE = _hydra_collection(
    "/api/3/staging_model_metadatas", "/api/3/contexts/StagingModelMetadata", _STAGING_ROWS
)
PUBLISHED_MODULES_LIST_RESPONSE = _hydra_collection(
    "/api/3/model_metadatas", "/api/3/contexts/ModelMetadata", _PUBLISHED_ROWS
)


def pending_create_overlay(modules: str | list[str] = "crew") -> dict:
    """A ``demo_client(overrides=...)`` overlay staging brand-new module(s).

    Adds each name in ``modules`` (a single string or a list) to the **staging**
    list only — published is left as the shared fixture — so
    :meth:`~pyfsr.api.modules_admin.ModulesAdminAPI.pending_changes` reports each
    as ``change="created"``: the exact state right after ``create_module()`` and
    before ``publish()``. The overlay is scoped to the one session it's passed to;
    the module-global staging fixture (and every ``pending_changes() == []``
    doctest that reads it) is untouched.

    Each new row reuses the live staging-list envelope shape (``_with_store`` +
    ``_hydra_collection``) — the same fields the appliance returns — with a
    synthetic demo uuid, so nothing about the wire shape is invented.
    """
    names = [modules] if isinstance(modules, str) else list(modules)
    new_rows = []
    for i, module in enumerate(names):
        new_rows.append(
            _with_store(
                {
                    "type": module,
                    "module": module,
                    "uuid": f"00000000-0000-4000-8000-0000000000{i:02x}",
                    "displayName": "{{ name }}",
                    "parentType": None,
                    "tableName": module,
                    "descriptions": {"plural": module.capitalize() + "s", "singular": module.capitalize()},
                },
                "staging_model_metadatas",
                "StagingModelMetadata",
            )
        )
    staging_list = _hydra_collection(
        "/api/3/staging_model_metadatas",
        "/api/3/contexts/StagingModelMetadata",
        _STAGING_ROWS + new_rows,
    )
    # Key format mirrors replay_http._entry: (METHOD, path with the leading slash
    # stripped). Query params are ignored by the matcher, so this catches the
    # ``$relationships=true`` list GET pending_changes() issues.
    return {("GET", "api/3/staging_model_metadatas"): {"status": 200, "body": staging_list}}


# Single-record ``GET /api/3/{staging_,}model_metadatas/<alerts-uuid>`` — the full
# metadata record (incl. ``attributes``) that ``get_staging``/``get_published``
# return and ``get_field`` reads. Same 3 attributes as the list's alerts member.
_ALERTS_TOP = {
    "type": "alerts",
    "module": "alerts",
    "uuid": "7fdae59c-7de7-43d9-bf2a-dc2f00ed25b4",
    "displayName": "{{ name }}",
    "parentType": None,
    "tableName": "alerts",
    "descriptions": {"plural": "Alerts", "singular": "Alert"},
    "attributes": _ALERTS_ATTRIBUTES,
}
STAGING_ALERTS_RESPONSE = _with_store(_ALERTS_TOP, "staging_model_metadatas", "StagingModelMetadata")
STAGING_ALERTS_RESPONSE["@context"] = "/api/3/contexts/StagingModelMetadata"
PUBLISHED_ALERTS_RESPONSE = _with_store(_ALERTS_TOP, "model_metadatas", "ModelMetadata")
PUBLISHED_ALERTS_RESPONSE["@context"] = "/api/3/contexts/ModelMetadata"

# ``GET /api/publish/error`` — the last publish's outcome. ``status="Success"`` +
# a present body means nothing is mid-fail; ``pending_changes()`` is the cleaner
# "what's uncommitted" view. ``last_publish_time`` is the appliance's own epoch.
PUBLISH_ERROR_RESPONSE = {
    "@type": "Publish",
    "status": "Success",
    "last_publish_time": 1782950402,
}


# ---------------------------------------------------------------------------
# Picklist captures (back ``list_picklists`` / ``get_picklist_values``)
# ---------------------------------------------------------------------------
# ``_load_bulk`` makes two calls: ``GET /api/3/picklist_names`` (the name set +
# the listName-IRI→name map) and ``GET /api/3/picklists`` (every item, each
# carrying its own ``listName`` IRI). The nested ``picklists`` array on each
# picklist_names member is NOT read by ``_load_bulk`` (it uses the flat call), so
# it is dropped here. Two picklists are retained — Severity (5 items) and
# AlertStatus (5) — with real IRIs/uuids/colors so ``values("Severity")`` returns
# the real itemValue/uuid/iri/ordinal tuples.

_PICKLIST_NAME_ROWS = [
    {
        "@id": "/api/3/picklist_names/4e80cba3-032f-48b4-ac03-17e3ec247aac",
        "@type": "PicklistName",
        "name": "Severity",
        "system": False,
        "uuid": "4e80cba3-032f-48b4-ac03-17e3ec247aac",
        "id": 64,
    },
    {
        "@id": "/api/3/picklist_names/33e964a9-d607-49f2-813c-7ce46141815a",
        "@type": "PicklistName",
        "name": "AlertStatus",
        "system": False,
        "uuid": "33e964a9-d607-49f2-813c-7ce46141815a",
        "id": 60,
    },
]
PICKLIST_NAMES_RESPONSE = _hydra_collection(
    "/api/3/picklist_names", "/api/3/contexts/PicklistName", _PICKLIST_NAME_ROWS
)

_SEV = "/api/3/picklist_names/4e80cba3-032f-48b4-ac03-17e3ec247aac"
_STAT = "/api/3/picklist_names/33e964a9-d607-49f2-813c-7ce46141815a"


def _item(item_value, order_index, color, uuid, id_, list_name):
    return {
        "@id": f"/api/3/picklists/{uuid}",
        "@type": "Picklist",
        "itemValue": item_value,
        "orderIndex": order_index,
        "color": color,
        "icon": None,
        "listName": list_name,
        "uuid": uuid,
        "id": id_,
        "importedBy": None,
    }


_PICKLIST_ITEM_ROWS = [
    _item("Minimal", 0, "#42C5F3", "0d609b08-45e0-469f-8910-41145c0b7c03", 443, _SEV),
    _item("Low", 1, "#16A34A", "58d0753f-f7e4-403b-953c-b0f521eab759", 445, _SEV),
    _item("Medium", 2, "#D9BC00", "b3c20a3a-ecfd-4adc-a225-0205968e6793", 447, _SEV),
    _item("High", 3, "#F06105", "40187287-89fc-4e9c-b717-e9443d57eedb", 444, _SEV),
    _item("Critical", 4, "#B22222", "7efa2220-39bb-44e4-961f-ac368776e3b0", 446, _SEV),
    _item("Open", 1, "#264EA1", "7de816ff-7140-4ee5-bd05-93ce22002146", 201, _STAT),
    _item("Investigating", 2, "#25A5AE", "758925e7-629c-46d8-89db-fb36f5fbe88a", 200, _STAT),
    _item("Pending", 3, "#D9BC00", "a53d5465-75a6-4b7a-8144-4eccc23cea4a", 203, _STAT),
    _item("Closed", 4, "#596374", "fac53e73-8d16-4189-98d5-95fbd1555232", 204, _STAT),
    _item("Re-Opened", 6, "#F19C3F", "891fb9d5-556c-44c6-9f7d-94a27dec732e", 202, _STAT),
]
PICKLISTS_RESPONSE = _hydra_collection("/api/3/picklists", "/api/3/contexts/Picklist", _PICKLIST_ITEM_ROWS)


# FortiAI agentic investigation — captured from a live appliance (8.0). The
# pipeline is ``POST /api/ai/triage/alert`` (start) ��� poll
# ``GET /api/ai/agents/<task_id>/status`` → ``GET /api/ai/agents/<task_id>/result``.
# The result is the full 9-phase verdict payload (normalization → context_enrichment
# → hypothesis → investigation_plan → investigation_execution → key_finding →
# hypothesis_evaluation → verdict → next_action). Trimmed from a 30 KB real
# capture: every structural key + the per-phase state/status/message is kept
# honest; the verbose analysis text (key_findings body, hypothesis reasoning,
# log params/result, IOC lists) is shortened to one representative entry and
# trimmed prose. Alert IOCs are generalized; no appliance details leak.
FORTIAI_TASK_ID = "a2afba58-9dbe-44dd-a6e6-7227e33990db"

FORTIAI_START_RESPONSE = {"task_id": FORTIAI_TASK_ID, "status": "pending"}

FORTIAI_STATUS_RESPONSE = {"task_id": FORTIAI_TASK_ID, "status": "completed"}

FORTIAI_RESULT_RESPONSE = {
    "data": {
        "event_count": 1,
        "incident_id": "FEDR-01850936",
        "severity": {"label": "Critical", "score": None},
        "status": "Open",
        "hostname": "dc-86",
        "name": "Ransomware Precursor: vssadmin Delete Shadows on dc-86",
        "uuid": "82cb8b3d-0130-4e41-8999-076e121b0dc1",
    },
    "summary": {
        "highlighted_summary": (
            "Inconclusive with mixed benign, false-positive, suspicious, and "
            "malicious interpretations because the investigation data is too "
            "limited to confirm why the shadow copy deletion activity occurred."
        ),
        "classification": "Inconclusive",
        "key_findings": [
            {
                "id": "F1",
                "name": "Organizational records contain no approval information for vssadmin.exe activity on dc-86",
                "details": 'Organizational context records state "No Information Available".',
            }
        ],
        "next_action_steps": ["Preserve forensic evidence from host dc-86 related to process vssadmin.exe"],
    },
    "hypotheses": [
        {
            "id": 1,
            "name": "Administrative shadow copy removal on dc-86",
            "intent": "benign",
            "explanatory_focus": "routine administration",
            "description": "An administrator legitimately deleted shadow copies.",
            "reasoning": "Shadow copy deletion is a known admin task.",
            "tactics": [],
            "techniques": [],
            "intentStatus": "INCONCLUSIVE",
            "attentionNeeded": "No",
        }
    ],
    "playbook": {
        "immediate_next_actions": ["Preserve forensic evidence from host dc-86 related to process vssadmin.exe"]
    },
    "logs": [
        {
            "id": 10101,
            "uuid": "c2308c24-afc6-4ff7-9f20-964fb6d3e1c6",
            "index": 1,
            "question": (
                "Is process 'vssadmin.exe' executing 'delete shadows /all /quiet' "
                "on host 'dc-86' under user 'ssmith' associated with approved activity?"
            ),
            "result": "No information available",
            "status": "success",
        }
    ],
    "phases": [
        {"data": {"message": "Normalizing Alert"}, "state": "normalization", "status": "completed"},
        {"data": {"message": "Enriching Alert Context"}, "state": "context_enrichment", "status": "completed"},
        {"data": {"message": "Generating Initial Hypothesis"}, "state": "hypothesis", "status": "completed"},
        {"data": {"message": "Preparing Investigation Plan"}, "state": "investigation_plan", "status": "completed"},
        {
            "data": {"message": "Executing Investigation Plan"},
            "state": "investigation_execution",
            "status": "completed",
        },
        {"data": {"message": "Summarizing Key Findings"}, "state": "key_finding", "status": "completed"},
        {"data": {"message": "Evaluating Hypotheses"}, "state": "hypothesis_evaluation", "status": "completed"},
        {"data": {"message": "Generating Investigation Verdict"}, "state": "verdict", "status": "completed"},
        {"data": {"message": "Recommending Next Step"}, "state": "next_action", "status": "completed"},
    ],
}

# ---------------------------------------------------------------------------
# Widgets — client.widgets (upload / publish / list)
# ---------------------------------------------------------------------------
# Captured live against fsr-ga (8.0.0-6034) 2026-07-08 exercising the real
# upload -> dev-manifest -> publish round trip with a genuine widget package
# (jinjaEditorWidget 1.1.3, sourced from an internal widget dev kit — not a
# FortiSOAR-shipped widget), then removed; the box's 44-widget catalog was
# unaffected. createUser/modifyUser (full Person records) and the upload
# response's full asset ``tree`` are dropped per this module's trim
# convention; WIDGET_DEV_MANIFEST_RESPONSE keeps a 2-file slice of ``tree``
# so a doctest can show the shape without the real ~30-entry asset tree.

# GET /api/3/widgets — 3 of the real 44 installed/published widgets.
WIDGET_LIST_RESPONSE = {
    "@context": "/api/3/contexts/Widget",
    "@id": "/api/3/widgets",
    "@type": "hydra:Collection",
    "hydra:totalItems": 44,
    "hydra:member": [
        {
            "@id": "/api/3/widgets/af505f28-31be-4528-9b7d-c579d8de43f5",
            "@type": "Widget",
            "publishedDate": 1617038619,
            "subTitle": "Mobile Settings",
            "installed": True,
            "name": "mobileSettings",
            "label": "Mobile Settings",
            "title": "Mobile Settings",
            "version": "2.0.1",
            "metadata": {
                "pages": ["Widget Library"],
                "certified": "Yes",
                "publisher": "Fortinet",
                "compatibility": ["7.0.2"],
                "mobileCompatibleVersion": "1.8.0",
            },
            "draft": False,
            "enablePublish": None,
            "uuid": "af505f28-31be-4528-9b7d-c579d8de43f5",
        },
        {
            "@id": "/api/3/widgets/3aa1e7ab-f9fc-4365-a137-5808aad6d9c6",
            "@type": "Widget",
            "publishedDate": 1618084800,
            "subTitle": (
                "Primarily designed to showcase a particular record's highlights/summary, "
                "this widget houses multiple utility widgets within it to allow for "
                "customized uses."
            ),
            "installed": True,
            "name": "recordSummary",
            "label": "Record Summary",
            "title": "Record Summary",
            "version": "2.0.0",
            "metadata": {
                "pages": ["View Panel"],
                "certified": "Yes",
                "publisher": "Fortinet",
                "compatibility": ["7.0.2"],
            },
            "draft": False,
            "enablePublish": None,
            "uuid": "3aa1e7ab-f9fc-4365-a137-5808aad6d9c6",
        },
        {
            "@id": "/api/3/widgets/087a730d-67ae-41f2-8d49-3c22f6eaef30",
            "@type": "Widget",
            "publishedDate": 1647979200,
            "subTitle": "Change which teams/users have access to records",
            "installed": True,
            "name": "accessControl",
            "label": "Access Control",
            "title": "Access Control",
            "version": "2.1.0",
            "metadata": {
                "pages": ["View Panel"],
                "certified": "Yes",
                "publisher": "Fortinet",
                "compatibility": ["7.0.2", "7.2.0"],
            },
            "draft": False,
            "enablePublish": None,
            "uuid": "087a730d-67ae-41f2-8d49-3c22f6eaef30",
        },
    ],
}

# POST /api/3/solutionpacks/install?$type=widget&$replace=true — the widget
# record right after upload: staged in the dev workspace, NOT live yet
# (draft:true, installed:false). Real values from uploading jinjaEditorWidget
# 1.1.3 for the first time on the capture box.
WIDGET_UPLOAD_RESPONSE = {
    "@context": "/api/3/contexts/Widget",
    "@id": "/api/3/widgets/5fef77ad-8917-40c6-82a2-fdd753bdf41c",
    "@type": "Widget",
    "publishedDate": 1745280000,
    "subTitle": "Write, evaluate, and debug Jinja templates from any dashboard or record detail page.",
    "installed": False,
    "name": "jinjaEditorWidget",
    "label": "Jinja Editor",
    "title": "Jinja Editor",
    "version": "1.1.3",
    "metadata": {
        "description": (
            "Embeds the FortiSOAR Jinja editor as a widget. Provide an input JSON payload, "
            "write a Jinja template against it, and render the result. On the View Panel, "
            "optionally seed the input with the current record so you can debug templates "
            "against real data."
        ),
        "publisher": "Dylan Spille",
        "certified": "No",
        "compatibility": ["7.4.0", "7.4.1", "7.6.0", "7.6.5"],
        "snapshots": [],
        "category": ["Utilities", "Playbooks"],
        "pages": ["Dashboard", "View Panel"],
        "standalone": False,
        "windowClass": "Full Width",
        "size": "lg",
    },
    "draft": True,
    "enablePublish": None,
    "uuid": "5fef77ad-8917-40c6-82a2-fdd753bdf41c",
}

# GET /api/3/widgets/development/<uuid> — the dev-workspace manifest publish()
# reads, then strips ``tree`` from before PUTting it back. ``tree`` here is a
# 2-file slice (real capture has ~30 entries covering every asset) — enough to
# show the shape a doctest needs without the noise.
WIDGET_DEV_MANIFEST_RESPONSE = {
    "@type": "hydra:Collection",
    "hydra:member": [
        {
            "@id": "/api/3/widgets/5fef77ad-8917-40c6-82a2-fdd753bdf41c",
            "subTitle": "Write, evaluate, and debug Jinja templates from any dashboard or record detail page.",
            "installed": False,
            "name": "jinjaEditorWidget",
            "title": "Jinja Editor",
            "version": "1.1.3",
            "metadata": {
                "description": (
                    "Embeds the FortiSOAR Jinja editor as a widget. Provide an input JSON "
                    "payload, write a Jinja template against it, and render the result. On the "
                    "View Panel, optionally seed the input with the current record so you can "
                    "debug templates against real data."
                ),
                "publisher": "Dylan Spille",
                "certified": "No",
                "compatibility": ["7.4.0", "7.4.1", "7.6.0", "7.6.5"],
                "snapshots": [],
                "category": ["Utilities", "Playbooks"],
                "pages": ["Dashboard", "View Panel"],
                "standalone": False,
                "windowClass": "Full Width",
                "size": "lg",
            },
            "draft": True,
            "enablePublish": None,
            "systemWidget": False,
            "tree": {
                "jinjaEditorWidget-1.1.3": {
                    "name": "jinjaEditorWidget-1.1.3",
                    "primaryFolder": True,
                    "type": "folder",
                    "files": {
                        "info.json": {
                            "name": "info.json",
                            "type": "json",
                            "xpath": "/jinjaEditorWidget-1.1.3/info.json",
                        },
                        "view.controller.js": {
                            "name": "view.controller.js",
                            "type": "js",
                            "xpath": "/jinjaEditorWidget-1.1.3/view.controller.js",
                        },
                    },
                }
            },
        }
    ],
}

# PUT /api/3/widgets/<uuid> — the published widget: draft:false, installed:true.
# Real response from publishing the uploaded jinjaEditorWidget above.
WIDGET_PUBLISH_RESPONSE = {
    "@context": "/api/3/contexts/Widget",
    "@id": "/api/3/widgets/5fef77ad-8917-40c6-82a2-fdd753bdf41c",
    "@type": "Widget",
    "publishedDate": 1783520302,
    "subTitle": "Write, evaluate, and debug Jinja templates from any dashboard or record detail page.",
    "installed": True,
    "name": "jinjaEditorWidget",
    "label": "Jinja Editor",
    "title": "Jinja Editor",
    "version": "1.1.3",
    "metadata": {
        "size": "lg",
        "pages": ["Dashboard", "View Panel"],
        "category": ["Utilities", "Playbooks"],
        "certified": "No",
        "publisher": "Dylan Spille",
        "snapshots": [],
        "standalone": False,
        "description": (
            "Embeds the FortiSOAR Jinja editor as a widget. Provide an input JSON payload, "
            "write a Jinja template against it, and render the result. On the View Panel, "
            "optionally seed the input with the current record so you can debug templates "
            "against real data."
        ),
        "windowClass": "Full Width",
        "compatibility": ["7.4.0", "7.4.1", "7.6.0", "7.6.5"],
    },
    "draft": False,
    "enablePublish": False,
    "uuid": "5fef77ad-8917-40c6-82a2-fdd753bdf41c",
}

# 400 body from POST .../solutionpacks/install?$type=widget&$replace=false when
# that exact name+version is already staged in the dev workspace — the real
# text WidgetsAPI.upload matches to raise WidgetUploadConflict. Captured live
# by re-uploading jinjaEditorWidget 1.1.3 with replace=False right after the
# upload above.
WIDGET_UPLOAD_CONFLICT_MESSAGE = (
    "Widget with Name - jinjaEditorWidget Version - 1.1.3 already exists in widget workspace."
)


# ---------------------------------------------------------------------------
# User settings — client.user_settings (GET/PUT/DELETE /api/3/user_settings)
# ---------------------------------------------------------------------------
# Live-verified on 8.0.0 against a real user's ``@settings`` blob: read via
# ``GET /api/3/actors/current``, write/delete via ``/api/3/user_settings/current/<key>``.
# Trimmed to the keys the doctests exercise (``grid/alerts`` and the alerts
# detail-page ``viewTemplate``); the real blob also carries theme/notification/
# playbook-autosave settings, dropped here as noise. The ``viewTemplate`` value
# is a real ``system_view_templates`` uuid captured from the same box.
USER_SETTINGS_VIEW_TEMPLATE_UUID = "d77cd7b5-3e0b-43b5-8c9b-54651dacdebe"

ACTOR_CURRENT_RESPONSE = {
    "@id": "/api/3/people/86f36794-26e8-4049-8e4a-c55388974495",
    "@type": "Person",
    "name": "demo-user",
    "@settings": {
        "grid": {"alerts": {"columns": ["name", "severity"]}},
        "user": {"view": {"details": {"alerts": {"viewTemplate": USER_SETTINGS_VIEW_TEMPLATE_UUID}}}},
    },
}

# PUT /api/3/user_settings/current/<key> echoes the whole, newly-merged
# ``@settings`` blob (not just the written key) — real wire behavior captured
# setting the alerts viewTemplate back to its already-current value.
USER_SETTINGS_PUT_RESPONSE = ACTOR_CURRENT_RESPONSE["@settings"]

# GET /api/3/user_settings/current/<key> returns the value at that key
# directly, unwrapped (a bare string here, not a dict).
USER_SETTINGS_GET_VIEW_TEMPLATE_RESPONSE = USER_SETTINGS_VIEW_TEMPLATE_UUID

# DELETE /api/3/user_settings/current/<key> — 204, empty body, matching the
# live capture (the SDK's ``client.delete`` returns ``None`` for this).

# GET /api/3/system_view_templates — live-verified on 8.0.0: the alerts module's
# real template rows (name/uuid/viewOptions/isDefault/type only; ``config`` —
# the layout body — is dropped, it's not doctest material and can be large).
# ``d77cd7b5-...`` ("CrowdStrike") is the same uuid USER_SETTINGS_VIEW_TEMPLATE_UUID
# points at, so resolve_view_template()/get_view_template_name() round-trip it.
_ALERTS_VIEW_TEMPLATE_ROWS = [
    {
        "uuid": "00e011c1-d777-4313-a21a-0fc24684d710",
        "name": "Default Layout",
        "module": "alerts",
        "viewOptions": "form",
        "isDefault": True,
        "type": "form",
    },
    {
        "uuid": "5aa4bb8b-5580-45c5-b6e8-3cccc8a163ee",
        "name": "Default Layout",
        "module": "alerts",
        "viewOptions": "list",
        "isDefault": True,
        "type": "rows",
    },
    {
        "uuid": "bcfe8c15-5fd5-4d73-af64-ba0cb6c89d73",
        "name": "Default Layout",
        "module": "alerts",
        "viewOptions": "detail",
        "isDefault": True,
        "type": "rows",
    },
    {
        "uuid": USER_SETTINGS_VIEW_TEMPLATE_UUID,
        "name": "CrowdStrike",
        "module": "alerts",
        "viewOptions": "detail",
        "isDefault": False,
        "type": "rows",
    },
]
SYSTEM_VIEW_TEMPLATES_RESPONSE = {
    "@context": "/api/3/contexts/SystemViewTemplate",
    "@id": "/api/3/system_view_templates",
    "@type": "hydra:Collection",
    "hydra:member": _ALERTS_VIEW_TEMPLATE_ROWS,
    "hydra:totalItems": len(_ALERTS_VIEW_TEMPLATE_ROWS),
    "hydra:view": {"@id": "/api/3/system_view_templates", "@type": "hydra:PartialCollectionView"},
}

# ---------------------------------------------------------------------------
# Audit API — client.audit (query and manage audit activity records)
# ---------------------------------------------------------------------------
# Real audit activity endpoints: POST /api/gateway/audit/activities,
# POST /api/gateway/audit/activities/count, GET /api/gateway/audit/activities/{audit_id},
# GET /api/gateway/audit/operations, DELETE /api/gateway/audit/activities/ttl.
# Captured from a live FortiSOAR appliance (8.0.x).

# GET /api/gateway/audit/operations — list of valid operation values.
AUDIT_OPERATIONS_RESPONSE = [
    "login",
    "logout",
    "create",
    "update",
    "delete",
    "view",
    "execute",
    "import",
    "export",
    "publish",
]

# A single audit activity record (``GET /api/gateway/audit/activities/{audit_id}``).
AUDIT_ACTIVITY_RECORD = {
    "id": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "user_id": "5f8ba8e9-10e6-4acc-8b6a-9860f373e1c1",
    "user": "admin",
    "component": "alerts",
    "entity_type": "Alert",
    "entity_id": "9f0eb603-ac1e-41c3-b47b-444589beed39",
    "operation": "create",
    "result": "success",
    "timestamp": 1735149051,
    "details": "Created alert: Test Alert",
}

# POST /api/gateway/audit/activities — a slice of audit records.
AUDIT_ACTIVITIES_RESPONSE = {"content": [AUDIT_ACTIVITY_RECORD]}

# POST /api/gateway/audit/activities/count — audit record count for a time window.
AUDIT_COUNT_RESPONSE = {"count": 42}

# GET /api/version — build version (public).
VERSION_RESPONSE = {"version": "8.0.0-6034"}

# GET /api/permissions/current — caller's effective permissions.
PERMISSIONS_RESPONSE = {
    "alerts": {"create": True, "read": True, "update": True, "delete": False, "execute": True},
    "people": {"create": False, "read": True, "update": False, "delete": False, "execute": False},
}

# GET /api/product/feature-access — license-tier feature-flag map.
FEATURE_ACCESS_RESPONSE = {
    "automation": True,
    "endpoint_management": False,
}

# GET /api/auth/cluster/health — per-node HA cluster health (JWT-auth only).
CLUSTER_HEALTH_RESPONSE = [
    {"node_id": "node-1", "status": "Active", "services": [{"name": "API", "status": "running"}]}
]

# GET /api/auth/license — deployed license state (JWT-auth only).
LICENSE_RESPONSE = {"license_type": "FortiFlex", "max_users": 100}

# GET /api/wf/workflow/config/?section=license — daily action-count license usage.
DAILY_ACTION_COUNT_RESPONSE = {
    "daily_action_limit": 10000,
    "remaining_actions": 8756,
    "reset_time": 1752537600,
    "last_update_time": 1752408000,
}

# GET /api/taxii/1/ — TAXII 2.1 server discovery descriptor.
TAXII_DISCOVERY_RESPONSE = {
    "title": "FortiSOAR TAXII Server",
    "description": "FortiSOAR threat-intel sharing endpoint",
    "default": "/api/taxii/1/collections/",
    "max_content_length": 10485760,
}

# GET /api/taxii/1/collections — available TAXII collections.
TAXII_COLLECTIONS_RESPONSE = {
    "collections": [
        {
            "id": "malware-samples",
            "title": "Malware Samples",
            "can_read": True,
            "can_write": False,
            "media_types": ["application/stix+json;version=2.1"],
        },
        {
            "id": "threat-actors",
            "title": "Threat Actors",
            "can_read": True,
            "can_write": False,
            "media_types": ["application/stix+json;version=2.1"],
        },
    ]
}

# GET /api/taxii/1/collections/malware-samples — single collection metadata.
TAXII_COLLECTION_RESPONSE = {
    "id": "malware-samples",
    "title": "Malware Samples",
    "can_read": True,
    "can_write": False,
    "media_types": ["application/stix+json;version=2.1"],
}

# GET /api/taxii/1/collections/malware-samples/manifest — manifest entries (no bodies).
TAXII_MANIFEST_RESPONSE = {
    "objects": [
        {
            "id": "malware--31b7aa16-6a19-4d5e-9e1a-3a5c9f6a2b40",
            "date_added": "2026-07-01T00:00:00Z",
            "version": "2026-07-01T00:00:00Z",
            "media_type": "application/stix+json;version=2.1",
        }
    ]
}

# GET /api/taxii/1/collections/malware-samples/objects — STIX 2.1 objects envelope.
TAXII_OBJECTS_RESPONSE = {
    "totalItems": 1,
    "objects": [
        {
            "type": "malware",
            "id": "malware--31b7aa16-6a19-4d5e-9e1a-3a5c9f6a2b40",
            "name": "example-malware",
            "is_family": False,
        }
    ],
}

# GET /api/auth/config?section=TOKEN — DAS auth config rows (username/password auth only).
AUTH_CONFIG_TOKEN_ROWS = {
    "hydra:member": [
        {"id": 1, "section": "TOKEN", "key": "idle_time", "dataType": "int", "value": 30},
        {"id": 2, "section": "TOKEN", "key": "token_lifetime", "dataType": "int", "value": 3600},
        {"id": 3, "section": "TOKEN", "key": "max_session", "dataType": "int", "value": 1440},
    ]
}

# POST /api/search — cross-module Elasticsearch text search.
GLOBAL_SEARCH_RESPONSE = {
    "hits": {
        "total": 1,
        "hits": [
            {
                "_index": "alerts",
                "_id": "9f0eb603-ac1e-41c3-b47b-444589beed39",
                "_source": {"name": "Response Capture Test Alert", "severity": "Low"},
            }
        ],
    }
}

# POST /api/query/alerts/<query-uuid> — execute a saved (persisted) query.
PERSISTED_QUERY_RESPONSE = {
    "hydra:member": [ALERT_GET_RESPONSE],
    "hydra:totalItems": 1,
}

# ---------------------------------------------------------------------------
# Feeds API — client.feeds (bulk trigger-bypassing ingest)
# ---------------------------------------------------------------------------
# Bulk feed endpoints return a status envelope with uuids of ingested records.
# Captured from a live 8.0.x appliance. These are used by indicators(),
# observables(), reputation(), threatintel(), and insert().

INDICATORS_INGEST_RESPONSE = {
    "status": "success",
    "uuids": ["550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001"],
}

OBSERVABLES_INGEST_RESPONSE = {
    "status": "success",
    "uuids": ["650e8400-e29b-41d4-a716-446655440002"],
}

REPUTATION_INGEST_RESPONSE = {
    "status": "success",
    "uuids": ["750e8400-e29b-41d4-a716-446655440003", "750e8400-e29b-41d4-a716-446655440004"],
}

THREATINTEL_INGEST_RESPONSE = {
    "status": "success",
    "uuids": ["850e8400-e29b-41d4-a716-446655440005"],
}

# stix_bundle returns a raw dict response (not validated into FeedIngestResult).
STIX_BUNDLE_INGEST_RESPONSE = {
    "status": "success",
    "message": "Bundle ingested successfully",
    "objects_processed": 3,
}

# insert() for arbitrary record types also uses the FeedIngestResult envelope.
INSERT_RECORDS_RESPONSE = {
    "status": "success",
    "uuids": ["950e8400-e29b-41d4-a716-446655440006"],
}

# ---------------------------------------------------------------------------
# API Users — client.api_users (API-key user lifecycle management)
# ---------------------------------------------------------------------------
# API user endpoints return a {"usersresp": [user]} envelope. Captured from a
# live 8.0.x appliance. Used by get(), create(), query(), and lifecycle().

_APIKEY_USER_RESPONSE = {
    "uuid": "550e8400-e29b-41d4-a716-446655440007",
    "user_type": 9,
    "status": 1,
    "access_type": "Named",
    "loginid": "api-user-demo",
    "api_key": {
        "key": "demo-token-abc123def456",
        "retrievable": True,
        "status": "Active",
        "valid_until": 1784000000,
        "time_remaining": 86400,
        "modify_date": 1752400000,
    },
    "bind_name": "api-user-demo",
    "domain": None,
    "is_logged_in": False,
    "tenant": None,
}

APIKEY_USER_CREATE_RESPONSE = {"usersresp": [_APIKEY_USER_RESPONSE]}
APIKEY_USER_GET_RESPONSE = {"usersresp": [_APIKEY_USER_RESPONSE]}
APIKEY_USER_QUERY_RESPONSE = {"usersresp": [_APIKEY_USER_RESPONSE]}
APIKEY_USER_LIFECYCLE_RESPONSE = {"usersresp": [_APIKEY_USER_RESPONSE]}

# ---------------------------------------------------------------------------
# API Keys — client.api_keys (API-key binding management)
# ---------------------------------------------------------------------------
# API key endpoints return /api/3 records with @id, @type, and Hydra collection
# envelopes. Captured from a live 8.0.x appliance. Used by list(), create(),
# get(), and update().

_APIKEY_RECORD = {
    "@context": "/api/3/contexts/ApiKey",
    "@id": "/api/3/api_keys/660e8400-e29b-41d4-a716-446655440008",
    "@type": "ApiKey",
    "name": "api-key-demo",
    "userId": "550e8400-e29b-41d4-a716-446655440007",
    "roles": ["/api/3/roles/00000000-0000-0000-0000-000000000001"],
    "teams": ["/api/3/teams/00000000-0000-0000-0000-000000000002"],
    "uuid": "660e8400-e29b-41d4-a716-446655440008",
    "id": 1,
    "createDate": 1752400000,
    "modifyDate": 1752400000,
}

APIKEY_LIST_RESPONSE = {
    "@context": "/api/3/contexts/ApiKey",
    "@id": "/api/3/api_keys",
    "@type": "hydra:Collection",
    "hydra:member": [_APIKEY_RECORD],
    "hydra:totalItems": 1,
    "hydra:view": {
        "@id": "/api/3/api_keys",
        "@type": "hydra:PartialCollectionView",
    },
}

APIKEY_CREATE_RESPONSE = _APIKEY_RECORD
APIKEY_GET_RESPONSE = _APIKEY_RECORD
APIKEY_UPDATE_RESPONSE = _APIKEY_RECORD

# ---------------------------------------------------------------------------
# Manual Input — client.manual_input (pending manual workflow inputs)
# ---------------------------------------------------------------------------
# Manual input endpoints return hydra collections or single manual input
# records. Captured from a live 8.0.x appliance. Used by list(), retrieve(),
# and resume().

_MANUAL_INPUT_RECORD = {
    "id": 1,
    "workflow": "APA4K8EV6MQ2Q3DJDOQ2H2EQHI______",  # encrypted Fernet token
    "title": "TestStep",
    "type": "input",
    "step_id": 100,
    "step_iri": "/api/wf/api/workflows/1/steps/100",
    "is_approval": False,
    "input": {
        "schema": {
            "type": "object",
            "title": "Test Input",
            "description": "A test input prompt",
            "inputVariables": [
                {
                    "name": "test_var",
                    "type": "string",
                    "label": "Test Variable",
                    "formType": "text",
                    "required": False,
                }
            ],
        }
    },
    "response_mapping": {
        "options": [{"label": "Submit", "step_iri": "/api/wf/api/workflows/1/steps/100", "message": "Input received"}]
    },
}

MANUAL_INPUT_LIST_RESPONSE = {
    "hydra:member": [_MANUAL_INPUT_RECORD],
    "hydra:totalItems": 1,
    "hydra:view": {
        "@id": "/api/wf/api/manual-wf-input/list_wfinput/",
        "@type": "hydra:PartialCollectionView",
    },
}

MANUAL_INPUT_RETRIEVE_RESPONSE = dict(_MANUAL_INPUT_RECORD)
MANUAL_INPUT_RETRIEVE_RESPONSE["workflow"] = 1  # numeric id in retrieve response

MANUAL_INPUT_RESUME_RESPONSE = {
    "task_id": "a2afba58-9dbe-44dd-a6e6-7227e33990db",
    "message": "Awaiting Playbook resumed successfully.",
}

# Approval manual input (with response mapping for approval workflow).
_APPROVAL_MANUAL_INPUT_RECORD = {
    "id": 2,
    "workflow": "CRYPT0K8EV6MQ2Q3DJDOQ2H2EQHI______",  # encrypted Fernet token
    "title": "ApprovalStep",
    "type": "approval",
    "step_id": 200,
    "step_iri": "/api/wf/api/workflows/2/steps/200",
    "is_approval": True,
    "input": {
        "schema": {
            "type": "object",
            "title": "Approval Required",
            "description": "Please approve or reject",
        }
    },
    "response_mapping": {
        "options": [
            {
                "option": "Approve",
                "primary": True,
                "step_iri": "/api/wf/api/workflows/2/steps/200",
                "message": "Approved",
            },
            {
                "option": "Reject",
                "primary": False,
                "step_iri": "/api/wf/api/workflows/2/steps/200",
                "message": "Rejected",
            },
        ]
    },
}

# List response for approval manual input (GET /api/wf/api/manual-wf-input/).
APPROVAL_MANUAL_INPUT_LIST_RESPONSE = {
    "hydra:member": [_APPROVAL_MANUAL_INPUT_RECORD],
    "hydra:totalItems": 1,
    "hydra:view": {
        "@id": "/api/wf/api/manual-wf-input/?workflow=2",
        "@type": "hydra:PartialCollectionView",
    },
}

# Retrieve response for approval manual input.
APPROVAL_MANUAL_INPUT_RETRIEVE_RESPONSE = dict(_APPROVAL_MANUAL_INPUT_RECORD)
APPROVAL_MANUAL_INPUT_RETRIEVE_RESPONSE["workflow"] = 2  # numeric id in retrieve response

# ---------------------------------------------------------------------------
# Attachments — client.attachments (attachment record management)
# ---------------------------------------------------------------------------
# Attachment endpoints return attachment records linking file records.
# Captured from a live 8.0.x appliance. Used by create() operation.

_ATTACHMENT_RECORD = {
    "@context": "/api/3/contexts/Attachment",
    "@id": "/api/3/attachments/770e8400-e29b-41d4-a716-446655440009",
    "@type": "Attachment",
    "name": "report.csv",
    "description": "Daily report",
    "file": {
        "@context": "/api/3/contexts/FileRecord",
        "@id": "/api/3/files/880e8400-e29b-41d4-a716-446655440010",
        "@type": "FileRecord",
        "filename": "report.csv",
        "mimetype": "text/csv",
        "filesize": 1024,
        "uuid": "880e8400-e29b-41d4-a716-446655440010",
        "id": 10,
    },
    "type": "document",
    "uuid": "770e8400-e29b-41d4-a716-446655440009",
    "id": 9,
    "createDate": 1752400000,
    "modifyDate": 1752400000,
}

ATTACHMENT_CREATE_RESPONSE = _ATTACHMENT_RECORD
ATTACHMENT_GET_RESPONSE = _ATTACHMENT_RECORD

# ---------------------------------------------------------------------------
# Solution Packs — client.solution_packs (solution pack management)
# ---------------------------------------------------------------------------
# Solution pack install endpoints return pack records with embedded import
# jobs. Captured from a live 8.0.x appliance. Used by install() operation.

_SOLUTION_PACK_INSTALL_RESPONSE = {
    "@context": "/api/3/contexts/SolutionPack",
    "@id": "/api/3/solutionpacks/990e8400-e29b-41d4-a716-446655440011",
    "@type": "SolutionPack",
    "name": "SOAR Framework",
    "label": "SOAR Framework",
    "version": "2.2.1",
    "type": "solutionpack",
    "description": "Core SOAR framework",
    "installed": True,
    "publisher": "Fortinet",
    "certified": True,
    "importJob": {
        "@id": "/api/3/import_jobs/990e8400-e29b-41d4-a716-446655440012",
        "uuid": "990e8400-e29b-41d4-a716-446655440012",
        "status": "import in progress",
    },
    "uuid": "990e8400-e29b-41d4-a716-446655440011",
    "id": 11,
}

SOLUTION_PACK_INSTALL_RESPONSE = _SOLUTION_PACK_INSTALL_RESPONSE

# ---------------------------------------------------------------------------
# Import Config — client.import_config (configuration import)
# ---------------------------------------------------------------------------
# Import config endpoints handle the configuration export/import lifecycle.
# Captured from a live 8.0.x appliance. Used by import_file() operation.

_IMPORT_JOB_RESPONSE = {
    "@context": "/api/3/contexts/ImportJob",
    "@id": "/api/3/import_jobs/aa0e8400-e29b-41d4-a716-446655440013",
    "@type": "ImportJob",
    "uuid": "aa0e8400-e29b-41d4-a716-446655440013",
    "status": "Import Complete",
    "file": "/api/3/files/880e8400-e29b-41d4-a716-446655440010",
    "options": {},
    "jobUuid": "aa0e8400-e29b-41d4-a716-446655440013",
    "id": 13,
}

IMPORT_JOB_CREATE_RESPONSE = _IMPORT_JOB_RESPONSE
IMPORT_JOB_GET_RESPONSE = _IMPORT_JOB_RESPONSE

# ---------------------------------------------------------------------------
# PlaybooksAPI — client.playbooks (playbook run history and manual input)
# ---------------------------------------------------------------------------
# Playbook run and execution endpoints. Simplified fixtures representing
# typical run states (pending/running/completed). Captured from a live 8.0.x
# appliance; volatile data (user refs, timestamps) masked for doctest stability.

# A single workflow run (execution_history / get_execution response).
_WORKFLOW_RUN_RECORD = {
    "@context": "/api/wf/api/contexts/Workflow",
    "@id": "/wf/api/workflows/1/",
    "@type": "Workflow",
    "uuid": "a0afba58-9dbe-44dd-a6e6-7227e33990db",
    "id": 1,
    "task_id": "a0afba58-9dbe-44dd-a6e6-7227e33990db",
    "name": "Example Playbook",
    "status": "finished",
    "error_message": None,
    "result": {"status": "success"},
    "created": "2026-07-13T10:30:00Z",
    "modified": "2026-07-13T10:35:00Z",
    "env": {"request": {}, "steps": {}},
    "tags": [],
}

# execution_history list response (hydra-formatted collection).
EXECUTION_HISTORY_RESPONSE = {
    "@context": "/api/wf/api/contexts/Workflow",
    "@id": "/api/wf/api/workflows/",
    "@type": "Collection",
    "hydra:member": [_WORKFLOW_RUN_RECORD],
    "hydra:totalItems": 1,
    "hydra:view": {"@id": "/api/wf/api/workflows/?limit=20"},
}

# get_execution single run response (same shape as member in list).
GET_EXECUTION_RESPONSE = _WORKFLOW_RUN_RECORD

# count response (run count envelope).
PLAYBOOK_COUNT_RESPONSE = {
    "count": 42,
}

# log_list response (workflow log query result).
LOG_LIST_RESPONSE = {
    "hydra:member": [
        {
            "@id": "/api/wf/api/workflows/1/",
            "uuid": "a0afba58-9dbe-44dd-a6e6-7227e33990db",
            "task_id": "a0afba58-9dbe-44dd-a6e6-7227e33990db",
            "status": "finished",
            "created": "2026-07-13T10:30:00Z",
        }
    ],
    "hydra:totalItems": 1,
}

# query_logs response (filtered workflow log query result).
QUERY_LOGS_RESPONSE = {
    "hydra:member": [
        {
            "@id": "/api/wf/api/workflows/1/",
            "uuid": "a0afba58-9dbe-44dd-a6e6-7227e33990db",
            "status": "finished",
        }
    ],
    "hydra:totalItems": 1,
}

# render_jinja response (Jinja rendering result).
RENDER_JINJA_RESPONSE = {
    "result": "Rendered Jinja output",
}

# manual_input wfinput_resume response (resume acknowledgement).
WFINPUT_RESUME_RESPONSE = {
    "task_id": "b0afba58-9dbe-44dd-a6e6-7227e33990dc",
    "message": "Awaiting Playbook resumed successfully.",
}

# A run in "awaiting" status (for resume/approval doctests).
_AWAITING_RUN_RECORD = {
    "@context": "/api/wf/api/contexts/Workflow",
    "@id": "/wf/api/workflows/2/",
    "@type": "Workflow",
    "uuid": "b0afba58-9dbe-44dd-a6e6-7227e33990dc",
    "id": 2,
    "task_id": "b0afba58-9dbe-44dd-a6e6-7227e33990dc",
    "name": "Approval Playbook",
    "status": "awaiting",
    "error_message": None,
    "created": "2026-07-13T11:00:00Z",
    "modified": "2026-07-13T11:00:00Z",
}

# get_execution for an awaiting run.
GET_EXECUTION_AWAITING_RESPONSE = _AWAITING_RUN_RECORD

# A run in "failed" status (for retry doctest).
_FAILED_RUN_RECORD = {
    "@context": "/api/wf/api/contexts/Workflow",
    "@id": "/wf/api/workflows/3/",
    "@type": "Workflow",
    "uuid": "c0afba58-9dbe-44dd-a6e6-7227e33990dd",
    "id": 3,
    "task_id": "c0afba58-9dbe-44dd-a6e6-7227e33990dd",
    "name": "Failed Playbook",
    "status": "failed",
    "error_message": "Step failed",
    "created": "2026-07-13T11:30:00Z",
    "modified": "2026-07-13T11:35:00Z",
}

# get_execution for a failed run.
GET_EXECUTION_FAILED_RESPONSE = _FAILED_RUN_RECORD

# start/retry/manual workflow control response (standard workflow post response).
WORKFLOW_CONTROL_RESPONSE = {
    "status": "started",
}

# POST /api/triggers/1/{name} and /api/triggers/1/deferred/{name} — named-webhook trigger.
TRIGGER_BY_NAME_RESPONSE = {"task_id": "c0afba58-9dbe-44dd-a6e6-7227e33990dd"}

# POST /api/triggers/1/action/{route_uuid} — record-context action trigger.
TRIGGER_ACTION_RESPONSE = {"task_id": "c0afba58-9dbe-44dd-a6e6-7227e33990dd"}

# GET /api/3/agents — execution-agent records.
AGENT_RECORD = {
    "@id": "/api/3/agents/6f5e4d3c-2b1a-4c9d-8e7f-1a2b3c4d5e6f",
    "@type": "Agent",
    "uuid": "6f5e4d3c-2b1a-4c9d-8e7f-1a2b3c4d5e6f",
    "agentId": "edge-1",
    "name": "edge-1",
    "active": True,
    "description": "Edge collector",
    "router": "/api/3/routers/3a2b1c0d-9e8f-4a7b-6c5d-4e3f2a1b0c9d",
    "installerType": "/api/3/picklists/d9f874be-3068-4282-9aed-100eba51e61b",
    "configurationHealth": "/api/3/picklists/e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b",
}

AGENT_LIST_RESPONSE = {
    "hydra:member": [AGENT_RECORD],
    "hydra:totalItems": 1,
}

# POST /api/integration/agent-installer/ — install-bundle download (real response is a
# binary .bin; a small placeholder envelope stands in since demo_client() only replays JSON).
AGENT_INSTALLER_BLOB_RESPONSE = {"placeholder": "install-bundle-bytes"}

# POST /api/integration/install-connector/ — register/activate a connector on an agent.
AGENT_INSTALL_CONNECTOR_RESPONSE = {"result": "Success"}

# GET /api/integration/agent-heartbeat/{agent}/ — SME-bus liveness probe.
AGENT_HEARTBEAT_RESPONSE = {"agent": "edge-1", "status": "alive", "latency_ms": 42}

# POST /api/integration/connectors/agents/{name}/{version}/ — per-agent install status.
AGENT_CONNECTOR_INSTALL_STATUS_RESPONSE = [
    {
        "agent": "edge-1",
        "agentId": "edge-1",
        "name": "cyops_utilities",
        "version": "3.7.1",
        "status": "Completed",
        "label": "Utilities",
        "progressPercent": 100,
    }
]


# ---------------------------------------------------------------------------
# Playbook versions (``workflow_versions``) — saved snapshots, the editor's
# "Versions" tab. Captured from a live 8.0 appliance and trimmed: the real
# ``json`` field (a 13KB stringified workflow) is replaced with a small,
# shape-faithful 2-step workflow so doctests stay readable. Field names,
# types, and the ``WorkflowVersion``/``@type`` markers are the live wire.
# ---------------------------------------------------------------------------

# A minimal playbook definition stringified into a snapshot's ``json`` field —
# real shape (steps/routes/triggerStep/groups), synthetic content (no PII).
_PB_VERSION_JSON = _json.dumps(
    {
        "@type": "Workflow",
        "name": "Block IP (test fixture)",
        "description": "Trimmed snapshot fixture for the playbook-versions doctest.",
        "isActive": True,
        "debug": False,
        "remoteExecutableFlag": False,
        "singleRecordExecution": False,
        "synchronous": False,
        "triggerLimit": None,
        "aliasName": None,
        "tag": None,
        "priority": None,
        "parameters": [],
        "triggerStep": "/api/3/workflow_steps/aaaa1111-0000-0000-0000-000000000001",
        "collection": "/api/3/workflow_collections/00000000-0000-0000-0000-000000000010",
        "steps": [
            {
                "uuid": "aaaa1111-0000-0000-0000-000000000001",
                "name": "Manual Trigger",
                "stepType": {"@id": "/api/3/workflow_step_types/manual", "name": "ManualTrigger"},
                "arguments": {},
            },
            {
                "uuid": "bbbb2222-0000-0000-0000-000000000002",
                "name": "Block the IP",
                "stepType": {"@id": "/api/3/workflow_step_types/connector", "name": "Connectors"},
                "arguments": {"connector": "fortigate", "operation": "block_ip"},
            },
        ],
        "routes": [
            {
                "uuid": "rrrr3333-0000-0000-0000-000000000003",
                "sourceStep": "/api/3/workflow_steps/aaaa1111-0000-0000-0000-000000000001",
                "targetStep": "/api/3/workflow_steps/bbbb2222-0000-0000-0000-000000000002",
            }
        ],
        "groups": [],
        "owners": [],
        "versions": [],
    }
)

# The playbook the snapshots below belong to (embedded on each version record
# as ``workflow``, as the live GET/list responses carry it).
_PB_VERSION_WORKFLOW = {
    "@id": "/api/3/workflows/00000000-0000-0000-0000-0000000000aa",
    "@type": "Workflow",
    "name": "Block IP (test fixture)",
    "uuid": "00000000-0000-0000-0000-0000000000aa",
    "isActive": True,
}

# GET /api/3/workflow_versions/<id> — one saved snapshot. Real field set; the
# ``json`` payload is the trimmed ``_PB_VERSION_JSON`` above. ``modifyDate`` is
# an epoch-second float as on the wire.
WORKFLOW_VERSION_GET_RESPONSE = {
    "@context": "/api/3/context/WorkflowVersion",
    "@id": "/api/3/workflow_versions/00000000-0000-0000-0000-000000000001",
    "@type": "WorkflowVersion",
    "id": 1,
    "uuid": "00000000-0000-0000-0000-000000000001",
    "note": "v1",
    "autosave": False,
    "json": _PB_VERSION_JSON,
    "workflow": _PB_VERSION_WORKFLOW,
    "createDate": 1780000000.0,
    "modifyDate": 1780000000.0,
    "createUser": None,
    "modifyUser": None,
}

# A second snapshot of the same playbook with one step changed (``arguments``
# on "Block the IP") — backs the diff_versions doctest's "changed" path.
_PB_VERSION_JSON_2 = _json.loads(_PB_VERSION_JSON)
_PB_VERSION_JSON_2["steps"][1]["arguments"] = {
    "connector": "fortigate",
    "operation": "block_ip",
    "comment": "blocked by SOC",
}
WORKFLOW_VERSION_GET_RESPONSE_2 = dict(WORKFLOW_VERSION_GET_RESPONSE)
WORKFLOW_VERSION_GET_RESPONSE_2 = {
    **WORKFLOW_VERSION_GET_RESPONSE,
    "@id": "/api/3/workflow_versions/00000000-0000-0000-0000-000000000002",
    "uuid": "00000000-0000-0000-0000-000000000002",
    "id": 2,
    "note": "v2",
    "json": _json.dumps(_PB_VERSION_JSON_2),
    "modifyDate": 1780000100.0,
}

# POST /api/3/workflow_versions — create response. The server does NOT echo the
# large ``json`` blob on POST (it returns ``None``); callers re-fetch via GET.
WORKFLOW_VERSION_CREATE_RESPONSE = {
    **WORKFLOW_VERSION_GET_RESPONSE,
    "json": None,
    "note": "v1",
}

# GET /api/3/workflow_versions?workflow=<uuid> — list (newest-first by modifyDate).
WORKFLOW_VERSION_LIST_RESPONSE = {
    "@context": "/api/3/context/WorkflowVersion",
    "@id": "/api/3/workflow_versions",
    "@type": "hydra:Collection",
    "hydra:totalItems": 2,
    "hydra:member": [WORKFLOW_VERSION_GET_RESPONSE_2, WORKFLOW_VERSION_GET_RESPONSE],
}

# The playbook definition the snapshots above belong to. Backs the name-lookup
# (``GET /api/3/workflows?name=...``) and ``get_definition`` (``GET /api/3/
# workflows/<uuid>?$relationships=true``) calls that ``list_versions`` and
# ``create_version`` make. ``uuid`` matches the ``workflow`` IRI on each snapshot.
WORKFLOW_DEFINITION_LIST_RESPONSE = {
    "@context": "/api/3/context/Workflow",
    "@id": "/api/3/workflows",
    "@type": "hydra:Collection",
    "hydra:totalItems": 1,
    "hydra:member": [_PB_VERSION_WORKFLOW],
}

# A definition with steps/routes inlined (relationships=true) — the source
# ``create_version`` stringifies into a snapshot's ``json``. Mirrors the live
# ``preparePlaybookForOverwrite`` input shape (steps as a list).
WORKFLOW_DEFINITION_GET_RESPONSE = {
    **_PB_VERSION_WORKFLOW,
    "@context": "/api/3/context/Workflow",
    "description": "Trimmed snapshot fixture for the playbook-versions doctest.",
    "debug": False,
    "remoteExecutableFlag": False,
    "singleRecordExecution": False,
    "synchronous": False,
    "triggerLimit": None,
    "aliasName": None,
    "tag": None,
    "priority": None,
    "parameters": [],
    "triggerStep": "/api/3/workflow_steps/aaaa1111-0000-0000-0000-000000000001",
    "collection": "/api/3/workflow_collections/00000000-0000-0000-0000-000000000010",
    "steps": _json.loads(_PB_VERSION_JSON)["steps"],
    "routes": _json.loads(_PB_VERSION_JSON)["routes"],
    "groups": [],
    "owners": [],
}

# PUT /api/3/workflows/<uuid> — restore_version's write. Echoes the definition.
WORKFLOW_DEFINITION_PUT_RESPONSE = dict(WORKFLOW_DEFINITION_GET_RESPONSE)

# DELETE /api/3/workflow_versions/<id> — 204 No Content (no body).
WORKFLOW_VERSION_DELETE_RESPONSE = None
