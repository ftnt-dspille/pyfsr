"""The actors module — ``client.actors``.

An **actor** is any security principal in FortiSOAR. All of them live in a single
``actors`` table using single-table inheritance keyed on a ``record_type``
discriminator, so ``/api/3/actors`` is the union view spanning humans
(:class:`~pyfsr.models.User`, ``@type: "Person"``), appliances
(:class:`~pyfsr.models.Appliance`), and API keys (:class:`~pyfsr.models.ApiKey`).
The per-subtype collections (``/api/3/people``, ``/api/3/appliances``) are filtered
views of the same rows — use ``client.users`` when you specifically want people.

Reach for this API when you need the union — e.g. resolving the ``title`` shown in
the export wizard's *Actors* category, which lists every subtype together.

Note the collection is an **aggregate**: it accepts no server-side ``title``
filter, so :meth:`ActorsAPI.get` matches client-side over the (small) full list.
"""

from __future__ import annotations

from typing import Any

from ..models import Actor
from ..models._system import ApiKey, Appliance, User
from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/actors"

#: ``@type`` discriminator → concrete actor model. Anything unrecognized falls
#: back to :class:`User`, whose ``extra="allow"`` keeps unknown keys intact.
_BY_TYPE: dict[str, type[User] | type[Appliance] | type[ApiKey]] = {
    "Person": User,
    "Appliance": Appliance,
    "ApiKey": ApiKey,
}


def _as_actor(record: dict[str, Any]) -> Actor:
    """Parse one actor record into the concrete subtype named by its ``@type``."""
    model = _BY_TYPE.get(str(record.get("@type") or ""), User)
    return model.model_validate(record)


class ActorsAPI(BaseAPI):
    """Read the union of security principals (``/api/3/actors``).

    Example:
        .. code-block:: python

            # Every principal, typed by subtype
            for actor in client.actors.list():
                print(type(actor).__name__, actor.title)

            # Resolve the title the export wizard shows
            admin = client.actors.get("Admin")
    """

    def list(self, *, typed: bool = True) -> list[Actor] | list[dict[str, Any]]:
        """List every actor — people, appliances, and API keys together.

        Args:
            typed: when ``True`` (default) parse each record into its concrete
                model (:class:`~pyfsr.models.User` / :class:`~pyfsr.models.Appliance`
                / :class:`~pyfsr.models.ApiKey`) per its ``@type``; when ``False``
                return raw dicts.
        """
        members = [m for m in extract_members(self.client.get(_BASE)) if isinstance(m, dict)]
        if not typed:
            return members
        return [_as_actor(m) for m in members]

    def find_by_title(self, title: str, *, typed: bool = True) -> list[Actor] | list[dict[str, Any]]:
        """Return **every** actor whose ``title`` matches exactly.

        Titles are **not unique** — an appliance can carry several distinct actors
        (different uuids) under one title, which live 8.0.0 boxes do in practice.
        Use this when you need to see the ambiguity that :meth:`get` hides; an
        empty list means no match rather than an error.

        Note only ``Person`` actors have a title at all: the shared ``actors``
        table's ``title`` column is left unpopulated by the ``Appliance`` and
        ``ApiKey`` subtypes, which are identified by ``name``.

        Args:
            title: the exact, case-sensitive ``title`` to match.
            typed: parse into concrete actor models (default) or return dicts.
        """
        matches = [
            record
            for record in extract_members(self.client.get(_BASE))
            if isinstance(record, dict) and record.get("title") == title
        ]
        if not typed:
            return matches
        return [_as_actor(m) for m in matches]

    def get(self, title: str, *, typed: bool = True) -> Actor | dict[str, Any]:
        """Resolve a single actor by its exact ``title``.

        ``/api/3/actors`` is an aggregate and takes no ``title`` server filter, so
        the full (small) list is fetched once and matched client-side. The match is
        exact and case-sensitive.

        **Titles are not unique.** When several actors share ``title`` this returns
        the first the server lists — matching how the export wizard resolves the
        *Actors* category, so an export template built from this picks the same
        actor the UI would. If that ambiguity matters (the duplicates are distinct
        principals with distinct uuids), use :meth:`find_by_title` to see them all
        and select by uuid yourself.

        Args:
            title: the actor's ``title`` (e.g. ``"Admin"``).
            typed: parse into the concrete actor model (default) or return a dict.

        Raises:
            ValueError: if no actor carries that title.
        """
        for record in extract_members(self.client.get(_BASE)):
            if isinstance(record, dict) and record.get("title") == title:
                return _as_actor(record) if typed else record
        raise ValueError(f"actor {title!r} not found")
