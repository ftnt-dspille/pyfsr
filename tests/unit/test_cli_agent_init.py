"""pyfsr agent init: verification, env-file save, per-product MCP snippets."""

from __future__ import annotations

import json

import pytest

from pyfsr.cli import agent_setup
from pyfsr.cli.__main__ import main


@pytest.fixture
def fake_verify(monkeypatch):
    calls = {}

    def _verify(args):
        calls["url"] = args.url
        return {"loginid": "csadmin", "email": "csadmin@example.com"}

    monkeypatch.setattr(agent_setup, "_verify", _verify)
    return calls


def _run(argv):
    return main(argv)


def test_init_prints_claude_code_snippet_by_default(fake_verify, capsys):
    rc = _run(["agent", "init", "--url", "fortisoar.example.com", "--api-key", "k"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "connected: csadmin" in out
    assert "--- claude-code ---" in out
    snippet = out.split("--- claude-code ---", 1)[1]
    cfg = json.loads(snippet.split("\n", 2)[2].split("\nsafety:")[0])
    server = cfg["mcpServers"]["fortisoar"]
    assert server["env"]["FSR_BASE_URL"] == "fortisoar.example.com"
    assert server["env"]["FSR_API_KEY"] == "k"
    assert "pyfsr-mcp" in server["command"] or "uvx" == server["command"]


def test_init_all_products_and_readonly(fake_verify, capsys):
    rc = _run(
        [
            "agent",
            "init",
            "--url",
            "fortisoar.example.com",
            "--api-key",
            "k",
            "--claude-code",
            "--opencode",
            "--copilot",
            "--readonly",
            "--no-verify-ssl",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    for marker in ("--- claude-code ---", "--- opencode ---", "--- copilot ---"):
        assert marker in out
    assert out.count('"FSR_MCP_READONLY": "1"') == 3
    assert out.count('"FSR_VERIFY_SSL": "false"') == 3


def test_init_saves_env_file_with_tight_mode(fake_verify, tmp_path, capsys):
    env_path = tmp_path / "agent.env"
    rc = _run(["agent", "init", "--url", "fortisoar.example.com", "--api-key", "secret", "--save-env", str(env_path)])
    assert rc == 0
    text = env_path.read_text()
    assert "FSR_BASE_URL=fortisoar.example.com" in text
    assert "FSR_API_KEY=secret" in text
    assert (env_path.stat().st_mode & 0o777) == 0o600


def test_init_fails_closed_when_whoami_fails(monkeypatch, capsys):
    monkeypatch.setattr(agent_setup, "_verify", lambda args: None)
    rc = _run(["agent", "init", "--url", "fortisoar.example.com", "--api-key", "bad"])
    assert rc == 1
    assert "--skip-verify" in capsys.readouterr().err


def test_init_skip_verify_still_prints(monkeypatch, capsys):
    monkeypatch.setattr(agent_setup, "_verify", lambda args: None)
    rc = _run(["agent", "init", "--url", "fortisoar.example.com", "--api-key", "k", "--skip-verify"])
    assert rc == 0
    assert "--- claude-code ---" in capsys.readouterr().out
