"""``pyfsr playbook ... --refresh-catalog`` wiring.

The CLI compile is offline by default (packaged slim catalog, no connector
rows), which makes connector steps compile without a ``name``/``version`` and
renders as "undefined" in the playbook editor. ``--refresh-catalog`` warms the
reference catalog from the live instance first so connector tokens resolve.
These tests assert the flag threads a live client into ``compile_playbook_yaml``
(the warm trigger) and that the default stays offline (client=None).
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pyfsr.cli import playbook as pb


def _args(**kw) -> argparse.Namespace:
    base = dict(
        file="x.yaml",
        refresh_catalog=False,
        server=None,
        token=None,
        username=None,
        password=None,
        port=None,
        no_verify_ssl=False,
        out=None,
        replace=False,
        dry_run=False,
        check_connectors=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _fake_result(ok=True):
    return SimpleNamespace(
        ok=ok,
        errors=[],
        fsr_json={"type": "workflow_collections", "data": []},
        collection_names=[],
        playbook_names=[],
        blocking=[],
        warnings=[],
    )


def test_compile_offline_by_default_passes_no_client():
    with (
        patch("pyfsr.cli.playbook._read", return_value="yaml: text"),
        patch("pyfsr.authoring.compile_playbook_yaml") as mock_compile,
        patch("pyfsr.cli.playbook._make_client") as mock_client,
    ):
        mock_compile.return_value = _fake_result()
        pb._compile(_args())
    assert mock_client.call_count == 0, "offline default must not build a client"
    assert mock_compile.call_args.kwargs.get("client") is None


def test_refresh_catalog_warms_via_live_client():
    with (
        patch("pyfsr.cli.playbook._read", return_value="yaml: text"),
        patch("pyfsr.authoring.compile_playbook_yaml") as mock_compile,
        patch("pyfsr.cli.playbook._make_client") as mock_client,
    ):
        mock_compile.return_value = _fake_result()
        sentinel = object()
        mock_client.return_value = sentinel
        pb._compile(_args(refresh_catalog=True))
    mock_client.assert_called_once()
    assert mock_compile.call_args.kwargs.get("client") is sentinel


def test_supplied_client_takes_precedence_over_flag():
    """When the caller hands in a client, ``_compile`` reuses it instead of
    building another (deploy/lint build one up front for posting/preflight)."""
    supplied = object()
    with (
        patch("pyfsr.cli.playbook._read", return_value="yaml: text"),
        patch("pyfsr.authoring.compile_playbook_yaml") as mock_compile,
        patch("pyfsr.cli.playbook._make_client") as mock_client,
    ):
        mock_compile.return_value = _fake_result()
        pb._compile(_args(refresh_catalog=True), client=supplied)
    assert mock_client.call_count == 0
    assert mock_compile.call_args.kwargs.get("client") is supplied


def test_deploy_dry_run_without_refresh_builds_no_client():
    with (
        patch("pyfsr.cli.playbook._read", return_value="yaml: text"),
        patch("pyfsr.authoring.compile_playbook_yaml") as mock_compile,
        patch("pyfsr.cli.playbook._make_client") as mock_client,
    ):
        mock_compile.return_value = _fake_result()
        rc = pb.cmd_deploy(_args(dry_run=True))
    assert rc == 0
    assert mock_client.call_count == 0


def test_deploy_dry_run_with_refresh_warms_through_one_client():
    sentinel = object()
    with (
        patch("pyfsr.cli.playbook._read", return_value="yaml: text"),
        patch("pyfsr.authoring.compile_playbook_yaml") as mock_compile,
        patch("pyfsr.cli.playbook._make_client", return_value=sentinel) as mock_client,
    ):
        mock_compile.return_value = _fake_result()
        rc = pb.cmd_deploy(_args(dry_run=True, refresh_catalog=True))
    assert rc == 0
    mock_client.assert_called_once()
    assert mock_compile.call_args.kwargs.get("client") is sentinel


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
