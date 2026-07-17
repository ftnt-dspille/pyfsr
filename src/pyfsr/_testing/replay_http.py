"""Replay HTTP session + ``demo_client()`` for doctests and tests.

:class:`ReplaySession` is a :class:`requests.Session` whose ``request()`` answers
by matching ``(method, path)`` against the recorded ``/api/3`` captures in
:mod:`pyfsr._testing.client_captures` — no sockets, no TLS, no network. It is the
doctest/test analogue of the real ``requests.Session`` a live
:class:`pyfsr.client.FortiSOAR` uses: same call shapes in, same real JSON out.

:func:`demo_client` is the doctest entry point: it builds a
:class:`pyfsr.FortiSOAR` whose ``session`` is a ``ReplaySession``, so guide
examples call ``client.records("alerts").get(uuid)`` and get the real return
shape with zero network. It is the REST-API analogue of
:func:`pyfsr._testing.replay.demo_box`.

See :mod:`pyfsr._testing.client_captures` for provenance and the refresh
workflow.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import TYPE_CHECKING, Any

from requests import Response, Session

from . import client_captures as cap

if TYPE_CHECKING:
    from ..client import FortiSOAR

__all__ = ["ReplaySession", "demo_client"]


# The dispatch table — keyed by (METHOD, path-without-/api/3-prefix...). Paths
# are matched as stored (leading slash stripped, trailing uuid ignored for the
# single-record GET/DELETE so any uuid resolves to the one Alert capture).
def _entry(method: str, path: str, body: Any, status: int = 200) -> tuple[tuple[str, str], dict]:
    return (method.upper(), path.lstrip("/")), {"status": status, "body": body}


_FIXTURES: dict[tuple[str, str], dict] = dict(
    [
        _entry("GET", "/api/3/people", [{"@id": "/api/3/people/3", "@type": "Person"}]),
        _entry("GET", "/api/3/alerts/9f0eb603-ac1e-41c3-b47b-444589beed39", cap.ALERT_GET_RESPONSE),
        _entry("GET", "/api/3/alerts", cap.ALERT_LIST_RESPONSE),
        _entry("POST", "/api/3/alerts", cap.ALERT_CREATE_RESPONSE),
        _entry("PUT", "/api/3/alerts/9f0eb603-ac1e-41c3-b47b-444589beed39", cap.ALERT_GET_RESPONSE),
        _entry("DELETE", "/api/3/alerts/9f0eb603-ac1e-41c3-b47b-444589beed39", {}, status=204),
        _entry("POST", "/api/query/alerts", cap.ALERT_LIST_RESPONSE),
        _entry("POST", "/api/3/bulkupsert/alerts", cap.BULK_UPSERT_ALERTS_MIXED_RESPONSE),
        _entry("POST", "/api/3/upsert/alerts", cap.UPSERT_ALERT_RESPONSE),
        _entry("POST", "/api/3/insert/alerts", cap.BULK_INSERT_ALERTS_RESPONSE),
        # Comments on a record — keyed by the collapsed alert uuid path.
        _entry(
            "GET",
            "/api/3/alerts/9f0eb603-ac1e-41c3-b47b-444589beed39/comments",
            cap.ALERT_COMMENTS_RESPONSE,
        ),
        # Incidents — the generic-record-path example in getting-started.md.
        _entry("GET", "/api/3/incidents/0740411d-e852-4eee-b33b-596210d09a9b", cap.INCIDENT_GET_RESPONSE),
        _entry("POST", "/api/3/incidents", cap.INCIDENT_CREATE_RESPONSE),
        # Connector discovery + health (the connectors guide's read-only calls).
        # healthcheck resolves to one fixture regardless of <name>/<version>.
        _entry("GET", "/api/integration/connectors/", cap.CONNECTORS_LIST_RESPONSE),
        _entry(
            "GET", "/api/integration/connectors/healthcheck/mitre-attack/2.0.2/", cap.CONNECTOR_HEALTHCHECK_RESPONSE
        ),
        # connector_detail: POST /api/integration/connectors/<id>/ — one fixture
        # regardless of which connector id the doctest resolves (id collapsed below).
        _entry("POST", "/api/integration/connectors/3/", cap.CONNECTOR_DETAIL_RESPONSE),
        # execute(): POST /api/integration/execute/ — one fixture regardless of
        # which connector/operation the doctest names (the body varies, the path
        # doesn't; matching ignores the body, same as every other POST fixture here).
        _entry("POST", "/api/integration/execute/", cap.CONNECTOR_EXECUTE_CISA_ADVISORY_RESPONSE),
        # create_configuration / update_configuration / delete_configuration —
        # one fixture regardless of config_id (the body varies, the path shape
        # doesn't, matching this module's convention for other POST/PUT fixtures).
        _entry("POST", "/api/integration/configuration/", cap.CONNECTOR_CREATE_CONFIG_RESPONSE),
        _entry(
            "PUT",
            "/api/integration/configuration/0e75640a-ba4a-4bc2-be41-524a9e47fa3f/",
            cap.CONNECTOR_UPDATE_CONFIG_RESPONSE,
        ),
        _entry("DELETE", "/api/integration/configuration/0e75640a-ba4a-4bc2-be41-524a9e47fa3f/", {}, status=204),
        # FortiAI agentic investigation — start (POST /api/ai/triage/alert), then
        # poll status + result by task_id. The task_id in the start response is
        # the recorded one, so a doctest that passes ``started["task_id"]`` through
        # to get_investigation_result resolves directly; the canonicalization below
        # also collapses any task_id so a doctest with a different id still hits.
        _entry("POST", "/api/ai/triage/alert", cap.FORTIAI_START_RESPONSE),
        _entry("GET", f"/api/ai/agents/{cap.FORTIAI_TASK_ID}/status", cap.FORTIAI_STATUS_RESPONSE),
        _entry("GET", f"/api/ai/agents/{cap.FORTIAI_TASK_ID}/result", cap.FORTIAI_RESULT_RESPONSE),
        # Module-admin (staging/published schema) read-only envelopes. The two
        # lists are hit by ``list_modules``/``describe_module``/``pending_changes``
        # (query string ignored, so the with-relationships list serves all three);
        # the single-record GETs back ``get_staging``/``get_published``/``get_field``.
        _entry("GET", "/api/3/staging_model_metadatas", cap.STAGING_MODULES_LIST_RESPONSE),
        _entry("POST", "/api/3/staging_model_metadatas", cap.MODULE_CREATE_STAGING_RESPONSE, status=201),
        _entry("GET", "/api/3/model_metadatas", cap.PUBLISHED_MODULES_LIST_RESPONSE),
        _entry(
            "GET",
            "/api/3/staging_model_metadatas/7fdae59c-7de7-43d9-bf2a-dc2f00ed25b4",
            cap.STAGING_ALERTS_RESPONSE,
        ),
        _entry(
            "GET",
            "/api/3/model_metadatas/7fdae59c-7de7-43d9-bf2a-dc2f00ed25b4",
            cap.PUBLISHED_ALERTS_RESPONSE,
        ),
        _entry("GET", "/api/publish/error", cap.PUBLISH_ERROR_RESPONSE),
        # Picklists — the two bulk calls ``_load_bulk`` makes (names + flat items).
        _entry("GET", "/api/3/picklist_names", cap.PICKLIST_NAMES_RESPONSE),
        _entry("GET", "/api/3/picklists", cap.PICKLISTS_RESPONSE),
        # Widgets — list, upload (solutionpacks/install with $type=widget), the
        # dev-manifest GET publish() reads, and the publish PUT response.
        _entry("GET", "/api/3/widgets", cap.WIDGET_LIST_RESPONSE),
        _entry("POST", "/api/3/solutionpacks/install", cap.WIDGET_UPLOAD_RESPONSE),
        _entry(
            "GET",
            "/api/3/widgets/development/5fef77ad-8917-40c6-82a2-fdd753bdf41c",
            cap.WIDGET_DEV_MANIFEST_RESPONSE,
        ),
        _entry("PUT", "/api/3/widgets/5fef77ad-8917-40c6-82a2-fdd753bdf41c", cap.WIDGET_PUBLISH_RESPONSE),
        # User settings — actors/current backs all()/get(); the /current/<key>
        # path backs get_direct()/set()/delete() (and the view-template
        # convenience wrappers built on top of them).
        _entry("GET", "/api/3/actors/current", cap.ACTOR_CURRENT_RESPONSE),
        _entry(
            "PUT",
            "/api/3/user_settings/current/user/view/details/alerts/viewTemplate",
            cap.USER_SETTINGS_PUT_RESPONSE,
        ),
        _entry(
            "GET",
            "/api/3/user_settings/current/user/view/details/alerts/viewTemplate",
            cap.USER_SETTINGS_GET_VIEW_TEMPLATE_RESPONSE,
        ),
        _entry(
            "DELETE",
            "/api/3/user_settings/current/user/view/details/alerts/viewTemplate",
            None,
            status=204,
        ),
        # ViewTemplatesAPI.list_templates — backs resolve_view_template() /
        # get_view_template_name() / set_view_template(module, <name>).
        _entry("GET", "/api/3/system_view_templates", cap.SYSTEM_VIEW_TEMPLATES_RESPONSE),
        # Audit API — query and manage audit activity records.
        _entry("GET", "/api/gateway/audit/operations", cap.AUDIT_OPERATIONS_RESPONSE),
        _entry(
            "GET",
            "/api/gateway/audit/activities/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            cap.AUDIT_ACTIVITY_RECORD,
        ),
        _entry("POST", "/api/gateway/audit/activities", cap.AUDIT_ACTIVITIES_RESPONSE),
        _entry("POST", "/api/gateway/audit/activities/count", cap.AUDIT_COUNT_RESPONSE),
        _entry("DELETE", "/api/gateway/audit/activities/ttl", {}, status=204),
        _entry("DELETE", "/api/gateway/audit/activities", {}, status=204),
        # SystemAPI — version/permissions/feature-access/daily-action-count.
        _entry("GET", "/api/version", cap.VERSION_RESPONSE),
        _entry("GET", "/api/permissions/current", cap.PERMISSIONS_RESPONSE),
        _entry("GET", "/api/product/feature-access", cap.FEATURE_ACCESS_RESPONSE),
        _entry("GET", "/api/auth/cluster/health", cap.CLUSTER_HEALTH_RESPONSE),
        _entry("GET", "/api/auth/license", cap.LICENSE_RESPONSE),
        _entry("GET", "/api/wf/workflow/config/", cap.DAILY_ACTION_COUNT_RESPONSE),
        # TaxiiAPI — discovery/collections/manifest/objects.
        _entry("GET", "/api/taxii/1/", cap.TAXII_DISCOVERY_RESPONSE),
        _entry("GET", "/api/taxii/1/collections", cap.TAXII_COLLECTIONS_RESPONSE),
        _entry("GET", "/api/taxii/1/collections/malware-samples", cap.TAXII_COLLECTION_RESPONSE),
        _entry("GET", "/api/taxii/1/collections/malware-samples/manifest", cap.TAXII_MANIFEST_RESPONSE),
        _entry("GET", "/api/taxii/1/collections/malware-samples/objects", cap.TAXII_OBJECTS_RESPONSE),
        _entry(
            "GET",
            "/api/taxii/1/collections/malware-samples/objects/malware--31b7aa16-6a19-4d5e-9e1a-3a5c9f6a2b40",
            cap.TAXII_OBJECTS_RESPONSE,
        ),
        # AuthConfigAPI — username/password auth only, all doctests are +SKIP.
        _entry("GET", "/api/auth/config", cap.AUTH_CONFIG_TOKEN_ROWS),
        # SearchAPI — global search + persisted-query execution.
        _entry("POST", "/api/search", cap.GLOBAL_SEARCH_RESPONSE),
        _entry(
            "POST",
            "/api/query/alerts/6f1c9e2a-6b7a-4b0a-9a1e-2f6a5c9b3d10",
            cap.PERSISTED_QUERY_RESPONSE,
        ),
        # FeedsAPI — bulk trigger-bypassing ingest for threat-intel + records.
        _entry("POST", "/api/ingest-feeds/indicators", cap.INDICATORS_INGEST_RESPONSE),
        _entry("POST", "/api/ingest-feeds/observables", cap.OBSERVABLES_INGEST_RESPONSE),
        _entry("POST", "/api/ingest-feeds/reputation", cap.REPUTATION_INGEST_RESPONSE),
        _entry("POST", "/api/ingest-feeds/threatintel", cap.THREATINTEL_INGEST_RESPONSE),
        _entry("POST", "/api/ingest-feeds/stix-bundle", cap.STIX_BUNDLE_INGEST_RESPONSE),
        # insert() for arbitrary record types (e.g., /api/ingest-feeds/alerts)
        # resolves to the same fixture regardless of record_type (the body varies,
        # the path structure is matched generically).
        _entry("POST", "/api/ingest-feeds/alerts", cap.INSERT_RECORDS_RESPONSE),
        # ApiKeyUsersAPI — API-key user lifecycle management.
        _entry("POST", "/api/auth/users", cap.APIKEY_USER_CREATE_RESPONSE),
        _entry("GET", "/api/auth/users", cap.APIKEY_USER_GET_RESPONSE),
        _entry("POST", "/api/auth/query/users", cap.APIKEY_USER_QUERY_RESPONSE),
        _entry("PUT", "/api/auth/users", cap.APIKEY_USER_LIFECYCLE_RESPONSE),
        # ApiKeysAPI — API-key binding (roles/teams on a user).
        # System queries (datasets) — client.system_queries
        _entry("GET", "/api/3/system_queries", cap.SYSTEM_QUERY_LIST_RESPONSE),
        _entry("POST", "/api/3/system_queries", cap.SYSTEM_QUERY_CREATE_RESPONSE),
        _entry(
            "GET",
            "/api/3/system_queries/7d245801-38d7-4400-9453-7bf7c42b7353",
            cap.SYSTEM_QUERY_GET_RESPONSE,
        ),
        _entry(
            "PUT",
            "/api/3/system_queries/7d245801-38d7-4400-9453-7bf7c42b7353",
            cap.SYSTEM_QUERY_GET_RESPONSE,
        ),
        _entry(
            "DELETE",
            "/api/3/system_queries/7d245801-38d7-4400-9453-7bf7c42b7353",
            {},
            status=204,
        ),
        _entry("GET", "/api/3/api_keys", cap.APIKEY_LIST_RESPONSE),
        _entry("POST", "/api/3/api_keys", cap.APIKEY_CREATE_RESPONSE),
        # get() / update() / delete() for any api_key uuid — collapse to recorded uuid.
        _entry("GET", "/api/3/api_keys/660e8400-e29b-41d4-a716-446655440008", cap.APIKEY_GET_RESPONSE),
        _entry("PUT", "/api/3/api_keys/660e8400-e29b-41d4-a716-446655440008", cap.APIKEY_UPDATE_RESPONSE),
        _entry("DELETE", "/api/3/api_keys/660e8400-e29b-41d4-a716-446655440008", {}, status=204),
        # ManualInputAPI — pending manual workflow inputs.
        _entry("POST", "/api/wf/api/manual-wf-input/list_wfinput/", cap.MANUAL_INPUT_LIST_RESPONSE),
        _entry("POST", "/api/wf/api/manual-wf-input/1/retrieve_wfinput/", cap.MANUAL_INPUT_RETRIEVE_RESPONSE),
        _entry("POST", "/api/wf/api/workflows/1/wfinput_resume/", cap.MANUAL_INPUT_RESUME_RESPONSE),
        # AttachmentsAPI — attachment record management.
        _entry("POST", "/api/3/attachments", cap.ATTACHMENT_CREATE_RESPONSE),
        _entry("GET", "/api/3/attachments/770e8400-e29b-41d4-a716-446655440009", cap.ATTACHMENT_GET_RESPONSE),
        _entry("DELETE", "/api/3/attachments/770e8400-e29b-41d4-a716-446655440009", {}, status=204),
        # SolutionPackAPI — solution pack management.
        _entry("POST", "/api/3/solutionpacks/install", cap.SOLUTION_PACK_INSTALL_RESPONSE),
        # ImportConfigAPI — configuration import management.
        _entry("POST", "/api/3/import_jobs", cap.IMPORT_JOB_CREATE_RESPONSE),
        _entry("GET", "/api/3/import_jobs/aa0e8400-e29b-41d4-a716-446655440013", cap.IMPORT_JOB_GET_RESPONSE),
        _entry("GET", "/api/import/aa0e8400-e29b-41d4-a716-446655440013", {"status": "generating"}),
        # PlaybooksAPI — playbook run history and manual input management.
        _entry("GET", "/api/wf/api/workflows/", cap.EXECUTION_HISTORY_RESPONSE),
        # get_execution — single run (any pk resolves to one of the run records).
        _entry("GET", "/api/wf/api/workflows/1/", cap.GET_EXECUTION_RESPONSE),
        _entry("GET", "/api/wf/api/workflows/2/", cap.GET_EXECUTION_AWAITING_RESPONSE),
        _entry("GET", "/api/wf/api/workflows/3/", cap.GET_EXECUTION_FAILED_RESPONSE),
        _entry("GET", "/api/wf/api/workflows/count/", cap.PLAYBOOK_COUNT_RESPONSE),
        _entry("POST", "/api/wf/api/workflows/log_list/", cap.LOG_LIST_RESPONSE),
        _entry("POST", "/api/wf/api/query/workflow_logs/", cap.QUERY_LOGS_RESPONSE),
        _entry("POST", "/api/wf/api/jinja-editor/", cap.RENDER_JINJA_RESPONSE),
        # start/retry on any workflow pk.
        _entry("POST", "/api/wf/api/workflows/1/start/", cap.WORKFLOW_CONTROL_RESPONSE),
        _entry("POST", "/api/wf/api/workflows/3/retry/", cap.WORKFLOW_CONTROL_RESPONSE),
        # wfinput_resume — resume response on any workflow pk.
        _entry("POST", "/api/wf/api/workflows/2/wfinput_resume/", cap.WFINPUT_RESUME_RESPONSE),
        # Manual input list (GET for approval workflows).
        _entry("GET", "/api/wf/api/manual-wf-input/", cap.APPROVAL_MANUAL_INPUT_LIST_RESPONSE),
        # Approval manual input retrieve.
        _entry("POST", "/api/wf/api/manual-wf-input/2/retrieve_wfinput/", cap.APPROVAL_MANUAL_INPUT_RETRIEVE_RESPONSE),
        # Named/action triggers — any name/route_uuid resolves to the same fixture.
        _entry("POST", "/api/triggers/1/my-hook", cap.TRIGGER_BY_NAME_RESPONSE),
        _entry("POST", "/api/triggers/1/deferred/my-hook", cap.TRIGGER_BY_NAME_RESPONSE),
        _entry(
            "POST",
            "/api/triggers/1/action/2b6a1e8e-6f0a-4c6b-9e29-6c2f6a1d8b30",
            cap.TRIGGER_ACTION_RESPONSE,
        ),
        # Playbook versions (workflow_versions snapshots) — the editor's "Versions"
        # tab. list (bare collection), get/create/delete on <id> (collapsed below so
        # any version uuid resolves to the v1 fixture; the diff doctest needs v2, so
        # a specific second uuid is pinned). list_versions resolves the playbook by
        # name first via the workflows?name= capture above.
        _entry("GET", "/api/3/workflow_versions", cap.WORKFLOW_VERSION_LIST_RESPONSE),
        _entry("POST", "/api/3/workflow_versions", cap.WORKFLOW_VERSION_CREATE_RESPONSE),
        _entry("DELETE", "/api/3/workflow_versions/00000000-0000-0000-0000-000000000001", None, status=204),
        # The diff doctest fetches two distinct versions; pin v1 + v2 by uuid.
        _entry(
            "GET",
            "/api/3/workflow_versions/00000000-0000-0000-0000-000000000001",
            cap.WORKFLOW_VERSION_GET_RESPONSE,
        ),
        _entry(
            "GET",
            "/api/3/workflow_versions/00000000-0000-0000-0000-000000000002",
            cap.WORKFLOW_VERSION_GET_RESPONSE_2,
        ),
        # The fixture playbook's definition — backs list_versions' name lookup
        # (GET /api/3/workflows?name=...) and create_version's get_definition
        # (GET /api/3/workflows/<uuid>?$relationships=true, collapsed below to
        # the fixture uuid), plus restore_version's PUT.
        _entry("GET", "/api/3/workflows", cap.WORKFLOW_DEFINITION_LIST_RESPONSE),
        _entry(
            "GET",
            "/api/3/workflows/00000000-0000-0000-0000-0000000000aa",
            cap.WORKFLOW_DEFINITION_GET_RESPONSE,
        ),
        _entry(
            "PUT",
            "/api/3/workflows/00000000-0000-0000-0000-0000000000aa",
            cap.WORKFLOW_DEFINITION_PUT_RESPONSE,
        ),
        # AgentsAPI — execution-agent lifecycle + installer + agent-scoped connectors.
        _entry("GET", "/api/3/agents", cap.AGENT_LIST_RESPONSE),
        _entry("GET", "/api/3/agents/6f5e4d3c-2b1a-4c9d-8e7f-1a2b3c4d5e6f", cap.AGENT_RECORD),
        _entry("POST", "/api/3/agents", cap.AGENT_RECORD),
        _entry("DELETE", "/api/3/agents/6f5e4d3c-2b1a-4c9d-8e7f-1a2b3c4d5e6f", {}, status=204),
        _entry("POST", "/api/integration/agent-installer/", cap.AGENT_INSTALLER_BLOB_RESPONSE),
        _entry("POST", "/api/integration/install-connector/", cap.AGENT_INSTALL_CONNECTOR_RESPONSE),
        _entry("PUT", "/api/integration/install-connector/", cap.AGENT_INSTALL_CONNECTOR_RESPONSE),
        _entry("DELETE", "/api/integration/install-connector/", cap.AGENT_INSTALL_CONNECTOR_RESPONSE),
        _entry("GET", "/api/integration/agent-heartbeat/edge-1/", cap.AGENT_HEARTBEAT_RESPONSE),
        _entry(
            "POST",
            "/api/integration/connectors/agents/cyops_utilities/3.7.1/",
            cap.AGENT_CONNECTOR_INSTALL_STATUS_RESPONSE,
        ),
    ]
)


def _build_response(fixture: dict, url: str) -> Response:
    """Build a :class:`requests.Response` from a recorded fixture."""
    resp = Response()
    resp.status_code = fixture["status"]
    resp.url = url
    resp.headers["Content-Type"] = "application/json"
    body = fixture.get("body")
    raw = b"" if body in (None, b"") else json.dumps(body).encode()
    resp._content = raw
    resp.encoding = "utf-8"
    return resp


def _path_and_match(method: str, url: str) -> tuple[str, str]:
    """Return the (canonical-path, raw-path) used for fixture lookup.

    ``canonical`` collapses volatile path segments to the recorded values so a
    doctest can call with any plausible id and still hit the one capture:

    - A single-record alerts path ``/api/3/alerts/<uuid>`` collapses the uuid
      to the recorded one (so ``.get("<any-uuid>")`` resolves). The bare
      collection ``/api/3/alerts`` is left alone so ``list()`` resolves to the
      collection capture, not the single-record one.
    - A connector healthcheck path
      ``/api/integration/connectors/healthcheck/<name>/<version>/`` collapses to
      the recorded ``mitre-attack/2.0.2`` (so ``healthcheck("mitre-attack")``
      resolves regardless of which connector the doctest names).

    ``raw`` is the literal path for the no-match error message.
    """
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    segments = path.rstrip("/").split("/")
    # /api/3/alerts/<uuid>/comments  ->  collapse the uuid to the recorded one.
    # Must precede the 5-segment single-record rule below.
    if (
        len(segments) == 6
        and segments[1] == "api"
        and segments[2] == "3"
        and segments[3] == "alerts"
        and segments[5] == "comments"
    ):
        return "/api/3/alerts/9f0eb603-ac1e-41c3-b47b-444589beed39/comments", path
    # /api/3/alerts/<uuid>  ->  collapse the uuid to the recorded one.
    if len(segments) == 5 and segments[1] == "api" and segments[2] == "3" and segments[3] == "alerts":
        return "/api/3/alerts/9f0eb603-ac1e-41c3-b47b-444589beed39", path
    # /api/3/staging_model_metadatas/<uuid> or /api/3/model_metadatas/<uuid>  ->
    # collapse to alerts' recorded uuid (so ``get_staging``/``get_published``/
    # ``get_field`` resolve regardless of which module the doctest names). The
    # bare collections (4 segments) are left alone so the list capture resolves.
    if (
        len(segments) == 5
        and segments[1] == "api"
        and segments[2] == "3"
        and segments[3] in ("staging_model_metadatas", "model_metadatas")
    ):
        return f"/api/3/{segments[3]}/7fdae59c-7de7-43d9-bf2a-dc2f00ed25b4", path
    # /api/3/widgets/development/<uuid>  ->  collapse to the recorded uuid (so
    # publish() resolves regardless of which uuid upload() returned).
    if len(segments) == 6 and segments[1] == "api" and segments[3] == "widgets" and segments[4] == "development":
        return "/api/3/widgets/development/5fef77ad-8917-40c6-82a2-fdd753bdf41c", path
    # /api/3/widgets/<uuid>  (publish PUT / remove DELETE)  ->  collapse to the
    # recorded uuid. The bare collection (4 segments) is left alone so list()
    # resolves to the list capture, not this one.
    if len(segments) == 5 and segments[1] == "api" and segments[2] == "3" and segments[3] == "widgets":
        return "/api/3/widgets/5fef77ad-8917-40c6-82a2-fdd753bdf41c", path
    # /api/integration/connectors/healthcheck/<name>/<version>/  ->  recorded.
    # segments: ['', 'api', 'integration', 'connectors', 'healthcheck', name, version]
    if len(segments) == 7 and segments[1] == "api" and segments[3] == "connectors" and segments[4] == "healthcheck":
        return "/api/integration/connectors/healthcheck/mitre-attack/2.0.2/", path
    # /api/integration/connectors/<id>/  (connector_detail POST)  ->  recorded.
    # segments: ['', 'api', 'integration', 'connectors', id, '']
    if len(segments) == 6 and segments[1] == "api" and segments[2] == "integration" and segments[3] == "connectors":
        return "/api/integration/connectors/3/", path
    # /api/ai/agents/<task_id>/{status,result}  ->  collapse the task_id to the
    # recorded one (so get_investigation_result resolves regardless of which
    # task_id the start response returned). segments: ['', 'api', 'ai', 'agents', task_id, ep]
    if (
        len(segments) == 6
        and segments[1] == "api"
        and segments[2] == "ai"
        and segments[3] == "agents"
        and segments[5] in ("status", "result")
    ):
        return f"/api/ai/agents/{cap.FORTIAI_TASK_ID}/{segments[5]}", path
    # /api/3/api_keys/<uuid>  (get / update / delete)  ->  collapse to the
    # recorded uuid. The bare collection (4 segments) is left alone so list()
    # resolves to the list capture, not this one.
    if len(segments) == 5 and segments[1] == "api" and segments[2] == "3" and segments[3] == "api_keys":
        return "/api/3/api_keys/660e8400-e29b-41d4-a716-446655440008", path
    # /api/3/attachments/<uuid>  (get / delete)  ->  collapse to the recorded uuid.
    if len(segments) == 5 and segments[1] == "api" and segments[2] == "3" and segments[3] == "attachments":
        return "/api/3/attachments/770e8400-e29b-41d4-a716-446655440009", path
    # /api/3/workflows/<uuid>  (get_definition / restore PUT)  ->  collapse to the
    # fixture playbook's uuid. The bare collection (4 segments) is left alone so
    # the name-lookup list capture resolves.
    if len(segments) == 5 and segments[1] == "api" and segments[2] == "3" and segments[3] == "workflows":
        return "/api/3/workflows/00000000-0000-0000-0000-0000000000aa", path
    # /api/3/workflow_versions/<uuid>  (get / delete)  ->  collapse to the v1
    # fixture uuid, EXCEPT the pinned v2 uuid (the diff doctest needs it distinct).
    # The bare collection (4 segments) is left alone so list_versions resolves.
    if (
        len(segments) == 5
        and segments[1] == "api"
        and segments[2] == "3"
        and segments[3] == "workflow_versions"
        and segments[4] != "00000000-0000-0000-0000-000000000002"
    ):
        return "/api/3/workflow_versions/00000000-0000-0000-0000-000000000001", path
    # /api/3/import_jobs/<uuid>  (get / put)  ->  collapse to the recorded uuid.
    if len(segments) == 5 and segments[1] == "api" and segments[2] == "3" and segments[3] == "import_jobs":
        return "/api/3/import_jobs/aa0e8400-e29b-41d4-a716-446655440013", path
    # /api/import/<uuid>  (generate options / trigger)  ->  collapse to the recorded uuid.
    if len(segments) == 4 and segments[1] == "api" and segments[2] == "import":
        return "/api/import/aa0e8400-e29b-41d4-a716-446655440013", path
    return path, path


class ReplaySession(Session):
    """A :class:`requests.Session` that replays recorded ``/api/3`` captures.

    Every ``request()`` is matched by ``(METHOD, path)`` to a fixture in
    :mod:`pyfsr._testing.client_captures`; a miss raises ``RuntimeError`` so a
    doctest author immediately sees which capture to add. Query-string params are
    ignored for matching (the captures are representative, not per-filter), so a
    list with a filter and a plain list both resolve to the collection capture.

    ``overrides`` scopes extra (or replacement) fixtures to **this session only**:
    a ``{(METHOD, path): fixture}`` mapping consulted before the module-global
    ``_FIXTURES`` table. It lets one doctest demonstrate a stateful flow (e.g. a
    module staged but not yet published, so ``pending_changes()`` reports it)
    without editing the shared table and perturbing every other doctest that
    reads the same collection. The global table is never mutated.
    """

    def __init__(self, overrides: dict[tuple[str, str], dict] | None = None) -> None:
        super().__init__()
        self._overrides: dict[tuple[str, str], dict] = dict(overrides or {})

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Response:  # type: ignore[override]
        canonical, raw = _path_and_match(method, url)
        key = (str(method).upper(), canonical.lstrip("/"))
        fixture = self._overrides.get(key)
        if fixture is None:
            fixture = _FIXTURES.get(key)
        if fixture is None:
            raise RuntimeError(
                f"[demo_client] no replay fixture for {method.upper()} {raw!r} "
                f"(looked up as {key}). Add an entry in "
                "src/pyfsr/_testing/replay_http._FIXTURES (and a capture in "
                "pyfsr._testing.client_captures)."
            )
        return _build_response(fixture, url)


def demo_client(
    *,
    base_url: str = "https://demo.fortisoar.example",
    token: str = "demo-token",
    overrides: dict[tuple[str, str], dict] | None = None,
) -> FortiSOAR:
    """Return a :class:`pyfsr.FortiSOAR` wired to a replay REST session.

    The doctest entry point: guides and docstrings call ``client = demo_client()``
    and get real return shapes with zero network. The session is a
    :class:`ReplaySession` seeded from :mod:`pyfsr._testing.client_captures`.

    ``overrides`` is passed through to :class:`ReplaySession` — a per-call
    ``{(METHOD, path): fixture}`` overlay for doctests that need a scoped,
    stateful view (a staged-but-unpublished module, say) without touching the
    shared fixture table.

    Construction uses **token auth**: ``APIKeyAuth.__init__`` validates the key
    with a live ``GET /api/3/people``, so ``demo_client`` briefly neutralises
    that one validation call (it would otherwise hit the network before the
    replay session is installed). The neutralisation is scoped to construction
    only; once the replay session is swapped in, every subsequent call —
    including the validation GET — replays from fixtures.
    """
    from ..auth.api_key import APIKeyAuth
    from ..client import FortiSOAR

    orig_validate = APIKeyAuth._validate_api_key
    APIKeyAuth._validate_api_key = lambda self: None  # type: ignore[method-assign]
    try:
        client = FortiSOAR(base_url=base_url, token=token, verify_ssl=False, suppress_insecure_warnings=True)
    finally:
        APIKeyAuth._validate_api_key = orig_validate  # type: ignore[method-assign]

    # Swap the live session for the replay session and re-apply auth headers so
    # the client's request path (self.session.request + .headers) is coherent.
    replay = ReplaySession(overrides=overrides)
    replay.verify = False
    replay.headers.update(client.auth.get_auth_headers())
    client.session = replay
    return client
