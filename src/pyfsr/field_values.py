"""Check record values against their field's display type before a write.

FortiSOAR stores several wrong values without complaint and answers others with a
message that points the wrong way. Each rule below was observed on a live
appliance (``tests/integration/test_field_type_matrix_integration.py`` records them):

- datetime: epoch **milliseconds** are stored as a date in 2008; an ISO string is
  refused with "Could not convert PHP value '' of type 'boolean' to type 'datetime'".
- checkbox: any string is stored as ``true`` (so ``"no"`` reads back true).
- integer: above the 32-bit range, or a non-whole float, answers HTTP 500.
- ipv4 / ipv6 / email / url / json: a malformed value is stored as given.
- a field name the module does not have (``descriptoin``) answers 200 and is
  dropped without a word, so the write looks done.

Values with one clear meaning are converted (ISO date and millis -> epoch seconds,
``"yes"``/``"no"`` -> bool, ``5.0`` -> ``5``); the rest are refused with
:class:`~pyfsr.exceptions.ValidationError` naming every bad field, before anything
is sent. Rules key on the display type (``form_type``): a datetime *stores* as
``integer``, so the storage type cannot tell a date from a count.
"""

from __future__ import annotations

import difflib
import ipaddress
import re
from datetime import UTC, datetime
from typing import Any

from .exceptions import ValidationError

DATE_FORM_TYPES = frozenset({"datetime", "date"})
INT32_MAX = 2**31 - 1
# Epoch seconds stay below this until the year 5138; anything larger is milliseconds.
EPOCH_MS_FLOOR = 10**11
_TRUE = frozenset({"true", "yes", "y", "1", "on"})
_FALSE = frozenset({"false", "no", "n", "0", "off"})
_FORMATS = {
    "email": re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+"),
    "url": re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE),
}


def check_value(form_type: str, value: Any) -> tuple[Any, str | None, str | None]:
    """Check one value against a display type.

    Returns ``(value_to_send, conversion_note, refusal)``; at most one of the last
    two is set. Unknown display types and ``None`` pass through unchanged.
    """
    form = (form_type or "").lower()
    if value is None:
        return value, None, None
    if form in DATE_FORM_TYPES:
        return _date(value)
    if form == "checkbox":
        return _checkbox(value)
    if form == "integer":
        return _integer(value)
    if form in ("ipv4", "ipv6") and isinstance(value, str):
        try:
            ok = ipaddress.ip_address(value.strip()).version == int(form[-1])
        except ValueError:
            ok = False
        return value, None, None if ok else f"{value!r} is not a valid {form.upper()} address"
    if form in _FORMATS and isinstance(value, str) and not _FORMATS[form].fullmatch(value.strip()):
        return value, None, f"{value!r} is not a valid {form}"
    if form in ("json", "object") and not isinstance(value, (dict, list)):
        return value, None, f"a {form} field needs an object or list, got {type(value).__name__}"
    return value, None, None


def _date(value: Any) -> tuple[Any, str | None, str | None]:
    if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            return value, None, f"{value!r} is not a date: send epoch seconds or ISO 8601"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp()), "ISO date -> epoch seconds", None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) >= EPOCH_MS_FLOOR:
        return int(value // 1000), "epoch milliseconds -> seconds", None
    return value, None, None


def _checkbox(value: Any) -> tuple[Any, str | None, str | None]:
    if isinstance(value, bool):
        return value, None, None
    text = str(value).strip().lower()
    if text in _TRUE:
        return True, f"{value!r} -> true", None
    if text in _FALSE:
        return False, f"{value!r} -> false", None
    return value, None, f"{value!r} is not true/false (FortiSOAR would store it as true)"


def _integer(value: Any) -> tuple[Any, str | None, str | None]:
    n = value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        n = int(value)
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if not isinstance(n, int) or isinstance(n, bool):
        return value, None, f"{value!r} is not a whole number"
    if abs(n) > INT32_MAX:
        return value, None, f"{value!r} is outside the 32-bit integer range (FortiSOAR answers 500)"
    return n, None, None


def check_record(
    form_types: dict[str, str], data: dict[str, Any], *, module: str = ""
) -> tuple[dict[str, Any], list[str]]:
    """Check every field of ``data`` whose display type is known.

    Returns ``(data_to_send, conversion_notes)``. Raises
    :class:`~pyfsr.exceptions.ValidationError` listing every refused field, so one
    call reports all of them. Fields not in ``form_types`` pass through.
    """
    out, notes, refused = dict(data), [], []
    for name, value in data.items():
        form = form_types.get(name)
        if not form:
            continue
        sent, note, problem = check_value(form, value)
        if problem:
            refused.append(f"{name}: {problem}")
        else:
            out[name] = sent
            if note:
                notes.append(f"{name}: {note}")
    if refused:
        where = f" for {module}" if module else ""
        raise ValidationError(f"refused before writing{where}: " + "; ".join(refused))
    return out, notes


# Writable record keys that are not module fields (system and ownership keys).
SYSTEM_KEYS = frozenset(
    {
        "id",
        "uuid",
        "createDate",
        "createUser",
        "modifyDate",
        "modifyUser",
        "recordTags",
        "tenant",
        "tenantRecordId",
        "owners",
        "conflict",
        "fsrQueue",
    }
)


def unknown_fields_problem(field_names: Any, data: dict[str, Any]) -> str | None:
    """Name every key of ``data`` the module does not have, with the closest field.

    ``@``/``__`` keys and :data:`SYSTEM_KEYS` are allowed. Returns ``None`` when
    every key is a field.
    """
    names = set(field_names)
    bad = [k for k in data if k not in names and k not in SYSTEM_KEYS and not k.startswith(("@", "__"))]
    if not bad:
        return None
    parts = []
    for k in bad:
        close = difflib.get_close_matches(k, names, n=1)
        parts.append(f"{k!r} is not a field" + (f" (did you mean {close[0]!r}?)" if close else ""))
    return "; ".join(parts) + " -- FortiSOAR would drop it silently"
