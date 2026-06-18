"""Unit tests for ConnectorsAPI (discovery / health / execute)."""

import pytest

from pyfsr.api.connectors import ConnectorsAPI, _import_job_id

_CONFIGURED = {
    "data": [
        {
            "id": 16,
            "name": "virustotal",
            "version": "3.1.0",
            "label": "VirusTotal",
            "configuration": [
                {"config_id": "vt-default", "name": "Default", "default": True},
                {"config_id": "vt-alt", "name": "Alt", "default": False},
            ],
        },
        {
            "id": 22,
            "name": "fortigate",
            "version": "2.0.0",
            "configuration": [],
        },
        {
            "id": 33,
            "name": "fortinet-fortisiem",
            "version": "6.1.0",
            "configuration": [],
        },
    ]
}


class FakeClient:
    def __init__(self, *, get_map=None, post_resp=None, raiser=None):
        self.get_calls = []
        self.post_calls = []
        self.put_calls = []
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
        self.last_post_params = params
        self.last_post_files = kwargs.get("files")
        return self._post_resp or {"operation": data.get("operation"), "status": "Success"}

    def put(self, endpoint, data=None, params=None, **kwargs):
        self.put_calls.append((endpoint, data))
        return self._post_resp or {"config_id": (data or {}).get("config_id"), "status": "ok"}

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
        "fortinet-fortisiem",
        _FSIEM_CONFIG,
        name="prod",
        version="6.1.0",
        default=True,
        validate=False,
    )
    assert res["config_id"] == "new-uuid"
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/integration/configuration/"
    assert body["connector"] == 33  # integer id resolved from name (endpoint 500s without it)
    assert body["connector_name"] == "fortinet-fortisiem"
    assert body["connector_version"] == "6.1.0"
    assert body["name"] == "prod"
    assert body["default"] is True
    assert body["config"] == _FSIEM_CONFIG
    assert "config_id" not in body  # minted server-side
    assert "agent" not in body  # self-agent by default


def test_create_configuration_resolves_version():
    api, client = _api(post_resp={"config_id": "x"})
    api.create_configuration("virustotal", {"key": "v"}, name="c", validate=False)
    _, body = client.post_calls[0]
    assert body["connector_version"] == "3.1.0"
    assert body["connector"] == 16


def test_create_configuration_unknown_connector_raises():
    import pytest

    api, _ = _api()
    with pytest.raises(ValueError, match="not installed"):
        api.create_configuration("brand-new", {"k": "v"}, name="c", version="1.0", validate=False)


def test_create_configuration_with_config_id_and_agent():
    api, client = _api(post_resp={})
    api.create_configuration(
        "virustotal",
        {"k": "v"},
        name="c",
        config_id="cfg-1",
        agent="agent-9",
        validate=False,
    )
    _, body = client.post_calls[0]
    assert body["config_id"] == "cfg-1"
    assert body["agent"] == "agent-9"


def test_create_configuration_clears_cache():
    api, client = _api(post_resp={})
    api.list_configured()  # prime
    api.create_configuration("virustotal", {"k": "v"}, name="c", validate=False)
    api.list_configured()  # should refetch
    assert len([c for c in client.get_calls if "connectors/" in c[0]]) == 2


def test_create_configuration_validate_missing_raises():
    import pytest

    # config_schema comes from definition() (a POST returning config_schema.fields)
    schema = {"config_schema": {"fields": [{"name": "fsm_type", "required": True}]}}
    api, _ = _api(post_resp=schema)
    with pytest.raises(ValueError, match="missing required field"):
        api.create_configuration("virustotal", {}, name="c", version="3.1.0")


def test_update_configuration_puts_to_config_id_path():
    api, client = _api()
    api.update_configuration(
        "virustotal", "cfg-1", {"k": "v2"}, name="c", version="3.1.0", validate=False
    )
    endpoint, body = client.put_calls[0]
    assert endpoint == "/api/integration/configuration/cfg-1/"
    assert body["config_id"] == "cfg-1"
    assert body["connector"] == 16
    assert body["config"] == {"k": "v2"}


def test_delete_configuration_trailing_slash():
    api, client = _api()
    api.delete_configuration("cfg-1")
    assert client.delete_calls[-1][0] == "/api/integration/configuration/cfg-1/"


# -- config schema / validation ---------------------------------------------
_SIEM_SCHEMA = {
    "config_schema": {
        "fields": [
            {
                "name": "fsm_type",
                "type": "select",
                "required": True,
                "onchange": {
                    "FortiSIEM": [
                        {"name": "server", "required": True},
                        {"name": "username", "required": True},
                        {"name": "password", "required": True},
                        {"name": "organization", "required": False},
                    ],
                },
            },
            {"name": "verify_ssl", "type": "checkbox", "required": False},
        ]
    }
}


def test_config_schema_returns_fields():
    api, _ = _api(post_resp=_SIEM_SCHEMA)
    fields = api.config_schema("virustotal")
    assert fields[0]["name"] == "fsm_type"


def test_required_config_fields_follows_onchange_branch():
    api, _ = _api(post_resp=_SIEM_SCHEMA)
    req = api.required_config_fields("virustotal", {"fsm_type": "FortiSIEM"})
    assert req == ["fsm_type", "server", "username", "password"]


def test_validate_config_flags_missing_in_active_branch():
    api, _ = _api(post_resp=_SIEM_SCHEMA)
    res = api.validate_config(
        "virustotal", {"fsm_type": "FortiSIEM", "server": "x", "username": "u"}
    )
    assert res["valid"] is False
    assert res["missing"] == ["password"]


def test_validate_config_unknown_keys_when_branch_inactive():
    api, _ = _api(post_resp=_SIEM_SCHEMA)
    # no fsm_type -> branch never activates, so server/username are "unknown"
    res = api.validate_config("virustotal", {"server": "x", "username": "u"})
    assert res["missing"] == ["fsm_type"]
    assert set(res["unknown"]) == {"server", "username"}


def test_validate_config_valid():
    api, _ = _api(post_resp=_SIEM_SCHEMA)
    res = api.validate_config(
        "virustotal",
        {"fsm_type": "FortiSIEM", "server": "x", "username": "u", "password": "p"},
    )
    assert res == {"valid": True, "missing": [], "unknown": []}


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


def test_install_from_file_uploads_tgz(tmp_path):
    bundle = tmp_path / "hello-world-1.0.0.tgz"
    bundle.write_bytes(b"\x1f\x8b\x08fake-tgz")
    api, client = _api(post_resp={"id": 42, "name": "hello-world"})
    out = api.install_from_file(str(bundle), replace=True)
    assert out["id"] == 42
    assert client.post_calls[-1][0] == "/api/3/solutionpacks/install"
    assert client.last_post_params == {"$type": "connector", "$replace": "true"}
    assert client.last_post_files["file"][0] == "hello-world-1.0.0.tgz"


def test_install_from_file_missing_path_raises():
    api, _ = _api()
    with pytest.raises(FileNotFoundError):
        api.install_from_file("/no/such/bundle.tgz")


def test_uninstall_resolves_id_and_deletes():
    api, client = _api()
    api.uninstall("virustotal")
    assert client.delete_calls[-1][0] == "/api/integration/connectors/16/"


def test_uninstall_unknown_connector_raises():
    api, _ = _api()
    with pytest.raises(ValueError):
        api.uninstall("nope")


def test_connector_detail_posts_empty_body_to_id():
    api, client = _api(post_resp={"operations": []})
    api.connector_detail("virustotal")
    assert client.post_calls[-1] == ("/api/integration/connectors/16/", {})


def test_list_configurations_filters():
    api, client = _api()
    api.list_configurations(name="virustotal", active=True)
    endpoint, params = client.get_calls[-1]
    assert endpoint == "/api/integration/configuration/"
    assert params["name"] == "virustotal" and params["active"] is True


def test_dev_read_and_write_file():
    api, client = _api(post_resp={"data": "x"})
    api.dev_read_file("dev-7", "/foo_1_0_0_dev/info.json")
    assert client.post_calls[-1] == (
        "/api/integration/connector/development/entity/dev-7/files/",
        {"xpath": "/foo_1_0_0_dev/info.json"},
    )
    api.dev_write_file("dev-7", {"path": "info.json", "content": "{}"})
    assert client.put_calls[-1] == (
        "/api/integration/connector/development/entity/dev-7/files/",
        {"fileData": {"path": "info.json", "content": "{}"}},
    )


def test_dev_publish_sends_flags():
    api, client = _api(post_resp={"status": "ok"})
    api.dev_publish("dev-7", replace=True, discard=False)
    assert client.post_calls[-1] == (
        "/api/integration/connector/development/entity/dev-7/publish/",
        {"replace": True, "discard": False},
    )
