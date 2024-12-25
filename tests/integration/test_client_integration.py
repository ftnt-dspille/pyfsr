import os
import tomllib
from pathlib import Path

import pytest


def load_config():
    """Load test configuration from config file"""
    config_path = Path(__file__).parent.parent.parent / 'examples' / 'config.toml'
    if not config_path.exists():
        pytest.skip("Integration test config not found")

    with open(config_path, 'rb') as f:
        return tomllib.load(f)


@pytest.fixture(scope="module")
def client():
    """Create real FortiSOAR client for integration tests"""
    from pyfsr import FortiSOAR

    config = load_config()

    return FortiSOAR(
        base_url=config["fortisoar"]["base_url"],
        auth=(
            config["fortisoar"]["auth"]["username"],
            config["fortisoar"]["auth"]["password"]
        ),
        verify_ssl=config["fortisoar"].get("verify_ssl", True),
        suppress_insecure_warnings=True
    )


@pytest.mark.integration
def test_alert_lifecycle(client):
    """Test complete alert lifecycle with real API"""
    # Create alert
    alert_data = {
        "name": "Integration Test Alert",
        "description": "Test alert from integration tests",
        "severity": "/api/3/picklists/58d0753f-f7e4-403b-953c-b0f521eab759"  # High
    }

    created_alert = client.alerts.create(**alert_data)
    alert_id = created_alert["@id"].split("/")[-1]

    try:
        # Verify alert was created
        retrieved_alert = client.alerts.get(alert_id)
        assert retrieved_alert["name"] == alert_data["name"]

        # Update alert
        update_data = {
            "description": "Updated test description"
        }
        updated_alert = client.alerts.update(alert_id, update_data)
        assert updated_alert["description"] == update_data["description"]

        # List alerts and verify our test alert is present
        alerts = client.alerts.list({"name": alert_data["name"]})
        assert any(a["@id"].endswith(alert_id) for a in alerts.get("hydra:member", []))

    finally:
        # Cleanup - delete test alert
        client.alerts.delete(alert_id)

        # Verify deletion
        with pytest.raises(Exception):
            client.alerts.get(alert_id)


@pytest.mark.integration
def test_file_upload(client):
    """Test file upload functionality"""
    # Create test file
    test_file = Path(__file__).parent.parent / "resources" / "sample_files" / "test.txt"
    test_file.parent.mkdir(exist_ok=True)
    test_file.write_text("Test content for file upload")

    try:
        # Upload file
        result = client.files.upload(str(test_file))
        assert result["@type"] == "File"
        assert result["filename"] == test_file.name

        # Create attachment using uploaded file
        attachment_data = {
            "name": "Test Attachment",
            "description": "Test attachment from integration tests",
            "file": result["@id"]
        }

        attachment = client.post("/api/3/attachments", data=attachment_data)
        assert attachment["name"] == attachment_data["name"]

        # delete attachment
        client.delete(attachment["@id"])

    finally:
        # Cleanup
        test_file.unlink()


@pytest.mark.integration
def test_export_config(client):
    """Test configuration export functionality"""
    # Create export template
    template = client.export_config.create_simplified_template(
        name="Integration Test Export",
        modules=["alerts"],
        picklists=["AlertStatus", "Severity"]
    )

    try:
        # Export using template
        output_path = "test_export.zip"
        exported_file = client.export_config.export_by_template_name(
            template_name="Integration Test Export",
            output_path=output_path
        )

        assert Path(exported_file).exists()
        assert Path(exported_file).suffix == ".zip"

    finally:
        # Cleanup
        if os.path.exists(output_path):
            os.remove(output_path)
