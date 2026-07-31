"""Typed models for STIX 2.1 objects served by FortiSOAR's TAXII server.

FortiSOAR serves STIX 2.1 objects from its threat-intel datasets via the TAXII
endpoints (``/api/taxii/1/collections/<id>/objects``). Objects carry a scalar
``value`` field (the indicator value -- an IP, hash, domain, etc.) and
``pattern`` is typically null, so consumers do not need a STIX pattern parser
to extract actionable indicators.

These models are lenient (``extra="allow"``) and dict-compatible (``.get()``,
``obj["key"]``, ``"key" in obj``), matching the :class:`~pyfsr.models.BaseRecord`
pattern. Unknown STIX extensions and vendor-specific fields are preserved.

Shapes are live-verified on 8.0.0; the STIX 2.1 spec fields are curated for the
SDO types FortiSOAR ingests (indicator, malware, threat-actor, campaign,
attack-pattern, intrusion-set, tool, vulnerability, report).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Lenient(BaseModel):
    """Dict-compatible base for STIX/TAXII response shapes.

    Not module records (no ``@id``), but still dict-compatible so existing
    ``.get(...)``-style call sites keep working alongside attribute access.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, self.model_extra.get(key, default) if self.model_extra else default)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        if self.model_extra and key in self.model_extra:
            return self.model_extra[key]
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return hasattr(self, key) or bool(self.model_extra and key in self.model_extra)


class StixObject(_Lenient):
    """Base for every STIX 2.1 object (SDO, SCO, SRO).

    Carries the common STIX 2.1 properties. FortiSOAR's TAXII server also stamps
    a scalar ``value`` (the indicator value) and leaves ``pattern`` null, so
    those are exposed here rather than only on :class:`StixIndicator`.

    Attributes:
        type: STIX object type (``"indicator"``, ``"malware"``, ``"threat-actor"``, ...).
        id: STIX identifier (``"<type>--<uuid>"``).
        spec_version: STIX spec version (``"2.1"``).
        created: creation timestamp (ISO 8601).
        modified: last-modified timestamp (ISO 8601).
        name: human-readable name.
        description: longer description.
        value: FortiSOAR's scalar indicator value (an IP, hash, domain, etc.).
            Present on objects served from the TAXII endpoint; null on standard
            STIX SDOs that use ``pattern`` instead.
        pattern: STIX pattern expression. Typically null on FortiSOAR-served
            objects (``value`` carries the indicator instead).
    """

    type: str
    id: str | None = None
    spec_version: str | None = None
    created: str | None = None
    modified: str | None = None
    name: str | None = None
    description: str | None = None
    value: str | None = None
    pattern: str | None = None


class StixIndicator(StixObject):
    """A STIX 2.1 Indicator SDO.

    Attributes:
        pattern_type: pattern language (``"stix"``, ``"pcre"``).
        valid_from: when the indicator is first considered valid.
        valid_until: when the indicator is no longer considered valid.
        labels: open-vocab labels (e.g. ``["malicious-activity"]``).
        kill_chain_phases: kill-chain phase entries.
    """

    pattern_type: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    labels: list[str] = Field(default_factory=list)
    kill_chain_phases: list[dict[str, Any]] = Field(default_factory=list)


class StixMalware(StixObject):
    """A STIX 2.1 Malware SDO.

    Attributes:
        is_family: whether this is a malware family (``True``) or instance.
        malware_types: open-vocab types (``["ransomware"``, ``["trojan"]``).
        kill_chain_phases: kill-chain phase entries.
    """

    is_family: bool | None = None
    malware_types: list[str] = Field(default_factory=list)
    kill_chain_phases: list[dict[str, Any]] = Field(default_factory=list)


class StixThreatActor(StixObject):
    """A STIX 2.1 Threat Actor SDO.

    Attributes:
        threat_actor_types: open-vocab types (``["nation-state"``, ...).
        aliases: alternative names.
        first_seen: when the actor was first observed.
        last_seen: when the actor was last observed.
        kill_chain_phases: kill-chain phase entries.
    """

    threat_actor_types: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    kill_chain_phases: list[dict[str, Any]] = Field(default_factory=list)


class StixCampaign(StixObject):
    """A STIX 2.1 Campaign SDO.

    Attributes:
        aliases: alternative names.
        first_seen: when the campaign was first observed.
        last_seen: when the campaign was last observed.
        objectives: campaign objectives.
    """

    aliases: list[str] = Field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    objectives: str | None = None


class StixAttackPattern(StixObject):
    """A STIX 2.1 Attack Pattern SDO.

    Attributes:
        kill_chain_phases: kill-chain phase entries.
        external_references: MITRE ATT&CK technique IDs, etc.
    """

    kill_chain_phases: list[dict[str, Any]] = Field(default_factory=list)
    external_references: list[dict[str, Any]] = Field(default_factory=list)


class StixIntrusionSet(StixObject):
    """A STIX 2.1 Intrusion Set SDO.

    Attributes:
        aliases: alternative names.
        first_seen: when the intrusion set was first observed.
        last_seen: when the intrusion set was last observed.
        goals: intrusion-set goals.
        resource_level: resource level (``"individual"``, ``"organization"``).
        primary_motivation: primary motivation (``"financial"``, ...).
    """

    aliases: list[str] = Field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    goals: list[str] = Field(default_factory=list)
    resource_level: str | None = None
    primary_motivation: str | None = None


class StixTool(StixObject):
    """A STIX 2.1 Tool SDO.

    Attributes:
        tool_types: open-vocab types (``["hacking"``, ...).
        kill_chain_phases: kill-chain phase entries.
    """

    tool_types: list[str] = Field(default_factory=list)
    kill_chain_phases: list[dict[str, Any]] = Field(default_factory=list)


class StixVulnerability(StixObject):
    """A STIX 2.1 Vulnerability SDO.

    Attributes:
        cve: CVE identifier, when available.
        external_references: CVE / NVD references.
    """

    cve: str | None = None
    external_references: list[dict[str, Any]] = Field(default_factory=list)


class StixReport(StixObject):
    """A STIX 2.1 Report SDO.

    Attributes:
        published: publication timestamp.
        report_types: open-vocab types (``["threat-report"``, ...).
        object_refs: STIX IDs of the objects this report references.
    """

    published: str | None = None
    report_types: list[str] = Field(default_factory=list)
    object_refs: list[str] = Field(default_factory=list)


class StixBundle(_Lenient):
    """A STIX 2.1 Bundle -- the top-level container for STIX objects.

    The object that FortiSOAR's ``POST /api/ingest-feeds/stix-bundle`` accepts
    and that the TAXII objects endpoint can return (wrapped in the
    :class:`~pyfsr.models.TaxiiObjectsEnvelope`).

    Attributes:
        type: always ``"bundle"``.
        id: bundle identifier (``"bundle--<uuid>"``).
        objects: the STIX objects inside, parsed into typed subclasses when
            possible (falls back to :class:`StixObject` for unknown types).
    """

    type: str = "bundle"
    id: str | None = None
    objects: list[StixObject] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _ensure_str(cls, data: Any) -> Any:
        if isinstance(data, dict):
            objs = data.get("objects")
            if isinstance(objs, list):
                data["objects"] = [parse_stix_object(o) if isinstance(o, dict) else o for o in objs]
        return data


_STIX_TYPE_REGISTRY: dict[str, type[StixObject]] = {
    "indicator": StixIndicator,
    "malware": StixMalware,
    "threat-actor": StixThreatActor,
    "campaign": StixCampaign,
    "attack-pattern": StixAttackPattern,
    "intrusion-set": StixIntrusionSet,
    "tool": StixTool,
    "vulnerability": StixVulnerability,
    "report": StixReport,
}


def parse_stix_object(data: dict[str, Any]) -> StixObject:
    """Parse a raw STIX object dict into the best-fit typed model.

    Dispatches on the ``type`` field. Unknown types fall back to the
    :class:`StixObject` base, preserving all fields (``extra="allow"``).

    Example:
        >>> obj = parse_stix_object({"type": "malware", "id": "malware--123", "name": "evil"})
        >>> isinstance(obj, StixMalware)
        True
        >>> obj.type
        'malware'
        >>> obj["name"]
        'evil'
    """
    stix_type = data.get("type", "")
    cls = _STIX_TYPE_REGISTRY.get(stix_type, StixObject)
    return cls.model_validate(data)


__all__ = [
    "StixObject",
    "StixIndicator",
    "StixMalware",
    "StixThreatActor",
    "StixCampaign",
    "StixAttackPattern",
    "StixIntrusionSet",
    "StixTool",
    "StixVulnerability",
    "StixReport",
    "StixBundle",
    "parse_stix_object",
]
