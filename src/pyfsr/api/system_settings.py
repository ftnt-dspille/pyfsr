"""System Settings — the single ``/api/3/system_settings`` configuration blob.

FortiSOAR keeps almost every appliance-wide UI/behaviour toggle in one root
``SystemSettings`` record whose ``publicValues`` is a deeply-nested dict
(playbook-log filters, recycle-bin TTL, workflow-log config, themes, …). The
GUI re-PUTs the *entire* object on every save; this wrapper instead does a
read / deep-merge / minimal-PUT so callers only specify the keys they want to
change.

Accessed as ``client.system_settings``.

Example:
    >>> # Exclude "test"-tagged runs from the playbook execution-log view
    >>> client.system_settings.set_workflow_log_filter(["test"])
    >>> # Or merge an arbitrary patch into publicValues
    >>> client.system_settings.update({"recycleBin": {"enabled": True}})
"""

from __future__ import annotations

import copy
from typing import Any

from .base import BaseAPI

_RELATIONSHIPS = {"$relationships": "true"}


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge ``patch`` into a copy of ``base`` (patch wins).

    Nested dicts are merged key-by-key; every other value (including lists) is
    replaced wholesale, matching how FortiSOAR stores leaf settings.
    """
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class SystemSettingsAPI(BaseAPI):
    """Read and minimally update the root ``SystemSettings`` record."""

    _ENDPOINT = "/api/3/system_settings"

    def get_root(self) -> dict[str, Any]:
        """Return the root settings record (the one whose ``parent`` is null).

        ``GET /api/3/system_settings?$relationships=true`` returns the root plus
        its ``sections`` (TAXII, iframe, …); this picks out the root object.
        """
        resp = self.client.get(self._ENDPOINT, params=_RELATIONSHIPS)
        members = (resp or {}).get("hydra:member") or []
        for record in members:
            if record.get("parent") is None:
                return record
        if members:
            return members[0]
        raise ValueError("No system_settings root record returned")

    def get_public_values(self) -> dict[str, Any]:
        """Return just the root record's ``publicValues`` dict."""
        return self.get_root().get("publicValues") or {}

    def get_named(self, name: str) -> dict[str, Any]:
        """Return a ``SystemSettings`` record by its ``name``.

        Some settings live in their own named records rather than the root blob
        (e.g. ``"Advanced Development Settings"``). Raises ``ValueError`` if no
        record with that name is returned.
        """
        resp = self.client.get(self._ENDPOINT, params=_RELATIONSHIPS)
        for record in (resp or {}).get("hydra:member") or []:
            if record.get("name") == name:
                return record
        raise ValueError(f"No system_settings record named {name!r}")

    def update(
        self,
        public_values_patch: dict[str, Any] | None = None,
        *,
        private_values_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Deep-merge patches into the root record and PUT the minimal body.

        Only ``publicValues`` / ``privateValues`` are sent (not the bulky
        ``sections`` array), which is all the API needs. Returns the updated
        root record.

        Args:
            public_values_patch: keys to merge into ``publicValues``.
            private_values_patch: keys to merge into ``privateValues``.
        """
        if not public_values_patch and not private_values_patch:
            raise ValueError("update() needs at least one patch")

        root = self.get_root()
        uuid = root["uuid"]
        body: dict[str, Any] = {}
        if public_values_patch:
            body["publicValues"] = _deep_merge(root.get("publicValues") or {}, public_values_patch)
        if private_values_patch:
            body["privateValues"] = _deep_merge(root.get("privateValues") or {}, private_values_patch)
        return self.client.put(f"{self._ENDPOINT}/{uuid}", data=body, params=_RELATIONSHIPS)

    # ------------------------------------------------------------- convenience
    _DEV_SETTINGS_NAME = "Advanced Development Settings"
    _DEV_FLAGS = {
        "connectors": "allowCustomConnector",
        "widgets": "allowCustomWidget",
        "agents": "allow_ai_agent",
    }

    def get_development_mode(self) -> dict[str, bool]:
        """Return the current dev-edit toggles as ``{connectors, widgets, agents}``.

        Reads the *Advanced Development Settings* record (System Settings →
        Application Editor → *Advanced Development Settings* in the UI).
        """
        entry = self._dev_entry(self.get_named(self._DEV_SETTINGS_NAME))
        return {arg: bool(entry.get(key)) for arg, key in self._DEV_FLAGS.items()}

    def set_development_mode(
        self,
        *,
        connectors: bool | None = None,
        widgets: bool | None = None,
        agents: bool | None = None,
    ) -> dict[str, Any]:
        """Enable/disable editing custom connectors, widgets, and AI agents.

        Flips the *Advanced Development Settings* flags that gate the in-product
        editors — ``allowCustomConnector`` / ``allowCustomWidget`` /
        ``allow_ai_agent`` — which live in their own named ``SystemSettings``
        record (``privateValues.values[0]``), not the root blob. Each argument is
        tri-state: ``True``/``False`` to set, ``None`` (default) to leave as-is.

        Example:
            >>> client.system_settings.set_development_mode(
            ...     connectors=True, widgets=True, agents=True)

        Returns the updated record. Raises ``ValueError`` if no flag is given.
        """
        wanted = {"connectors": connectors, "widgets": widgets, "agents": agents}
        if all(v is None for v in wanted.values()):
            raise ValueError("set_development_mode() needs at least one flag")

        record = self.get_named(self._DEV_SETTINGS_NAME)
        values = list((record.get("privateValues") or {}).get("values") or [{}])
        entry = dict(values[0])
        for arg, value in wanted.items():
            if value is not None:
                entry[self._DEV_FLAGS[arg]] = value
        values[0] = entry
        return self.client.put(
            f"{self._ENDPOINT}/{record['uuid']}",
            data={"privateValues": {"values": values}},
            params=_RELATIONSHIPS,
        )

    @staticmethod
    def _dev_entry(record: dict[str, Any]) -> dict[str, Any]:
        """The first ``privateValues.values`` entry holding the dev flags."""
        values = (record.get("privateValues") or {}).get("values") or []
        return values[0] if values else {}

    def set_workflow_log_filter(self, tags: list[str], operation: str = "exclude") -> dict[str, Any]:
        """Set the playbook execution-log tag filter (Settings → Playbooks → Logs).

        ``operation`` is ``"exclude"`` or ``"include"``. Note the FortiSOAR key
        is the (misspelled) ``filterOpration`` — handled here so callers don't
        have to reproduce the typo.
        """
        if operation not in ("exclude", "include"):
            raise ValueError("operation must be 'exclude' or 'include'")
        return self.update({"playbook": {"logs": {"tags": tags, "filterOpration": operation}}})

    def set_playbook_debug_logging(
        self, enabled: bool = True, *, allow_playbook_override: bool = False
    ) -> dict[str, Any]:
        """Set the global playbook execution-log verbosity (Settings → Playbooks).

        ``enabled`` turns on full debug logging for every run;
        ``allow_playbook_override=False`` forces individual playbooks to use the
        global setting (they can't opt out). Maps to
        ``publicValues.workflow_log_config``.
        """
        return self.update(
            {
                "workflow_log_config": {
                    "debug": enabled,
                    "allow_pb_to_override": allow_playbook_override,
                }
            }
        )
