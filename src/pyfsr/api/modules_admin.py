"""Module schema administration: create modules, add/alter fields, publish.

Where :class:`~pyfsr.api.modules.ModulesAPI` is read-only *discovery*, this is the
*write* surface for the Application/Module Editor. It drives the same endpoints the
in-product editor and the "Clone Module" playbook use:

- **Staging** lives at ``/api/3/staging_model_metadatas`` — the editable draft. Creating
  a module or changing a field edits staging only; nothing is live yet.
- **Published** lives at ``/api/3/model_metadatas`` — the committed schema records read.
- **Publish** is a single global ``PUT /api/publish`` that promotes *all* pending staged
  changes on the appliance to live. It is **appliance-wide**, not per-module — see
  :meth:`ModulesAdminAPI.publish`.

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
import uuid as _uuid
from typing import Any

from ..exceptions import FortiSOARException
from .base import BaseAPI

_STAGING = "/api/3/staging_model_metadatas"
_PUBLISHED = "/api/3/model_metadatas"
_PUBLISH = "/api/publish"
_VIEW_TEMPLATES = "/api/3/system_view_templates"
_VIEW_TEMPLATES_BULK = "/api/3/bulkupsert/system_view_templates"
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
        """True if ``module`` exists in the published schema (``model_metadatas``).

        Note: on appliances that auto-mirror staging into ``model_metadatas`` on every
        write (e.g. the dev-mode schema toggle), this can read ``True`` for a module you
        have not explicitly :meth:`publish`-ed. Use :meth:`pending_changes` to see what is
        genuinely uncommitted.
        """
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
        required: bool | dict[str, Any] = False,
        searchable: bool = False,
        editable: bool = True,
        grid_column: bool = False,
        encrypted: bool = False,
        visibility: bool | dict[str, Any] = True,
        default_value: Any = None,
        tooltip: str | None = None,
        minlength: int = 0,
        maxlength: int = 10485760,
        enable_range: bool = False,
        bulk_edit: bool = False,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build an attribute (field) dict with sane defaults for create/add.

        Mirrors the Field **Properties** panel of the in-product editor:

        - ``db_type`` — storage type (``text``/``json``/``integer``/``picklists``/...);
          ``form_type`` is the UI widget/sub-type (defaults to ``db_type``).
        - ``label`` — the **Field Title** (``name`` is the immutable **Field API Key**).
        - ``editable`` — UI "Editable" (maps to ``writeable``).
        - ``searchable`` / ``grid_column`` / ``encrypted`` — the **Field Options** row.
          Note: encrypted fields can't be searchable and vice-versa.
        - ``required`` — ``False`` / ``True``, or a **condition** dict for
          "Required by condition" (the FortiSOAR filter shape).
        - ``visibility`` — ``True`` (Visible) / ``False`` (Hidden), or a **condition**
          dict for "Visible by Condition".
        - ``default_value`` / ``tooltip`` — the Default Value and Tooltip inputs.
        - ``minlength`` / ``maxlength`` / ``enable_range`` — **Length Constraints**
          ("Add minimum/maximum range" sets ``enable_range``).
        - ``bulk_edit`` — UI "Allow Bulk Edit" (maps to ``bulkAction.allow``).

        Extra keys override the defaults (e.g. ``collection=True``, ``dataSource={...}``).
        """
        validation: dict[str, Any] = {
            "required": required,
            "minlength": minlength,
            "maxlength": maxlength,
        }
        if enable_range:
            validation["_enableRange"] = True
        attr = {
            "name": name,
            "type": db_type,
            "formType": form_type or db_type,
            "descriptions": {"singular": label or name},
            "displayName": f"{{{{ {name} }}}}",
            "searchable": searchable,
            "gridColumn": grid_column,
            "encrypted": encrypted,
            "collection": False,
            "visibility": visibility,
            "readable": True,
            "writeable": editable,
            "defaultValue": default_value,
            "tooltip": tooltip,
            "validation": validation,
            "bulkAction": {
                "allow": bulk_edit,
                "buttonText": "",
                "buttonIcon": "",
                "buttonClass": "btn btn-default btn-sm",
            },
        }
        attr.update(extra)
        return attr

    @classmethod
    def picklist_field(
        cls,
        name: str,
        picklist_name: str,
        *,
        multi: bool = False,
        label: str | None = None,
        **opts: Any,
    ) -> dict[str, Any]:
        """Build a single- or multi-select **picklist** field bound to ``picklist_name``.

        ``picklist_name`` is the picklist's *list name* (e.g. ``"AlertStatus"``). ``multi``
        switches between ``picklist`` and ``multiselectpicklist`` (a collection). Pass
        through any :meth:`field` option (``required``, ``grid_column``, ...).
        """
        attr = cls.field(
            name,
            db_type="picklists",
            form_type="multiselectpicklist" if multi else "picklist",
            label=label,
            **opts,
        )
        attr["collection"] = multi
        attr["dataSource"] = {
            "model": "picklists",
            "query": {
                "logic": "AND",
                "filters": [{"field": "listName__name", "operator": "eq", "value": picklist_name}],
                "sort": [{"field": "orderIndex", "direction": "ASC"}],
            },
        }
        return attr

    @classmethod
    def relationship_field(
        cls,
        name: str,
        target_module: str,
        *,
        many: bool = True,
        label: str | None = None,
        **opts: Any,
    ) -> dict[str, Any]:
        """Build a **relationship** field to ``target_module`` (its module ``type``).

        ``many`` selects ``manyToMany`` (default) vs ``oneToMany``; both are collections.
        """
        attr = cls.field(
            name,
            db_type=target_module,
            form_type="manyToMany" if many else "oneToMany",
            label=label,
            **opts,
        )
        attr["collection"] = True
        attr["dataSource"] = {"model": target_module}
        return attr

    # ----------------------------------------------------- view templates
    @staticmethod
    def _default_view_templates(module: str) -> list[dict[str, Any]]:
        """The three default ``system_view_templates`` (list / detail / form) the
        in-product editor creates alongside a new module.

        Creating a module via ``POST staging_model_metadatas`` alone does NOT make
        these — without them the module has no grid/detail layout to render in the
        UI. The shapes mirror what the editor's "Save" posts to
        ``bulkupsert/system_view_templates``.
        """
        return [
            {
                "@type": "SystemViewTemplate",
                "name": "Default Layout",
                "isDefault": True,
                "viewOptions": "list",
                "uuid": str(_uuid.uuid4()),
                "type": "rows",
                "config": {"rows": [{"columns": [{"widgets": [{"type": "grid", "config": []}]}]}]},
                "module": module,
            },
            {
                "@type": "SystemViewTemplate",
                "name": "Default Layout",
                "isDefault": True,
                "viewOptions": "detail",
                "uuid": str(_uuid.uuid4()),
                "type": "rows",
                "config": {
                    "rows": [
                        {"columns": [{"widgets": [{"type": "editableForm", "config": []}]}]},
                        {
                            "columns": [
                                {
                                    "widgets": [
                                        {
                                            "type": "tabs",
                                            "config": {
                                                "tabs": [
                                                    {
                                                        "title": "Related Records",
                                                        "widget": {
                                                            "type": "relationship.subtab",
                                                            "config": [],
                                                        },
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                }
                            ]
                        },
                    ]
                },
                "module": module,
            },
            {
                "@type": "SystemViewTemplate",
                "name": "Default Layout",
                "isDefault": True,
                "viewOptions": "form",
                "uuid": str(_uuid.uuid4()),
                "type": "form",
                "config": {"rows": []},
                "module": module,
            },
        ]

    def get_view_templates(self, module: str) -> list[dict[str, Any]]:
        """Return the ``system_view_templates`` (list/detail/form layouts) for ``module``."""
        want = module.strip().lower()
        data = self.client.get(_VIEW_TEMPLATES, params=_ALL) or {}
        return [m for m in data.get("hydra:member", []) if str(m.get("module", "")).lower() == want]

    def create_view_templates(self, module: str) -> dict[str, Any]:
        """Create the default list/detail/form layouts for ``module`` (idempotent upsert)."""
        return self.client.post(
            _VIEW_TEMPLATES_BULK,
            data={"__data": self._default_view_templates(module), "__unique": ["uuid"]},
        )

    # ----------------------------------------------------- change tracking
    def pending_changes(self) -> list[dict[str, Any]]:
        """Modules with **uncommitted** schema changes (staged but not yet published).

        Both ``staging_model_metadatas`` and ``model_metadatas`` mirror every module;
        a module has a pending change when its staging record differs from its
        published one (or exists in only one store). Returns one entry per changed
        module::

            [{"module": "alerts", "change": "modified"},
             {"module": "widgets", "change": "created"},
             {"module": "legacy",  "change": "deleted"}]

        An empty list means the appliance is fully published — nothing for
        :meth:`publish` to commit. Use this before a (appliance-wide) publish to see
        exactly what would be promoted.
        """
        stg = {
            str(m.get("type", "")).lower(): m
            for m in (self.client.get(_STAGING, params=_ALL) or {}).get("hydra:member", [])
        }
        pub = {
            str(m.get("type", "")).lower(): m
            for m in (self.client.get(_PUBLISHED, params=_ALL) or {}).get("hydra:member", [])
        }
        changes: list[dict[str, Any]] = []
        for mod in sorted(set(stg) | set(pub)):
            if mod not in pub:
                changes.append({"module": mod, "change": "created"})
            elif mod not in stg:
                changes.append({"module": mod, "change": "deleted"})
            elif self._differs(stg[mod], pub[mod]):
                changes.append({"module": mod, "change": "modified"})
        return changes

    @staticmethod
    def _differs(staged: dict[str, Any], published: dict[str, Any]) -> bool:
        """True if a staged record differs from its published one, ignoring the
        endpoint-relative ``@id``/``@type`` keys (which always differ by store)."""
        skip = {"@id", "@type", "@context"}
        return {k: v for k, v in staged.items() if k not in skip} != {
            k: v for k, v in published.items() if k not in skip
        }

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
        trackable: bool = True,
        indexable: bool = True,
        taggable: bool = False,
        queueable: bool = False,
        recycle_bin: bool = False,
        multi_tenancy: bool = False,
        record_uniqueness: list[str] | None = None,
        default_sort: list[dict[str, Any]] | None = None,
        create_view_templates: bool = True,
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

        ``create_view_templates`` (default True) also creates the default list/detail/form
        ``system_view_templates`` — the in-product editor makes these on Save, and without
        them the module has no UI layout to render. Set False for an API-only module.

        The remaining flags map to the editor's **Additional Settings**:
        ``ownable`` (Team Ownable), ``trackable``, ``indexable``, ``taggable``,
        ``queueable``, ``recycle_bin`` (Enable Recycle Bin → ``softDeleteable``),
        ``multi_tenancy`` (Enable Multi-Tenancy → ``peerReplicable``).
        ``record_uniqueness`` is a list of field names enforcing uniqueness;
        ``default_sort`` is the default sort spec (e.g. ``[{"field": "createDate",
        "direction": "DESC"}]``).
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
            "trackable": trackable,
            "indexable": indexable,
            "writable": True,
            "queueable": queueable,
            "softDeleteable": recycle_bin,
            "peerReplicable": multi_tenancy,
            "uniqueConstraint": record_uniqueness or [],
            "defaultSort": default_sort or [],
            "system": False,
            "attributes": fields,
        }
        payload.update(opts)
        created = self.client.post(_STAGING, data=payload, params=_REL)
        if create_view_templates:
            self.create_view_templates(module)
        return created

    def _put_attributes(
        self, mod: dict[str, Any], attributes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """PUT only the ``attributes`` of a staging record (a full-record PUT is
        rejected — the GET payload carries read-only ``@id``/``@context`` keys)."""
        return self.client.put(
            f"{_STAGING}/{mod['uuid']}", data={"attributes": attributes}, params=_REL
        )

    # friendly setting name -> metadata key, for set_module_settings
    _MODULE_SETTING_KEYS = {
        "ownable": "ownable",
        "trackable": "trackable",
        "indexable": "indexable",
        "taggable": "taggable",
        "queueable": "queueable",
        "recycle_bin": "softDeleteable",
        "multi_tenancy": "peerReplicable",
        "display_template": "displayName",
        "default_sort": "defaultSort",
    }

    def set_module_settings(self, module: str, **settings: Any) -> dict[str, Any]:
        """Edit a staged module's **Additional Settings** (and display template / sort).

        Accepts the same friendly names as :meth:`create_module` — ``ownable``,
        ``trackable``, ``indexable``, ``taggable``, ``queueable``, ``recycle_bin``,
        ``multi_tenancy``, ``display_template``, ``default_sort`` — and PUTs the changed
        keys to staging. ``ownable`` also syncs ``userOwnable``. Change is staged until
        :meth:`publish`.

        Verification note: on appliances that auto-mirror staging to ``model_metadatas``
        on every write, the PUT's *response* may surface a sync error even though the
        staging row updated. This method therefore confirms the result by **re-reading
        staging** and only raises if a requested value did not actually take.
        """
        mod = self.get_staging(module)
        if not mod:
            raise ValueError(f"module {module!r} not found in staging")
        payload: dict[str, Any] = {}
        for friendly, value in settings.items():
            key = self._MODULE_SETTING_KEYS.get(friendly)
            if key is None:
                raise ValueError(f"unknown module setting {friendly!r}")
            payload[key] = value
            if friendly == "ownable":
                payload["userOwnable"] = value
        if not payload:
            return mod
        try:
            self.client.put(f"{_STAGING}/{mod['uuid']}", data=payload, params=_REL)
        except FortiSOARException:
            pass  # tolerate the staging->published auto-sync error; verify by re-read
        updated = self.get_staging(module) or {}
        not_applied = {k: v for k, v in payload.items() if updated.get(k) != v}
        if not_applied:
            raise FortiSOARException(
                f"module settings did not apply for {module!r}: {sorted(not_applied)}"
            )
        return updated

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

    def discard_staging_draft(self, module: str, *, drop_view_templates: bool = True) -> bool:
        """Discard a module's editable **staging draft** (``DELETE`` on staging). Returns
        False if no draft existed.

        This is the same call the in-product editor's "Revert" fires for an unpublished
        module (``DELETE /api/3/staging_model_metadatas/{uuid}``).

        ⚠️ **This only undoes an UNPUBLISHED draft — it is not a module delete.** FortiSOAR
        has no supported delete-module operation:

        - If the module was **never published**, this effectively removes it (no table was
          ever created).
        - If the module **was published**, the live module and its Postgres table REMAIN —
          you just orphan them by removing the draft, with **no API path to fully delete**
          them (requires backend CLI/SQL). Prefer leaving the draft in place.

        For a clean throwaway, do NOT publish it; then discarding the draft removes it.

        ``drop_view_templates`` (default True) also deletes the module's
        ``system_view_templates``. The UI's own revert leaves these orphaned; pyfsr cleans
        them so a discarded module leaves nothing behind.
        """
        lite = self._staging_lite(module)
        if not lite:
            return False
        self.client.delete(f"{_STAGING}/{lite['uuid']}")
        if drop_view_templates:
            for vt in self.get_view_templates(module):
                if vt.get("uuid"):
                    self.client.delete(f"{_VIEW_TEMPLATES}/{vt['uuid']}")
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
        text = (
            " ".join(
                str(getattr(exc, attr, "") or "") for attr in ("message", "error_type")
            ).lower()
            or str(exc).lower()
        )
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
                    f"publish did not complete within {timeout}s (last appliance state: {last_exc})"
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
