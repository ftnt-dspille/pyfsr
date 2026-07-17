# tests/test_solution_packs.py
import pytest

from pyfsr.models import SolutionPackInstallResponse


def test_find_installed_pack(mock_client, mock_response, monkeypatch):
    """Test finding an installed solution pack"""
    expected_response = {
        "@context": "/api/3/contexts/SolutionPack",
        "hydra:member": [
            {
                "uuid": "abc-123",
                "name": "SOAR Framework",
                "label": "SOAR Framework",
                "version": "1.0.0",
                "installed": True,
            }
        ],
    }

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=expected_response),
    )

    result = mock_client.content_hub.find_installed_pack("SOAR Framework")
    assert result["name"] == "SOAR Framework"
    assert result["installed"] is True


def test_install_no_wait_returns_typed_response(mock_client, mock_response, monkeypatch):
    """install() with wait=False returns a SolutionPackInstallResponse with job_id."""
    install_resp = {
        "@id": "/api/3/solutionpacks/pack-uuid",
        "uuid": "pack-uuid",
        "name": "SOAR Framework",
        "version": "2.2.1",
        "importJob": {
            "@id": "/api/3/import_jobs/job-uuid",
            "uuid": "job-uuid",
        },
        "status": "Pending",
    }

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=install_resp),
    )

    result = mock_client.solution_packs.install("SOAR Framework", "2.2.1", wait=False)
    assert isinstance(result, SolutionPackInstallResponse)
    assert result.name == "SOAR Framework"
    assert result.uuid == "pack-uuid"
    assert result.job_id == "job-uuid"


def test_install_response_job_id_from_iri(mock_client, mock_response, monkeypatch):
    """job_id falls back to parsing the @id IRI when uuid is absent."""
    install_resp = {
        "uuid": "pack-uuid",
        "name": "SOAR Framework",
        "version": "2.2.1",
        "importJob": {"@id": "/api/3/import_jobs/iri-job-uuid"},
    }

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=install_resp),
    )

    result = mock_client.solution_packs.install("SOAR Framework", "2.2.1")
    assert result.job_id == "iri-job-uuid"


def test_install_status(mock_client, mock_response, monkeypatch):
    """install_status() returns a typed InstallJobStatus."""
    job_resp = {
        "status": "Import Complete",
        "progressPercent": 100,
        "errorMessage": None,
        "currentlyImporting": None,
    }

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=job_resp),
    )

    result = mock_client.solution_packs.install_status("job-uuid")
    assert result.status == "Import Complete"
    assert result.progressPercent == 100


def test_install_wait(mock_client, mock_response, monkeypatch):
    """install() with wait=True polls until terminal and returns final InstallJobStatus."""
    responses = iter(
        [
            # POST /api/3/solutionpacks/install
            {
                "uuid": "pack-uuid",
                "importJob": {"uuid": "job-uuid"},
            },
            # GET /api/3/import_jobs/job-uuid  (first poll — not done yet)
            {"status": "Importing", "progressPercent": 50},
            # GET /api/3/import_jobs/job-uuid  (second poll — done)
            {"status": "Import Complete", "progressPercent": 100},
        ]
    )

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=next(responses)),
    )
    monkeypatch.setattr("time.sleep", lambda _: None)

    result = mock_client.solution_packs.install("SOAR Framework", "2.2.1", wait=True, interval=0)
    assert result.status == "Import Complete"
    assert result.progressPercent == 100


def test_uninstall_success(mock_client, mock_response, monkeypatch):
    """uninstall() looks up the pack UUID and sends DELETE."""
    calls = []

    def fake_request(self, method, url, **kwargs):
        calls.append((method, url))
        if method == "POST":
            # content_hub search response
            return mock_response(
                json_data={"hydra:member": [{"uuid": "pack-uuid", "name": "SOAR Framework", "installed": True}]}
            )
        # DELETE — return 204-style empty
        return mock_response(json_data={}, status_code=204)

    monkeypatch.setattr("requests.Session.request", fake_request)
    mock_client.solution_packs.uninstall("SOAR Framework")

    delete_calls = [c for c in calls if c[0] == "DELETE"]
    assert len(delete_calls) == 1
    assert "pack-uuid" in delete_calls[0][1]


def test_uninstall_not_found(mock_client, mock_response, monkeypatch):
    """uninstall() raises ValueError when the pack isn't installed."""
    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data={"hydra:member": []}),
    )
    with pytest.raises(ValueError, match="No installed solution pack"):
        mock_client.solution_packs.uninstall("Nonexistent Pack")


def _capture_body(monkeypatch, mock_response, payload):
    """Record the POST body install() sends."""
    seen = {}

    def _req(*args, **kwargs):
        seen.update(kwargs)
        return mock_response(json_data=payload)

    monkeypatch.setattr("requests.Session.request", _req)
    return seen


_INSTALL_OK = {"uuid": "pack-uuid", "name": "vulnerabilityManagement", "version": "2.3.0"}


def test_install_omits_build_number_by_default(mock_client, mock_response, monkeypatch):
    # Back-compat: the body stays {name, version} unless a build is asked for.
    seen = _capture_body(monkeypatch, mock_response, _INSTALL_OK)
    mock_client.solution_packs.install("vulnerabilityManagement", "2.3.0")
    assert seen["json"] == {"name": "vulnerabilityManagement", "version": "2.3.0"}


def test_install_sends_build_number_when_given(mock_client, mock_response, monkeypatch):
    # Without buildNumber the appliance falls back to the repo's "latest" build path,
    # which 404s on a repo that publishes numbered builds with no "latest" alias --
    # surfaced as a misleading "check the network connection" error (live-verified 8.0.0).
    seen = _capture_body(monkeypatch, mock_response, _INSTALL_OK)
    mock_client.solution_packs.install("vulnerabilityManagement", "2.3.0", build_number=1102)
    assert seen["json"] == {
        "name": "vulnerabilityManagement",
        "version": "2.3.0",
        "buildNumber": 1102,
    }


def test_install_build_number_accepts_str(mock_client, mock_response, monkeypatch):
    seen = _capture_body(monkeypatch, mock_response, _INSTALL_OK)
    mock_client.solution_packs.install("vulnerabilityManagement", "2.3.0", build_number="latest")
    assert seen["json"]["buildNumber"] == "latest"


# --------------------------------------------------------------------------- #
# SolutionPackBuilder + create() / install_from_file()  (new authoring surface)
# --------------------------------------------------------------------------- #
from pyfsr.api.export_config import SolutionPackBuilder  # noqa: E402
from pyfsr.models import PostInstallConfig, PostInstallWidget  # noqa: E402


def test_builder_slugifies_name_and_builds_metadata():
    b = (
        SolutionPackBuilder("My SOC Pack", version="1.0.0", description="demo")
        .add_module("alerts")
        .post_install_widget("AI Assistant", "5.0.0", auto_launch=True)
        .tags("Agentic AI", "SOC")
        .category("Utilities")
    )
    assert b.name == "my-soc-pack"  # slug derived from label
    assert b.label == "My SOC Pack"
    assert b.build() == {"modules": [{"value": "alerts", "includedAttributes": []}]}
    ic = b.info_content()
    assert ic["label"] == "My SOC Pack"
    assert ic["version"] == "1.0.0"
    assert ic["postInstallConfig"]["enabled"] is True
    w = ic["postInstallConfig"]["widgets"][0]
    assert (w["name"], w["version"], w["autoLaunch"], w["buttonLabel"]) == ("AI Assistant", "5.0.0", True, "Configure")
    assert b._tags == ["Agentic AI", "SOC"]
    assert b._categories == ["Utilities"]


def test_builder_tags_and_category_dedupe():
    b = SolutionPackBuilder("P", version="1.0.0").tags("a", "a", "b").category("X", "X")
    assert b._tags == ["a", "b"]
    assert b._categories == ["X"]


def test_create_posts_solutionpack_export_body(mock_client, mock_response, monkeypatch):
    """create() POSTs /api/3/solutionpacks with a nested SolutionPack Export template."""
    created = {"@id": "/api/3/solutionpacks/new-uuid", "uuid": "new-uuid", "name": "my-pack", "version": "1.0.0"}
    seen = _capture_body(monkeypatch, mock_response, created)
    b = SolutionPackBuilder("My Pack", name="my-pack", version="1.0.0").add_module("alerts").tags("t1")
    resp = mock_client.solution_packs.create(b, publish=True)
    assert resp.uuid == "new-uuid"
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/3/solutionpacks")
    body = seen["json"]
    assert body["name"] == "my-pack"
    assert body["type"] == "solutionpack"
    assert body["installed"] is True and body["development"] is False
    assert body["local"] is True and body["draft"] is True
    assert body["recordTags"] == ["t1"]
    assert body["template"]["type"] == "SolutionPack Export"
    assert body["template"]["options"] == {"modules": [{"value": "alerts", "includedAttributes": []}]}


def test_create_publish_false_marks_development(mock_client, mock_response, monkeypatch):
    seen = _capture_body(monkeypatch, mock_response, {"uuid": "u", "name": "p", "version": "1.0.0"})
    b = SolutionPackBuilder("P", name="p", version="1.0.0").add_module("alerts")
    mock_client.solution_packs.create(b, publish=False)
    assert seen["json"]["installed"] is False
    assert seen["json"]["development"] is True


def test_install_from_file_returns_typed_and_reads_job(mock_client, mock_response, monkeypatch, tmp_path):
    """install_from_file() uploads the bundle and returns a typed response with job_id."""
    bundle = tmp_path / "pack.zip"
    bundle.write_bytes(b"PK\x03\x04stub")
    upload_resp = {
        "@id": "/api/3/solutionpacks/pack-uuid",
        "uuid": "pack-uuid",
        "name": "my-pack",
        "version": "1.0.0",
        "importJob": {"@id": "/api/3/import_jobs/job-uuid", "uuid": "job-uuid", "status": "Draft"},
    }
    seen = {}

    def _req(*args, **kwargs):
        seen.update(kwargs)
        return mock_response(json_data=upload_resp)

    monkeypatch.setattr("requests.Session.request", _req)
    result = mock_client.solution_packs.install_from_file(str(bundle))
    assert isinstance(result, SolutionPackInstallResponse)
    assert result.job_id == "job-uuid"
    assert seen["params"]["$type"] == "solutionpack"


def test_install_status_treats_503_as_importing(mock_client, mock_response, monkeypatch):
    """A pack import migrate briefly 503s the API; the poll must not abort."""

    def _req(*args, **kwargs):
        return mock_response(status_code=503, json_data={"message": "system down"})

    monkeypatch.setattr("requests.Session.request", _req)
    # 503 should be swallowed into a non-terminal "Importing" status, not raised.
    status = mock_client.solution_packs.install_status("job-uuid")
    assert status.status == "Importing"


def test_post_install_config_model_parses_live_shape():
    cfg = PostInstallConfig(
        enabled=True,
        widgets=[{"name": "w", "label": "W", "version": "1.0.0", "buttonLabel": "Go", "autoLaunch": True}],
    )
    assert isinstance(cfg.widgets[0], PostInstallWidget)
    assert cfg.widgets[0].autoLaunch is True
    assert cfg["enabled"] is True  # dict-compatible
