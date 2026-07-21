"""The alerts module — ``client.alerts``.

A typed shortcut over :class:`~pyfsr.records.RecordSet` for the alerts module:
create/read/update alerts with friendly picklist values (``severity``,
``status``, ``type``) resolved to IRIs automatically. Reads return raw Hydra
dicts rather than typed models — see :doc:`/guides/records` for the
dict-vs-model distinction and when to reach for each.
"""

from typing import Any

from .base import BaseAPI


class AlertsAPI(BaseAPI):
    """Typed shortcut for the alerts module.

    ``client.alerts`` is a thin wrapper over :class:`~pyfsr.records.RecordSet`
    that returns raw dicts (Hydra envelopes) instead of typed models — see
    :doc:`/guides/records` for the dict-vs-model distinction.

    >>> client = demo_client()
    >>> alert = client.alerts.get("9f0eb603-ac1e-41c3-b47b-444589beed39")
    >>> (alert["@type"], alert["name"])
    ('Alert', 'Response Capture Test Alert')
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

        .. note::

            This returns the raw hydra envelope (``{"hydra:member": [...], ...}``),
            so callers index ``["hydra:member"]`` by hand. Prefer the modern
            :meth:`client.records("alerts") <pyfsr.client.FortiSOAR.records>`
            surface, which unpacks the envelope, returns typed (dict-compatible)
            :class:`~pyfsr.models.Alert` records, and offers ``.first()`` /
            ``.list()`` / iteration::

                from pyfsr import Query

                latest = client.records("alerts").first(
                    Query().sort("createDate", "DESC")
                )

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

        Example:
            >>> client = demo_client()
            >>> client.alerts.delete("9f0eb603-ac1e-41c3-b47b-444589beed39")
        """
        self.client.delete(f"/api/3/{self.module}/{alert_id}")
