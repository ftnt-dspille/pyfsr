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

from ..models import ScheduledTask
from ..pagination import extract_members
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

    def _list_raw(self) -> list[dict[str, Any]]:
        """Return all scheduled periodic tasks as raw dicts.

        A single ``limit``-unbounded fetch (the wf API ignores ``page`` but
        honours ``offset``/``limit``). Internal -- write paths (``set_enabled``,
        ``delete``, ``trigger_now``) mutate the raw dict, so they use this
        rather than the public, optionally-typed :meth:`list`.
        """
        resp = self.client.get(_ENDPOINT, params={"format": "json", "offset": 0, "limit": 2147483647})
        members = extract_members(resp)
        if not members and isinstance(resp, dict) and isinstance(resp.get("results"), list):
            members = resp["results"]
        return members

    def list(self, *, typed: bool = True) -> list[ScheduledTask] | list[dict[str, Any]]:
        """Return all scheduled periodic tasks.

        Args:
            typed: parse rows into :class:`~pyfsr.models.ScheduledTask`
                (default); pass ``False`` for raw dicts.
        """
        raw = self._list_raw()
        if typed:
            return [ScheduledTask.model_validate(t) for t in raw]
        return raw

    def get(self, name: str, *, typed: bool = True) -> ScheduledTask | dict[str, Any] | None:
        """Return one scheduled task by exact ``name`` (``None`` if absent).

        Args:
            typed: parse the result into a :class:`~pyfsr.models.ScheduledTask`
                (default); pass ``False`` for the raw dict.
        """
        for task in self._list_raw():
            if task.get("name") == name:
                return ScheduledTask.model_validate(task) if typed else task
        return None

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        """Enable/disable the task named ``name`` and return the updated record.

        The wf API only accepts a full-record ``PUT`` (no PATCH), so this reads
        the current row, flips ``enabled``, and PUTs it back.
        """
        task = self.get(name, typed=False)
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
        task = self.get(name, typed=False)
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
        typed: bool = True,
    ) -> ScheduledTask | dict[str, Any]:
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
            typed: parse the result into a :class:`~pyfsr.models.ScheduledTask`
                (default); pass ``False`` for the raw dict.

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
        resp = self.client.post(_ENDPOINT, data=body, params={"format": "json"})
        return ScheduledTask.model_validate(resp) if typed else resp

    def get_or_create(
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
        update_if_exists: bool = False,
        typed: bool = True,
    ) -> tuple[ScheduledTask | dict[str, Any], bool]:
        """Idempotently ensure a schedule named ``name`` exists; return ``(task, created)``.

        If a schedule with that name already exists, it is returned unchanged
        (``created=False``). When ``update_if_exists=True`` and the schedule
        exists, its ``workflow_iri``/``cron``/``timezone`` are updated in place
        via :meth:`set_enabled`-style PUT; otherwise the existing record is
        returned as-is. Returns ``created=True`` only when the schedule was
        newly created.

        Args:
            name: schedule display name (the lookup key).
            workflow_iri: the workflow IRI -- used on create (and on update if
                ``update_if_exists=True``).
            cron: 5-field cron expression -- used on create (and on update if
                ``update_if_exists=True``).
            timezone: IANA timezone (default ``"UTC"``).
            enabled: create the task enabled (default ``True``).
            exit_if_running: skip a fire if the previous run is still active.
            create_user: optional ``/api/3/people/<uuid>`` IRI.
            priority: optional task-priority picklist object.
            update_if_exists: when ``True`` and the schedule exists, replace its
                cron/workflow/timezone with the new values. Default ``False``
                (leave the existing schedule untouched).
            typed: parse the result into a :class:`~pyfsr.models.ScheduledTask`.

        Returns:
            ``(ScheduledTask, created)`` -- the existing task with
            ``created=False``, or the newly-created task with ``created=True``.
        """
        existing = self.get(name, typed=False)
        if existing is not None:
            if update_if_exists:
                body = copy.deepcopy(existing)
                crontab = _parse_cron(cron)
                crontab["timezone"] = timezone
                body["crontab"] = crontab
                body["kwargs"]["wf_iri"] = workflow_iri
                body["kwargs"]["timezone"] = timezone
                body["enabled"] = enabled
                updated = self.client.put(f"{_ENDPOINT}{existing['id']}/", data=body, params={"format": "json"})
                return (ScheduledTask.model_validate(updated) if typed else updated), False
            return (ScheduledTask.model_validate(existing) if typed else existing), False
        return self.create(
            name,
            workflow_iri,
            cron,
            timezone=timezone,
            enabled=enabled,
            exit_if_running=exit_if_running,
            create_user=create_user,
            priority=priority,
            typed=typed,
        ), True

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
            task = self.get(name, typed=False)
            if task is None:
                raise ValueError(f"No scheduled task named {name!r}")
            task_id = task["id"]
        return self.client.post(_TRIGGER_NOW, data={"id": task_id}, params={"format": "json"})

    def audit(self, name: str, *, run_limit: int = 10) -> dict[str, Any]:
        """One-call health report for a schedule: config + whether it FIRES.

        A schedule can look configured and still never run (created disabled,
        cron narrower than its name implies, workflow deleted out from under
        it), and a firing schedule is invisible unless you correlate it with
        playbook-run rows by hand. This folds both halves together:

        - the schedule row (``enabled``, cron, ``last_run_at``,
          ``total_run_count``, the target ``wf_iri``)
        - the target workflow's current name (or the fact that it is GONE)
        - its most recent playbook runs, matched by workflow name via the run
          list's ``search=`` filter (run rows carry no workflow uuid, and the
          ``wf_uuid`` query param is silently ignored on 8.0 -- verified live)
        - derived verdicts: ``ever_fired``, ``firing`` (a cron fire newer than
          the last two cron windows is not attempted -- ``firing`` simply
          means ``enabled and ever_fired``), and ``last_run_status``

        Returns a plain dict shaped for direct display::

            {"name", "enabled", "cron", "timezone", "last_run_at",
             "total_run_count", "workflow_iri", "workflow_name",
             "workflow_exists", "ever_fired", "firing", "last_run_status",
             "runs": [{"status", "created", "name"}, ...],
             "problems": ["..."]}

        Example:
            >>> client.schedules.audit("nightly-recon")["problems"]
            []
        """
        row = self.get(name, typed=False)
        if row is None:
            raise ValueError(f"No scheduled task named {name!r}")
        kwargs = row.get("kwargs") or {}
        if isinstance(kwargs, str):
            import json as _json

            try:
                kwargs = _json.loads(kwargs)
            except ValueError:
                kwargs = {}
        wf_iri = kwargs.get("wf_iri") or ""
        crontab = row.get("crontab") or {}
        cron = " ".join(
            str(crontab.get(k, "?")) for k in ("minute", "hour", "day_of_month", "month_of_year", "day_of_week")
        )

        wf_name: str | None = None
        wf_exists = False
        if wf_iri:
            try:
                wf = self.client.get(wf_iri)
                wf_name = wf.get("name")
                wf_exists = True
            except Exception:  # noqa: BLE001 - a 404'd workflow IS the finding
                wf_exists = False

        runs: list[dict[str, Any]] = []
        if wf_name:
            # Live table first; the box archives runs to historical-workflows
            # (hourly on stock schedules), so a daily cron's last fire is often
            # ONLY there.
            for path in ("/api/wf/api/workflows/", "/api/wf/api/historical-workflows/"):
                resp = self.client.get(
                    path,
                    params={"search": wf_name, "ordering": "-id", "limit": run_limit},
                )
                members = resp.get("hydra:member") or resp.get("results") or []
                runs.extend(
                    {"status": m.get("status"), "created": m.get("created"), "name": m.get("name")}
                    for m in members
                    if m.get("name") == wf_name  # search= is fuzzy; pin exact
                )
                if len(runs) >= run_limit:
                    break
            runs = runs[:run_limit]

        ever_fired = bool(row.get("total_run_count"))
        enabled = bool(row.get("enabled"))
        problems: list[str] = []
        if not enabled:
            problems.append("schedule is DISABLED -- the cron will never fire it")
        if not wf_exists:
            problems.append(f"target workflow {wf_iri or '<none>'} does not exist (deleted?)")
        if enabled and not ever_fired:
            problems.append("enabled but has never fired -- created recently, or beat is not picking it up")
        if runs and runs[0]["status"] not in ("finished",):
            problems.append(f"most recent run is {runs[0]['status']!r}, not finished")
        if wf_name and not runs and ever_fired:
            problems.append(
                "schedule has fired (per beat's last_run_at) but no run rows survive -- "
                "run logs purged/archived by the stock purge schedules, or the workflow "
                "was renamed since. Beat's counter is the durable evidence here."
            )

        return {
            "name": row.get("name"),
            "enabled": enabled,
            "cron": cron,
            "timezone": crontab.get("timezone"),
            "last_run_at": row.get("last_run_at"),
            "total_run_count": row.get("total_run_count"),
            "workflow_iri": wf_iri,
            "workflow_name": wf_name,
            "workflow_exists": wf_exists,
            "ever_fired": ever_fired,
            "firing": enabled and ever_fired,
            "last_run_status": runs[0]["status"] if runs else None,
            "runs": runs,
            "problems": problems,
        }
