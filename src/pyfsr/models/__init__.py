"""Typed Pydantic models for FortiSOAR records and API result shapes.

``BaseRecord`` is the dict-compatible base for entity records; concrete models
live in :mod:`pyfsr.models._generated`.  ``ApiResult`` is the lighter base for
typed integration API response shapes (connector configs, job statuses, etc.)
defined in :mod:`pyfsr.models._integration`.

``MODEL_REGISTRY`` maps a module (collection) name to its model so
:class:`~pyfsr.records.RecordSet` can parse responses into the right type,
falling back to ``BaseRecord`` for modules without a curated model.
"""

from __future__ import annotations

from ._agents import Agent, AgentConnectorStatus
from ._app_config import NavItem, NavRequire, NavState
from ._generated import Alert, Comment, Incident, Task
from ._integration import (
    ApiResult,
    ConfigValidationError,
    ConfigValidationResult,
    ConnectorConfig,
    ConnectorConfigSummary,
    ConnectorDefinition,
    EnsureVersionResult,
    ExecuteResult,
    ExportJobResult,
    HealthcheckResult,
    ImportJobResult,
    InstalledConnector,
    InstallJobStatus,
    IntegrationListEnvelope,
    LogMessage,
    Operation,
    OperationParam,
)
from ._modules_admin import (
    AttributeBulkAction,
    AttributeMetadata,
    AttributeValidation,
    DefaultSortEntry,
    InvalidDraft,
    ModuleDescriptions,
    ModuleMetadata,
    PendingChange,
    PublishedModelMetadata,
    StagingModelMetadata,
)
from ._playbooks import (
    ApprovalRequest,
    CreatePlaybookRequest,
    ResumeRequest,
    RunEnv,
    RunFailure,
    RunStep,
    RunSummary,
    TriggerActionRequest,
    TriggerRequest,
    TriggerResponse,
)
from ._system import (
    AggregateRow,
    ApiKey,
    ApiKeyMaterial,
    ApiKeyUser,
    Appliance,
    Attachment,
    ConnectorOperation,
    ConnectorVersionInfo,
    ContentHubConnector,
    ContentHubItem,
    ExportConnectorRef,
    ExportOptions,
    ExportTemplate,
    FeaturedTag,
    FileRecord,
    ImportJob,
    ModulePermission,
    PicklistItem,
    Role,
    SolutionPack,
    SolutionPackInstallResponse,
    Team,
    User,
    Widget,
    Workflow,
    WorkflowCollection,
    WorkflowRun,
)
from .base import BaseRecord
from .types import PicklistIRI, RecordIRI

# Module (collection) name → model. Keys are the FortiSOAR plural module slugs
# used in ``/api/3/<module>`` paths. The workflow entries are stable,
# platform-owned schemas (see ``_system`` and the SDK roadmap §7).
MODEL_REGISTRY: dict[str, type[BaseRecord]] = {
    "alerts": Alert,
    "incidents": Incident,
    "tasks": Task,
    "comments": Comment,
    "workflows": Workflow,
    "workflow_collections": WorkflowCollection,
    "files": FileRecord,
    "people": User,
    "teams": Team,
    "roles": Role,
    "api_keys": ApiKey,
}


def model_for(module: str) -> type[BaseRecord]:
    """Return the registered model for ``module``, or ``BaseRecord`` if none."""
    return MODEL_REGISTRY.get(module, BaseRecord)


__all__ = [
    # base classes
    "BaseRecord",
    "ApiResult",
    # IRI NewTypes
    "PicklistIRI",
    "RecordIRI",
    # agent records
    "Agent",
    "AgentConnectorStatus",
    # integration API result shapes
    "InstalledConnector",
    "ConnectorConfigSummary",
    "ConnectorConfig",
    "ConnectorDefinition",
    "Operation",
    "OperationParam",
    "ConfigValidationResult",
    "ConfigValidationError",
    "HealthcheckResult",
    "ExecuteResult",
    "IntegrationListEnvelope",
    "InstallJobStatus",
    "EnsureVersionResult",
    "ImportJobResult",
    "ExportJobResult",
    "LogMessage",
    # entity records
    "Appliance",
    "Alert",
    "Incident",
    "Task",
    "Comment",
    "Workflow",
    "WorkflowCollection",
    "WorkflowRun",
    # playbook-run output shapes
    "RunSummary",
    "RunStep",
    "RunEnv",
    "RunFailure",
    "TriggerResponse",
    # playbook write-request bodies
    "TriggerRequest",
    "TriggerActionRequest",
    "ResumeRequest",
    "ApprovalRequest",
    "CreatePlaybookRequest",
    "FileRecord",
    "Attachment",
    "ExportTemplate",
    "ExportOptions",
    "ExportConnectorRef",
    "User",
    "Team",
    "Role",
    "ModulePermission",
    "ApiKey",
    "ApiKeyMaterial",
    "ApiKeyUser",
    "ContentHubItem",
    "FeaturedTag",
    "SolutionPack",
    "SolutionPackInstallResponse",
    "ImportJob",
    "ContentHubConnector",
    "Widget",
    "ConnectorOperation",
    "ConnectorVersionInfo",
    "PicklistItem",
    "AggregateRow",
    "MODEL_REGISTRY",
    "model_for",
    # module admin
    "AttributeValidation",
    "AttributeBulkAction",
    "AttributeMetadata",
    "DefaultSortEntry",
    "ModuleDescriptions",
    "ModuleMetadata",
    "StagingModelMetadata",
    "PublishedModelMetadata",
    "PendingChange",
    "InvalidDraft",
    # application navigation
    "NavItem",
    "NavRequire",
    "NavState",
]
