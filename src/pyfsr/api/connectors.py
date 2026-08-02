"""Connector discovery, health, and operation execution.

Wraps FortiSOAR's ``/api/integration`` surface so callers don't hand-build
execute payloads or hunt for a connector's configured version / config UUID.
Covers discovery, healthcheck, operation execution, and writing a connector's
*configuration* (its credentials) -- see :meth:`ConnectorsAPI.create_configuration`.

Accessed as ``client.connectors``.

Example:
    >>> client = demo_client()
    >>> conn = client.connectors
    >>> [c.name for c in conn.list_configured()[:3]]   # installed + configured
    ['smtp', 'code-snippet', 'mitre-attack']
    >>> conn.resolve_version("mitre-attack")            # the configured version
    '2.0.2'
    >>> conn.healthcheck("mitre-attack").status         # "Available" is green
    'Available'

    Writes (install, create_configuration, execute) need a live appliance; see
    the connectors guide for those::

        conn.install("fortinet-fortisiem", "6.1.0", wait=True)
        conn.create_configuration("fortinet-fortisiem", {...}, name="prod")
        conn.execute("virustotal", "get_reputation_ip", params={"ip": "8.8.8.8"})

.. note::
    Setting up **data ingestion** (the *Configure Data Ingestion* wizard) is
    automated by :meth:`ConnectorsAPI.data_ingest_wizard`, which reproduces what
    the UI writes: a per-configuration playbook collection, the connector's
    sample ingestion playbooks cloned and rewritten into it, the periodic task
    that fires the ``ingest`` playbook, and the ``data-import`` metadata record
    that ties them together.

.. warning::
    Execution is **synchronous only for connectors that run on the FortiSOAR
    appliance itself**. For connectors bound to a remote *agent*, the
    ``/api/integration/execute/`` call is fire-and-forget: it returns
    immediately with an in-progress status and an empty ``data``, and the real
    result is pushed over a websocket (not pollable here). ``execute()`` does
    not -- and cannot -- wait for those; don't treat an empty ``data`` from an
    agent-bound connector as failure.
"""

from __future__ import annotations

import json
import mimetypes
import re
import tarfile
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any

from ..exceptions import APIError, ConfigurationExistsError, ConfigValidationError, ResourceNotFoundError
from ..models._integration import (
    ConfigValidationResult,
    ConnectorConfig,
    ConnectorConfigSummary,
    ConnectorDefinition,
    DependencyStatus,
    EnsureVersionResult,
    ExecuteResult,
    HealthcheckResult,
    IngestionMetadata,
    IngestionPlaybooks,
    IngestionSetupResult,
    IngestionStatus,
    IngestionTeardownResult,
    InstalledConnector,
    InstallJobStatus,
    IntegrationListEnvelope,
    Operation,
    OperationParam,
)
from ..models._schedules import ScheduledTask
from ..models._system import Workflow
from ..pagination import extract_members
from ._solutionpacks import upload_solutionpack
from .base import BaseAPI


def _resolve_config_kwarg(
    config: str | None,
    config_id: str | None,
    config_name: str | None,
) -> str | None:
    """Resolve the configuration argument for :meth:`ConnectorsAPI.execute` /
    :meth:`ConnectorsAPI.healthcheck`.

    ``config=`` is canonical -- it accepts either a configuration **UUID** or a
    display **name**; the FortiSOAR server resolves both in the wire ``config``
    field (live-verified on 8.0.0: a name and a UUID both select the right
    configuration). ``config_id=`` and ``config_name=`` are deprecated aliases
    that funnel into ``config=`` with a warning. Passing more than one raises.
    """
    given = [
        (k, v)
        for k, v in (
            ("config", config),
            ("config_id", config_id),
            ("config_name", config_name),
        )
        if v is not None
    ]
    if len(given) > 1:
        names = ", ".join(k for k, _ in given)
        raise ValueError(
            "Pass a configuration UUID or name as config= -- not together with "
            f"config_id= or config_name= (those are deprecated aliases). Got: {names}"
        )
    for label, value in given:
        if not isinstance(value, str):
            raise TypeError(
                f"{label!r} on execute/healthcheck takes a configuration UUID or name "
                f"(str), got {type(value).__name__} -- the field-map 'config=' on "
                "create_configuration/upsert_configuration is a dict; use config= "
                "for the UUID or name here."
            )
    if config_id is not None:
        warnings.warn(
            "The 'config_id' keyword is deprecated; use config= (it accepts a "
            "UUID or a name -- the server resolves both).",
            DeprecationWarning,
            stacklevel=3,  # 3: caller -> public method -> here
        )
        return config_id
    if config_name is not None:
        warnings.warn(
            "The 'config_name' keyword is deprecated; use config= (it accepts a "
            "UUID or a name -- the server resolves both).",
            DeprecationWarning,
            stacklevel=3,
        )
        return config_name
    return config


#: Directory names never shipped to the appliance -- virtualenvs, test suites,
#: caches, VCS / IDE metadata, and build artifacts that bloat the tgz (a .venv
#: alone can add hundreds of MB) and have no place in a running connector.
_PACK_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        "env",
        "tests",
        "test",
        ".pytest_cache",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "node_modules",
        "dist",
        "build",
        ".eggs",
        ".DS_Store",
    }
)

#: File names / suffixes never shipped -- OS metadata, bytecode, editor swap,
#: VCS config. Suffixes are matched on the file's own name.
_PACK_EXCLUDE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".DS_Store", ".swp", ".swo", ".egg-info")

#: Exact file names never shipped (dotfile / VCS metadata that has no place
#: in a running connector).
_PACK_EXCLUDE_FILES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        ".gitignore",
        ".gitattributes",
        ".gitmodules",
        ".editorconfig",
        ".python-version",
        ".flake8",
        ".env",
    }
)


#: info.json keys the importer genuinely requires, established by probing a
#: live 8.0.0 appliance one mutation at a time (see
#: ``fortisoar/connectors/probe_info_json.py`` in the Miscellaneous repo).
#: Dropping or emptying any of these is rejected. Notably *not* required:
#: description, publisher, cs_approved, cs_compatible, category,
#: icon_small_name, icon_large_name, ingestion_supported, tags.
_INFO_REQUIRED_KEYS: tuple[str, ...] = ("name", "version", "label")

#: The importer requires three numeric version parts. ``1.0``, ``banana`` and
#: the integer ``1`` are each rejected.
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


class ConnectorPackageError(ValueError):
    """A connector source folder will not import, and we can tell before upload.

    FortiSOAR's importer wraps every failure in one catch-all message --
    *"Connector with same name is already active."* -- regardless of the real
    cause. That message is emitted even when no connector of that name exists,
    so it cannot be acted on. These checks exist to name the actual problem
    locally instead.
    """


def validate_connector_source(source_dir: str, *, strict: bool = True, blocking_only: bool = False) -> list[str]:
    """Check a connector source folder against what the importer requires.

    Returns the list of problems found, worst first. With ``strict`` (the
    default) any problem raises :class:`ConnectorPackageError` instead, since
    uploading a package with any of these produces a server-side error that
    identifies none of them.

    The blocking rules were established empirically, by installing one
    mutation at a time against a live 8.0.0 appliance rather than by reading
    documentation. Blocking:

    * the folder is named **exactly** ``info.json``'s ``name``. The
      ``<name>_<version>`` form (``aws_3_1_2``) is the appliance's *installed*
      layout, not the package layout, and packing it that way fails.
    * ``info.json`` parses and has a non-empty ``name``, ``version``, ``label``.
    * ``version`` has three numeric parts -- ``1.0``, ``banana`` and ``1`` are
      each rejected.
    * ``operations`` is present and not ``null`` (an empty list is fine).
    * ``configuration`` is not ``null`` (absent or ``{}`` is fine).
    * every ``operations`` entry has an ``operation`` and a ``title``, and no
      two share an ``operation``.

    Advisory -- accepted by the importer but wrong or dangerous anyway:
    a declared operation with no implementation in ``operations.py`` (installs
    cleanly, fails at runtime), a missing ``__init__.py``, ``connector.py`` or
    ``operations.py``, an icon named but not shipped, ``cs_approved`` left
    ``true``, and a ``name`` with uppercase or spaces.

    Not required by the importer at all, despite appearances: ``description``,
    ``publisher``, ``cs_compatible``, ``category``, ``ingestion_supported``,
    ``tags``, and per-operation ``description`` / ``category`` / ``annotation``
    / ``enabled`` / ``parameters`` / ``output_schema``.

    Problems are either *blocking* -- the import is guaranteed to fail, and
    the server will not tell you why -- or advisory. ``blocking_only`` reports
    just the former, which is what :func:`pack_connector` enforces so that a
    rough working folder still packs.

    Args:
        source_dir: the connector folder (the one holding ``info.json``).
        strict: raise instead of returning the list.
        blocking_only: report only problems that guarantee an import failure.

    Returns:
        Problem descriptions; empty when the folder looks importable.

    Raises:
        ConnectorPackageError: with ``strict``, when anything is wrong.
        FileNotFoundError: if ``source_dir`` is not a directory.
    """
    src = Path(source_dir).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"connector source folder not found: {src}")

    problems: list[str] = []
    advisories: list[str] = []
    info_path = src / "info.json"
    if not info_path.exists():
        problems.append(f"{src} has no info.json -- not a connector source folder")
        if strict:
            raise ConnectorPackageError(problems[0])
        return problems

    try:
        info = json.loads(info_path.read_text())
    except ValueError as err:
        problems.append(f"info.json is not valid JSON: {err}")
        if strict:
            raise ConnectorPackageError(problems[0]) from err
        return problems

    # Blocking: probed by installing one mutation at a time against a live
    # 8.0.0 appliance. Each of these was rejected; everything not listed here
    # was accepted, however much the docs imply otherwise.
    for key in _INFO_REQUIRED_KEYS:
        if not info.get(key):
            problems.append(f"info.json {key!r} is missing or empty -- the importer rejects that")

    version = info.get("version")
    if version and not _SEMVER_RE.match(str(version)):
        problems.append(
            f"info.json version is {version!r} -- the importer requires three "
            "numeric parts, e.g. '1.0.0'. '1.0', 'banana' and 1 are all rejected."
        )

    if "configuration" in info and info["configuration"] is None:
        problems.append("info.json configuration is null -- omit the key or use {} instead")

    if "operations" in info and info["operations"] is None:
        problems.append("info.json operations is null -- omit the key or use [] instead")
    elif "operations" not in info:
        problems.append("info.json has no 'operations' key -- the importer rejects that")

    name = info.get("name")
    if name and src.name != name:
        problems.append(
            f"folder is named {src.name!r} but info.json name is {name!r} -- "
            f"they must match exactly (rename the folder to {name!r}). "
            "The <name>_<version> form is the installed layout, not the package layout."
        )

    for required in ("connector.py", "operations.py"):
        if not (src / required).exists():
            advisories.append(f"missing {required}")

    if not (src / "__init__.py").exists():
        advisories.append("missing __init__.py -- the connector will not import as a package")

    for key in ("icon_small_name", "icon_large_name"):
        icon = info.get(key)
        if icon and not (src / "images" / icon).exists():
            advisories.append(f"info.json {key} is {icon!r} but images/{icon} is not in the package")

    operations_py = ""
    if (src / "operations.py").exists():
        operations_py = (src / "operations.py").read_text()

    seen: set[str] = set()
    for index, operation in enumerate(info.get("operations") or []):
        op_name = operation.get("operation")
        if not op_name:
            problems.append(f"operations[{index}] has no 'operation' key")
            continue
        if not operation.get("title"):
            problems.append(f"operations[{index}] ({op_name}) has no 'title' -- the importer rejects that")
        if op_name in seen:
            problems.append(f"duplicate operation {op_name!r}")
        seen.add(op_name)
        # The importer does NOT check that the code implements the operation:
        # a name with no implementation installs cleanly and fails at runtime.
        if operations_py and op_name not in operations_py:
            advisories.append(
                f"operation {op_name!r} is declared in info.json but does not appear "
                "in operations.py -- the importer accepts this and it fails only when run"
            )

    if info.get("cs_approved"):
        advisories.append(
            "cs_approved is true -- a locally built package should set it false. "
            "The importer accepts it either way, but it claims a certification the "
            "bundle does not carry, and the appliance still needs its custom-connector "
            "gate on (system_settings.set_development_mode(connectors=True))"
        )

    if name and (name != name.lower() or " " in name):
        advisories.append(
            f"name {name!r} has uppercase or spaces -- the importer accepts it, but the "
            "name is used as an identifier throughout the platform; prefer lowercase-with-dashes"
        )

    found = problems if blocking_only else problems + advisories
    if found and strict:
        raise ConnectorPackageError(f"{src} will not import:\n  - " + "\n  - ".join(found))
    return found


def pack_connector(source_dir: str, output: str | None = None, *, validate: bool = True) -> str:
    """Bundle a connector source folder into a SOAR-importable ``.tgz``.

    FortiSOAR expects a connector archive to contain exactly **one top-level
    directory** (named for the connector) holding ``info.json``, ``connector.py``,
    ``operations.py``, etc. -- e.g. ``flatten-json/info.json``. This packs
    ``source_dir`` as that top-level directory, preserving its own name.

    Non-deployment artifacts are excluded so the bundle stays small and clean:

    * **Virtualenvs** -- ``.venv``, ``venv``, ``.env``, ``env``
    * **Test suites** -- ``tests``, ``test``, ``.pytest_cache``, ``.tox``
    * **Caches** -- ``__pycache__``, ``.mypy_cache``, ``.ruff_cache``
    * **VCS / IDE** -- ``.git``, ``.hg``, ``.svn``, ``.idea``, ``.vscode``
    * **Build output** -- ``dist``, ``build``, ``.eggs``, ``node_modules``
    * **OS / editor cruft** -- ``.DS_Store``, ``*.pyc``, ``*.pyo``, ``*.swp``

    Args:
        source_dir: path to the connector folder (the one containing ``info.json``).
        output: destination ``.tgz`` path. Defaults to ``<source_dir>.tgz``
            alongside the folder.
        validate: run :func:`validate_connector_source` first and refuse to
            build a package the importer will reject. Pass ``False`` only to
            reproduce a known-bad bundle deliberately.

    Returns:
        The path to the written ``.tgz``.

    Raises:
        FileNotFoundError: if ``source_dir`` doesn't exist.
        ValueError: if ``source_dir`` has no ``info.json`` (not a connector).
        ConnectorPackageError: with ``validate``, when the folder would fail to
            import -- the importer's own error names no cause, so this catches
            it locally instead.
    """
    src = Path(source_dir).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"connector source folder not found: {src}")
    if not (src / "info.json").exists():
        raise ValueError(f"{src} has no info.json -- not a connector source folder")
    if validate:
        validate_connector_source(str(src), strict=True, blocking_only=True)
    out = Path(output) if output else src.with_suffix(".tgz")

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        # Exclude any path component in the blocklist (e.g. .venv/lib/...).
        if any(p in _PACK_EXCLUDE_DIRS for p in parts):
            return None
        name = parts[-1]
        if name in _PACK_EXCLUDE_FILES:
            return None
        if name.endswith(_PACK_EXCLUDE_SUFFIXES):
            return None
        return info

    with tarfile.open(out, "w:gz") as tar:
        # arcname == folder name so the archive has a single top-level dir.
        tar.add(src, arcname=src.name, filter=_filter)
    return str(out)


#: Import-job statuses that mean a Content-Hub install has stopped running.
_INSTALL_TERMINAL = frozenset({"import complete", "completed", "failed", "error"})

#: Fields worth fetching when polling an install/import job's progress.
_INSTALL_FIELDS = "errorMessage,status,progressPercent,file,currentlyImporting,options"


def _field_label(field: dict[str, Any]) -> str:
    """Human label for a config field -- its ``title``, falling back to ``name``."""
    return field.get("title") or field.get("name") or "?"


def _option_values(field: dict[str, Any]) -> list[Any]:
    """The accepted values of a ``select`` field. Options are usually plain
    strings, but tolerate ``{"value"/"title": ...}`` dict forms too."""
    out: list[Any] = []
    for opt in field.get("options") or []:
        if isinstance(opt, dict):
            out.append(opt.get("value", opt.get("title")))
        else:
            out.append(opt)
    return out


def _value_fits_type(ftype: str, value: Any) -> bool:
    """Best-effort type check for a *present* config value. Lenient on purpose --
    FortiSOAR stores most values as strings -- flagging only clearly-wrong ones.
    Emptiness is handled separately by the required check."""
    if ftype == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, str) and value.strip().lstrip("+-").isdigit()
    if ftype == "checkbox":
        return isinstance(value, bool) or (
            isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0", "yes", "no"}
        )
    if ftype == "json":
        if isinstance(value, (dict, list)):
            return True
        if isinstance(value, str):
            try:
                json.loads(value)
                return True
            except (ValueError, TypeError):
                return False
        return False
    # text / password / select(no options) / email / etc. -- no value constraint here.
    return True


def _onchange_key(value: Any) -> str | None:
    """Coerce a config value to its ``onchange`` map key. Checkbox values are
    keyed as the strings ``"true"`` / ``"false"``; everything else by ``str``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _type_default(ftype: str | None) -> Any:
    """A type-appropriate empty default for a field with no declared ``value``."""
    if ftype == "checkbox":
        return False
    if ftype == "integer":
        return 0
    if ftype == "json":
        return {}
    return ""


def _missing_message(field: dict[str, Any], condition: dict[str, Any] | None) -> str:
    """Guidance for a missing required field, naming the selection that requires
    it when the field lives in a conditional ``onchange`` branch."""
    msg = f"{_field_label(field)} is required"
    if condition:
        msg += f" when {condition['label']} = {condition['value']!r}"
    return msg


def _format_validation_error(connector: str, check: ConfigValidationResult) -> str:
    """Render a :meth:`ConnectorsAPI.validate_config` result as a multi-line,
    user-facing error for the create/update raise path."""
    lines = [f"{connector!r} configuration is invalid:"]
    for err in check.errors or []:
        if err.code == "unknown_field":
            continue  # non-fatal; don't fail the write on extra keys
        suffix = ""
        if err.valid_options is not None:
            suffix = f" (valid: {', '.join(map(str, err.valid_options))})"
        lines.append(f"  - {err.message}{suffix}")
    lines.append("(see client.connectors.config_schema(name) for the full schema)")
    return "\n".join(lines)


class ConnectorsAPI(BaseAPI):
    """Live connector listing, healthcheck, and operation execution."""

    def __init__(self, client):
        super().__init__(client)
        self._configured: list[InstalledConnector] | None = None

    def clear_cache(self) -> None:
        """Drop the cached configured-connector listing."""
        self._configured = None

    # ------------------------------------------------------------- install
    def install(
        self,
        name: str,
        version: str,
        *,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Install a connector from Content Hub by ``name`` + ``version``.

        Posts ``{"name", "version"}`` to ``POST /api/3/solutionpacks/install`` --
        the same call the Content Hub *Install* button makes. The install runs
        asynchronously as an *import job*; the response carries that job's id
        (poll it with :meth:`install_status`). Discover installable
        ``name``/``version`` pairs via
        ``client.content_hub.search_available_connectors(...)``.

        Args:
            name: connector name (e.g. ``"fortinet-fortisiem"``).
            version: the Content Hub version to install (e.g. ``"6.1.0"``).
            wait: block until the import job reaches a terminal status.
            interval: seconds between polls when ``wait`` (default 3).
            timeout: give up waiting after this many seconds (default 300).

        Returns:
            With ``wait=False``, the install response (carrying the import-job
            id). With ``wait=True``, the final :meth:`install_status` payload --
            check its ``status`` (``"Import Complete"`` means success). The
            configured-connector cache is dropped on a successful wait.
        """
        resp = self.client.post("/api/3/solutionpacks/install", data={"name": name, "version": version})
        resp = resp if isinstance(resp, dict) else {"result": resp}
        if not wait:
            return resp
        job_id = _import_job_id(resp)
        if not job_id:
            return resp
        final = self.wait_for_install(job_id, interval=interval, timeout=timeout)
        if str(final.status or "").strip().lower() in _INSTALL_TERMINAL:
            self.clear_cache()
        return final

    def install_from_file(
        self,
        path: str,
        *,
        replace: bool = False,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Install a connector by uploading its ``.tgz`` bundle.

        The multipart-upload form of ``POST /api/3/solutionpacks/install`` (the
        same endpoint :meth:`install` posts a name to). Sends the archive as
        ``file`` with the required ``$type=connector`` query parameter; pass
        ``replace=True`` to re-install over an existing version (``$replace=true``).
        The response carries the full connector record -- including the integer
        ``id`` other calls need.

        Use this for connectors not in Content Hub (a locally built or
        custom ``.tgz``); use :meth:`install` to pull a published one by name.

        Args:
            path: filesystem path to the connector ``.tgz``.
            replace: overwrite an already-installed version of the same name.
            wait: block until the import job reaches a terminal status.
            interval: seconds between polls when ``wait`` (default 3).
            timeout: give up waiting after this many seconds (default 300).

        Returns:
            With ``wait=False``, the install response (the connector record,
            carrying any import-job id). With ``wait=True``, the final
            :meth:`install_status` payload. The configured-connector cache is
            dropped on a successful upload/wait.

        Raises:
            FileNotFoundError: if ``path`` doesn't exist.

        Example:
            >>> import io, tarfile, tempfile
            >>> from pathlib import Path
            >>> client = demo_client()
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     tgz = Path(tmp) / "demo-connector.tgz"
            ...     with tarfile.open(tgz, "w:gz") as tar:
            ...         info = tarfile.TarInfo("demo-connector/info.json")
            ...         data = b'{"name": "demo-connector", "version": "1.0.0"}'
            ...         info.size = len(data)
            ...         tar.addfile(info, io.BytesIO(data))
            ...     resp = client.connectors.install_from_file(str(tgz))
            >>> result = (resp["id"], resp["name"], resp["version"])
            >>> result
            (42, 'demo-connector', '1.0.0')
        """
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        # NOTE: kept on the mimetypes-guessed content-type (unlike widgets, which
        # default to the live-verified "application/gzip") to avoid changing this
        # long-working path's wire behavior without re-verifying live against a
        # real connector bundle -- see upload_solutionpack's docstring.
        resp = upload_solutionpack(self.client, path, type_="connector", replace=replace, content_type=mime_type)
        self.clear_cache()
        if not wait:
            return resp
        job_id = _import_job_id(resp)
        if not job_id:
            return resp
        return self.wait_for_install(job_id, interval=interval, timeout=timeout)

    def install_from_dir(
        self,
        source_dir: str,
        *,
        replace: bool = True,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Pack a connector source folder and upload it in one step.

        Bundles ``source_dir`` with :func:`pack_connector` into a temporary
        ``.tgz`` and hands it to :meth:`install_from_file`. Convenience for the
        build-test loop on a locally edited connector; defaults to
        ``replace=True`` since you're almost always re-pushing the same name.

        Args mirror :meth:`install_from_file` plus ``source_dir`` (the connector
        folder containing ``info.json``). Returns the install response.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tgz = pack_connector(source_dir, output=str(Path(tmp) / "bundle.tgz"))
            return self.install_from_file(tgz, replace=replace, wait=wait, interval=interval, timeout=timeout)

    def install_status(self, job_id: str) -> InstallJobStatus:
        """Fetch a connector install's import-job progress.

        ``GET /api/3/import_jobs/{job_id}`` (selecting just the progress fields).
        ``status == "Import Complete"`` means the install finished.
        """
        resp = self.client.get(f"/api/3/import_jobs/{job_id}", params={"__selectFields": _INSTALL_FIELDS})
        return InstallJobStatus.model_validate(resp if isinstance(resp, dict) else {"result": resp})

    def wait_for_install(self, job_id: str, *, interval: float = 3.0, timeout: float = 300.0) -> InstallJobStatus:
        """Poll an install import job until it reaches a terminal status.

        Returns the latest :meth:`install_status` payload. On timeout, returns
        the last poll with a non-terminal ``status`` rather than raising.
        """
        deadline = time.monotonic() + timeout
        status = self.install_status(job_id)
        while str(status.status or "").strip().lower() not in _INSTALL_TERMINAL and time.monotonic() < deadline:
            time.sleep(interval)
            status = self.install_status(job_id)
        return status

    def uninstall(self, connector: str, *, refresh: bool = True) -> None:
        """Uninstall a connector from the **appliance** (its self-agent).

        ``DELETE /api/integration/connectors/{id}/`` -- the integer install id is
        resolved from ``connector`` (a name-only call won't work). The trailing
        slash is mandatory; the endpoint returns 204 on success. To remove a
        connector from a remote *agent* instead, use
        :meth:`~pyfsr.api.agents.AgentsAPI.uninstall_connector`.

        Raises ``ValueError`` if the connector isn't installed.

        Example:
            >>> client = demo_client()
            >>> client.connectors.uninstall("virustotal")
        """
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        self.client.delete(f"/api/integration/connectors/{connector_id}/")
        if refresh:
            self.clear_cache()

    def ensure_version(
        self,
        name: str,
        version: str,
        *,
        bundle_path: str | None = None,
        auto_fetch: bool = True,
        backup_dir: str | None = None,
        allow_uninstall_fallback: bool = False,
        wait: bool = True,
        interval: float = 3.0,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Make ``name`` be installed at exactly ``version``, preserving configs.

        The safe way to change a connector's version -- including a **downgrade** --
        without losing its saved configurations. An in-place install (upgrade or
        downgrade) preserves configs on its own; this method additionally takes a
        Configuration-Export backup first and, if the version swap drops or
        shrinks the config set, restores it from that backup (re-creating configs
        with their original ``config_id`` so playbook references survive).

        Steps:

        1. If already at ``version``, no-op.
        2. If installed *and* configured, export a backup ``.zip`` (configs +
           encrypted secrets) via ``client.export_config.export_connector``.
        3. Install ``version`` in place -- from ``bundle_path`` if given (a local
           ``.tgz``/zip), else by name from Content Hub; if Content Hub won't
           serve that version and ``auto_fetch`` is set (default), the exact-
           version ``.tgz`` is downloaded from the public repo and installed.
        4. Verify. If configs survived, done. If they didn't (downgrade schema
           drift, or a forced replace), re-import the backup.
        5. Only if the in-place install didn't reach ``version`` *and*
           ``allow_uninstall_fallback`` is set: uninstall (destroys configs),
           reinstall, then restore configs from the backup.

        Args:
            name: connector machine name (e.g. ``"code-snippet"``).
            version: target version (e.g. ``"2.1.5"``).
            bundle_path: optional local connector archive to install instead of
                pulling ``version`` from Content Hub. Usually unnecessary now --
                when Content Hub won't serve the target, ``auto_fetch`` downloads
                the exact-version ``.tgz`` from the public repo for you.
            auto_fetch: when no ``bundle_path`` is given and the by-name Content
                Hub install fails, download ``version`` from the public content
                repository (:mod:`pyfsr.repo`) and install that. On by default;
                set False to require Content Hub / an explicit bundle.
            backup_dir: directory to write the backup ``.zip`` into (default cwd).
            allow_uninstall_fallback: permit the destructive uninstall→reinstall
                path if an in-place install can't reach ``version``. Off by
                default -- leaving the connector untouched is safer than a wipe.
            wait: block on installs/imports (default True).
            interval: poll interval for the install wait.
            timeout: per-install/-import timeout in seconds.

        Returns:
            A summary dict::

                {"action": "noop"|"in_place"|"restored"|"reinstalled"|"failed",
                 "from": <old version or None>, "to": <resolved version>,
                 "target": version, "backup": <path or None>,
                 "configs_before": N, "configs_after": M}
        """
        import os

        cur = self.resolve_version(name)
        if cur == version:
            n = len(self.configurations(name))
            return {
                "action": "noop",
                "from": cur,
                "to": cur,
                "backup": None,
                "configs_before": n,
                "configs_after": n,
            }

        installed = cur is not None
        configs_before = self.configurations(name) if installed else []

        backup_path: str | None = None
        if configs_before:
            out = os.path.join(backup_dir, f"{name}-{cur}-backup.zip") if backup_dir else None
            backup_path = self.client.export_config.export_connector(name, output_path=out)

        def _do_install() -> None:
            if bundle_path:
                self.install_from_file(bundle_path, replace=True, wait=wait, interval=interval, timeout=timeout)
                return
            try:
                self.install(name, version, wait=wait, interval=interval, timeout=timeout)
            except Exception:
                # Content Hub wouldn't serve ``version`` in place -- fall back to
                # downloading the exact-version .tgz from the public repo and
                # installing that, so the caller doesn't have to fetch by hand.
                if not auto_fetch:
                    raise
                from .. import repo as _repo

                fetched = _repo.download_connector(name, version, backup_dir)
                self.install_from_file(fetched, replace=True, wait=wait, interval=interval, timeout=timeout)

        _do_install()
        self.clear_cache()
        new = self.resolve_version(name)
        configs_after = self.configurations(name)

        # In-place install reached the target -- restore configs only if the swap
        # lost some (a clean in-place change keeps them).
        if new == version:
            if backup_path and len(configs_after) < len(configs_before):
                self.client.import_config.import_file(backup_path, wait=True)
                self.clear_cache()
                configs_after = self.configurations(name)
                return self._ensure_summary("restored", cur, version, backup_path, configs_before, configs_after)
            return self._ensure_summary("in_place", cur, version, backup_path, configs_before, configs_after)

        # In-place didn't take -- destructive fallback, only if allowed.
        if allow_uninstall_fallback:
            if self.resolve_connector_id(name) is not None:
                self.uninstall(name)
            _do_install()
            self.clear_cache()
            new = self.resolve_version(name)
            if backup_path:
                self.client.import_config.import_file(backup_path, wait=True)
                self.clear_cache()
            configs_after = self.configurations(name)
            action = "reinstalled" if new == version else "failed"
            return self._ensure_summary(action, cur, new, backup_path, configs_before, configs_after)

        return self._ensure_summary("failed", cur, new, backup_path, configs_before, configs_after)

    @staticmethod
    def _ensure_summary(
        action: str,
        old: str | None,
        new: str | None,
        backup: str | None,
        before: list,
        after: list,
    ) -> EnsureVersionResult:
        return EnsureVersionResult.model_validate(
            {
                "action": action,
                "from": old,
                "to": new,
                "backup": backup,
                "configs_before": len(before),
                "configs_after": len(after),
            }
        )

    def connector_detail(self, connector: str) -> dict[str, Any]:
        """Fetch a connector's full record by id (operations-discovery endpoint).

        ``POST /api/integration/connectors/{id}/`` with a ``{}`` body -- the
        spec-canonical way to enumerate a connector's installed operations.
        Returns the full record: ``operations[]`` (each with ``operation``,
        ``title``, ``description``, ``parameters[]``, ``output_schema``),
        ``configuration[]`` (each with ``config_id``, ``name``, ``config``,
        ``agent``), and ``config_schema``. GET is forbidden and an empty body
        415s, so this always POSTs ``{}``.

        Prefer this over :meth:`definition` when you have an installed connector
        and want exactly what the appliance reports for it. Raises ``ValueError``
        if the connector isn't installed.

        Example:
            >>> client = demo_client()
            >>> detail = client.connectors.connector_detail("smtp")
            >>> (detail["name"], detail["version"])
            ('smtp', '2.6.0')
            >>> [op["operation"] for op in detail["operations"][:2]]
            ['send_email_new', 'send_email']
        """
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        resp = self.client.post(f"/api/integration/connectors/{connector_id}/", data={})
        return resp if isinstance(resp, dict) else {"result": resp}

    # ------------------------------------------------------------- discovery
    def list_configured(self, *, refresh: bool = False) -> list[InstalledConnector]:
        """Installed + configured connectors.

        Cached after the first call; pass ``refresh=True`` to re-fetch.

        Example:
            >>> client = demo_client()
            >>> [c.name for c in client.connectors.list_configured()[:3]]
            ['smtp', 'code-snippet', 'mitre-attack']
        """
        if self._configured is not None and not refresh:
            return self._configured
        # The endpoint pages at ``page_size`` (default 30) and ignores ``$limit``
        # -- walk every page so a connector past the first 30 isn't silently
        # dropped (which would make resolve_version/healthcheck miss it).
        out: list[InstalledConnector] = []
        page = 1
        page_size = 100
        while True:
            env = IntegrationListEnvelope.parse(
                self.client.get(
                    "/api/integration/connectors/",
                    params={"page": page, "page_size": page_size},
                )
            )
            for m in env.data:
                # Some versions label the field "title" rather than "label".
                if isinstance(m, dict) and "label" not in m and "title" in m:
                    m = dict(m, label=m["title"])
                out.append(InstalledConnector.model_validate(m))
            if not env.data:
                break
            if env.totalItems is not None:
                if len(out) >= env.totalItems:
                    break
            elif not env.has_next and len(env.data) < page_size:
                break
            page += 1
        self._configured = out
        return out

    def list_configurations(
        self,
        *,
        name: str | None = None,
        connector: str | int | None = None,
        active: bool | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[ConnectorConfig]:
        """List connector configuration records via ``GET /api/integration/configuration/``.

        The dedicated, filterable configurations endpoint (distinct from the
        connector-derived view of :meth:`configurations`). Each entry carries
        ``id`` (int), ``config_id`` (uuid), ``connector`` (int connector id),
        ``agent`` (set when remote), and ``config`` (the field map). Returns the
        ``data[]`` array (this endpoint is the custom ``{status, totalItems,
        data[]}`` envelope, not Hydra).

        Filters:

        * ``name`` -- the **configuration's** name (e.g. ``"Branch FortiManager"``),
          i.e. what you passed as ``name`` to :meth:`upsert_configuration`.
        * ``connector`` -- every configuration of one connector, by machine name
          (``"fortinet-fortimanager-json-rpc"``) or integer install id.
        * ``active`` -- active configurations only.

        .. warning::
           ``name`` filters the CONFIGURATION name, **not** the connector name.
           This docstring claimed "connector name" until it was checked against a
           live appliance. The mistake is invisible at runtime: a connector name
           in ``name`` returns ``[]`` rather than raising, because the endpoint
           silently ignores filters it doesn't understand and this one simply
           matches nothing. Use ``connector=`` for that.

        ``connector`` resolves a name to its install id before querying -- the
        endpoint's ``connector`` filter is the numeric id, and a name passed
        straight through errors ("Unknown error occurred"). A not-installed
        connector yields ``[]``: it cannot have configurations.

        Example:
            >>> client = demo_client()
            >>> configs = client.connectors.list_configurations()
            >>> [(c.name, c.connector) for c in configs]
            [('localhost-postfix', 3), ('Demo', 21)]
        """
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if name is not None:
            params["name"] = name
        if connector is not None:
            # bool is an int subclass -- connector=True would query id 1.
            if isinstance(connector, bool):
                raise TypeError("connector must be a machine name or install id, not a bool")
            if isinstance(connector, int):
                connector_id: int | None = connector
            else:
                connector_id = self.resolve_connector_id(connector)
                if connector_id is None:
                    return []
            params["connector"] = connector_id
        if active is not None:
            params["active"] = active
        env = IntegrationListEnvelope.parse(self.client.get("/api/integration/configuration/", params=params))
        return [ConnectorConfig.model_validate(r) for r in env.data]

    def _find_configured(self, connector: str) -> InstalledConnector | None:
        return next((c for c in self.list_configured() if c.name == connector), None)

    def find_installed_connectors(self, query: str) -> list[InstalledConnector]:
        """Search *installed* connectors by partial, case-insensitive match.

        Scoped to connectors installed on this appliance (the
        :meth:`list_configured` set) -- it does **not** see the Content Hub
        catalog of installable-but-not-installed connectors. For that, use
        ``client.content_hub.search_available_connectors(...)``.

        Matches ``query`` as a substring of either the connector ``name`` or its
        ``label`` -- so ``"fortigate"`` finds ``fortigate-firewall`` (label
        ``"Fortinet FortiGate"``) regardless of hyphen/underscore or casing.
        Returns the matching :meth:`list_configured` entries (possibly empty),
        ordered with exact ``name`` matches first.

        Useful when you don't know a connector's exact machine name -- note that
        :meth:`resolve_version` and friends require the exact ``name``, while the
        human-facing label differs (``"Fortinet FortiGate"`` vs
        ``"fortigate-firewall"``).
        """

        def norm(s: str | None) -> str:
            # fold case and treat '-', '_', and whitespace as interchangeable so
            # 'fortigate_firewall', 'FortiGate', and 'forti gate' all match.
            return re.sub(r"[-_\s]+", "-", (s or "").strip().lower())

        q = norm(query)
        hits = [c for c in self.list_configured() if q in norm(c.name) or q in norm(c.label)]
        hits.sort(key=lambda c: norm(c.name) != q)
        return hits

    def configurations(self, connector: str) -> list[ConnectorConfigSummary]:
        """List a connector's configurations (``[{config_id, name, default}]``)."""
        hit = self._find_configured(connector)
        return hit.configurations if hit else []

    def resolve_version(self, connector: str) -> str | None:
        """The configured version of ``connector`` (``None`` if not configured)."""
        hit = self._find_configured(connector)
        return hit.version if hit else None

    def resolve_connector_id(self, connector: str) -> int | None:
        """The integer install id of ``connector`` (``None`` if not installed).

        Required by :meth:`create_configuration` -- the
        ``/api/integration/configuration/`` endpoint 500s on a name-only body
        and needs this numeric id.
        """
        hit = self._find_configured(connector)
        return hit.id if hit else None

    def resolve_config(self, connector: str, config_name: str | None = None) -> str | None:
        """Return a config UUID for ``connector``.

        With ``config_name`` given, matches by name; otherwise picks the
        configuration flagged default (falling back to the first one).
        """
        configs = self.configurations(connector)
        if not configs:
            return None
        chosen = None
        if config_name:
            chosen = next((c for c in configs if c.name == config_name), None)
        if chosen is None:
            chosen = next((c for c in configs if c.default), None) or configs[0]
        return chosen.config_id if chosen else None

    # ------------------------------------------------------------- health
    def healthcheck(
        self,
        connector: str,
        *,
        version: str | None = None,
        config: str | None = None,
        config_id: str | None = None,
    ) -> HealthcheckResult:
        """Live-check whether a connector configuration is reachable.

        ``status="Available"`` is green. A 404 is normalized to
        ``status="no-config"`` meaning the connector isn't configured.

        ``config`` selects which configuration to check. It accepts either a
        configuration **UUID** or a display **name** -- the FortiSOAR server
        resolves both (live-verified on 8.0.0). Omit it to check the connector's
        *default* configuration.

        .. deprecated::
           ``config_id=`` is a deprecated alias for ``config=``. It still works
           (and emits a warning) but gains nothing -- ``config=`` accepts both a
           UUID and a name. Passing both raises.

        Example:
            >>> client = demo_client()
            >>> client.connectors.healthcheck("mitre-attack").status
            'Available'
        """
        config = _resolve_config_kwarg(config, config_id, None)
        version = version or self.resolve_version(connector)
        if not version:
            return HealthcheckResult(
                name=connector,
                status="no-config",
                message=f"{connector!r} is not configured on this instance",
            )
        path = f"/api/integration/connectors/healthcheck/{connector}/{version}/"
        params = {"config": config} if config else None
        try:
            return HealthcheckResult.model_validate(self.client.get(path, params=params))
        except Exception as e:  # noqa: BLE001 - normalize "not configured" to data
            resp = getattr(e, "response", None)
            if resp is not None and getattr(resp, "status_code", None) == 404:
                return HealthcheckResult(
                    name=connector,
                    version=version,
                    status="no-config",
                    message="no configuration on this instance",
                    http_status=404,
                )
            raise

    def healthcheck_all(
        self, connectors: list[str] | None = None, *, max_workers: int = 8
    ) -> dict[str, HealthcheckResult]:
        """Healthcheck many connectors **concurrently**, keyed by connector name.

        With ``connectors=None`` (default), checks every configured connector
        (the :meth:`list_configured` set with a resolvable version). Each check is
        an independent ``GET``, so they run in a bounded thread pool -- a fleet
        status sweep that was N round-trips becomes roughly one. A connector whose
        check raises lands as a ``status="error"``
        :class:`~pyfsr.models._integration.HealthcheckResult` so
        one failure never sinks the whole sweep.
        """
        from .._concurrency import map_threaded

        names = (
            connectors if connectors is not None else [c.name for c in self.list_configured() if c.name and c.version]
        )

        def _one(name: str) -> tuple[str, HealthcheckResult]:
            try:
                return name, self.healthcheck(name)
            except Exception as e:  # noqa: BLE001 - report, don't abort the sweep
                return name, HealthcheckResult(name=name, status="error", message=str(e))

        return dict(map_threaded(_one, names, max_workers=max_workers, on_error="raise"))

    # ------------------------------------------------------------- definition
    def definition(self, connector: str, *, version: str | None = None) -> ConnectorDefinition:
        """Fetch a connector's full definition (config schema + operations).

        ``POST /api/integration/connectors/<name>/<version>/?format=json`` (the
        endpoint forbids GET). ``version`` is resolved from the configured
        connector when omitted. The returned
        :class:`~pyfsr.models._integration.ConnectorDefinition` carries
        ``config_schema``, ``configuration``, and typed ``operations`` (each an
        :class:`~pyfsr.models._integration.Operation` with ``operation``,
        ``title``, typed ``parameters``, ``output_schema``). Dict-compatible, so
        ``defn["operations"][0]["operation"]`` still works.

        Raises ``ValueError`` if the version can't be resolved.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} is not configured; pass version= to fetch its definition")
        resp = self.client.post(f"/api/integration/connectors/{connector}/{version}/?format=json", data={})
        return ConnectorDefinition.model_validate(resp if isinstance(resp, dict) else {})

    def operations(self, connector: str, *, version: str | None = None) -> list[Operation]:
        """List a connector's operations (the ``operations`` of :meth:`definition`).

        Returns typed, dict-compatible
        :class:`~pyfsr.models._integration.Operation` objects -- each carries
        ``operation`` (the api name), ``title``, ``description``, typed
        ``parameters`` (:class:`~pyfsr.models._integration.OperationParam`), and
        ``output_schema``.
        """
        defn = self.definition(connector, version=version)
        return defn.operations

    def action_ui_schema(
        self,
        connector: str,
        operation: str,
        *,
        version: str | None = None,
        required_only: bool = False,
        selections: dict[str, Any] | None = None,
    ) -> list[OperationParam]:
        """The input params a UI/agent must render to stage one connector action.

        Resolves ``connector``'s definition, finds ``operation`` by its api name,
        and returns its :meth:`~pyfsr.models.Operation.ui_params` --
        the visible params, required-first, deduped across conditional groups,
        each carrying its ``type``/``title``/``required`` and (for a ``select``)
        its :meth:`~pyfsr.models.OperationParam.select_options`.

        This is the "connector action UI schema" widget and tooling authors were
        re-deriving by hand from the raw definition.

        Pass ``selections`` (a ``{param_name: chosen_value}`` map of what the
        user has picked so far) to also include the sub-params those choices
        reveal via each ``select``'s ``onchange`` map -- so you render only the
        fields needed for the current state. Without it, only the base form is
        returned. Pass ``required_only=True`` for just the required inputs. See
        :meth:`~pyfsr.models.Operation.ui_params` for the reveal
        semantics. Raises :class:`ValueError` if the operation is not found on
        the connector.
        """
        for op in self.operations(connector, version=version):
            if op.operation == operation:
                return op.ui_params(required_only=required_only, selections=selections)
        raise ValueError(f"connector {connector!r} has no operation {operation!r}")

    def config_schema(self, connector: str, *, version: str | None = None) -> list[dict[str, Any]]:
        """Return a connector's configuration field schema (its ``config_schema``).

        Each field carries ``name``, ``type`` (``text``/``password``/``select``/
        ``checkbox``/…), ``title``, ``required``, a default ``value``, and -- for
        ``select`` fields -- an ``onchange`` map whose keys are option values and
        whose values are the *sub-fields* that become active when that option is
        chosen (e.g. FortiSIEM's ``fsm_type`` reveals ``server``/``username``/
        ``password`` only when set to ``"FortiSIEM"``). Feed the same shape to
        :meth:`validate_config` to check a config before saving.
        """
        defn = self.definition(connector, version=version)
        schema = defn.get("config_schema") or {}
        return schema.get("fields") or []

    def default_config(self, connector: str, *, version: str | None = None) -> dict[str, Any]:
        """Build a schema-complete **default** configuration dict for ``connector``.

        Walks the config schema and fills every field with its declared default
        ``value`` (or a type-appropriate empty default -- ``False`` for checkbox,
        ``0`` for integer, ``""`` otherwise), **including the conditional
        sub-fields that** ``onchange`` **reveals for those defaults**. That last
        part is the point: a connector like ``code-snippet`` requires
        ``restrict_imports`` only when ``allow_imports`` is unchecked, and that
        requirement is invisible until the *playbook run* fails with a
        ``KeyError``. Start from this dict, override what you need, and pass it to
        :meth:`create_configuration` / :meth:`upsert_configuration`.

        Example:
            >>> cfg = client.connectors.default_config("code-snippet")  # doctest: +SKIP
            >>> cfg                                   # doctest: +SKIP
            {'allow_imports': False, 'restrict_imports': ''}

        Args:
            connector: connector name (e.g. ``"code-snippet"``).
            version: connector version (resolved from configured instance if omitted).

        Returns:
            A dict with every config field populated with its default value,
            including onchange-revealed sub-fields for the default selections.
        """
        return self._materialize_config(self.config_schema(connector, version=version), {})

    def _materialize_config(self, fields: list[dict[str, Any]], overrides: dict[str, Any]) -> dict[str, Any]:
        """Resolve ``fields`` to a value map, honoring ``overrides`` and walking
        each chosen value's ``onchange`` branch so revealed fields are filled too."""
        out: dict[str, Any] = {}
        for field in fields:
            name = field.get("name")
            if not name:
                continue
            if name in overrides:
                value = overrides[name]
            else:
                value = field.get("value")
                if value is None:
                    # For select fields with options, default to the first option
                    # when no explicit value is declared.
                    if field.get("type") == "select" and field.get("options"):
                        opts = _option_values(field)
                        value = opts[0] if opts else ""
                    else:
                        value = _type_default(field.get("type"))
            out[name] = value
            branch = (field.get("onchange") or {}).get(_onchange_key(value))
            if isinstance(branch, list):
                out.update(self._materialize_config(branch, overrides))
        return out

    def required_config_fields(
        self, connector: str, config: dict[str, Any], *, version: str | None = None
    ) -> list[str]:
        """The config field names *required* given the selections in ``config``.

        Resolves ``select`` / ``checkbox`` ``onchange`` branches against the
        values already in ``config`` (so for FortiSIEM with ``fsm_type="FortiSIEM"``
        you get ``server``/``username``/``password``, and for ``code-snippet`` with
        ``allow_imports=False`` you get ``restrict_imports``). Use it to know which
        fields a user must supply.
        """
        required: list[str] = []

        def walk(fields: list[dict[str, Any]]) -> None:
            for field in fields:
                fname = field.get("name")
                if fname and field.get("required"):
                    required.append(fname)
                # onchange keys are strings ("true"/"false" for checkboxes); coerce
                # the config value the same way so checkbox branches aren't missed.
                branch = (field.get("onchange") or {}).get(_onchange_key(config.get(fname)))
                if isinstance(branch, list):
                    walk(branch)

        walk(self.config_schema(connector, version=version))
        return required

    def validate_config(
        self, connector: str, config: dict[str, Any], *, version: str | None = None
    ) -> ConfigValidationResult:
        """Check ``config`` against a connector's schema *before* saving it.

        Returns a :class:`~pyfsr.models.ConfigValidationResult` with:

        - ``missing`` -- required fields absent or blank in ``config``.
        - ``invalid`` -- fields with wrong values (bad select option, wrong type).
        - ``unknown`` -- keys in ``config`` not declared by the active schema.
        - ``errors`` -- one structured entry per problem with ``field``, ``code``,
          ``message``, and (for select fields) ``valid_options``.

        ``valid`` is ``True`` only when ``missing`` and ``invalid`` are empty.
        ``unknown`` keys are reported but don't make the config invalid.
        """
        missing: list[str] = []
        invalid: list[str] = []
        known: set[str] = set()
        errors: list[dict[str, Any]] = []
        self._collect_field_problems(
            self.config_schema(connector, version=version),
            config,
            condition=None,
            missing=missing,
            invalid=invalid,
            known=known,
            errors=errors,
        )
        unknown = [k for k in config if k not in known]
        for key in unknown:
            errors.append(
                {
                    "field": key,
                    "code": "unknown_field",
                    "message": (
                        f"{key!r} is not a recognized configuration field (typo, or gated behind a different selection)"
                    ),
                }
            )
        return ConfigValidationResult(
            valid=not missing and not invalid,
            missing=missing,
            invalid=invalid,
            unknown=unknown,
            errors=errors,
        )

    def _collect_field_problems(
        self,
        fields: list[dict[str, Any]],
        config: dict[str, Any],
        *,
        condition: dict[str, Any] | None,
        missing: list[str],
        invalid: list[str],
        known: set[str],
        errors: list[dict[str, Any]],
    ) -> None:
        """Walk a config schema collecting required/invalid/known field info.

        Recurses only into a ``select`` field's ``onchange`` branch that matches
        the value currently in ``config`` -- so conditionally-revealed fields are
        evaluated only when their controlling selection is active. ``condition``
        carries the controlling field that revealed the current branch, for
        guidance messages.
        """
        for field in fields:
            fname = field.get("name")
            if not fname:
                continue
            known.add(fname)
            ftype = (field.get("type") or "text").lower()
            value = config.get(fname)
            present = value is not None and value != ""

            if field.get("required") and not present:
                missing.append(fname)
                errors.append(
                    {
                        "field": fname,
                        "code": "missing_required",
                        "message": _missing_message(field, condition),
                    }
                )
            elif present:
                if ftype == "select" and field.get("options"):
                    allowed = _option_values(field)
                    if value not in allowed:
                        invalid.append(fname)
                        errors.append(
                            {
                                "field": fname,
                                "code": "invalid_option",
                                "message": (f"{_field_label(field)}: {value!r} is not a valid option"),
                                "valid_options": allowed,
                            }
                        )
                elif not _value_fits_type(ftype, value):
                    invalid.append(fname)
                    errors.append(
                        {
                            "field": fname,
                            "code": "wrong_type",
                            "message": (f"{_field_label(field)}: expected {ftype}, got {value!r}"),
                            "expected": ftype,
                        }
                    )

            branch = (field.get("onchange") or {}).get(_onchange_key(value))
            if isinstance(branch, list):
                self._collect_field_problems(
                    branch,
                    config,
                    condition={"name": fname, "label": _field_label(field), "value": value},
                    missing=missing,
                    invalid=invalid,
                    known=known,
                    errors=errors,
                )

    # ------------------------------------------------------------- configure
    def create_configuration(
        self,
        connector: str,
        config: dict[str, Any],
        *,
        name: str,
        version: str | None = None,
        default: bool = False,
        config_id: str | None = None,
        agent: str | None = None,
        validate: bool = True,
        autofill: bool = True,
        exist_ok: bool = False,
        refresh: bool = True,
    ) -> ConnectorConfig:
        """Create (or update) a connector configuration -- write its credentials.

        Persists a named configuration for ``connector`` via
        ``POST /api/integration/configuration/`` (the same endpoint the UI's
        connector-config form uses). ``config`` is the connector's own field
        map -- for ``fortinet-fortisiem`` that's
        ``{"server", "username", "password", "organization", "verify_ssl"}``;
        inspect :meth:`definition` (its ``config_schema``) for any connector's
        fields. Secrets (e.g. ``password``) are encrypted server-side, so always
        create configs through this API rather than writing the table directly.

        Args:
            connector: connector *name* (e.g. ``"fortinet-fortisiem"``).
            config: the connector's configuration field values.
            name: a label for this configuration (required; what the UI shows).
            version: connector version; resolved from an already-configured
                connector when omitted. Pass it explicitly the first time a
                connector is configured (``resolve_version`` only sees
                already-configured connectors). If invalid, the appliance falls
                back to the latest installed version.
            default: mark this the connector's default configuration.
            config_id: reuse a specific UUID -- passing an existing config's id
                **updates** that configuration instead of creating a new one
                (the endpoint upserts on ``config_id``); omit to mint a new one.
            agent: run the connector on a remote *agent* (its uuid); omit to use
                the appliance's self-agent.
            validate: structurally check ``config`` against the connector's
                schema first (via :meth:`validate_config`) and raise on a missing
                required field -- turns the server's opaque 500 into a clear
                error. Pass ``False`` to skip (default ``True``).
            autofill: fill any schema-defaulted fields ``config`` omits -- including
                the ``onchange``-revealed sub-fields that are otherwise required
                only at *playbook runtime* (see :meth:`default_config`). Your
                explicit values always win. Pass ``False`` to send ``config``
                verbatim (default ``True``).
            exist_ok: when ``True``, if a configuration with the same ``name``
                already exists for this connector/agent pair, delegate to
                :meth:`upsert_configuration` instead of raising
                :exc:`~pyfsr.exceptions.ConfigurationExistsError` (default ``False``).
            refresh: drop the cached configured-connector listing afterwards so
                the new config is visible to :meth:`resolve_config` etc.
                (default ``True``).

        The integer ``connector`` id the endpoint requires (a name-only body
        500s) is resolved automatically from ``connector``.

        Returns:
            The persisted configuration record (including its ``config_id``).

        Raises:
            ValueError: if the connector isn't installed or ``version`` can't be
                resolved.
            ConfigValidationError: when ``validate=True`` and the configuration
                fails structural validation (missing required fields, invalid
                option values, or wrong field types). Includes field-level error
                details so callers can programmatically handle them.
            ConfigurationExistsError: when ``exist_ok=False`` (the default) and
                the server rejects the write with a unique constraint violation
                on ``(name, connector, agent)``.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} version unknown (not yet configured); pass version=")
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(
                f"{connector!r} is not installed; install it before configuring "
                "(client.connectors.install(name, version))"
            )
        if autofill:
            config = self._materialize_config(self.config_schema(connector, version=version), config)
        if validate:
            check = self.validate_config(connector, config, version=version)
            if not check.valid:
                # Convert to the new ConfigValidationError with structured errors
                msg = _format_validation_error(connector, check)
                raise ConfigValidationError(msg, errors=check.errors)
        body: dict[str, Any] = {
            "connector": connector_id,
            "connector_name": connector,
            "connector_version": version,
            "name": name,
            "default": default,
            "config": config,
        }
        if config_id is not None:
            body["config_id"] = config_id
        if agent is not None:
            body["agent"] = agent
        try:
            resp = self.client.post("/api/integration/configuration/", data=body)
        except APIError as e:
            # Catch unique constraint violations and offer exist_ok hint
            error_msg = (e.message or "").lower()
            if "unique" in error_msg and ("name" in error_msg or "must" in error_msg):
                if not exist_ok:
                    raise ConfigurationExistsError(connector, name, response=e.response, error_type=e.error_type) from e
                # exist_ok=True: delegate to upsert
                return self.upsert_configuration(
                    connector,
                    config,
                    name=name,
                    version=version,
                    default=default,
                    agent=agent,
                    validate=False,  # already validated
                    autofill=False,  # already autofilled
                )
            raise
        if refresh:
            self.clear_cache()
        raw = resp if isinstance(resp, dict) else {"result": resp}
        return ConnectorConfig.model_validate(raw)

    def update_configuration(
        self,
        connector: str,
        config_id: str,
        config: dict[str, Any],
        *,
        name: str,
        version: str | None = None,
        default: bool = False,
        agent: str | None = None,
        validate: bool = True,
        autofill: bool = True,
        refresh: bool = True,
    ) -> ConnectorConfig:
        """Update an existing connector configuration by ``config_id``.

        ``PUT /api/integration/configuration/{config_id}/`` (the POST create path
        *rejects* a known ``config_id`` rather than upserting). Use this to
        rotate credentials on a configured connector -- e.g. re-stamp a FortiSIEM
        ``password`` or a refreshed token. ``config`` is sent whole, so include
        every field, not just the changed one.

        Like :meth:`create_configuration`, the integer ``connector`` id is
        resolved automatically, and ``config`` is structurally validated first
        unless ``validate=False``.

        Args:
            connector: connector name.
            config_id: the UUID of the configuration to update.
            config: the new connector configuration field values.
            name: the configuration's label.
            version: connector version (resolved if omitted).
            default: mark this the connector's default configuration.
            agent: run the connector on a remote agent (omit to keep existing).
            validate: structurally check ``config`` against the schema first
                (default ``True``).
            autofill: fill any schema-defaulted fields ``config`` omits (default ``True``).
            refresh: drop the cached configured-connector listing afterwards
                (default ``True``).

        Returns:
            The updated :class:`~pyfsr.models.ConnectorConfig`.

        Raises:
            ValueError: if the connector isn't installed or version can't be resolved.
            ConfigValidationError: when ``validate=True`` and the configuration
                fails structural validation.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} version unknown; pass version=")
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        if autofill:
            config = self._materialize_config(self.config_schema(connector, version=version), config)
        if validate:
            check = self.validate_config(connector, config, version=version)
            if not check.valid:
                msg = _format_validation_error(connector, check)
                raise ConfigValidationError(msg, errors=check.errors)
        body: dict[str, Any] = {
            "connector": connector_id,
            "connector_name": connector,
            "connector_version": version,
            "name": name,
            "default": default,
            "config_id": config_id,
            "config": config,
        }
        if agent is not None:
            body["agent"] = agent
        resp = self.client.put(f"/api/integration/configuration/{config_id}/", data=body)
        if refresh:
            self.clear_cache()
        raw = resp if isinstance(resp, dict) else {"result": resp}
        # NB: FortiSOAR 8.0's PUT echoes the saved row but puts an async
        # op-envelope in ``status`` (``{"status":"finished","message":...}``)
        # instead of 7.x's int active-flag. ``ConnectorConfig.status`` tolerates
        # this (coerced to None) -- see its validator in models/_integration.py.
        return ConnectorConfig.model_validate(raw)

    def set_default_configuration(
        self,
        connector: str,
        config_id: str | None = None,
        *,
        name: str | None = None,
        refresh: bool = True,
    ) -> ConnectorConfig:
        """Mark an existing configuration the connector's default, changing nothing else.

        There is no flag-only route: the API only exposes
        ``PUT /api/integration/configuration/{config_id}/``, which replaces the
        whole record. Doing that by hand is how credentials get wiped -- the
        connector listing returns ``config: null`` (the field map is only on the
        single-record GET), so a caller who builds the body from the listing
        PUTs an empty ``config`` over live secrets. This reads the current
        record first and re-sends it verbatim with ``default=True``.

        ``validate``/``autofill`` are deliberately NOT applied. The stored
        ``config`` is already whatever the appliance accepted, and secrets come
        back encrypted at rest; materializing it against the schema would
        rewrite fields this call has no business touching.

        The appliance re-encrypts secrets on save, so the stored ciphertext for
        a field like ``api_key`` legitimately DIFFERS after this call while the
        plaintext is unchanged -- do not read that as corruption. Verified on a
        live 8.0.0 appliance: a FortiGate configuration that could not be
        health-checked (``Could not find a configuration matching the id
        get_default_config or the default configuration``) reported
        ``Available`` afterwards, with the upstream still reachable.

        Args:
            connector: connector machine name, e.g. ``"fortigate-firewall"``.
            config_id: the configuration to promote. Omit when the connector has
                exactly one configuration and it should be the default.
            name: select the configuration by its label instead of ``config_id``.
            refresh: drop the cached configured-connector listing afterwards.

        Returns:
            The updated :class:`~pyfsr.models.ConnectorConfig`.

        Raises:
            ValueError: if the connector isn't installed, if neither
                ``config_id`` nor ``name`` is given and the connector has zero or
                more than one configuration, or if ``name`` matches no
                configuration.
        """
        if config_id is None:
            configs = self.list_configurations(connector=connector)
            if name is not None:
                configs = [c for c in configs if getattr(c, "name", None) == name]
                if not configs:
                    raise ValueError(f"{connector!r} has no configuration named {name!r}")
            if not configs:
                raise ValueError(f"{connector!r} has no configurations to make default")
            if len(configs) > 1:
                labels = ", ".join(repr(getattr(c, "name", "?")) for c in configs)
                raise ValueError(
                    f"{connector!r} has {len(configs)} configurations ({labels}); "
                    f"pass config_id= or name= to choose one"
                )
            config_id = configs[0].config_id

        cur = self.client.get(f"/api/integration/configuration/{config_id}/")
        if not isinstance(cur, dict) or "config" not in cur:
            raise ValueError(f"configuration {config_id!r} not found on this appliance")

        body: dict[str, Any] = {
            "connector": cur.get("connector"),
            "connector_name": connector,
            "connector_version": cur.get("connector_version") or self.resolve_version(connector),
            "name": cur.get("name"),
            "default": True,
            "config_id": config_id,
            "config": cur.get("config"),
        }
        if cur.get("agent"):
            # A connector bound to a remote agent loses that binding if the PUT
            # omits it, which silently moves execution back to the self-agent.
            body["agent"] = cur["agent"]
        resp = self.client.put(f"/api/integration/configuration/{config_id}/", data=body)
        if refresh:
            self.clear_cache()
        raw = resp if isinstance(resp, dict) else {"result": resp}
        return ConnectorConfig.model_validate(raw)

    def delete_configuration(self, config_id: str, *, refresh: bool = True) -> None:
        """Delete a connector configuration by id
        (``DELETE /api/integration/configuration/{config_id}/``).

        The trailing slash is mandatory -- without it the gateway rejects the
        call with ``403 Could not validate HMAC fingerprint``.
        """
        self.client.delete(f"/api/integration/configuration/{config_id}/")
        if refresh:
            self.clear_cache()

    # --------------------------------------------------------- connector studio
    # The Connector Studio development workspace: list checked-out connectors,
    # open one for editing, read/write its source files, then publish to land
    # the changes on the running appliance. ``entity_id`` is the dev-workspace
    # entity id (from :meth:`dev_list`), not the integer install id.
    _DEV_BASE = "/api/integration/connector/development/entity"

    def dev_list(self) -> list[dict[str, Any]]:
        """List connectors checked out into the Connector Studio dev workspace.

        ``GET /api/integration/connector/development/entity/`` -- the same set
        shown in the Studio's left-hand tree. Returns the ``data[]`` entries.
        """
        resp = self.client.get(f"{self._DEV_BASE}/") or {}
        if isinstance(resp, dict) and resp.get("data"):
            return resp["data"]
        return extract_members(resp)

    def dev_edit(self, entity_id: str) -> dict[str, Any]:
        """Open a dev-workspace connector for editing (Studio's *Edit* action).

        ``POST .../entity/{id}/`` with ``{"edit_repo_connector": true}``. Returns
        the entity's full operations + configuration schema + file tree. Follow
        with :meth:`dev_read_file`/:meth:`dev_write_file`, then :meth:`dev_publish`.
        """
        resp = self.client.post(f"{self._DEV_BASE}/{entity_id}/", data={"edit_repo_connector": True})
        return resp if isinstance(resp, dict) else {"result": resp}

    def dev_read_file(self, entity_id: str, xpath: str) -> dict[str, Any]:
        """Read one source file from a dev-workspace connector.

        ``POST .../entity/{id}/files/`` with ``{"xpath": ...}``. ``xpath`` is
        relative to the connector's dev-workspace root and starts with
        ``/<name>_<vtag>_dev/...``. Returns the file payload.
        """
        resp = self.client.post(f"{self._DEV_BASE}/{entity_id}/files/", data={"xpath": xpath})
        return resp if isinstance(resp, dict) else {"result": resp}

    def dev_write_file(self, entity_id: str, file_data: dict[str, Any]) -> dict[str, Any]:
        """Write one source file in a dev-workspace connector (Studio *Save*).

        ``PUT .../entity/{id}/files/`` with ``{"fileData": ...}``. ``file_data``
        is the editor's file object (path + contents). Saved changes are staged
        in the workspace and do **not** affect playbook execution until
        :meth:`dev_publish` is called.
        """
        resp = self.client.put(f"{self._DEV_BASE}/{entity_id}/files/", data={"fileData": file_data})
        return resp if isinstance(resp, dict) else {"result": resp}

    def dev_publish(
        self,
        entity_id: str,
        *,
        replace: bool = False,
        discard: bool = False,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Publish a dev-workspace connector onto the running appliance.

        ``POST .../entity/{id}/publish/``. Lands the workspace contents into the
        live installed-connectors area and refreshes the integrations service so
        subsequent playbook runs pick up the new code immediately. ``replace``
        overwrites an existing installed version of the same name + version.
        ``discard`` controls the dev-workspace twin's lifecycle (not whether
        edits are published). This is also the supported escape hatch when a
        same-version tgz upload left stale code cached in the integrations
        service (the standard ``$replace=true`` install path does not refresh it).
        """
        resp = self.client.post(
            f"{self._DEV_BASE}/{entity_id}/publish/",
            data={"replace": replace, "discard": discard},
        )
        if refresh:
            self.clear_cache()
        return resp if isinstance(resp, dict) else {"result": resp}

    def dev_delete(self, entity_id: str, *, refresh: bool = True) -> None:
        """Delete a dev-workspace connector twin (Studio *discard*).

        ``DELETE .../entity/{id}/``. Use to tear down an orphaned ``_dev``
        workspace left by a failed :meth:`dev_publish` -- an unreadable file in
        an orphaned ``_dev`` dir can wedge DAS's HA file-sync, so cleanup matters.
        """
        self.client.delete(f"{self._DEV_BASE}/{entity_id}/")
        if refresh:
            self.clear_cache()

    def republish(self, connector: str, *, replace: bool = True, discard: bool = True) -> dict[str, Any]:
        """Recycle the integrations workers onto a connector's installed code.

        A same-version ``$replace`` tgz install (see :meth:`install_from_file`)
        writes the new files but does **not** refresh the long-lived integrations
        uwsgi workers, so they keep serving the previously-imported module object
        from ``sys.modules`` (the "ghost bytecode" bug: a request randomly hits a
        stale worker and same-named-module edits silently don't take). This is the
        supported, SSH-free recycle: open the installed connector in the Connector
        Studio dev workspace (cloning the current installed state), then publish
        that twin back -- which copies it into the live dir **and** touches the dev
        config ini, recycling every worker within ~5s.

        ``discard`` makes a *successful* publish destroy the dev twin server-side
        so no orphan ``_dev`` dir is left; on a publish failure the twin is
        deleted explicitly here (an unreadable file in an orphaned ``_dev`` can
        wedge HA file-sync). ``replace`` overwrites the same name+version.

        Run this after a same-version ``install_from_file(..., replace=True)`` so
        every worker imports the new code. Returns ``{"ok": True, "dev_id": ...}``.

        Raises ``ValueError`` if ``connector`` isn't installed, or re-raises the
        publish error (after cleaning up the twin) if the publish itself fails.
        """
        connector_id = self.resolve_connector_id(connector)
        if connector_id is None:
            raise ValueError(f"{connector!r} is not installed")
        # Entering edit mode clones the installed tree into the dev workspace.
        dev = self.dev_edit(connector_id)
        dev_id = dev.get("id")
        # Defensive: edit-mode sometimes echoes the installed id rather than the
        # twin's -- find the real dev twin (development=true) by name.
        if dev_id == connector_id or not dev.get("development"):
            twin = next(
                (m for m in self.dev_list() if m.get("name") == connector and m.get("development")),
                None,
            )
            dev_id = (twin or {}).get("id", dev_id)
        if not dev_id:
            raise RuntimeError(f"could not resolve the dev-workspace twin for {connector!r}")
        try:
            self.dev_publish(dev_id, replace=replace, discard=discard)
        except Exception:
            # Publish didn't run its discard (failed/timed out) -- delete the twin
            # explicitly so no `_dev` dir is left to wedge HA file-sync.
            try:
                self.dev_delete(dev_id)
            except Exception:  # noqa: BLE001 -- best-effort teardown
                pass
            raise
        return {"ok": True, "dev_id": dev_id}

    def _find_configuration_by_name(self, connector: str, name: str) -> dict[str, Any] | None:
        """The connector's configuration row matching ``name`` (carrying
        ``config_id`` + ``agent``), or ``None``. Reads :meth:`connector_detail`,
        the only view that lists full config rows."""
        try:
            detail = self.connector_detail(connector)
        except ValueError:
            return None
        return next((c for c in (detail.get("configuration") or []) if c.get("name") == name), None)

    def upsert_configuration(
        self,
        connector: str,
        config: dict[str, Any],
        *,
        name: str,
        version: str | None = None,
        default: bool = False,
        agent: str | None = None,
        validate: bool = True,
        autofill: bool = True,
    ) -> ConnectorConfig:
        """Create a named configuration, or update it in place if one already
        exists with the same ``name`` -- the idempotent write the UI's *Save*
        button performs, safe to re-run from a deploy script.

        Finds an existing config by ``name`` (via :meth:`connector_detail`) and
        ``PUT``s to its ``config_id`` (preserving the existing ``agent`` unless
        ``agent`` is given), else ``POST``s a new one. Unlike calling
        :meth:`create_configuration` twice -- which 400s on
        ``"name, connector, agent must be unique"`` -- this updates the second time.

        Tolerates the platform's *persisted-despite-500* case: a connector's own
        ``on_add_config`` / ``on_update_config`` hook can raise **after** the row
        is written (e.g. a post-save warmup), surfacing a 500 even though the
        config saved. On a write error this re-fetches by ``name`` and returns the
        row if it landed, rather than failing a re-runnable deploy.

        Args:
            connector: connector name.
            config: the connector's configuration field values.
            name: a label for this configuration (required).
            version: connector version (resolved if omitted).
            default: mark this the connector's default configuration.
            agent: run the connector on a remote agent (omit for self-agent).
            validate: structurally check ``config`` against the schema first
                (default ``True``).
            autofill: fill any schema-defaulted fields ``config`` omits,
                including onchange-revealed sub-fields (default ``True``).

        Returns:
            The persisted :class:`~pyfsr.models.ConnectorConfig`.

        Raises:
            ValueError: if the connector isn't installed or version can't be resolved.
            ConfigValidationError: when ``validate=True`` and the configuration
                fails structural validation.
        """
        version = version or self.resolve_version(connector)
        existing = self._find_configuration_by_name(connector, name)

        def _write() -> ConnectorConfig:
            if existing:
                return self.update_configuration(
                    connector,
                    existing.get("config_id") or existing.get("id"),
                    config,
                    name=name,
                    version=version,
                    default=default,
                    agent=agent if agent is not None else existing.get("agent"),
                    validate=validate,
                    autofill=autofill,
                )
            return self.create_configuration(
                connector,
                config,
                name=name,
                version=version,
                default=default,
                agent=agent,
                validate=validate,
                autofill=autofill,
            )

        try:
            return _write()
        except Exception:
            # The write may have persisted before a post-save hook raised -- verify
            # by re-fetch rather than trusting the status code.
            confirmed = self._find_configuration_by_name(connector, name)
            if confirmed is not None:
                self.clear_cache()
                return ConnectorConfig.model_validate(confirmed)
            raise

    def ensure_configured(
        self,
        connector: str,
        config: dict[str, Any],
        *,
        config_name: str,
        version: str | None = None,
        default: bool = True,
        agent: str | None = None,
        validate: bool = True,
        autofill: bool = True,
        wait: bool = True,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> ConnectorConfig:
        """Ensure ``connector`` is installed **and** has the named configuration.

        Consolidates the common setup sequence -- "install from Content Hub if it
        isn't here yet, then create-or-update the config" -- into one idempotent
        call, joining the existing :meth:`ensure_version` in the ``ensure_*``
        family. Re-running it is safe: an already-installed connector is not
        reinstalled, and :meth:`upsert_configuration` updates the named config in
        place rather than duplicating it.

        ``version`` is only needed to *install* a missing connector (it is passed
        to :meth:`install`); when the connector is already installed it may be
        omitted and the configuration is applied against the installed version. If
        the connector is absent and ``version`` is ``None``, a clear ``ValueError``
        is raised rather than guessing.

        ``config_name`` is the configuration's display name (passed through as
        ``name=`` to :meth:`upsert_configuration`); ``default=True`` (the default)
        makes it the connector's default config so a config-less connector step
        picks it up. Returns the resulting :class:`~pyfsr.models.ConnectorConfig`.

        Example::

            cfg = client.connectors.ensure_configured(
                "servicenow",
                {"server_url": "...", "username": "...", "password": "..."},
                config_name="pilot",
                version="1.0.0",
            )
        """
        if self.resolve_connector_id(connector) is None:
            if not version:
                raise ValueError(
                    f"connector {connector!r} is not installed; pass version= to install it from the Content Hub"
                )
            self.install(connector, version, wait=wait, interval=interval, timeout=timeout)
        return self.upsert_configuration(
            connector,
            config,
            name=config_name,
            version=version,
            default=default,
            agent=agent,
            validate=validate,
            autofill=autofill,
        )

    # ------------------------------------------------------------- execute
    def execute(
        self,
        connector: str,
        operation: str,
        *,
        version: str | None = None,
        config: str | None = None,
        params: dict[str, Any] | None = None,
        config_id: str | None = None,
        config_name: str | None = None,
    ) -> ExecuteResult:
        """Run a single connector operation via ``POST /api/integration/execute/``.

        ``config`` selects which connector configuration to run against. It
        accepts either a configuration **UUID** or a display **name** -- the
        FortiSOAR server resolves both in the wire ``config`` field
        (live-verified on 8.0.0). Omit it to use the connector's *default*
        configuration (resolved client-side from the cached connector listing).

        .. deprecated::
           ``config_id=`` and ``config_name=`` are deprecated aliases for
           ``config=``. They still work (and emit a warning) but gain nothing --
           ``config=`` accepts both a UUID and a name. Passing more than one
           raises.

        ``version`` is resolved from the configured connector when omitted.

        Returns a typed :class:`~pyfsr.models.ExecuteResult` -- dict-compatible
        (``result["data"]`` still works), with a ``.ok`` property for the
        recurring ``status == "Success"`` check.

        See the module-level warning: for agent-bound connectors this call is
        fire-and-forget and ``data`` comes back empty -- that is not a failure.

        Live-verified on FortiSOAR 8.0.0-6034 against ``cisa-advisory``'s
        ``get_known_exploited_vulnerability_cves`` (a public, read-only,
        parameter-less feed lookup -- safe to demo against a real connector,
        no side effect beyond an outbound GET to CISA's public catalog):

            >>> client = demo_client()
            >>> result = client.connectors.execute(
            ...     "cisa-advisory", "get_known_exploited_vulnerability_cves"
            ... )
            >>> result.ok
            True
            >>> result.data["title"]
            'CISA Catalog of Known Exploited Vulnerabilities'
            >>> result.data["vulnerabilities"][0]["cveID"]
            'CVE-2026-45659'
        """
        config = _resolve_config_kwarg(config, config_id, config_name)
        version = version or self.resolve_version(connector)
        if config is None and self._configured is not None:
            config = self.resolve_config(connector)
        body = {
            "connector": connector,
            "operation": operation,
            "version": version or "",
            "config": config or "",
            "params": params or {},
        }
        resp = self.client.post("/api/integration/execute/", data=body)
        return ExecuteResult.model_validate(resp if isinstance(resp, dict) else {"result": resp})

    # ------------------------------------------------------- dependencies
    def dependencies_status(self, connector: str, *, version: str | None = None) -> DependencyStatus:
        """Whether a connector's Python dependencies finished installing.

        ``GET /api/integration/connectors/dependencies_check/<name>/<version>/``.
        The connector card's *Requirements* badge reads this: a false
        ``dependencies_installed`` is the ``Failed`` state, and is a common
        reason a freshly installed connector's operations blow up at runtime
        even though its configuration health is green.

        Example:
            >>> client = demo_client()
            >>> client.connectors.dependencies_status("mitre-attack").dependencies_installed
            True
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} is not installed (cannot resolve a version)")
        resp = self.client.get(f"/api/integration/connectors/dependencies_check/{connector}/{version}/")
        return DependencyStatus.model_validate(resp if isinstance(resp, dict) else {"result": resp})

    def install_dependencies(
        self,
        connector: str,
        *,
        version: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Retry the Python-dependency install for a connector.

        ``POST`` to the same URL :meth:`dependencies_status` reads -- the
        *Retry* button next to a failed *Requirements* badge. Pass ``agent`` to
        install the dependencies on a remote agent instead of the appliance.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} is not installed (cannot resolve a version)")
        params = {"format": "json"}
        if agent:
            params["agent"] = agent
        return self.client.post(
            f"/api/integration/connectors/dependencies_check/{connector}/{version}/",
            data={},
            params=params,
        )

    # ------------------------------------------------------ misc surfaces
    def ingestion_sources(self) -> list[InstalledConnector]:
        """Installed connectors that support data ingestion.

        ``POST /api/integration/connector_details/?ingestion_supported=true`` --
        the list backing the *Data Ingestion* page's connector picker. These are
        the connectors :meth:`data_ingest_wizard` can meaningfully target.
        """
        resp = self.client.post(
            "/api/integration/connector_details/",
            data={},
            params={
                "format": "json",
                "exclude": "operation",
                "ingestion_supported": "true",
                "ordering": "label",
            },
        )
        return [InstalledConnector.model_validate(row) for row in IntegrationListEnvelope.parse(resp).data]

    def output_schema(
        self,
        connector: str,
        operation: str,
        *,
        version: str | None = None,
        config: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Infer an operation's output schema.

        ``POST /api/integration/connector_output_schema/<name>/<version>/`` --
        what the playbook editor calls to populate step-output pickers. Note the
        UI resolves this endpoint's *failures* as successes too (it returns
        whatever body came back), so check the payload rather than trusting a 2xx.
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} is not installed (cannot resolve a version)")
        body = {
            "operation": operation,
            "config": config if config else self.resolve_config(connector),
            "params": params or {},
        }
        return self.client.post(
            f"/api/integration/connector_output_schema/{connector}/{version}/",
            data=body,
            params={"format": "json"},
        )

    def set_operation_roles(
        self,
        operation_id: str,
        roles: list[str],
        *,
        replace: bool = True,
    ) -> dict[str, Any]:
        """Restrict a connector operation to a set of roles.

        ``POST`` (first assignment) or ``PUT`` (replacing an existing one) to
        ``/api/integration/connectors/operations/<operation_id>/roles/``. The UI
        picks the verb by whether the operation already has roles -- mirror that
        with ``replace``.

        Args:
            operation_id: the operation's ``id`` from :meth:`operations`.
            roles: role uuids allowed to run the operation.
            replace: ``True`` (default) sends ``PUT``; ``False`` sends ``POST``
                for an operation that currently has no roles.
        """
        path = f"/api/integration/connectors/operations/{operation_id}/roles/"
        body = {"roles": list(roles)}
        return self.client.put(path, data=body) if replace else self.client.post(path, data=body)

    # -------------------------------------------------- ingestion metadata
    def ingestion_metadata(self, config_id: str) -> list[IngestionMetadata]:
        """The ``data-import`` records for a connector configuration.

        ``GET /api/integration/data-import/?configuration=<config_id>``. This is
        how the UI re-finds an existing ingestion schedule for a configuration
        (via ``metadata.scheduleId``) -- an ingestion set up without one of these
        looks unconfigured in the UI even though the schedule runs fine.

        Example:
            >>> client = demo_client()
            >>> meta = client.connectors.ingestion_metadata(
            ...     "01e4e6b4-c34e-4fc1-b692-bb08591f1fe5")[0]
            >>> meta.metadata["scheduleStatus"]
            True
        """
        resp = self.client.get("/api/integration/data-import/", params={"configuration": config_id})
        env = IntegrationListEnvelope.parse(resp)
        return [IngestionMetadata.model_validate(row) for row in env.data]

    def save_ingestion_metadata(
        self,
        config_id: str,
        *,
        connector: str,
        version: str,
        name: str,
        schedule_id: str | None = None,
        schedule_name: str = "",
        schedule_enabled: bool = False,
        sample_data: Any = None,
        actor: str | None = None,
        created: bool = True,
    ) -> dict[str, Any]:
        """Write the ``data-import`` record that links a config to its schedule.

        ``POST /api/integration/data-import/``. Called twice by the UI: once
        with ``sample_data`` after the fetch step, and once with the
        ``metadata.scheduleId`` after the schedule is saved.

        Args:
            config_id: the connector configuration's ``config_id``.
            connector: connector name.
            version: connector version.
            name: record name -- the UI uses the schedule name, falling back to
                the ``config_id``.
            schedule_id: the periodic task's id, once it exists.
            schedule_name: display name of that schedule.
            schedule_enabled: whether ingestion is scheduled to run.
            sample_data: parsed sample payload to store alongside the mapping.
            actor: ``people`` uuid to stamp as creator/modifier; resolved from
                the current actor when omitted.
            created: include ``created_by`` (the UI only sets it on first write).
        """
        if actor is None:
            actor = self._current_actor_uuid()
        body: dict[str, Any] = {
            "name": name,
            "description": f"Metadata for {name}",
            "modified_by": actor,
            "owners": [],
            "connector": {"name": connector, "version": version},
            "configuration": config_id,
            "metadata": {
                "scheduleId": schedule_id,
                "scheduleName": schedule_name,
                "scheduleStatus": bool(schedule_enabled),
            },
        }
        if created:
            body["created_by"] = actor
        if sample_data is not None:
            body["sample_data"] = sample_data
        return self.client.post("/api/integration/data-import/", data=body)

    def _current_actor_uuid(self) -> str | None:
        """The current user's ``people`` uuid, for ``created_by``/``modified_by``."""
        try:
            actor = self.client.get("/api/3/actors/current")
        except Exception:  # pragma: no cover - identity is best-effort metadata
            return None
        if not isinstance(actor, dict):
            return None
        uuid = actor.get("uuid")
        if isinstance(uuid, str) and uuid:
            return uuid
        iri = actor.get("@id")
        return iri.rstrip("/").split("/")[-1] if isinstance(iri, str) and iri else None

    # ------------------------------------------------ ingestion playbooks
    def _ingestion_query(self, collection_uuid: str, connector: str) -> list[Workflow]:
        """Playbooks in ``collection_uuid`` tagged for ``connector``.

        Reproduces the wizard's sample-playbook query: scope to one collection,
        then match either the ``#<connector>`` hashtag on ``tag.uuid`` or the
        connector name inside ``recordTags.uuid``.
        """
        body = {
            "limit": 256,
            "logic": "AND",
            "filters": [
                {"field": "collection.uuid", "operator": "eq", "value": collection_uuid, "type": "primitive"},
                {
                    "logic": "OR",
                    "filters": [
                        {"field": "tag.uuid", "operator": "like", "value": f"%#{connector}%", "type": "primitive"},
                        {
                            "field": "recordTags.uuid",
                            "operator": "like",
                            "value": f"%{connector}%",
                            "type": "primitive",
                        },
                    ],
                },
            ],
        }
        resp = self.client.post(
            "/api/query/workflows?$relationships=true&$export=true&$limit=256",
            data=body,
        )
        return [Workflow.model_validate(row) for row in extract_members(resp)]

    def _dataingestion_playbooks(self, collection_uuid: str, connector: str) -> list[Workflow]:
        """Every ``#dataingestion``-tagged playbook for ``connector`` in a collection.

        The four *roles* (fetch/ingest/create/update) are not the whole set -- a
        connector's ingestion often includes helper playbooks that carry only
        ``#dataingestion`` (FortiSIEM ships a *Fetch Associated events for
        Incident* that the ingest playbook calls). The UI clones **all** of
        them; cloning only the roles leaves the copies calling shared sample
        playbooks that are bound to no configuration.
        """
        out = []
        for pb in self._ingestion_query(collection_uuid, connector):
            tags = {str(t).strip().lower() for t in (pb.recordTags or []) if t}
            if "dataingestion" in tags or "#dataingestion" in str(pb.tag or "").lower():
                out.append(pb)
        return out

    @staticmethod
    def _bucket_by_tag(playbooks: list[Workflow]) -> IngestionPlaybooks:
        """Sort playbooks into the fetch/ingest/create/update ingestion roles.

        Matches on ``recordTags`` (and the ``#tag`` string) case-insensitively.
        A playbook may fill several roles at once -- FortiSIEM's
        *FortiSIEM > Ingest* is tagged both ``ingest`` and ``create``.
        """
        buckets: dict[str, Workflow | None] = {"fetch": None, "ingest": None, "create": None, "update": None}
        for pb in playbooks:
            tags = {str(t).strip().lower() for t in (pb.recordTags or []) if t}
            raw = str(pb.tag or "").lower()
            for role in buckets:
                if buckets[role] is None and (role in tags or f"#{role}" in raw):
                    buckets[role] = pb
        return IngestionPlaybooks.model_validate(buckets)

    def find_sample_ingestion_collection(self, connector: str, *, version: str | None = None) -> str | None:
        """Locate the connector's bundled ``Sample - <Label> - <version>`` collection.

        Installing a connector that ships playbooks creates one of these; it is
        the source :meth:`data_ingest_wizard` clones from. Returns its uuid, or
        ``None`` when the connector ships no sample playbooks (in which case
        there is nothing to ingest with and the wizard cannot proceed).
        """
        version = version or self.resolve_version(connector)
        resp = self.client.get(
            "/api/3/workflow_collections",
            params={"$limit": 2147483647, "__selectFields": "name,uuid"},
        )
        rows = list(extract_members(resp))
        label = None
        hit = self._find_configured(connector)
        if hit is not None:
            label = hit.label
        candidates = []
        for row in rows:
            name = str(row.get("name") or "")
            if not name.lower().startswith("sample - "):
                continue
            if version and name.rstrip().endswith(f"- {version}"):
                if label and label.lower() in name.lower():
                    return str(row.get("uuid"))
                candidates.append(row)
            elif label and label.lower() in name.lower():
                candidates.append(row)
        return str(candidates[0]["uuid"]) if candidates else None

    def ingestion_playbooks(
        self,
        connector: str,
        *,
        collection: str | None = None,
        version: str | None = None,
    ) -> IngestionPlaybooks:
        """The connector's ingestion playbooks, bucketed by role.

        Looks inside ``collection`` (defaulting to the connector's bundled
        ``Sample - …`` collection) for playbooks tagged with the connector name
        and sorts them into ``fetch`` / ``ingest`` / ``create`` / ``update``.

        Example:
            >>> client = demo_client()
            >>> pbs = client.connectors.ingestion_playbooks("mitre-attack")
            >>> pbs.ingest.name              # a typed Workflow, not a dict
            'MITRE ATT&CK > Ingest'
            >>> pbs.ingest.uuid == pbs.create.uuid   # one playbook, two roles
            True
            >>> pbs.missing()
            ['update']
        """
        collection = collection or self.find_sample_ingestion_collection(connector, version=version)
        if not collection:
            return IngestionPlaybooks()
        return self._bucket_by_tag(self._ingestion_query(collection, connector))

    # ------------------------------------------------------- the wizard
    def data_ingest_wizard(
        self,
        connector: str,
        *,
        config: str | None = None,
        version: str | None = None,
        cron: str | None = None,
        schedule_name: str | None = None,
        enabled: bool = True,
        timezone: str = "UTC",
        exit_if_running: bool = True,
        source_collection: str | None = None,
        agent: str | None = None,
        require_health: bool = True,
        activate: bool = True,
        reuse_existing: bool = True,
        dry_run: bool = False,
    ) -> IngestionSetupResult:
        """Set up data ingestion for a connector configuration -- the whole wizard.

        Reproduces every write the UI's *Configure Data Ingestion* wizard makes,
        in the order it makes them:

        1. resolve the configuration and (unless ``require_health=False``)
           refuse to proceed on anything but ``Available`` health -- the UI gates
           the wizard behind a green health check;
        2. find-or-create the per-configuration playbook collection, whose
           **uuid is the ``config_id``** and whose name follows the UI's
           ``"<label> <version> <config>Ingestion(<config_id>)"`` convention;
        3. clone the connector's sample ingestion playbooks into it, rewriting
           each clone the way the UI does -- ``arguments.config`` stamped with
           the ``config_id`` on that connector's own steps, ``globalVars``
           suffixed with the config id so two configurations of the same
           connector don't share variables, cross-playbook references remapped
           onto the clones, and ``params.create_pb_id`` on the *Fetch and
           Create* step pointed at the cloned ``create`` playbook;
        4. activate the clones;
        5. create the periodic task that fires the ``ingest`` playbook;
        6. write the ``data-import`` metadata record that links configuration →
           schedule, so the UI shows the ingestion as configured.

        Args:
            connector: connector name (e.g. ``"fortinet-fortisiem"``).
            config: which configuration to set ingestion up for -- a config
                **name** or a ``config_id`` uuid. Defaults to the connector's
                default configuration.
            version: connector version; resolved from the install when omitted.
            cron: 5-field cron expression for the ingestion schedule (e.g.
                ``"*/15 * * * *"``). Omit to build the playbooks without
                scheduling anything.
            schedule_name: schedule display name. Defaults to the platform's
                own convention, ``"Ingestion_<connector>_<config>_<config_id>"``
                -- the UI matches on this string, so overriding it makes the
                ingestion look unconfigured in the *Data Ingestion* screen.
            enabled: create the schedule enabled (default ``True``).
            timezone: IANA timezone for the crontab.
            exit_if_running: skip a fire while the previous run is still going.
            source_collection: uuid of the collection to clone sample playbooks
                from. Defaults to the connector's bundled ``Sample - …``.
            agent: stamp ``arguments.agent`` on the connector's steps so the
                cloned playbooks run the connector on a remote agent.
            require_health: refuse to run unless configuration health is
                ``Available`` (default, matching the UI). Set ``False`` to build
                the ingestion pipeline against a connector you know is
                unreachable -- useful when staging a box before credentials exist.
            activate: set ``isActive`` on the cloned playbooks (default ``True``).
            reuse_existing: if the per-config collection already holds ingestion
                playbooks, reuse them instead of re-cloning (default ``True``).
                Set ``False`` to rebuild from the samples.
            dry_run: resolve and report what *would* be written without making
                any change.

        Returns:
            :class:`~pyfsr.models.IngestionSetupResult` describing the
            collection, playbooks, schedule, and health at the end of the run.

        Raises:
            ValueError: the connector isn't installed, the configuration can't
                be resolved, health isn't ``Available`` (with ``require_health``),
                or the connector ships no ``ingest``-tagged playbook.

        Example (needs a live appliance)::

            result = client.connectors.data_ingest_wizard(
                "fortinet-fortisiem", config="prod", cron="*/15 * * * *")
            result.scheduled        # True
        """
        version = version or self.resolve_version(connector)
        if not version:
            raise ValueError(f"{connector!r} is not installed (cannot resolve a version)")

        summaries = self.configurations(connector)
        if not summaries:
            raise ValueError(f"{connector!r} has no configuration -- create one before setting up ingestion")
        chosen = None
        if config:
            chosen = next((c for c in summaries if c.config_id == config or c.name == config), None)
            if chosen is None:
                names = ", ".join(f"{c.name!r}" for c in summaries)
                raise ValueError(f"no configuration named or identified by {config!r} on {connector!r} (have: {names})")
        else:
            chosen = next((c for c in summaries if c.default), None) or summaries[0]
        config_id = chosen.config_id
        if not config_id:
            raise ValueError(f"configuration {chosen.name!r} on {connector!r} has no config_id")

        result = IngestionSetupResult(
            connector=connector,
            version=version,
            config_id=config_id,
            config_name=chosen.name,
            dry_run=dry_run,
        )

        health = self.healthcheck(connector, version=version, config=config_id)
        result.health_status = health.status
        if require_health and str(health.status or "").strip().lower() != "available":
            raise ValueError(
                f"configuration {chosen.name!r} on {connector!r} is {health.status!r}, not 'Available'. "
                "The UI gates data ingestion on a green health check; pass require_health=False to override."
            )

        installed = self._find_configured(connector)
        label = (installed.label if installed else None) or connector
        collection_name = f"{label} {version} {chosen.name or ''}Ingestion({config_id})"
        result.collection_name = collection_name
        result.collection_uuid = config_id

        source_collection = source_collection or self.find_sample_ingestion_collection(connector, version=version)
        if not source_collection:
            raise ValueError(
                f"{connector!r} ships no 'Sample - …' playbook collection to clone ingestion playbooks from"
            )

        existing = self._ingestion_query(config_id, connector) if self._collection_exists(config_id) else []
        if existing and reuse_existing:
            buckets = self._bucket_by_tag(existing)
            result.playbooks = existing
        else:
            source_playbooks = self._dataingestion_playbooks(source_collection, connector)
            samples = self._bucket_by_tag(source_playbooks)
            if samples.ingest is None:
                raise ValueError(
                    f"no 'ingest'-tagged playbook for {connector!r} in collection {source_collection!r} -- "
                    "the connector's sample playbooks must carry the #ingest and #dataingestion tags"
                )
            if dry_run:
                result.playbooks = source_playbooks
                return result
            self._ensure_ingestion_collection(config_id, collection_name)
            buckets, cloned = self._clone_ingestion_playbooks(
                source_playbooks,
                samples,
                connector=connector,
                config_id=config_id,
                collection_uuid=config_id,
                agent=agent,
                activate=activate,
            )
            result.playbooks = cloned
            result.cloned = True

        if dry_run:
            return result

        ingest = buckets.ingest
        if ingest is None:
            raise ValueError(f"the ingestion collection for {connector!r}/{config_id} has no 'ingest' playbook")
        ingest_iri = ingest.id_iri or f"/api/3/workflows/{ingest.uuid}"
        result.ingest_playbook_iri = ingest_iri

        # The UI's _getSchedularName(): "Ingestion_<connector>_<config name>_<config_id>".
        # Match it exactly -- the ingestion screen and the data-import record find each
        # other by this string, so a differently-named schedule reads as "not configured".
        schedule_name = schedule_name or f"Ingestion_{connector}_{chosen.name or ''}_{config_id}"
        result.schedule_name = schedule_name
        if cron:
            task = self.client.schedules.create(
                schedule_name,
                ingest_iri,
                cron,
                timezone=timezone,
                enabled=enabled,
                exit_if_running=exit_if_running,
                typed=False,
            )
            result.schedule_id = str(task.get("id")) if isinstance(task, dict) else None
            result.scheduled = bool(enabled)

        self.save_ingestion_metadata(
            config_id,
            connector=connector,
            version=version,
            name=schedule_name,
            schedule_id=result.schedule_id,
            schedule_name=schedule_name if cron else "",
            schedule_enabled=result.scheduled,
            created=not self.ingestion_metadata(config_id),
        )
        return result

    def ensure_ingestion(
        self, connector: str, *, config: str | None = None, **wizard_kwargs: Any
    ) -> IngestionSetupResult:
        """Set up data ingestion only if it isn't already -- "make it or get it".

        The idempotent front door to :meth:`data_ingest_wizard`, mirroring the
        ``get_or_create`` pattern elsewhere in pyfsr: it calls
        :meth:`ingestion_status` first and, when ingestion is already
        ``configured`` (per-config collection + an ``ingest``-tagged playbook),
        returns an :class:`~pyfsr.models.IngestionSetupResult` describing the
        existing setup **without writing anything** (``existed=True``).
        Otherwise it delegates to the wizard (``existed=False``), forwarding any
        ``**wizard_kwargs`` (``cron``, ``schedule_name``, ``agent``,
        ``require_health``, ``dry_run``, ...).

        Safe to call repeatedly -- the common "set ingestion up if it's not
        there" line becomes one call instead of a status-check-then-branch. It
        does **not** reconcile drift: an already-configured setup is returned
        as-is even if it has no schedule and you passed ``cron`` -- call
        :meth:`data_ingest_wizard` directly to add or change a schedule on an
        existing setup.

        Args:
            connector: connector name.
            config: config name or ``config_id``; defaults to the default
                configuration.
            **wizard_kwargs: forwarded verbatim to :meth:`data_ingest_wizard`
                when a setup is actually built.

        Returns:
            :class:`~pyfsr.models.IngestionSetupResult`; ``existed`` distinguishes
            the "get" path (already configured) from the "make" path.

        Example (needs a live appliance)::

            first = client.connectors.ensure_ingestion(
                "fortinet-fortisiem", config="prod", cron="*/15 * * * *")
            first.existed        # False -- the wizard built it
            again = client.connectors.ensure_ingestion("fortinet-fortisiem", config="prod")
            again.existed        # True -- returned as-is, no writes
        """
        status = self.ingestion_status(connector, config=config)
        if not status.configured:
            result = self.data_ingest_wizard(connector, config=config, **wizard_kwargs)
            result.existed = False
            return result

        # Already configured -- describe it without writing. Dedup the role
        # buckets by uuid (one playbook can fill several roles, e.g. ingest+create).
        seen: set[str] = set()
        playbooks: list[Workflow] = []
        for pb in (status.playbooks.fetch, status.playbooks.ingest, status.playbooks.create, status.playbooks.update):
            if pb is None:
                continue
            key = str(pb.uuid)
            if key not in seen:
                seen.add(key)
                playbooks.append(pb)
        ingest = status.playbooks.ingest
        return IngestionSetupResult(
            connector=connector,
            config_id=status.config_id,
            config_name=(status.metadata.name if status.metadata else None),
            collection_uuid=status.config_id,
            playbooks=playbooks,
            ingest_playbook_iri=(ingest.id_iri or f"/api/3/workflows/{ingest.uuid}") if ingest else None,
            schedule_id=status.schedule_id,
            schedule_name=status.schedule_name,
            scheduled=bool(status.schedule_enabled),
            existed=True,
        )

    def remove_ingestion(
        self,
        connector: str,
        *,
        config: str | None = None,
        delete_collection: bool = True,
        dry_run: bool = False,
    ) -> IngestionTeardownResult:
        """Tear down a configuration's data ingestion -- the inverse of the wizard.

        Reverses :meth:`data_ingest_wizard`'s writes in dependency order:

        1. delete the periodic task (by the name recorded in the metadata), so
           nothing fires mid-teardown;
        2. delete the ``data-import`` metadata record(s) that linked the config
           to that schedule;
        3. (when ``delete_collection``) hard-delete the per-configuration
           collection, which **cascades** the cloned ingestion playbooks.

        Idempotent and partial-safe: a missing schedule / metadata / collection
        is simply skipped (the corresponding ``*_deleted`` stays ``False``), so
        re-running after a half-done teardown finishes the job rather than
        erroring. Built entirely on verified primitives -- ``schedules.delete``
        and ``workflow_collections.delete`` (hard).

        Args:
            connector: connector name.
            config: config name or ``config_id``; defaults to the default
                configuration.
            delete_collection: also hard-delete the per-config collection and
                its cloned playbooks (default). ``False`` removes only the
                schedule + metadata, leaving the playbooks in place -- useful to
                keep hand-edited clones while detaching the schedule.
            dry_run: report what *would* be removed without deleting anything.

        Returns:
            :class:`~pyfsr.models.IngestionTeardownResult` recording what was
            (or, under ``dry_run``, would be) removed.

        Raises:
            ValueError: the configuration can't be resolved.

        Example (needs a live appliance)::

            client.connectors.remove_ingestion("fortinet-fortisiem", config="prod")
        """
        config_id = self.resolve_config(connector, config)
        if not config_id:
            raise ValueError(f"cannot resolve a configuration for {connector!r}")

        status = self.ingestion_status(connector, config=config)
        result = IngestionTeardownResult(connector=connector, config_id=config_id, dry_run=dry_run)

        if status.schedule_name:
            result.schedule_name = status.schedule_name
            if not dry_run:
                try:
                    self.client.schedules.delete(status.schedule_name)
                    result.schedule_deleted = True
                except ValueError:
                    pass  # already gone -- leave schedule_deleted False

        for meta in self.ingestion_metadata(config_id):
            if meta.id is None:
                continue
            result.metadata_ids.append(meta.id)
            if not dry_run:
                # Trailing slash is required: without it the appliance's APPEND_SLASH
                # redirect re-issues the DELETE and the retry fails HMAC validation (403).
                self.client.delete(f"/api/integration/data-import/{meta.id}/")
                result.metadata_deleted += 1

        if delete_collection and status.collection_exists:
            result.collection_uuid = config_id
            if not dry_run:
                try:
                    self.client.workflow_collections.delete(config_id, hard=True)
                    result.collection_deleted = True
                except ResourceNotFoundError:
                    # Deleting the data-import record cascades the per-config
                    # collection (and its cloned playbooks) on the appliance, so
                    # by this point it may already be gone. Idempotent: fine.
                    pass

        return result

    def ingestion_status(
        self, connector: str, *, config: str | None = None, live_schedule: bool = False
    ) -> IngestionStatus:
        """Report a configuration's current data-ingestion state -- read-only.

        The inverse-lens of :meth:`data_ingest_wizard`: it inspects the four
        pieces the wizard builds and reports whether each is present, **without
        writing anything**. Use it to answer "is ingestion set up for this
        config, and is its schedule running?" before setting up, tearing down,
        or triggering.

        The per-configuration ingestion collection has the ``config_id`` as its
        uuid (the wizard's own convention), so this looks there -- not in the
        connector's shared ``Sample - …`` collection -- for the cloned
        ``ingest``-tagged playbook. The schedule id / name / enabled state come
        from the ``data-import`` metadata record the wizard writes; pass
        ``live_schedule=True`` to re-read the periodic task itself so a schedule
        toggled off since setup reports ``schedule_enabled=False`` rather than
        the recorded write-time value.

        Args:
            connector: connector name.
            config: config name or ``config_id``; defaults to the default
                configuration.
            live_schedule: re-read the periodic task named in the metadata
                record for its current ``enabled`` state, instead of trusting
                the ``scheduleStatus`` the metadata recorded at setup time. One
                extra request; off by default.

        Returns:
            :class:`~pyfsr.models.IngestionStatus`. When nothing is set up,
            ``.configured`` is ``False`` and the playbook buckets are empty --
            not an error.

        Raises:
            ValueError: the configuration can't be resolved.

        Example:
            >>> client = demo_client()
            >>> status = client.connectors.ingestion_status("mitre-attack")
            >>> status.configured          # no per-config collection in the demo box
            False
        """
        config_id = self.resolve_config(connector, config)
        if not config_id:
            raise ValueError(f"cannot resolve a configuration for {connector!r}")

        status = IngestionStatus(connector=connector, config_id=config_id)
        if self._collection_exists(config_id):
            status.collection_exists = True
            status.playbooks = self._bucket_by_tag(self._ingestion_query(config_id, connector))

        metas = self.ingestion_metadata(config_id)
        if metas:
            meta = metas[0]
            md = meta.metadata or {}
            status.metadata = meta
            status.schedule_id = meta.schedule_id
            status.schedule_name = md.get("scheduleName") or None
            recorded = md.get("scheduleStatus")
            status.schedule_enabled = bool(recorded) if recorded is not None else None
            if live_schedule and status.schedule_name:
                task = self.client.schedules.get(status.schedule_name)
                if isinstance(task, ScheduledTask):
                    status.schedule_enabled = bool(task.enabled)
        return status

    def trigger_ingestion(
        self,
        connector: str,
        *,
        config: str | None = None,
        playbook: str | None = None,
    ) -> dict[str, Any]:
        """Run a configuration's ingestion right now -- the *Trigger Ingestion Now* button.

        ``POST /api/triggers/1/notrigger/<ingest playbook uuid>``. Note this is
        **not** the scheduler's trigger
        (:meth:`~pyfsr.api.schedules.SchedulesAPI.trigger_now`, which posts to
        ``/api/wf/api/scheduled/trigger-now/``): the UI's ingestion button
        bypasses the periodic task entirely and fires the playbook named by the
        schedule's ``kwargs.wf_iri``. That distinction matters -- the ingestion
        button works on a configuration whose schedule is disabled, or that has
        no schedule at all.

        Resolves the ``ingest``-tagged playbook inside the configuration's own
        ingestion collection, so it fires the per-config clone rather than the
        shared sample playbook.

        Args:
            connector: connector name.
            config: config name or ``config_id``; defaults to the default
                configuration.
            playbook: fire this playbook uuid/IRI instead of resolving one
                (escape hatch for a non-standard ingestion layout).

        Returns:
            The trigger response, carrying the async ``task_id``. The fire is
            asynchronous -- poll ``client.playbooks.execution_history(...)`` or
            watch the target module's record count to see the result.

        Raises:
            ValueError: the configuration can't be resolved, or its ingestion
                collection holds no ``ingest``-tagged playbook (i.e. ingestion
                was never set up -- run :meth:`data_ingest_wizard` first).

        Example:
            >>> client = demo_client()
            >>> client.connectors.trigger_ingestion("mitre-attack")
            {'task_id': '9e7df03a-29d9-4b90-a4a7-6e61810efd88'}
        """
        if playbook:
            uuid = playbook.rstrip("/").split("/")[-1]
        else:
            config_id = self.resolve_config(connector, config)
            if not config_id:
                raise ValueError(f"cannot resolve a configuration for {connector!r}")
            buckets = self._bucket_by_tag(self._ingestion_query(config_id, connector))
            if buckets.ingest is None:
                raise ValueError(
                    f"no ingestion playbook for {connector!r}/{config_id} -- "
                    "run data_ingest_wizard() to set ingestion up first"
                )
            uuid = str(buckets.ingest.uuid)
        resp = self.client.post(f"/api/triggers/1/notrigger/{uuid}", data={})
        return resp if isinstance(resp, dict) else {"result": resp}

    def _collection_exists(self, uuid: str) -> bool:
        """Whether a workflow collection with ``uuid`` already exists."""
        try:
            self.client.get(f"/api/3/workflow_collections/{uuid}")
            return True
        except Exception:
            return False

    def _ensure_ingestion_collection(self, config_id: str, name: str) -> dict[str, Any]:
        """Find-or-create the per-configuration ingestion collection.

        Its ``uuid`` is deliberately the ``config_id`` -- that identity is how
        the UI locates a configuration's ingestion playbooks, so it must not be
        left to the server to generate. Created ``visible=False`` so the
        collection doesn't clutter the playbook list.
        """
        try:
            existing = self.client.get(f"/api/3/workflow_collections/{config_id}")
            if isinstance(existing, dict):
                return existing
        except Exception:
            pass
        created = self.client.post(
            "/api/3/workflow_collections",
            data={"name": name, "uuid": config_id, "visible": False},
        )
        return created if isinstance(created, dict) else {"uuid": config_id, "name": name}

    def _clone_ingestion_playbooks(
        self,
        source_playbooks: list[Workflow],
        samples: IngestionPlaybooks,
        *,
        connector: str,
        config_id: str,
        collection_uuid: str,
        agent: str | None,
        activate: bool,
    ) -> tuple[IngestionPlaybooks, list[Workflow]]:
        """Clone the sample ingestion playbooks and apply the wizard's rewrites.

        Two passes, because the rewrites reference the *clones*: first clone
        every ``#dataingestion`` playbook into the per-config collection
        (remapping owned uuids via
        :meth:`~pyfsr.api.playbooks.PlaybooksAPI.clone`), then patch
        cross-playbook references -- the old→new uuid map and the ``create``
        playbook's new uuid aren't known until every clone exists.

        The second pass re-reads each clone with ``get_definition(...,
        relationships=True)``. A clone's POST *response* carries its steps as
        IRIs rather than inlined objects, so patching the response finds no
        references to rewrite and silently leaves the copies pointing at the
        shared sample playbooks.
        """
        suffix = config_id.replace("-", "_")
        uuid_map: dict[str, str] = {}
        clones: dict[str, Workflow] = {}

        for source in source_playbooks:
            source_uuid = str(source.uuid)
            if source_uuid in uuid_map:
                continue
            created = self.client.playbooks.clone(
                source_uuid,
                str(source.name or source_uuid),
                collection=collection_uuid,
                is_active=False,
                # Ingestion discovery is tag-based; carry the sample's functional
                # tags (fetch/ingest/create/dataingestion/<connector>) onto the
                # clone, which clone() would otherwise strip.
                record_tags=[str(t) for t in (source.recordTags or []) if t],
                transform=lambda body: _rewrite_ingestion_playbook(body, connector, config_id, suffix, agent),
            )
            uuid_map[source_uuid] = str(created.get("uuid"))
            clones[source_uuid] = Workflow.model_validate(created)

        create_uuid = None
        if samples.create is not None:
            create_uuid = uuid_map.get(str(samples.create.uuid))

        for source_uuid, clone in list(clones.items()):
            clone_uuid = str(clone.uuid)
            # Re-read with relationships so the steps are inlined objects; the
            # clone response's steps are bare IRIs and carry no references.
            definition = self.client.playbooks.get_definition(clone_uuid, relationships=True).to_dict(by_alias=True)
            patched = _patch_clone_references(definition, uuid_map, create_uuid)
            if patched is not None:
                self.client.playbooks.update(clone_uuid, steps=patched)
            if activate:
                self.client.playbooks.update(clone_uuid, isActive=True)
            clones[source_uuid] = Workflow.model_validate(
                self.client.playbooks.get_definition(clone_uuid, relationships=True).to_dict(by_alias=True)
            )

        def _clone_for(pb: Workflow | None) -> Workflow | None:
            return clones.get(str(pb.uuid)) if pb else None

        buckets = IngestionPlaybooks.model_validate(
            {role: _clone_for(getattr(samples, role)) for role in ("fetch", "ingest", "create", "update")}
        )
        return buckets, list(clones.values())


def _import_job_id(resp: dict[str, Any]) -> str | None:
    """Pull the import-job id out of a Content-Hub install response.

    The install reply is the ``SolutionPack`` record; its async install job is
    the nested ``importJob`` object (``{"@id": "/api/3/import_jobs/<uuid>",
    "uuid": ...}``). Falls back to a top-level ``import_jobs`` IRI if present.
    Note: the response's *top-level* ``uuid`` is the solution pack's, not the
    job's -- don't use it.
    """
    job = resp.get("importJob")
    if isinstance(job, dict):
        uuid = job.get("uuid")
        if isinstance(uuid, str) and uuid:
            return uuid
        iri = job.get("@id")
        if isinstance(iri, str) and iri:
            return iri.rstrip("/").split("/")[-1]
    iri = resp.get("@id")
    if isinstance(iri, str) and "import_jobs" in iri:
        return iri.rstrip("/").split("/")[-1]
    return None


_GLOBAL_VAR_RE = re.compile(r"(globalVars\.)(\w+)")


def _rewrite_ingestion_playbook(
    body: dict[str, Any],
    connector: str,
    config_id: str,
    suffix: str,
    agent: str | None,
) -> dict[str, Any]:
    """Apply the wizard's per-configuration rewrites to a cloned playbook body.

    Two edits, both of which the UI makes before persisting a clone:

    * **Global-variable namespacing** -- every ``globalVars.X`` reference becomes
      ``globalVars.X_<config_id with dashes as underscores>``, and any bare
      string equal to a renamed variable is renamed with it. Without this, two
      configurations of the same connector silently share ingestion state (last
      pull time, cursors) and interleave their fetches.
    * **Connector binding** -- steps whose ``arguments.connector`` is *this*
      connector get ``arguments.config`` set to the configuration being wired
      up, plus ``arguments.agent`` when the connector runs on an agent. Steps
      calling other connectors (``cyops_utilities`` and friends) are left alone,
      which is exactly what a live ingestion collection looks like.
    """
    renamed: set[str] = set()

    def rename(match: re.Match[str]) -> str:
        renamed.add(match.group(2))
        return f"{match.group(1)}{match.group(2)}_{suffix}"

    serialized = _GLOBAL_VAR_RE.sub(rename, json.dumps(body))
    for name in renamed:
        serialized = serialized.replace(f'"{name}"', f'"{name}_{suffix}"')
    body = json.loads(serialized)

    for step in body.get("steps") or []:
        args = step.get("arguments")
        if isinstance(args, dict) and args.get("connector") == connector:
            args["config"] = config_id
            if agent:
                args["agent"] = agent
    return body


def _patch_clone_references(
    playbook: dict[str, Any],
    uuid_map: dict[str, str],
    create_uuid: str | None,
) -> list[dict[str, Any]] | None:
    """Repoint a clone's cross-playbook references at the other clones.

    Runs after every clone exists, because these references can't be resolved
    during the clone itself:

    * ``params.create_pb_id`` on the *Fetch and Create* step must name the
      **cloned** ``create`` playbook, not the sample one -- otherwise ingestion
      writes records through the shared sample playbook and ignores the
      per-configuration copy;
    * reference steps pointing at a sample playbook's uuid/IRI are remapped onto
      its clone.

    Returns the patched ``steps`` list, or ``None`` when nothing changed.
    """
    steps = playbook.get("steps")
    if not isinstance(steps, list):
        return None
    serialized = json.dumps(steps)
    patched = serialized
    for old, new in uuid_map.items():
        patched = patched.replace(old, new)
    steps = json.loads(patched)

    changed = patched != serialized
    if create_uuid:
        for step in steps:
            if str(step.get("name") or "").strip().lower() != "fetch and create":
                continue
            args = step.setdefault("arguments", {})
            params = args.setdefault("params", {})
            if params.get("create_pb_id") != create_uuid:
                params["create_pb_id"] = create_uuid
                changed = True
    return steps if changed else None
