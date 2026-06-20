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
**storage type** (``type``) and a **UI widget** (``formType``). These are two different
axes and a correct field needs both:

- ``type`` is the Postgres column type the platform actually stores:
  ``string`` / ``integer`` / ``float`` / ``boolean`` / ``picklists`` / ``object`` /
  ``array`` or a *module type name* for a relationship (e.g. ``alerts``). **There is no
  ``text`` storage type** — text widgets store ``string``. Publishing a field whose ``type``
  is ``text`` fails validation ("Attribute type 'text' does not exist").
- ``formType`` is the editor widget: ``text`` / ``textarea`` / ``richtext`` / ``html`` /
  ``email`` / ``url`` / ``phone`` / ``domain`` / ``filehash`` / ``ipv4`` / ``ipv6`` /
  ``password`` / ``integer`` / ``decimal`` / ``datetime`` / ``checkbox`` / ``file`` /
  ``json`` / ``object`` / ``picklist`` / ``multiselectpicklist`` / ``lookup`` /
  ``manyToMany`` / ``oneToMany``.

Use the **typed builders** (:meth:`ModulesAdminAPI.text_field`,
:meth:`~ModulesAdminAPI.integer_field`, :meth:`~ModulesAdminAPI.datetime_field`,
:meth:`~ModulesAdminAPI.lookup_field`, ...) which set the right storage type for each
widget for you. :meth:`~ModulesAdminAPI.field` is the low-level escape hatch where you
pass both axes yourself. See :data:`WIDGET_STORAGE_TYPE` for the full widget→storage map.

For the field-type catalogue and relationship/reverse-field semantics from an authoring
perspective, see ``docs/source/guides/module-field-schema.md``.

Example::

    admin = client.modules_admin
    admin.create_module("widgets", label="Widget", fields=[
        admin.text_field("name", required=True),
        admin.text_field("payload", area=True),
        admin.integer_field("score"),
        admin.picklist_field("status", "WidgetStatus"),
        admin.relationship_field("relatedAlerts", "alerts"),  # many-to-many
    ])
    admin.publish()                      # appliance-wide commit
"""

from __future__ import annotations

import re
import time
import uuid as _uuid
from typing import TYPE_CHECKING, Any

from ..exceptions import FortiSOARException, describe_migrate_failure, is_migrate_transient
from .base import BaseAPI

if TYPE_CHECKING:
    from ..models import AttributeMetadata, PublishedModelMetadata, StagingModelMetadata

_STAGING = "/api/3/staging_model_metadatas"
_PUBLISHED = "/api/3/model_metadatas"
_PUBLISH = "/api/publish"
# After a publish is kicked off, ``/api/3`` (the API entrypoint) returns 503 for the whole
# backup + migrate window and 200 once it completes — the same signal the in-product UI
# polls. ``/api/publish/error`` reports the *last* publish's outcome
# (``{"status": "Success"|..., "last_publish_time": <epoch>}``); a fresh ``last_publish_time``
# with ``status == "Success"`` means this publish committed, any other status is a failure.
_ENTRYPOINT = "/api/3"
_PUBLISH_ERROR = "/api/publish/error"
_VIEW_TEMPLATES = "/api/3/system_view_templates"
_VIEW_TEMPLATES_BULK = "/api/3/bulkupsert/system_view_templates"
_REL = {"$relationships": "true"}
_ALL = {"$limit": 2147483647}

# UI widget (``formType``) -> storage column type (``type``). This is the mapping the
# in-product editor applies under the hood: many distinct widgets all store ``string``,
# datetime stores an epoch ``integer``, a checkbox stores ``boolean``, etc. Relationship
# widgets (lookup/manyToMany/oneToMany) store the *target module type* and are handled by
# the relationship builders, so they are intentionally absent here.
WIDGET_STORAGE_TYPE: dict[str, str] = {
    "text": "string",
    "textarea": "string",
    "richtext": "string",
    "html": "string",
    "email": "string",
    "url": "string",
    "phone": "string",
    "password": "string",
    "filehash": "string",
    "ipv4": "string",
    "ipv6": "string",
    "domain": "string",
    "file": "string",
    "integer": "integer",
    "decimal": "float",  # the Decimal Field widget stores a 'float' column
    "datetime": "integer",  # stored as an epoch-millis integer
    "checkbox": "boolean",
    "picklist": "picklists",
    "multiselectpicklist": "picklists",
    "json": "object",  # the JSON widget; a distinct widget from the raw 'object' one
    "object": "object",
    "array": "array",
}

# A field's ``name`` is its immutable **API key** — it must start with a letter and contain
# only letters, digits, or underscores (it becomes a DB column / JSON key). The appliance
# accepts spaces/special chars into *staging* but then fails the **publish** migrate, so we
# reject them up front to avoid the slow, appliance-wide round-trip. A module ``type`` is a
# table name and is additionally lower-cased by convention.
_FIELD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_NAME_LEN = 63  # Postgres identifier limit

# Non-existent storage types people reach for by habit; both fail the publish validator
# ("Attribute type 'text' does not exist"). Map each to the right builder/storage type.
_BOGUS_DB_TYPES = {
    "text": "a text widget stores 'string' — use text_field()/typed_field(), or db_type='string'",
    "json": "JSON stores 'object' — use object_field(), or db_type='object'",
    "datetime": "datetime stores an epoch 'integer' — use datetime_field()",
    "date": "dates store an epoch 'integer' — use datetime_field()",
    "bool": "booleans store 'boolean' — use checkbox_field()",
}


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

    def get_staging(
        self, module: str, *, typed: bool = False
    ) -> dict[str, Any] | StagingModelMetadata | None:
        """Return the full staging metadata record (incl. ``attributes``) for ``module``.

        ``module`` is the module ``type`` (or plural ``module`` name). Returns None if no
        staging record exists. Pass ``typed=True`` for a
        :class:`~pyfsr.models.StagingModelMetadata`.
        """
        lite = self._staging_lite(module)
        if not lite:
            return None
        raw = self.client.get(f"{_STAGING}/{lite['uuid']}", params=_REL)
        if typed and raw:
            from ..models import StagingModelMetadata

            return StagingModelMetadata(**raw)
        return raw

    def get_published(
        self, module: str, *, typed: bool = False
    ) -> dict[str, Any] | PublishedModelMetadata | None:
        """Return the full *published* metadata record for ``module``, or None.

        None means the module has never been published (it may still exist in staging).
        Pass ``typed=True`` for a :class:`~pyfsr.models.PublishedModelMetadata`.
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
        raw = self.client.get(f"{_PUBLISHED}/{lite['uuid']}", params=_REL)
        if typed and raw:
            from ..models import PublishedModelMetadata

            return PublishedModelMetadata(**raw)
        return raw

    def is_published(self, module: str) -> bool:
        """True if ``module`` exists in the published schema (``model_metadatas``).

        Note: on appliances that auto-mirror staging into ``model_metadatas`` on every
        write (e.g. the dev-mode schema toggle), this can read ``True`` for a module you
        have not explicitly :meth:`publish`-ed. Use :meth:`pending_changes` to see what is
        genuinely uncommitted.
        """
        return self.get_published(module) is not None

    def get_field(
        self, module: str, field: str, *, typed: bool = False
    ) -> dict[str, Any] | AttributeMetadata | None:
        """Return one staged attribute (field) dict by ``name``, or None.

        Pass ``typed=True`` for an :class:`~pyfsr.models.AttributeMetadata`.
        """
        mod = self.get_staging(module)
        if not mod:
            return None
        raw = next((a for a in mod.get("attributes", []) if a.get("name") == field), None)
        if typed and raw:
            from ..models import AttributeMetadata

            return AttributeMetadata(**raw)
        return raw

    def reverse_field(
        self, source_module: str, source_field: str, *, published: bool = False
    ) -> dict[str, Any] | None:
        """Resolve the reverse (inverse) attribute on the *target* of a relationship.

        Given ``source_field`` on ``source_module`` (a ``manyToMany`` / ``oneToMany`` /
        ``lookup``), return the matching attribute on the target module that points back —
        or ``None`` if the platform did not create one. Use this to confirm whether a
        relationship's reverse field was auto-created (it is **not** always — see
        :meth:`relationship_field`). Reads staging by default; pass ``published=True`` to
        check the live schema after :meth:`publish`.

        Resolution: the source field's ``type`` is the target module; its ``inversedField``
        (or, for an un-customised many-to-many, this module's name) is the expected reverse
        field name.
        """
        getter = self.get_published if published else self.get_staging
        src = self.get_field(source_module, source_field)
        if not src:
            return None
        target = src.get("type")
        inverse_name = src.get("inversedField") or source_module
        target_mod = getter(target)
        if not target_mod:
            return None
        return next(
            (a for a in target_mod.get("attributes", []) if a.get("name") == inverse_name),
            None,
        )

    # ------------------------------------------------------------- helpers
    @staticmethod
    def field(
        name: str,
        *,
        db_type: str = "string",
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

        - ``db_type`` — **storage** type (``string``/``integer``/``float``/``boolean``/
          ``picklists``/``object``/``array`` or a target module type); ``form_type`` is the
          UI widget (defaults to ``db_type``). Prefer the typed builders (:meth:`text_field` etc.) —
          they pick the right pair; ``"text"``/``"json"`` are widgets, not storage types,
          and are rejected here.
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

        Raises:
            ValueError: if ``name`` is not a valid API key, if ``db_type`` is a non-existent
                storage type (e.g. ``"text"``), or if ``encrypted`` and ``searchable`` are
                both set — all of which the appliance would only reject at publish time.
        """
        if not isinstance(name, str) or not _FIELD_NAME_RE.match(name):
            raise ValueError(
                f"invalid field name {name!r}: a field API key must start with a letter and "
                "contain only letters, digits, or underscores (no spaces or punctuation)"
            )
        if len(name) > _MAX_NAME_LEN:
            raise ValueError(f"field name {name!r} exceeds {_MAX_NAME_LEN} characters")
        if db_type in _BOGUS_DB_TYPES:
            raise ValueError(
                f"db_type={db_type!r} is not a storage type: {_BOGUS_DB_TYPES[db_type]}"
            )
        if encrypted and searchable:
            raise ValueError(f"field {name!r} cannot be both encrypted and searchable — pick one")
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

    # ------------------------------------------------ typed scalar builders
    @classmethod
    def typed_field(
        cls, name: str, form_type: str, *, label: str | None = None, **opts: Any
    ) -> dict[str, Any]:
        """Build a scalar field by **widget**, deriving the storage ``type`` for you.

        ``form_type`` is any key of :data:`WIDGET_STORAGE_TYPE` (``text``, ``datetime``,
        ``checkbox``, ``email``, ...). This is the recommended way to build non-relationship
        fields — it guarantees ``type``/``formType`` agree, avoiding the
        "Attribute type 'text' does not exist" publish error you get from hand-setting
        ``db_type``. For relationships/picklists use the dedicated builders instead.
        """
        db_type = WIDGET_STORAGE_TYPE.get(form_type)
        if db_type is None:
            raise ValueError(
                f"unknown scalar widget {form_type!r}; use a key of WIDGET_STORAGE_TYPE, "
                "or picklist_field / lookup_field / relationship_field for non-scalars"
            )
        return cls.field(name, db_type=db_type, form_type=form_type, label=label, **opts)

    @classmethod
    def text_field(
        cls,
        name: str,
        *,
        area: bool = False,
        rich: bool = False,
        html: bool = False,
        label: str | None = None,
        **opts: Any,
    ) -> dict[str, Any]:
        """Build a string field: single-line (default), ``textarea``, ``richtext`` or
        ``html``. ``area``/``rich``/``html`` pick the widget (all store ``string``)."""
        widget = "html" if html else "richtext" if rich else "textarea" if area else "text"
        return cls.typed_field(name, widget, label=label, **opts)

    @classmethod
    def integer_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build an integer field (``integer`` storage, ``integer`` widget)."""
        return cls.typed_field(name, "integer", label=label, **opts)

    @classmethod
    def decimal_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a Decimal Field (``float`` storage, ``decimal`` widget) for fractional
        numbers — the floating-point counterpart of :meth:`integer_field`."""
        return cls.typed_field(name, "decimal", label=label, **opts)

    @classmethod
    def datetime_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a date/time field. Stored as an epoch-millis ``integer`` behind a
        ``datetime`` widget — that storage type is intentional, not a bug."""
        return cls.typed_field(name, "datetime", label=label, **opts)

    @classmethod
    def checkbox_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a boolean checkbox field (``boolean`` storage, ``checkbox`` widget)."""
        return cls.typed_field(name, "checkbox", label=label, **opts)

    # alias: the editor labels this widget "checkbox"; "boolean" reads naturally too
    boolean_field = checkbox_field

    @classmethod
    def email_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build an email field (``string`` storage, ``email`` widget with email validation)."""
        return cls.typed_field(name, "email", label=label, **opts)

    @classmethod
    def url_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a URL field (``string`` storage, ``url`` widget)."""
        return cls.typed_field(name, "url", label=label, **opts)

    @classmethod
    def phone_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a phone field (``string`` storage, ``phone`` widget)."""
        return cls.typed_field(name, "phone", label=label, **opts)

    @classmethod
    def domain_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a Domain field (``string`` storage, ``domain`` widget)."""
        return cls.typed_field(name, "domain", label=label, **opts)

    @classmethod
    def ipv4_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build an IPv4 field (``string`` storage, ``ipv4`` widget)."""
        return cls.typed_field(name, "ipv4", label=label, **opts)

    @classmethod
    def ipv6_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build an IPv6 field (``string`` storage, ``ipv6`` widget)."""
        return cls.typed_field(name, "ipv6", label=label, **opts)

    @classmethod
    def filehash_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a FileHash field (``string`` storage, ``filehash`` widget)."""
        return cls.typed_field(name, "filehash", label=label, **opts)

    @classmethod
    def file_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a file-attachment field (``string`` storage, ``file`` widget)."""
        return cls.typed_field(name, "file", label=label, **opts)

    @classmethod
    def password_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a masked password field. Pass ``encrypted=True`` to store it encrypted
        at rest (encrypted fields cannot be ``searchable``)."""
        return cls.typed_field(name, "password", label=label, **opts)

    @classmethod
    def json_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a JSON field (``object`` storage, ``json`` widget) — the editor's "JSON"
        type with a JSON editor control. See also :meth:`object_field` (the raw ``object``
        widget): both store ``object`` and differ only in the UI control."""
        return cls.typed_field(name, "json", label=label, **opts)

    @classmethod
    def object_field(cls, name: str, *, label: str | None = None, **opts: Any) -> dict[str, Any]:
        """Build a raw object field (``object`` storage, ``object`` widget). For the editor's
        "JSON" field type (a JSON editor control) use :meth:`json_field` instead."""
        return cls.typed_field(name, "object", label=label, **opts)

    # ---------------------------------------------------- relationship/refs
    @classmethod
    def lookup_field(
        cls,
        name: str,
        target_module: str,
        *,
        label: str | None = None,
        ownable_filter: bool = False,
        owning_module: str | None = None,
        **opts: Any,
    ) -> dict[str, Any]:
        """Build a **lookup** (many-to-one) field: a *single* reference to one record of
        ``target_module``.

        Unlike :meth:`relationship_field`, a lookup is **not a collection** and does **not**
        own a relationship, so the platform creates **no reverse field** on the target
        module — it is a one-directional pointer. Two lookups from different modules to the
        same target are independent.

        A lookup is also what a ``oneToMany`` relationship needs on its *target* side: a
        ``oneToMany`` field will not publish unless a matching lookup already exists on the
        target module (see :meth:`relationship_field`).

        ``ownable_filter`` adds the ``isOwnable``/``modulePermissions`` ``dataSourceFilters``
        the in-product editor sets for team-ownable lookups (``owning_module`` defaults to
        the module being edited; set it when building fields ahead of the module).
        """
        attr = cls.field(name, db_type=target_module, form_type="lookup", label=label, **opts)
        attr["collection"] = False
        attr["ownsRelationship"] = False
        attr["dataSource"] = {"model": target_module}
        if ownable_filter:
            attr["dataSourceFilters"] = {
                "isOwnable": True,
                "modulePermissions": owning_module,
                "modulePermissionsType": {"canUpdate": True, "canRead": True},
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
        inversed_field: str | None = None,
        owns_relationship: bool = True,
        **opts: Any,
    ) -> dict[str, Any]:
        """Build a **collection** relationship to ``target_module`` (its module ``type``).

        ``many`` selects ``manyToMany`` (default) vs ``oneToMany``; both are collections.

        **Reverse-field behavior** (the part that "sometimes" creates a field on the other
        module — verify it with :meth:`reverse_field` after :meth:`publish`):

        - ``manyToMany`` with the **default** inverse (``inversed_field=None``): the editor
          auto-creates a reverse many-to-many field on ``target_module`` named after *this*
          module, wired at staging time. This is the common "it auto-created the field" case.
        - ``manyToMany`` with a **custom** ``inversed_field``: the reverse field is **not**
          auto-created — you must add it to the target yourself (another
          ``relationship_field`` pointing back). This is the common "it did *not* create the
          field" case.
        - ``oneToMany``: requires a matching **lookup** (many-to-one) field to already exist
          on ``target_module`` (its name = ``inversed_field``). Publish fails with
          "there is no lookup field present in '<target>'" if it is missing — create it with
          :meth:`lookup_field` on the target *before* publishing.

        ``owns_relationship`` (default True) marks this as the owning side of the join.
        Pass ``owns_relationship=False`` for the non-owning mirror of an existing relation.
        """
        attr = cls.field(
            name,
            db_type=target_module,
            form_type="manyToMany" if many else "oneToMany",
            label=label,
            **opts,
        )
        attr["collection"] = True
        attr["ownsRelationship"] = owns_relationship
        if inversed_field is not None:
            attr["inversedField"] = inversed_field
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

    def find_invalid_drafts(self, *, deep: bool = False) -> list[dict[str, Any]]:
        """Scan **staging** for drafts whose names would break the next publish.

        Because :meth:`publish` is appliance-wide, a single staged module or field with an
        illegal identifier (e.g. a module ``9probe`` or a field ``"bad name"`` added through
        the UI) makes the whole publish fail mid-migrate with a cryptic Postgres error like
        ``syntax error, unexpected integer "9", expecting identifier`` — and the error does
        **not** name the offender. Run this first to find it.

        Returns one entry per problem: ``{"module", "uuid", "problem"}`` (and ``"field"`` for
        a bad attribute). ``deep=False`` (default) checks only module names — one cheap list
        read; ``deep=True`` also fetches every draft's attributes to validate field names
        (one read per module). An empty list means nothing staged would fail name validation.

        pyfsr's own builders reject these inputs up front, so a hit here is typically a draft
        created in the in-product editor or by another tool.
        """
        problems: list[dict[str, Any]] = []
        members = (self.client.get(_STAGING, params=_ALL) or {}).get("hydra:member", [])
        for m in members:
            t = m.get("type") or ""
            uuid = m.get("uuid")
            if not _MODULE_NAME_RE.match(t):
                problems.append({"module": t, "uuid": uuid, "problem": "invalid module name"})
            elif len(t) > _MAX_NAME_LEN:
                problems.append({"module": t, "uuid": uuid, "problem": "module name too long"})
            if not deep:
                continue
            full = self.client.get(f"{_STAGING}/{uuid}", params=_REL) or {}
            for a in full.get("attributes", []) or []:
                n = a.get("name") or ""
                if not _FIELD_NAME_RE.match(n):
                    problems.append(
                        {"module": t, "uuid": uuid, "field": n, "problem": "invalid field name"}
                    )
        return problems

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
        ``record_uniqueness`` is a list of field names whose combined value must be
        unique per record (the editor's "Make Records Unique"). Pass plain field names
        (e.g. ``["name"]`` or ``["sourceIp", "sourcePort"]``); pyfsr builds the platform's
        ``uniqueConstraint`` object shape for you. The unique index is created when the
        module is **published** (it is a DB migration), not at staging time.
        ``default_sort`` is the default sort spec (e.g. ``[{"field": "createDate",
        "direction": "DESC"}]``).
        """
        if not isinstance(module, str) or not _MODULE_NAME_RE.match(module):
            raise ValueError(
                f"invalid module name {module!r}: a module type must start with a lowercase "
                "letter and contain only lowercase letters, digits, or underscores "
                "(e.g. 'customwidgets', 'threat_reports')"
            )
        if len(module) > _MAX_NAME_LEN:
            raise ValueError(f"module name {module!r} exceeds {_MAX_NAME_LEN} characters")
        if fields is not None and not fields:
            raise ValueError(
                "a module needs at least one field; pass fields=None for a default 'name'"
            )
        label = label or module
        if fields is None:
            fields = [self.text_field("name", required=True)]
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
            "uniqueConstraint": self._unique_constraint(module, record_uniqueness),
            "defaultSort": default_sort or [],
            "system": False,
            "attributes": fields,
        }
        payload.update(opts)
        created = self.client.post(_STAGING, data=payload, params=_REL)
        if create_view_templates:
            self.create_view_templates(module)
        return created

    def get_staging_typed(self, module: str) -> StagingModelMetadata | None:
        """Typed convenience wrapper for :meth:`get_staging` — always returns
        a :class:`~pyfsr.models.StagingModelMetadata` (or None)."""
        return self.get_staging(module, typed=True)  # type: ignore[return-value]

    def get_published_typed(self, module: str) -> PublishedModelMetadata | None:
        """Typed convenience wrapper for :meth:`get_published` — always returns
        a :class:`~pyfsr.models.PublishedModelMetadata` (or None)."""
        return self.get_published(module, typed=True)  # type: ignore[return-value]

    @staticmethod
    def _unique_constraint(module: str, fields: list[str] | None) -> list[dict[str, Any]]:
        """Build the platform's ``uniqueConstraint`` value from plain field names.

        FortiSOAR does NOT store record-uniqueness as a flat list of field names; it
        expects a list of named constraint objects keyed by ``<table>_unique``::

            [{"alerts_unique": {"columns": ["name", "source"]}}]

        (confirmed against live module metadata). Passing a bare ``["name"]`` — as pyfsr
        did before — is silently ignored and no unique index is ever created. Returns
        ``[]`` for an empty/None field list (uniqueness off).
        """
        if not fields:
            return []
        return [{f"{module}_unique": {"columns": list(fields)}}]

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
        ``multi_tenancy``, ``display_template``, ``default_sort``, ``record_uniqueness``
        — and PUTs the changed keys to staging. ``ownable`` also syncs ``userOwnable``.
        Change is staged until :meth:`publish`.

        ``record_uniqueness`` takes plain field names (e.g. ``["name"]``) and is converted
        to the platform's ``uniqueConstraint`` object shape via the ``_unique_constraint`` helper.
        ⚠️ Note: changing record-uniqueness on an **already-published** module requires a
        DB migration that only runs on :meth:`publish`; the staging PUT records the intent
        but the unique index is (re)built at publish time, and removing an existing
        constraint may not drop the live index without backend action.

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
            if friendly == "record_uniqueness":
                payload["uniqueConstraint"] = self._unique_constraint(module, value)
                continue
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

    def remove_field(self, module: str, field: str, *, missing_ok: bool = False) -> dict[str, Any]:
        """Remove a field from ``module`` in **staging** (staged until :meth:`publish`).

        The inverse of :meth:`add_field`. Removing a **relationship** field also removes its
        join behaviour on publish; removing the field that another module's relationship was
        *inverse* to is the first step of :meth:`delete_module` (a dangling reverse field
        fails the publish validator with "Attribute type '<module>' does not exist").

        Raises ``ValueError`` if the field is absent, unless ``missing_ok`` is True.
        """
        mod = self.get_staging(module)
        if not mod:
            raise ValueError(f"module {module!r} not found in staging")
        attrs = mod.get("attributes", [])
        kept = [a for a in attrs if a.get("name") != field]
        if len(kept) == len(attrs):
            if missing_ok:
                return mod
            raise ValueError(f"field {field!r} not found on module {module!r}")
        return self._put_attributes(mod, kept)

    def find_relationship_referrers(self, module: str) -> list[tuple[str, list[str]]]:
        """Return every *staged* module that has a relationship field pointing **at**
        ``module``, as ``[(referrer_type, [field_names]), ...]``.

        A relationship attribute stores its target module in ``type``; this scans all staged
        modules for attributes whose ``type`` equals ``module``. These are the reverse fields
        (e.g. the ``alerts`` field auto-created on a many-to-many target) that must be removed
        **before** the module can be deleted — otherwise :meth:`publish` rejects the delete
        synchronously ("Attribute type '<module>' does not exist as core or custom model
        metadata"). Used by :meth:`delete_module`.
        """
        want = module.strip().lower()
        data = self.client.get(_STAGING, params={**_ALL, **_REL})
        out: list[tuple[str, list[str]]] = []
        for m in (data or {}).get("hydra:member", []):
            if str(m.get("type", "")).lower() == want:
                continue  # the module's own fields are going away with it
            hits = [
                a.get("name")
                for a in (m.get("attributes") or [])
                if str(a.get("type", "")).lower() == want and a.get("name")
            ]
            if hits:
                out.append((m.get("type"), hits))
        return out

    def delete_module(
        self,
        module: str,
        *,
        detach_relationships: bool = False,
        publish: bool = True,
        drop_view_templates: bool = True,
        drop_orphan_tables: Any | None = None,
        timeout: float = 600.0,
        poll_interval: float = 10.0,
    ) -> dict[str, Any]:
        """Delete a module — the **only** API path that actually removes one, verified live.

        FortiSOAR exposes **no** ``DELETE`` on the published endpoint
        (``DELETE /api/3/model_metadatas/{uuid}`` → 405 *Method Not Allowed, Allow: GET*).
        The real mechanism, confirmed against a live appliance, is:

        1. **Detach reverse relationships.** Any other module with a relationship field
           pointing at ``module`` (see :meth:`find_relationship_referrers`) must have that
           field removed first, or the publish below fails synchronously with
           "Attribute type '<module>' does not exist as core or custom model metadata".
        2. **Discard the module's own staging draft** (``DELETE`` on staging), leaving a
           *published-without-staging* record.
        3. **Publish.** The appliance-wide migrate then drops the module: its
           ``model_metadatas`` / ``staging_model_metadatas`` rows and ``attribute_metadatas``
           (including the reverse fields detached in step 1) all disappear and the module
           vanishes from the API.

        ⚠️ **The physical Postgres tables are NOT dropped.** Verified: after the module is
        gone from the API, its base table and its relationship/ownership **join** tables
        (e.g. ``<table>``, ``<table>_<target>``, ``<table>_team``, ``<table>_actor``) remain
        as orphans. pyfsr cannot drop them over the API. They are harmless **except** that a
        future module reusing the same ``tableName`` collides on the leftover index names
        (Postgres ``42P07``) and wedges that publish — reclaim them with a backend
        ``DROP TABLE ... CASCADE`` if you intend to recreate the module. The returned
        ``orphan_table`` names the base table for this cleanup.

        Args:
            detach_relationships: If reverse fields point at this module, remove them first.
                Defaults to **False** — with referrers present the method **refuses** (raising
                with the list) rather than silently editing other modules. Set True to proceed.
            publish: If True (default) run the appliance-wide :meth:`publish` to commit the
                delete. If False, the staging changes are staged but the module is not yet
                gone (you must publish later).
            drop_view_templates: Also delete the module's ``system_view_templates``.
            drop_orphan_tables: Optional :class:`pyfsr.cli.appliance.Facts` (an
                appliance transport context). When given **and** ``publish`` is
                True, the orphaned physical Postgres tables (base + join tables)
                are dropped with ``DROP TABLE ... CASCADE`` after the publish
                commits the delete — the only way to fully reclaim a module's
                ``tableName`` so a future module can reuse it. Left None, the
                tables are reported in ``orphan_table`` but not dropped (the API
                cannot touch them; see the warning above).
            timeout: Passed to :meth:`publish`.
            poll_interval: Passed to :meth:`publish`.

        Returns:
            A dict with keys ``module``, ``detached`` (list), ``orphan_table``
            (``str`` or ``None``), ``published`` (publish result or ``None``),
            and ``dropped_tables`` (``list`` or ``None``).

        Raises:
            ValueError: if the module is not found.
            FortiSOARException: if referrers exist and ``detach_relationships`` is False.
        """
        pub = self.get_published(module)
        stg = self.get_staging(module)
        if not pub and not stg:
            raise ValueError(f"module {module!r} not found in staging or published")
        source = pub or stg
        orphan_table = source.get("tableName") or source.get("module") or module

        referrers = self.find_relationship_referrers(module)
        if referrers and not detach_relationships:
            listing = "; ".join(f"{t}: {', '.join(fs)}" for t, fs in referrers)
            raise FortiSOARException(
                f"cannot delete {module!r}: other modules have relationship fields pointing "
                f"at it ({listing}). Publishing the delete would fail validation. Pass "
                f"detach_relationships=True to remove these reverse fields first, or remove "
                f"them yourself with remove_field()."
            )

        detached: list[str] = []
        for ref_type, fields in referrers:
            for f in fields:
                self.remove_field(ref_type, f, missing_ok=True)
                detached.append(f"{ref_type}.{f}")

        if stg:
            self.discard_staging_draft(module, drop_view_templates=drop_view_templates)

        result: dict[str, Any] = {
            "module": module,
            "detached": detached,
            "orphan_table": orphan_table,
            "published": None,
            "dropped_tables": None,
        }
        if publish:
            result["published"] = self.publish(timeout=timeout, poll_interval=poll_interval)
            if drop_orphan_tables is not None and orphan_table:
                # Lazy import: keeps the API client free of the CLI/SSH/psql layer
                # unless an appliance context is actually handed in.
                from ..cli.appliance import db as _appliance_db

                drop = _appliance_db.drop_module_tables(drop_orphan_tables, orphan_table, yes=True)
                result["dropped_tables"] = drop["dropped"]
        return result

    def discard_staging_draft(self, module: str, *, drop_view_templates: bool = True) -> bool:
        """Discard a module's editable **staging draft** (``DELETE`` on staging). Returns
        False if no draft existed.

        This is the same call the in-product editor's "Revert" fires for an unpublished
        module (``DELETE /api/3/staging_model_metadatas/{uuid}``).

        ⚠️ **This only undoes the draft — on its own it is not a module delete.**

        - If the module was **never published**, this effectively removes it (no table was
          ever created).
        - If the module **was published**, discarding the draft leaves a
          *published-without-staging* record. On the **next** :meth:`publish` that record is
          deleted (the module disappears from the API) — so discarding a published module's
          draft and then publishing **does** delete it. To do this safely (detach reverse
          relationships first, and understand that the physical Postgres tables are left
          orphaned), use :meth:`delete_module` rather than calling this directly.

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

    _is_publish_transient = staticmethod(is_migrate_transient)

    def _last_publish_time(self) -> int | None:
        """Best-effort read of the last publish's ``last_publish_time`` (epoch), or None."""
        try:
            body = self.client.get(_PUBLISH_ERROR)
        except Exception:
            return None
        return body.get("last_publish_time") if isinstance(body, dict) else None

    def publish(
        self,
        *,
        timeout: float = 600.0,
        poll_interval: float = 10.0,
        precheck: bool = True,
    ) -> dict[str, Any]:
        """Commit **all** pending staged schema changes to live (``PUT /api/publish``).

        ⚠️ Appliance-wide: this publishes every pending change in staging across the whole
        instance, not just modules you touched. On a shared appliance, confirm nothing else
        is mid-edit before calling.

        Because it is appliance-wide, **one** illegally-named staged draft anywhere (e.g. a
        module ``9probe`` created in the UI) makes the whole publish fail mid-migrate with a
        cryptic Postgres error (``syntax error, unexpected integer "9", expecting identifier``)
        — and ``/api/publish/error`` may even still report ``Success`` while nothing actually
        commits. To avoid that, ``precheck`` (default True) runs
        :meth:`find_invalid_drafts` first and raises a clear, named ``ValueError`` *before*
        the destructive PUT. Set ``precheck=False`` to skip it.

        **Always synchronous.** The PUT only *starts* the publish (the response is
        ``{"status": "started"}``); the backup + DB migrate + commit then runs server-side,
        during which the **entire API — including ``/api/3`` — returns 503**. Since the whole
        appliance is unusable for that window there is nothing a caller could do concurrently,
        so this method blocks: it waits out the outage and confirms the result via
        ``/api/publish/error``, returning only once the schema is actually live (or raising).

        Note that *schema validation* errors (e.g. a relationship with no matching lookup on
        the target, or a field whose ``type`` does not exist) are returned **synchronously**
        as an :class:`~pyfsr.exceptions.APIError` on the PUT itself, before any migrate runs.
        A :class:`~pyfsr.exceptions.FortiSOARException` from :meth:`publish` therefore carries
        the appliance's own validation message — surface it verbatim to the user.

        Args:
            timeout: Max seconds to wait for the publish to complete.
            poll_interval: Seconds between readiness probes.
            precheck: If True (default), refuse to publish when any staged draft has an
                invalid module name (see :meth:`find_invalid_drafts`).

        Returns:
            The final ``/api/publish/error`` body
            (``{"status": "Success", "last_publish_time": ...}``).

        Raises:
            ValueError: if ``precheck`` finds an invalid staged draft that would wedge the
                appliance-wide migrate.
            FortiSOARException: if the PUT is rejected by schema validation, or if the
                publish finishes in any state other than ``Success``.
            TimeoutError: if the publish does not complete within ``timeout``.
        """
        if precheck:
            bad = self.find_invalid_drafts()
            if bad:
                names = ", ".join(f"{b['module']!r} ({b['problem']})" for b in bad)
                raise ValueError(
                    "refusing to publish: invalid staged draft(s) would fail the "
                    f"appliance-wide migrate: {names}. Fix or discard them "
                    "(discard_staging_draft), or pass precheck=False to override."
                )
        prev_time = self._last_publish_time()
        try:
            self.client.put(_PUBLISH, data={})
        except FortiSOARException as exc:
            # A 5xx body here is the migrate cycle already starting (the publish was
            # accepted); anything else — notably a 400 with a validation message — is a
            # real rejection the caller needs to see.
            if not self._is_publish_transient(exc):
                raise
        return self._wait_for_publish(prev_time, timeout, poll_interval)

    def _wait_for_publish(
        self, prev_time: int | None, timeout: float, poll_interval: float
    ) -> dict[str, Any]:
        """Block until the async publish finishes, using ``/api/publish/error`` as the truth.

        Polls ``/api/publish/error`` until ``last_publish_time`` advances past ``prev_time``.
        Returns the final body on ``status == "Success"``; raises
        :class:`~pyfsr.exceptions.FortiSOARException` with the appliance's reported
        status/error on any other terminal state, or :class:`TimeoutError` if it never
        reports back.

        While the migrate runs the **whole API is unstable** — ``/api/publish/error`` itself
        may return 503s, gateway errors, or unparseable bodies ("Unknown error occurred").
        Every such failure is treated as "still in progress, keep waiting": only a cleanly
        parsed 200 body is allowed to decide the outcome. That is why a publish error is
        reported from the JSON ``status`` field, never inferred from a failed poll.
        """
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        saw_outage = False
        while True:
            body: dict[str, Any] | None = None
            try:
                got = self.client.get(_PUBLISH_ERROR)
                body = got if isinstance(got, dict) else None
            except Exception:
                # API down / unparseable mid-migrate — the outage itself is the signal that
                # the publish is running; keep polling until it stabilises or we time out.
                saw_outage = True
            if body is not None:
                last = body
                status = str(body.get("status", "")).lower()
                # This publish is done once its outcome is fresh: either the timestamp moved
                # past what we captured, or we have ridden through the 503 migrate outage and
                # come out the other side (covers the case where ``prev_time`` was unreadable
                # — never trust a stale "Success" that predates this publish).
                fresh = body.get("last_publish_time") != prev_time or saw_outage
                if fresh and status == "success":
                    return body
                if fresh and status not in ("", "started", "in progress", "inprogress"):
                    raise FortiSOARException(
                        describe_migrate_failure(
                            body.get("status"), body.get("message") or body.get("error") or body
                        )
                    )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"publish did not complete within {timeout}s (last state: {last})"
                )
            time.sleep(poll_interval)
