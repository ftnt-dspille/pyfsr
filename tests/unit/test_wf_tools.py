"""Unit tests for WfToolsAPI (Jinja render / global variables)."""

from pyfsr.api.wf_tools import WfToolsAPI

_DYNVARS = {
    "hydra:member": [
        {"id": 8, "name": "TTL_Days", "value": "20", "default_value": "20"},
        {"id": 9, "name": "Region", "value": "us", "default_value": ""},
    ],
    "hydra:totalItems": 2,
}


class FakeClient:
    def __init__(self, *, post_resp=None, get_resp=None):
        self.post_calls = []
        self.get_calls = []
        self._post_resp = post_resp
        self._get_resp = get_resp

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data))
        return self._post_resp

    def get(self, endpoint, params=None, **kwargs):
        self.get_calls.append((endpoint, params))
        return self._get_resp


def _api(**kw):
    c = FakeClient(**kw)
    return WfToolsAPI(c), c


# -- render -----------------------------------------------------------------
def test_render_unwraps_result():
    api, client = _api(post_resp={"result": 7})
    assert api.render("{{ vars.x + 2 }}", {"vars": {"x": 5}}) == 7
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/wf/api/jinja-editor/"
    assert body == {"template": "{{ vars.x + 2 }}", "values": {"vars": {"x": 5}}}


def test_render_defaults_values_to_empty_dict():
    api, client = _api(post_resp={"result": "hi"})
    assert api.render("hi") == "hi"
    assert client.post_calls[0][1]["values"] == {}


def test_render_raw_returns_full_envelope():
    api, _ = _api(post_resp={"result": 7})
    assert api.render_raw("{{ 7 }}") == {"result": 7}


# -- dynamic variables ------------------------------------------------------
def test_dynamic_variables_returns_members():
    api, client = _api(get_resp=_DYNVARS)
    out = api.dynamic_variables()
    assert [v["name"] for v in out] == ["TTL_Days", "Region"]
    endpoint, params = client.get_calls[0]
    assert endpoint == "/api/wf/api/dynamic-variable/"
    assert params == {"offset": 0, "limit": 2147483647}


def test_dynamic_variable_resolves_value_by_name():
    api, _ = _api(get_resp=_DYNVARS)
    assert api.dynamic_variable("Region") == "us"


def test_dynamic_variable_missing_returns_none():
    api, _ = _api(get_resp=_DYNVARS)
    assert api.dynamic_variable("Nope") is None
