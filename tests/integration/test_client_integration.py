import os
from pathlib import Path

import pytest

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Backport for older versions


def load_config():
    """Load test configuration from config file"""
    config_path = Path(__file__).parent.parent.parent / 'examples' / 'config.toml'
    if not config_path.exists():
        pytest.skip("Integration test config not found")

    with open(config_path, 'rb') as f:
        return tomllib.load(f)


def get_auth_from_config(config):
    """
    Extract authentication details from config.
    Supports both API key and username/password authentication.
    """
    auth_config = config["fortisoar"]["auth"]

    # Check for API key first
    if "api_key" in auth_config:
        return auth_config["api_key"]

    # Fall back to username/password
    if "username" in auth_config and "password" in auth_config:
        return (auth_config["username"], auth_config["password"])

    raise ValueError("No valid authentication configuration found. "
                     "Please provide either api_key or username/password.")


@pytest.fixture(scope="module")
def client():
    """
    Create FortiSOAR client for integration tests.
    Supports both API key and username/password authentication.

    Expected config.toml format:

    [fortisoar]
    base_url = "https://your-fortisoar-instance"
    verify_ssl = true  # optional

    [fortisoar.auth]
    # Either:
    api_key = "your-api-key"
    # Or:
    username = "your-username"
    password = "your-password"
    """
    from pyfsr import FortiSOAR

    config = load_config()
    auth = get_auth_from_config(config)

    return FortiSOAR(
        base_url=config["fortisoar"]["base_url"],
        auth=auth,
        verify_ssl=config["fortisoar"].get("verify_ssl", True),
        suppress_insecure_warnings=True
    )


@pytest.fixture(scope="module")
def api_key_client():
    """Fixture that specifically requires API key authentication"""
    from pyfsr import FortiSOAR

    config = load_config()
    auth_config = config["fortisoar"]["auth"]

    if "api_key" not in auth_config:
        pytest.skip("API key authentication not configured")

    return FortiSOAR(
        base_url=config["fortisoar"]["base_url"],
        auth=auth_config["api_key"],
        verify_ssl=config["fortisoar"].get("verify_ssl", True),
        suppress_insecure_warnings=True
    )


@pytest.fixture(scope="module")
def user_pass_client():
    """Fixture that specifically requires username/password authentication"""
    from pyfsr import FortiSOAR

    config = load_config()
    auth_config = config["fortisoar"]["auth"]

    if "username" not in auth_config or "password" not in auth_config:
        pytest.skip("Username/password authentication not configured")

    return FortiSOAR(
        base_url=config["fortisoar"]["base_url"],
        auth=(auth_config["username"], auth_config["password"]),
        verify_ssl=config["fortisoar"].get("verify_ssl", True),
        suppress_insecure_warnings=True
    )


@pytest.mark.integration
@pytest.mark.parametrize("client_fixture", [
    pytest.param("api_key_client", id="api-key"),
    pytest.param("user_pass_client", id="user-pass")
])
def test_alert_lifecycle(request, client_fixture, client):
    """Test complete alert lifecycle with real API using both auth methods"""
    # Get the appropriate client fixture
    client = request.getfixturevalue(client_fixture)

    # Create alert
    alert_data = {
        "name": f"Integration Test Alert - {client_fixture}",
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
