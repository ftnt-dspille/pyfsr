"""Module editor: create modules, add/alter fields, publish.

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
**storage type** (``type``) and a **display type** (``formType``). These are two different
axes and a correct field needs both:

- ``type`` is the Postgres column type the platform actually stores:
  ``string`` / ``integer`` / ``float`` / ``boolean`` / ``picklists`` / ``object`` /
  ``array`` or a *module type name* for a relationship (e.g. ``alerts``). **There is no
  ``text`` storage type** — text-like fields store ``string``. Publishing a field whose
  ``type`` is ``text`` fails validation ("Attribute type 'text' does not exist").
- ``formType`` is the display type (the field kind shown in the editor): ``text`` /
  ``textarea`` / ``richtext`` / ``html`` / ``email`` / ``url`` / ``phone`` / ``domain`` /
  ``filehash`` / ``ipv4`` / ``ipv6`` / ``password`` / ``integer`` / ``decimal`` /
  ``datetime`` / ``checkbox`` / ``file`` / ``json`` / ``object`` / ``picklist`` /
  ``multiselectpicklist`` / ``lookup`` / ``manyToMany`` / ``oneToMany``.

Use the **typed builders** (:meth:`ModulesAdminAPI.text_field`,
:meth:`~ModulesAdminAPI.integer_field`, :meth:`~ModulesAdminAPI.datetime_field`,
:meth:`~ModulesAdminAPI.lookup_field`, ...) which set the right storage type for each
display type for you. :meth:`~ModulesAdminAPI.field` is the low-level escape hatch where you
pass both axes yourself. See :data:`DISPLAY_STORAGE_TYPE` for the full display-type→storage
map.

For the field-type catalogue and relationship/reverse-field semantics from an authoring
perspective, see ``docs/source/guides/module-field-schema.md``.

Read-only schema introspection is doctested against captured responses:

    >>> client = demo_client()
    >>> admin = client.modules_admin
    >>> admin.is_published("alerts")
    True
    >>> admin.pending_changes()          # fully-published box: nothing staged
    []

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
from typing import TYPE_CHECKING, Any, Literal

import requests

from ..exceptions import FortiSOARException, describe_migrate_failure, is_migrate_transient
from ..models import AttributeMetadata, NavItem, NavRequire, NavState
from ..utils.validation import is_uuid
from .base import BaseAPI

if TYPE_CHECKING:
    from ..models import (
        InvalidDraft,
        PendingChange,
        PublishedModelMetadata,
        StagingModelMetadata,
    )
    from ..query import Query

_STAGING = "/api/3/staging_model_metadatas"
_PUBLISHED = "/api/3/model_metadatas"
_PUBLISH = "/api/publish"
# After a publish is kicked off, ``/api/3`` (the API entrypoint) may return 503 during the
# backup + migrate window and 200 once it completes — the same signal the in-product UI
# polls. (On 7.6.x this 503 covers the whole window for every publish; on 8.0+ only
# structural changes disrupt a subset of surfaces — see :meth:`publish` for the measured
# blast radius.) ``/api/publish/error`` reports the *last* publish's outcome
# (``{"status": "Success"|..., "last_publish_time": <epoch>}``); a fresh ``last_publish_time``
# with ``status == "Success"`` means this publish committed, any other status is a failure.
_ENTRYPOINT = "/api/3"
_PUBLISH_ERROR = "/api/publish/error"
_REVERT = "/api/publish/revert"
#: Sentinel for "no prior errors captured" — distinct from a real ``errors`` value of None.
_UNSET = object()
_VIEW_TEMPLATES = "/api/3/system_view_templates"
_VIEW_TEMPLATES_BULK = "/api/3/bulkupsert/system_view_templates"
_REL = {"$relationships": "true"}
_ALL = {"$limit": 2147483647}

# Display type (``formType``) -> storage column type (``type``). This is the mapping the
# in-product editor applies under the hood: many distinct display types all store ``string``,
# datetime stores an epoch ``integer``, a checkbox stores ``boolean``, etc. Relationship
# display types (lookup/manyToMany/oneToMany) store the *target module type* and are handled
# by the relationship builders, so they are intentionally absent here.
DISPLAY_STORAGE_TYPE: dict[str, str] = {
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
    "decimal": "float",  # the Decimal Field display type stores a 'float' column
    "datetime": "integer",  # stored as an epoch-millis integer
    "checkbox": "boolean",
    "picklist": "picklists",
    "multiselectpicklist": "picklists",
    "json": "object",  # the JSON display type; distinct from the raw 'object' one
    "object": "object",
    "array": "array",
}

# Backward-compatible alias for the pre-0.x name (the constant was previously framed in
# terms of "widget"; "display type" reads clearer and matches the rest of the API).
WIDGET_STORAGE_TYPE = DISPLAY_STORAGE_TYPE

# Display types that should NOT default to a grid column. All six are grid columns 0% of
# the time across the captured module corpus (password = security; object/json/array =
# opaque blobs; manyToMany/oneToMany = collection relationships). Everything else defaults
# visible; override per-field with grid_column=True/False. See :meth:`ModulesAdminAPI.field`.
_NON_GRID_FORM_TYPES = frozenset({"password", "object", "json", "array", "manyToMany", "oneToMany"})

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
    "text": "a text field stores 'string' — use text_field()/typed_field(), or db_type='string'",
    "json": "JSON stores 'object' — use object_field(), or db_type='object'",
    "datetime": "datetime stores an epoch 'integer' — use datetime_field()",
    "date": "dates store an epoch 'integer' — use datetime_field()",
    "bool": "booleans store 'boolean' — use checkbox_field()",
}

# The "Only show users from selected teams" field option is new in FortiSOAR 8.0 — there is
# no equivalent on 7.6.x. It binds to ``dataSourceFilters.showTeams`` (bool) +
# ``dataSourceFilters.teams`` (a list of team IRIs); the engine adds a ``teams`` filter to the
# People lookup query so the picker only offers users on those teams. pyfsr refuses to stage it
# on an older appliance rather than silently shipping a no-op attribute.
_TEAM_SCOPE_MIN_VERSION = (8, 0, 0)
_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _parse_version(raw: str | dict[str, Any]) -> tuple[int, int, int] | None:
    """Parse a FortiSOAR version (``"8.0.0-6034"`` or ``{"version": ...}``) to ``(maj, min, patch)``.

    Tolerant of the several shapes :meth:`FortiSOAR.version` can return; the build suffix
    after ``-``/``+`` is ignored. Returns ``None`` when no ``N[.N[.N]]`` can be found, so the
    caller can decide how to treat an unknown version.
    """
    if isinstance(raw, dict):
        raw = raw.get("version") or raw.get("build") or raw.get("@version") or ""
    m = _VERSION_RE.search(str(raw))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


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

    def get_staging(self, module: str, *, typed: bool = False) -> dict[str, Any] | StagingModelMetadata | None:
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

    def get_published(self, module: str, *, typed: bool = False) -> dict[str, Any] | PublishedModelMetadata | None:
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
                if str(m.get("type", "")).lower() == want or str(m.get("module", "")).lower() == want
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

    def get_field(self, module: str, field: str, *, typed: bool = False) -> dict[str, Any] | AttributeMetadata | None:
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

    def reverse_field(self, source_module: str, source_field: str, *, published: bool = False) -> dict[str, Any] | None:
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

    def _reverse_attr_for(self, source_module: str, field: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """Compute the reverse-side attribute pyfsr should create on the target module.

        Returns ``(target_module, reverse_attribute)`` for relationship fields whose
        reverse side the platform does **not** auto-create — so pyfsr can create it and
        keep both sides of the relationship valid — or ``None`` when no explicit reverse
        is needed:

        - ``lookup`` (many-to-one): one-directional pointer, no reverse field exists.
        - ``manyToMany`` with the **default** inverse: FortiSOAR auto-creates the reverse,
          so pyfsr must not duplicate it.
        - ``manyToMany`` with a **custom** ``inversedField``: pyfsr builds the mirror
          ``manyToMany`` on the target pointing back.
        - ``oneToMany``: pyfsr builds the required ``lookup`` (many-to-one) on the target.
        """
        form_type = field.get("formType")
        target = field.get("type")
        name = field.get("name")
        if not target or not name:
            return None
        if form_type == "oneToMany":
            reverse_name = field.get("inversedField") or source_module
            reverse = self.lookup_field(reverse_name, source_module, inversedField=name)
            return target, self._to_field_dict(reverse)
        if form_type == "manyToMany" and field.get("inversedField"):
            reverse = self.relationship_field(
                field["inversedField"],
                source_module,
                many=True,
                inversed_field=name,
                owns_relationship=False,
            )
            return target, self._to_field_dict(reverse)
        # lookup / default-inverse manyToMany: nothing for pyfsr to create.
        return None

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _as_condition(value: bool | dict[str, Any] | Any) -> bool | dict[str, Any]:
        """Normalize a ``required``/``visibility`` value to the wire shape.

        Accepts a plain bool, an already-built FortiSOAR condition dict, or a
        :class:`~pyfsr.query.Query` — the latter is rendered to the
        ``{"logic": ..., "filters": [...]}`` envelope the editor uses for
        "Required/Visible by condition", so callers can build conditions with the
        same fluent DSL they query with instead of hand-assembling filter dicts.
        """
        from ..query import Query

        if isinstance(value, Query):
            return {"logic": value._logic, "filters": value._build_filters()}
        return value

    @classmethod
    def field(
        cls,
        name: str,
        *,
        db_type: str = "string",
        form_type: str | None = None,
        label: str | None = None,
        required: bool | dict[str, Any] | Query = False,
        searchable: bool = False,
        editable: bool = True,
        grid_column: bool | None = None,
        encrypted: bool = False,
        visibility: bool | dict[str, Any] | Query = True,
        default_value: Any = None,
        tooltip: str | None = None,
        minlength: int = 0,
        maxlength: int = 10485760,
        enable_range: bool = False,
        bulk_edit: bool = False,
        **extra: Any,
    ) -> AttributeMetadata:
        """Build an attribute (field) with sane defaults for create/add.

        Returns a typed :class:`~pyfsr.models.AttributeMetadata` (dict-compatible
        for reads — ``f["type"]`` / ``f.get(...)`` / ``"type" in f`` all work — so
        existing code that treated the result as a dict keeps working).

        Mirrors the Field **Properties** panel of the in-product editor:

        - ``db_type`` — **storage** type (``string``/``integer``/``float``/``boolean``/
          ``picklists``/``object``/``array`` or a target module type); ``form_type`` is the
          display type (defaults to ``db_type``). Prefer the typed builders (:meth:`text_field`
          etc.) — they pick the right pair; ``"text"``/``"json"`` are display types, not storage
          types, and are rejected here.
        - ``label`` — the **Field Title** (``name`` is the immutable **Field API Key**).
        - ``editable`` — UI "Editable" (maps to ``writeable``).
        - ``searchable`` / ``grid_column`` / ``encrypted`` — the **Field Options** row.
          ``grid_column`` (Default Grid Column) is **on by default** for scalar, lookup and
          picklist fields (visible in the list/grid view) and **off** for ``password``,
          ``object``/``json``/``array`` and collection relationships (``manyToMany``/
          ``oneToMany``) — the types that are never grid columns in practice. Override
          either way with ``grid_column=True/False``. Note: encrypted fields can't be
          searchable and vice-versa.
        - ``required`` — ``False`` / ``True``, a **condition** for "Required by condition",
          or a :class:`~pyfsr.query.Query` that pyfsr renders to the condition shape.
        - ``visibility`` — ``True`` (Visible) / ``False`` (Hidden), a **condition** for
          "Visible by Condition", or a :class:`~pyfsr.query.Query`.
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
            raise ValueError(f"db_type={db_type!r} is not a storage type: {_BOGUS_DB_TYPES[db_type]}")
        if encrypted and searchable:
            raise ValueError(f"field {name!r} cannot be both encrypted and searchable — pick one")
        validation: dict[str, Any] = {
            "required": cls._as_condition(required),
            "minlength": minlength,
            "maxlength": maxlength,
        }
        if enable_range:
            validation["_enableRange"] = True
        if grid_column is None:
            # Visible in the list/grid view by default — except for the types that are
            # never grid columns in practice (see _NON_GRID_FORM_TYPES). A caller can
            # still force either way with grid_column=True/False.
            grid_column = (form_type or db_type) not in _NON_GRID_FORM_TYPES
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
            "visibility": cls._as_condition(visibility),
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
        return AttributeMetadata.model_validate(attr)

    @classmethod
    def _field_dict(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Build a field as a plain, mutable wire dict (for post-processing builders).

        :meth:`field` now returns a typed (immutable-keyed) ``AttributeMetadata``;
        builders that need to mutate the result in place (picklist / lookup /
        relationship) build through this and re-wrap at the end. The dump is
        byte-identical to the pre-typing builder output (``exclude_unset`` emits
        only the keys ``field`` actually set, extras included).
        """
        return cls.field(*args, **kwargs).model_dump(by_alias=True, exclude_unset=True)

    @classmethod
    def picklist_field(
        cls,
        name: str,
        picklist_name: str,
        *,
        multi: bool = False,
        label: str | None = None,
        **opts: Any,
    ) -> AttributeMetadata:
        """Build a single- or multi-select **picklist** field bound to ``picklist_name``.

        ``picklist_name`` is the picklist's *list name* (e.g. ``"AlertStatus"``). ``multi``
        switches between ``picklist`` and ``multiselectpicklist`` (a collection). Pass
        through any :meth:`field` option (``required``, ``grid_column``, ...).
        """
        attr = cls._field_dict(
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
        return AttributeMetadata.model_validate(attr)

    # ------------------------------------------------ typed scalar builders
    @classmethod
    def typed_field(
        cls, name: str, display_type: str | None = None, *, label: str | None = None, **opts: Any
    ) -> AttributeMetadata:
        """Build a scalar field by **display type**, deriving the storage ``type`` for you.

        ``display_type`` is the kind of field as shown in the editor — any key of
        :data:`DISPLAY_STORAGE_TYPE` (``text``, ``datetime``, ``checkbox``, ``email``, ...).
        This is the recommended way to build non-relationship fields: it guarantees the
        storage ``type`` and ``formType`` agree, avoiding the "Attribute type 'text' does not
        exist" publish error you get from hand-setting ``db_type``. For relationships and
        picklists use the dedicated builders instead.

        (The argument was formerly named ``form_type``; ``display_type`` is the clearer name,
        and the legacy ``form_type=`` keyword is still accepted.)
        """
        if display_type is None:
            display_type = opts.pop("form_type", None)
        if display_type is None:
            raise ValueError("typed_field() requires a display type (e.g. 'text', 'datetime', 'email')")
        db_type = DISPLAY_STORAGE_TYPE.get(display_type)
        if db_type is None:
            raise ValueError(
                f"unknown display type {display_type!r}; use a key of DISPLAY_STORAGE_TYPE, "
                "or picklist_field / lookup_field / relationship_field for non-scalars"
            )
        return cls.field(name, db_type=db_type, form_type=display_type, label=label, **opts)

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
    ) -> AttributeMetadata:
        """Build a string field: single-line (default), ``textarea``, ``richtext`` or
        ``html``. ``area``/``rich``/``html`` pick the display type (all store ``string``)."""
        display_type = "html" if html else "richtext" if rich else "textarea" if area else "text"
        return cls.typed_field(name, display_type, label=label, **opts)

    @classmethod
    def integer_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build an integer field (stores ``integer``)."""
        return cls.typed_field(name, "integer", label=label, **opts)

    @classmethod
    def decimal_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a Decimal field (stores ``float``) for fractional numbers — the
        floating-point counterpart of :meth:`integer_field`."""
        return cls.typed_field(name, "decimal", label=label, **opts)

    @classmethod
    def datetime_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a date/time field. Stored as an epoch-millis ``integer`` — that storage
        type is intentional, not a bug."""
        return cls.typed_field(name, "datetime", label=label, **opts)

    @classmethod
    def checkbox_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a boolean checkbox field (stores ``boolean``)."""
        return cls.typed_field(name, "checkbox", label=label, **opts)

    # alias: the editor labels this field "checkbox"; "boolean" reads naturally too
    boolean_field = checkbox_field

    @classmethod
    def email_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build an email field (stores ``string``, with email-format validation)."""
        return cls.typed_field(name, "email", label=label, **opts)

    @classmethod
    def url_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a URL field (stores ``string``)."""
        return cls.typed_field(name, "url", label=label, **opts)

    @classmethod
    def phone_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a phone field (stores ``string``)."""
        return cls.typed_field(name, "phone", label=label, **opts)

    @classmethod
    def domain_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a Domain field (stores ``string``)."""
        return cls.typed_field(name, "domain", label=label, **opts)

    @classmethod
    def ipv4_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build an IPv4 field (stores ``string``)."""
        return cls.typed_field(name, "ipv4", label=label, **opts)

    @classmethod
    def ipv6_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build an IPv6 field (stores ``string``)."""
        return cls.typed_field(name, "ipv6", label=label, **opts)

    @classmethod
    def filehash_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a FileHash field (stores ``string``)."""
        return cls.typed_field(name, "filehash", label=label, **opts)

    @classmethod
    def file_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a file-attachment field (stores ``string``)."""
        return cls.typed_field(name, "file", label=label, **opts)

    @classmethod
    def password_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a masked password field. Pass ``encrypted=True`` to store it encrypted
        at rest (encrypted fields cannot be ``searchable``)."""
        return cls.typed_field(name, "password", label=label, **opts)

    @classmethod
    def json_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a JSON field (stores ``object``) — the editor's "JSON" type with a JSON
        editor control. See also :meth:`object_field` (the raw ``object`` type): both store
        ``object`` and differ only in the editor control."""
        return cls.typed_field(name, "json", label=label, **opts)

    @classmethod
    def object_field(cls, name: str, *, label: str | None = None, **opts: Any) -> AttributeMetadata:
        """Build a raw object field (stores ``object``). For the editor's "JSON" field type
        (a JSON editor control) use :meth:`json_field` instead."""
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
        team_scope: list[str] | None = None,
        **opts: Any,
    ) -> AttributeMetadata:
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

        ``team_scope`` (FortiSOAR **8.0+ only**, for ``target_module="people"``) is the
        editor's "Only show users from selected teams": pass the teams whose members may be
        picked — by name, bare uuid, or IRI — and the lookup only offers users on those teams.
        It sets ``dataSourceFilters.showTeams``/``teams``; :meth:`add_field` /
        :meth:`create_module` resolve the team identifiers to IRIs and **refuse to stage it on
        an appliance older than 8.0** (7.6.x has no equivalent). See :meth:`scope_field_to_teams`.
        """
        attr = cls._field_dict(name, db_type=target_module, form_type="lookup", label=label, **opts)
        attr["collection"] = False
        attr["ownsRelationship"] = False
        attr["dataSource"] = {"model": target_module}
        if ownable_filter:
            attr["dataSourceFilters"] = {
                "isOwnable": True,
                "modulePermissions": owning_module,
                "modulePermissionsType": {"canUpdate": True, "canRead": True},
            }
        if team_scope:
            cls._apply_team_scope(attr, team_scope)
        return AttributeMetadata.model_validate(attr)

    @staticmethod
    def _to_field_dict(field: dict[str, Any] | AttributeMetadata) -> dict[str, Any]:
        """Normalize a field (typed :class:`AttributeMetadata` or plain dict) to a wire dict.

        The staging consumers (:meth:`create_module` / :meth:`add_field` /
        :meth:`scope_field_to_teams`) mutate field dicts in place and POST them, so
        they coerce a typed field back to a plain dict at their boundary.
        ``exclude_unset`` keeps the body byte-identical to a hand-built dict.
        """
        if isinstance(field, AttributeMetadata):
            return field.model_dump(by_alias=True, exclude_unset=True)
        return field

    @staticmethod
    def _apply_team_scope(attr: dict[str, Any], teams: list[str]) -> None:
        """Set the ``dataSourceFilters.showTeams``/``teams`` pair on a People field in place.

        Stores the raw team identifiers as given; :meth:`_guard_team_scope` normalizes them to
        IRIs (and version-gates) at the staging boundary, where a client is available to resolve
        names. Merges into any existing ``dataSourceFilters`` (e.g. an ``ownable_filter``).
        """
        dsf = attr.setdefault("dataSourceFilters", {})
        dsf["showTeams"] = True
        dsf["teams"] = list(teams)

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
        team_scope: list[str] | None = None,
        **opts: Any,
    ) -> AttributeMetadata:
        """Build a **collection** relationship to ``target_module`` (its module ``type``).

        ``many`` selects ``manyToMany`` (default) vs ``oneToMany``; both are collections.

        ``team_scope`` (FortiSOAR **8.0+ only**, for ``target_module="people"``) is the
        editor's "Only show users from selected teams" — restrict the pickable users to members
        of the given teams (name, uuid, or IRI). It sets ``dataSourceFilters.showTeams``/
        ``teams``; :meth:`add_field`/:meth:`create_module` resolve the teams to IRIs and refuse
        to stage it on an appliance older than 8.0 (7.6.x has no equivalent).

        This is a pure builder — it returns the owning-side attribute only. Add it with
        :meth:`add_field`, which **creates the reverse side on the target for you** so the
        relationship is valid on publish:

        - ``manyToMany`` with the **default** inverse (``inversed_field=None``): FortiSOAR
          itself mirrors a reverse field onto ``target_module``; :meth:`add_field` leaves
          the target untouched to avoid duplicating it.
        - ``manyToMany`` with a **custom** ``inversed_field``: :meth:`add_field` adds the
          matching reverse ``manyToMany`` (named ``inversed_field``) to the target.
        - ``oneToMany``: :meth:`add_field` adds the required **lookup** (many-to-one) to the
          target (a ``oneToMany`` will not publish without it).

        Pass ``create_reverse=False`` to :meth:`add_field` to stage only this side, then
        confirm whatever landed with :meth:`reverse_field`.

        ``owns_relationship`` (default True) marks this as the owning side of the join.
        Pass ``owns_relationship=False`` for the non-owning mirror of an existing relation.
        """
        attr = cls._field_dict(
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
        if team_scope:
            cls._apply_team_scope(attr, team_scope)
        return AttributeMetadata.model_validate(attr)

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
    def pending_changes(self) -> list[PendingChange]:
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
        # ``$relationships=true`` is REQUIRED: without it the list payload omits the
        # ``attributes`` (fields) relationship entirely, so a field-only change — adding /
        # removing a field, toggling visibility, setting required-by-condition — leaves the
        # bare scalar records identical and ``_differs`` reports no change (false-empty).
        # Only module create/delete would be caught. See _REL.
        stg = {
            str(m.get("type", "")).lower(): m
            for m in (self.client.get(_STAGING, params={**_ALL, **_REL}) or {}).get("hydra:member", [])
        }
        pub = {
            str(m.get("type", "")).lower(): m
            for m in (self.client.get(_PUBLISHED, params={**_ALL, **_REL}) or {}).get("hydra:member", [])
        }
        from ..models import PendingChange

        changes: list[PendingChange] = []
        for mod in sorted(set(stg) | set(pub)):
            if mod not in pub:
                changes.append(PendingChange(module=mod, change="created"))
            elif mod not in stg:
                changes.append(PendingChange(module=mod, change="deleted"))
            elif self._differs(stg[mod], pub[mod]):
                changes.append(PendingChange(module=mod, change="modified"))
        return changes

    def find_invalid_drafts(self, *, deep: bool = False) -> list[InvalidDraft]:
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
        from ..models import InvalidDraft

        problems: list[InvalidDraft] = []
        members = (self.client.get(_STAGING, params=_ALL) or {}).get("hydra:member", [])
        for m in members:
            t = m.get("type") or ""
            uuid = m.get("uuid")
            if not _MODULE_NAME_RE.match(t):
                problems.append(InvalidDraft(module=t, uuid=uuid, problem="invalid module name"))
            elif len(t) > _MAX_NAME_LEN:
                problems.append(InvalidDraft(module=t, uuid=uuid, problem="module name too long"))
            if not deep:
                continue
            full = self.client.get(f"{_STAGING}/{uuid}", params=_REL) or {}
            for a in full.get("attributes", []) or []:
                n = a.get("name") or ""
                if not _FIELD_NAME_RE.match(n):
                    problems.append(InvalidDraft(module=t, uuid=uuid, field=n, problem="invalid field name"))
        return problems

    @staticmethod
    def _differs(staged: dict[str, Any], published: dict[str, Any]) -> bool:
        """True if a staged record differs from its published one, ignoring the
        endpoint-relative ``@id``/``@type``/``@context`` keys.

        These hypermedia keys differ by *store* at **every** nesting level, not just the
        top — e.g. a field's ``@id`` is ``/api/3/attribute_metadatas/<uuid>`` in staging but
        ``/api/3/attrib_model_metadatas/<uuid>`` in published (same uuid, different path), and
        each attribute also carries a ``sattrib`` back-reference IRI
        (``/api/3/staging_model_metadatas/<uuid>`` vs ``/api/3/model_metadatas/<uuid>``). A
        shallow strip would therefore flag every module as ``modified`` once ``attributes``
        are compared. We (1) drop the ``@id``/``@type``/``@context`` keys recursively and
        (2) canonicalize the store segment of any metadata IRI *value* (staging vs published
        names → one token, keeping the shared uuid) so only semantic differences remain."""
        skip = {"@id", "@type", "@context"}
        # staging/published variants of the model + attribute metadata store segments.
        store_iri = re.compile(r"/api/3/(?:staging_)?(?:model_metadatas|attrib_model_metadatas|attribute_metadatas)/")

        def scrub(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: scrub(v) for k, v in value.items() if k not in skip}
            if isinstance(value, list):
                return [scrub(v) for v in value]
            if isinstance(value, str):
                return store_iri.sub("/api/3/_metadata_/", value)
            return value

        return scrub(staged) != scrub(published)

    # -------------------------------------------------------------- write
    def create_module(
        self,
        module: str,
        *,
        label: str | None = None,
        plural: str | None = None,
        fields: list[dict[str, Any] | AttributeMetadata] | None = None,
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
        grant_to: list[str] | str | None = None,
        add_to_nav: bool = False,
        nav_title: str | None = None,
        nav_icon: str | None = None,
        nav_parent: str | None = None,
        nav_position: Literal["top", "bottom"] = "bottom",
        facts: Any | None = None,
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

        ``grant_to`` (optional) grants full CRUD+execute permissions on the new module to
        one or more roles. Pass a single role name (string) or a list of role names. **This is
        explicit opt-in** — no roles are auto-granted unless you specify them, ensuring RBAC
        changes are intentional. The grant is **deferred until the next** :meth:`publish`: a
        brand-new module lives only in staging, and role grants resolve the module via
        ``/api/3/modules`` (published only), so granting at create time would fail with "module
        not found … call publish() first". ``publish()`` applies the pending grants once the
        module is live. If you never publish (or grant manually), the module exists but its
        records cannot be accessed until a role is granted via
        :meth:`~pyfsr.api.roles.RolesAPI.grant_module_permissions`.

        ``add_to_nav`` (optional, default False) adds the module to the application
        navigation bar so it is reachable in the UI. Like ``grant_to`` it is **deferred
        until the next** :meth:`publish` (the nav entry's ``require`` gate and route only
        resolve once the module is live). By default it appends a **new top-level section
        at the bottom** of the navigation, gated by ``read`` permission on the module and
        routing to the module's record list. Customize with ``nav_title`` (defaults to the
        module label), ``nav_icon`` (defaults to a generic icon), ``nav_parent`` (a group
        title/module to nest under instead of top-level), and ``nav_position``
        (``"top"``/``"bottom"``). For full control, edit navigation directly via
        :class:`~pyfsr.api.app_config.AppConfigAPI` (``client.app_config``).

        Example::

            admin.create_module(
                "custom_alerts",
                fields=[admin.text_field("name")],
                grant_to=["Full App Permissions", "SOC Analyst"],
                add_to_nav=True,  # new section at the bottom of the nav bar
            )

        ``facts`` (optional :class:`pyfsr.cli.appliance.Facts`) enables a **create-side
        collision precheck**: a previously-deleted module of the same name leaves orphaned
        physical tables behind (FortiSOAR cannot ``DROP`` them over the API — see
        :meth:`delete_module`), and reusing that ``tableName`` wedges the next
        :meth:`publish` on a Postgres ``42P07`` index-name collision. With ``facts`` given,
        this refuses up front (before staging anything) if leftover tables exist for
        ``module`` while no live module backs them, pointing you at the reclaim path. This
        is symmetric with :meth:`find_invalid_drafts` (which catches bad *names*); here we
        catch leftover *tables*.
        """
        if not isinstance(module, str) or not _MODULE_NAME_RE.match(module):
            raise ValueError(
                f"invalid module name {module!r}: a module type must start with a lowercase "
                "letter and contain only lowercase letters, digits, or underscores "
                "(e.g. 'customwidgets', 'threat_reports')"
            )
        if len(module) > _MAX_NAME_LEN:
            raise ValueError(f"module name {module!r} exceeds {_MAX_NAME_LEN} characters")
        if facts is not None:
            self._guard_orphan_table_collision(module, facts)
        if fields is not None and not fields:
            raise ValueError("a module needs at least one field; pass fields=None for a default 'name'")
        label = label or module
        if fields is None:
            fields = [self.text_field("name", required=True)]
        field_dicts: list[dict[str, Any]] = [self._to_field_dict(f) for f in fields]
        self._guard_team_scope(field_dicts)
        if display_template is None:
            names = [f.get("name") for f in field_dicts if f.get("name")]
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
            "attributes": field_dicts,
        }
        payload.update(opts)
        created = self.client.post(_STAGING, data=payload, params=_REL)
        if create_view_templates:
            self.create_view_templates(module)
        if grant_to is not None:
            # A brand-new module lives only in STAGING; role grants resolve the module via
            # /api/3/modules (PUBLISHED only), so granting here would raise "not found ... call
            # publish() first". Defer the grant — publish() flushes it once the module is live.
            roles = grant_to if isinstance(grant_to, list) else [grant_to]
            if roles:
                self._pending_grants.setdefault(module, []).extend(roles)
        if add_to_nav:
            # Deferred for the same reason as grants: the nav entry routes to a live
            # module and gates on a permission that only resolves post-publish.
            self._pending_nav[module] = {
                "title": nav_title or label,
                "icon": nav_icon,
                "parent": nav_parent,
                "position": nav_position,
            }
        return created

    def get_or_create_module(
        self,
        module: str,
        *,
        publish: bool = True,
        publish_kwargs: dict[str, Any] | None = None,
        **create_kwargs: Any,
    ) -> tuple[dict[str, Any], bool]:
        """Idempotently ensure ``module`` exists; return ``(metadata, created)``.

        The structural-object analogue of
        :meth:`~pyfsr.records.RecordSet.get_or_create`, returning the same Django-style
        ``(obj, created)`` tuple:

        - If ``module`` already exists (published **or** staging), return its current
          metadata with ``created=False`` and make **no** changes — nothing is created,
          nothing is published.
        - Otherwise call :meth:`create_module` with ``create_kwargs`` (``fields=``,
          ``grant_to=``, ``label=``, etc.) and, when ``publish`` is True (default),
          :meth:`publish` so the module goes live (which also flushes any ``grant_to``).
          Return the resulting metadata with ``created=True``.

        Because :meth:`publish` is appliance-wide, an existing staging-only module is
        returned as-is rather than force-published — re-publishing is left to the caller.
        Pass ``publish_kwargs`` to forward options (e.g. ``{"timeout": 420}``) to
        :meth:`publish`.

        Example::

            meta, created = admin.get_or_create_module(
                "reconciliation_result",
                fields=[admin.text_field("name", required=True)],
                grant_to=["Security Administrator"],
            )
        """
        existing = self.get_published(module) or self.get_staging(module)
        if existing is not None:
            return existing, False

        self.create_module(module, **create_kwargs)
        if publish:
            self.publish(**(publish_kwargs or {}))

        meta = (self.get_published(module) if publish else None) or self.get_staging(module)
        return meta or {}, True

    @property
    def _pending_grants(self) -> dict[str, list[str]]:
        """Role grants requested via ``create_module(grant_to=...)``, applied on the next publish.

        Keyed by module type → role names. A new module is staging-only, so its grants can only
        be applied after :meth:`publish` makes it resolvable in ``/api/3/modules``.
        """
        cache: dict[str, list[str]] | None = getattr(self, "_pending_grants_cache", None)
        if cache is None:
            cache = {}
            self._pending_grants_cache = cache
        return cache

    def _flush_pending_grants(self) -> None:
        """Apply (and clear) deferred ``grant_to`` grants after a successful publish.

        Invalidates the roles module cache first (the just-published module would otherwise be
        absent), then grants each role full CRUD+execute. A grant failure is surfaced verbatim
        — the publish itself already committed, so this raises rather than swallowing the error.
        """
        pending = self._pending_grants
        if not pending:
            return
        self.client.roles._module_cache = None  # the new module is now published; refresh
        try:
            for module, roles in pending.items():
                for role_name in roles:
                    self.client.roles.grant_module_permissions(
                        role_name,
                        module=module,
                        can_read=True,
                        can_create=True,
                        can_update=True,
                        can_delete=True,
                        can_execute=True,
                    )
        finally:
            pending.clear()

    @property
    def _pending_nav(self) -> dict[str, dict[str, Any]]:
        """Navigation entries requested via ``create_module(add_to_nav=...)``, applied on publish.

        Keyed by module type → the nav-entry spec (title/icon/parent/position). Deferred for
        the same reason as :attr:`_pending_grants`: the entry routes to a live module and gates
        on a permission that only resolves once the module is published.
        """
        cache: dict[str, dict[str, Any]] | None = getattr(self, "_pending_nav_cache", None)
        if cache is None:
            cache = {}
            self._pending_nav_cache = cache
        return cache

    def _flush_pending_nav(self) -> None:
        """Add (and clear) deferred ``add_to_nav`` entries after a successful publish.

        Builds a :class:`~pyfsr.models.NavItem` for each pending module — a leaf routing to
        the module's record list, gated by ``read`` permission on the module — and inserts it
        via :meth:`~pyfsr.api.app_config.AppConfigAPI.add_navigation_item`. A failure is
        surfaced verbatim (the publish itself already committed).
        """
        pending = self._pending_nav
        if not pending:
            return
        try:
            for module, spec in pending.items():
                item = NavItem(
                    title=spec.get("title") or module,
                    icon=spec.get("icon") or "icon icon-bookmark",
                    state=NavState(name="main.modules.list", parameters={"module": module}),
                    require=NavRequire(module=module, action="read"),
                )
                self.client.app_config.add_navigation_item(
                    item, parent=spec.get("parent"), position=spec.get("position", "bottom")
                )
        finally:
            pending.clear()

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

    def _put_attributes(self, mod: dict[str, Any], attributes: list[dict[str, Any]]) -> dict[str, Any]:
        """PUT only the ``attributes`` of a staging record (a full-record PUT is
        rejected — the GET payload carries read-only ``@id``/``@context`` keys)."""
        return self.client.put(f"{_STAGING}/{mod['uuid']}", data={"attributes": attributes}, params=_REL)

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
            raise FortiSOARException(f"module settings did not apply for {module!r}: {sorted(not_applied)}")
        return updated

    def _appliance_version(self) -> tuple[int, int, int] | None:
        """The appliance's ``(major, minor, patch)``, fetched once and cached, or ``None``.

        ``None`` means the version could not be determined (endpoint failure or an
        unparseable string); callers gating a feature treat that as "cannot confirm".
        """
        cached = getattr(self, "_appliance_version_cache", "unset")
        if cached != "unset":
            return cached  # type: ignore[return-value]
        try:
            parsed = _parse_version(self.client.version())
        except FortiSOARException:
            parsed = None
        self._appliance_version_cache: tuple[int, int, int] | None = parsed
        return parsed

    def _appliance_at_least(self, minimum: tuple[int, int, int]) -> bool | None:
        """``True``/``False`` if the appliance is ≥ ``minimum``; ``None`` if version is unknown."""
        version = self._appliance_version()
        if version is None:
            return None
        return version >= minimum

    def scope_field_to_teams(self, field: dict[str, Any] | AttributeMetadata, teams: list[str]) -> AttributeMetadata:
        """Restrict a People lookup/relationship ``field``'s pickable users to members of ``teams``.

        The in-product "Only show users from selected teams" option (FortiSOAR **8.0+**). Takes a
        field (built with :meth:`lookup_field`/:meth:`relationship_field`) and returns a typed
        :class:`~pyfsr.models.AttributeMetadata` with the team-scope option applied::

            f = admin.relationship_field("approvers", "people")
            f = admin.scope_field_to_teams(f, ["SOC Team", "TeamA"])

        Equivalent to passing ``team_scope=`` to :meth:`lookup_field`/:meth:`relationship_field`.
        Version-gating + team→IRI resolution still happen when the field is staged via
        :meth:`add_field`/:meth:`create_module`.
        """
        attr = self._to_field_dict(field)
        self._apply_team_scope(attr, teams)
        return AttributeMetadata.model_validate(attr)

    def _guard_orphan_table_collision(self, module: str, facts: Any) -> None:
        """Refuse to create ``module`` if leftover physical tables already occupy its
        ``tableName`` while no live module backs them — the ``42P07`` publish wedge.

        Only fires when the module is not currently live (a live module legitimately owns
        its tables). Needs an appliance ``Facts`` context (the API can't see physical tables).
        """
        if self.get_published(module) or self.get_staging(module):
            return  # the module is live/staged — its tables belong to it, no collision
        from ..cli.appliance import db as _appliance_db

        leftover = _appliance_db.find_module_tables(facts, module)
        if leftover:
            raise FortiSOARException(
                f"cannot create module {module!r}: {len(leftover)} orphaned physical table(s) "
                f"from a previously-deleted module still occupy this tableName "
                f"({', '.join(leftover)}). Publishing would wedge on a Postgres 42P07 index "
                f"collision. Reclaim them first with `pyfsr appliance db orphans --drop --yes` "
                f"or delete_module(..., drop_orphan_tables=facts), then retry."
            )

    def _guard_team_scope(self, fields: list[dict[str, Any]]) -> None:
        """Version-gate + normalize the team-scope option on any People field in ``fields``.

        For each field carrying ``dataSourceFilters.showTeams``: refuse to stage it unless the
        appliance is ≥ 8.0 (7.6.x has no equivalent — staging it there ships a silent no-op), and
        rewrite the ``teams`` list from whatever the caller gave (team name, bare uuid, or IRI) to
        the ``/api/3/teams/<uuid>`` IRIs the engine stores. Mutates the field dicts in place.
        """
        scoped = [f for f in fields if isinstance(f, dict) and (f.get("dataSourceFilters") or {}).get("showTeams")]
        if not scoped:
            return
        at_least = self._appliance_at_least(_TEAM_SCOPE_MIN_VERSION)
        if at_least is False:
            version = self._appliance_version()
            raise FortiSOARException(
                "the 'Only show users from selected teams' field option (dataSourceFilters.showTeams) "
                f"requires FortiSOAR {'.'.join(map(str, _TEAM_SCOPE_MIN_VERSION))}+, but this appliance "
                f"is {'.'.join(map(str, version)) if version else 'older'}; 7.6.x has no equivalent. "
                "Drop team_scope=/scope_field_to_teams() for this appliance."
            )
        # at_least is None (unknown version) → proceed; the appliance itself is the backstop.
        for field in scoped:
            field["dataSourceFilters"]["teams"] = [self._team_iri(t) for t in field["dataSourceFilters"]["teams"]]

    def _team_iri(self, team: str) -> str:
        """Normalize a team identifier (IRI, bare uuid, or display name) to a ``/api/3/teams/..`` IRI."""
        if team.startswith("/api/"):
            return team
        if is_uuid(team):
            return f"/api/3/teams/{team}"
        uuid = self.client.teams.team_uuid_by_name(team)
        if not uuid:
            raise FortiSOARException(f"no team named {team!r} found to scope the field to")
        return f"/api/3/teams/{uuid}"

    def add_field(
        self, module: str, field: dict[str, Any] | AttributeMetadata, *, create_reverse: bool = True
    ) -> dict[str, Any]:
        """Append a field (build it with :meth:`field`) to ``module`` in staging.

        When ``field`` is a relationship whose reverse side FortiSOAR does **not**
        auto-create, pyfsr creates it for you so the relationship is valid on publish
        (``create_reverse=True``, the default):

        - ``oneToMany`` → a matching ``lookup`` (many-to-one) is added to the target
          module (a ``oneToMany`` will not publish without it).
        - ``manyToMany`` with a **custom** ``inversedField`` → the mirror ``manyToMany``
          is added to the target.

        A plain ``lookup`` (one-directional) and a default-inverse ``manyToMany`` (which
        the platform mirrors itself) add no extra field. Pass ``create_reverse=False`` to
        stage only this side. The reverse is created in **staging** too, so publishing the
        modules commits both sides.

        Raises ``ValueError`` if the reverse is needed but the target module does not
        exist, or already has a *different* field under the reverse name.
        """
        field = self._to_field_dict(field)
        self._guard_team_scope([field])
        mod = self.get_staging(module)
        if not mod:
            raise ValueError(f"module {module!r} not found in staging")
        attrs = mod.get("attributes", []) + [field]
        result = self._put_attributes(mod, attrs)
        if create_reverse:
            reverse = self._reverse_attr_for(module, field)
            if reverse is not None:
                self._ensure_reverse_field(*reverse, source_module=module, source_field=field)
        return result

    def _ensure_reverse_field(
        self,
        target_module: str,
        reverse_attr: dict[str, Any],
        *,
        source_module: str,
        source_field: dict[str, Any],
    ) -> None:
        """Add ``reverse_attr`` to ``target_module`` in staging, idempotently."""
        target = self.get_staging(target_module) or self.get_published(target_module)
        if not target:
            raise ValueError(
                f"cannot create the reverse field {reverse_attr['name']!r}: target module "
                f"{target_module!r} (referenced by {source_module}.{source_field.get('name')}) "
                "does not exist — create it first, or pass create_reverse=False"
            )
        existing = next((a for a in target.get("attributes", []) if a.get("name") == reverse_attr["name"]), None)
        if existing is not None:
            # Idempotent: a matching reverse (same target type) is fine; a clashing
            # field of a different type is a conflict the caller must resolve.
            if existing.get("type") != reverse_attr.get("type"):
                raise ValueError(
                    f"target module {target_module!r} already has a field "
                    f"{reverse_attr['name']!r} of a different type "
                    f"({existing.get('type')!r}); rename the inverse or pass create_reverse=False"
                )
            return
        self.add_field(target_module, reverse_attr, create_reverse=False)

    def set_field_type(self, module: str, field: str, *, db_type: str, form_type: str | None = None) -> dict[str, Any]:
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
        remove_from_nav: bool = False,
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
            remove_from_nav: If True, also remove the module's navigation entry (the inverse
                of ``create_module(add_to_nav=True)``) via
                :meth:`~pyfsr.api.app_config.AppConfigAPI.remove_navigation_item`. Done after
                the publish commits the delete; a no-op if the module has no nav entry.
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
            ``dropped_tables`` (``list`` or ``None``), and ``nav_removed``
            (``True`` if a nav entry was removed, else ``None``).

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
            "nav_removed": None,
        }
        if publish:
            result["published"] = self.publish(timeout=timeout, poll_interval=poll_interval)
            if remove_from_nav:
                # The module is gone; drop its nav entry too (no-op if it had none).
                self.client.app_config.remove_navigation_item(module=module, missing_ok=True)
                result["nav_removed"] = True
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

    def _publish_status(self) -> dict[str, Any] | None:
        """Parse ``/api/publish/error`` regardless of HTTP status.

        ``/api/publish/error`` returns **HTTP 400** (not 200) whenever the appliance has a
        prior publish error on record — and the body still carries the authoritative
        ``status`` / ``last_publish_time`` fields. A plain ``client.get`` raises on that 400
        and the caller loses the body, so we read with ``raise_on_status=False`` and parse
        the JSON for any status code. Returns the parsed dict, or None if unparseable.
        """
        try:
            resp = self.client.get(_PUBLISH_ERROR, raise_on_status=False)
            # raise_on_status=False yields the raw Response; parse its JSON best-effort.
            body = resp.json() if hasattr(resp, "json") else resp
            return body if isinstance(body, dict) else None
        except Exception:
            return None

    def _last_publish_time(self) -> int | None:
        """Best-effort read of the last publish's ``last_publish_time`` (epoch), or None."""
        body = self._publish_status()
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
        during which some or all of the API may be unavailable. This method blocks until the
        migrate finishes and confirms the result via ``/api/publish/error``, returning only
        once the schema is actually live (or raising).

        **Blast radius is version- and change-dependent** (measured, not assumed):

        - On **7.6.x** every publish — even a no-op — runs the full backup + migrate + cache
          rebuild, during which the **entire ``/api/3`` surface returns 503** for the whole
          window (~50–57s observed on 7.6.5).
        - On **8.0+** the impact is greatly reduced. Minor field-only edits (toggling
          *visibility* or setting *required-by-condition*) commit in **~3s with no observable
          outage** on any read surface. A **structural** change (adding a field/module) takes
          longer (~30s) and disrupts only the *record/query/auth* layer: record-list endpoints
          (``GET /api/3/<module>``) return transient **404** for ~13s and ``/api/query`` returns
          **400/401** for ~2.5s while the migrate runs — *not* a global 503. Module-metadata,
          view-resolve, app-config and picklist surfaces stay available throughout.

          That record-layer blip is **instance-wide, not scoped to the changed module**:
          reads on *unrelated* modules (measured: ``alerts``/``incidents``/``indicators``) go
          down for the same window as the module being altered, because the migrate cycles the
          shared ORM/query services. So a structural publish briefly breaks record reads across
          the whole appliance, while leaving the metadata-serving layer up.

        See ``Miscellaneous/fortisoar/repro/publish_blast_radius_repro.py`` and
        the ``PublishProbe`` helper in ``scripts/publish_probe.py`` for the per-surface
        measurement.

        **No-op publishes return immediately.** If :meth:`pending_changes` is empty there is
        nothing to migrate — the PUT returns 200 but no backup/migrate runs and
        ``last_publish_time`` never advances — so this returns right after the PUT instead of
        blocking for the full ``timeout`` waiting for an outage that will never happen.

        Note ``/api/publish/error`` returns **HTTP 400** (with a usable JSON body) whenever the
        appliance has a prior publish error on record; the poller reads that body
        status-agnostically (see ``_publish_status``) rather than treating the 400 as an
        outage. A box whose error record stays stale across a successful publish can therefore
        report a misleading ``status`` — trust :meth:`pending_changes` / a schema check to
        confirm the live state.

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
        # A publish with nothing staged is a server-side no-op: the PUT returns 200 but no
        # migrate runs, so ``last_publish_time`` never advances and there is no 503 outage.
        # Capture this up front so we can skip the (otherwise full-timeout) wait below.
        had_pending = bool(self.pending_changes())
        prev_state = self._publish_status() or {}
        prev_time = prev_state.get("last_publish_time")
        prev_errors = prev_state.get("errors", _UNSET)
        try:
            self.client.put(_PUBLISH, data={})
        except (FortiSOARException, requests.exceptions.RequestException) as exc:
            # A 5xx body — or a read timeout / dropped connection — here is the migrate
            # cycle already starting (the publish was accepted; the PUT itself can block
            # past the client timeout). Anything else — notably a 400 with a validation
            # message — is a real rejection the caller needs to see. The poller confirms
            # the true outcome via /api/publish/error regardless.
            if not self._is_publish_transient(exc):
                raise
        if not had_pending:
            # Nothing to migrate: ``_wait_for_publish`` would block for the full timeout
            # waiting for a ``last_publish_time`` advance that never comes. Return now.
            # (Deferred grants/nav from create_module(grant_to=/add_to_nav=) are flushed below.)
            self._flush_pending_grants()
            self._flush_pending_nav()
            return self._publish_status() or {"status": "Success", "note": "no pending changes"}
        result = self._wait_for_publish(prev_time, timeout, poll_interval, prev_errors=prev_errors)
        # The schema is now live — apply role grants and nav entries deferred from create_module.
        self._flush_pending_grants()
        self._flush_pending_nav()
        return result

    def revert(self) -> dict[str, Any]:
        """Discard **all** pending staged schema changes (``PUT /api/publish/revert``).

        The inverse of :meth:`publish`: rather than committing staging to live, this
        drops every uncommitted draft so staging matches the currently-published
        schema again. Use it to abandon a half-built change, or to clear a wedged
        staged draft (e.g. an illegally-named module that :meth:`find_invalid_drafts`
        flags) so a subsequent :meth:`publish` can succeed.

        ⚠️ Appliance-wide: like publish, this is **not** scoped to modules you
        touched — it discards every pending staged change across the whole instance.
        On a shared appliance, confirm nothing else is mid-edit before calling.

        Unlike publish there is no DB migrate, so this returns synchronously without
        the 503 outage window.

        Returns:
            The decoded API response (commonly ``{"status": ...}``), or an empty
            dict if the endpoint returns no body.
        """
        result = self.client.put(_REVERT, data={})
        return result if isinstance(result, dict) else {}

    def _wait_for_publish(
        self,
        prev_time: int | None,
        timeout: float,
        poll_interval: float,
        prev_errors: Any = _UNSET,
    ) -> dict[str, Any]:
        """Block until the async publish finishes, using ``/api/publish/error`` as the truth.

        Polls ``/api/publish/error`` until ``last_publish_time`` advances past ``prev_time``.
        Returns the final body on ``status == "Success"``; raises
        :class:`~pyfsr.exceptions.FortiSOARException` with the appliance's reported
        status/error on a **fresh** failure, or :class:`TimeoutError` if it never reports back.

        While the migrate runs the **whole API is unstable** — ``/api/publish/error`` itself
        may return 503s, gateway errors, or unparseable bodies ("Unknown error occurred").
        Every such failure is treated as "still in progress, keep waiting": only a cleanly
        parsed body is allowed to decide the outcome.

        **Stale error logs.** Some appliances keep a *persistent* publish-error record: after
        a wedge, ``/api/publish/error`` returns ``status == "Fail"`` (HTTP 400) with the old
        ``errors`` text **forever**, even after subsequent publishes succeed. Trusting
        ``status`` alone would then report a false failure on every publish. So a failure is
        only believed when the ``errors`` text **changed** versus ``prev_errors`` (captured by
        :meth:`publish` before the PUT): an unchanged error log after we have ridden the 503
        outage and ``last_publish_time`` advanced means the logged failure is stale → success.
        """
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        saw_outage = False
        while True:
            # ``/api/publish/error`` returns 400 (with a usable body) when a prior error is
            # on record, so read it status-agnostically. A None here means the endpoint was
            # genuinely unreachable/unparseable — i.e. mid-migrate API outage.
            body = self._publish_status()
            if body is None:
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
                advanced = body.get("last_publish_time") != prev_time
                fresh = advanced or saw_outage
                # A reported failure is only real if its error log changed vs. before the PUT;
                # an unchanged log on a box that committed (rode the outage, advanced the time)
                # is a stale record, not this publish's outcome.
                errors_stale = prev_errors is not _UNSET and body.get("errors") == prev_errors
                if fresh and status == "success":
                    return body
                if saw_outage and advanced and errors_stale:
                    return body
                # Lightweight (metadata-only) publishes — toggling visibility, setting
                # required-by-condition — commit on 8.0 WITHOUT a migrate: no 503 outage and
                # ``last_publish_time`` never advances, so the signals above never fire and we
                # would wait out the full timeout. Detect completion structurally instead: once
                # staging matches published again (nothing pending), the publish is done. Guard
                # it to the "no outage, no advance" case so it never races a real migrate, and
                # only when there is no *fresh* failure on record. ``pending_changes`` itself can
                # error mid-migrate — treat that as "still in progress".
                if not advanced and not saw_outage and (errors_stale or status in ("", "success")):
                    try:
                        committed = not self.pending_changes()
                    except Exception:
                        committed = False
                    if committed:
                        return body
                if fresh and not errors_stale and status not in ("", "started", "in progress", "inprogress"):
                    raise FortiSOARException(
                        describe_migrate_failure(body.get("status"), body.get("message") or body.get("error") or body)
                    )
            if time.monotonic() >= deadline:
                raise TimeoutError(f"publish did not complete within {timeout}s (last state: {last})")
            time.sleep(poll_interval)
