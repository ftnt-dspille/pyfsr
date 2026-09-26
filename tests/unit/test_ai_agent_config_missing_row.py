"""An agent with no config row: fsr-ai 500s on its config GET (live on 8.0.1)."""

from types import SimpleNamespace

import pytest

from pyfsr.api.ai import AIApi
from pyfsr.exceptions import APIError

DEFAULT = {"name": "Default", "config": {"config_type": "default", "mcp_server": ["soc"], "llm_provider": "llm-1"}}


class FakeClient:
    def __init__(self, responses):
        self.responses, self.posts = responses, []

    def get(self, endpoint, params=None, **kw):
        value = self.responses.get(endpoint, {})
        if isinstance(value, Exception):
            raise value
        return value

    def post(self, endpoint, data=None, **kw):
        self.posts.append((endpoint, data))
        return data


def _err(code):
    return APIError("Internal server error", SimpleNamespace(status_code=code))


def _client(config_error):
    return FakeClient(
        {
            "/api/ai/agent/config/endpoint-telemetry/1.0.0": config_error,
            "/api/ai/agent/endpoint-telemetry/1.0.0": {"name": "endpoint-telemetry", "version": "1.0.0"},
            "/api/ai/agent/config/default": DEFAULT,
        }
    )


def test_missing_config_row_reads_as_the_default_config():
    dto = AIApi(_client(_err(500))).get_agent_config("endpoint-telemetry", "1.0.0")
    assert (dto.agent_name, dto.default, dto.config_id) == ("endpoint-telemetry", True, None)
    assert dto.config.config_type == "default" and dto.config.mcp_server == ["soc"]


def test_allow_creates_the_row_from_the_default():
    c = _client(_err(500))
    AIApi(c).allow_mcp_server_for_agent("endpoint-telemetry", "1.0.0", "eval-edr")
    endpoint, body = c.posts[-1]
    assert endpoint == "/api/ai/agent/config"
    assert len(body["config_id"]) == 36  # minted: the column is NOT NULL (live on 8.0.1)
    assert body["name"] == body["config"]["name"] == "Custom Configuration"
    assert body["config"]["mcp_server"] == ["soc", "eval-edr"]
    assert body["config"]["config_type"] == "custom"


def test_other_errors_still_raise():
    with pytest.raises(APIError):
        AIApi(_client(_err(403))).get_agent_config("endpoint-telemetry", "1.0.0")
