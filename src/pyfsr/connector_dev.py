"""Local ConnectorsV2 development helpers: scaffold, package, deploy.

The full connector flow a coding agent (or a human) drives from a checkout:

1. :func:`scaffold_connector` -- write a minimal, installable connector skeleton
   (``info.json``, ``connector.py``, ``operations.py``, ``requirements.txt``).
2. edit the generated files (add operations, config fields, icons).
3. :func:`package_connector` -- build the ``dist/<name>-<version>.tgz`` bundle
   FortiSOAR installs, preferring git-tracked files.
4. :func:`deploy_connector` -- upload the bundle (``$replace`` so redeploys are
   idempotent), wait for the connector to activate, optionally configure it, and
   healthcheck.

CLI: ``pyfsr connector scaffold|package|deploy`` (see :mod:`pyfsr.cli.connector`).
"""

from __future__ import annotations

import json
import re
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any

from pyfsr.api.connectors import ConnectorPackageError

#: Files every installable connector bundle must contain.
REQUIRED_FILES = ("info.json", "connector.py", "operations.py", "requirements.txt")

#: Paths never shipped in a bundle (the ConnectorsV2 package.sh exclusions, plus
#: secrets and environments). Matched per path SEGMENT, so a nested ``sub/.env`` or
#: ``lib/.venv/`` is excluded as surely as a top-level one.
_EXCLUDE = re.compile(
    r"(^|/)("
    r"\.git(/|$)|\.gitignore$|\.env[^/]*$|\.venv(/|$)|venv(/|$)|__pycache__(/|$)"
    r"|[^/]*\.pyc$|\.DS_Store$|[^/]*\.pem$|[^/]*\.key$|id_rsa[^/]*$|\.pyfsr(/|$)|\.netrc$"
    r"|credentials(\.[^/]*)?$|[^/]*secrets?\.(json|ya?ml|toml|ini)$"
    r")"
    r"|^(dist/|scripts/|tests/|README\.md$|release_notes\.md$)"
)

#: Outside git, only these connector files ship (an allowlist, so a stray file in
#: the working tree never rides along).
_ALLOW_UNTRACKED = re.compile(
    r"^(info\.json|requirements\.txt|[^/]+\.py|[^/]+/[^/].*\.py"
    r"|images/[^/]+\.(png|svg|jpg|jpeg|gif)|playbooks/.+|docs/.+)$"
)


def _validate_name(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError(
            f"connector name {name!r} must be lowercase letters, digits and hyphens (e.g. 'acme-threat-feed')"
        )
    return name


# --------------------------------------------------------------------------- scaffold

_CONNECTOR_PY = """\
from connectors.core.connector import Connector, ConnectorError, get_logger

from .operations import check_health, operations

logger = get_logger('{name}')


class {class_name}(Connector):
    def execute(self, config, operation, params, **kwargs):
        action = operations.get(operation)
        if action is None:
            raise ConnectorError('Unknown operation: {{0}}'.format(operation))
        logger.info('Executing action {{0}}'.format(operation))
        try:
            return action(config, params or {{}})
        except ConnectorError:
            raise
        except Exception as err:
            raise ConnectorError(str(err))

    def check_health(self, config):
        return check_health(config)
"""

_OPERATIONS_PY = '''\
"""Operation handlers for the {label} connector.

Each handler takes ``(config, params)`` and returns a JSON-serializable result.
``config`` holds the field values defined in info.json's configuration block.
"""

from connectors.core.connector import ConnectorError


def ping(config, params):
    """The scaffold's sample operation: echo the message back with the server."""
    return {{
        'reply': params.get('message', 'pong'),
        'server': config.get('server'),
    }}


def check_health(config):
    """Raise ConnectorError when the configured target is unreachable."""
    if not config.get('server'):
        raise ConnectorError('server is not configured')
    return True


operations = {{
    'ping': ping,
}}
'''


def _info_json(name: str, label: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "version": "1.0.0",
        "label": label,
        "description": description,
        "publisher": "Community",
        "cs_approved": False,
        "cs_compatible": True,
        "category": "Utilities",
        "icon_small_name": "",
        "icon_large_name": "",
        "configuration": {
            "fields": [
                {
                    "name": "server",
                    "title": "Server URL",
                    "type": "text",
                    "editable": True,
                    "visible": True,
                    "required": True,
                    "placeholder": "e.g. https://service.example.com",
                    "description": "Base URL of the target service.",
                }
            ]
        },
        "operations": [
            {
                "operation": "ping",
                "title": "Ping",
                "description": "Echo a message back; the scaffold's sample operation.",
                "category": "investigation",
                "annotation": "ping",
                "enabled": True,
                "parameters": [
                    {
                        "name": "message",
                        "title": "Message",
                        "type": "text",
                        "editable": True,
                        "visible": True,
                        "required": False,
                        "description": "Text to echo back (default 'pong').",
                    }
                ],
                "output_schema": {"reply": "", "server": ""},
            }
        ],
    }


# -------- multi-operation scaffold (a real-API foundation to build on) --------
# When the caller names operations, the skeleton gets a shared request helper and
# one handler per op instead of the single `ping`. The helper is antipattern-clean
# by construction: verify comes from config (no TLS bypass), the status is checked
# before .json(), and path segments are quoted -- so the connector passes a static
# review from the start and the author only fills in endpoints.

_OPERATIONS_PY_MULTI_HEADER = '''\
"""Operation handlers for the {label} connector.

Fill in each handler's endpoint path, query and body. The request helper already
checks the response status, honours the configured SSL verification, and quotes
path segments, so build on it rather than calling requests directly.
"""
from urllib.parse import quote

import requests

from connectors.core.connector import ConnectorError, get_logger

logger = get_logger("{name}")


def _request(config, method, path, query=None, body=None):
    base = str(config.get("base_url", "")).rstrip("/")
    safe_path = "/".join(quote(str(seg), safe="") for seg in path if seg != "")
    url = f"{{base}}/{{safe_path}}"
    headers = {{"Authorization": f"Bearer {{config.get('api_key', '')}}",
               "Accept": "application/json"}}
    try:
        resp = requests.request(method, url, headers=headers, params=query or {{}},
                                json=body, timeout=30, verify=config.get("verify", True))
    except requests.RequestException as exc:
        raise ConnectorError(f"{label}: request to {{url}} failed: {{exc}}")
    if resp.status_code not in (200, 201, 202, 204):
        raise ConnectorError(f"{label}: {{method}} {{url}} returned HTTP {{resp.status_code}}")
    if not resp.content:
        return {{}}
    try:
        return resp.json()
    except ValueError:
        return {{"raw": resp.text}}
'''


def _op_slug(raw: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", str(raw).strip().lower()).strip("_")
    return s or "operation"


def _multi_operations_py(name: str, label: str, ops: list[dict]) -> str:
    out = [_OPERATIONS_PY_MULTI_HEADER.format(label=label, name=name)]
    for op in ops:
        slug = _op_slug(op["operation"])
        required = [p["name"] for p in op.get("parameters", []) if p.get("required")]
        reads = "\n    ".join(f"{_op_slug(n)} = params.get({n!r})" for n in required) or "pass"
        query = ", ".join(f"{n!r}: params.get({n!r})" for n in required)
        out.append(
            f"\n\ndef {slug}(config, params):\n"
            f"    # TODO: set the real path segments, query and body for this operation.\n"
            f"    {reads}\n"
            f"    return _request(config, {op.get('method', 'GET').upper()!r}, "
            f"[{slug!r}], query={{{query}}}, body=None)\n"
        )
    out.append('\n\ndef check_health(config):\n    _request(config, "GET", [], query={})\n    return True\n')
    items = ",\n".join(f"    {_op_slug(op['operation'])!r}: {_op_slug(op['operation'])}" for op in ops)
    out.append(f"\n\noperations = {{\n{items},\n}}\n")
    return "".join(out)


def _info_json_multi(name: str, label: str, description: str, ops: list[dict]) -> dict[str, Any]:
    info = _info_json(name, label, description)
    info["configuration"]["fields"] = [
        {
            "name": "base_url",
            "title": "Server URL",
            "type": "text",
            "required": True,
            "visible": True,
            "editable": True,
        },
        {
            "name": "api_key",
            "title": "API Key",
            "type": "password",
            "required": True,
            "visible": True,
            "editable": True,
        },
        {
            "name": "verify",
            "title": "Verify SSL",
            "type": "checkbox",
            "required": False,
            "visible": True,
            "editable": True,
            "value": True,
        },
    ]
    info["operations"] = [
        {
            "operation": _op_slug(op["operation"]),
            "title": op.get("title") or _op_slug(op["operation"]).replace("_", " ").title(),
            "description": op.get("description", op.get("title", op["operation"])),
            "category": "investigation",
            "annotation": _op_slug(op["operation"]),
            "enabled": True,
            "parameters": [
                {
                    "name": p["name"],
                    "title": p.get("title") or p["name"].replace("_", " ").title(),
                    "type": p.get("type", "text"),
                    "required": bool(p.get("required", False)),
                    "visible": True,
                    "editable": True,
                }
                for p in op.get("parameters", [])
            ],
            "output_schema": op.get("output_schema", {}),
        }
        for op in ops
    ]
    return info


def scaffold_connector(
    name: str,
    directory: str | Path | None = None,
    *,
    label: str | None = None,
    description: str = "A custom FortiSOAR connector.",
    ops: list[dict] | None = None,
) -> list[Path]:
    """Write an installable ConnectorsV2 skeleton and return the paths.

    By default the skeleton has one configuration field (``server``), one
    operation (``ping``) and a health check, so it packages, installs, configures
    and runs end to end before any real logic is added.

    With ``ops`` (a list of ``{operation, title?, description?, method?,
    parameters?:[{name,type?,required?,title?}], output_schema?}``) the skeleton
    instead gets a shared, antipattern-clean request helper and one handler per op
    over a ``base_url``/``api_key``/``verify`` configuration -- a real-API
    foundation to fill in, not just a hello-world. Refuses to overwrite.
    """
    name = _validate_name(name)
    root = Path(directory) if directory is not None else Path(name)
    root.mkdir(parents=True, exist_ok=True)
    existing = [f for f in REQUIRED_FILES if (root / f).exists()]
    if existing:
        raise FileExistsError(f"{root} already holds {', '.join(existing)}; refusing to overwrite")

    label = label or name.replace("-", " ").title()
    class_name = re.sub(r"[^0-9a-zA-Z]", "", label.title()) or "CustomConnector"

    written: list[Path] = []

    def _write(rel: str, text: str) -> None:
        p = root / rel
        p.write_text(text)
        written.append(p)

    if ops:
        info = _info_json_multi(name, label, description, ops)
        operations_py = _multi_operations_py(name, label, ops)
    else:
        info = _info_json(name, label, description)
        operations_py = _OPERATIONS_PY.format(label=label)

    _write("info.json", json.dumps(info, indent=2) + "\n")
    _write("connector.py", _CONNECTOR_PY.format(name=name, class_name=class_name))
    _write("operations.py", operations_py)
    _write("requirements.txt", "")
    return written


# --------------------------------------------------------------------------- package


def _bundle_files(root: Path) -> list[str]:
    """Relative paths to ship: git-tracked when available, else the working tree."""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        files = [line for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.CalledProcessError):
        files = []
    if not files:
        files = [
            rel
            for p in sorted(root.rglob("*"))
            if p.is_file() and _ALLOW_UNTRACKED.match(rel := p.relative_to(root).as_posix())
        ]
    return [f for f in files if not _EXCLUDE.search(f)]


def _bump_version(version: str, part: str = "patch") -> str:
    """Increment a ``MAJOR.MINOR.PATCH`` version. Non-semver input gets a numeric
    suffix bumped if present, else ``.1`` appended -- never raises, so a reupload
    can always be made distinct to avoid a ghost connector."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(.*)", version.strip())
    if m:
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if part == "major":
            major, minor, patch = major + 1, 0, 0
        elif part == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
        return f"{major}.{minor}.{patch}"
    tail = re.search(r"(\d+)(\D*)$", version)
    if tail:
        return version[: tail.start(1)] + str(int(tail.group(1)) + 1) + tail.group(2)
    return f"{version}.1"


def package_connector(
    directory: str | Path = ".", *, out_dir: str | Path | None = None, version: str | None = None
) -> Path:
    """Build ``<out_dir>/<name>-<version>.tgz`` unpacking to one ``<name>/`` dir.

    Ports the ConnectorsV2 ``package.sh``: prefer git-tracked files, drop tests,
    packaging furniture and caches, and require the four core files. Returns the
    bundle path. ``out_dir`` defaults to ``<directory>/dist``.

    ``version`` overrides the version used for the bundle name AND injected into
    the archived ``info.json`` -- the source file is left untouched. This is how a
    redeploy ships a bumped version without a ghost connector.
    """
    root = Path(directory).resolve()
    info_path = root / "info.json"
    if not info_path.exists():
        raise FileNotFoundError(f"{root} has no info.json; is it a connector checkout?")
    info = json.loads(info_path.read_text())
    name = info["name"]
    src_version = info["version"]
    version = version or src_version

    files = _bundle_files(root)
    missing = [f for f in REQUIRED_FILES if f not in files]
    if missing:
        raise FileNotFoundError(f"bundle is missing required file(s): {', '.join(missing)}")

    out = Path(out_dir) if out_dir is not None else root / "dist"
    out.mkdir(parents=True, exist_ok=True)
    tgz = out / f"{name}-{version}.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        for rel in files:
            if rel == "info.json" and version != src_version:
                import io

                data = json.dumps({**info, "version": version}, indent=2).encode()
                ti = tarfile.TarInfo(f"{name}/info.json")
                ti.size = len(data)
                ti.mtime = int(time.time())
                tar.addfile(ti, io.BytesIO(data))
            else:
                tar.add(root / rel, arcname=f"{name}/{rel}")
    return tgz


# --------------------------------------------------------------------------- deploy


def wait_for_active(client, name: str, timeout: float = 180.0, interval: float = 3.0) -> None:
    """Poll ``list_configured(refresh=True)`` until ``name`` shows up active.

    The install job finishing does not mean the connector record is live yet
    (live-verified in lab deploys), so installs must wait on the listing.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for entry in client.connectors.list_configured(refresh=True):
            if entry.name == name and entry.active is not False:
                return
        time.sleep(interval)
    raise TimeoutError(f"{name} did not activate within {int(timeout)}s (check the import job)")


def _tgz_identity(tgz: Path) -> tuple[str, str]:
    """(name, version) read from a built bundle's archived info.json."""
    with tarfile.open(tgz, "r:gz") as tar:
        name = tar.getnames()[0].split("/", 1)[0]
        member = tar.extractfile(f"{name}/info.json")
        version = json.loads(member.read())["version"] if member else ""
    return name, version


def _installed_version(client, name: str) -> str | None:
    """The version of ``name`` already on the box, or None. Degrades to None when
    the client cannot report it, so the guard never blocks a valid first install."""
    rv = getattr(client.connectors, "resolve_version", None)
    if callable(rv):
        try:
            return rv(name)
        except Exception:  # noqa: BLE001
            return None
    return None


def deploy_connector(
    client,
    source: str | Path,
    *,
    config: dict[str, Any] | None = None,
    config_name: str = "Default",
    replace: bool = True,
    timeout: float = 180.0,
    on_existing_version: str = "bump_patch",
) -> dict[str, Any]:
    """Package (if ``source`` is a checkout), install, activate, configure, healthcheck.

    ``source`` is a connector directory (packaged first) or a built ``.tgz``.
    With ``config``, an idempotent named configuration is upserted and marked
    default, then healthchecked. Returns a summary dict of what happened.

    GHOST-CONNECTOR GUARD. FortiSOAR keys a connector by name+version; reinstalling
    the SAME version over a changed bundle can leave a stale "ghost" alongside the
    new code. ``on_existing_version`` controls what happens when the box already
    has this connector at the version being deployed:

    - ``"bump_patch"`` (default): bump the patch and ship that instead (only for a
      directory source, which can be repackaged); the result records ``bumped``.
    - ``"error"``: raise, so the caller bumps info.json deliberately.
    - ``"allow"``: install over it anyway (the old behaviour).
    """
    src = Path(source)
    if src.is_dir():
        info = json.loads((src / "info.json").read_text())
        name, version = info["name"], info["version"]
    else:
        if not src.exists():
            raise FileNotFoundError(f"{src} does not exist")
        name, version = _tgz_identity(src)

    bumped_to: str | None = None
    installed = _installed_version(client, name)
    if installed is not None and installed == version and on_existing_version != "allow":
        if on_existing_version == "error":
            raise ConnectorPackageError(
                f"{name} {version} is already installed; bump info.json version "
                f"before redeploying or a ghost connector may result "
                f"(or pass on_existing_version='bump_patch'/'allow')."
            )
        if not src.is_dir():
            raise ConnectorPackageError(
                f"{name} {version} is already installed and the source is a prebuilt "
                f"bundle that cannot be re-versioned; rebuild from the directory with "
                f"a higher version, or pass on_existing_version='allow'."
            )
        bumped_to = _bump_version(version)
        version = bumped_to

    tgz = package_connector(src, version=bumped_to) if src.is_dir() else src
    if not tgz.exists():
        raise FileNotFoundError(f"{tgz} does not exist")

    client.connectors.install_from_file(str(tgz), replace=replace, wait=True)
    wait_for_active(client, name, timeout)
    result: dict[str, Any] = {"installed": name, "bundle": str(tgz), "version": version}
    if bumped_to is not None:
        result["bumped"] = bumped_to

    if config is not None:
        cfg = client.connectors.upsert_configuration(name, config, name=config_name, default=True)
        result["configured"] = config_name
        result["config_id"] = getattr(cfg, "config_id", None) or (
            cfg.get("config_id") if isinstance(cfg, dict) else None
        )
        result["healthcheck"] = client.connectors.healthcheck(name)
    return result
