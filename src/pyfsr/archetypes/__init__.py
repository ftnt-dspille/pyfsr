"""Use-case -> FortiSOAR-artifact archetype framework (Form 1: archetype recipes).

Archetypes are parameterized use-case shapes harvested from real FortiSOAR solution packs:
the module schema, connector manifest, and playbook skeleton that implement a use case, plus
the ``{{param}}`` slots a router fills to instantiate it. This package holds the record types,
the writable companion store, and the harvester.

Step 2 (this package) ships the harvester + store scaffolding only -- the router
(``map_use_case``), the first curated archetype (``reconcile-and-report``), and the MCP tools
are steps 3-4 of the ``mutable-yawning-fox`` plan.

Public surface::

    from pyfsr.archetypes import (
        Archetype,
        ArchetypeStore,
        harvest_from_dir,
        harvest_from_zip,
        harvest_archetype_from_pack,
    )
"""

from .harvest import (
    harvest_archetype_from_pack,
    harvest_from_dir,
    harvest_from_zip,
)
from .record import (
    Archetype,
    ConnectorUse,
    ModuleField,
    PlaybookSkeleton,
    StepSkeleton,
)
from .store import ArchetypeStore

__all__ = [
    "Archetype",
    "ArchetypeStore",
    "ConnectorUse",
    "ModuleField",
    "PlaybookSkeleton",
    "StepSkeleton",
    "harvest_archetype_from_pack",
    "harvest_from_dir",
    "harvest_from_zip",
]
