from typing import Dict, Optional

from .base import BaseAPI


class AlertsAPI(BaseAPI):
    """
    AlertsAPI provides methods to interact with FortiSIEM alert endpoints.

    This class handles all alert-related operations including creating, retrieving,
    listing, updating, and deleting alerts in the FortiSIEM system.

    Attributes:
        module (str): The API module name, set to 'alerts'
    """

    def __init__(self, client):
        """
        Initialize the AlertsAPI.

        Args:
            client: The API client instance used for making requests
        """
        super().__init__(client)
        self.module = 'alerts'

    def create(self, **data: Dict) -> Dict:
        """
        Create a new alert in FortiSIEM.

        Args:
            **data: Keyword arguments containing alert configuration:
                - name (str): Name of the alert
                - description (str, optional): Description of the alert
                - severity (str): Alert severity level ('Critical', 'High', 'Medium', 'Low')
                - enabled (bool, optional): Whether the alert is active, defaults to True
                - notification_groups (List[str], optional): List of notification group IDs
                - conditions (Dict): Alert triggering conditions

        Returns:
            Dict: The created alert object containing:
                - id (str): Unique identifier of the created alert
                - name (str): Name of the alert
                - created_at (str): Timestamp of alert creation
                - other alert properties as specified in creation

        Raises:
            APIError: If the alert creation fails
            ValidationError: If required fields are missing
        """
        return self._make_request('POST', f'/{self.module}', json=data)

    def get(self, alert_id: str) -> Dict:
        """
        Retrieve a specific alert by its ID.

        Args:
            alert_id (str): The unique identifier of the alert to retrieve

        Returns:
            Dict: The alert object containing:
                - id (str): Alert identifier
                - name (str): Alert name
                - description (str): Alert description
                - severity (str): Alert severity level
                - enabled (bool): Alert status
                - created_at (str): Creation timestamp
                - updated_at (str): Last update timestamp
                - conditions (Dict): Alert conditions
                - notification_groups (List[str]): Associated notification groups

        Raises:
            APIError: If the alert doesn't exist or cannot be retrieved
        """
        return self._make_request('GET', f'/{self.module}/{alert_id}')

    def list(self, params: Optional[Dict] = None) -> Dict:
        """
        Retrieve all alerts with optional filtering.

        Args:
            params (Dict, optional): Query parameters to filter alerts:
                - severity (str, optional): Filter by severity level
                - enabled (bool, optional): Filter by enabled status
                - created_after (str, optional): ISO timestamp to filter by creation date
                - page (int, optional): Page number for pagination
                - page_size (int, optional): Number of items per page

        Returns:
            Dict: Dictionary containing:
                - items (List[Dict]): List of alert objects
                - total (int): Total number of alerts
                - page (int): Current page number
                - page_size (int): Number of items per page

        Raises:
            APIError: If the request fails
        """
        return self._make_request('GET', f'/{self.module}', params=params)

    def update(self, alert_id: str, data: Dict) -> Dict:
        """
        Update an existing alert.

        Args:
            alert_id (str): The unique identifier of the alert to update
            data (Dict): Updated alert properties:
                - name (str, optional): New alert name
                - description (str, optional): New alert description
                - severity (str, optional): New severity level
                - enabled (bool, optional): New enabled status
                - notification_groups (List[str], optional): New notification groups
                - conditions (Dict, optional): New alert conditions

        Returns:
            Dict: The updated alert object with all current properties

        Raises:
            APIError: If the alert doesn't exist or update fails
            ValidationError: If provided data is invalid
        """
        return self._make_request('PUT', f'/{self.module}/{alert_id}', json=data)

    def delete(self, alert_id: str) -> None:
        """
        Delete an alert.

        Args:
            alert_id (str): The unique identifier of the alert to delete

        Raises:
            APIError: If the alert doesn't exist or deletion fails
        """
        return self._make_request('DELETE', f'/{self.module}/{alert_id}')
