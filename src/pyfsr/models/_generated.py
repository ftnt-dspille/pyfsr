"""Typed FortiSOAR entity models — GENERATED, do not edit by hand.

Regenerate with ``python scripts/gen_models.py``. Field sets come from the
curated FortiSOAR OpenAPI spec; unknown fields are still preserved at runtime
via ``BaseRecord``'s ``extra='allow'``.
"""

from __future__ import annotations

from typing import Any

from .base import BaseRecord


class Alert(BaseRecord):
    """An Alert record. Field set is illustrative - 127 properties exist on this entity per the
    Hydra walk; the ones below are the most-used. Full list via GET /api/3/contexts/Alert.
    """

    name: str | None = None
    sourceId: str | None = None
    source: str | None = None
    description: str | None = None
    type: str | None = None  # picklist IRI
    severity: str | None = None  # picklist IRI
    status: str | None = None  # picklist IRI
    assignedTo: Any | None = None
    dueDate: int | None = None
    createUser: str | dict[str, Any] | None = None
    modifyUser: str | None = None
    createDate: float | None = None
    modifyDate: float | None = None
    id: int | None = None


class Incident(BaseRecord):
    """An Incident record. Field set derived from `GET /api/3/model_metadatas?$relationships=true`.
    Unique constraints: `[{'incidents_unique': {'columns': ['sourceId', 'tenant']}}]`. Flags:
    taggable, queueable.
    """

    responseSLAResumeDate: float | None = None
    mitretechniques: list[Any] | None = None
    mitresubtechniques: list[Any] | None = None
    mitremitigations: list[Any] | None = None
    mitregroups: list[Any] | None = None
    mitretactics: list[Any] | None = None
    mitresoftware: list[Any] | None = None
    state: str | None = None  # picklist IRI
    escalated: str | None = None  # picklist IRI (not a bool — uses Yes/No picklist)
    ticketID: str | None = None
    impactROI: int | None = None
    wasPersonalDataAffected: str | None = None  # picklist IRI
    warrooms: list[Any] | None = None
    incRemainingRespSLA: int | None = None
    incRemainingAckSLA: int | None = None
    respSLApausedon: float | None = None
    ackSLApausedon: float | None = None
    volatileData: str | None = None
    businessImpact: str | None = None
    comments: list[Any] | None = None
    companies: list[Any] | None = None
    confirmationDate: float | None = None
    senderEmailAddress: str | None = None
    eradicationDate: float | None = None
    filehash: str | None = None
    identificationDate: float | None = None
    impactAssessments: str | None = None
    incidentLead: Any | None = None  # entity relationship (Person)
    incidentsummary: str | None = None
    indicators: list[Any] | None = None
    metrics: str | None = None
    nextsteps: str | None = None
    persons: list[Any] | None = None
    phase: str | None = None  # picklist IRI
    incidentphase: str | None = None
    recoveryDate: float | None = None
    resDate: float | None = None
    resDueBy: float | None = None
    receipientEmailAddress: str | None = None
    recoveryTime: int | None = None
    resolution: str | None = None
    resolveddate: float | None = None
    resSla: str | None = None  # picklist IRI
    resPercentSla: int | None = None
    senderDomain: str | None = None
    severity: str | None = None  # picklist IRI
    sourceId: str | None = None
    targetAsset: str | None = None
    tasks: list[Any] | None = None
    category: str | None = None  # picklist IRI
    ackDueDate: float | None = None
    responseDate: float | None = None
    otherLogs: str | None = None
    siemQuery: str | None = None
    fileName: str | None = None
    name: str | None = None
    alerts: list[Any] | None = None
    assets: list[Any] | None = None
    campaigns: list[Any] | None = None
    communications: list[Any] | None = None
    mitreattackid: str | None = None
    c2server: str | None = None
    dLLName: str | None = None
    processName: str | None = None
    affectedUser: str | None = None
    affectedHost: str | None = None
    pcapFile: str | None = None
    ackDate: float | None = None
    slaState: str | None = None  # picklist IRI
    slaPercentage: int | None = None
    aftermathDate: float | None = None
    assigneddate: float | None = None
    attachments: list[Any] | None = None
    containmentDate: float | None = None
    containmentTime: int | None = None
    dateOfIncident: float | None = None
    deliveryVector: str | None = None  # picklist IRI
    description: str | None = None
    destinationIP: str | None = None
    deviceUID: str | None = None
    discoveredOn: float | None = None
    dwellTime: int | None = None
    source: str | None = None
    sourcedata: str | None = None
    sourceIP: str | None = None
    cVEs: list[Any] | None = None
    status: str | None = None  # picklist IRI
    vulnerabilities: list[Any] | None = None


class Task(BaseRecord):
    """A Task record. Field set derived from `GET /api/3/model_metadatas?$relationships=true`.
    Flags: ownable, taggable, queueable.
    """

    submittedBy: Any | None = None  # entity relationship (Person)
    name: str | None = None
    description: str | None = None
    type: str | None = None  # picklist IRI
    dueBy: float | None = None
    assignedOnDate: float | None = None
    startDate: float | None = None
    completedOnDate: float | None = None
    actualMinutes: int | None = None
    priority: str | None = None  # picklist IRI
    status: str | None = None  # picklist IRI
    assignedToPerson: Any | None = None  # entity relationship (Person)
    companies: list[Any] | None = None
    persons: list[Any] | None = None
    alerts: list[Any] | None = None
    attachments: list[Any] | None = None
    assets: list[Any] | None = None
    comments: list[Any] | None = None
    incidents: list[Any] | None = None
    indicators: list[Any] | None = None
    warrooms: list[Any] | None = None
    approvalhost: str | None = None
    cVEs: list[Any] | None = None
    vulnerabilities: list[Any] | None = None
    workflowid: str | None = None
    taskdata: str | None = None
    tasktype: str | None = None
    hunt: list[Any] | None = None
    stepid: int | None = None
    threatIntelFeeds: list[Any] | None = None
    workspaces: list[Any] | None = None


class Comment(BaseRecord):
    """A Comment record. Field set derived from `GET /api/3/model_metadatas?$relationships=true`.
    Flags: ownable, taggable.
    """

    attachments: list[Any] | None = None
    approvals: list[Any] | None = None
    content: str | None = None
    tasks: list[Any] | None = None
    people: list[Any] | None = None
    type: str | None = None  # picklist IRI
    alerts: list[Any] | None = None
    isDeleted: str | None = None
    assets: list[Any] | None = None
    file: str | None = None
    file1: str | None = None
    campaigns: list[Any] | None = None
    file2: str | None = None
    rawCommentData: str | None = None
    communication: list[Any] | None = None
    file3: str | None = None
    events: list[Any] | None = None
    file4: str | None = None
    incidents: list[Any] | None = None
    isImportant: bool | None = None
    indicators: list[Any] | None = None
    peopleUpdated: bool | None = None
    replyTo: Any | None = None
    warrooms: list[Any] | None = None
    devices: list[Any] | None = None
    replies: list[Any] | None = None
    lastReplyDate: float | None = None
    managers: list[Any] | None = None
    scenario: list[Any] | None = None
    cVEs: list[Any] | None = None
    scans: list[Any] | None = None
    vulnerabilities: list[Any] | None = None
    hunt: list[Any] | None = None
    threatActors: list[Any] | None = None
    threatIntelReports: list[Any] | None = None
    workspaces: list[Any] | None = None
