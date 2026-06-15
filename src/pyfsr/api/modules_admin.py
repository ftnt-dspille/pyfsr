"""Module schema administration: create modules, add/alter fields, publish.

Where :class:`~pyfsr.api.modules.ModulesAPI` is read-only *discovery*, this is the
*write* surface for the Application/Module Editor. It drives the same endpoints the
in-product editor and the "Clone Module" playbook use:

- **Staging** lives at ``/api/3/staging_model_metadatas`` — the editable draft. Creating
  a module or changing a field edits staging only; nothing is live yet.
- **Published** lives at ``/api/3/model_metadatas`` — the committed schema records read.
- **Publish** is a single global ``PUT /api/publish`` that promotes *all* pending staged
  changes on the appliance to live. It is **appliance-wide**, not per-module — see
  :meth:`publish`.

A module is a staging record with an ``attributes`` list; each attribute (field) carries a
``type`` (the storage type, e.g. ``text`` / ``json`` / ``integer``) and a ``formType`` (the
UI widget, e.g. ``text`` / ``textarea`` / ``json`` / ``datetime``).

Example::

    admin = client.modules_admin
    admin.create_module("widgets", label="Widget", fields=[
        admin.field("name", db_type="text", form_type="text", required=True),
        admin.field("payload", db_type="text", form_type="textarea"),
    ])
    admin.set_field_type("widgets", "payload", db_type="json", form_type="json")
    admin.publish()                      # appliance-wide commit
"""

from __future__ import annotations

import time
from typing import Any

from ..exceptions import FortiSOARException
from .base import BaseAPI

_STAGING = "/api/3/staging_model_metadatas"
_PUBLISHED = "/api/3/model_metadatas"
_PUBLISH = "/api/publish"
_REL = {"$relationships": "true"}
_ALL = {"$limit": 2147483647}

# Substrings the appliance returns (as 5xx bodies / error messages) while a publish
# is mid-flight — it runs a full backup + DB migrate cycle and the API is briefly
# unavailable, surfacing transient state strings instead of real errors.
_PUBLISH_TRANSIENT_MARKERS = (
    "decrypt database",
    "encrypt database",
    "cleaning up old backups",
    "creating backup",
    "taking backup",
    "restoring",
    "migrat",  # "migrating" / "migration in progress"
    "backup",
    "service temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway time-out",
    "gateway timeout",
)


class ModulesAdminAPI(BaseAPI):
    """Create modules, add/alter fields, and publish schema changes."""

    # --------------------------------------------------------------- read
    def _staging_lite(self, module: str) -> dict[str, Any] | None:
        """Return the lightweight staging row for ``module`` (by type), or None."""
        want = module.strip().lower()
        data = self.client.get(_STAGING, params=_ALL)
        for m in (data or {}).get("hydra:member", []):
            if str(m.get("type", "")).lower() == want or str(m.get("module", "")).lower() == want:
                return m
        return None

    def get_staging(self, module: str) -> dict[str, Any] | None:
        """Return the full staging metadata record (incl. ``attributes``) for ``module``.

        ``module`` is the module ``type`` (or plural ``module`` name). Returns None if no
        staging record exists.
        """
        lite = self._staging_lite(module)
        if not lite:
            return None
        return self.client.get(f"{_STAGING}/{lite['uuid']}", params=_REL)

    def get_published(self, module: str) -> dict[str, Any] | None:
        """Return the full *published* metadata record for ``module``, or None.

        None means the module has never been published (it may still exist in staging).
        """
        want = module.strip().lower()
        data = self.client.get(_PUBLISHED, params=_ALL)
        lite = next(
            (
                m
                for m in (data or {}).get("hydra:member", [])
                if str(m.get("type", "")).lower() == want
                or str(m.get("module", "")).lower() == want
            ),
            None,
        )
        if not lite:
            return None
        return self.client.get(f"{_PUBLISHED}/{lite['uuid']}", params=_REL)

    def is_published(self, module: str) -> bool:
        """True if ``module`` exists in the published schema (``model_metadatas``)."""
        return self.get_published(module) is not None

    def get_field(self, module: str, field: str) -> dict[str, Any] | None:
        """Return one staged attribute (field) dict by ``name``, or None."""
        mod = self.get_staging(module)
        if not mod:
            return None
        return next((a for a in mod.get("attributes", []) if a.get("name") == field), None)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def field(
        name: str,
        *,
        db_type: str = "text",
        form_type: str | None = None,
        label: str | None = None,
        required: bool = False,
        searchable: bool = False,
        maxlength: int = 10485760,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build an attribute (field) dict with sane defaults for create/add.

        ``db_type`` is the storage type (``text``/``json``/``integer``/...); ``form_type``
        is the UI widget (defaults to ``db_type``). Extra keys override the defaults.
        """
        attr = {
            "name": name,
            "type": db_type,
            "formType": form_type or db_type,
            "descriptions": {"singular": label or name},
            "displayName": f"{{{{ {name} }}}}",
            "searchable": searchable,
            "collection": False,
            "visibility": True,
            "readable": True,
            "writeable": True,
            "validation": {"required": required, "minlength": 0, "maxlength": maxlength},
        }
        attr.update(extra)
        return attr

    # -------------------------------------------------------------- write
    def create_module(
        self,
        module: str,
        *,
        label: str | None = None,
        plural: str | None = None,
        fields: list[dict[str, Any]] | None = None,
        display_template: str | None = None,
        ownable: bool = True,
        taggable: bool = False,
        **opts: Any,
    ) -> dict[str, Any]:
        """Create a new module in **staging** (not yet live — call :meth:`publish`).

        ``module`` is the type/table name. ``fields`` is a list of attribute dicts (use
        :meth:`field`); if omitted, a single required ``name`` text field is created so the
        module is valid. Returns the created staging record.

        ``display_template`` is the module's **Display Template** — the Jinja expression
        used to render a record's title throughout the UI. It must reference a field, e.g.
        ``"{{ name }}"`` (this is the top-level ``displayName`` on the metadata record, NOT
        the human label, which lives in ``descriptions``). If omitted it defaults to the
        first field, preferring a field literally named ``name``.
        """
        label = label or module
        if fields is None:
            fields = [self.field("name", db_type="text", form_type="text", required=True)]
        if display_template is None:
            names = [f.get("name") for f in fields if f.get("name")]
            anchor = "name" if "name" in names else (names[0] if names else "name")
            display_template = f"{{{{ {anchor} }}}}"
        payload = {
            "type": module,
            "module": module,
            "tableName": module,
            "displayName": display_template,
            "descriptions": {"singular": label, "plural": plural or f"{label}s"},
            "ownable": ownable,
            "userOwnable": ownable,
            "taggable": taggable,
            "trackable": True,
            "indexable": True,
            "writable": True,
            "queueable": False,
            "system": False,
            "attributes": fields,
        }
        payload.update(opts)
        return self.client.post(_STAGING, data=payload, params=_REL)

    def _put_attributes(self, mod: dict[str, Any], attributes: list[dict[str, Any]]) -> dict[str, Any]:
        """PUT only the ``attributes`` of a staging record (a full-record PUT is
        rejected — the GET payload carries read-only ``@id``/``@context`` keys)."""
        return self.client.put(
            f"{_STAGING}/{mod['uuid']}", data={"attributes": attributes}, params=_REL
        )

    def add_field(self, module: str, field: dict[str, Any]) -> dict[str, Any]:
        """Append a field (build it with :meth:`field`) to ``module`` in staging."""
        mod = self.get_staging(module)
        if not mod:
            raise ValueError(f"module {module!r} not found in staging")
        attrs = mod.get("attributes", []) + [field]
        return self._put_attributes(mod, attrs)

    def set_field_type(
        self, module: str, field: str, *, db_type: str, form_type: str | None = None
    ) -> dict[str, Any]:
        """Change a staged field's storage ``type`` (and ``formType``).

        Edits go through a PUT of the whole staging record — individual
        ``attribute_metadatas`` are read-only on the platform. Change is staged until
        :meth:`publish`.
        """
        mod = self.get_staging(module)
        if not mod:
            raise ValueError(f"module {module!r} not found in staging")
        attr = next((a for a in mod.get("attributes", []) if a.get("name") == field), None)
        if attr is None:
            raise ValueError(f"field {field!r} not found on module {module!r}")
        attr["type"] = db_type
        attr["formType"] = form_type or db_type
        return self._put_attributes(mod, mod["attributes"])

    def discard_staging_draft(self, module: str) -> bool:
        """Discard a module's editable **staging draft** (``DELETE`` on staging). Returns
        False if no draft existed.

        ⚠️ **This is NOT a module delete.** FortiSOAR has no supported delete-module
        operation (the in-product editor cannot delete modules either). This only drops the
        *unpublished* staging draft:

        - If the module was **never published**, that effectively removes it (no table was
          ever created).
        - If the module **was published**, the live module and its Postgres table REMAIN —
          you just orphan them by removing the draft, with **no API path to fully delete**
          them (requires backend CLI/SQL). Prefer leaving the draft in place.

        For a clean throwaway, do NOT publish it; then discarding the draft removes it.
        """
        lite = self._staging_lite(module)
        if not lite:
            return False
        self.client.delete(f"{_STAGING}/{lite['uuid']}")
        return True

    @staticmethod
    def _is_publish_transient(exc: Exception) -> bool:
        """True if ``exc`` is a transient state surfaced while a publish is in flight.

        During a publish the appliance runs a backup + DB migrate cycle and the API is
        briefly down, returning 5xx and/or state strings like "Decrypt Database" /
        "Cleaning Up Old Backups" rather than a real error. We treat any 5xx, plus any
        error message matching a known migrate-cycle marker, as "still working".
        """
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return True
        text = " ".join(
            str(getattr(exc, attr, "") or "")
            for attr in ("message", "error_type")
        ).lower() or str(exc).lower()
        return any(marker in text for marker in _PUBLISH_TRANSIENT_MARKERS)

    def _wait_until_ready(self, timeout: float, poll_interval: float) -> None:
        """Poll a cheap read until the appliance finishes the publish/migrate cycle.

        Raises :class:`TimeoutError` if it does not recover within ``timeout`` seconds, or
        re-raises any non-transient error encountered while probing.
        """
        deadline = time.monotonic() + timeout
        last_exc: Exception | None = None
        while True:
            try:
                self.client.get(_PUBLISHED, params={"$limit": 1})
                return
            except FortiSOARException as exc:
                if not self._is_publish_transient(exc):
                    raise
                last_exc = exc
            except Exception as exc:  # network drop mid-restart counts as transient
                last_exc = exc
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"publish did not complete within {timeout}s "
                    f"(last appliance state: {last_exc})"
                )
            time.sleep(poll_interval)

    def publish(
        self,
        *,
        wait: bool = True,
        timeout: float = 600.0,
        poll_interval: float = 10.0,
    ) -> dict[str, Any] | None:
        """Commit **all** pending staged schema changes to live (``PUT /api/publish``).

        ⚠️ Appliance-wide: this publishes every pending change in staging across the whole
        instance, not just modules you touched. On a shared appliance, confirm nothing else
        is mid-edit before calling.

        The publish triggers a full backup + DB migrate cycle during which the API is
        briefly unavailable, returning 5xx and transient state strings ("Decrypt Database",
        "Cleaning Up Old Backups", ...). By default this method is **synchronous**: it
        tolerates those transient states on the initial call and then polls until the
        appliance has finished, so callers can safely read back the published schema
        immediately on return.

        Args:
            wait: If True (default), block until the publish/migrate cycle completes. If
                False, fire-and-forget — return as soon as the PUT is accepted (legacy
                behavior; the caller must handle transient states themselves).
            timeout: Max seconds to wait for completion when ``wait`` is True.
            poll_interval: Seconds between readiness probes.

        Returns:
            The publish response when available; ``None`` if the PUT response was consumed
            by the transient migrate cycle but the publish otherwise completed.
        """
        result: dict[str, Any] | None = None
        try:
            result = self.client.put(_PUBLISH, data={})
        except FortiSOARException as exc:
            # The PUT itself can return a transient migrate-state body; that means the
            # publish was accepted and is running, not that it failed.
            if not (wait and self._is_publish_transient(exc)):
                raise
        if wait:
            self._wait_until_ready(timeout, poll_interval)
        return result
