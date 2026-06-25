"""The archetype record -- a parameterized use-case shape.

An **archetype** is the reusable shape of an operational use case (reconcile-and-report,
ingest-enrich-notify, ...): the module schema it needs, the connectors + operations it uses,
and a playbook skeleton, plus the ``{{param}}`` slots a router fills to instantiate it.

This module holds only the record types -- pure stdlib dataclasses (no pydantic, no I/O),
mirroring :class:`pyfsr.authoring.CompiledPlaybook`'s style so ``archetypes`` stays core-pyfsr
with no new dependencies. Records are JSON-serializable so :class:`~pyfsr.archetypes.store.ArchetypeStore`
can persist them as a blob.

A record produced by the harvester (:mod:`pyfsr.archetypes.harvest`) is a **draft**:
``when_to_use`` is empty and ``parameters`` is unset -- an operator (or agent) curates those
during step 3. The harvested ``module_schema`` / ``connector_manifest`` / ``playbook_skeletons``
are honest extractions from a real solution pack, not guesses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ModuleField:
    """One field of an archetype's module schema, extracted from a pack's ``mmd.json``.

    ``picklist`` is the list name (e.g. ``"IncidentStatus"``) for picklist fields, resolved
    from ``dataSource.query.filters``; ``relationship`` is the related module (e.g. ``"alerts"``)
    from ``dataSource.model`` for relationship fields. Both are ``None`` for plain scalars.
    """

    module: str
    name: str
    type: str
    form_type: str | None = None
    required: bool = False
    display_name: str | None = None
    picklist: str | None = None
    relationship: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModuleField:
        return cls(
            module=d["module"],
            name=d["name"],
            type=d["type"],
            form_type=d.get("form_type"),
            required=bool(d.get("required", False)),
            display_name=d.get("display_name"),
            picklist=d.get("picklist"),
            relationship=d.get("relationship"),
        )


@dataclass
class ConnectorUse:
    """A connector + operation the archetype's playbook skeleton invokes.

    ``step_name`` is the harvesting step's human label (the step's ``name``), kept for
    traceability. ``role`` is the use-case role this connector plays in a *curated* archetype
    (``source_a`` / ``source_b`` / ``notify`` / ...) -- empty on a harvested draft (the harvester
    sees connector+operation pairs, not their semantic role) and assigned during curation (step 3).
    """

    connector: str
    operation: str
    step_name: str
    role: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConnectorUse:
        return cls(
            connector=d["connector"],
            operation=d["operation"],
            step_name=d["step_name"],
            role=d.get("role"),
        )


@dataclass
class StepSkeleton:
    """One step of a harvested playbook skeleton.

    ``step_type`` is the ``stepType`` UUID tail (kept for traceability only -- the harvester
    does not resolve it to a step-type name, since that mapping lives in the optional
    ``fsr_playbooks`` extra). ``connector``/``operation`` are present for connector steps.
    """

    name: str
    step_type: str | None = None
    connector: str | None = None
    operation: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StepSkeleton:
        return cls(
            name=d["name"],
            step_type=d.get("step_type"),
            connector=d.get("connector"),
            operation=d.get("operation"),
        )


@dataclass
class PlaybookSkeleton:
    """A generalized view of one harvested playbook (``playbooks/**/*.json``)."""

    name: str
    description: str | None = None
    steps: list[StepSkeleton] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlaybookSkeleton:
        return cls(
            name=d["name"],
            description=d.get("description"),
            steps=[StepSkeleton.from_dict(s) for s in d.get("steps", [])],
        )


@dataclass
class Archetype:
    """A use-case archetype record -- the unit the companion store persists.

    Args:
        name: the archetype's stable key (slug).
        when_to_use: one-line intent used by the router to classify a use case; empty on a
            harvested draft (curated in step 3).
        description: free-text description -- on harvest, the pack's ``info.json`` description.
        module_schema: the module fields the use case's target module needs.
        connector_manifest: the connector + operation pairs the skeleton uses (deduped).
        playbook_skeletons: one per harvested playbook.
        parameters: the ``{{param}}`` slots the router/agent fills; empty on a draft.
        source: provenance -- ``{pack_name, pack_version, pack_label, harvested_at}`` on harvest.
    """

    name: str
    when_to_use: str = ""
    description: str = ""
    module_schema: list[ModuleField] = field(default_factory=list)
    connector_manifest: list[ConnectorUse] = field(default_factory=list)
    playbook_skeletons: list[PlaybookSkeleton] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict (nested dataclasses expanded)."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to a JSON string (stable, human-readable)."""
        import json

        return json.dumps(asdict(self), indent=2, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Archetype:
        return cls(
            name=d["name"],
            when_to_use=d.get("when_to_use", ""),
            description=d.get("description", ""),
            module_schema=[ModuleField.from_dict(f) for f in d.get("module_schema", [])],
            connector_manifest=[ConnectorUse.from_dict(c) for c in d.get("connector_manifest", [])],
            playbook_skeletons=[PlaybookSkeleton.from_dict(p) for p in d.get("playbook_skeletons", [])],
            parameters=list(d.get("parameters", [])),
            source=dict(d.get("source", {})),
        )

    @classmethod
    def from_json(cls, s: str) -> Archetype:
        """Deserialize from a JSON string produced by :meth:`to_json`."""
        import json

        return cls.from_dict(json.loads(s))
