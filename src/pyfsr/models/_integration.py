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

from pydantic import BaseModel, ConfigDict, Field

from .types import RecordIRI


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
    # update all return the saved record). Kept strict so callers can rely on
    # ``cfg.status == 1``; unknown *fields* are still tolerated via extra="allow".
    status: int | None = None
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
