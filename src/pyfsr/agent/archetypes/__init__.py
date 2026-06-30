"""Use-case -> FortiSOAR-artifact archetype framework (Form 1: archetype recipes).

Archetypes are parameterized use-case shapes harvested from real FortiSOAR solution packs:
the module schema, connector manifest, and playbook skeleton that implement a use case, plus
the ``{{param}}`` slots a router fills to instantiate it. This package holds the record types,
the writable companion store, and the harvester.

This package ships the harvester + store (steps 1-2), the first curated archetype
``reconcile-and-report`` (step 3), and the ``map_use_case`` router (step 4).

Public surface::

    from pyfsr.agent.archetypes import (
        Archetype,
        ArchetypeStore,
        map_use_case,
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
from .router import map_use_case
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
    "map_use_case",
]
