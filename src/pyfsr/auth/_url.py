"""Shared ``base_url`` validation for auth backends.

A malformed ``base_url`` (missing scheme, stray path/query, typo'd host) used
to surface deep inside ``requests`` as a generic connection error — "Name or
service not known" or similar — with no hint that the URL itself was the
problem. Validating upfront with Pydantic gives a clear, actionable message at
construction time instead.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from ..exceptions import ValidationError

_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


def normalize_base_url(base_url: str) -> str:
    """Validate ``base_url`` is a well-formed ``http(s)://host[:port]`` URL and
    return it with any trailing slash stripped.

    Raises :class:`~pyfsr.exceptions.ValidationError` with a specific reason
    (missing scheme, not http/https, no host) rather than letting a bad URL
    fail confusingly later inside ``requests``.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValidationError("base_url must be a non-empty string")
    candidate = base_url.strip()
    try:
        parsed = _URL_ADAPTER.validate_python(candidate)
    except PydanticValidationError as exc:
        raise ValidationError(
            f"base_url {candidate!r} is not a valid http(s) URL: {exc.errors()[0].get('msg', exc)}"
        ) from None
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(f"base_url {candidate!r} must use http or https, got {parsed.scheme!r}")
    return candidate.rstrip("/")
