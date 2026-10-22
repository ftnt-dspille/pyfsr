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


def scaffold_connector(
    name: str,
    directory: str | Path | None = None,
    *,
    label: str | None = None,
    description: str = "A custom FortiSOAR connector.",
) -> list[Path]:
    """Write a minimal, installable ConnectorsV2 skeleton and return the paths.

    The skeleton has one configuration field (``server``), one operation
    (``ping``) and a health check, so it packages, installs, configures and runs
    end to end before any real logic is added. Refuses to overwrite: the target
    directory must not already contain any of the generated files.
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

    _write("info.json", json.dumps(_info_json(name, label, description), indent=2) + "\n")
    _write("connector.py", _CONNECTOR_PY.format(name=name, class_name=class_name))
    _write("operations.py", _OPERATIONS_PY.format(label=label))
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
        files = [rel for p in sorted(root.rglob("*")) if p.is_file()
                 and _ALLOW_UNTRACKED.match(rel := p.relative_to(root).as_posix())]
    return [f for f in files if not _EXCLUDE.search(f)]


def package_connector(directory: str | Path = ".", *, out_dir: str | Path | None = None) -> Path:
    """Build ``<out_dir>/<name>-<version>.tgz`` unpacking to one ``<name>/`` dir.

    Ports the ConnectorsV2 ``package.sh``: prefer git-tracked files, drop tests,
    packaging furniture and caches, and require the four core files. Returns the
    bundle path. ``out_dir`` defaults to ``<directory>/dist``.
    """
    root = Path(directory).resolve()
    info_path = root / "info.json"
    if not info_path.exists():
        raise FileNotFoundError(f"{root} has no info.json; is it a connector checkout?")
    info = json.loads(info_path.read_text())
    name, version = info["name"], info["version"]

    files = _bundle_files(root)
    missing = [f for f in REQUIRED_FILES if f not in files]
    if missing:
        raise FileNotFoundError(f"bundle is missing required file(s): {', '.join(missing)}")

    out = Path(out_dir) if out_dir is not None else root / "dist"
    out.mkdir(parents=True, exist_ok=True)
    tgz = out / f"{name}-{version}.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        for rel in files:
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


def deploy_connector(
    client,
    source: str | Path,
    *,
    config: dict[str, Any] | None = None,
    config_name: str = "Default",
    replace: bool = True,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Package (if ``source`` is a checkout), install, activate, configure, healthcheck.

    ``source`` is a connector directory (packaged first) or a built ``.tgz``.
    With ``config``, an idempotent named configuration is upserted and marked
    default, then healthchecked. Returns a summary dict of what happened.
    """
    src = Path(source)
    tgz = package_connector(src) if src.is_dir() else src
    if not tgz.exists():
        raise FileNotFoundError(f"{tgz} does not exist")

    # The bundle unpacks to <name>/, so the archive's leading dir is the name.
    with tarfile.open(tgz, "r:gz") as tar:
        first = tar.getnames()[0]
    name = first.split("/", 1)[0]

    client.connectors.install_from_file(str(tgz), replace=replace, wait=True)
    wait_for_active(client, name, timeout)
    result: dict[str, Any] = {"installed": name, "bundle": str(tgz)}

    if config is not None:
        cfg = client.connectors.upsert_configuration(name, config, name=config_name, default=True)
        result["configured"] = config_name
        result["config_id"] = getattr(cfg, "config_id", None) or (
            cfg.get("config_id") if isinstance(cfg, dict) else None
        )
        result["healthcheck"] = client.connectors.healthcheck(name)
    return result
