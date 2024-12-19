from typing import Dict

from .base import BaseAPI


class AlertsAPI(BaseAPI):
    """Alerts API endpoints"""

    def __init__(self, client):
        super().__init__(client)
        self.module = 'alerts'

    def create(self, **data: Dict) -> Dict:
        """Create a new alert"""
        return self._make_request('POST', f'/{self.module}', json=data)

    def get(self, alert_id: str) -> Dict:
        """Get alert by ID"""
        return self._make_request('GET', f'/{self.module}/{alert_id}')

    def get_all(self) -> Dict:
        """Get All alerts"""
        return self._make_request('GET', f'/{self.module}')

    def update(self, alert_id: str, data: Dict) -> Dict:
        """Update an alert"""
        return self._make_request('PUT', f'/{self.module}/{alert_id}', json=data)

    def delete(self, alert_id: str) -> None:
        """Delete an alert"""
        return self._make_request('DELETE', f'/{self.module}/{alert_id}')
