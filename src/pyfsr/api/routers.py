"""Agent router records (``/api/3/routers``).

A *router* is a secure-message-exchange endpoint an execution agent dials into; an agent
record references one at create time (see :meth:`~pyfsr.api.agents.AgentsAPI.create`). On a
single-master appliance there is typically exactly one, created by
``sudo csadm secure-message-exchange enable``. Accessed as ``client.routers``.

Example::

    router = client.routers.first()          # the one configured router, or None
    client.agents.create("edge-1", router=router)
"""

from __future__ import annotations

from typing import Any

from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/routers"


class RoutersAPI(BaseAPI):
    """List the agent secure-message routers."""

    def list(self, *, limit: int = 2147483647) -> list[dict[str, Any]]:
        """Return router records (the ``hydra:member`` array). Each carries ``@id``,
        ``uuid``, ``name``, ``address``, ``sni``, and the broker ``certificate`` PEM."""
        return extract_members(self.client.get(_BASE, params={"$limit": limit}))

    def first(self) -> dict[str, Any] | None:
        """Return the first router by name (the usual single configured router), or None.

        Raises nothing if none exist — callers that require one (e.g. creating an agent)
        should check for ``None`` and prompt to enable the secure message exchange.
        """
        members = extract_members(self.client.get(_BASE, params={"$limit": 1, "$orderby": "+name"}))
        return members[0] if members else None
