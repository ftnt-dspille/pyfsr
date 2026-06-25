"""Unit tests for ViewTemplatesAPI.

These are offline tests (no live appliance) that verify the wrapper's request
construction, response parsing, and error handling.
"""

import pytest


# Mock the FortiSOAR client for these offline tests
class MockFortiSOAR:
    def get(self, url, params=None):
        return self._last_get_result or {}

    def post(self, url, data=None):
        return self._last_post_result or {}

    def put(self, url, data=None):
        return self._last_put_result or {}

    def delete(self, url):
        return self._last_delete_result or {}


def test_view_templates_api_init():
    """Test ViewTemplatesAPI initialization."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()
    api = ViewTemplatesAPI(mock_client)
    assert api.client is mock_client


def test_get_template():
    """Test get_template fetches a named view template."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()
    expected = {
        "name": "Custom Detail",
        "module": "alerts",
        "viewOptions": "detail",
        "type": "rows",
        "config": {"rows": []},
        "uuid": "test-uuid-123",
        "isDefault": False,
    }
    mock_client._last_get_result = expected

    api = ViewTemplatesAPI(mock_client)
    result = api.get_template("Custom Detail")

    assert result == expected


def test_list_templates_all():
    """Test list_templates returns all templates when no filter."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()
    expected = {
        "hydra:member": [
            {
                "uuid": "uuid-1",
                "name": "Detail 1",
                "module": "alerts",
                "viewOptions": "detail",
                "type": "rows",
            },
            {
                "uuid": "uuid-2",
                "name": "Detail 2",
                "module": "incidents",
                "viewOptions": "detail",
                "type": "rows",
            },
        ]
    }
    mock_client._last_get_result = expected

    api = ViewTemplatesAPI(mock_client)
    result = api.list_templates()

    assert len(result) == 2
    assert result[0]["module"] == "alerts"
    assert result[1]["module"] == "incidents"


def test_list_templates_filtered():
    """Test list_templates filters by module when specified."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()
    all_templates = {
        "hydra:member": [
            {
                "uuid": "uuid-1",
                "name": "Detail 1",
                "module": "alerts",
                "viewOptions": "detail",
            },
            {
                "uuid": "uuid-2",
                "name": "Detail 2",
                "module": "incidents",
                "viewOptions": "detail",
            },
            {
                "uuid": "uuid-3",
                "name": "Detail 3",
                "module": "alerts",
                "viewOptions": "list",
            },
        ]
    }
    mock_client._last_get_result = all_templates

    api = ViewTemplatesAPI(mock_client)
    result = api.list_templates(module="alerts")

    assert len(result) == 2
    assert all(t["module"] == "alerts" for t in result)


def test_get_default_template():
    """get_default_template returns the isDefault SVT row for a module/layout."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()
    mock_client._last_get_result = {
        "hydra:member": [
            {"uuid": "u1", "module": "alerts", "viewOptions": "detail", "isDefault": False},
            {"uuid": "u2", "module": "alerts", "viewOptions": "detail", "isDefault": True},
            {"uuid": "u3", "module": "alerts", "viewOptions": "list", "isDefault": True},
        ]
    }

    api = ViewTemplatesAPI(mock_client)
    result = api.get_default_template("alerts", "detail")

    assert result["uuid"] == "u2"


def test_get_default_template_invalid_kind():
    """get_default_template raises ValueError for an unknown layout kind."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    api = ViewTemplatesAPI(MockFortiSOAR())

    with pytest.raises(ValueError, match="kind must be one of"):
        api.get_default_template("alerts", "rows")


def test_get_default_template_none():
    """get_default_template returns None when no row is flagged default."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()
    mock_client._last_get_result = {
        "hydra:member": [
            {"uuid": "u1", "module": "alerts", "viewOptions": "detail", "isDefault": False},
        ]
    }
    api = ViewTemplatesAPI(mock_client)
    result = api.get_default_template("alerts", "detail")

    assert result is None


def test_create_template_minimal():
    """Test create_template with minimal required fields."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()
    created = {
        "@type": "SystemViewTemplate",
        "name": "MyTemplate",
        "module": "alerts",
        "viewOptions": "detail",
        "type": "rows",
        "config": {"rows": []},
        "isDefault": False,
        "uuid": "generated-uuid",
    }
    mock_client._last_post_result = created

    api = ViewTemplatesAPI(mock_client)
    result = api.create_template(
        "MyTemplate",
        config={"rows": []},
        module="alerts",
        viewOptions="detail",
    )

    assert result["name"] == "MyTemplate"
    assert result["module"] == "alerts"
    assert result["@type"] == "SystemViewTemplate"


def test_create_template_with_extra_fields():
    """Test create_template passes through unknown fields via **extra."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()

    posted_data = {}

    def mock_post(url, data=None):
        posted_data.update(data or {})
        data["uuid"] = "created-uuid"
        return data

    mock_client.post = mock_post

    api = ViewTemplatesAPI(mock_client)
    api.create_template(
        "MyTemplate",
        config={"rows": []},
        module="alerts",
        viewOptions="detail",
        roles=["Full App Permissions"],
    )

    assert posted_data.get("roles") == ["Full App Permissions"]


def test_update_template_partial():
    """Test update_template merges only provided fields."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()

    sent_data = {}

    def mock_put(url, data=None):
        sent_data.update(data or {})
        return {"name": "MyTemplate", "isDefault": True}

    mock_client.put = mock_put

    api = ViewTemplatesAPI(mock_client)
    api.update_template("MyTemplate", isDefault=True)

    assert "isDefault" in sent_data
    assert sent_data["isDefault"] is True
    assert "module" not in sent_data or sent_data.get("module") is None


def test_bulk_upsert_templates():
    """Test bulk_upsert_templates sends list with unique fields."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()

    sent_data = {}

    def mock_post(url, data=None):
        sent_data.update(data or {})
        return {"upserted": 2}

    mock_client.post = mock_post

    api = ViewTemplatesAPI(mock_client)
    templates = [
        {
            "uuid": "uuid-1",
            "name": "Template 1",
            "module": "alerts",
            "viewOptions": "detail",
        },
        {
            "uuid": "uuid-2",
            "name": "Template 2",
            "module": "incidents",
            "viewOptions": "detail",
        },
    ]
    result = api.bulk_upsert_templates(templates)

    assert sent_data.get("__data") == templates
    assert sent_data.get("__unique") == ["uuid"]
    assert result["upserted"] == 2


def test_set_default_template():
    """set_default_template upserts the full row with isDefault flipped (real path)."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    mock_client = MockFortiSOAR()

    sent = {}

    def mock_post(url, data=None):
        sent["url"] = url
        sent["data"] = data or {}
        return {"upserted": 1}

    mock_client.post = mock_post

    api = ViewTemplatesAPI(mock_client)
    row = {"uuid": "u2", "module": "alerts", "viewOptions": "detail", "isDefault": False}
    api.set_default_template(row)

    assert "bulkupsert/system_view_templates" in sent["url"]
    assert sent["data"]["__unique"] == ["uuid"]
    assert sent["data"]["__data"][0]["isDefault"] is True
    assert sent["data"]["__data"][0]["uuid"] == "u2"


def test_set_default_template_requires_full_row():
    """set_default_template rejects a fragment without a uuid."""
    from pyfsr.api.view_templates import ViewTemplatesAPI

    api = ViewTemplatesAPI(MockFortiSOAR())
    with pytest.raises(ValueError, match="full SVT row"):
        api.set_default_template({"module": "alerts"})


def test_endpoint_constants():
    """Test that endpoint constants are correct."""
    from pyfsr.api.view_templates import (
        _KINDS,
        _SYSTEM_TEMPLATES,
        _SYSTEM_TEMPLATES_BULK,
        _VIEWSET,
    )

    assert _VIEWSET == 1
    assert _KINDS == ("list", "detail", "form")
    assert _SYSTEM_TEMPLATES == "/api/3/system_view_templates"
    assert _SYSTEM_TEMPLATES_BULK == "/api/3/bulkupsert/system_view_templates"
