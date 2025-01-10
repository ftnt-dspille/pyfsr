import os
from pathlib import Path

import pytest

from pyfsr.client import FortiSOAR
from pyfsr.exceptions import UnsupportedAuthOperationError

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

    return FortiSOAR(base_url=config["fortisoar"]["base_url"], auth=auth,
                     verify_ssl=config["fortisoar"].get("verify_ssl", True), suppress_insecure_warnings=True,
                     verbose=True)


@pytest.fixture(scope="module")
def api_key_client():
    """Fixture that specifically requires API key authentication"""
    from pyfsr import FortiSOAR

    config = load_config()
    auth_config = config["fortisoar"]["auth"]

    if "api_key" not in auth_config:
        pytest.skip("API key authentication not configured")

    return FortiSOAR(base_url=config["fortisoar"]["base_url"], auth=auth_config["api_key"],
                     verify_ssl=config["fortisoar"].get("verify_ssl", True), suppress_insecure_warnings=True,
                     verbose=True)


@pytest.fixture(scope="module")
def user_pass_client() -> FortiSOAR:
    """Fixture that specifically requires username/password authentication"""
    from pyfsr import FortiSOAR

    config = load_config()
    auth_config = config["fortisoar"]["auth"]

    if "username" not in auth_config or "password" not in auth_config:
        pytest.skip("Username/password authentication not configured")

    return FortiSOAR(base_url=config["fortisoar"]["base_url"], auth=(auth_config["username"], auth_config["password"]),
                     verify_ssl=config["fortisoar"].get("verify_ssl", True), suppress_insecure_warnings=True,
                     verbose=True)


@pytest.fixture
def known_pack_name() -> str:
    """Known solution pack name for testing"""
    return "SOAR Framework"


@pytest.fixture
def non_existent_pack() -> str:
    """Non-existent solution pack name for testing"""
    return "Non-existent Pack 12345"


# test url missing https with invalid auth
def test_invalid_auth():
    """Test invalid authentication configuration"""
    from pyfsr import FortiSOAR

    config = load_config()
    url = config["fortisoar"]["base_url"]
    # strip https://
    url = url.replace("https://", "")

    with pytest.raises(ValueError):
        FortiSOAR(url, verify_ssl=False, auth=123)


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

        query_payload = {
            "logic": "AND",
            "filters": [
                {
                    "field": "name",
                    "operator": "eq",
                    "value": alert_data["name"]
                },
                {
                    "field": "severity",
                    "operator": "eq",
                    "value": alert_data["severity"]
                },
                {
                    "field": "uuid",
                    "operator": "eq",
                    "value": alert_id
                }
            ]
        }
        alerts = client.query("alerts", query_payload)
        assert all(a["@id"].endswith(alert_id) for a in alerts.get("hydra:member", []))

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


@pytest.mark.parametrize("client_fixture,should_raise", [
    ("api_key_client", True),
    ("user_pass_client", False)
])
@pytest.mark.integration
def test_export_config(request, client_fixture, should_raise, client):
    """Test configuration export functionality"""
    client = request.getfixturevalue(client_fixture)
    output_path = "test_export.zip"

    try:
        template = client.export_config.create_simplified_template(
            name="Integration Test Export",
            modules=["alerts"],
            picklists=["AlertStatus", "Severity"],
            connectors=["Code Snippet"],
            playbook_collections=["01 - Drafts"]
        )

        if should_raise:
            with pytest.raises(UnsupportedAuthOperationError):
                client.export_config.export_by_template_name(
                    template_name="Integration Test Export",
                    output_path=output_path
                )
        else:
            exported_file = client.export_config.export_by_template_name(
                template_name="Integration Test Export",
                output_path=output_path
            )
            assert Path(exported_file).exists()
            assert Path(exported_file).suffix == ".zip"

    finally:
        pass
        if os.path.exists(output_path):
            os.remove(output_path)


@pytest.mark.parametrize("client_fixture,should_raise", [
    ("api_key_client", True),
    ("user_pass_client", False)
])
@pytest.mark.integration
def test_export_pack(request, client_fixture, should_raise, client):
    """Test solution pack export functionality"""
    client = request.getfixturevalue(client_fixture)
    output_path = "test_export.zip"

    try:
        if should_raise:
            with pytest.raises(UnsupportedAuthOperationError):
                client.solution_packs.export_pack("SOAR Framework", output_path)
        else:
            exported_file = client.solution_packs.export_pack("SOAR Framework", output_path)
            assert Path(exported_file).exists()
            assert Path(exported_file).suffix == ".zip"

    finally:
        if os.path.exists(output_path):
            # os.remove(output_path)
            pass


@pytest.mark.integration
def test_find_installed_pack(client, known_pack_name, non_existent_pack):
    """Test finding a single installed solution pack"""
    # Test finding existing pack
    pack = client.solution_packs.find_installed_pack(known_pack_name)
    assert pack is not None
    assert pack["label"] == known_pack_name
    assert isinstance(pack, dict)
    assert "name" in pack
    assert "version" in pack

    # Test non-existent pack
    missing_pack = client.solution_packs.find_installed_pack(non_existent_pack)
    assert missing_pack is None


@pytest.mark.integration
def test_search_installed_packs(client, known_pack_name):
    """Test searching for multiple installed solution packs"""
    # Test default search (all installed packs)
    all_packs = client.solution_packs.search_installed_packs()
    assert isinstance(all_packs, list)
    assert len(all_packs) > 0
    assert all(isinstance(p, dict) for p in all_packs)
    assert all("name" in p for p in all_packs)

    # Test searching with known term
    matching_packs = client.solution_packs.search_installed_packs(known_pack_name)
    assert len(matching_packs) > 0
    assert any(p["label"] == known_pack_name for p in matching_packs)

    # Test limit parameter
    limited_packs = client.solution_packs.search_installed_packs(limit=1)
    assert len(limited_packs) == 1

    # Test empty search results
    empty_results = client.solution_packs.search_installed_packs("zzzzzzz")
    assert len(empty_results) == 0


@pytest.mark.integration
def test_find_available_pack(client, known_pack_name, non_existent_pack):
    """Test finding a single available solution pack"""
    # Test finding existing pack
    pack = client.solution_packs.find_available_pack(known_pack_name)
    assert pack is not None
    assert pack["label"] == known_pack_name
    assert isinstance(pack, dict)
    assert "name" in pack
    assert "version" in pack

    # Test empty search term (should return first available pack)
    default_pack = client.solution_packs.find_available_pack()
    assert default_pack is not None
    assert isinstance(default_pack, dict)

    # Test non-existent pack
    missing_pack = client.solution_packs.find_available_pack(non_existent_pack)
    assert missing_pack is None


@pytest.mark.integration
def test_search_available_packs(client, known_pack_name):
    """Test searching for multiple available solution packs"""
    # Test default search (all available packs)
    all_packs = client.solution_packs.search_available_packs()
    assert isinstance(all_packs, list)
    assert len(all_packs) > 0
    assert all(isinstance(p, dict) for p in all_packs)
    assert all("name" in p for p in all_packs)

    # Test searching with known term
    matching_packs = client.solution_packs.search_available_packs(known_pack_name)
    assert len(matching_packs) > 0
    assert any(p["label"] == known_pack_name for p in matching_packs)

    # Test limit parameter
    limited_packs = client.solution_packs.search_available_packs(limit=1)
    assert len(limited_packs) == 1

    # Test empty search results
    empty_results = client.solution_packs.search_available_packs("zzzzzzz")
    assert len(empty_results) == 0


@pytest.mark.integration
def test_auth_endpoints_user_pass(user_pass_client):
    """Test that /auth endpoints are not restricted with username/password"""
    response = user_pass_client.get('/api/auth/license/?param=license_details')
    assert type(response) == dict
    assert "users" in response


@pytest.mark.integration
def test_auth_endpoints_license(api_key_client):
    """Test that /auth endpoints are not restricted with username/password"""
    with pytest.raises(UnsupportedAuthOperationError):
        api_key_client.get('/api/auth/license/?param=license_details')


@pytest.mark.integration
def test_get_alerts(client):
    """Test get alerts"""
    alerts = client.get("alerts")
    assert type(alerts) == dict
    assert "hydra:member" in alerts
    assert type(alerts["hydra:member"]) == list
    assert len(alerts["hydra:member"]) > 0
    assert "name" in alerts["hydra:member"][0]
    assert "alerts" in alerts["hydra:member"][0]["@id"]
