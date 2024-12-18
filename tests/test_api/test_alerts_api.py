def test_create_alert(mock_client, mock_response):
    """Test creating an alert"""
    alert_data = {
        "name": "Test Alert",
        "source": "Test Source",
        "severity": "/api/3/picklists/test-uuid"
    }
    expected_response = {
        "@id": "/api/3/alerts/test-uuid",
        "@type": "Alert",
        **alert_data
    }

    mock_client.session.request.return_value = mock_response(json_data=expected_response)
    response = mock_client.alerts.create(alert_data)

    assert response == expected_response
    mock_client.session.request.assert_called_once_with(
        'POST',
        f"{mock_client.base_url}/api/3/alerts",
        json=alert_data
    )


def test_get_alert(mock_client, mock_response):
    """Test getting an alert by ID"""
    alert_id = "test-uuid"
    expected_response = {
        "@id": f"/api/3/alerts/{alert_id}",
        "@type": "Alert",
        "name": "Test Alert"
    }

    mock_client.session.request.return_value = mock_response(json_data=expected_response)
    response = mock_client.alerts.get(alert_id)

    assert response == expected_response
    mock_client.session.request.assert_called_once()


def test_update_alert(mock_client, mock_response):
    """Test updating an alert"""
    alert_id = "test-uuid"
    update_data = {"status": "Closed"}
    expected_response = {
        "@id": f"/api/3/alerts/{alert_id}",
        "@type": "Alert",
        **update_data
    }

    mock_client.session.request.return_value = mock_response(json_data=expected_response)
    response = mock_client.alerts.update(alert_id, update_data)

    assert response == expected_response
    mock_client.session.request.assert_called_once()


def test_delete_alert(mock_client, mock_response):
    """Test deleting an alert"""
    alert_id = "test-uuid"
    mock_client.session.request.return_value = mock_response()

    mock_client.alerts.delete(alert_id)
    mock_client.session.request.assert_called_once_with(
        'DELETE',
        f"{mock_client.base_url}/api/3/alerts/{alert_id}"
    )