# tests/test_solution_packs.py
def test_find_installed_pack(mock_client, mock_response, monkeypatch):
    """Test finding an installed solution pack"""
    expected_response = {
        "@context": "/api/3/contexts/SolutionPack",
        "hydra:member": [{
            "name": "SOAR Framework",
            "label": "SOAR Framework",
            "version": "1.0.0",
            "installed": True
        }]
    }

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=expected_response)
    )

    result = mock_client.solution_packs.find_installed_pack("SOAR Framework")
    assert result["name"] == "SOAR Framework"
    assert result["installed"] is True
