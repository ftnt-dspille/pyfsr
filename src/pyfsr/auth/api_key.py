"""API key authentication for FortiSOAR"""
import requests

from .base import BaseAuth
from ..exceptions import APIError


class APIKeyAuth(BaseAuth):
    """
    API Key authentication handler for FortiSOAR.

    API key authentication has several limitations:
    - Cannot use /auth endpoints
    - Cannot export configurations

    Args:
        base_url: Base URL of the FortiSOAR instance 
        api_key: The FortiSOAR API key
        verify_ssl: Whether to verify SSL certificates. Defaults to True.

    Raises:
        APIError: If API key validation fails

    Example:
        >>> auth = APIKeyAuth(
        ...     base_url="https://fortisoar.example.com",
        ...     api_key="your-api-key"
        ... )
        >>> headers = auth.get_auth_headers()
    """

    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = True):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.verify_ssl = verify_ssl

        # Set unsupported operations
        self._unsupported_operations = {
            self.OPERATION_AUTH,
            self.OPERATION_CONFIG_EXPORT,
        }

        self._validate_api_key()

    def _validate_api_key(self) -> None:
        """
        Validates the API key by making a test request to the FortiSOAR API.

        Raises:
            APIError: If validation fails
        """
        headers = self.get_auth_headers()
        try:
            response = requests.get(
                f"{self.base_url}/api/3/people",
                headers=headers,
                verify=self.verify_ssl
            )

            if response.status_code == 401:
                raise APIError("Invalid API key - authentication failed")
            elif response.status_code != 200:
                raise APIError(
                    f"API key validation failed with status {response.status_code}: {response.text}"
                )

        except requests.exceptions.RequestException as e:
            raise APIError(f"API key validation request failed: {str(e)}")

    def get_auth_headers(self) -> dict:
        """
        Get the authentication headers required for API requests.

        Returns:
            dict: Headers including the API key authentication
        """
        return {
            'Authorization': f'API-KEY {self.api_key}',
            'Content-Type': 'application/json'
        }

    def is_valid(self) -> bool:
        """
        Check if the API key is currently valid.

        Returns:
            bool: True if the API key passes validation, False otherwise
        """
        try:
            self._validate_api_key()
            return True
        except APIError:
            return False
