"""Typed models for the workflow-engine's periodic tasks (``/api/wf/api/scheduled/``).

A scheduled task is a django-celery-beat ``PeriodicTask`` row, not an ``/api/3``
module record — no ``@id``/``uuid`` envelope, just a Fernet-token ``id``. Shape
live-verified against an 8.0 box (``client.schedules.list()``).
"""

from __future__ import annotations

from typing import Any

from ._integration import ApiResult


class CrontabScheduleModel(ApiResult):
    """The nested ``crontab`` on a :class:`ScheduledTask`."""

    id: int | None = None
    minute: str | None = None
    hour: str | None = None
    day_of_month: str | None = None
    month_of_year: str | None = None
    day_of_week: str | None = None
    timezone: str | None = None


class ScheduledTask(ApiResult):
    """A django-celery-beat ``PeriodicTask`` from ``/api/wf/api/scheduled/``.

    ``id`` is a per-request Fernet token (not a stable primary key) — always
    look a task up by ``name`` before writing it back, per
    :class:`~pyfsr.api.schedules.SchedulesAPI`'s module docstring. ``kwargs``
    carries the workflow-specific payload (``wf_iri``, ``exit_if_running``,
    ``schedule_id``, ...) and is left untyped since its shape varies by task.
    """

    id: str | None = None
    name: str | None = None
    crontab: CrontabScheduleModel | None = None
    interval: Any | None = None
    task: str | None = None
    args: str | None = None
    kwargs: dict[str, Any] | None = None
    queue: str | None = None
    exchange: str | None = None
    routing_key: str | None = None
    headers: str | None = None
    priority: Any | None = None
    expires: str | None = None
    expire_seconds: int | None = None
    one_off: bool | None = None
    start_time: str | None = None
    enabled: bool | None = None
    last_run_at: str | None = None
    total_run_count: int | None = None
    date_changed: str | None = None
    description: str | None = None
    solar: Any | None = None
    clocked: Any | None = None
