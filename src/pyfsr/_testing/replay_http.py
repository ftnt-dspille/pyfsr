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
        # Connector discovery + health (the connectors guide's read-only calls).
        # healthcheck resolves to one fixture regardless of <name>/<version>.
        _entry("GET", "/api/integration/connectors/", cap.CONNECTORS_LIST_RESPONSE),
        _entry(
            "GET", "/api/integration/connectors/healthcheck/mitre-attack/2.0.2/", cap.CONNECTOR_HEALTHCHECK_RESPONSE
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
    # /api/3/alerts/<uuid>  ->  collapse the uuid to the recorded one.
    if len(segments) == 5 and segments[1] == "api" and segments[2] == "3" and segments[3] == "alerts":
        return "/api/3/alerts/9f0eb603-ac1e-41c3-b47b-444589beed39", path
    # /api/integration/connectors/healthcheck/<name>/<version>/  ->  recorded.
    # segments: ['', 'api', 'integration', 'connectors', 'healthcheck', name, version]
    if len(segments) == 7 and segments[1] == "api" and segments[3] == "connectors" and segments[4] == "healthcheck":
        return "/api/integration/connectors/healthcheck/mitre-attack/2.0.2/", path
    return path, path


class ReplaySession(Session):
    """A :class:`requests.Session` that replays recorded ``/api/3`` captures.

    Every ``request()`` is matched by ``(METHOD, path)`` to a fixture in
    :mod:`pyfsr._testing.client_captures`; a miss raises ``RuntimeError`` so a
    doctest author immediately sees which capture to add. Query-string params are
    ignored for matching (the captures are representative, not per-filter), so a
    list with a filter and a plain list both resolve to the collection capture.
    """

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Response:  # type: ignore[override]
        canonical, raw = _path_and_match(method, url)
        key = (str(method).upper(), canonical.lstrip("/"))
        fixture = _FIXTURES.get(key)
        if fixture is None:
            raise RuntimeError(
                f"[demo_client] no replay fixture for {method.upper()} {raw!r} "
                f"(looked up as {key}). Add an entry in "
                "src/pyfsr/_testing/replay_http._FIXTURES (and a capture in "
                "pyfsr._testing.client_captures)."
            )
        return _build_response(fixture, url)


def demo_client(*, base_url: str = "https://demo.fortisoar.example", token: str = "demo-token") -> FortiSOAR:
    """Return a :class:`pyfsr.FortiSOAR` wired to a replay REST session.

    The doctest entry point: guides and docstrings call ``client = demo_client()``
    and get real return shapes with zero network. The session is a
    :class:`ReplaySession` seeded from :mod:`pyfsr._testing.client_captures`.

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
    replay = ReplaySession()
    replay.verify = False
    replay.headers.update(client.auth.get_auth_headers())
    client.session = replay
    return client
