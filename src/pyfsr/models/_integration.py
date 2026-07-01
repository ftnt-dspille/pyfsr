"""Typed models for FortiSOAR integration API response shapes.

These cover the ``/api/integration/`` surface (connectors, configurations,
healthchecks, execute) and the ``/api/3/import_jobs`` / ``/api/3/export_jobs``
job records.  All models subclass :class:`ApiResult` which adds dict-compat
shims so existing code that does ``result["config_id"]`` keeps working while
new code uses ``result.config_id``.

Shapes are validated against a live 7.6.5 appliance — see the pyfsr dev notes
for the capture script.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .types import RecordIRI


def _coerce_display_text(v: Any) -> Any:
    """Tolerate non-string display values (``title``/``description``/``tooltip``/
    ``placeholder``) from sloppy connector definitions.

    Connector authors sometimes put a bare int (e.g. an example port ``34510``)
    or a list in a free-text display field. FortiSOAR itself accepts these, so
    the SDK must too, rather than failing the whole ``definition()`` parse (which
    would silently drop the connector from a warmed catalog).

    Live-grounded sweep of all 93 configured connectors on .205:
      - ``int`` (e.g. listener_port ``10443``, placeholder ``365``) → stringify.
      - ``list`` (e.g. activedirectory object_dn placeholder
        ``['CN=user-name,...']``) → a 1-element list whose single element IS
        the real string; take ``v[0]`` (NOT ``str(v)``, which yields the Python
        repr ``"['CN=...']"``).
      - empty ``dict``/``list`` (e.g. misp payload placeholder ``{}``) → ``None``.
    """
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, list):
        return v[0] if v else None
    if isinstance(v, dict) and not v:
        return None
    return str(v)


class ApiResult(BaseModel):
    """Dict-compatible base for typed API result shapes.

    Subclasses get attribute access (``r.config_id``) **and** dict-style
    subscripting (``r["config_id"]``), so callers don't need to migrate all at
    once.  Unknown fields from the wire are preserved under ``extra``.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def __getitem__(self, key: str) -> Any:
        for name, info in type(self).model_fields.items():
            if key == name or key == info.alias:
                return getattr(self, name)
        extra = self.__pydantic_extra__ or {}
        if key in extra:
            return extra[key]
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        try:
            self[key]
            return True
        except KeyError:
            return False

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self, *, by_alias: bool = True, exclude_none: bool = False) -> dict[str, Any]:
        return self.model_dump(by_alias=by_alias, exclude_none=exclude_none)


# ---------------------------------------------------------------------------
# Custom (non-Hydra) list envelope
# ---------------------------------------------------------------------------


class IntegrationListEnvelope(ApiResult):
    """The custom (non-Hydra) list envelope several ``/api/integration`` endpoints return.

    Unlike the JSON-LD collection wrapped by :class:`~pyfsr.pagination.HydraPage`
    (``hydra:member``/``hydra:totalItems``), endpoints like
    ``GET /api/integration/connectors/`` and
    ``GET /api/integration/configuration/`` page with a plain envelope::

        {"status": "...", "totalItems": 73, "itemsPerPage": 30,
         "nextPage": 2, "previousPage": null, "data": [ {...}, ... ]}

    Typed once here so callers parse it the same way everywhere (it has been
    mis-read as a bare list more than once). ``data`` stays ``list[Any]`` —
    the per-endpoint method validates each row into its own model.
    """

    status: str | None = None
    totalItems: int | None = None
    itemsPerPage: int | None = None
    nextPage: int | None = None
    previousPage: int | None = None
    data: list[Any] = Field(default_factory=list)

    @classmethod
    def parse(cls, response: Any) -> IntegrationListEnvelope:
        """Coerce a raw response into an envelope, tolerating a bare list/None.

        A dict is validated as the envelope; a bare list is wrapped as its
        ``data`` (some endpoints/versions return the array directly); anything
        else yields an empty envelope.
        """
        if isinstance(response, dict):
            return cls.model_validate(response)
        if isinstance(response, list):
            return cls(data=response)
        return cls()

    @property
    def has_next(self) -> bool:
        """Whether the envelope advertises a further page (``nextPage`` set)."""
        return self.nextPage is not None


# ---------------------------------------------------------------------------
# Connector listing
# ---------------------------------------------------------------------------


class ConnectorConfigSummary(ApiResult):
    """A single configuration entry embedded in the connector listing.

    From ``/api/integration/connectors/`` ``configuration[]``.
    """

    id: int | None = None
    config_id: str | None = None
    name: str | None = None
    default: bool = False


class InstalledConnector(ApiResult):
    """An installed connector entry from ``GET /api/integration/connectors/``.

    Only the fields that are stable and useful for code are typed; the rest
    (icons, descriptions, help links) live in ``extra``.
    """

    id: int | None = None
    name: str | None = None
    version: str | None = None
    label: str | None = None
    active: bool | None = None
    system: bool | None = None
    config_count: int | None = None
    status: str | None = None
    configurations: list[ConnectorConfigSummary] = Field(default_factory=list, alias="configuration")
    ingestion_supported: bool | None = None
    tags: list[Any] = Field(default_factory=list)
    agent: str | None = None
    development: bool | None = None
    created: str | None = None
    modified: str | None = None
    publisher: str | None = None
    contributor: str | None = None
    rpm_installed: bool | None = None


# ---------------------------------------------------------------------------
# Connector definition (config schema + operations)
# ---------------------------------------------------------------------------


class OperationParam(ApiResult):
    """One input parameter of a connector operation, from a connector definition.

    The ``parameters[]`` of an operation in
    ``POST /api/integration/connectors/<name>/<version>/?format=json``. ``value``
    is the declared default (its type varies by field). ``visible``/``editable``
    default to ``True`` when the wire omits them. ``onchange`` is typed
    recursively (``dict[str, list[OperationParam]]``) so conditional sub-params
    validate too; ``options`` stays ``list[Any]`` (plain strings or
    ``{value,title}`` dicts); uncurated fields (``apiOperation``, …) stay in
    ``extra``.
    """

    name: str | None = None
    title: str | None = None
    type: str | None = None
    description: str | None = None
    tooltip: str | None = None
    placeholder: str | None = None
    # Wire defaults: a param is not required unless stated, and visible/editable
    # unless the definition explicitly turns them off. Matching these here keeps
    # ``param.visible`` correct whether or not the key was present on the wire.
    required: bool = False
    value: Any = None
    visible: bool = True
    editable: bool = True
    # ``onchange``: a ``select`` param reveals further sub-params once a given
    # option is chosen — ``{option_value: [sub-param, ...]}``. Live-grounded on
    # .205 (smtp_ng.send_email_new type/body_type, fortigate-firewall
    # block_ip.method, etc.): one level deep across 50 connectors (no nesting),
    # but typed RECURSIVELY so each sub-param is itself a validated
    # ``OperationParam`` — otherwise sub-params land in ``__pydantic_extra__``
    # as raw dicts, BYPASSING this model's display-text coercion (the
    # activedirectory.add_group_members.object_dn placeholder arrives as a
    # 1-element list ``['CN=user-name,...']``; un-coerced it's a list a SQLite
    # write can't bind, truncating the whole warm_catalog connector section).
    onchange: dict[str, list[OperationParam]] = Field(default_factory=dict)

    @field_validator("title", "description", "tooltip", "placeholder", mode="before")
    @classmethod
    def _coerce_display_text(cls, v: Any) -> Any:
        """Delegate to the module-level :func:`_coerce_display_text` (shared with
        :class:`ConfigSchemaField`). See that function's docstring for the
        live-grounded coercion rules."""
        return _coerce_display_text(v)


# Self-recursive ``onchange`` (dict[str, list[OperationParam]]) needs the
# class to exist in the module namespace before pydantic can resolve the
# forward ref — rebuild now that ``OperationParam`` is defined.
OperationParam.model_rebuild()


class Operation(ApiResult):
    """One action a connector exposes, from its definition's ``operations[]``.

    Richer than :class:`~pyfsr.models._system.ConnectorOperation` (the Content-Hub
    catalog shape) — this is the *runtime* definition, carrying typed
    :class:`OperationParam` inputs. ``visible``/``enabled`` default to ``True``
    when omitted. Dict-compatible (``op["operation"]`` still works).
    """

    operation: str | None = None
    title: str | None = None
    description: str | None = None
    annotation: str | None = None
    category: str | None = None
    # Visible/enabled unless the definition explicitly turns them off (matches
    # the wire default and the warm-catalog reader's ``op.get("visible", True)``).
    visible: bool = True
    enabled: bool = True
    parameters: list[OperationParam] = Field(default_factory=list)
    output_schema: Any = None


class ConnectorDefinition(ApiResult):
    """A connector's full definition (config schema + operations).

    Returned by :meth:`~pyfsr.api.connectors.ConnectorsAPI.definition` — the
    ``POST /api/integration/connectors/<name>/<version>/?format=json`` payload
    ``warm_catalog`` reads to sync the installed connector catalog. ``category``
    may arrive as a string or a list; both are tolerated. Curated fields are
    typed; ``config_schema``/``configuration`` stay loose (shape varies by
    connector). Dict-compatible.
    """

    name: str | None = None
    version: str | None = None
    label: str | None = None
    description: str | None = None
    publisher: str | None = None
    category: str | list[str] | None = None
    active: bool | None = None
    cs_approved: bool | None = None
    cs_compatible: bool | None = None
    operations: list[Operation] = Field(default_factory=list)
    config_schema: ConfigSchema = Field(default_factory=lambda: ConfigSchema())
    configuration: Any = None


# Self-recursive ``ConnectorDefinition.config_schema`` forward-ref — rebuild
# after ``ConfigSchema``/``ConfigSchemaField`` are defined (end of this block).
class ConfigSchemaField(ApiResult):
    """One field of a connector's configuration schema, from a connector
    definition's ``config_schema.fields[]``.

    Live-grounded on .205 (40 connectors) and cross-checked against
    ``fsr_connector_toolkit/CONNECTOR_BUILDING_GUIDE.md`` §"Configuration
    Parameters" (universal-property survey of 711 connectors / 29,066 fields):
    every field carries ``name``/``type``/``title``/``visible``/``editable``/
    ``required``; ``description``/``value``/``tooltip``/``placeholder`` are
    common; ``options``/``onchange`` appear on ``select``/conditional fields;
    Pattern-B ``isOnChange``/``onChangeParam``/``onChangeOption`` and the
    dynamic-options ``apiOperation``/``apiOnchange``/``renderer_type``/
    ``validation`` are declared too. ``type`` is one of the documented types
    (``text``/``password``/``checkbox``/``integer``/``select``/``json``/
    ``multiselect``/``file`` + aliases) ��� left as ``str | None`` rather than an
    enum because the guide documents undocumented/legacy aliases (§"Type
    aliases") and typos in the wild that the loader drops. ``onchange`` reveals
    sub-fields when a controlling selection is made (keyed by the parent's
    option value, or ``"true"``/``"false"`` for checkboxes) — typed RECURSIVELY
    so sub-fields validate too (microsoft-graph auth trees nest), mirroring
    :class:`OperationParam`. Display fields use the same
    :func:`_coerce_display_text` coercion. Note: the guide warns of real
    field-name typos in published connectors (``tootltip``, ``Description``, …);
    the FortiSOAR loader is key-strict and drops them, so they're deliberately
    NOT modeled — they fall through to ``extra`` as the loader itself does.
    """

    name: str | None = None
    type: str | None = None
    title: str | None = None
    description: str | None = None
    tooltip: str | None = None
    placeholder: str | None = None
    required: bool = False
    value: Any = None
    visible: bool = True
    editable: bool = True
    # ``options`` elements are plain strings OR ``{"value"/"title": ...}`` dicts
    # (see connectors._option_values), so stay ``list[Any]``.
    options: list[Any] = Field(default_factory=list)
    onchange: dict[str, list[ConfigSchemaField]] = Field(default_factory=dict)
    # Pattern-B conditional fields (CONNECTOR_BUILDING_GUIDE §"Conditional
    # Fields" Pattern B): a child declares which parent value makes it visible,
    # instead of the parent carrying an ``onchange`` block. ``onChangeParam`` is
    # the parent's ``title`` (not its ``name``); ``onChangeOption`` is the parent
    # value that triggers visibility. Declared (not ``extra``) so the walker
    # that resolves conditionals can read them as typed fields.
    isOnChange: bool = False
    onChangeParam: str | None = None
    onChangeOption: str | None = None
    # Dynamic dropdowns (§"Dynamic Options from an Operation"): ``apiOperation``
    # names the connector operation whose result populates the choices;
    # ``apiOnchange=True`` re-calls it whenever a sibling field changes. ``renderer_type``
    # overrides the default render (e.g. ``richtext``+``"html"``); ``validation``
    # is an integer-field constraint block.
    apiOperation: str | None = None
    apiOnchange: bool = False
    renderer_type: str | None = None
    validation: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "description", "tooltip", "placeholder", mode="before")
    @classmethod
    def _coerce_display_text(cls, v: Any) -> Any:
        return _coerce_display_text(v)


class ConfigSchema(ApiResult):
    """A connector's configuration schema — always ``{fields: [...]}`` on the
    wire (live-verified: ``fields`` is the sole top-level key across 40
    connectors). Typed so the config-field walkers
    (``default_config``/``validate_config``/``required_config_fields``) read
    validated :class:`ConfigSchemaField` objects instead of raw dicts — closing
    the ``dict[str, Any]`` escape hatch ``ConnectorDefinition.config_schema``
    used to be.
    """

    fields: list[ConfigSchemaField] = Field(default_factory=list)


# Resolve the recursive/forward refs now that all config-schema classes exist:
# ``ConfigSchemaField.onchange`` references itself, and ``ConnectorDefinition``
# (defined above) references ``ConfigSchema`` (defined here) via a forward ref.
ConfigSchemaField.model_rebuild()
ConnectorDefinition.model_rebuild()


# ---------------------------------------------------------------------------
# Connector configuration record
# ---------------------------------------------------------------------------


class ConnectorConfig(ApiResult):
    """A connector configuration record from ``/api/integration/configuration/``.

    Returned by ``create_configuration()``, ``update_configuration()``, and
    ``list_configurations()``.  ``config`` is the live field map — its shape
    varies by connector.
    """

    id: int | None = None
    config_id: str | None = None
    name: str | None = None
    default: bool = False
    # Active flag: 1 == active. Verified int on the live wire (list, create, and
    # 7.x update all return the saved record). Kept as int so callers can rely on
    # ``cfg.status == 1``; unknown *fields* are still tolerated via extra="allow".
    # FortiSOAR 8.0's *update* echo instead nests an async op-envelope here
    # (``{"status":"finished","message":...}``) — that conveys no active-flag, so
    # the validator coerces any non-int (dict/str) to None rather than failing.
    status: int | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v: Any) -> int | None:
        if isinstance(v, bool):  # avoid True->1 surprises
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return None
        return None  # dict op-envelope (8.0) or anything else -> no active flag

    config: dict[str, Any] = Field(default_factory=dict)
    connector: int | None = None
    agent: str | None = None
    teams: list[Any] = Field(default_factory=list)
    remote_status: dict[str, Any] = Field(default_factory=dict)
    health_status: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class ConfigValidationError(ApiResult):
    """A single field-level error from ``validate_config()``."""

    field: str | None = None
    code: str | None = None
    message: str | None = None
    valid_options: list[Any] | None = None
    expected: str | None = None


class ConfigValidationResult(ApiResult):
    """Return value of ``client.connectors.validate_config()``.

    ``valid`` is ``True`` only when ``missing`` and ``invalid`` are both empty.
    ``unknown`` fields are reported but do not make the config invalid.
    """

    valid: bool = False
    missing: list[str] = Field(default_factory=list)
    invalid: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    errors: list[ConfigValidationError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


class HealthcheckResult(ApiResult):
    """Return value of ``client.connectors.healthcheck()``.

    ``status == "Available"`` is green.  ``status == "no-config"`` means the
    connector isn't configured on this instance (pyfsr-synthesised, not from
    the wire).
    """

    status: str | None = None
    message: str | None = None
    name: str | None = None
    version: str | None = None
    config_id: str | None = None
    request_id: str | None = None
    http_status: int | None = None
    ok: bool | None = Field(default=None, alias="_status")


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


class ExecuteResult(ApiResult):
    """Return value of ``client.connectors.execute()``.

    ``data`` is the connector's own output — its shape varies by connector and
    operation.
    """

    operation: str | None = None
    status: str | None = None
    message: str | None = None
    data: Any = None

    @property
    def ok(self) -> bool:
        """True when the connector reported success (``status == "Success"``).

        Saves callers the recurring ``str(r.status).lower() == "success"`` check.
        Note (see ``ConnectorsAPI.execute``): an agent-bound, fire-and-forget call
        can succeed with empty ``data`` — ``ok`` reflects ``status``, not ``data``.
        """
        return str(self.status).strip().lower() == "success"


# ---------------------------------------------------------------------------
# Install job status
# ---------------------------------------------------------------------------


class InstallJobStatus(ApiResult):
    """Progress record for a connector install import job.

    Returned by ``install_status()`` and ``wait_for_install()``.
    ``status == "Import Complete"`` means the install finished successfully.
    """

    status: str | None = None
    progressPercent: int | None = None
    errorMessage: str | None = None
    currentlyImporting: str | None = None


# ---------------------------------------------------------------------------
# ensure_version summary
# ---------------------------------------------------------------------------


class EnsureVersionResult(ApiResult):
    """Return value of ``client.connectors.ensure_version()``.

    ``action`` is one of ``"noop"``, ``"in_place"``, ``"restored"``,
    ``"reinstalled"``, or ``"failed"``.
    """

    action: str | None = None
    from_version: str | None = Field(default=None, alias="from")
    to: str | None = None
    backup: str | None = None
    configs_before: int = 0
    configs_after: int = 0


# ---------------------------------------------------------------------------
# Import / export jobs
# ---------------------------------------------------------------------------


class LogMessage(ApiResult):
    """A single entry from an import job's ``logMessages`` list."""

    message: str | None = None
    date: int | None = None


class ImportJobResult(ApiResult):
    """A ``/api/3/import_jobs`` record.

    Returned by ``import_config.import_file()`` and the lower-level job
    polling methods.  ``status == "Import Complete"`` means success.
    ``options`` is the server-generated import option tree (section → include
    flags).
    """

    id_iri: str | None = Field(default=None, alias="@id")
    record_type: str | None = Field(default=None, alias="@type")
    uuid: str | None = None
    id: int | None = None
    status: str | None = None
    errorMessage: str | None = None
    logMessages: list[LogMessage] = Field(default_factory=list)
    options: dict[str, Any] | list = Field(default_factory=dict)
    file: RecordIRI | dict[str, Any] | None = None
    # Client-side cross-reference (NOT a wire field): the SDK records the job
    # UUID the caller polled with, so callers using the dict-compat legacy path
    # ``result["jobUuid"]`` still get it. Declared here (typed) instead of
    # stashed into ``__pydantic_extra__`` so the field is visible on the model.
    jobUuid: str | None = None


class ExportJobResult(ApiResult):
    """A ``/api/3/export_jobs`` record.

    Returned by export polling in ``export_config``.
    ``status == "Export Complete"`` means the archive is ready for download.
    ``file`` is the ``/api/3/files/<uuid>`` record (or its IRI string) once
    the export finishes.
    """

    id_iri: str | None = Field(default=None, alias="@id")
    record_type: str | None = Field(default=None, alias="@type")
    uuid: str | None = None
    id: int | None = None
    status: str | None = None
    errorMessage: str | None = None
    fileName: str | None = None
    progressPercent: int | None = None
    currentlyExporting: str | None = None
    type: str | None = None
    file: RecordIRI | dict[str, Any] | None = None
