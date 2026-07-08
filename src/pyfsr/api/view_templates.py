"""View template CRUD and role/condition-based default template assignment.

This module wraps the system view template (SVT) **write** surface complementing the
read-only :class:`~pyfsr.api.views.ViewsAPI`. It handles:

- **Template read/write** — ``/api/views/1/{name}`` (GET, PUT, POST for named view template
  CRUD). A named template can be assigned as the active template for a module/layout via the
  platform's view-resolution logic.
- **Default template assignment** — a default is the ``isDefault`` flag on a
  ``system_view_templates`` row (keyed by ``(module, viewOptions)``), saved via
  ``bulkupsert/system_view_templates``. There is **no** ``/api/3/template/{type}/default``
  endpoint (it 400s "Invalid Type", verified 8.0).
- **Template enumeration** — ``/api/3/system_view_templates`` (GET list, POST single create).
- **Bulk operations** — ``/api/3/bulkupsert/system_view_templates`` (POST bulk upsert).

Read methods return :class:`~pyfsr.models.SystemViewTemplate` — dict-compatible
(``t["name"]``/``t.get(...)`` keep working) but with typed attribute access too.

Example:
    >>> client = demo_client()
    >>> [t.name for t in client.view_templates.list_templates(module="alerts")]
    ['Default Layout', 'Default Layout', 'Default Layout', 'CrowdStrike']
    >>> default = client.view_templates.get_default_template("alerts", "detail")
    >>> default.name, default.isDefault
    ('Default Layout', True)

**Note on role/condition-based visibility (Issue 1290905/1289639):**

FortiSOAR 8.0 **may** expose role or condition fields for template visibility control
(template shown only to users with specific roles or matching a condition). The exact
field names are currently unknown and not yet discovered on a live appliance. This
wrapper accepts these fields via ``**extra`` parameters in write methods and passes them
through unchanged. Known candidate field names (unconfirmed):

- ``roles`` — list of role names or UUIDs
- ``displayConditions`` — list of condition objects
- ``visibilityCondition`` — single condition object
- ``restrictedAccess`` — boolean or permission spec
- ``conditions`` — generic condition list

Once the field name is confirmed (via 8.0 appliance inspection), this module should be
updated to add explicit typed parameters instead of accepting ``**extra``.

Example (once field name is known)::

    # After confirmation, this would become a typed parameter:
    client.view_templates.create_template(
        "MyTemplate",
        config={...},
        module="alerts",
        viewOptions="detail",
        roles=["Full App Permissions", "SOC Analyst"],  # future: typed once confirmed
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models._system import SystemViewTemplate
from ..pagination import extract_members
from .base import BaseAPI

if TYPE_CHECKING:
    from ..client import FortiSOAR

#: Canonical viewset segment for named view templates.
_VIEWSET = 1

#: The three layout kinds a module exposes (the ``viewOptions`` values).
_KINDS = ("list", "detail", "form")

#: Endpoint for listing/creating system view templates.
_SYSTEM_TEMPLATES = "/api/3/system_view_templates"

#: Endpoint for bulk upserting templates (the editor's real save path for defaults).
_SYSTEM_TEMPLATES_BULK = "/api/3/bulkupsert/system_view_templates"

#: Query params for fetching all records.
_ALL = {"$limit": 2147483647}


def _row_to_dict(template: dict[str, Any] | SystemViewTemplate) -> dict[str, Any]:
    """Normalize a dict or :class:`SystemViewTemplate` row to a plain wire dict."""
    if isinstance(template, SystemViewTemplate):
        return template.model_dump(by_alias=True, exclude_none=True)
    return dict(template)


class ViewTemplatesAPI(BaseAPI):
    """Read/write system view templates and manage role/condition-based defaults.

    Complements :class:`~pyfsr.api.views.ViewsAPI` (which resolves the *active*
    template for a module/layout) by exposing the *write* surface for template
    management, including default-template assignment and (on 8.0+) role/condition
    visibility control.

    Example::

        # Create a named template
        tpl = client.view_templates.create_template(
            "Custom Detail Layout",
            config={
                "rows": [
                    {"columns": [{"widgets": [{"type": "editableForm", "config": []}]}]},
                ]
            },
            module="alerts",
            viewOptions="detail",
            type="rows",
        )

        # Set it as the default for its module/layout (flips isDefault on the row)
        client.view_templates.set_default_template(tpl)

        # Fetch the active default for alert detail layouts
        default = client.view_templates.get_default_template("alerts", "detail")
    """

    def __init__(self, client: FortiSOAR) -> None:
        super().__init__(client)

    # ----------------------------------------------------------------- read
    def get_template(self, name: str) -> SystemViewTemplate:
        """Fetch a named view template by name (``GET /api/views/1/{name}``).

        Args:
            name: Template name (e.g., ``"Custom Detail Layout"``).

        Returns:
            The template record, typed as :class:`~pyfsr.models.SystemViewTemplate`
            (``name``, ``config``, ``module``, ``viewOptions``, ``uuid``, ``type``,
            ``isDefault``, plus any role/condition fields via dict-style access).

        Raises:
            FortiSOARException: if the template does not exist (404).
        """
        result = self.client.get(f"/api/views/{_VIEWSET}/{name}")
        assert isinstance(result, dict)
        return SystemViewTemplate.model_validate(result)

    def list_templates(self, module: str | None = None) -> list[SystemViewTemplate]:
        """List all system view templates, optionally filtered by module.

        Args:
            module: Optional module name (e.g., ``"alerts"``) to filter templates
                for that module only. If None, returns all templates.

        Returns:
            List of :class:`~pyfsr.models.SystemViewTemplate` (each with ``uuid``,
            ``name``, ``module``, ``type``, ``viewOptions``, ``isDefault``, ``config``).
        """
        data = self.client.get(_SYSTEM_TEMPLATES, params=_ALL) or {}
        templates = [SystemViewTemplate.model_validate(t) for t in extract_members(data)]

        if module:
            want = module.strip().lower()
            templates = [t for t in templates if str(t.module or "").lower() == want]

        return templates

    def get_default_template(self, module: str, kind: str = "detail") -> SystemViewTemplate | None:
        """Return the **default** view template for ``module`` and layout ``kind``.

        Defaults are not a separate endpoint: on FortiSOAR (verified 8.0) a default
        is simply the ``system_view_templates`` row carrying ``isDefault: true`` for
        a given ``(module, viewOptions)`` pair. (The ``/api/3/template/{type}/default``
        path an earlier draft assumed does not exist — it 400s with "Invalid Type".)

        Args:
            module: Module name (e.g. ``"alerts"``).
            kind: Layout — one of ``"list"``, ``"detail"`` (default), or ``"form"``.

        Returns:
            The default SVT row for that module/layout, or ``None`` if none is flagged.

        Raises:
            ValueError: if ``kind`` is not one of the three layout kinds.
        """
        if kind not in _KINDS:
            raise ValueError(f"kind must be one of {_KINDS}, got {kind!r}")
        for t in self.list_templates(module=module):
            if t.get("viewOptions") == kind and t.get("isDefault"):
                return t
        return None

    # ----------------------------------------------------------------- write
    def create_template(
        self,
        name: str,
        config: dict[str, Any],
        *,
        module: str,
        viewOptions: str,
        type: str = "rows",
        isDefault: bool = False,
        uuid: str | None = None,
        **extra: Any,
    ) -> SystemViewTemplate:
        """Create a new named view template (``POST /api/views/1/{name}``).

        Args:
            name: Template name (e.g., ``"Custom Detail Layout"``).
            config: Template layout configuration (dict with ``rows``, widgets, etc.).
            module: Module name (e.g., ``"alerts"``).
            viewOptions: One of ``"list"``, ``"detail"``, or ``"form"``.
            type: Storage type, one of ``"rows"`` or ``"form"``. Defaults to ``"rows"``.
            isDefault: Whether this should be the default template for the module/layout.
                Defaults to False.
            uuid: Optional explicit UUID. If not provided, a new UUID will be generated.
            **extra: Additional fields (e.g., role/condition fields once they are
                discovered on 8.0). These are passed through unchanged to the API.

        Returns:
            The created template record, typed as :class:`~pyfsr.models.SystemViewTemplate`.

        Raises:
            FortiSOARException: on validation errors or API failures.
        """
        body: dict[str, Any] = {
            "@type": "SystemViewTemplate",
            "name": name,
            "config": config,
            "module": module,
            "viewOptions": viewOptions,
            "type": type,
            "isDefault": isDefault,
        }
        if uuid:
            body["uuid"] = uuid
        body.update(extra)

        result = self.client.post(f"/api/views/{_VIEWSET}/{name}", data=body)
        assert isinstance(result, dict)
        return SystemViewTemplate.model_validate(result)

    def update_template(
        self,
        name: str,
        config: dict[str, Any] | None = None,
        *,
        viewOptions: str | None = None,
        type: str | None = None,
        isDefault: bool | None = None,
        module: str | None = None,
        **extra: Any,
    ) -> SystemViewTemplate:
        """Update an existing named view template (``PUT /api/views/1/{name}``).

        Only the fields you provide are updated; omitted fields retain their current values
        (server-side merge).

        Args:
            name: Template name.
            config: New layout configuration. If None, the current config is retained.
            viewOptions: New viewOptions (``"list"``, ``"detail"``, or ``"form"``).
                If None, retained.
            type: New type (``"rows"`` or ``"form"``). If None, retained.
            isDefault: New default flag. If None, retained.
            module: New module. If None, retained.
            **extra: Additional fields (role/condition fields) to merge. Passed through
                unchanged to the API.

        Returns:
            The updated template record, typed as :class:`~pyfsr.models.SystemViewTemplate`.

        Raises:
            FortiSOARException: if the template does not exist or validation fails.
        """
        body: dict[str, Any] = {}

        if config is not None:
            body["config"] = config
        if viewOptions is not None:
            body["viewOptions"] = viewOptions
        if type is not None:
            body["type"] = type
        if isDefault is not None:
            body["isDefault"] = isDefault
        if module is not None:
            body["module"] = module

        body.update(extra)

        result = self.client.put(f"/api/views/{_VIEWSET}/{name}", data=body)
        assert isinstance(result, dict)
        return SystemViewTemplate.model_validate(result)

    def bulk_upsert_templates(
        self,
        templates: list[dict[str, Any] | SystemViewTemplate],
        *,
        unique_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Bulk upsert system view templates (``POST /api/3/bulkupsert/system_view_templates``).

        Args:
            templates: List of template records to upsert — plain dicts or
                :class:`~pyfsr.models.SystemViewTemplate` instances (e.g. straight
                from :meth:`list_templates`), mixed freely. Each should have at
                minimum ``name``, ``uuid``, ``module``, ``viewOptions``, ``config``,
                ``type``.
            unique_fields: Fields that define uniqueness for upsert. Defaults to ``["uuid"]``.
                Change to ``["name"]`` or ``["module", "viewOptions"]`` if needed.

        Returns:
            The bulk operation result (typically ``{"upserted": N}`` or similar).

        Raises:
            FortiSOARException: on validation or API failures.
        """
        if unique_fields is None:
            unique_fields = ["uuid"]

        body = {
            "__data": [_row_to_dict(t) for t in templates],
            "__unique": unique_fields,
        }

        result = self.client.post(_SYSTEM_TEMPLATES_BULK, data=body)
        assert isinstance(result, dict)
        return result

    def set_default_template(
        self, template: dict[str, Any] | SystemViewTemplate, *, isDefault: bool = True
    ) -> dict[str, Any]:
        """Mark a ``system_view_templates`` row as the default for its module/layout.

        A default is the ``isDefault`` flag on an SVT row, not a separate resource —
        the editor's "Mark as default" sets ``isDefault = true`` on the full template
        and saves it through ``bulkupsert/system_view_templates`` (the
        ``/api/3/template/{type}/default`` path an earlier draft assumed does not exist;
        it 400s "Invalid Type"). This mirrors that: it upserts the **whole** row with
        the flag flipped.

        Args:
            template: A complete SVT row — a dict or :class:`~pyfsr.models.SystemViewTemplate`
                (as returned by :meth:`get_template` or an entry from
                :meth:`list_templates`). Pass the full row, not a fragment —
                ``bulkupsert`` replaces by ``uuid``, so a partial body would drop fields.
            isDefault: Flag value to write (default ``True``; ``False`` to unset).

        Returns:
            The bulk-upsert response.

        Raises:
            ValueError: if ``template`` has no ``uuid``.

        Note:
            The platform enforces **exactly one default per ``(module, viewOptions)``**
            (verified live, 8.0): promoting a row to default auto-demotes the previously
            default row server-side. The corollary — you cannot directly *unset* the
            lone default (``isDefault=False`` on the only default 500s on
            ``bulkupsert/system_view_templates``); instead **promote another row**, which
            demotes this one. So to change a module's default, call this on the new row.
        """
        row = _row_to_dict(template)
        if not row.get("uuid"):
            raise ValueError("set_default_template() needs a full SVT row with a 'uuid'")
        row["isDefault"] = isDefault
        return self.bulk_upsert_templates([row])
