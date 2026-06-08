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
    type: str | None = None
    severity: str | None = None
    status: str | None = None
    assignedTo: str | None = None
    dueDate: int | None = None
    createUser: Any | None = None
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
    mitretechniques: list[str] | None = None
    mitresubtechniques: list[str] | None = None
    mitremitigations: list[str] | None = None
    mitregroups: list[str] | None = None
    mitretactics: list[str] | None = None
    mitresoftware: list[str] | None = None
    state: str | None = None
    escalated: str | None = None
    ticketID: str | None = None
    impactROI: int | None = None
    wasPersonalDataAffected: str | None = None
    warrooms: list[str] | None = None
    incRemainingRespSLA: int | None = None
    incRemainingAckSLA: int | None = None
    respSLApausedon: float | None = None
    ackSLApausedon: float | None = None
    volatileData: str | None = None
    businessImpact: str | None = None
    comments: list[str] | None = None
    companies: list[str] | None = None
    confirmationDate: float | None = None
    senderEmailAddress: str | None = None
    eradicationDate: float | None = None
    filehash: str | None = None
    identificationDate: float | None = None
    impactAssessments: str | None = None
    incidentLead: str | None = None
    incidentsummary: str | None = None
    indicators: list[str] | None = None
    metrics: str | None = None
    nextsteps: str | None = None
    persons: list[str] | None = None
    phase: str | None = None
    incidentphase: str | None = None
    recoveryDate: float | None = None
    resDate: float | None = None
    resDueBy: float | None = None
    receipientEmailAddress: str | None = None
    recoveryTime: int | None = None
    resolution: str | None = None
    resolveddate: float | None = None
    resSla: str | None = None
    resPercentSla: int | None = None
    senderDomain: str | None = None
    severity: str | None = None
    sourceId: str | None = None
    targetAsset: str | None = None
    tasks: list[str] | None = None
    category: str | None = None
    ackDueDate: float | None = None
    responseDate: float | None = None
    otherLogs: str | None = None
    siemQuery: str | None = None
    fileName: str | None = None
    name: str | None = None
    alerts: list[str] | None = None
    assets: list[str] | None = None
    campaigns: list[str] | None = None
    communications: list[str] | None = None
    mitreattackid: str | None = None
    c2server: str | None = None
    dLLName: str | None = None
    processName: str | None = None
    affectedUser: str | None = None
    affectedHost: str | None = None
    pcapFile: str | None = None
    ackDate: float | None = None
    slaState: str | None = None
    slaPercentage: int | None = None
    aftermathDate: float | None = None
    assigneddate: float | None = None
    attachments: list[str] | None = None
    containmentDate: float | None = None
    containmentTime: int | None = None
    dateOfIncident: float | None = None
    deliveryVector: str | None = None
    description: str | None = None
    destinationIP: str | None = None
    deviceUID: str | None = None
    discoveredOn: float | None = None
    dwellTime: int | None = None
    source: str | None = None
    sourcedata: str | None = None
    sourceIP: str | None = None
    cVEs: list[str] | None = None
    status: str | None = None
    vulnerabilities: list[str] | None = None


class Task(BaseRecord):
    """A Task record. Field set derived from `GET /api/3/model_metadatas?$relationships=true`.
    Flags: ownable, taggable, queueable.
    """

    submittedBy: str | None = None
    name: str | None = None
    description: str | None = None
    type: str | None = None
    dueBy: float | None = None
    assignedOnDate: float | None = None
    startDate: float | None = None
    completedOnDate: float | None = None
    actualMinutes: int | None = None
    priority: str | None = None
    status: str | None = None
    assignedToPerson: str | None = None
    companies: list[str] | None = None
    persons: list[str] | None = None
    alerts: list[str] | None = None
    attachments: list[str] | None = None
    assets: list[str] | None = None
    comments: list[str] | None = None
    incidents: list[str] | None = None
    indicators: list[str] | None = None
    warrooms: list[str] | None = None
    approvalhost: str | None = None
    cVEs: list[str] | None = None
    vulnerabilities: list[str] | None = None
    workflowid: str | None = None
    taskdata: str | None = None
    tasktype: str | None = None
    hunt: list[str] | None = None
    stepid: int | None = None
    threatIntelFeeds: list[str] | None = None
    workspaces: list[str] | None = None


class Comment(BaseRecord):
    """A Comment record. Field set derived from `GET /api/3/model_metadatas?$relationships=true`.
    Flags: ownable, taggable.
    """

    attachments: list[str] | None = None
    approvals: list[str] | None = None
    content: str | None = None
    tasks: list[str] | None = None
    people: list[str] | None = None
    type: str | None = None
    alerts: list[str] | None = None
    isDeleted: str | None = None
    assets: list[str] | None = None
    file: str | None = None
    file1: str | None = None
    campaigns: list[str] | None = None
    file2: str | None = None
    rawCommentData: str | None = None
    communication: list[str] | None = None
    file3: str | None = None
    events: list[str] | None = None
    file4: str | None = None
    incidents: list[str] | None = None
    isImportant: bool | None = None
    indicators: list[str] | None = None
    peopleUpdated: bool | None = None
    replyTo: str | None = None
    warrooms: list[str] | None = None
    devices: list[str] | None = None
    replies: list[str] | None = None
    lastReplyDate: float | None = None
    managers: list[str] | None = None
    scenario: list[str] | None = None
    cVEs: list[str] | None = None
    scans: list[str] | None = None
    vulnerabilities: list[str] | None = None
    hunt: list[str] | None = None
    threatActors: list[str] | None = None
    threatIntelReports: list[str] | None = None
    workspaces: list[str] | None = None
