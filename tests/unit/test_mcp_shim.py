"""pyfsr-mcp hands off to fsr-agentic-toolkit, or explains how to install it."""

from __future__ import annotations

import builtins
import sys
import types

from pyfsr.cli import mcp_shim


def _hide_toolkit(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("fsr_agentic_toolkit"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_delegates_to_toolkit_server_when_installed(monkeypatch, capsys):
    calls = []
    cli = types.ModuleType("fsr_agentic_toolkit.cli")
    cli.main = lambda argv: calls.append(argv) or 0
    monkeypatch.setitem(sys.modules, "fsr_agentic_toolkit", types.ModuleType("fsr_agentic_toolkit"))
    monkeypatch.setitem(sys.modules, "fsr_agentic_toolkit.cli", cli)

    assert mcp_shim.main([]) == 0
    assert calls == [["mcp"]]
    err = capsys.readouterr()
    assert "deprecated" in err.err
    assert err.out == ""  # stdout belongs to the MCP protocol


def test_execs_toolkit_on_path_when_not_importable(monkeypatch):
    _hide_toolkit(monkeypatch)
    monkeypatch.setattr(mcp_shim.shutil, "which", lambda name: "/bin/fsr-agentic-toolkit")
    seen = []
    import os

    monkeypatch.setattr(os, "execv", lambda exe, args: seen.append((exe, args)))
    mcp_shim.main([])
    assert seen == [("/bin/fsr-agentic-toolkit", ["/bin/fsr-agentic-toolkit", "mcp"])]


def test_explains_install_when_missing(monkeypatch, capsys):
    _hide_toolkit(monkeypatch)
    monkeypatch.setattr(mcp_shim.shutil, "which", lambda name: None)
    assert mcp_shim.main([]) == 1
    assert "pip install fsr-agentic-toolkit" in capsys.readouterr().err


def test_core_package_has_no_agent_layer():
    import importlib.util

    assert importlib.util.find_spec("pyfsr.agent") is None
