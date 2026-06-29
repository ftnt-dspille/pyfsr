"""Unit tests for the public content-repository download module (pyfsr.repo).

All HTTP is mocked — these never touch the network. The live URL layouts were
verified separately against repo.fortisoar.fortinet.com (see SCRIPT_ERGONOMICS_PLAN T3.6).
"""

from __future__ import annotations

import os

import pytest
import requests

from pyfsr import repo
from pyfsr.exceptions import RepoArtifactNotFoundError, RepoUnreachableError


class _FakeResponse:
    def __init__(self, *, status_code=200, body=b"ARTIFACT-BYTES"):
        self.status_code = status_code
        self._body = body

    def iter_content(self, chunk_size=1):
        yield self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _patch_get(monkeypatch, *, status_code=200, raises=None):
    calls: list[dict] = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if raises is not None:
            raise raises
        return _FakeResponse(status_code=status_code)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


# ---------------------------------------------------------------------------
# URL layout (the live-verified shapes)
# ---------------------------------------------------------------------------


def test_connector_url_layout():
    assert repo._connector_url("servicenow", "1.0.0") == (
        "https://repo.fortisoar.fortinet.com/xf/solutions/connectors/servicenow-1.0.0/latest/servicenow.tgz"
    )


def test_widget_url_layout():
    assert repo._widget_url("accessControl", "2.1.0") == (
        "https://repo.fortisoar.fortinet.com/fsr-widgets/accessControl-2.1.0/accessControl-2.1.0.tgz"
    )


def test_solution_pack_url_layout():
    assert repo._solution_pack_url("fortindrEssentials", "1.0.4") == (
        "https://repo.fortisoar.fortinet.com/xf/solutions/solutionpacks/fortindrEssentials-1.0.4/latest/fortindrEssentials.zip"
    )


# ---------------------------------------------------------------------------
# reachable()
# ---------------------------------------------------------------------------


def test_reachable_true_on_2xx(monkeypatch):
    _patch_get(monkeypatch, status_code=200)
    assert repo.reachable() is True


def test_reachable_true_even_on_http_error(monkeypatch):
    # A non-2xx HTTP answer still means the host is up.
    _patch_get(monkeypatch, status_code=503)
    assert repo.reachable() is True


def test_reachable_false_on_connection_error(monkeypatch):
    _patch_get(monkeypatch, raises=requests.exceptions.ConnectionError("offline"))
    assert repo.reachable() is False


def test_reachable_false_on_timeout(monkeypatch):
    _patch_get(monkeypatch, raises=requests.exceptions.Timeout("slow"))
    assert repo.reachable() is False


# ---------------------------------------------------------------------------
# download_* happy path + dest handling
# ---------------------------------------------------------------------------


def test_download_connector_to_dir_uses_repo_filename(monkeypatch, tmp_path):
    _patch_get(monkeypatch, status_code=200)
    out = repo.download_connector("servicenow", "1.0.0", str(tmp_path))
    assert out == os.path.join(str(tmp_path), "servicenow.tgz")
    assert os.path.exists(out)
    assert open(out, "rb").read() == b"ARTIFACT-BYTES"


def test_download_connector_to_explicit_file(monkeypatch, tmp_path):
    _patch_get(monkeypatch, status_code=200)
    target = str(tmp_path / "pinned.tgz")
    out = repo.download_connector("servicenow", "1.0.0", target)
    assert out == target
    assert os.path.exists(out)


def test_download_widget_filename(monkeypatch, tmp_path):
    _patch_get(monkeypatch, status_code=200)
    out = repo.download_widget("accessControl", "2.1.0", str(tmp_path))
    assert os.path.basename(out) == "accessControl-2.1.0.tgz"


def test_download_solution_pack_filename(monkeypatch, tmp_path):
    _patch_get(monkeypatch, status_code=200)
    out = repo.download_solution_pack("fortindrEssentials", "1.0.4", str(tmp_path))
    assert os.path.basename(out) == "fortindrEssentials.zip"


# ---------------------------------------------------------------------------
# error branches: unreachable vs not-found
# ---------------------------------------------------------------------------


def test_download_unreachable_preflight(monkeypatch, tmp_path):
    # Reachability preflight fails -> RepoUnreachableError, never attempts download.
    _patch_get(monkeypatch, raises=requests.exceptions.ConnectionError("offline"))
    with pytest.raises(RepoUnreachableError):
        repo.download_connector("servicenow", "1.0.0", str(tmp_path))


def test_download_404_is_artifact_not_found(monkeypatch, tmp_path):
    # Reachable (preflight 200) but the artifact GET returns 404.
    state = {"n": 0}

    def fake_get(url, **kwargs):
        state["n"] += 1
        # first call is the preflight (reachable), second is the artifact GET
        return _FakeResponse(status_code=200 if state["n"] == 1 else 404)

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(RepoArtifactNotFoundError):
        repo.download_connector("servicenow", "9.9.9", str(tmp_path))


def test_download_verifies_tls_by_default(monkeypatch, tmp_path):
    calls = _patch_get(monkeypatch, status_code=200)
    repo.download_connector("servicenow", "1.0.0", str(tmp_path))
    # both the preflight and the artifact GET must verify TLS
    assert all(c["verify"] is True for c in calls)


def test_download_verify_false_opt_out(monkeypatch, tmp_path):
    calls = _patch_get(monkeypatch, status_code=200)
    repo.download_connector("servicenow", "1.0.0", str(tmp_path), verify=False)
    assert all(c["verify"] is False for c in calls)


def test_unreachable_error_carries_url():
    err = RepoUnreachableError(url="https://x/y.tgz")
    assert err.url == "https://x/y.tgz"
    assert "x/y.tgz" in str(err)
