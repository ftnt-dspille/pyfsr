"""Unit tests for the generic RecordSet CRUD layer."""

from pyfsr import Query, RecordSet
from pyfsr.records import resolve_record_path


class FakeClient:
    """Records get/post/put/delete calls and returns scripted responses."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def get(self, endpoint, params=None, **kwargs):
        self.calls.append(("GET", endpoint, params, None))
        return self._resp(endpoint)

    def post(self, endpoint, data=None, **kwargs):
        self.calls.append(("POST", endpoint, None, data))
        return self._resp(endpoint)

    def put(self, endpoint, data=None, **kwargs):
        self.calls.append(("PUT", endpoint, None, data))
        return self._resp(endpoint)

    def delete(self, endpoint, params=None, **kwargs):
        self.calls.append(("DELETE", endpoint, params, None))
        return None

    def _resp(self, endpoint):
        if callable(self.responses):
            return self.responses(endpoint)
        return self.responses.get(endpoint, {"hydra:member": []})


# -- path resolution --------------------------------------------------------
def test_resolve_record_path_uuid():
    assert resolve_record_path("alerts", "abc-123") == "/api/3/alerts/abc-123"


def test_resolve_record_path_iri_passthrough():
    assert resolve_record_path("alerts", "/api/3/alerts/abc") == "/api/3/alerts/abc"


def test_resolve_record_path_module_colon_shorthand():
    assert resolve_record_path("alerts", "incidents:xyz") == "/api/3/incidents/xyz"


# -- reads ------------------------------------------------------------------
def test_get_by_uuid():
    client = FakeClient({"/api/3/incidents/u1": {"uuid": "u1"}})
    rec = RecordSet(client, "incidents").get("u1")
    assert rec == {"uuid": "u1"}
    assert client.calls[0] == ("GET", "/api/3/incidents/u1", None, None)


def test_get_with_relationships():
    client = FakeClient()
    RecordSet(client, "incidents").get("u1", relationships=True)
    assert client.calls[0] == ("GET", "/api/3/incidents/u1", {"$relationships": "true"}, None)


def test_list_sets_limit_and_page():
    client = FakeClient({"/api/3/alerts": {"hydra:member": [1, 2], "hydra:totalItems": 2}})
    page = RecordSet(client, "alerts").list(limit=10, page=2)
    assert page.members == [1, 2]
    assert page.total == 2
    assert client.calls[0] == ("GET", "/api/3/alerts", {"$limit": 10, "$page": 2}, None)


def test_search_adds_search_param():
    client = FakeClient()
    RecordSet(client, "alerts").search("malware", limit=5)
    method, endpoint, params, _ = client.calls[0]
    assert params["$search"] == "malware"
    assert params["$limit"] == 5


def test_query_posts_to_query_endpoint():
    client = FakeClient(
        {"/api/query/incidents": {"hydra:member": [{"uuid": "x"}], "hydra:totalItems": 1}}
    )
    q = Query().eq("status.itemValue", "Open").limit(50)
    page = RecordSet(client, "incidents").query(q)
    method, endpoint, _, data = client.calls[0]
    assert (method, endpoint) == ("POST", "/api/query/incidents")
    assert data["filters"][0]["operator"] == "eq"
    assert data["limit"] == 50
    assert page.members == [{"uuid": "x"}]


def test_iterate_walks_pages_via_query():
    pages = {
        1: {"hydra:member": [{"i": 1}, {"i": 2}]},
        2: {"hydra:member": [{"i": 3}]},
    }
    seen_pages = []

    def responder(endpoint):
        return None  # unused; overridden below

    client = FakeClient()

    def post(endpoint, data=None, **kwargs):
        seen_pages.append(data["page"])
        return pages.get(data["page"], {"hydra:member": []})

    client.post = post
    out = list(RecordSet(client, "incidents").iterate(Query().eq("x", 1), page_size=2))
    assert [r["i"] for r in out] == [1, 2, 3]
    assert seen_pages == [1, 2]


def test_iterate_without_query_uses_list_endpoint():
    pages = {1: {"hydra:member": [{"i": 1}]}, 2: {"hydra:member": []}}
    client = FakeClient()

    def get(endpoint, params=None, **kwargs):
        return pages.get(params["$page"], {"hydra:member": []})

    client.get = get
    out = list(RecordSet(client, "alerts").iterate(page_size=1))
    assert [r["i"] for r in out] == [1]


# -- writes -----------------------------------------------------------------
def test_create():
    client = FakeClient({"/api/3/alerts": {"uuid": "new"}})
    rec = RecordSet(client, "alerts").create({"name": "x"})
    assert client.calls[0] == ("POST", "/api/3/alerts", None, {"name": "x"})
    assert rec == {"uuid": "new"}


def test_update_uses_put():
    client = FakeClient()
    RecordSet(client, "alerts").update("u1", {"severity": "High"})
    assert client.calls[0] == ("PUT", "/api/3/alerts/u1", None, {"severity": "High"})


def test_delete():
    client = FakeClient()
    RecordSet(client, "alerts").delete("u1")
    assert client.calls[0] == ("DELETE", "/api/3/alerts/u1", None, None)


def test_records_accessor_on_client(mock_client):
    rs = mock_client.records("alerts")
    assert isinstance(rs, RecordSet)
    assert rs.module == "alerts"
