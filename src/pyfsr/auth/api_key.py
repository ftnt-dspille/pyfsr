from typing import Optional

import requests

from .base import BaseAuth


class APIKeyAuthError(Exception):
    """Exception raised for API key authentication errors."""
    pass


class APIKeyAuth(BaseAuth):
    """
    API Key authentication handler with validation.

    Validates the API key on initialization by making a test request to the FortiSOAR API.

    Args:
        api_key (str): The FortiSOAR API key
        base_url (str): Base URL of the FortiSOAR instance
        verify_ssl (bool, optional): Whether to verify SSL certificates. Defaults to True.

    Raises:
        APIKeyAuthError: If the API key validation fails

    Example:
        >>> auth = APIKeyAuth("https://fortisoar.example.com","your-api-key")
        >>> headers = auth.get_auth_headers()
    """

    def __init__(self, base_url: str, api_key: str, verify_ssl: Optional[bool] = True):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.verify_ssl = verify_ssl
        self._validate_api_key()

    def _validate_api_key(self) -> None:
        """
        Validates the API key by making a test request to the alerts endpoint.

        The alerts endpoint is used as it's commonly available and typically has low overhead.
        A limit of 1 is used to minimize data transfer.

        Raises:
            APIKeyAuthError: If the validation request fails
        """
        headers = self.get_auth_headers()
        try:
            # Make a minimal request to validate the API key
            response = requests.get(
                f"{self.base_url}/api/3/people",
                headers=headers,
                verify=self.verify_ssl
            )

            # Check for successful response
            if response.status_code == 401:
                raise APIKeyAuthError("Invalid API key - authentication failed")
            elif response.status_code != 200:
                raise APIKeyAuthError(
                    f"API key validation failed with status {response.status_code}: {response.text}"
                )

        except requests.exceptions.RequestException as e:
            raise APIKeyAuthError(f"API key validation request failed: {str(e)}")

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
            bool: True if the API key is valid, False otherwise
        """
        try:
            self._validate_api_key()
            return True
        except APIKeyAuthError:
            return False
