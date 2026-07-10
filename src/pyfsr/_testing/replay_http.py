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
