"""Unit tests for ConnectorsAPI (discovery / health / execute)."""

from pyfsr.api.connectors import ConnectorsAPI

_CONFIGURED = {
    "data": [
        {
            "name": "virustotal",
            "version": "3.1.0",
            "label": "VirusTotal",
            "configuration": [
                {"config_id": "vt-default", "name": "Default", "default": True},
                {"config_id": "vt-alt", "name": "Alt", "default": False},
            ],
        },
        {
            "name": "fortigate",
            "version": "2.0.0",
            "configuration": [],
        },
    ]
}


class FakeClient:
    def __init__(self, *, get_map=None, post_resp=None, raiser=None):
        self.get_calls = []
        self.post_calls = []
        self._get_map = get_map or {}
        self._post_resp = post_resp or {}
        self._raiser = raiser

    def get(self, endpoint, params=None, **kwargs):
        self.get_calls.append((endpoint, params))
        if self._raiser:
            self._raiser(endpoint)
        if endpoint.startswith("/api/integration/connectors/"):
            if "healthcheck" in endpoint:
                return self._get_map.get("healthcheck", {"status": "Available"})
            return _CONFIGURED
        return self._get_map.get(endpoint, {})

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data))
        return self._post_resp or {"operation": data.get("operation"), "status": "Success"}


def _api(**kw):
    c = FakeClient(**kw)
    return ConnectorsAPI(c), c


# -- discovery --------------------------------------------------------------
def test_list_configured_shape_and_cache():
    api, client = _api()
    out = api.list_configured()
    assert out[0]["name"] == "virustotal"
    assert out[0]["configurations"][0]["config_id"] == "vt-default"
    api.list_configured()  # cached
    assert len([c for c in client.get_calls if "connectors" in c[0]]) == 1


def test_list_configured_refresh_refetches():
    api, client = _api()
    api.list_configured()
    api.list_configured(refresh=True)
    assert len([c for c in client.get_calls if "connectors" in c[0]]) == 2


def test_resolve_version():
    api, _ = _api()
    assert api.resolve_version("virustotal") == "3.1.0"
    assert api.resolve_version("nope") is None


def test_resolve_config_default_and_named():
    api, _ = _api()
    assert api.resolve_config("virustotal") == "vt-default"  # default flag
    assert api.resolve_config("virustotal", "Alt") == "vt-alt"
    assert api.resolve_config("fortigate") is None  # no configs


def test_configurations():
    api, _ = _api()
    assert [c["name"] for c in api.configurations("virustotal")] == ["Default", "Alt"]
    assert api.configurations("nope") == []


# -- healthcheck ------------------------------------------------------------
def test_healthcheck_resolves_version_and_hits_path():
    api, client = _api(get_map={"healthcheck": {"status": "Available"}})
    res = api.healthcheck("virustotal")
    assert res["status"] == "Available"
    hc = [c for c in client.get_calls if "healthcheck" in c[0]][0]
    assert hc[0] == "/api/integration/connectors/healthcheck/virustotal/3.1.0/"


def test_healthcheck_with_config_param():
    api, client = _api()
    api.healthcheck("virustotal", config="vt-default")
    hc = [c for c in client.get_calls if "healthcheck" in c[0]][0]
    assert hc[1] == {"config": "vt-default"}


def test_healthcheck_unconfigured_connector():
    api, _ = _api()
    res = api.healthcheck("nope")
    assert res["status"] == "no-config"


def test_healthcheck_404_normalized():
    class Resp:
        status_code = 404

    def raiser(endpoint):
        if "healthcheck" in endpoint:
            e = RuntimeError("not found")
            e.response = Resp()
            raise e

    api, _ = _api(raiser=raiser)
    res = api.healthcheck("virustotal")
    assert res["status"] == "no-config"
    assert res["http_status"] == 404


# -- execute ----------------------------------------------------------------
def test_execute_builds_body_and_resolves_version():
    api, client = _api()
    api.list_configured()  # prime cache so config resolves too
    api.execute("virustotal", "get_reputation_ip", params={"ip": "8.8.8.8"})
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/integration/execute/"
    assert body["connector"] == "virustotal"
    assert body["operation"] == "get_reputation_ip"
    assert body["version"] == "3.1.0"
    assert body["config"] == "vt-default"  # default config resolved
    assert body["params"] == {"ip": "8.8.8.8"}


def test_execute_explicit_version_and_config_no_resolution():
    api, client = _api()
    api.execute("acme", "op", version="9.9", config="cfg-1")
    _, body = client.post_calls[0]
    assert (body["version"], body["config"]) == ("9.9", "cfg-1")
    # no list_configured fetch needed when both are explicit
    assert client.get_calls == []


def test_execute_defaults_empty_params_and_config():
    api, client = _api()
    api.execute("acme", "op", version="1.0")
    _, body = client.post_calls[0]
    assert body["params"] == {}
    assert body["config"] == ""  # not resolved, no cache primed


def test_execute_config_name_selects_named():
    api, _client = _api()
    api.execute("virustotal", "op", config_name="Alt")
    _, body = _client.post_calls[0]
    assert body["config"] == "vt-alt"
