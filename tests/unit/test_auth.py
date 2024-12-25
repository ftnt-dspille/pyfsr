def test_api_key_auth_headers():
    """Test API key authentication headers"""
    from pyfsr import FortiSOAR

    api_key = "test-api-key-123"
    client = FortiSOAR("https://test.fortisoar.com", api_key)

    headers = client.auth.get_auth_headers()
    assert headers["Authorization"] == f"API-KEY {api_key}"
    assert headers["Content-Type"] == "application/json"


def test_user_pass_auth_headers(mock_response, monkeypatch):
    """Test username/password authentication headers"""
    from pyfsr import FortiSOAR

    # Mock successful authentication response
    auth_response = {"token": "mock-jwt-token-123"}
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: mock_response(json_data=auth_response)
    )

    client = FortiSOAR(
        "https://test.fortisoar.com",
        ("test_user", "test_pass")
    )

    headers = client.auth.get_auth_headers()
    assert headers["Authorization"] == f"Bearer {auth_response['token']}"
    assert headers["Content-Type"] == "application/json"

