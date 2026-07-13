"""Unit tests for the self-hosted Content Hub catalog builder (pyfsr.content_catalog).

Pure in-memory / filesystem — no network. Entry shapes and path conventions are
pinned against the live 8.0.0 catalog captured in
``docs/plans/CONTENT_HUB_sample_catalog.json`` (see
``docs/plans/CONTENT_HUB_SELF_HOSTED_REPO_PLAN.md``).
"""

from __future__ import annotations

import json
import tarfile
import zipfile

import pytest
import requests

from pyfsr.content_catalog import (
    CATALOG_TYPES,
    ContentCatalog,
    _catalog_url,
    artifact_path,
    build_entry,
    entry_from_artifact,
    fetch_catalog,
    icon_path,
    info_path,
    read_artifact_info,
    validate_entry,
)
from pyfsr.exceptions import RepoArtifactNotFoundError, RepoUnreachableError


class _FakeResponse:
    def __init__(self, *, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


# -- path conventions --------------------------------------------------------


def test_path_conventions_match_live_layout():
    assert info_path("abuseipdb", "2.0.0", 5886) == "/content-hub/abuseipdb-2.0.0/5886"
    assert icon_path("abuseipdb", "2.0.0", 5886) == "/content-hub/abuseipdb-2.0.0/5886/images/fsr-icon-large.png"
    assert artifact_path("abuseipdb", "2.0.0", 5886) == "/content-hub/abuseipdb-2.0.0/5886/abuseipdb-2.0.0.zip"


# -- build_entry -------------------------------------------------------------


def test_build_entry_fills_paths_and_defaults():
    e = build_entry(name="myConn", type="connector", version="1.0.0", buildNumber=7, label="My Conn")
    assert e["infoPath"] == "/content-hub/myConn-1.0.0/7"
    assert e["iconLarge"] == "/content-hub/myConn-1.0.0/7/images/fsr-icon-large.png"
    assert e["availableVersions"] == ["1.0.0"]  # defaults to [version]
    assert e["category"] == []
    assert validate_entry(e) == []


def test_build_entry_preserves_string_category():
    # a string category (connector/widget/ai_agent on the wire) must not be
    # shredded into a list of characters
    e = build_entry(
        name="c", type="connector", version="1.0.0", buildNumber=1, label="C", category="Threat Intelligence"
    )
    assert e["category"] == "Threat Intelligence"
    # a list category (solutionpack) is preserved as a list
    e2 = build_entry(
        name="s",
        type="solutionpack",
        version="1.0.0",
        buildNumber=1,
        label="S",
        category=["Identity and Access Management"],
    )
    assert e2["category"] == ["Identity and Access Management"]


def test_build_entry_merges_type_specific_extras():
    e = build_entry(
        name="sp1",
        type="solutionpack",
        version="1.0.0",
        buildNumber=3,
        label="SP",
        contents={"playbooks": [{"name": "pb"}]},
        dependencies=[{"name": "sOARFramework", "type": "solutionpack", "version": "1.1.0"}],
    )
    assert e["contents"]["playbooks"][0]["name"] == "pb"
    assert e["dependencies"][0]["name"] == "sOARFramework"


# -- validate_entry ----------------------------------------------------------


def test_validate_entry_accepts_all_live_types():
    for t in CATALOG_TYPES:
        e = build_entry(name="x", type=t, version="1.0.0", buildNumber=1, label="X")
        assert validate_entry(e) == [], t


def test_validate_entry_flags_missing_required():
    problems = validate_entry({"type": "connector", "version": "1.0.0"})
    assert any("name" in p for p in problems)
    assert any("buildNumber" in p for p in problems)
    assert any("label" in p for p in problems)


def test_validate_entry_flags_unknown_type():
    e = build_entry(name="x", type="connector", version="1.0.0", buildNumber=1, label="X")
    e["type"] = "bogus"
    assert any("unknown type" in p for p in validate_entry(e))


def test_validate_entry_flags_non_int_build_and_bad_lists():
    e = build_entry(name="x", type="widget", version="1.0.0", buildNumber=1, label="X")
    e["buildNumber"] = "1"
    e["availableVersions"] = "not-a-list"
    problems = validate_entry(e)
    assert any("buildNumber must be an int" in p for p in problems)
    assert any("availableVersions" in p for p in problems)


def test_validate_entry_accepts_string_or_list_category():
    e = build_entry(name="x", type="connector", version="1.0.0", buildNumber=1, label="X")
    e["category"] = "Threat Intelligence"  # live connectors carry a string here
    assert validate_entry(e) == []
    e["category"] = ["Identity and Access Management"]  # solutionpacks carry a list
    assert validate_entry(e) == []
    e["category"] = 42
    assert any("category" in p for p in validate_entry(e))


def test_validate_entry_flags_version_not_in_available():
    e = build_entry(name="x", type="connector", version="2.0.0", buildNumber=1, label="X", availableVersions=["1.0.0"])
    assert any("not in availableVersions" in p for p in validate_entry(e))


def test_validate_entry_non_dict():
    assert validate_entry("nope") == ["entry is not a JSON object (got str)"]


@pytest.mark.parametrize("bad", ["../evil", "a/b", "..", "x\x00y", "a b", "foo/../bar"])
def test_validate_entry_rejects_path_traversal_names(bad):
    e = build_entry(name="ok", type="connector", version="1.0.0", buildNumber=1, label="X")
    e["name"] = bad
    assert any("illegal characters" in p for p in validate_entry(e)), bad
    e2 = build_entry(name="ok", type="connector", version="1.0.0", buildNumber=1, label="X")
    e2["version"] = bad
    e2["availableVersions"] = [bad]
    assert any("illegal characters" in p for p in validate_entry(e2)), bad


def test_validate_entry_allows_normal_slugs():
    e = build_entry(
        name="abuse_ipdb-2",
        type="connector",
        version="2.0.0-rc.1",
        buildNumber=1,
        label="X",
        availableVersions=["2.0.0-rc.1"],
    )
    assert validate_entry(e) == []


def test_write_tree_refuses_traversal_even_without_validate(tmp_path):
    # validate=False bypasses validate_entry, so write_tree must still self-guard
    cat = ContentCatalog()
    evil = build_entry(name="ok", type="connector", version="1.0.0", buildNumber=1, label="X")
    evil["name"] = "../../etc/pwn"
    cat.add(evil)
    with pytest.raises(ValueError, match="outside the served tree"):
        cat.write_tree(str(tmp_path), validate=False)


# -- ContentCatalog: add / merge / dedup -------------------------------------


def _entry(name, type="connector", version="1.0.0", build=1):
    return build_entry(name=name, type=type, version=version, buildNumber=build, label=name)


def test_add_dedups_by_type_and_name_last_wins():
    cat = ContentCatalog()
    cat.add(_entry("a", version="1.0.0"))
    cat.add(_entry("a", version="2.0.0"))
    assert len(cat) == 1
    assert cat.to_list()[0]["version"] == "2.0.0"


def test_same_name_different_type_coexist():
    cat = ContentCatalog([_entry("dup", type="connector"), _entry("dup", type="widget")])
    assert len(cat) == 2


def test_merge_argument_wins():
    local = ContentCatalog([_entry("shared", version="9.9.9"), _entry("localonly")])
    upstream = ContentCatalog([_entry("shared", version="1.0.0"), _entry("uponly")])
    # local.merge(upstream) -> upstream wins on 'shared'
    local.merge(upstream)
    by_name = {e["name"]: e for e in local}
    assert by_name["shared"]["version"] == "1.0.0"
    assert {"shared", "localonly", "uponly"} == set(by_name)


def test_remove_and_counts():
    cat = ContentCatalog([_entry("a", type="connector"), _entry("b", type="widget")])
    assert cat.counts() == {"connector": 1, "widget": 1}
    assert cat.remove(type="connector", name="a") is True
    assert cat.remove(type="connector", name="a") is False
    assert cat.counts() == {"widget": 1}


def test_insertion_order_preserved():
    cat = ContentCatalog([_entry("z"), _entry("a"), _entry("m")])
    assert [e["name"] for e in cat] == ["z", "a", "m"]


# -- from_file / from_list ---------------------------------------------------


def test_from_file_round_trips(tmp_path):
    src = tmp_path / "content-hub.json"
    src.write_text(json.dumps([_entry("a"), _entry("b")]))
    cat = ContentCatalog.from_file(str(src))
    assert len(cat) == 2


def test_from_file_rejects_non_array(tmp_path):
    src = tmp_path / "bad.json"
    src.write_text(json.dumps({"not": "an array"}))
    with pytest.raises(ValueError, match="must be a JSON array"):
        ContentCatalog.from_file(str(src))


def test_from_sample_catalog_loads():
    """The captured live sample (one entry per type) validates clean."""
    import os

    root = os.path.join(os.path.dirname(__file__), "..", "..")
    sample = os.path.join(root, "docs", "plans", "CONTENT_HUB_sample_catalog.json")
    with open(sample, encoding="utf-8") as fh:
        by_type = json.load(fh)
    cat = ContentCatalog(by_type.values())
    assert cat.validate() == {}
    assert set(cat.counts()) <= CATALOG_TYPES


# -- read_artifact_info / entry_from_artifact --------------------------------


def _make_tgz(path, info, *, member="myconn/info.json"):
    import io

    payload = json.dumps(info).encode()
    with tarfile.open(path, "w:gz") as tf:
        ti = tarfile.TarInfo(member)
        ti.size = len(payload)
        tf.addfile(ti, io.BytesIO(payload))


def _make_zip(path, info, *, member="info.json"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, json.dumps(info))


def test_read_artifact_info_from_tgz(tmp_path):
    p = tmp_path / "myconn-1.0.0.tgz"
    _make_tgz(str(p), {"name": "myconn", "version": "1.0.0", "operations": []})
    info = read_artifact_info(str(p))
    assert info["name"] == "myconn"


def test_read_artifact_info_from_zip(tmp_path):
    p = tmp_path / "mysp-1.0.0.zip"
    _make_zip(str(p), {"name": "mysp", "version": "1.0.0", "contents": {}})
    assert read_artifact_info(str(p))["name"] == "mysp"


def test_read_artifact_info_picks_shallowest(tmp_path):
    import io

    p = tmp_path / "a.tgz"
    with tarfile.open(str(p), "w:gz") as tf:
        for member, body in [
            ("pkg/sub/info.json", {"name": "deep"}),
            ("pkg/info.json", {"name": "shallow"}),
        ]:
            payload = json.dumps(body).encode()
            ti = tarfile.TarInfo(member)
            ti.size = len(payload)
            tf.addfile(ti, io.BytesIO(payload))
    assert read_artifact_info(str(p))["name"] == "shallow"


def test_read_artifact_info_no_info_json(tmp_path):
    p = tmp_path / "empty.zip"
    with zipfile.ZipFile(str(p), "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="no info.json"):
        read_artifact_info(str(p))


def test_read_artifact_info_not_an_archive(tmp_path):
    p = tmp_path / "nope.txt"
    p.write_text("plain")
    with pytest.raises(ValueError, match="not a .tgz or .zip"):
        read_artifact_info(str(p))


def test_entry_from_artifact_infers_connector(tmp_path):
    p = tmp_path / "myconn-2.0.0.tgz"
    _make_tgz(
        str(p),
        {
            "name": "myconn",
            "version": "2.0.0",
            "label": "My Conn",
            "description": "d",
            "publisher": "Acme",
            "category": "Threat Intelligence",
            "operations": [{"operation": "lookup"}],
        },
    )
    e = entry_from_artifact(str(p))
    assert e["type"] == "connector"  # inferred from operations
    assert e["name"] == "myconn" and e["version"] == "2.0.0"
    assert e["category"] == "Threat Intelligence"
    assert e["operations"][0]["operation"] == "lookup"  # passthrough
    assert e["infoPath"] == "/content-hub/myconn-2.0.0/1"
    assert validate_entry(e) == []


def test_entry_from_artifact_infers_solutionpack(tmp_path):
    p = tmp_path / "mysp-1.0.0.zip"
    _make_zip(str(p), {"name": "mysp", "version": "1.0.0", "label": "SP", "contents": {"playbooks": []}})
    e = entry_from_artifact(str(p))
    assert e["type"] == "solutionpack"


def test_entry_from_artifact_type_and_build_override(tmp_path):
    p = tmp_path / "w-1.0.0.tgz"
    _make_tgz(str(p), {"name": "w", "version": "1.0.0"})
    e = entry_from_artifact(str(p), type="widget", buildNumber=77, publisher="Acme")
    assert e["type"] == "widget"
    assert e["buildNumber"] == 77
    assert e["publisher"] == "Acme"
    assert validate_entry(e) == []


def test_entry_from_artifact_missing_name_version(tmp_path):
    p = tmp_path / "bad.tgz"
    _make_tgz(str(p), {"label": "no name or version"})
    with pytest.raises(ValueError, match="missing name/version"):
        entry_from_artifact(str(p))


# -- fetch_catalog / from_url (crawler) --------------------------------------


def test_catalog_url_normalization():
    assert _catalog_url("mirror.example.com") == "https://mirror.example.com/content-hub/content-hub.json"
    assert _catalog_url("https://mirror.example.com/") == "https://mirror.example.com/content-hub/content-hub.json"
    assert _catalog_url("http://box:8443") == "http://box:8443/content-hub/content-hub.json"
    # already a full manifest URL -> left as-is (no double-append)
    full = "https://mirror.example.com/content-hub/content-hub.json"
    assert _catalog_url(full) == full


def _patch_get(monkeypatch, resp=None, *, raises=None):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if raises is not None:
            raise raises
        return resp

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def test_fetch_catalog_returns_entries(monkeypatch):
    body = [_entry("a"), _entry("b")]
    calls = _patch_get(monkeypatch, _FakeResponse(json_body=body))
    out = fetch_catalog("mirror.example.com")
    assert [e["name"] for e in out] == ["a", "b"]
    assert calls[0]["url"] == "https://mirror.example.com/content-hub/content-hub.json"


def test_from_url_builds_catalog(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(json_body=[_entry("a"), _entry("b")]))
    cat = ContentCatalog.from_url("mirror.example.com")
    assert len(cat) == 2


def test_fetch_catalog_passes_client_cert(monkeypatch):
    calls = _patch_get(monkeypatch, _FakeResponse(json_body=[]))
    fetch_catalog("secops-content.forticloud.com", cert=("/certs/fdn.pem", "/certs/fdn.key"))
    assert calls[0]["cert"] == ("/certs/fdn.pem", "/certs/fdn.key")


def test_fetch_catalog_transport_error_is_unreachable(monkeypatch):
    _patch_get(monkeypatch, raises=requests.exceptions.ConnectionError("boom"))
    with pytest.raises(RepoUnreachableError):
        fetch_catalog("mirror.example.com")


def test_fetch_catalog_404_is_not_found(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(status_code=404))
    with pytest.raises(RepoArtifactNotFoundError):
        fetch_catalog("mirror.example.com")


def test_fetch_catalog_non_array_raises(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(json_body={"not": "an array"}))
    with pytest.raises(ValueError, match="must be a JSON array"):
        fetch_catalog("mirror.example.com")


def test_crawl_then_merge_local(monkeypatch):
    """The end-to-end merge shape: crawl upstream, splice a local override in."""
    _patch_get(monkeypatch, _FakeResponse(json_body=[_entry("shared", version="1.0.0"), _entry("uponly")]))
    upstream = ContentCatalog.from_url("mirror.example.com")
    local = ContentCatalog([_entry("shared", version="9.9.9"), _entry("localonly")])
    upstream.merge(local)  # local wins on 'shared'
    by_name = {e["name"]: e for e in upstream}
    assert by_name["shared"]["version"] == "9.9.9"
    assert {"shared", "uponly", "localonly"} == set(by_name)


# -- validate over a catalog -------------------------------------------------


def test_catalog_validate_reports_bad_entry():
    cat = ContentCatalog([_entry("good")])
    cat.add({"type": "connector", "name": "bad"})  # missing version/build/label
    problems = cat.validate()
    assert "connector/bad" in problems
    assert "connector/good" not in problems


# -- write_tree --------------------------------------------------------------


def test_write_tree_lays_out_fetch_contract(tmp_path):
    cat = ContentCatalog([build_entry(name="conn", type="connector", version="1.0.0", buildNumber=42, label="Conn")])
    manifest = cat.write_tree(str(tmp_path))
    base = tmp_path / "content-hub"
    assert manifest == str(base / "content-hub.json")

    # manifest is the flat array
    data = json.loads((base / "content-hub.json").read_text())
    assert data[0]["name"] == "conn"

    # info.json at both the numbered build and latest/
    build_info = base / "conn-1.0.0" / "42" / "info.json"
    latest_info = base / "conn-1.0.0" / "latest" / "info.json"
    assert json.loads(build_info.read_text())["name"] == "conn"
    assert json.loads(latest_info.read_text())["name"] == "conn"


def test_write_tree_copies_artifact_and_icon(tmp_path):
    art = tmp_path / "conn.zip"
    art.write_bytes(b"ZIPBYTES")
    ico = tmp_path / "icon.png"
    ico.write_bytes(b"PNGBYTES")
    cat = ContentCatalog([build_entry(name="conn", type="connector", version="1.0.0", buildNumber=42, label="Conn")])
    out = tmp_path / "out"
    cat.write_tree(str(out), artifacts={("connector", "conn"): str(art)}, icons={("connector", "conn"): str(ico)})
    zip_dst = out / "content-hub" / "conn-1.0.0" / "42" / "conn-1.0.0.zip"
    icon_dst = out / "content-hub" / "conn-1.0.0" / "42" / "images" / "fsr-icon-large.png"
    assert zip_dst.read_bytes() == b"ZIPBYTES"
    assert icon_dst.read_bytes() == b"PNGBYTES"


def test_write_tree_preserves_tgz_extension(tmp_path):
    # a .tgz source (connectors/widgets) must be served as .tgz, not renamed .zip
    art = tmp_path / "conn-1.0.0.tgz"
    art.write_bytes(b"TGZBYTES")
    cat = ContentCatalog([build_entry(name="conn", type="connector", version="1.0.0", buildNumber=1, label="Conn")])
    out = tmp_path / "out"
    cat.write_tree(str(out), artifacts={("connector", "conn"): str(art)})
    tgz_dst = out / "content-hub" / "conn-1.0.0" / "1" / "conn-1.0.0.tgz"
    assert tgz_dst.read_bytes() == b"TGZBYTES"


def test_write_tree_refuses_invalid_catalog(tmp_path):
    cat = ContentCatalog()
    cat.add({"type": "connector", "name": "bad"})  # invalid
    with pytest.raises(ValueError, match="invalid entr"):
        cat.write_tree(str(tmp_path))
    # nothing written
    assert not (tmp_path / "content-hub").exists()
