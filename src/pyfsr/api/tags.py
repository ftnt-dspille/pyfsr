"""Tag listing (``/api/3/tags``).

FortiSOAR stores a tag's human name in its ``uuid`` column — there is no separate name
field — and the list endpoint only returns rows when ``$export=true`` is set. This wrapper
hides both footguns: :meth:`~pyfsr.api.tags.TagsAPI.list` returns plain tag-name strings.
Accessed as ``client.tags``.

Example::

    client.tags.list()                 # every tag name
    client.tags.list(prefix="mitre")   # names starting with "mitre"
"""

from __future__ import annotations

import urllib.parse

from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/tags"


def _tag_name(member: object) -> str | None:
    """The tag name from a listing member, tolerant of both wire shapes.

    Some appliances return ``hydra:member`` as bare name strings; others return
    dicts carrying the name in the ``uuid`` column (FortiSOAR stores it there).
    """
    if isinstance(member, str):
        return member or None
    if isinstance(member, dict):
        return member.get("uuid") or member.get("name") or None
    return None


def _tag_iri(member: object, name: str) -> str:
    """The IRI a record stores for a tag — the dict's ``@id`` when present,
    else ``/api/3/tags/<name>`` (the tag name doubles as its uuid)."""
    if isinstance(member, dict) and member.get("@id"):
        return member["@id"]
    return f"/api/3/tags/{urllib.parse.quote(name, safe='')}"


class TagsAPI(BaseAPI):
    """List tags by name."""

    def list(self, *, prefix: str | None = None, limit: int = 200) -> list[str]:
        """Return tag **names** (strings), optionally filtered to those starting with
        ``prefix``.

        ``$export=true`` is always sent — without it the endpoint returns nothing.
        ``prefix`` filters server-side via ``uuid$like`` (the tag name lives in the ``uuid``
        column); the ``%`` wildcard is appended and URL-encoded by the client.

        Tolerates both wire shapes: members as bare name strings (seen on 7.6.x
        demo boxes) or as dicts carrying the name in ``uuid``.

        Doctest:

            >>> from pyfsr._testing import demo_client
            >>> client = demo_client()
            >>> tags = client.tags.list()
            >>> len(tags)
            3
            >>> tags[0]
            '2022 Annual Report'
            >>> tags[1]
            '3CX Supply Chain Attack'
        """
        params: dict[str, object] = {"$export": "true", "$limit": limit}
        if prefix:
            params["uuid$like"] = f"{prefix}%"
        members = extract_members(self.client.get(_BASE, params=params))
        return [name for name in (_tag_name(m) for m in members) if name]

    def map_names(self, *, limit: int = 2147483647) -> dict[str, str]:
        """Return ``{tag_name: IRI}`` for every tag — what a writer needs to set a
        record's ``tags`` to ``/api/3/tags/<uuid>`` from a friendly name.

        Same ``$export=true`` quirk as :meth:`list`. Tolerates both wire shapes:
        a dict member's IRI is its ``@id``; a bare-string member maps to
        ``/api/3/tags/<name>`` (the tag name doubles as its uuid).
        """
        params: dict[str, object] = {"$export": "true", "$limit": limit}
        members = extract_members(self.client.get(_BASE, params=params))
        out: dict[str, str] = {}
        for m in members:
            name = _tag_name(m)
            if name:
                out[name] = _tag_iri(m, name)
        return out
