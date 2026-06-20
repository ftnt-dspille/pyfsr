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
