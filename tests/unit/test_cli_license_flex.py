"""Unit tests for the ``pyfsr appliance license deploy-flex`` CLI verb.

Hermetic: verifies argparse wiring and that the handler dispatches to
``client.system.deploy_flex_license`` — no live network calls.
"""

from __future__ import annotations

import argparse

from pyfsr.cli.__main__ import build_parser, cmd_license_deploy_flex


def test_deploy_flex_parses_and_routes():
    args = build_parser().parse_args(
        [
            "appliance",
            "license",
            "deploy-flex",
            "TOK123",
            "--public",
            "--server",
            "https://box:13000",
            "--no-verify-ssl",
        ]
    )
    assert args.func is cmd_license_deploy_flex
    assert args.license_token == "TOK123"
    assert args.public is True
    assert args.server == "https://box:13000"
    assert args.no_verify_ssl is True


def test_public_without_server_exits_2(capsys):
    args = argparse.Namespace(
        public=True,
        server=None,
        no_verify_ssl=True,
        port=None,
        license_token="TOK",
        node_id=None,
        timeout=600,
        poll_interval=5,
        fmt="table",
    )
    assert cmd_license_deploy_flex(args) == 2
    assert "--public requires --server" in capsys.readouterr().err


def test_handler_calls_deploy_flex_and_maps_exit(monkeypatch):
    """--public builds a no-auth client and returns 0 on ok, 1 otherwise."""
    import pyfsr.cli.__main__ as m

    captured = {}

    class _Sys:
        def deploy_flex_license(self, token, *, node_id=None, timeout=None, poll_interval=None):
            captured["token"] = token
            return {"ok": True, "depl_status": "finished", "depl_message": None, "polls": 2}

    class _Client:
        system = _Sys()

    monkeypatch.setattr(m, "FortiSOAR", lambda *a, **k: _Client(), raising=False)
    # FortiSOAR is imported lazily inside the handler; patch at its source too.
    import pyfsr.client as _clientmod

    monkeypatch.setattr(_clientmod, "FortiSOAR", lambda *a, **k: _Client())

    args = argparse.Namespace(
        public=True,
        server="https://box:13000",
        no_verify_ssl=True,
        port=None,
        license_token="TOKENZ",
        node_id=None,
        timeout=600,
        poll_interval=5,
        fmt="json",
    )
    assert cmd_license_deploy_flex(args) == 0
    assert captured["token"] == "TOKENZ"
