"""Unit tests for ContentHubSearch.connector_versions."""

import pytest
import requests

from pyfsr.api.content_hub import _REPO_BASE, _REPO_HOST, ContentHubSearch

# ---------------------------------------------------------------------------
# Minimal fake client
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self, search_members):
        self._members = search_members
        self.calls = []

    def post(self, endpoint, data=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"hydra:member": self._members, "hydra:totalItems": len(self._members)}

    def get(self, endpoint, params=None, **kw):  # pragma: no cover
        self.calls.append(("GET", endpoint, params))
        return {}


def _api(members):
    client = FakeClient(members)
    return ContentHubSearch(client), client


# ---------------------------------------------------------------------------
# requests.get stub
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json = json_data or {}
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class _GetRecorder:
    """Captures requests.get calls and returns a canned response."""

    def __init__(self, json_data=None, status_code=200):
        self._json = json_data
        self._status = status_code
        self.called = False
        self.last_url = None

    def __call__(self, url, *args, **kwargs):
        self.called = True
        self.last_url = url
        return _FakeResponse(self._json, self._status)


def _patch_get(monkeypatch, json_data=None, status_code=200):
    recorder = _GetRecorder(json_data, status_code)
    monkeypatch.setattr(requests, "get", recorder)
    return recorder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_INSTALLED_RECORD = {
    "name": "code-snippet",
    "version": "2.1.4",
    "type": "connector",
    "installed": True,
    "local": False,
    "latestAvailableVersion": "2.2.1",
    "infoPath": "/content-hub/code-snippet-2.1.4/9000",
}

_AVAILABLE_RECORD = {
    "name": "code-snippet",
    "version": "2.2.1",
    "type": "connector",
    "installed": False,
    "local": False,
    "latestAvailableVersion": None,
    "infoPath": "/content-hub/code-snippet-2.2.1/11354",
}

_LOCAL_ONLY_RECORD = {
    "name": "code-snippet",
    "version": "2.1.4_dev",
    "type": "connector",
    "installed": False,
    "local": True,
    "latestAvailableVersion": None,
    "infoPath": None,
}

_INFO_JSON = {
    "name": "code-snippet",
    "version": "2.2.1",
    "availableVersions": ["2.1.4", "2.1.5", "2.2.0", "2.2.1"],
    "operations": [{"operation": "python_inline_code_editor"}],
}


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


def test_uses_latest_available_version_to_build_url(monkeypatch):
    """When latestAvailableVersion is set, URL uses that version not infoPath."""
    api, _ = _api([_INSTALLED_RECORD])
    rec = _patch_get(monkeypatch, _INFO_JSON)

    result = api.connector_versions("code-snippet")

    assert result["version"] == "2.2.1"
    assert rec.called
    assert rec.last_url == f"{_REPO_BASE}/code-snippet-2.2.1/latest/info.json"


def test_falls_back_to_info_path_when_no_latest_available(monkeypatch):
    """When latestAvailableVersion is absent, derives URL from infoPath."""
    api, _ = _api([_AVAILABLE_RECORD])
    rec = _patch_get(monkeypatch, _INFO_JSON)

    result = api.connector_versions("code-snippet")

    assert result["version"] == "2.2.1"
    assert rec.last_url == f"{_REPO_HOST}/content-hub/code-snippet-2.2.1/latest/info.json"


def test_absolute_info_path_used_directly(monkeypatch):
    """An infoPath that is already an absolute URL is used as-is (minus buildNumber)."""
    record = {**_AVAILABLE_RECORD, "infoPath": f"{_REPO_BASE}/code-snippet-2.2.1/11354"}
    api, _ = _api([record])
    rec = _patch_get(monkeypatch, _INFO_JSON)

    api.connector_versions("code-snippet")

    assert rec.last_url == f"{_REPO_BASE}/code-snippet-2.2.1/latest/info.json"


def test_relative_info_path_prepends_repo_host(monkeypatch):
    """A root-relative infoPath gets the repo host prepended."""
    record = {**_AVAILABLE_RECORD, "infoPath": "/content-hub/code-snippet-2.2.1/11354"}
    api, _ = _api([record])
    rec = _patch_get(monkeypatch, _INFO_JSON)

    api.connector_versions("code-snippet")

    assert rec.last_url == f"{_REPO_HOST}/content-hub/code-snippet-2.2.1/latest/info.json"


# ---------------------------------------------------------------------------
# Record selection
# ---------------------------------------------------------------------------


def test_exact_name_preferred_over_fuzzy_match(monkeypatch):
    """Exact name match wins even when a non-matching cloud record appears first."""
    fuzzy = {
        **_AVAILABLE_RECORD,
        "name": "code-snippet-extra",
        "latestAvailableVersion": None,
        "infoPath": "/content-hub/code-snippet-extra-1.0.0/1",
    }
    exact = {**_INSTALLED_RECORD}
    api, _ = _api([fuzzy, exact])
    _patch_get(monkeypatch, _INFO_JSON)

    result = api.connector_versions("code-snippet")

    assert result["name"] == "code-snippet"


def test_local_records_are_skipped(monkeypatch):
    """local=True records are excluded; only cloud-backed records are used."""
    rec = _patch_get(monkeypatch, _INFO_JSON)
    api, _ = _api([_LOCAL_ONLY_RECORD, _INSTALLED_RECORD])

    api.connector_versions("code-snippet")

    assert rec.called


def test_raises_when_no_cloud_records():
    """ValueError raised when only local records exist (no FDN access)."""
    api, _ = _api([_LOCAL_ONLY_RECORD])

    with pytest.raises(ValueError, match="no cloud-backed connector found"):
        api.connector_versions("code-snippet")


def test_raises_when_search_returns_nothing():
    """ValueError raised when the search returns no results at all."""
    api, _ = _api([])

    with pytest.raises(ValueError, match="no cloud-backed connector found"):
        api.connector_versions("nonexistent-connector")


# ---------------------------------------------------------------------------
# Return value
# ---------------------------------------------------------------------------


def test_returns_full_info_json_payload(monkeypatch):
    """The full info.json payload is returned, including availableVersions."""
    api, _ = _api([_INSTALLED_RECORD])
    _patch_get(monkeypatch, _INFO_JSON)

    result = api.connector_versions("code-snippet")

    assert result["availableVersions"] == ["2.1.4", "2.1.5", "2.2.0", "2.2.1"]
    assert result["operations"][0]["operation"] == "python_inline_code_editor"


def test_repo_404_raises(monkeypatch):
    """HTTP 404 from the public repo propagates as an HTTPError."""
    api, _ = _api([_INSTALLED_RECORD])
    _patch_get(monkeypatch, status_code=404)

    with pytest.raises(requests.HTTPError):
        api.connector_versions("code-snippet")


# --------------------------------------------------------------- AI agents (8.0.0+)

# Trimmed from a live 8.0.0 catalog query: AI agents are served by the same
# /api/query/solutionpacks endpoint (@type stays "SolutionPack") and are
# discriminated only by `type: "ai_agent"`.
_AGENTS = [
    {
        "uuid": "a1",
        "name": "conversation",
        "label": "Chat Assistant",
        "type": "ai_agent",
        "version": "1.0.0",
        "installed": True,
    },
    {
        "uuid": "a2",
        "name": "alert-investigation",
        "label": "Alert Investigation",
        "type": "ai_agent",
        "version": "1.2.0",
        "installed": True,
    },
]


def test_search_installed_ai_agents_filters_on_ai_agent_type():
    api, client = _api(_AGENTS)
    agents = api.search_installed_ai_agents()
    assert [a.name for a in agents] == ["conversation", "alert-investigation"]
    body = client.calls[-1][2]
    assert {"field": "type", "operator": "in", "value": ["ai_agent"]} in body["filters"]
    assert {"field": "installed", "operator": "eq", "value": True} in body["filters"]


def test_ai_agents_parse_as_ai_agent_model():
    from pyfsr.models import AIAgent

    api, _ = _api(_AGENTS)
    assert isinstance(api.search_installed_ai_agents()[0], AIAgent)


def test_search_available_ai_agents_flips_installed_filter():
    api, client = _api(_AGENTS)
    api.search_available_ai_agents()
    assert {"field": "installed", "operator": "eq", "value": False} in client.calls[-1][2]["filters"]


def test_get_installed_ai_agent_matches_name_exactly():
    api, _ = _api(_AGENTS)
    assert api.get_installed_ai_agent("conversation").label == "Chat Assistant"


def test_get_installed_ai_agent_matches_label_exactly():
    api, _ = _api(_AGENTS)
    assert api.get_installed_ai_agent("Chat Assistant").name == "conversation"


def test_get_installed_ai_agent_prefers_name_over_label_on_collision():
    members = [
        {"uuid": "x", "name": "zeta", "label": "alpha", "type": "ai_agent"},
        {"uuid": "y", "name": "alpha", "label": "other", "type": "ai_agent"},
    ]
    api, _ = _api(members)
    # "alpha" is zeta's label AND alpha's name — name wins.
    assert api.get_installed_ai_agent("alpha").uuid == "y"


def test_get_installed_ai_agent_requests_every_agent_not_a_page():
    # An exact match must not be lost off the end of a fuzzy-ranked default page.
    api, client = _api(_AGENTS)
    api.get_installed_ai_agent("conversation")
    assert client.calls[-1][2]["limit"] == 2147483647


def test_get_installed_ai_agent_unknown_raises():
    api, _ = _api(_AGENTS)
    with pytest.raises(ValueError, match="AI agent 'Ghost' not found"):
        api.get_installed_ai_agent("Ghost")
