"""Unit tests for ConnectorsAPI (discovery / health / execute)."""

from pyfsr.api.connectors import ConnectorsAPI, _import_job_id

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
        self.delete_calls = []
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

    def delete(self, endpoint, params=None, **kwargs):
        self.delete_calls.append((endpoint, params))
        return None


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


# -- install / import-job helpers -------------------------------------------
def test_import_job_id_from_nested_importjob():
    resp = {
        "uuid": "solutionpack-uuid",  # the SP's, NOT the job's
        "importJob": {"@id": "/api/3/import_jobs/job-uuid", "uuid": "job-uuid"},
    }
    assert _import_job_id(resp) == "job-uuid"


def test_import_job_id_from_iri_only():
    resp = {"importJob": {"@id": "/api/3/import_jobs/abc-123"}}
    assert _import_job_id(resp) == "abc-123"


def test_import_job_id_none_when_absent():
    assert _import_job_id({"uuid": "sp-uuid"}) is None


def test_install_returns_response_without_wait():
    resp = {"importJob": {"uuid": "job-1"}, "name": "abuseipdb"}
    api, client = _api(post_resp=resp)
    out = api.install("abuseipdb", "2.0.0")
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/3/solutionpacks/install"
    assert body == {"name": "abuseipdb", "version": "2.0.0"}
    assert out is resp


def test_install_status_selects_progress_fields():
    api, client = _api(get_map={"/api/3/import_jobs/job-1": {"status": "Import Complete"}})
    res = api.install_status("job-1")
    assert res["status"] == "Import Complete"
    endpoint, params = client.get_calls[-1]
    assert endpoint == "/api/3/import_jobs/job-1"
    assert "status" in params["__selectFields"]


def test_wait_for_install_returns_terminal():
    api, _ = _api(get_map={"/api/3/import_jobs/job-1": {"status": "Import Complete"}})
    res = api.wait_for_install("job-1", interval=0)
    assert res["status"] == "Import Complete"


# -- pagination -------------------------------------------------------------
def test_list_configured_paginates():
    pages = {
        1: {"totalItems": 3, "data": [{"name": "a"}, {"name": "b"}]},
        2: {"totalItems": 3, "data": [{"name": "c"}]},
    }

    class PagingClient(FakeClient):
        def get(self, endpoint, params=None, **kwargs):
            self.get_calls.append((endpoint, params))
            if endpoint == "/api/integration/connectors/":
                return pages[params["page"]]
            return {}

    api = ConnectorsAPI(PagingClient())
    out = api.list_configured()
    assert [c["name"] for c in out] == ["a", "b", "c"]


# -- configuration ----------------------------------------------------------
_FSIEM_CONFIG = {
    "server": "https://siem.example.com",
    "username": "admin",
    "password": "secret",
    "organization": "Super",
    "verify_ssl": True,
}


def test_create_configuration_builds_body():
    api, client = _api(post_resp={"config_id": "new-uuid"})
    res = api.create_configuration(
        "fortinet-fortisiem", _FSIEM_CONFIG, name="prod", version="5.2.1", default=True
    )
    assert res["config_id"] == "new-uuid"
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/integration/configuration/"
    assert body["connector_name"] == "fortinet-fortisiem"
    assert body["connector_version"] == "5.2.1"
    assert body["name"] == "prod"
    assert body["default"] is True
    assert body["config"] == _FSIEM_CONFIG
    assert "config_id" not in body  # minted server-side
    assert "agent" not in body  # self-agent by default


def test_create_configuration_resolves_version():
    api, client = _api(post_resp={"config_id": "x"})
    api.list_configured()  # prime cache (virustotal 3.1.0)
    api.create_configuration("virustotal", {"key": "v"}, name="c")
    _, body = client.post_calls[0]
    assert body["connector_version"] == "3.1.0"


def test_create_configuration_unknown_version_raises():
    import pytest

    api, _ = _api()
    with pytest.raises(ValueError, match="version"):
        api.create_configuration("brand-new", {"k": "v"}, name="c")


def test_create_configuration_with_config_id_and_agent():
    api, client = _api(post_resp={})
    api.create_configuration(
        "acme", {"k": "v"}, name="c", version="1.0", config_id="cfg-1", agent="agent-9"
    )
    _, body = client.post_calls[0]
    assert body["config_id"] == "cfg-1"
    assert body["agent"] == "agent-9"


def test_create_configuration_clears_cache():
    api, client = _api(post_resp={})
    api.list_configured()  # prime
    api.create_configuration("acme", {"k": "v"}, name="c", version="1.0")
    api.list_configured()  # should refetch
    assert len([c for c in client.get_calls if "connectors/" in c[0]]) == 2


def test_update_configuration_sends_config_id():
    api, client = _api(post_resp={})
    api.update_configuration("acme", "cfg-1", {"k": "v2"}, name="c", version="1.0")
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/integration/configuration/"
    assert body["config_id"] == "cfg-1"
    assert body["config"] == {"k": "v2"}


def test_delete_configuration_trailing_slash():
    api, client = _api()
    api.delete_configuration("cfg-1")
    assert client.delete_calls[-1][0] == "/api/integration/configuration/cfg-1/"


# -- definition / operations / files ---------------------------------------
_DEFINITION = {
    "name": "virustotal",
    "version": "3.1.0",
    "config_schema": {},
    "operations": [
        {"operation": "get_reputation_ip", "title": "IP Reputation", "parameters": []},
        {"operation": "get_reputation_url", "title": "URL Reputation", "parameters": []},
    ],
}


def test_definition_posts_with_format_json_and_resolves_version():
    api, client = _api(post_resp=_DEFINITION)
    defn = api.definition("virustotal")
    assert defn["operations"][0]["operation"] == "get_reputation_ip"
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/integration/connectors/virustotal/3.1.0/?format=json"
    assert body == {}


def test_definition_explicit_version_overrides():
    api, client = _api(post_resp=_DEFINITION)
    api.definition("virustotal", version="9.9.9")
    assert client.post_calls[0][0] == "/api/integration/connectors/virustotal/9.9.9/?format=json"


def test_definition_unconfigured_raises():
    import pytest

    api, _ = _api()
    with pytest.raises(ValueError, match="not configured"):
        api.definition("nope")


def test_operations_returns_operation_list():
    api, _ = _api(post_resp=_DEFINITION)
    ops = api.operations("virustotal")
    assert [o["operation"] for o in ops] == ["get_reputation_ip", "get_reputation_url"]


def test_files_hits_files_endpoint():
    api, client = _api(get_map={"/api/integration/connector/dev-7/files/": {"files": []}})
    assert api.files("dev-7") == {"files": []}
    assert client.get_calls[-1][0] == "/api/integration/connector/dev-7/files/"
