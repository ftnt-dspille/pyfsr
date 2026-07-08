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
