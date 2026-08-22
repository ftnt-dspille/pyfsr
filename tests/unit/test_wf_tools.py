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
    def __init__(self, *, post_resp=None, get_resp=None, put_resp=None):
        self.post_calls = []
        self.get_calls = []
        self.put_calls = []
        self.delete_calls = []
        self._post_resp = post_resp
        self._get_resp = get_resp
        self._put_resp = put_resp

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data))
        return self._post_resp

    def get(self, endpoint, params=None, **kwargs):
        self.get_calls.append((endpoint, params))
        return self._get_resp

    def put(self, endpoint, data=None, params=None, **kwargs):
        self.put_calls.append((endpoint, data))
        return self._put_resp

    def delete(self, endpoint, **kwargs):
        self.delete_calls.append(endpoint)
        return None


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


# -- writing global variables -----------------------------------------------
def test_set_dynamic_variable_creates_when_absent():
    """No POST-or-PUT upsert route exists, so an unknown name has to POST."""
    api, client = _api(get_resp=_DYNVARS, post_resp={"id": 10, "name": "New", "value": "1"})
    out = api.set_dynamic_variable("New", "1")
    assert out["name"] == "New"
    assert client.post_calls[0][0] == "/api/wf/api/dynamic-variable/"
    assert client.post_calls[0][1] == {"name": "New", "value": "1"}
    assert client.put_calls == []


def test_set_dynamic_variable_updates_in_place_when_present():
    """POSTing a name that already exists errors, so an existing one must PUT."""
    api, client = _api(get_resp=_DYNVARS, put_resp={"id": 8, "name": "TTL_Days", "value": "45"})
    api.set_dynamic_variable("TTL_Days", "45")
    assert client.post_calls == []
    endpoint, body = client.put_calls[0]
    # keyed on the id, not the name, and the trailing slash is mandatory
    assert endpoint == "/api/wf/api/dynamic-variable/8/"
    assert body == {"name": "TTL_Days", "value": "45"}


def test_delete_dynamic_variable_resolves_the_name_to_an_id():
    api, client = _api(get_resp=_DYNVARS)
    assert api.delete_dynamic_variable("Region") is True
    assert client.delete_calls == ["/api/wf/api/dynamic-variable/9/"]


def test_delete_dynamic_variable_raises_on_unknown_name():
    api, client = _api(get_resp=_DYNVARS)
    try:
        api.delete_dynamic_variable("Nope")
    except ValueError as exc:
        assert "Nope" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
    assert client.delete_calls == []


def test_delete_dynamic_variable_missing_ok_reports_no_deletion():
    api, client = _api(get_resp=_DYNVARS)
    assert api.delete_dynamic_variable("Nope", missing_ok=True) is False
    assert client.delete_calls == []


def test_dynamic_variable_record_exposes_the_id_the_write_routes_need():
    api, _ = _api(get_resp=_DYNVARS)
    assert api.dynamic_variable_record("TTL_Days")["id"] == 8
    assert api.dynamic_variable_record("absent") is None
