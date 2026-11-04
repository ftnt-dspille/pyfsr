"""Typed models for STIX 2.1 objects served by FortiSOAR's TAXII server.

FortiSOAR serves STIX 2.1 objects from its threat-intel datasets via the TAXII
endpoints (``/api/taxii/1/collections/<id>/objects``). Objects carry a scalar
``value`` field (the indicator value -- an IP, hash, domain, etc.) and
``pattern`` is typically null, so consumers do not need a STIX pattern parser
to extract actionable indicators.

These models are lenient (``extra="allow"``) and dict-compatible (``.get()``,
``obj["key"]``, ``"key" in obj``), matching the :class:`~pyfsr.models.BaseRecord`
pattern. Unknown STIX extensions and vendor-specific fields are preserved.

Two serving shapes exist and both parse into the same models:

* **Unpatched appliance / FortiSOC cloud** -- the objects are the raw
  ``threat_intel_feeds`` module records: scalar ``value``, ``pattern`` set when
  the record carries one (the block-list records do), a **bare-uuid ``id``**
  (no ``indicator--`` prefix) and camelCase STIX keys (``specVersion``,
  ``validFrom``). The camelCase forms are copied into the snake_case typed
  fields; the originals stay reachable as extras / raw dict keys.
* **Patched appliance** (the appliance-only TAXII 2.1 patch) -- proper STIX 2.1
  objects: ``indicator--``-prefixed ids and snake_case keys, with a
  ``?stix=2.1`` query switching the response to a bundle.

Shapes are live-verified on 8.0.0; the STIX 2.1 spec fields are curated for the
SDO types FortiSOAR ingests (indicator, malware, threat-actor, campaign,
attack-pattern, intrusion-set, tool, vulnerability, report).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MISSING = object()


class _Lenient(BaseModel):
    """Dict-compatible base for STIX/TAXII response shapes.

    Not module records (no ``@id``), but still dict-compatible so existing
    ``.get(...)``-style call sites keep working alongside attribute access.
    Raw server keys resolve even when a typed field consumed them through a
    pydantic alias (FortiSOAR's ``{totalItems, objects}`` envelope fills the
    ``total_items`` field): the dict-compat promise is against the *server's*
    keys, so ``.get("totalItems")`` must return the served value, not the
    default.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def _field_for_alias(self, key: str) -> str | None:
        """The field name populated from the raw key ``key`` via an alias."""
        for name, field in type(self).model_fields.items():
            if field.alias == key:
                return name
        return None

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        name = self._field_for_alias(key)
        if name is not None:
            return getattr(self, name)
        if self.model_extra and key in self.model_extra:
            return self.model_extra[key]
        return default

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return self.get(key, _MISSING) is not _MISSING


# FortiSOAR's unpatched TAXII endpoint (and FortiSOC cloud) serves the raw
# threat_intel_feeds module records, whose STIX properties are camelCase
# ("specVersion", "validFrom"); a patched appliance serves proper snake_case
# STIX. The camelCase forms are copied into the typed snake_case fields --
# never the other way around, and the originals are kept (extras / raw keys).
_CAMEL_TO_STIX_FIELD = {
    "specVersion": "spec_version",
    "patternType": "pattern_type",
    "validFrom": "valid_from",
    "validUntil": "valid_until",
    "killChainPhases": "kill_chain_phases",
    "malwareTypes": "malware_types",
    "threatActorTypes": "threat_actor_types",
    "toolTypes": "tool_types",
    "reportTypes": "report_types",
    "objectRefs": "object_refs",
    "externalReferences": "external_references",
    "firstSeen": "first_seen",
    "lastSeen": "last_seen",
    "isFamily": "is_family",
    "resourceLevel": "resource_level",
    "primaryMotivation": "primary_motivation",
}


class StixObject(_Lenient):
    """Base for every STIX 2.1 object (SDO, SCO, SRO).

    Carries the common STIX 2.1 properties. FortiSOAR's TAXII server also stamps
    a scalar ``value`` (the indicator value), so both it and ``pattern`` are
    exposed here rather than only on :class:`StixIndicator`.

    Attributes:
        type: STIX object type (``"indicator"``, ``"malware"``, ``"threat-actor"``, ...).
        id: STIX identifier (``"<type>--<uuid>"``). The unpatched endpoint (and
            FortiSOC cloud) serves the **bare record uuid** without the prefix;
            a patched appliance serves the proper prefixed form.
        spec_version: STIX spec version (``"2.1"``).
        created: creation timestamp (ISO 8601).
        modified: last-modified timestamp (ISO 8601).
        name: human-readable name.
        description: longer description.
        value: FortiSOAR's scalar indicator value (an IP, hash, domain, etc.).
            Present on objects served from the TAXII endpoint; null on standard
            STIX SDOs that use ``pattern`` instead.
        pattern: STIX pattern expression. Set when the record carries one
            (FortiSOC block-list records and FortiGuard-served objects do);
            null on records created without one (``value`` still carries the
            indicator).
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

    @model_validator(mode="before")
    @classmethod
    def _accept_camel_case_keys(cls, data: Any) -> Any:
        """Copy FortiSOAR's camelCase STIX keys into the snake_case fields.

        The unpatched endpoint serves module records (``specVersion``,
        ``validFrom``, ...); without this the typed fields silently read None
        while the data sits in extras. Snake_case (a patched appliance) always
        wins when both are present.
        """
        if isinstance(data, dict):
            for camel, snake in _CAMEL_TO_STIX_FIELD.items():
                if camel in data and data.get(snake) is None:
                    data[snake] = data[camel]
        return data


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
