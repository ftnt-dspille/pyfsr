"""Unit tests for the tags and agents wrappers."""

from pyfsr.api.agents import AgentsAPI
from pyfsr.api.tags import TagsAPI


class RecordingClient:
    def __init__(self, members):
        self.calls = []
        self._members = members

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        return {"hydra:member": self._members}


def test_tags_list_returns_names_and_sets_export():
    c = RecordingClient([{"uuid": "mitre.t1059"}, {"uuid": "phishing"}])
    out = TagsAPI(c).list()
    assert out == ["mitre.t1059", "phishing"]
    method, endpoint, params = c.calls[-1]
    assert method == "GET" and endpoint == "/api/3/tags"
    assert params["$export"] == "true" and params["$limit"] == 200
    assert "uuid$like" not in params


def test_tags_list_prefix_filter():
    c = RecordingClient([{"uuid": "mitre.t1059"}])
    TagsAPI(c).list(prefix="mitre", limit=50)
    params = c.calls[-1][2]
    assert params["uuid$like"] == "mitre%" and params["$limit"] == 50


def test_tags_list_skips_blank_rows():
    # Genuinely blank members (empty uuid, empty dict, empty string) are dropped;
    # everything carrying a name survives.
    c = RecordingClient([{"uuid": "ok"}, {"uuid": ""}, {}, ""])
    assert TagsAPI(c).list() == ["ok"]


def test_tags_list_tolerates_string_and_name_members():
    # Some appliances (7.6.x demo) return bare name strings; others put the name
    # under "name" instead of "uuid". Both resolve.
    c = RecordingClient(["phishing", {"name": "mitre.t1059"}, {"uuid": "malware"}])
    assert TagsAPI(c).list() == ["phishing", "mitre.t1059", "malware"]


def test_tags_map_names_dual_shape_iris():
    c = RecordingClient(
        [
            "phishing",  # bare string -> /api/3/tags/<name>
            {"uuid": "malware", "@id": "/api/3/tags/abc-123"},  # dict with explicit IRI
        ]
    )
    assert TagsAPI(c).map_names() == {
        "phishing": "/api/3/tags/phishing",
        "malware": "/api/3/tags/abc-123",
    }


def test_agents_list_returns_all_records():
    c = RecordingClient([{"agentId": "a1", "active": True}, {"agentId": "a2", "active": False}])
    out = AgentsAPI(c).list()
    assert [a["agentId"] for a in out] == ["a1", "a2"]
    assert c.calls[-1][1] == "/api/3/agents"


def test_agents_list_active_only():
    c = RecordingClient([{"agentId": "a1", "active": True}, {"agentId": "a2", "active": False}])
    out = AgentsAPI(c).list(active_only=True)
    assert [a["agentId"] for a in out] == ["a1"]
