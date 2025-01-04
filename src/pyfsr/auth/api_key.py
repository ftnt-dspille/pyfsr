import requests

from .base import BaseAuth


class APIKeyAuthError(Exception):
    """Exception raised for API key authentication errors."""
    pass


class APIKeyAuth(BaseAuth):
    """
    API Key authentication handler for FortiSOAR.

    This class manages API key authentication for FortiSOAR requests. It validates the API key
    on initialization and provides methods to generate authentication headers.

    Parameters:
        base_url: Base URL of the FortiSOAR instance
        api_key: The FortiSOAR API key
        verify_ssl: Whether to verify SSL certificates. Defaults to True.

    Raises:
        APIKeyAuthError: If API key validation fails

    Examples:
        Create an API key authentication handler:

        >>> auth = APIKeyAuth(
        ...     base_url="https://fortisoar.example.com",
        ...     api_key="your-api-key"
        ... )
        >>> headers = auth.get_auth_headers()

        Create with SSL verification disabled:

        >>> auth = APIKeyAuth(
        ...     base_url="https://fortisoar.example.com",
        ...     api_key="your-api-key",
        ...     verify_ssl=False
        ... )

    Note:
        The API key is validated immediately upon initialization by making a test
        request to the FortiSOAR API. This helps catch invalid keys early.
    """

    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = True) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.verify_ssl = verify_ssl
        self._validate_api_key()

    def _validate_api_key(self) -> None:
        """
        Validates the API key by making a test request to the FortiSOAR API.

        The validation uses the /api/3/people endpoint as it's commonly available
        and typically has low overhead.

        Raises:
            APIKeyAuthError: If the validation request fails or returns an error status
        """
        headers = self.get_auth_headers()
        try:
            response = requests.get(
                f"{self.base_url}/api/3/people",
                headers=headers,
                verify=self.verify_ssl
            )

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
            dict: Dictionary containing required headers:
                - Authorization: API-KEY {api_key}
                - Content-Type: application/json
        """
        return {
            'Authorization': f'API-KEY {self.api_key}',
            'Content-Type': 'application/json'
        }

    def is_valid(self) -> bool:
        """
        Check if the API key is currently valid.

        Returns:
            True if the API key passes validation, False otherwise.

        Note:
            This method makes an actual API request to verify the key's validity.
            Consider caching the result if you need to check validity frequently.
        """
        try:
            self._validate_api_key()
            return True
        except APIKeyAuthError:
            return False
