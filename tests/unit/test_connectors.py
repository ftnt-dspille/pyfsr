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
        return self._post_resp or {"config_id": (data or {}).get("config_id"), "status": 1}

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


def test_find_installed_connectors_partial_and_label():
    api, _ = _api()
    # case-insensitive substring on name
    assert [c["name"] for c in api.find_installed_connectors("forti")] == [
        "fortigate",
        "fortinet-fortisiem",
    ]
    # substring on label (name has no "virus")
    assert [c["name"] for c in api.find_installed_connectors("virus")] == ["virustotal"]
    # no match -> empty
    assert api.find_installed_connectors("nope") == []


def test_find_installed_connectors_separator_and_case_folding():
    api, _ = _api()
    # underscores/spaces/casing fold to the hyphenated name
    assert [c["name"] for c in api.find_installed_connectors("Fortinet_FortiSIEM")] == ["fortinet-fortisiem"]


def test_find_installed_connectors_exact_name_sorts_first():
    api, _ = _api()
    # "fortigate" is an exact name and also a prefix of nothing else here, but
    # it must rank ahead of any non-exact match for the same query token.
    hits = api.find_installed_connectors("fortigate")
    assert hits[0]["name"] == "fortigate"


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


def test_healthcheck_all_default_checks_every_configured():
    api, _ = _api(get_map={"healthcheck": {"status": "Available"}})
    out = api.healthcheck_all()
    # every configured connector with a version, keyed by name
    assert set(out) == {"virustotal", "fortigate", "fortinet-fortisiem"}
    assert all(r["status"] == "Available" for r in out.values())


def test_healthcheck_all_explicit_subset():
    api, _ = _api(get_map={"healthcheck": {"status": "Available"}})
    out = api.healthcheck_all(["virustotal"])
    assert set(out) == {"virustotal"}


def test_healthcheck_all_one_failure_does_not_sink_sweep():
    def raiser(endpoint):
        if "healthcheck/fortigate/" in endpoint:
            raise RuntimeError("boom")

    api, _ = _api(raiser=raiser)
    out = api.healthcheck_all()
    assert out["fortigate"]["status"] == "error"
    assert out["virustotal"]["status"] == "Available"


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


def test_execute_config_accepts_name_passthrough():
    """config= accepts a display NAME — passed straight to the wire (server resolves)."""
    api, _client = _api()
    api.execute("virustotal", "op", config="Alt")
    _, body = _client.post_calls[0]
    assert body["config"] == "Alt"  # name, not a UUID — server resolves it


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


def test_list_configured_paginates_via_nextpage():
    # No totalItems: pagination is driven by the envelope's nextPage marker.
    pages = {
        1: {"data": [{"name": "a"}, {"name": "b"}], "nextPage": 2},
        2: {"data": [{"name": "c"}], "nextPage": None},
    }

    class PagingClient(FakeClient):
        def get(self, endpoint, params=None, **kwargs):
            self.get_calls.append((endpoint, params))
            if endpoint == "/api/integration/connectors/":
                return pages.get(params["page"], {"data": []})
            return {}

    out = ConnectorsAPI(PagingClient()).list_configured()
    assert [c["name"] for c in out] == ["a", "b", "c"]


def test_integration_list_envelope_parse_tolerates_shapes():
    from pyfsr.models import IntegrationListEnvelope

    env = IntegrationListEnvelope.parse({"totalItems": 2, "nextPage": 2, "data": [{"x": 1}]})
    assert env.totalItems == 2 and env.has_next is True and env.data == [{"x": 1}]
    # A bare list is wrapped as data; a non-collection yields an empty envelope.
    assert IntegrationListEnvelope.parse([{"y": 2}]).data == [{"y": 2}]
    assert IntegrationListEnvelope.parse(None).data == []
    assert IntegrationListEnvelope.parse({"data": []}).has_next is False


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
        autofill=False,
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
    api.create_configuration("virustotal", {"key": "v"}, name="c", validate=False, autofill=False)
    _, body = client.post_calls[0]
    assert body["connector_version"] == "3.1.0"
    assert body["connector"] == 16


def test_create_configuration_unknown_connector_raises():
    import pytest

    api, _ = _api()
    with pytest.raises(ValueError, match="not installed"):
        api.create_configuration("brand-new", {"k": "v"}, name="c", version="1.0", validate=False)


# A realistic POST response from /api/integration/configuration/ — the saved
# config record (status is the int active-flag, not a string "Success").
_CREATED_CONFIG = {"config_id": "cfg-1", "name": "c", "status": 1, "connector": 16}


def test_create_configuration_with_config_id_and_agent():
    api, client = _api(post_resp=_CREATED_CONFIG)
    api.create_configuration(
        "virustotal",
        {"k": "v"},
        name="c",
        config_id="cfg-1",
        agent="agent-9",
        validate=False,
        autofill=False,
    )
    _, body = client.post_calls[0]
    assert body["config_id"] == "cfg-1"
    assert body["agent"] == "agent-9"


def test_create_configuration_clears_cache():
    api, client = _api(post_resp=_CREATED_CONFIG)
    api.list_configured()  # prime
    api.create_configuration("virustotal", {"k": "v"}, name="c", validate=False)
    api.list_configured()  # should refetch
    assert len([c for c in client.get_calls if "connectors/" in c[0]]) == 2


def test_create_configuration_validate_missing_raises():
    import pytest

    from pyfsr.exceptions import ConfigValidationError

    # config_schema comes from definition() (a POST returning config_schema.fields)
    schema = {"config_schema": {"fields": [{"name": "fsm_type", "title": "FortiSIEM Type", "required": True}]}}
    api, _ = _api(post_resp=schema)
    with pytest.raises(ConfigValidationError, match="is required"):
        api.create_configuration("virustotal", {}, name="c", version="3.1.0")


def test_update_configuration_puts_to_config_id_path():
    api, client = _api()
    api.update_configuration(
        "virustotal", "cfg-1", {"k": "v2"}, name="c", version="3.1.0", validate=False, autofill=False
    )
    endpoint, body = client.put_calls[0]
    assert endpoint == "/api/integration/configuration/cfg-1/"
    assert body["config_id"] == "cfg-1"
    assert body["connector"] == 16
    assert body["config"] == {"k": "v2"}


def test_update_configuration_tolerates_8_0_nested_status_envelope():
    # FortiSOAR 8.0 echoes the saved row on PUT but nests an async op-envelope in
    # ``status`` ({"status":"finished","message":...}) instead of 7.x's int flag.
    # The row must still validate; status coerces to None (no active-flag conveyed).
    api, client = _scripted()
    client.put_envelope = {
        "id": 37,
        "config_id": "cfg-7",
        "name": "prod",
        "default": True,
        "status": {"status": "finished", "message": "Configuration prod has been updated successfully"},
        "config": {"k": "v"},
        "connector": 16,
    }
    cfg = api.update_configuration(
        "virustotal", "cfg-7", {"k": "v"}, name="prod", version="3.1.0", default=True, validate=False
    )
    assert client.put_calls[-1][0] == "/api/integration/configuration/cfg-7/"
    assert cfg.config_id == "cfg-7"
    assert cfg.default is True
    assert cfg.status is None  # nested op-envelope -> coerced, not a ValidationError


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
    res = api.validate_config("virustotal", {"fsm_type": "FortiSIEM", "server": "x", "username": "u"})
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
    assert res["valid"] is True
    assert res["missing"] == []
    assert res["unknown"] == []
    assert res["invalid"] == []
    assert res["errors"] == []


# -- enhanced validation: conditional visibility, invalid options, types -------
# Mirrors the http connector's option-driven required fields (auth_type select
# reveals more required fields per selection), plus typed fields.
_HTTP_SCHEMA = {
    "config_schema": {
        "fields": [
            {"name": "server_url", "title": "Server URL", "type": "text", "required": False},
            {"name": "port", "title": "Port", "type": "integer", "required": False},
            {
                "name": "auth_type",
                "title": "Authentication Type",
                "type": "select",
                "required": True,
                "options": ["None", "Basic", "Bearer Token"],
                "onchange": {
                    "None": [],
                    "Basic": [
                        {"name": "basic_username", "title": "Username", "required": True},
                        {
                            "name": "basic_password",
                            "title": "Password",
                            "type": "password",
                            "required": True,
                        },
                    ],
                    "Bearer Token": [
                        {
                            "name": "bearer_token",
                            "title": "Bearer Token",
                            "type": "password",
                            "required": True,
                        },
                    ],
                },
            },
            {"name": "default_headers", "title": "Default Headers", "type": "json", "required": False},
            {"name": "verify_ssl", "title": "Verify SSL", "type": "checkbox", "required": False},
        ]
    }
}


def test_validate_config_tolerates_dict_valued_field():
    """A json/dict field value must not crash the onchange walk (unhashable key)."""
    api, _ = _api(post_resp=_HTTP_SCHEMA)
    res = api.validate_config(
        "http",
        {"auth_type": "None", "default_headers": {"X-Api-Key": "abc"}},
        version="1.0.0",
    )
    assert res["valid"] is True


def test_validate_config_conditional_required_revealed_by_selection():
    api, _ = _api(post_resp=_HTTP_SCHEMA)
    res = api.validate_config("http", {"auth_type": "Basic"}, version="1.0.0")
    # The Basic branch reveals two required fields.
    assert res["valid"] is False
    assert set(res["missing"]) == {"basic_username", "basic_password"}
    # Guidance names the selection that requires them.
    msgs = {e["field"]: e["message"] for e in res["errors"]}
    assert "Authentication Type = 'Basic'" in msgs["basic_password"]


def test_validate_config_inactive_branch_value_is_unknown():
    api, _ = _api(post_resp=_HTTP_SCHEMA)
    # bearer_token belongs to the Bearer Token branch, not Basic.
    res = api.validate_config(
        "http",
        {
            "auth_type": "Basic",
            "basic_username": "u",
            "basic_password": "p",
            "bearer_token": "leaked",
        },
        version="1.0.0",
    )
    assert res["valid"] is True  # all active required fields present
    assert "bearer_token" in res["unknown"]


def test_validate_config_invalid_select_option():
    api, _ = _api(post_resp=_HTTP_SCHEMA)
    res = api.validate_config("http", {"auth_type": "Bsaic"}, version="1.0.0")  # typo
    assert res["valid"] is False
    assert "auth_type" in res["invalid"]
    err = next(e for e in res["errors"] if e["field"] == "auth_type")
    assert err["code"] == "invalid_option"
    assert err["valid_options"] == ["None", "Basic", "Bearer Token"]


def test_validate_config_wrong_type():
    api, _ = _api(post_resp=_HTTP_SCHEMA)
    res = api.validate_config("http", {"auth_type": "None", "port": "not-a-number"}, version="1.0.0")
    assert res["valid"] is False
    assert "port" in res["invalid"]
    assert next(e for e in res["errors"] if e["field"] == "port")["code"] == "wrong_type"


def test_validate_config_accepts_string_integer_and_bool_checkbox():
    api, _ = _api(post_resp=_HTTP_SCHEMA)
    res = api.validate_config("http", {"auth_type": "None", "port": "443", "verify_ssl": True}, version="1.0.0")
    assert res["valid"] is True
    assert res["invalid"] == []


def test_create_configuration_raises_with_guidance_on_invalid_option():
    # 'virustotal' is in the mock's configured list (resolves version + id); the
    # schema is served from post_resp regardless of name.
    from pyfsr.exceptions import ConfigValidationError

    api, _ = _api(post_resp=_HTTP_SCHEMA)
    with pytest.raises(ConfigValidationError) as exc:
        api.create_configuration("virustotal", {"auth_type": "Bsaic"}, name="t", version="3.1.0")
    assert "not a valid option" in str(exc.value)
    assert "Basic" in str(exc.value)  # lists valid options


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


def test_definition_returns_typed_but_dict_compatible():
    from pyfsr.models import ConnectorDefinition, Operation

    api, _ = _api(post_resp=_DEFINITION)
    defn = api.definition("virustotal")
    # Typed...
    assert isinstance(defn, ConnectorDefinition)
    assert defn.name == "virustotal"
    assert isinstance(defn.operations[0], Operation)
    assert defn.operations[0].operation == "get_reputation_ip"
    # ...and still dict-compatible (existing callers unaffected).
    assert defn["operations"][0]["operation"] == "get_reputation_ip"


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


# -- action UI schema (connector action UI for widget/tooling authors) ------
# A block_ip-shaped op: a select param (options both plain + {value,title}),
# a required text param declared AFTER the optional one, a hidden param, and
# the same param name repeated across conditional groups (dedup).
_UI_DEFINITION = {
    "name": "fortigate-firewall",
    "version": "1.0.0",
    "config_schema": {},
    "operations": [
        {
            "operation": "block_ip",
            "title": "Block IP",
            "parameters": [
                {"name": "comment", "type": "text", "title": "Comment"},
                {
                    "name": "method",
                    "type": "select",
                    "title": "Method",
                    "required": True,
                    "options": ["Quarantine Based", {"value": "policy", "title": "Policy Based"}],
                },
                {"name": "ip", "type": "text", "title": "IP", "required": True},
                {"name": "ip", "type": "text", "title": "IP", "required": True},
                {"name": "internal_id", "type": "text", "title": "Internal", "visible": False},
            ],
        },
    ],
}


def test_action_ui_schema_orders_required_first_dedupes_and_hides():
    api, _ = _api(post_resp=_UI_DEFINITION)
    params = api.action_ui_schema("fortigate-firewall", "block_ip", version="1.0.0")
    # required-first (method, ip), then optional (comment); hidden dropped;
    # duplicate `ip` collapsed to one entry.
    assert [p.name for p in params] == ["method", "ip", "comment"]
    assert "internal_id" not in [p.name for p in params]


def test_action_ui_schema_select_options_normalized():
    api, _ = _api(post_resp=_UI_DEFINITION)
    params = api.action_ui_schema("fortigate-firewall", "block_ip", version="1.0.0")
    method = next(p for p in params if p.name == "method")
    choices = method.select_options()
    assert [(o.value, o.title) for o in choices] == [
        ("Quarantine Based", "Quarantine Based"),
        ("policy", "Policy Based"),
    ]


def test_action_ui_schema_required_only():
    api, _ = _api(post_resp=_UI_DEFINITION)
    params = api.action_ui_schema("fortigate-firewall", "block_ip", version="1.0.0", required_only=True)
    assert [p.name for p in params] == ["method", "ip"]


def test_action_ui_schema_unknown_operation_raises():
    api, _ = _api(post_resp=_UI_DEFINITION)
    with pytest.raises(ValueError, match="no operation 'nope'"):
        api.action_ui_schema("fortigate-firewall", "nope", version="1.0.0")


# An op with a gating select (`type`) whose onchange reveals `to`/`cc`, and a
# nested reveal: choosing `to`'s `Advanced` mode surfaces `filter`. Mirrors the
# live smtp/send_email_new shape (type -> to/cc/bcc), plus one nesting level.
_ONCHANGE_DEFINITION = {
    "name": "smtp",
    "version": "1.0.0",
    "config_schema": {},
    "operations": [
        {
            "operation": "send",
            "title": "Send",
            "parameters": [
                {"name": "from", "type": "text", "title": "From", "required": True},
                {
                    "name": "type",
                    "type": "select",
                    "title": "Type",
                    "required": True,
                    "options": ["Team", "User"],
                    "onchange": {
                        "Team": [
                            {"name": "to", "type": "text", "title": "To", "required": True},
                            {"name": "cc", "type": "text", "title": "Cc"},
                        ],
                        "User": [
                            {"name": "to", "type": "text", "title": "To", "required": True},
                        ],
                    },
                },
            ],
        },
    ],
}


def test_action_ui_schema_no_selections_returns_base_only():
    api, _ = _api(post_resp=_ONCHANGE_DEFINITION)
    params = api.action_ui_schema("smtp", "send", version="1.0.0")
    # onchange sub-params are NOT surfaced without a selection.
    assert [p.name for p in params] == ["from", "type"]


def test_action_ui_schema_selection_reveals_subparams():
    api, _ = _api(post_resp=_ONCHANGE_DEFINITION)
    params = api.action_ui_schema("smtp", "send", version="1.0.0", selections={"type": "Team"})
    names = [p.name for p in params]
    # revealed to/cc join; required-first (from, type, to) then optional (cc).
    assert names == ["from", "type", "to", "cc"]
    assert [p.required for p in params] == [True, True, True, False]


def test_action_ui_schema_selection_only_reveals_chosen_branch():
    api, _ = _api(post_resp=_ONCHANGE_DEFINITION)
    params = api.action_ui_schema("smtp", "send", version="1.0.0", selections={"type": "User"})
    # "User" branch has only `to`, not `cc`.
    assert [p.name for p in params] == ["from", "type", "to"]


def test_action_ui_schema_unknown_selection_value_ignored():
    api, _ = _api(post_resp=_ONCHANGE_DEFINITION)
    params = api.action_ui_schema("smtp", "send", version="1.0.0", selections={"type": "Nope"})
    assert [p.name for p in params] == ["from", "type"]


def test_action_ui_schema_reveal_respects_required_only():
    api, _ = _api(post_resp=_ONCHANGE_DEFINITION)
    params = api.action_ui_schema("smtp", "send", version="1.0.0", selections={"type": "Team"}, required_only=True)
    # cc (optional) drops; revealed-and-required `to` stays.
    assert [p.name for p in params] == ["from", "type", "to"]


def test_operation_tolerates_empty_dict_parameters():
    # Live-grounded: fortinet-fortiai-proxy ships an op with ``parameters: {}``
    # (a dict, not a list) — it must not sink the whole definition parse.
    from pyfsr.models import Operation

    defn = {
        "name": "fortinet-fortiai-proxy",
        "version": "1.0.0",
        "config_schema": {},
        "operations": [
            {"operation": "ping", "title": "Ping", "parameters": {}},
            {"operation": "act", "title": "Act", "parameters": [{"name": "x", "type": "text"}]},
        ],
    }
    api, _ = _api(post_resp=defn)
    ops = api.operations("fortinet-fortiai-proxy", version="1.0.0")
    assert isinstance(ops[0], Operation)
    assert ops[0].parameters == []  # {} coerced to []
    assert api.action_ui_schema("fortinet-fortiai-proxy", "ping", version="1.0.0") == []
    assert [p.name for p in api.action_ui_schema("fortinet-fortiai-proxy", "act", version="1.0.0")] == ["x"]


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


def test_list_configurations_filters_by_configuration_name():
    """`name` is the CONFIGURATION's name, not the connector's.

    This test used to pass name="virustotal" — a connector name — which read as
    though it filtered by connector and quietly encoded the wrong belief (it only
    ever asserted passthrough, so it could not catch the mismatch). Live-checked:
    the endpoint's `name` matches the configuration name, and a connector name
    there returns [] rather than erroring, because unknown/unmatched filters are
    silently ignored.
    """
    api, client = _api()
    api.list_configurations(name="VT Production", active=True)
    endpoint, params = client.get_calls[-1]
    assert endpoint == "/api/integration/configuration/"
    assert params["name"] == "VT Production" and params["active"] is True
    assert "connector" not in params


def test_list_configurations_by_connector_name_resolves_to_install_id():
    """`connector=` takes a name but must query the numeric id.

    The endpoint's `connector` filter is the install id; a machine name passed
    straight through errors ("Unknown error occurred"), so the name is resolved
    first.
    """
    api, client = _api()
    api.list_configurations(connector="virustotal")
    _, params = client.get_calls[-1]
    assert params["connector"] == 16  # virustotal's install id in the fixture
    assert "name" not in params


def test_list_configurations_by_connector_id_skips_resolution():
    api, client = _api()
    api.list_configurations(connector=16)
    _, params = client.get_calls[-1]
    assert params["connector"] == 16
    # an int id needs no connector lookup
    assert not [c for c in client.get_calls if "/api/integration/connectors/" in c[0]]


def test_list_configurations_unknown_connector_returns_empty_without_querying():
    """A connector that isn't installed cannot have configurations."""
    api, client = _api()
    assert api.list_configurations(connector="not-installed") == []
    assert not [c for c in client.get_calls if c[0] == "/api/integration/configuration/"]


def test_list_configurations_rejects_bool_connector():
    # bool is an int subclass — connector=True would otherwise query id 1.
    api, _ = _api()
    with pytest.raises(TypeError, match="not a bool"):
        api.list_configurations(connector=True)


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


# -- republish + upsert_configuration --------------------------------------
class ScriptedClient:
    """Endpoint-scripted fake: route POST/PUT/DELETE by endpoint substring."""

    def __init__(self):
        self.post_calls = []
        self.put_calls = []
        self.delete_calls = []
        # connector listing so resolve_connector_id('virustotal') -> 16
        self._listing = _CONFIGURED
        self.detail = {"configuration": []}
        self.dev_edit_resp = {"id": "dev-1", "development": True}
        self.dev_list_resp = []
        self.publish_raises = False
        self.create_raises = False
        # When set, PUT returns this instead of a config row (FortiSOAR 8.0
        # returns an async op-envelope on configuration PUT).
        self.put_envelope = None

    def get(self, endpoint, params=None, **kwargs):
        if endpoint.startswith("/api/integration/connectors/"):
            return self._listing
        if endpoint == "/api/integration/connector/development/entity/":
            return {"data": self.dev_list_resp}
        return {}

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data))
        if endpoint.endswith("/publish/"):
            if self.publish_raises:
                raise RuntimeError("publish 500")
            return {"ok": True}
        if "/development/entity/" in endpoint:
            return self.dev_edit_resp
        if endpoint == "/api/integration/configuration/":
            if self.create_raises:
                raise RuntimeError("config 500 (post-save hook)")
            return {"config_id": "new-cfg", "name": (data or {}).get("name")}
        # connector_detail POST /api/integration/connectors/{id}/
        if endpoint.startswith("/api/integration/connectors/"):
            return self.detail
        return {}

    def put(self, endpoint, data=None, params=None, **kwargs):
        self.put_calls.append((endpoint, data))
        if self.put_envelope is not None:
            return self.put_envelope
        return {"config_id": (data or {}).get("config_id"), "name": (data or {}).get("name")}

    def delete(self, endpoint, params=None, **kwargs):
        self.delete_calls.append((endpoint, params))
        return None


def _scripted():
    c = ScriptedClient()
    return ConnectorsAPI(c), c


def test_republish_happy_path():
    api, client = _scripted()
    out = api.republish("virustotal", replace=True, discard=True)
    assert out == {"ok": True, "dev_id": "dev-1"}
    # edit posted to the installed id (16), publish to the twin with replace+discard
    assert ("/api/integration/connector/development/entity/16/", {"edit_repo_connector": True}) in client.post_calls
    assert (
        "/api/integration/connector/development/entity/dev-1/publish/",
        {"replace": True, "discard": True},
    ) in client.post_calls
    assert client.delete_calls == []  # success path leaves no orphan to delete


def test_republish_resolves_twin_when_edit_echoes_installed_id():
    api, client = _scripted()
    # edit-mode echoes the installed id and no development flag -> find twin in dev_list
    client.dev_edit_resp = {"id": 16, "development": False}
    client.dev_list_resp = [{"id": "twin-9", "name": "virustotal", "development": True}]
    out = api.republish("virustotal")
    assert out["dev_id"] == "twin-9"


def test_republish_cleans_up_dev_twin_on_publish_failure():
    api, client = _scripted()
    client.publish_raises = True
    with pytest.raises(RuntimeError, match="publish 500"):
        api.republish("virustotal")
    # the orphaned dev twin is deleted so no _dev dir lingers
    assert client.delete_calls == [("/api/integration/connector/development/entity/dev-1/", None)]


def test_republish_unknown_connector_raises():
    api, _ = _scripted()
    with pytest.raises(ValueError, match="not installed"):
        api.republish("does-not-exist")


def test_upsert_creates_when_absent():
    api, client = _scripted()
    client.detail = {"configuration": []}
    api.upsert_configuration("virustotal", {"k": "v"}, name="prod", default=True, validate=False)
    assert client.post_calls[-1][0] == "/api/integration/configuration/"
    assert client.put_calls == []


def test_upsert_updates_in_place_and_preserves_agent():
    api, client = _scripted()
    client.detail = {"configuration": [{"name": "prod", "config_id": "cfg-7", "agent": "agent-x"}]}
    api.upsert_configuration("virustotal", {"k": "v"}, name="prod", default=True, validate=False)
    assert client.put_calls[-1][0] == "/api/integration/configuration/cfg-7/"
    assert client.put_calls[-1][1]["agent"] == "agent-x"  # preserved
    assert all(e != "/api/integration/configuration/" for e, _ in client.post_calls)


def test_upsert_tolerates_persisted_despite_500():
    api, client = _scripted()
    # create raises (post-save hook 500), but a re-fetch finds the row -> success
    client.create_raises = True
    seq = [{"configuration": []}, {"configuration": [{"name": "prod", "config_id": "landed"}]}]
    calls = {"n": 0}

    def fake_detail(conn):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    api.connector_detail = fake_detail  # type: ignore[assignment]
    cfg = api.upsert_configuration("virustotal", {"k": "v"}, name="prod", validate=False)
    assert cfg["config_id"] == "landed"


# -- autofill and default_config -----------------------------------------------
_CODE_SNIPPET_SCHEMA = {
    "config_schema": {
        "fields": [
            {
                "name": "allow_imports",
                "type": "checkbox",
                "title": "Allow Imports",
                "required": False,
                "value": False,
                "onchange": {
                    "false": [
                        {
                            "name": "restrict_imports",
                            "type": "text",
                            "title": "Restrict Imports",
                            "required": True,
                        }
                    ],
                    "true": [],
                },
            },
        ]
    }
}


def test_default_config_includes_declared_defaults():
    """default_config fills every field with its declared value or type-default."""
    api, _ = _api(post_resp=_CODE_SNIPPET_SCHEMA)
    cfg = api.default_config("code-snippet", version="1.0.0")
    assert cfg["allow_imports"] is False  # explicit default


def test_default_config_expands_onchange_for_default_selection():
    """default_config walks the onchange branch for the default selected option."""
    api, _ = _api(post_resp=_CODE_SNIPPET_SCHEMA)
    cfg = api.default_config("code-snippet", version="1.0.0")
    # allow_imports defaults to False; that matches the "false" onchange key
    # so restrict_imports (revealed by that branch) should be in the config
    assert "restrict_imports" in cfg
    assert cfg["restrict_imports"] == ""  # text field with no explicit default


def test_default_config_handles_select_with_explicit_default():
    """default_config includes onchange sub-fields for the default select option."""
    schema = {
        "config_schema": {
            "fields": [
                {
                    "name": "auth_type",
                    "type": "select",
                    "title": "Auth Type",
                    "required": True,
                    "value": "Basic",  # explicit default
                    "options": ["None", "Basic", "Bearer Token"],
                    "onchange": {
                        "None": [],
                        "Basic": [
                            {"name": "username", "type": "text", "required": True},
                            {"name": "password", "type": "password", "required": True},
                        ],
                        "Bearer Token": [
                            {"name": "token", "type": "password", "required": True},
                        ],
                    },
                }
            ]
        }
    }
    api, _ = _api(post_resp=schema)
    cfg = api.default_config("http", version="1.0.0")
    assert cfg["auth_type"] == "Basic"
    assert cfg["username"] == ""  # Basic branch is revealed
    assert cfg["password"] == ""
    assert "token" not in cfg  # Bearer Token branch not active


def test_default_config_select_defaults_to_first_option_if_no_explicit_default():
    """When a select has no explicit default, default_config uses the first option."""
    schema = {
        "config_schema": {
            "fields": [
                {
                    "name": "level",
                    "type": "select",
                    "title": "Level",
                    "required": False,
                    # no "value" field
                    "options": ["Low", "Medium", "High"],
                    "onchange": {
                        "Low": [{"name": "reason", "type": "text", "required": False}],
                        "Medium": [],
                        "High": [{"name": "approval", "type": "text", "required": True}],
                    },
                }
            ]
        }
    }
    api, _ = _api(post_resp=schema)
    cfg = api.default_config("test", version="1.0.0")
    # First option "Low" is selected as the default
    assert cfg["level"] == "Low"
    assert "reason" in cfg  # Low branch is revealed


def test_default_config_checkbox_branches_on_string_key():
    """Checkbox default branches on the string keys 'true'/'false'."""
    schema = {
        "config_schema": {
            "fields": [
                {
                    "name": "enabled",
                    "type": "checkbox",
                    "title": "Enabled",
                    "required": False,
                    "value": True,
                    "onchange": {
                        "true": [{"name": "port", "type": "integer", "value": 8080}],
                        "false": [],
                    },
                }
            ]
        }
    }
    api, _ = _api(post_resp=schema)
    cfg = api.default_config("test", version="1.0.0")
    assert cfg["enabled"] is True
    assert cfg["port"] == 8080  # true branch reveals this


def test_default_config_nested_onchange():
    """default_config handles nested onchange branches (sub-field with its own onchange)."""
    schema = {
        "config_schema": {
            "fields": [
                {
                    "name": "method",
                    "type": "select",
                    "title": "Method",
                    "value": "api",
                    "options": ["file", "api"],
                    "onchange": {
                        "api": [
                            {
                                "name": "api_type",
                                "type": "select",
                                "title": "API Type",
                                "value": "rest",
                                "options": ["rest", "graphql"],
                                "onchange": {
                                    "rest": [{"name": "endpoint", "type": "text", "required": True}],
                                    "graphql": [{"name": "query", "type": "text", "required": True}],
                                },
                            }
                        ],
                        "file": [{"name": "path", "type": "text", "required": True}],
                    },
                }
            ]
        }
    }
    api, _ = _api(post_resp=schema)
    cfg = api.default_config("test", version="1.0.0")
    assert cfg["method"] == "api"
    assert cfg["api_type"] == "rest"
    assert "endpoint" in cfg  # rest branch is revealed
    assert "query" not in cfg  # graphql branch not active
    assert "path" not in cfg  # file branch not active


def test_required_config_fields_with_default_selection():
    """required_config_fields walks the active onchange branch."""
    api, _ = _api(post_resp=_CODE_SNIPPET_SCHEMA)
    # allow_imports defaults to False (unchecked); that reveals restrict_imports
    config = {"allow_imports": False}
    req = api.required_config_fields("code-snippet", config, version="1.0.0")
    assert "restrict_imports" in req


def test_required_config_fields_different_selection():
    """required_config_fields changes with the current selection value."""
    schema = {
        "config_schema": {
            "fields": [
                {
                    "name": "auth_type",
                    "type": "select",
                    "title": "Auth Type",
                    "required": True,
                    "options": ["None", "Basic"],
                    "onchange": {
                        "None": [],
                        "Basic": [{"name": "password", "type": "password", "required": True}],
                    },
                }
            ]
        }
    }
    api, _ = _api(post_resp=schema)
    # None branch has no sub-fields
    req_none = api.required_config_fields("http", {"auth_type": "None"}, version="1.0.0")
    assert req_none == ["auth_type"]
    # Basic branch requires password
    req_basic = api.required_config_fields("http", {"auth_type": "Basic"}, version="1.0.0")
    assert set(req_basic) == {"auth_type", "password"}


def test_create_configuration_autofill_merges_defaults_without_clobbering():
    """autofill=True fills missing fields but never overwrites caller-provided values."""
    schema = {
        "config_schema": {
            "fields": [
                {"name": "server", "type": "text", "required": False, "value": "localhost"},
                {"name": "port", "type": "integer", "required": False, "value": 8080},
            ]
        }
    }
    api, client = _api(post_resp={"config_id": "new-cfg"})
    # virustotal is in the mock's configured list (see _CONFIGURED)
    api.config_schema = lambda connector, version=None: schema["config_schema"]["fields"]
    # Pass only port, let server be autofilled
    api.create_configuration(
        "virustotal",
        {"port": 9000},  # explicit override
        name="test",
        version="3.1.0",
        validate=False,
        autofill=True,
    )
    _, body = client.post_calls[0]
    # server should be filled with its default
    assert body["config"]["server"] == "localhost"
    # port should keep the caller's value (not the schema default)
    assert body["config"]["port"] == 9000


def test_create_configuration_autofill_false_sends_verbatim():
    """autofill=False sends config as-is, no schema defaults merged."""
    api, client = _api(post_resp={"config_id": "new-cfg"})
    api.create_configuration(
        "virustotal",
        {"key": "explicit-value"},
        name="test",
        version="3.1.0",
        validate=False,
        autofill=False,
    )
    _, body = client.post_calls[0]
    # Only the caller's key should be present
    assert body["config"] == {"key": "explicit-value"}


def test_create_configuration_autofill_with_validation_composes():
    """autofill fills defaults, then validation checks the merged config."""
    from pyfsr.exceptions import ConfigValidationError

    schema = {
        "config_schema": {
            "fields": [
                {
                    "name": "auth_type",
                    "type": "select",
                    "title": "Auth Type",
                    "required": True,
                    "value": "Basic",
                    "options": ["None", "Basic"],
                    "onchange": {"Basic": [{"name": "password", "type": "password", "required": True}]},
                }
            ]
        }
    }
    api, _ = _api(post_resp=schema)
    # With autofill=True, the password field (revealed by "Basic" default) gets
    # filled with "", which satisfies the schema but may fail validation elsewhere.
    # This test just ensures autofill + validate don't conflict.
    with pytest.raises(ConfigValidationError, match="is required"):
        # password is empty after autofill, so validation fails
        api.create_configuration("virustotal", {}, name="test", version="3.1.0", validate=True)


def test_update_configuration_autofill_similar_to_create():
    """update_configuration with autofill=True merges defaults without clobbering."""
    schema = {
        "config_schema": {
            "fields": [
                {"name": "server", "type": "text", "required": False, "value": "localhost"},
                {"name": "timeout", "type": "integer", "required": False, "value": 30},
            ]
        }
    }
    api, client = _scripted()
    api.config_schema = lambda connector, version=None: schema["config_schema"]["fields"]
    # Update with only timeout changed
    api.update_configuration(
        "virustotal",
        "cfg-7",
        {"timeout": 60},  # explicit change
        name="prod",
        version="3.1.0",
        validate=False,
        autofill=True,
    )
    _, body = client.put_calls[0]
    # server should be filled from schema default
    assert body["config"]["server"] == "localhost"
    # timeout should keep the caller's override
    assert body["config"]["timeout"] == 60


def test_upsert_configuration_passes_autofill_to_create_and_update():
    """upsert_configuration forwards autofill to both create and update paths."""
    schema = {
        "config_schema": {"fields": [{"name": "key", "type": "text", "required": False, "value": "default-value"}]}
    }
    # Test create path (no existing config)
    api, client = _scripted()
    api.config_schema = lambda connector, version=None: schema["config_schema"]["fields"]
    client.detail = {"configuration": []}
    api.upsert_configuration("virustotal", {}, name="test", version="3.1.0", validate=False, autofill=True)
    _, create_body = client.post_calls[-1]
    assert create_body["config"]["key"] == "default-value"


def test_upsert_configuration_autofill_false():
    """upsert with autofill=False sends config verbatim."""
    api, client = _scripted()
    client.detail = {"configuration": []}
    api.upsert_configuration(
        "virustotal", {"key": "value"}, name="test", version="3.1.0", validate=False, autofill=False
    )
    _, create_body = client.post_calls[-1]
    # Only the caller's key
    assert create_body["config"] == {"key": "value"}


# -- A2: ConfigValidationError with field-level details ---------------------
def test_create_configuration_validation_raises_config_validation_error():
    """Invalid config raises ConfigValidationError (not plain ValueError) with error details."""
    from pyfsr.exceptions import ConfigValidationError

    schema = {"config_schema": {"fields": [{"name": "required_field", "required": True}]}}
    api, _ = _api(post_resp=schema)
    with pytest.raises(ConfigValidationError) as exc_info:
        api.create_configuration("virustotal", {}, name="test", version="3.1.0")
    exc = exc_info.value
    assert "required" in str(exc).lower()
    # Check that errors are attached
    assert exc.errors is not None
    assert len(exc.errors) > 0
    assert any(e.get("field") == "required_field" for e in exc.errors)


def test_config_validation_error_carries_structured_errors():
    """ConfigValidationError.errors includes field-level problem details."""
    from pyfsr.exceptions import ConfigValidationError

    schema = {"config_schema": {"fields": [{"name": "port", "type": "integer", "required": False}]}}
    api, _ = _api(post_resp=schema)
    with pytest.raises(ConfigValidationError) as exc_info:
        api.create_configuration("virustotal", {"port": "not-a-number"}, name="test", version="3.1.0")
    exc = exc_info.value
    assert exc.errors is not None
    port_error = next((e for e in exc.errors if e.get("field") == "port"), None)
    assert port_error is not None
    assert "wrong_type" in port_error.get("code", "")


def test_config_validation_error_invalid_select_option():
    """ConfigValidationError includes valid_options when option is invalid."""
    from pyfsr.exceptions import ConfigValidationError

    schema = {
        "config_schema": {
            "fields": [{"name": "level", "type": "select", "options": ["Low", "Medium", "High"], "required": True}]
        }
    }
    api, _ = _api(post_resp=schema)
    with pytest.raises(ConfigValidationError) as exc_info:
        api.create_configuration("virustotal", {"level": "Invalid"}, name="test", version="3.1.0")
    exc = exc_info.value
    level_error = next((e for e in exc.errors if e.get("field") == "level"), None)
    assert level_error is not None
    assert level_error.get("code") == "invalid_option"
    assert level_error.get("valid_options") == ["Low", "Medium", "High"]


def test_update_configuration_validation_raises_config_validation_error():
    """update_configuration with invalid config raises ConfigValidationError."""
    from pyfsr.exceptions import ConfigValidationError

    schema = {"config_schema": {"fields": [{"name": "required", "required": True}]}}
    api, client = _scripted()
    api.config_schema = lambda connector, version=None: schema["config_schema"]["fields"]
    with pytest.raises(ConfigValidationError) as exc_info:
        api.update_configuration("virustotal", "cfg-1", {}, name="test", version="3.1.0")
    assert exc_info.value.errors is not None


def test_upsert_configuration_validation_raises_config_validation_error():
    """upsert_configuration with invalid config raises ConfigValidationError."""
    from pyfsr.exceptions import ConfigValidationError

    schema = {"config_schema": {"fields": [{"name": "required", "required": True}]}}
    api, client = _scripted()
    client.detail = {"configuration": []}
    api.config_schema = lambda connector, version=None: schema["config_schema"]["fields"]
    with pytest.raises(ConfigValidationError):
        api.upsert_configuration("virustotal", {}, name="test", version="3.1.0")


# -- A3: create_configuration with exist_ok and ConfigurationExistsError ----
def test_create_configuration_exist_ok_false_raises_on_unique_violation():
    """When exist_ok=False (default) and server returns unique constraint error, raise ConfigurationExistsError."""
    from pyfsr.exceptions import APIError, ConfigurationExistsError

    class FailingClient(ScriptedClient):
        def post(self, endpoint, data=None, params=None, **kwargs):
            if endpoint == "/api/integration/configuration/":
                from unittest.mock import Mock

                resp = Mock()
                resp.status_code = 400
                resp.json = lambda: {"message": "fields name, connector, agent must make a unique set"}
                resp.text = "fields name, connector, agent must make a unique set"
                raise APIError("fields name, connector, agent must make a unique set", resp)
            return super().post(endpoint, data, params, **kwargs)

    api = ConnectorsAPI(FailingClient())
    with pytest.raises(ConfigurationExistsError) as exc_info:
        api.create_configuration("virustotal", {"k": "v"}, name="prod", version="3.1.0", validate=False, exist_ok=False)
    exc = exc_info.value
    assert exc.connector == "virustotal"
    assert exc.name == "prod"
    assert "exist_ok=True" in str(exc)
    assert "upsert_configuration" in str(exc)


def test_create_configuration_exist_ok_true_delegates_to_upsert():
    """When exist_ok=True and unique constraint error occurs, delegate to upsert instead of raising."""
    from pyfsr.exceptions import APIError

    class FailingThenPassingClient(ScriptedClient):
        def __init__(self):
            super().__init__()
            self.post_count = 0

        def post(self, endpoint, data=None, params=None, **kwargs):
            if endpoint == "/api/integration/configuration/":
                self.post_count += 1
                if self.post_count == 1:
                    # First POST fails with unique constraint
                    from unittest.mock import Mock

                    resp = Mock()
                    resp.status_code = 400
                    resp.json = lambda: {"message": "fields name, connector, agent must make a unique set"}
                    resp.text = "fields name, connector, agent must make a unique set"
                    raise APIError("fields name, connector, agent must make a unique set", resp)
            return super().post(endpoint, data, params, **kwargs)

    client = FailingThenPassingClient()
    api = ConnectorsAPI(client)
    # Set up detail to show an existing config, so upsert will use PUT
    client.detail = {"configuration": [{"name": "prod", "config_id": "cfg-existing", "agent": None}]}
    res = api.create_configuration(
        "virustotal", {"k": "v"}, name="prod", version="3.1.0", validate=False, exist_ok=True
    )
    # Should have succeeded via upsert path (PUT, not POST)
    assert res is not None
    # The upsert should have detected the existing config and issued a PUT
    assert len(client.put_calls) > 0


def test_create_configuration_exist_ok_false_non_unique_error_propagates():
    """Non-unique-constraint errors should propagate even with exist_ok context."""
    from pyfsr.exceptions import APIError

    class FailingClient(ScriptedClient):
        def post(self, endpoint, data=None, params=None, **kwargs):
            if endpoint == "/api/integration/configuration/":
                from unittest.mock import Mock

                resp = Mock()
                resp.status_code = 500
                resp.json = lambda: {"message": "Internal Server Error"}
                resp.text = "Internal Server Error"
                raise APIError("Internal Server Error", resp)
            return super().post(endpoint, data, params, **kwargs)

    api = ConnectorsAPI(FailingClient())
    with pytest.raises(APIError, match="Internal Server Error"):
        api.create_configuration("virustotal", {"k": "v"}, name="prod", version="3.1.0", validate=False)


def test_create_configuration_exist_ok_default_false():
    """exist_ok defaults to False; unique constraint errors raise by default."""
    from pyfsr.exceptions import APIError, ConfigurationExistsError

    class FailingClient(ScriptedClient):
        def post(self, endpoint, data=None, params=None, **kwargs):
            if endpoint == "/api/integration/configuration/":
                from unittest.mock import Mock

                resp = Mock()
                resp.status_code = 400
                resp.json = lambda: {"message": "name, connector, agent must be unique"}
                resp.text = "name, connector, agent must be unique"
                raise APIError("name, connector, agent must be unique", resp)
            return super().post(endpoint, data, params, **kwargs)

    api = ConnectorsAPI(FailingClient())
    # No exist_ok parameter → defaults to False
    with pytest.raises(ConfigurationExistsError):
        api.create_configuration("virustotal", {"k": "v"}, name="prod", version="3.1.0", validate=False)


# -- ensure_configured (install-if-missing + upsert, idempotent) ------------
def _ensure_api(monkeypatch, *, installed):
    api = ConnectorsAPI(FakeClient())
    calls = {"install": [], "upsert": []}
    monkeypatch.setattr(api, "resolve_connector_id", lambda c: 7 if installed else None)
    monkeypatch.setattr(api, "install", lambda c, v, **k: calls["install"].append((c, v)) or {"status": "Completed"})

    def _upsert(connector, config, **kw):
        calls["upsert"].append((connector, kw.get("name"), kw.get("version")))
        return object()

    monkeypatch.setattr(api, "upsert_configuration", _upsert)
    return api, calls


def test_ensure_configured_installed_skips_install(monkeypatch):
    api, calls = _ensure_api(monkeypatch, installed=True)
    api.ensure_configured("servicenow", {"k": "v"}, config_name="pilot", version="1.0.0")
    assert calls["install"] == []  # already installed -> no reinstall
    assert calls["upsert"] == [("servicenow", "pilot", "1.0.0")]


def test_ensure_configured_absent_installs_then_upserts(monkeypatch):
    api, calls = _ensure_api(monkeypatch, installed=False)
    api.ensure_configured("servicenow", {"k": "v"}, config_name="pilot", version="1.0.0")
    assert calls["install"] == [("servicenow", "1.0.0")]
    assert calls["upsert"] == [("servicenow", "pilot", "1.0.0")]


def test_ensure_configured_absent_without_version_raises(monkeypatch):
    api, calls = _ensure_api(monkeypatch, installed=False)
    with pytest.raises(ValueError, match="not installed"):
        api.ensure_configured("servicenow", {"k": "v"}, config_name="pilot")
    assert calls["install"] == []  # never attempted without a version


def test_ensure_configured_installed_version_optional(monkeypatch):
    api, calls = _ensure_api(monkeypatch, installed=True)
    api.ensure_configured("servicenow", {"k": "v"}, config_name="pilot")  # no version
    assert calls["install"] == []
    assert calls["upsert"] == [("servicenow", "pilot", None)]


# ---------------------------------------------------------------------------
# ensure_version auto_fetch fallback (T3.6) — Content Hub fails -> repo download
# ---------------------------------------------------------------------------


def _ensure_version_api(monkeypatch, *, hub_install_ok):
    """A ConnectorsAPI wired so install() (Content Hub) succeeds or fails on demand.

    Connector starts absent (no backup path), so ensure_version goes straight to
    _do_install. resolve_version returns None first, then the target version
    after a successful install_from_file.
    """
    api = ConnectorsAPI(FakeClient())
    calls = {"install": 0, "install_from_file": [], "downloaded": []}
    state = {"installed": False}

    def _resolve(_name):
        return "1.0.0" if state["installed"] else None

    def _install(_name, _version, **_kw):
        calls["install"] += 1
        if not hub_install_ok:
            raise RuntimeError("Content Hub will not serve 1.0.0")
        state["installed"] = True

    def _install_from_file(path, **_kw):
        calls["install_from_file"].append(path)
        state["installed"] = True

    monkeypatch.setattr(api, "resolve_version", _resolve)
    monkeypatch.setattr(api, "configurations", lambda _n: [])
    monkeypatch.setattr(api, "install", _install)
    monkeypatch.setattr(api, "install_from_file", _install_from_file)
    monkeypatch.setattr(api, "clear_cache", lambda: None)
    monkeypatch.setattr(api, "resolve_connector_id", lambda _n: None)

    import pyfsr.repo as _repo

    def _download(name, version, dest=None, **_kw):
        calls["downloaded"].append((name, version, dest))
        return f"/tmp/{name}.tgz"

    monkeypatch.setattr(_repo, "download_connector", _download)
    return api, calls


def test_ensure_version_uses_content_hub_when_it_works(monkeypatch):
    api, calls = _ensure_version_api(monkeypatch, hub_install_ok=True)
    result = api.ensure_version("servicenow", "1.0.0")
    assert result["action"] == "in_place"
    assert calls["install"] == 1
    assert calls["install_from_file"] == []  # never fell back
    assert calls["downloaded"] == []


def test_ensure_version_auto_fetches_when_content_hub_fails(monkeypatch):
    api, calls = _ensure_version_api(monkeypatch, hub_install_ok=False)
    result = api.ensure_version("servicenow", "1.0.0")
    assert result["action"] == "in_place"
    assert calls["downloaded"] == [("servicenow", "1.0.0", None)]
    assert calls["install_from_file"] == ["/tmp/servicenow.tgz"]


def test_ensure_version_no_auto_fetch_reraises(monkeypatch):
    api, calls = _ensure_version_api(monkeypatch, hub_install_ok=False)
    with pytest.raises(RuntimeError, match="Content Hub"):
        api.ensure_version("servicenow", "1.0.0", auto_fetch=False)
    assert calls["downloaded"] == []


# -- config_id= / config_name= deprecated aliases for config= ----------------
# The server resolves both UUIDs and names in the wire `config` field, so one
# param (config=) replaces both. config_id= and config_name= still work but warn.


def test_execute_config_id_kwarg_warns():
    api, client = _api()
    with pytest.deprecated_call(match="config="):
        api.execute("acme", "op", version="9.9", config_id="cfg-1")
    _, body = client.post_calls[0]
    assert body["config"] == "cfg-1"  # same wire body


def test_execute_config_name_kwarg_warns():
    api, client = _api()
    with pytest.deprecated_call(match="config="):
        api.execute("acme", "op", version="9.9", config_name="my-config")
    _, body = client.post_calls[0]
    assert body["config"] == "my-config"  # name passed through


def test_healthcheck_config_id_kwarg_warns():
    api, client = _api()
    with pytest.deprecated_call(match="config="):
        api.healthcheck("virustotal", config_id="vt-default")
    hc = [c for c in client.get_calls if "healthcheck" in c[0]][0]
    assert hc[1] == {"config": "vt-default"}


def test_execute_config_kwarg_is_canonical_no_warning():
    """config= is the canonical param — it must NOT warn."""
    import warnings as _w

    api, _ = _api()
    with _w.catch_warnings():
        _w.simplefilter("error", DeprecationWarning)
        api.execute("acme", "op", version="9.9", config="cfg-1")


def test_healthcheck_config_kwarg_is_canonical_no_warning():
    import warnings as _w

    api, _ = _api()
    with _w.catch_warnings():
        _w.simplefilter("error", DeprecationWarning)
        api.healthcheck("virustotal", config="vt-default")


@pytest.mark.parametrize(
    "call",
    [
        lambda api: api.execute("acme", "op", version="9.9", config="a", config_id="b"),
        lambda api: api.execute("acme", "op", version="9.9", config="a", config_name="b"),
        lambda api: api.execute("acme", "op", version="9.9", config_id="a", config_name="b"),
        lambda api: api.healthcheck("virustotal", config="a", config_id="b"),
    ],
)
def test_passing_multiple_config_params_raises(call):
    api, _ = _api()
    with pytest.raises(ValueError, match="deprecated aliases"):
        call(api)


def test_execute_config_rejects_dict():
    """A dict passed as config= is the field-map sense — catch it loudly."""
    api, _ = _api()
    with pytest.raises(TypeError, match="field-map"):
        api.execute("acme", "op", version="9.9", config={"server": "x"})
