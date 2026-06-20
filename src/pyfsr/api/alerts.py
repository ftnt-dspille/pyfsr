from typing import Any

from .base import BaseAPI


class AlertsAPI(BaseAPI):
    """
    The Alerts API provides methods for managing FortiSOAR alerts including creating,
    updating, and querying alerts.

    Example:
        Create a client and use the alerts API:

        .. code-block:: python

            from pyfsr import FortiSOAR

            # Initialize client
            client = FortiSOAR("your-server", "your-token")

            # Create new alert
            new_alert = {
                "name": "Suspicious Login",
                "description": "Multiple failed login attempts detected"
            }
            result = client.alerts.create(**new_alert)

            # Query alerts
            all_alerts = client.alerts.list()

            # Update alert
            client.alerts.update(
                alert_id="123",
                data={"assignedTo": "analyst@example.com"}
            )
    """

    def __init__(self, client):
        """
        Initialize the AlertsAPI.

        Args:
            client: The API client instance used for making requests
        """
        super().__init__(client)
        self.module = "alerts"

    def create(self, *, resolve_picklists: bool = True, **data: Any) -> dict[str, Any]:
        """
        Create a new alert in FortiSOAR.

        Args:
            resolve_picklists (bool): When True (default), friendly picklist
                values (e.g. ``severity="High"``) are mapped to the IRIs the API
                stores before sending. Pass ``resolve_picklists=False`` to skip
                that (and the metadata lookup it needs) when every value is
                already an IRI.
            **data (Any): Keyword arguments containing alert configuration.
                The following keys are expected:

                - **name** (*str*): Name of the alert.
                - **description** (*str, optional*): Description of the alert.
                - **severity** (*str*): Alert severity level, one of:
                    'Critical', 'High', 'Medium', or 'Low'.
        Returns:
            Dict[str, Any]: The created alert object.

        Example:
            .. code-block:: python

                # Friendly picklist values are resolved automatically.
                response = client.alerts.create(
                    name="Test Alert",
                    description="This is a test alert",
                    severity="High",
                )
        """
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        return self.client.post(f"/api/3/{self.module}", data=data)

    def list(self, params: dict | None = None) -> dict[str, Any]:
        """
        List all alerts with optional filtering.

        Args:
            params: Optional query parameters for filtering results

        Returns:
            Dict[str, Any]: List of alerts matching the criteria

        Example:
            .. code-block:: python

                # List all alerts
                alerts = client.alerts.list()

                # List with filtering
                filtered = client.alerts.list({"severity": "High"})
        """
        return self.client.get(f"/api/3/{self.module}", params=params)

    def get(self, alert_id: str) -> dict[str, Any]:
        """
        Get a specific alert by ID.

        Args:
            alert_id: The unique identifier of the alert

        Returns:
            Dict[str, Any]: The alert object

        Example:
            .. code-block:: python

                alert = client.alerts.get("alert-123")
                print(alert['name'])
        """

        return self.client.get(f"/api/3/{self.module}/{alert_id}")

    def update(self, alert_id: str, data: dict[str, Any], *, resolve_picklists: bool = True) -> dict[str, Any]:
        """
        Update an existing alert.

        Args:
            alert_id: The unique identifier of the alert
            data: Updated alert properties
            resolve_picklists: When True (default), friendly picklist values are
                mapped to IRIs before sending; pass False to skip that.

        Returns:
            Dict[str, Any]: The updated alert object

        Examples:
            .. code-block:: python

                client.alerts.update("alert-123", {
                    "severity": "Critical",
                    "description": "Updated description",
                })
        """
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        return self.client.put(f"/api/3/{self.module}/{alert_id}", data=data)

    def delete(self, alert_id: str) -> None:
        """
        Delete an alert.

        Args:
            alert_id: The unique identifier of the alert to delete

        Examples:
            >>> client.alerts.delete("alert-123")
        """
        self.client.delete(f"/api/3/{self.module}/{alert_id}")
