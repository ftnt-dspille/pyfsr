"""configurations_by_location: hub AND agent configs, each tagged with where it lives.

configurations() reads the hub's own install only, so a connector installed just
on agents listed nothing. A configuration's `connector` field is the id of the
install it belongs to; each agent has its own install (`conn_id`).
"""

from pyfsr.api.connectors import ConnectorsAPI

A1, A2 = "a" * 32, "b" * 32


class FakeClient:
    def get(self, endpoint, params=None, **kw):
        if endpoint == "/api/integration/connectors/":
            return {"data": [{"name": "activedirectory", "id": 30, "version": "2.4.0"}]}
        if endpoint.startswith("/api/integration/configuration/"):
            return {
                "data": [
                    {"name": "Hub AD", "config_id": "c-hub", "default": True, "connector": 30, "agent": None},
                    {"name": "Lab AD", "config_id": "c-a1", "default": True, "connector": 613, "agent": A1},
                    {"name": "Lab AD", "config_id": "c-a2", "default": True, "connector": 626, "agent": A2},
                    {"name": "Second", "config_id": "c-a2b", "default": False, "connector": 626, "agent": A2},
                    {"name": "Other", "config_id": "c-x", "default": True, "connector": 999, "agent": None},
                ],
                "nextPage": None,
            }
        raise AssertionError(endpoint)

    def post(self, endpoint, data=None, **kw):
        assert "/connectors/agents/activedirectory/2.4.0/" in endpoint
        return {"data": [{"agent": A1, "conn_id": 613}, {"agent": A2, "conn_id": 626}]}


def test_lists_hub_and_every_agent_config_with_its_location():
    rows = ConnectorsAPI(FakeClient()).configurations_by_location("activedirectory")
    got = {(r["name"], r["agent"], r["config_id"]) for r in rows}
    assert got == {("Hub AD", "", "c-hub"), ("Lab AD", A1, "c-a1"), ("Lab AD", A2, "c-a2"), ("Second", A2, "c-a2b")}
