"""Typed models for Configuration Export template ``options`` entries.

Each model is one entry in an ``options.<category>[]`` list. The field sets and
their required/optional status were established empirically against a live 8.0.0
appliance by creating stripped-down templates and observing which entries the
export engine actually emitted (see :class:`~pyfsr.api.export_config.ExportTemplate`).

Highlights from that probing:

- **RecordSet** — only ``type`` and a ``query`` carrying a ``limit`` are needed to
  emit records. ``limit`` is the *trigger*: a record set whose query has no
  ``limit`` exports **zero** records. ``label`` / ``include`` /
  ``includeCorrelations`` are optional (the engine even ignores ``include: false``
  and exports anyway).
- **ModuleSelection** — only ``value`` (the module api name) is required;
  ``includedAttributes`` is optional (omit to export the whole schema).
- **ConnectorSelection** — only ``value`` (the ``cyops-connector-<name>-<ver>``
  string) is required; the engine ignores a bare ``name``.
- **PlaybookCollectionSelection** — only ``value`` (the collection uuid) is required.

Field names are the exact camelCase wire keys (these models *are* the wire form,
not a re-cased view of it), so :meth:`_ExportEntry.wire` is a plain dump.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _ExportEntry(BaseModel):
    """Base for an ``options.<category>[]`` entry. Fields are the wire keys."""

    model_config = ConfigDict(extra="allow")

    def wire(self) -> dict[str, Any]:
        """Render the entry as its wire dict, dropping unset optional keys."""
        return self.model_dump(exclude_none=True)


class ModuleSelection(_ExportEntry):
    """A module **schema** selection (``options.modules[]``).

    ``value`` is the module api name (e.g. ``"alerts"``). ``includedAttributes``
    limits the exported fields; leave empty to export the whole schema.
    """

    value: str
    includedAttributes: list[str] = []


class RecordSet(_ExportEntry):
    """A filtered **record-data** export (``options.recordSets[]``).

    ``query`` must carry a ``limit`` (the record-export trigger — absent means the
    engine emits no records). The rest of ``query`` is a standard
    :meth:`pyfsr.query.Query.to_body` dict, so filtering works as elsewhere.
    """

    type: str
    query: dict[str, Any]
    label: str | None = None
    include: bool = True
    includeCorrelations: bool = False


class ConnectorSelection(_ExportEntry):
    """A connector selection (``options.connectors[]``).

    ``value`` (the ``cyops-connector-<name>-<version>`` string) is the only field
    the engine keys on; a bare ``name`` is ignored. ``configurations`` toggles
    whether the connector's saved configs (secrets) ride along.
    """

    value: str
    label: str | None = None
    version: str | None = None
    include: bool = True
    rpm: bool = True
    configurations: bool = True
    configCount: int = 0
    recordCount: int = 0


class RoleSelection(_ExportEntry):
    """An RBAC role selection (``options.roles[]``).

    ``value`` is the role IRI (``/api/3/roles/<uuid>``) — the field the engine
    keys on, mirroring connectors/collections. ``label``/``name``/``uuid`` are the
    identity fields the wizard echoes from the resolved role (kept so a
    round-tripped template reads friendly); they are optional. Live-observed shape
    on 8.0.0: ``{value, label, name, uuid, include}``.
    """

    value: str
    label: str | None = None
    name: str | None = None
    uuid: str | None = None
    include: bool = True


class TeamSelection(_ExportEntry):
    """A team selection (``options.teams[]``).

    ``value`` is the team IRI (``/api/3/teams/<uuid>``). Live-observed shape on
    8.0.0: ``{value, name, uuid, include}``.
    """

    value: str
    name: str | None = None
    uuid: str | None = None
    include: bool = True


class ActorSelection(_ExportEntry):
    """An actor (person) selection (``options.actors[]``).

    Actors resolve to people, so ``value`` is a ``/api/3/people/<uuid>`` IRI and
    the identity field is ``title`` (not ``name``). Live-observed shape on 8.0.0:
    ``{value, title, uuid, include}``.
    """

    value: str
    title: str | None = None
    uuid: str | None = None
    include: bool = True


class NavigationSelection(_ExportEntry):
    """A navigation-menu selection (``options.views[]``).

    The ``views`` export category ships slices of the left-hand navigation. Every
    entry targets the single "app" navigation view — ``value`` is always ``"app"``
    and ``uuid`` is that view's record uuid (resolved live from
    ``/api/views/1/app``). ``appendNavigation`` lists the top-level section titles
    to carry, and ``navigationOptions`` repeats each title with its own
    ``mergeType`` (``"merge"`` layers onto the target's nav, ``"replace"``
    overwrites it). Live-observed shape on 8.0.0.
    """

    value: str = "app"
    uuid: str
    mergeType: str = "merge"
    appendNavigation: list[str] = []
    navigationOptions: list[dict[str, Any]] = []


class PlaybookCollectionSelection(_ExportEntry):
    """A playbook-collection selection (``options.playbooks.collections[]``).

    ``value`` is the collection uuid. The ``include*`` flags mirror the wizard's
    Playbooks-step toggles for pulling the collection's dependent content.
    """

    value: str
    label: str | None = None
    include: bool = True
    recordCount: int = 0
    includeVersions: bool = True
    includeSchedules: bool = True
    includeGlobalVariables: bool = True
