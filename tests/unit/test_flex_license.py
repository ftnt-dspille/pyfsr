"""FortiFlex license deploy via the public (no-auth) client.

Covers the no-auth client construction (usable on a license-locked appliance
where no credential authenticates) and the install → poll → resolve flow of
``system.deploy_flex_license``.
"""

from __future__ import annotations

import time

import pytest

from pyfsr import FortiSOAR
from pyfsr.auth.no_auth import NoAuth


def test_public_client_builds_without_network_or_creds():
    """public=True constructs with no credentials and makes no auth call
    (so it works while the box blocks auth)."""
    c = FortiSOAR("https://soar.example.com", public=True, verify_ssl=False)
    assert isinstance(c.auth, NoAuth)
    assert c.auth.get_auth_headers() == {}
    assert "Authorization" not in c.session.headers


def test_public_rejects_credentials():
    with pytest.raises(ValueError):
        FortiSOAR("https://soar.example.com", public=True, token="k")


def _public_client():
    return FortiSOAR("https://soar.example.com", public=True, verify_ssl=False)


def test_install_and_status_shapes(monkeypatch):
    c = _public_client()
    seen = []
    monkeypatch.setattr(c, "post", lambda ep, data=None: seen.append((ep, data)) or {})
    c.system.install_flex_license("TOKEN123", node_id="n1")
    c.system.flex_license_status(node_id="n1")
    assert seen[0] == (
        "/api/public/license",
        {"action": "install_flex_license", "data": {"license_token": "TOKEN123"}, "nodeId": "n1"},
    )
    assert seen[1] == ("/api/public/license", {"action": "get_status", "nodeId": "n1"})


def test_deploy_flex_license_polls_until_finished(monkeypatch):
    c = _public_client()
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    # install ack, then in_progress, then finished
    replies = [
        {},  # install_flex_license
        {"depl_status": "in_progress"},
        {"depl_status": "finished", "depl_message": None},
    ]
    calls = {"n": 0}

    def fake_post(ep, data=None):
        r = replies[calls["n"]]
        calls["n"] += 1
        return r

    monkeypatch.setattr(c, "post", fake_post)
    res = c.system.deploy_flex_license("TOKEN123", poll_interval=0)
    assert res["ok"] is True
    assert res["depl_status"] == "finished"
    assert res["polls"] == 2


def test_deploy_flex_license_reports_failure_without_raising(monkeypatch):
    c = _public_client()
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    replies = [{}, {"depl_status": "failed", "depl_message": "FSR-Auth-001"}]
    calls = {"n": 0}

    def fake_post(ep, data=None):
        r = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(c, "post", fake_post)
    res = c.system.deploy_flex_license("TOKEN123", poll_interval=0)
    assert res["ok"] is False
    assert res["depl_status"] == "failed"
    assert "FSR-Auth-001" in (res["depl_message"] or "")


def test_deploy_flex_license_times_out(monkeypatch):
    c = _public_client()
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    # never resolves; a monotonic clock that advances past the timeout ends it
    ticks = iter([0, 1, 2, 3, 100, 200])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(c, "post", lambda ep, data=None: {"depl_status": "in_progress"})
    res = c.system.deploy_flex_license("TOKEN123", timeout=10, poll_interval=0)
    assert res["ok"] is False
    assert res["depl_status"] == "in_progress"
