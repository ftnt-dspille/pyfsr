"""Typed models for FortiSOAR module-admin API response shapes.

Covers ``/api/3/staging_model_metadatas``, ``/api/3/model_metadatas``, and
``/api/3/attribute_metadatas`` — the three endpoints driven by
:class:`~pyfsr.api.modules_admin.ModulesAdminAPI`.

Shapes validated against a live 7.6.5 appliance.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import BaseRecord
from .types import RecordIRI

# ---------------------------------------------------------------------------
# Attribute (field) models
# ---------------------------------------------------------------------------


class AttributeValidation(BaseRecord):
    """``validation`` sub-object on an attribute."""

    required: bool = False
    minlength: int | None = None
    maxlength: int | None = None
    enable_range: bool | None = Field(default=None, alias="_enableRange")


class AttributeBulkAction(BaseRecord):
    """``bulkAction`` sub-object on an attribute."""

    allow: bool = False
    button_text: str = Field(default="", alias="buttonText")
    button_icon: str = Field(default="", alias="buttonIcon")
    button_class: str = Field(default="", alias="buttonClass")


class AttributeMetadata(BaseRecord):
    """A single field definition on a staging or published module.

    ``type`` is the Postgres storage type (``"string"``, ``"integer"``,
    ``"boolean"``, ``"object"``, ``"picklists"``, or a module name like
    ``"alerts"`` for relationships).  ``form_type`` is the UI widget.

    The ``sattrib`` field is the IRI of the parent
    :class:`StagingModelMetadata` (or its dict form when the attribute is
    fetched via the staging module endpoint).
    """

    id_iri: str | None = Field(default=None, alias="@id")
    record_type: str | None = Field(default=None, alias="@type")
    uuid: str | None = None

    # Core identity
    name: str | None = None
    type: str | None = None
    form_type: str | None = Field(default=None, alias="formType")

    # Structural flags
    length: int | None = None
    order_index: int | None = Field(default=None, alias="orderIndex")
    collection: bool = False
    system: bool = False
    encrypted: bool = False
    searchable: bool = False
    peer_replicable: bool = Field(default=True, alias="peerReplicable")
    grid_column: bool = Field(default=False, alias="gridColumn")
    skip_serialization: bool = Field(default=False, alias="skipSerialization")
    html_escape: bool = Field(default=False, alias="htmlEscape")
    visibility: bool = True
    readable: bool = True
    writeable: bool = True
    unique: bool = False
    recommend: bool = False
    identifier: bool | None = None
    orphan_removal: bool | None = Field(default=None, alias="orphanRemoval")
    owns_relationship: bool | None = Field(default=None, alias="ownsRelationship")
    inverted_field: str | None = Field(default=None, alias="inversedField")

    # Data source (picklist / relationship / lookup)
    data_source: dict[str, Any] | list | None = Field(default=None, alias="dataSource")
    data_source_filters: dict[str, Any] | list | None = Field(default=None, alias="dataSourceFilters")

    # Validation / bulk action
    validation: AttributeValidation | dict[str, Any] | None = None
    bulk_action: AttributeBulkAction | dict[str, Any] | None = Field(default=None, alias="bulkAction")

    # Misc
    default_value: Any = Field(default=None, alias="defaultValue")
    tooltip: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    descriptions: dict[str, str] | None = None
    imported_by: list[Any] = Field(default_factory=list, alias="importedBy")

    # Parent staging module — IRI string or condensed object
    sattrib: RecordIRI | dict[str, Any] | None = None

    @property
    def label(self) -> str | None:
        """Human-readable label from ``descriptions.singular`` or ``displayName``."""
        if self.descriptions:
            return self.descriptions.get("singular") or self.display_name
        return self.display_name

    @property
    def is_relationship(self) -> bool:
        """True when the field is a relationship (lookup / manyToMany / oneToMany)."""
        return self.form_type in ("lookup", "manyToMany", "oneToMany")

    @property
    def is_picklist(self) -> bool:
        """True when the field is a picklist or multiselect picklist."""
        return self.form_type in ("picklist", "multiselectpicklist")


# ---------------------------------------------------------------------------
# Module (staging / published) models
# ---------------------------------------------------------------------------


class DefaultSortEntry(BaseRecord):
    """One entry in ``defaultSort`` on a module metadata record."""

    field: str | None = None
    direction: str | None = None


class ModuleDescriptions(BaseRecord):
    """``descriptions`` sub-object on a module metadata record."""

    singular: str | None = None
    plural: str | None = None


class ModuleMetadata(BaseRecord):
    """Shared shape for both staging (``StagingModelMetadata``) and published
    (``ModelMetadata``) module records.

    ``attributes`` is only populated when the record is fetched individually
    (``GET /api/3/staging_model_metadatas/{uuid}``), not in list responses.
    """

    id_iri: str | None = Field(default=None, alias="@id")
    record_type: str | None = Field(default=None, alias="@type")
    uuid: str | None = None

    # Identity
    type: str | None = None
    module: str | None = None
    table_name: str | None = Field(default=None, alias="tableName")
    parent_type: str | None = Field(default=None, alias="parentType")

    # Behaviour flags
    ownable: bool = False
    user_ownable: bool = Field(default=False, alias="userOwnable")
    queueable: bool = False
    trackable: bool = False
    taggable: bool = False
    peer_replicable: bool = Field(default=True, alias="peerReplicable")
    indexable: bool = True
    writable: bool = True
    system: bool = False
    soft_deleteable: bool = Field(default=False, alias="softDeleteable")
    archivable: bool = False
    paused: bool = False
    enable_replication: bool = Field(default=True, alias="enableReplication")

    # Partitioning / archival
    partition_by: str | None = Field(default=None, alias="partitionBy")
    archival_criteria: dict[str, Any] | None = Field(default=None, alias="archivalCriteria")
    archival_filters: list[Any] = Field(default_factory=list, alias="archivalFilters")
    replication_filters: list[Any] = Field(default_factory=list, alias="replicationFilters")

    # Schema
    default_sort: list[DefaultSortEntry | dict[str, Any]] = Field(default_factory=list, alias="defaultSort")
    unique_constraint: list[dict[str, Any]] = Field(default_factory=list, alias="uniqueConstraint")

    # Display
    display_name: str | None = Field(default=None, alias="displayName")
    descriptions: ModuleDescriptions | dict[str, str] | None = None

    # Attributes — populated only on single-record fetch
    attributes: list[AttributeMetadata | dict[str, Any]] = Field(default_factory=list)

    imported_by: list[Any] = Field(default_factory=list, alias="importedBy")

    @property
    def label(self) -> str | None:
        """Human-readable singular label from ``descriptions``."""
        if isinstance(self.descriptions, ModuleDescriptions):
            return self.descriptions.singular
        if isinstance(self.descriptions, dict):
            return self.descriptions.get("singular")
        return None

    @property
    def plural_label(self) -> str | None:
        """Human-readable plural label from ``descriptions``."""
        if isinstance(self.descriptions, ModuleDescriptions):
            return self.descriptions.plural
        if isinstance(self.descriptions, dict):
            return self.descriptions.get("plural")
        return None

    def get_attribute(self, name: str) -> AttributeMetadata | dict[str, Any] | None:
        """Return the attribute with ``name``, or ``None``."""
        for attr in self.attributes:
            attr_name = attr.name if isinstance(attr, AttributeMetadata) else attr.get("name")
            if attr_name == name:
                return attr
        return None


class StagingModelMetadata(ModuleMetadata):
    """A staging module record from ``/api/3/staging_model_metadatas``."""


class PublishedModelMetadata(ModuleMetadata):
    """A published module record from ``/api/3/model_metadatas``."""


__all__ = [
    "AttributeValidation",
    "AttributeBulkAction",
    "AttributeMetadata",
    "DefaultSortEntry",
    "ModuleDescriptions",
    "ModuleMetadata",
    "StagingModelMetadata",
    "PublishedModelMetadata",
]
