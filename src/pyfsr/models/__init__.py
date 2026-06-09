"""Typed Pydantic models for FortiSOAR records.

``BaseRecord`` is the dict-compatible base; the concrete entity models live in
the generated :mod:`pyfsr.models._generated` module. ``MODEL_REGISTRY`` maps a
module (collection) name to its model so :class:`~pyfsr.records.RecordSet` can
parse responses into the right type, falling back to ``BaseRecord`` for modules
without a curated model.
"""

from __future__ import annotations

from ._generated import Alert, Comment, Incident, Task
from ._system import (
    ContentHubConnector,
    ContentHubItem,
    SolutionPack,
    Widget,
    Workflow,
    WorkflowCollection,
    WorkflowRun,
)
from .base import BaseRecord

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
}


def model_for(module: str) -> type[BaseRecord]:
    """Return the registered model for ``module``, or ``BaseRecord`` if none."""
    return MODEL_REGISTRY.get(module, BaseRecord)


__all__ = [
    "BaseRecord",
    "Alert",
    "Incident",
    "Task",
    "Comment",
    "Workflow",
    "WorkflowCollection",
    "WorkflowRun",
    "ContentHubItem",
    "SolutionPack",
    "ContentHubConnector",
    "Widget",
    "MODEL_REGISTRY",
    "model_for",
]
