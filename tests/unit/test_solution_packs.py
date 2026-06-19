# tests/test_solution_packs.py
def test_find_installed_pack(mock_client, mock_response, monkeypatch):
    """Test finding an installed solution pack"""
    expected_response = {
        "@context": "/api/3/contexts/SolutionPack",
        "hydra:member": [
            {
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


def test_install_no_wait(mock_client, mock_response, monkeypatch):
    """install() with wait=False returns an InstallJobStatus immediately."""
    install_resp = {
        "@id": "/api/3/solutionpacks/abc-uuid",
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
    assert result.status == "Pending"


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
    """install() with wait=True polls until terminal and returns final status."""
    responses = iter(
        [
            # POST /api/3/solutionpacks/install
            {
                "@id": "/api/3/solutionpacks/abc-uuid",
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
