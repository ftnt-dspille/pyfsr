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
    def __init__(self, *, status_code=200, body=b"ARTIFACT-BYTES", json_body=None):
        self.status_code = status_code
        self._body = body
        self._json = json_body

    def iter_content(self, chunk_size=1):
        yield self._body

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body on this fake response")
        return self._json

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


def _patch_get_seq(monkeypatch, responses):
    """Return one :class:`_FakeResponse` per ``requests.get`` call, in order.

    For the discovery helpers, each call wraps a reachability preflight
    (``reachable()``) plus the real fetch, so a single public call typically
    consumes two ``responses`` entries. Asserts if the code makes more calls
    than supplied (catches a missing preflight / extra fetch).
    """
    calls: list[dict] = []
    it = iter(responses)

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        try:
            return next(it)
        except StopIteration as exc:  # pragma: no cover - test guard
            raise AssertionError(f"unexpected extra requests.get call to {url!r}") from exc

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


# A two-entry manifest: one list-category connector, one string-category one —
# exercises the str/list polymorphism the live manifest actually exhibits.
_MANIFEST = {
    "servicenow_3.6.0": {
        "name": "servicenow",
        "label": "ServiceNow",
        "version": "3.6.0",
        "description": "ServiceNow CMDB integration",
        "category": ["CMDB", "Threat Intelligence"],
        "path": "/info/",
        "rpm_name": "cyops-connector-servicenow-3.6.0",
        "rpm_full_name": "cyops-connector-servicenow-3.6.0-1.el9.x86_64.rpm",
        "icon": "large.png",
    },
    "anyrun_1.1.0": {
        "name": "anyrun",
        "label": "ANY.RUN",
        "version": "1.1.0",
        "description": "ANY.RUN malware analysis",
        "category": "Malware Analysis",
        "path": "/info/",
        "rpm_name": "cyops-connector-anyrun-1.1.0",
        "icon": "medium.png",
    },
}

_CONN_INFO = {
    "name": "anyrun",
    "label": "ANY.RUN",
    "version": "1.1.0",
    "type": "connector",
    "availableVersions": ["1.0.0", "1.1.0"],
    "operations": [{"operation": "get_report"}, {"operation": "run_analysis"}],
    "category": "Malware Analysis",
    "publisher": "Community",
    "certified": False,
    "releaseNotes": "initial release",
    "buildNumber": 454,
}

_WIDGET_INFO = {
    "metadata": {
        "description": "Access Control widget",
        "publisher": "Fortinet",
        "certified": "Yes",
        "compatibility": ["7.0.2", "7.2.0"],
    },
    "name": "accessControl",
    "title": "Access Control",
    "subTitle": "change who can access records",
    "version": "2.1.0",
    "published_date": {"date": "2022-03-22"},
}

_SP_INFO = {
    "name": "fortindrEssentials",
    "label": "FortiNDR Essentials Solution Pack",
    "version": "1.0.4",
    "availableVersions": ["1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4"],
    "dependencies": [{"name": "soar-framework"}],
    "fsrMinCompatibility": "7.2.0",
    "category": ["Case Management"],
    "publisher": "Fortinet",
    "certified": False,
    "contents": [{"name": "playbook_x"}],
}


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


def test_manifest_url_layout():
    assert repo._manifest_url() == ("https://repo.fortisoar.fortinet.com/connectors/info/connectors.json")


def test_connector_info_url_layout():
    assert repo._connector_info_url("servicenow", "1.0.0") == (
        "https://repo.fortisoar.fortinet.com/xf/solutions/connectors/servicenow-1.0.0/latest/info.json"
    )


def test_widget_info_url_layout():
    # Flat path — no /latest/ segment, unlike connector/solution-pack info.json.
    assert repo._widget_info_url("accessControl", "2.1.0") == (
        "https://repo.fortisoar.fortinet.com/fsr-widgets/accessControl-2.1.0/info.json"
    )


def test_solution_pack_info_url_layout():
    assert repo._solution_pack_info_url("fortindrEssentials", "1.0.4") == (
        "https://repo.fortisoar.fortinet.com/xf/solutions/solutionpacks/fortindrEssentials-1.0.4/latest/info.json"
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


# ===========================================================================
# Discovery (list / search / info / versions) — all HTTP mocked
# ===========================================================================
# Each public discovery call wraps a reachability preflight (reachable()) plus
# the real fetch, so the sequence mocks below supply a 200 preflight response
# first, then the JSON payload response.


def test_list_connectors_parses_manifest(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),  # preflight
            _FakeResponse(status_code=200, json_body=_MANIFEST),  # manifest
        ],
    )
    entries = repo.list_connectors()
    assert [e.name for e in entries] == ["anyrun", "servicenow"]  # sorted by name
    sn = next(e for e in entries if e.name == "servicenow")
    assert sn.label == "ServiceNow"
    assert sn.version == "3.6.0"
    assert sn.rpm_name == "cyops-connector-servicenow-3.6.0"
    assert sn["icon"] == "large.png"  # dict-compatible access to a manifest-only field
    # category is a list for servicenow in the fixture -> flattened by category_str
    assert sn.category == ["CMDB", "Threat Intelligence"]
    assert sn.category_str == "CMDB, Threat Intelligence"
    # anyrun has a string category -> category_str returns it unchanged
    ar = next(e for e in entries if e.name == "anyrun")
    assert ar.category == "Malware Analysis"
    assert ar.category_str == "Malware Analysis"


def test_list_connectors_404_is_artifact_not_found(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),  # preflight (host is up)
            _FakeResponse(status_code=404),  # manifest path gone
        ],
    )
    with pytest.raises(RepoArtifactNotFoundError):
        repo.list_connectors()


def test_list_connectors_unreachable(monkeypatch):
    # Preflight itself fails -> RepoUnreachableError, no manifest fetch.
    _patch_get(monkeypatch, raises=requests.exceptions.ConnectionError("offline"))
    with pytest.raises(RepoUnreachableError):
        repo.list_connectors()


def test_list_connectors_verifies_tls_by_default(monkeypatch):
    calls = _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    repo.list_connectors()
    assert all(c["verify"] is True for c in calls)


def test_list_connectors_verify_false_opt_out(monkeypatch):
    calls = _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    repo.list_connectors(verify=False)
    assert all(c["verify"] is False for c in calls)


def test_search_connectors_matches_across_fields(monkeypatch):
    # search_connectors delegates to list_connectors, which does a preflight +
    # manifest fetch per call; the test issues three searches, so supply three
    # (preflight, manifest) pairs.
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    # "malware" lives in anyrun's description only
    hits = repo.search_connectors("malware")
    assert [h.name for h in hits] == ["anyrun"]
    # "CMDB" lives in servicenow's category (a list)
    hits = repo.search_connectors("cmdb")
    assert [h.name for h in hits] == ["servicenow"]
    # "servicenow" matches name/label
    hits = repo.search_connectors("servicenow")
    assert [h.name for h in hits] == ["servicenow"]


def test_search_connectors_no_match_returns_empty(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    assert repo.search_connectors("nonexistent-term-xyz") == []


def test_connector_info_parses_info_json(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_CONN_INFO),
        ],
    )
    info = repo.connector_info("anyrun", "1.1.0")
    assert info.name == "anyrun"
    assert info.availableVersions == ["1.0.0", "1.1.0"]
    assert info["operations"] == [{"operation": "get_report"}, {"operation": "run_analysis"}]
    assert info.certified is False


def test_connector_info_404_is_artifact_not_found(monkeypatch):
    # 404 on the primary path triggers the content-hub fallback; 404 on both
    # paths is a genuine "no such name/version" -> RepoArtifactNotFoundError.
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),  # preflight (primary)
            _FakeResponse(status_code=404),  # primary info.json 404 -> fallback
            _FakeResponse(status_code=200),  # preflight (fallback)
            _FakeResponse(status_code=404),  # fallback info.json 404 -> raise
        ],
    )
    with pytest.raises(RepoArtifactNotFoundError):
        repo.connector_info("anyrun", "9.9.9")


def test_connector_info_falls_back_to_content_hub_path(monkeypatch):
    # Some connectors (e.g. code-snippet) 404 on /xf/solutions/connectors/ but
    # resolve under /content-hub/. The fallback must be tried transparently.
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),  # preflight (primary)
            _FakeResponse(status_code=404),  # primary info.json 404 -> fallback
            _FakeResponse(status_code=200),  # preflight (fallback)
            _FakeResponse(status_code=200, json_body=_CONN_INFO),  # fallback OK
        ],
    )
    info = repo.connector_info("code-snippet", "2.2.1")
    assert info.name == "anyrun"  # fixture payload
    assert info.availableVersions == ["1.0.0", "1.1.0"]


def test_connector_versions_chains_manifest_then_info(monkeypatch):
    # list_connectors (preflight + manifest) then connector_info (preflight + info)
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_CONN_INFO),
        ],
    )
    assert repo.connector_versions("anyrun") == ["1.0.0", "1.1.0"]


def test_connector_versions_unknown_name_raises_valueerror(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    with pytest.raises(ValueError):
        repo.connector_versions("no-such-connector")


def test_widget_info_flattens_metadata(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_WIDGET_INFO),
        ],
    )
    w = repo.widget_info("accessControl", "2.1.0")
    assert w.name == "accessControl"
    assert w.title == "Access Control"
    assert w.version == "2.1.0"
    # human fields nested under ``metadata`` are flattened onto the typed view
    assert w.compatibility == ["7.0.2", "7.2.0"]
    assert w.publisher == "Fortinet"
    assert w.certified == "Yes"
    assert w.description == "Access Control widget"
    # the raw metadata wrapper is preserved in extra
    assert w["metadata"]["compatibility"] == ["7.0.2", "7.2.0"]


def test_widget_info_404_is_artifact_not_found(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=404),
        ],
    )
    with pytest.raises(RepoArtifactNotFoundError):
        repo.widget_info("accessControl", "9.9.9")


def test_solution_pack_info_parses_info_json(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_SP_INFO),
        ],
    )
    sp = repo.solution_pack_info("fortindrEssentials", "1.0.4")
    assert sp.name == "fortindrEssentials"
    assert sp.availableVersions == ["1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4"]
    assert sp.fsrMinCompatibility == "7.2.0"
    assert sp.dependencies == [{"name": "soar-framework"}]
    assert sp.category == ["Case Management"]
    assert sp["contents"] == [{"name": "playbook_x"}]  # extra rides through


def test_solution_pack_info_404_is_artifact_not_found(monkeypatch):
    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=404),
        ],
    )
    with pytest.raises(RepoArtifactNotFoundError):
        repo.solution_pack_info("fortindrEssentials", "9.9.9")


# ===========================================================================
# CLI handlers (called directly; no subprocess — exec_cli_examples is
# playbook-only and repo commands hit the network, so they're not run there)
# ===========================================================================


def _run_handler(handler, capsys, **namespace) -> tuple[int, str, str]:
    """Invoke a CLI handler with a synthesized args namespace; capture stdio.

    Uses ``capsys`` (not ``redirect_stdout``) because :mod:`pyfsr.cli._output`
    binds ``file=sys.stdout`` as a default argument at import time, which
    defeats ``redirect_stdout``; capsys shares pytest's capture and sees it.
    """
    import argparse

    args = argparse.Namespace(**namespace)
    rc = handler(args)
    out, err = capsys.readouterr()
    return rc, out, err


def test_cli_reachable_true(monkeypatch, capsys):
    from pyfsr.cli import repo as repo_cli

    _patch_get(monkeypatch, status_code=200)
    rc, out, _ = _run_handler(repo_cli.cmd_reachable, capsys, timeout=5.0)
    assert rc == 0
    assert out.strip() == "reachable"


def test_cli_reachable_false_exit_1(monkeypatch, capsys):
    from pyfsr.cli import repo as repo_cli

    _patch_get(monkeypatch, raises=requests.exceptions.ConnectionError("offline"))
    rc, out, _ = _run_handler(repo_cli.cmd_reachable, capsys, timeout=5.0)
    assert rc == 1
    assert out.strip() == "unreachable"


def test_cli_list_connectors_table(monkeypatch, capsys):
    from pyfsr.cli import repo as repo_cli

    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    rc, out, _ = _run_handler(repo_cli.cmd_list_connectors, capsys, fmt="table", category=None)
    assert rc == 0
    assert "servicenow" in out
    assert "anyrun" in out


def test_cli_list_connectors_json(monkeypatch, capsys):
    import json

    from pyfsr.cli import repo as repo_cli

    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    rc, out, _ = _run_handler(repo_cli.cmd_list_connectors, capsys, fmt="json", category=None)
    assert rc == 0
    rows = json.loads(out)
    assert {r["name"] for r in rows} == {"servicenow", "anyrun"}


def test_cli_list_connectors_category_filter(monkeypatch, capsys):
    from pyfsr.cli import repo as repo_cli

    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    rc, out, _ = _run_handler(repo_cli.cmd_list_connectors, capsys, fmt="table", category="malware")
    assert rc == 0
    assert "anyrun" in out
    assert "servicenow" not in out


def test_cli_versions_json(monkeypatch, capsys):
    import json

    from pyfsr.cli import repo as repo_cli

    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_CONN_INFO),
        ],
    )
    rc, out, _ = _run_handler(repo_cli.cmd_versions, capsys, fmt="json", name="anyrun")
    assert rc == 0
    assert json.loads(out) == ["1.0.0", "1.1.0"]


def test_cli_versions_unknown_name_exit_1(monkeypatch, capsys):
    from pyfsr.cli import repo as repo_cli

    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_MANIFEST),
        ],
    )
    rc, _, err = _run_handler(repo_cli.cmd_versions, capsys, fmt="table", name="no-such-xyz")
    assert rc == 1
    assert "no connector" in err.lower()


def test_cli_info_connector(monkeypatch, capsys):
    from pyfsr.cli import repo as repo_cli

    _patch_get_seq(
        monkeypatch,
        [
            _FakeResponse(status_code=200),
            _FakeResponse(status_code=200, json_body=_CONN_INFO),
        ],
    )
    rc, out, _ = _run_handler(repo_cli.cmd_info, capsys, fmt="table", kind="connector", name="anyrun", version="1.1.0")
    assert rc == 0
    assert "anyrun" in out
    assert "1.0.0" in out  # availableVersions shown


def test_cli_download_connector(monkeypatch, capsys, tmp_path):
    from pyfsr.cli import repo as repo_cli

    _patch_get(monkeypatch, status_code=200)  # preflight + streamed artifact both 200
    rc, out, _ = _run_handler(
        repo_cli.cmd_download,
        capsys,
        kind="connector",
        name="servicenow",
        version="1.0.0",
        dest=str(tmp_path),
    )
    assert rc == 0
    assert out.strip() == os.path.join(str(tmp_path), "servicenow.tgz")
    assert os.path.exists(out.strip())


def test_cli_download_404_exit_1(monkeypatch, capsys, tmp_path):
    from pyfsr.cli import repo as repo_cli

    state = {"n": 0}

    def fake_get(url, **kwargs):
        state["n"] += 1
        return _FakeResponse(status_code=200 if state["n"] == 1 else 404)

    monkeypatch.setattr(requests, "get", fake_get)
    rc, _, err = _run_handler(
        repo_cli.cmd_download,
        capsys,
        kind="connector",
        name="servicenow",
        version="9.9.9",
        dest=str(tmp_path),
    )
    assert rc == 1
    assert "no artifact" in err.lower()
