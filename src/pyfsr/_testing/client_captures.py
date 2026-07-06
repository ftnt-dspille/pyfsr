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
