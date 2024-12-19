from ..exceptions import APIError


class BaseAPI:
    """Base API class for all module-specific APIs"""

    def __init__(self, client):
        self.client = client

    def _make_request(self, method: str, endpoint: str, **kwargs):
        """Make API request with error handling"""
        try:
            response = self.client.request(method, endpoint, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise APIError(f"API request failed: {str(e)}")
