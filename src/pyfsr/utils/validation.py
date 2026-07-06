"""Shared validation helpers used across ``api/*.py`` modules.

``is_uuid`` was independently reimplemented (identically) in ``roles.py``,
``teams.py``, ``playbooks.py``, ``modules_admin.py``, and
``workflow_collections.py`` -- a classic "two copies drift apart" risk. This is
the one copy.
"""

from __future__ import annotations

import re

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def is_uuid(value: str) -> bool:
    """Whether ``value`` looks like a bare UUID (as opposed to a name)."""
    return bool(UUID_RE.match(value.strip()))
