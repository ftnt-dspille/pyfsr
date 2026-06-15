"""Scheduled (periodic) Celery tasks — ``/api/wf/api/scheduled/``.

The workflow engine's recurring jobs (Reclaim Disk Space, Purge Executed
Playbook Logs, Archive Data, …) are django-celery-beat ``PeriodicTask`` rows.
This wrapper lists them and toggles ``enabled`` — the common "turn off the
noisy housekeeping job on a fresh instance" operation.

Accessed as ``client.schedules``.

Note: each row's ``id`` is a per-request Fernet token that decrypts to a stable
primary key, so always look the task up by ``name`` (the id from one GET is
fine to PUT back immediately, which is what :meth:`SchedulesAPI.set_enabled` does).

Example:
    >>> [t["name"] for t in client.schedules.list() if t["enabled"]]
    ['Reclaim disk space periodically', ...]
    >>> client.schedules.disable("Reclaim disk space periodically")
"""

from __future__ import annotations

import copy
from typing import Any

from .base import BaseAPI

_ENDPOINT = "/api/wf/api/scheduled/"


class SchedulesAPI(BaseAPI):
    """List and enable/disable workflow-engine periodic tasks."""

    def list(self) -> list[dict[str, Any]]:
        """Return all scheduled periodic tasks.

        A single ``limit``-unbounded fetch (the wf API ignores ``page`` but
        honours ``offset``/``limit``).
        """
        resp = self.client.get(
            _ENDPOINT, params={"format": "json", "offset": 0, "limit": 2147483647}
        )
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
