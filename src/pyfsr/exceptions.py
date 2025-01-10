"""Custom exceptions for the FortiSOAR API client."""
from typing import Optional


class FortiSOARException(Exception):
    """Base exception for FortiSOAR API errors."""

    def __init__(self, message: str = None, response=None):
        self.message = message
        self.response = response
        super().__init__(self.message)


class ValidationError(FortiSOARException):
    """Raised when API request validation fails."""
    pass


class AuthenticationError(FortiSOARException):
    """Raised when authentication fails."""
    pass


class ResourceNotFoundError(FortiSOARException):
    """Raised when a requested resource is not found."""
    pass


class PermissionError(FortiSOARException):
    """Raised when the user lacks required permissions."""
    pass


class APIError(FortiSOARException):
    """Generic API error."""
    pass


class UnsupportedAuthOperationError(FortiSOARException):
    """Operation not supported with current authentication method"""

    def __init__(self, operation: str, auth_type: str, message: Optional[str] = None):
        self.operation = operation
        self.auth_type = auth_type
        msg = message or f"Operation '{operation}' is not supported with {auth_type} authentication"
        super().__init__(msg)


def handle_api_error(response):
    """Convert API error responses to appropriate exceptions."""
    try:
        error_data = response.json()
    except Exception:
        error_data = {"message": response.text}

    error_type = error_data.get("type", "")
    message = error_data.get("message", "Unknown error occurred")

    if response.status_code == 400:
        if "ValidationException" in error_type:
            raise ValidationError(message, response)
        raise APIError(message, response)
    elif response.status_code == 401:
        raise AuthenticationError(message, response)
    elif response.status_code == 403:
        raise PermissionError(message, response)
    elif response.status_code == 404:
        raise ResourceNotFoundError(message, response)
    else:
        raise APIError(message, response)
