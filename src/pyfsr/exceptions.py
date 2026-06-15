"""Custom exceptions for the FortiSOAR API client."""


class FortiSOARException(Exception):
    """Base exception for FortiSOAR API errors.

    Carries the originating ``response`` plus, when available, the HTTP
    ``status_code`` and the FortiSOAR error ``error_type`` (the ``type`` field
    of the error body) so callers — and agents — can branch on them without
    re-parsing the response.
    """

    def __init__(self, message: str = None, response=None, *, error_type: str | None = None):
        self.message = message
        self.response = response
        self.error_type = error_type
        self.status_code = getattr(response, "status_code", None)
        super().__init__(self.message)


class ValidationError(FortiSOARException):
    """Raised when API request validation fails."""

    pass


class PicklistResolutionError(ValidationError):
    """Raised when a friendly picklist value can't be mapped to an IRI.

    Carries the offending field/value, the picklist name, and the valid options
    so callers (and AIs) get an actionable message instead of a server 400.
    """

    def __init__(self, field: str, value, picklist: str, valid_values: list[str]):
        self.field = field
        self.value = value
        self.picklist = picklist
        self.valid_values = valid_values
        shown = ", ".join(valid_values[:25]) + ("  …" if len(valid_values) > 25 else "")
        super().__init__(
            f"{field}={value!r} is not a valid '{picklist}' value. "
            f"Valid ({len(valid_values)}): {shown}"
        )


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

    def __init__(self, operation: str, auth_type: str, message: str | None = None):
        self.operation = operation
        self.auth_type = auth_type
        msg = message or f"Operation '{operation}' is not supported with {auth_type} authentication"
        super().__init__(msg)


def _extract_message(error_data):
    """Pull a human-readable message out of a FortiSOAR/Symfony error body.

    FortiSOAR returns at least two error shapes:

    - ``{"message": "..."}`` — the simple form.
    - Symfony validation errors — ``{"title": "Validation Failed", "detail": "...",
      "violations": [{"propertyPath": "...", "title": "..."}]}`` with **no** ``message``
      key. Reading only ``message`` collapsed these to "Unknown error occurred", hiding the
      real cause (e.g. an invalid attribute ``type`` rejected at ``/api/publish``).

    Prefer ``detail`` (most specific), then a rollup of ``violations``, then ``title``,
    then ``message``. Returns None if nothing usable is present.
    """
    if not isinstance(error_data, dict):
        return str(error_data) or None
    if error_data.get("message"):
        return error_data["message"]
    if error_data.get("detail"):
        return error_data["detail"]
    violations = error_data.get("violations")
    if isinstance(violations, list) and violations:
        parts = []
        for v in violations:
            if not isinstance(v, dict):
                continue
            path, title = v.get("propertyPath"), v.get("title") or v.get("message")
            parts.append(f"{path}: {title}" if path and title else (title or path or ""))
        rolled = "; ".join(p for p in parts if p)
        if rolled:
            return rolled
    return error_data.get("title")


def handle_api_error(response):
    """Convert API error responses to appropriate exceptions."""
    try:
        error_data = response.json()
    except Exception:
        error_data = {"message": response.text}

    error_type = error_data.get("type", "")
    message = _extract_message(error_data) or "Unknown error occurred"

    if response.status_code == 400:
        if "ValidationException" in error_type:
            raise ValidationError(message, response, error_type=error_type)
        raise APIError(message, response, error_type=error_type)
    elif response.status_code == 401:
        raise AuthenticationError(message, response, error_type=error_type)
    elif response.status_code == 403:
        raise PermissionError(message, response, error_type=error_type)
    elif response.status_code == 404:
        raise ResourceNotFoundError(message, response, error_type=error_type)
    else:
        raise APIError(message, response, error_type=error_type)
