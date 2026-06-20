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
        super().__init__(f"{field}={value!r} is not a valid '{picklist}' value. Valid ({len(valid_values)}): {shown}")


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


# Substrings the appliance returns (as 5xx bodies / error messages) while it is
# mid-migrate — a publish *or* a module-bearing import runs a full backup + DB
# migrate + cache-rebuild cycle, during which the API is briefly unavailable and
# surfaces transient state strings ("System Backup", "Clearing Cache", "Schema
# Update", "Decrypt Database", …) instead of real errors.
_MIGRATE_TRANSIENT_MARKERS = (
    "decrypt database",
    "encrypt database",
    "cleaning up old backups",
    "creating backup",
    "taking backup",
    "system backup",
    "restoring",
    "migrat",  # "migrating" / "migration in progress"
    "schema update",
    "clearing cache",
    "backup",
    "service temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway time-out",
    "gateway timeout",
)


def is_migrate_transient(exc: Exception) -> bool:
    """True if ``exc`` is a transient state surfaced while a migrate is in flight.

    Both a publish and an import that carries schema changes drive the appliance
    through a backup + DB migrate + cache-rebuild cycle. During that window the
    API is briefly down, returning 5xx and/or state strings like "System Backup"
    / "Clearing Cache" / "Schema Update" rather than a real error. We treat any
    5xx, plus any error message matching a known migrate-cycle marker, as "still
    working" so pollers keep waiting instead of failing.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    text = " ".join(str(getattr(exc, attr, "") or "") for attr in ("message", "error_type")).lower() or str(exc).lower()
    return any(marker in text for marker in _MIGRATE_TRANSIENT_MARKERS)


def describe_migrate_failure(status, message) -> str:
    """Build an actionable message for a failed publish/import migrate.

    ``status`` / ``message`` come from the failure's source of truth — an import
    job record (``status``/``errorMessage``) or a ``/api/publish/error`` body
    (``status``/``message``). For the half-applied-migration wedge (Postgres
    ``42P07`` / "already exists" / "Duplicate table") it appends remediation
    guidance, since pyfsr cannot repair appliance DB state over the API.
    """
    raw = str(message or "")
    msg = f"publish/migrate failed: {status}"
    if raw:
        msg += f" ({raw})"
    low = raw.lower()
    if "already exists" in low or "42p07" in low or "duplicate" in low:
        msg += (
            "\nThis is a half-applied migration: a prior migrate created a DB object "
            "(often an index, e.g. from a tableName rename) without recording it, so the "
            "appliance's CREATE (no IF NOT EXISTS) now fails on every publish/import. "
            "pyfsr cannot fix appliance DB state over the API — drop the orphaned relation "
            "named in the error on the FortiSOAR Postgres node (or restore a pre-migrate "
            "backup), then retry. To avoid re-triggering it, import the offending module "
            "with resolve='skip_schema' (don't re-apply the schema change)."
        )
    return msg


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
