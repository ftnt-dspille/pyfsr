"""Scheduled (periodic) Celery tasks -- ``/api/wf/api/scheduled/``.

The workflow engine's recurring jobs (Reclaim Disk Space, Purge Executed
Playbook Logs, Archive Data, plus user-created playbook schedules) are
django-celery-beat ``PeriodicTask`` rows. This wrapper lists them, toggles
their ``enabled`` flag, creates new ones, and force-triggers a schedule
out-of-band of its cron.

Accessed as ``client.schedules``.

A scheduled task is a ``PeriodicTask`` with a nested ``crontab``
(``minute``/``hour``/``day_of_week``/``day_of_month``/``month_of_year``/
``timezone``) and ``kwargs`` carrying ``wf_iri`` (the workflow the schedule
runs), ``exit_if_running``, ``timezone``/``utcOffset``. The server fills in
``task`` (``workflow.tasks.periodic_task``), ``schedule_id``, ``crontab.id``,
and ``kwargs.name``/``description``/``auth``/``schedule_entry_name``.

Note: each row's ``id`` is a per-request Fernet token that decrypts to a stable
primary key, so always look the task up by ``name`` (the id from one GET is
fine to PUT back immediately, which is what :meth:`SchedulesAPI.set_enabled`
does, and to POST to ``trigger-now/``, which is what :meth:`SchedulesAPI.trigger_now` does).

Example:
    >>> [t["name"] for t in client.schedules.list() if t["enabled"]]
    ['Reclaim disk space periodically', ...]
    >>> client.schedules.disable("Reclaim disk space periodically")
    >>> iri = client.playbooks.resolve_iri("Nightly Recon")
    >>> task = client.schedules.create("nightly-recon", iri, "7 2 * * *")
    >>> client.schedules.trigger_now(name="nightly-recon")
"""

from __future__ import annotations

import copy
import datetime as _dt
from typing import Any
from zoneinfo import ZoneInfo

from .base import BaseAPI

_ENDPOINT = "/api/wf/api/scheduled/"
_TRIGGER_NOW = f"{_ENDPOINT}trigger-now/"

# django-celery-beat CrontabSchedule fields, in standard 5-field cron order
# (minute hour day_of_month month_of_year day_of_week).
_CRON_FIELDS = ("minute", "hour", "day_of_month", "month_of_year", "day_of_week")


def _parse_cron(cron: str) -> dict[str, str]:
    """Split a 5-field cron string into the crontab field map.

    ``"7 2 * * *"`` -> ``{minute: "7", hour: "2", day_of_month: "*",
    month_of_year: "*", day_of_week: "*"}``. Raises ``ValueError`` unless the
    expression is exactly five whitespace-separated fields.
    """
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(
            f"cron must be 5 fields (minute hour day_of_month month_of_year day_of_week), got {len(parts)}: {cron!r}"
        )
    return dict(zip(_CRON_FIELDS, parts, strict=True))


def _utc_offset(timezone: str) -> str | None:
    """Best-effort ``UTC±HH:MM`` for an IANA timezone name.

    Returns ``None`` if the timezone is unknown (or tzdata is absent), so the
    caller can omit ``kwargs.utcOffset`` rather than send a wrong value. This
    mirrors the display hint FortiSOAR's scheduler UI sends; the crontab's
    ``timezone`` is the value the scheduler actually honours.
    """
    try:
        now = _dt.datetime.now(ZoneInfo(timezone))
    except Exception:
        return None
    offset = now.utcoffset()
    if offset is None:
        return None
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"UTC{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


class SchedulesAPI(BaseAPI):
    """List, enable/disable, create, and trigger workflow-engine periodic tasks."""

    def list(self) -> list[dict[str, Any]]:
        """Return all scheduled periodic tasks.

        A single ``limit``-unbounded fetch (the wf API ignores ``page`` but
        honours ``offset``/``limit``).
        """
        resp = self.client.get(_ENDPOINT, params={"format": "json", "offset": 0, "limit": 2147483647})
        if isinstance(resp, dict):
            return resp.get("hydra:member") or resp.get("results") or []
        return resp or []

    def get(self, name: str) -> dict[str, Any] | None:
        """Return one scheduled task by exact ``name`` (``None`` if absent)."""
        for task in self.list():
            if task.get("name") == name:
                return task
        return None

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        """Enable/disable the task named ``name`` and return the updated record.

        The wf API only accepts a full-record ``PUT`` (no PATCH), so this reads
        the current row, flips ``enabled``, and PUTs it back.
        """
        task = self.get(name)
        if task is None:
            raise ValueError(f"No scheduled task named {name!r}")
        body = copy.deepcopy(task)
        body["enabled"] = enabled
        return self.client.put(f"{_ENDPOINT}{task['id']}/", data=body, params={"format": "json"})

    def disable(self, name: str) -> dict[str, Any]:
        """Disable the task named ``name``."""
        return self.set_enabled(name, False)

    def enable(self, name: str) -> dict[str, Any]:
        """Enable the task named ``name``."""
        return self.set_enabled(name, True)

    def delete(self, name: str) -> None:
        """Delete the periodic task named ``name``.

        Resolves the task's current ``id`` (a per-request Fernet token, so it
        is looked up fresh immediately before the DELETE) and removes it via
        ``DELETE /api/wf/api/scheduled/{id}/``. The task is gone entirely -- to
        merely pause it, use :meth:`disable`.

        Args:
            name: the schedule display name.

        Example:
            >>> client.schedules.delete("nightly-recon")
        """
        task = self.get(name)
        if task is None:
            raise ValueError(f"No scheduled task named {name!r}")
        self.client.delete(f"{_ENDPOINT}{task['id']}/", params={"format": "json"})

    def create(
        self,
        name: str,
        workflow_iri: str,
        cron: str,
        *,
        timezone: str = "UTC",
        enabled: bool = True,
        exit_if_running: bool = True,
        create_user: str | None = None,
        priority: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a periodic task that runs ``workflow_iri`` on a cron schedule.

        Mirrors what FortiSOAR's scheduler UI sends to
        ``POST /api/wf/api/scheduled/``: a django-celery-beat ``PeriodicTask``
        with a nested ``crontab`` and a ``kwargs.wf_iri`` pointing at the
        workflow. The server fills the rest (``task``, ``schedule_id``,
        ``crontab.id``, ``kwargs.name``/``description``/``auth``).

        Args:
            name: schedule display name (the server also uses it as the
                task's ``description``).
            workflow_iri: the workflow IRI, ``/api/3/workflows/<uuid>`` --
                resolve a playbook name with ``client.playbooks.resolve_iri(name)``.
            cron: 5-field cron expression ``"minute hour day_of_month
                month_of_year day_of_week"`` (e.g. ``"7 2 * * *"`` for 02:07
                daily, ``"0 0 * * 1"`` for midnight Mondays).
            timezone: IANA timezone for the crontab (default ``"UTC"``).
            enabled: create the task enabled (default ``True``).
            exit_if_running: skip a fire if the previous run is still active
                (default ``True`` -- prevents overlap for long-running playbooks).
            create_user: optional ``/api/3/people/<uuid>`` IRI; the server
                normally derives this from the auth context, so omit unless a
                create is rejected without it.
            priority: optional task-priority picklist object; omitted by
                default (the server applies its own default -- the UI's Medium
                picklist is instance-specific and not assumed).

        Returns:
            The created periodic-task record, with the server-generated
            ``id`` (Fernet token) and ``schedule_id``.

        Example:
            >>> iri = client.playbooks.resolve_iri("Nightly Recon")
            >>> task = client.schedules.create("nightly-recon", iri, "7 2 * * *")
        """
        crontab = _parse_cron(cron)
        crontab["timezone"] = timezone
        kwargs: dict[str, Any] = {
            "exit_if_running": exit_if_running,
            "wf_iri": workflow_iri,
            "timezone": timezone,
        }
        utc = _utc_offset(timezone)
        if utc is not None:
            kwargs["utcOffset"] = utc
        if create_user is not None:
            kwargs["createUser"] = create_user
        if priority is not None:
            kwargs["priority"] = priority
        body = {
            "name": name,
            "crontab": crontab,
            "kwargs": kwargs,
            "expires": None,
            "start_time": None,
            "enabled": enabled,
        }
        return self.client.post(_ENDPOINT, data=body, params={"format": "json"})

    def trigger_now(self, *, name: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        """Force-trigger a scheduled task immediately (``POST .../trigger-now/``).

        Identifies the task by ``name`` (resolved to its ``id`` via
        :meth:`get`) or by its ``task_id`` (the Fernet-token ``id`` from
        :meth:`list`/:meth:`get`/:meth:`create`). The fire is asynchronous --
        the response confirms the trigger was accepted; use
        ``client.playbooks.wait_for_run`` to track the resulting playbook run.
        Fires regardless of the task's ``enabled`` flag (``enabled`` governs the
        cron scheduler, not manual triggers).

        Prefer ``name=`` over ``task_id``: the schedule ``id`` is a per-request
        Fernet token that rotates, so a ``task_id`` captured from an earlier
        :meth:`create`/:meth:`list` call can be stale by the time it is used.
        ``name=`` re-resolves a fresh id each call.

        Args:
            name: schedule display name (resolved to its id).
            task_id: the schedule's ``id`` (Fernet token) instead of name. Only
                reliable when used immediately after the call that produced it.

        Example:
            >>> client.schedules.trigger_now(name="nightly-recon")
            {'message': 'The associated workflow is successfully triggered'}
        """
        if task_id is None and name is None:
            raise ValueError("trigger_now requires name or task_id")
        if task_id is None:
            task = self.get(name)
            if task is None:
                raise ValueError(f"No scheduled task named {name!r}")
            task_id = task["id"]
        return self.client.post(_TRIGGER_NOW, data={"id": task_id}, params={"format": "json"})
