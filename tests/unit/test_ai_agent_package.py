"""Tests for the AI agent *package* surface: parse/validate, pack, import, export."""

from __future__ import annotations

import json
import zipfile

import pytest

from pyfsr import pack_agent
from pyfsr.api.ai import AIApi
from pyfsr.models import AgentPackage

# ------------------------------------------------------------------ fixtures

_INFO = {
    "name": "widget-counter",
    "label": "Widget Counter",
    "agentclass": "WidgetCounterAgent",
    "version": "1.0.0",
    "description": "Counts things.",
    "publisher": "ACME",
    "icon_small_name": "small.png",
    "icon_large_name": "large.png",
    "tags": ["Insight"],
    "fsrMinCompatibility": "8.0.0",
}

_PROMPT_UUID = "d63013a4-8e06-4d87-beba-ef0a2e6d51f5"
_PROMPT_YAML = f"""\
prompts:
  "{_PROMPT_UUID}":
    name: Count things
    system_instruction: "You count. {{data}}"
    user_instruction: "{{query}}"
    validation_instruction: null
    response_format: null
    description: counting
"""

_MEMORY_YAML = """\
allowed_tools:
  "2e541107-0b55-4623-b297-dfac495a863e": ['query_records']
  "c8ef6eb0-3d0e-4c5b-b98c-6d03a23148d6": []
"""

_AGENT_PY = f"""\
from agents.base_agent import BaseAgent


class WidgetCounterAgent(BaseAgent):
    def act(self, input_data):
        self.prompt = self.get_prompt_by_uuid('{_PROMPT_UUID}')
        return {{"status": "success"}}
"""


def _make_agent_dir(tmp_path, *, info=None, agent_py=_AGENT_PY, with_icons=True):
    root = tmp_path / (info or _INFO)["name"]
    (root / "config").mkdir(parents=True)
    (root / "images").mkdir()
    (root / "info.json").write_text(json.dumps(info or _INFO))
    (root / "prompt.yaml").write_text(_PROMPT_YAML)
    (root / "config" / "memory.yaml").write_text(_MEMORY_YAML)
    (root / "agent.py").write_text(agent_py)
    (root / "__init__.py").write_text("")
    if with_icons:
        (root / "images" / "small.png").write_bytes(b"\x89PNG")
        (root / "images" / "large.png").write_bytes(b"\x89PNG")
    # cruft that packing must exclude
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "agent.cpython-313.pyc").write_bytes(b"x")
    return root


# ------------------------------------------------------------------ parsing


def test_from_dir_parses_manifest_prompts_and_memory(tmp_path):
    pkg = AgentPackage.from_dir(str(_make_agent_dir(tmp_path)))
    assert pkg.info.name == "widget-counter"
    assert pkg.info.agentclass == "WidgetCounterAgent"
    assert _PROMPT_UUID in pkg.prompts.prompts
    assert pkg.memory.mcp_configuration_uuids() == [
        "2e541107-0b55-4623-b297-dfac495a863e",
        "c8ef6eb0-3d0e-4c5b-b98c-6d03a23148d6",
    ]


def test_missing_info_json_raises(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        AgentPackage.from_dir(str(tmp_path / "empty"))


def test_bad_agentclass_fails_consistency(tmp_path):
    info = {**_INFO, "agentclass": "NotDefinedAgent"}
    with pytest.raises(ValueError, match="agentclass"):
        AgentPackage.from_dir(str(_make_agent_dir(tmp_path, info=info)))


def test_referenced_prompt_uuid_missing_fails(tmp_path):
    bad = _AGENT_PY.replace(_PROMPT_UUID, "00000000-0000-0000-0000-000000000000")
    with pytest.raises(ValueError, match="prompt uuid"):
        AgentPackage.from_dir(str(_make_agent_dir(tmp_path, agent_py=bad)))


def test_manifest_icon_missing_fails(tmp_path):
    with pytest.raises(ValueError, match="icon"):
        AgentPackage.from_dir(str(_make_agent_dir(tmp_path, with_icons=False)))


# ------------------------------------------------------------------ packing


def test_pack_agent_layout_and_excludes_pyc(tmp_path):
    root = _make_agent_dir(tmp_path)
    out = pack_agent(str(root))
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert {n.split("/")[0] for n in names} == {"widget-counter"}
    assert "widget-counter/info.json" in names
    assert not any(n.endswith(".pyc") for n in names)
    assert not any("__pycache__" in n for n in names)


def test_pack_agent_validates_before_packing(tmp_path):
    info = {**_INFO, "agentclass": "Nope"}
    with pytest.raises(ValueError):
        pack_agent(str(_make_agent_dir(tmp_path, info=info)))


# ------------------------------------------------------------------ import/export


class FakeResponse:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class RecordingClient:
    def __init__(self, response=None):
        self.requests = []
        self._response = response or FakeResponse(payload={"uuid": "a-1", "active": False})

    def request(self, method, endpoint, *, files=None, params=None, headers=None, data=None):
        self.requests.append(
            {
                "method": method,
                "endpoint": endpoint,
                "files": files,
                "params": params,
                "headers": headers,
            }
        )
        return self._response


def test_import_agent_from_source_dir_packs_and_posts_multipart(tmp_path):
    root = _make_agent_dir(tmp_path)
    client = RecordingClient()
    api = AIApi(client)

    result = api.import_agent(str(root), replace=True)

    assert result == {"uuid": "a-1", "active": False}
    (call,) = client.requests
    assert call["method"] == "POST"
    assert call["endpoint"] == "/api/ai/agent/import"
    assert call["params"] == {"replace": "true"}
    assert "file" in call["files"]
    # the on-the-fly zip is cleaned up (only a source dir remains)
    assert not (root.with_suffix(".zip")).exists()


def test_import_agent_from_zip_does_not_validate_or_delete(tmp_path):
    root = _make_agent_dir(tmp_path)
    zip_path = tmp_path / "prebuilt.zip"
    pack_agent(str(root), output=str(zip_path))
    client = RecordingClient()
    api = AIApi(client)

    api.import_agent(str(zip_path))

    (call,) = client.requests
    assert call["params"] is None  # replace defaults off
    assert zip_path.exists()  # caller's file is never removed


def test_import_agent_missing_path_raises(tmp_path):
    api = AIApi(RecordingClient())
    with pytest.raises(FileNotFoundError):
        api.import_agent(str(tmp_path / "nope"))


def test_export_agent_writes_bytes_to_dest(tmp_path):
    client = RecordingClient(FakeResponse(content=b"PK\x03\x04zipbytes"))
    api = AIApi(client)
    dest = tmp_path / "exported.zip"

    out = api.export_agent("a-99", str(dest))

    assert out == str(dest)
    assert dest.read_bytes() == b"PK\x03\x04zipbytes"
    (call,) = client.requests
    assert call["method"] == "POST"
    assert call["endpoint"] == "/api/ai/agent/export/a-99"


def test_export_agent_requires_uuid(tmp_path):
    api = AIApi(RecordingClient())
    with pytest.raises(ValueError):
        api.export_agent("  ", str(tmp_path / "x.zip"))
