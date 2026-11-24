"""Rule-engine models — delivery rules, channels, and preprocessing rules.

FortiSOAR splits "rules" across **two different applications**, which is why these
models don't share a base:

- **Delivery rules** and **channels** live in the standalone *rule engine*, proxied at
  ``/rule/api/`` (or ``/api/rule/api/`` depending on the build). These are **not**
  crudhub records: they carry no JSON-LD envelope (no ``@id`` / ``@type``), use
  ``snake_case`` field names, and are keyed by a bare ``uuid``.
- **Preprocessing rules** live in crudhub at ``/api/3/preprocessing_rules`` and *are*
  ordinary records — JSON-LD envelope, ``camelCase`` fields.

All three field sets are transcribed from live 8.0.0 responses (per the
``types-from-live-wire`` doctrine), not from documentation.
"""

from __future__ import annotations

from typing import Any

from pydantic import field_validator

from .base import BaseRecord


class DeliveryRule(BaseRecord):
    """A **delivery rule** from the rule engine (``GET /rule/api/rules/``).

    The notification rules the SOAR UI lists under *Rules*: each pairs a
    ``trigger_condition`` (a crudhub-style filter over ``entity_type`` records) with
    ``actions[]`` that fire on match, every action naming the ``channel_uuid`` it
    delivers through (see :class:`RuleChannel`).

    Rule-engine objects carry no JSON-LD envelope, so :attr:`~pyfsr.models.base.BaseRecord.iri`
    is ``None`` here — ``uuid`` is the only identifier. ``is_system`` marks the
    rules FortiSOAR ships; those exist on every appliance and are the safest ones
    to reference in a portable export template.
    """

    uuid: str | None = None
    name: str | None = None
    entity_type: str | None = None
    event_type: str | None = None
    event_source: str | None = None
    trigger_condition: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None
    is_system: bool | None = None
    is_active: bool | None = None
    visible: bool | None = None
    priority: int | None = None
    category: str | None = None
    source: Any | None = None
    channel_preference_field: Any | None = None
    expiry: Any | None = None
    entity_id: Any | None = None
    parent_rule: Any | None = None
    workflow: Any | None = None


class RuleChannel(BaseRecord):
    """A **rule channel** from the rule engine (``GET /rule/api/channel/``).

    The delivery transport a :class:`DeliveryRule` action targets by ``channel_uuid``
    — e.g. *In-App Notifications*, email. ``type`` is ``"system"`` for the built-in
    channels. Like :class:`DeliveryRule`, this is not a crudhub record: no JSON-LD
    envelope, ``uuid`` is the identifier.

    The registry also drives **manual-input delivery** (live-verified 8.0.1):
    a Manual Input step's ``inline_channel_list``/``external_channel_list`` name a
    channel from this table, and the workflow engine dispatches the prompt through
    that channel's connector operation -- ``Microsoft Teams`` →
    ``microsoft-teams.send_message_or_approval_form_to_bot``, ``Slack`` →
    ``slack.send_input``, ``Email`` → ``smtp.send_email`` (``type="connector"`` /
    ``"email"``). The Teams/Slack rows are registered when those connectors
    install, so they are absent from the shipped ``default_channels.json`` fixture
    and carry no bound connector-config uuid.

    ``config`` is the dispatch mapping (``connector`` / ``operation`` /
    optional bound ``config`` uuid) for connector/email channels, ``{}`` for
    system channels.
    """

    uuid: str | None = None
    name: str | None = None
    type: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None
    default_params: dict[str, Any] | list[Any] | None = None
    """Live shapes on 8.0.1: ``{}`` for the built-in fixture channels, a bare
    empty list ``[]`` for connector-registered channels (Teams/Slack ship
    ``default_params: []``). Normalized to ``{}`` below so callers can always
    ``.get()``; a non-empty list (never observed) is passed through untouched
    rather than discarded."""

    @field_validator("default_params", mode="before")
    @classmethod
    def _empty_list_default_params(cls, v: Any) -> Any:
        if isinstance(v, list) and not v:
            return {}
        return v


class PreprocessingRule(BaseRecord):
    """A **preprocessing rule** (``GET /api/3/preprocessing_rules``).

    Rules that run against records as they arrive (``applicableOn: "incoming"``) to
    dedupe, link, or update them before playbooks fire. Unlike :class:`DeliveryRule`
    and :class:`RuleChannel` this *is* a crudhub record — JSON-LD envelope and
    ``camelCase`` fields — so :attr:`~pyfsr.models.base.BaseRecord.iri` is populated.

    ``criteria`` holds the match ``condition`` (plus a ``days`` lookback window) and
    ``action`` describes what to do on match (``link`` / ``update``).
    """

    name: str | None = None
    description: str | None = None
    entityType: str | None = None
    applicableOn: str | None = None
    isActive: bool | None = None
    priority: int | None = None
    criteria: dict[str, Any] | None = None
    action: dict[str, Any] | None = None
    actionType: dict[str, Any] | str | None = None
    endDate: float | None = None
    skipPlaybookExecution: Any | None = None
    recordTags: list[Any] | None = None
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    id: int | None = None
