"""Tag listing (``/api/3/tags``).

FortiSOAR stores a tag's human name in its ``uuid`` column — there is no separate name
field — and the list endpoint only returns rows when ``$export=true`` is set. This wrapper
hides both footguns: :meth:`list` returns plain tag-name strings. Accessed as
``client.tags``.

Example::

    client.tags.list()                 # every tag name
    client.tags.list(prefix="mitre")   # names starting with "mitre"
"""

from __future__ import annotations

from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/tags"


class TagsAPI(BaseAPI):
    """List tags by name."""

    def list(self, *, prefix: str | None = None, limit: int = 200) -> list[str]:
        """Return tag **names** (strings), optionally filtered to those starting with
        ``prefix``.

        ``$export=true`` is always sent — without it the endpoint returns nothing.
        ``prefix`` filters server-side via ``uuid$like`` (the tag name lives in the ``uuid``
        column); the ``%`` wildcard is appended and URL-encoded by the client.
        """
        params: dict[str, object] = {"$export": "true", "$limit": limit}
        if prefix:
            params["uuid$like"] = f"{prefix}%"
        members = extract_members(self.client.get(_BASE, params=params))
        return [t["uuid"] for t in members if isinstance(t, dict) and t.get("uuid")]
