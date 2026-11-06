"""Widget packaging -- build a deployable ``.tgz`` from a widget folder.

The connector flow has :mod:`pyfsr.connector_dev`; widgets get the same
treatment here. :func:`pack_widget` validates a widget source folder and
bundles it exactly the way the Content Hub ships widgets: a single top-level
``<name>-<version>/`` directory (taken from the folder's ``info.json``, not
the folder's own name) holding the widget files.

Pitfalls this packing rules out:

* **macOS AppleDouble entries** (``._*``, ``__MACOSX/``): the widget importer
  walks the extracted tree looking for ``info.json``; a stray
  ``._<dir>/info.json`` miss makes it resolve the wrong root and the upload
  fails with a 500 whose ``prod.log`` reads ``max(): Argument #1 ($value)
  must contain at least one element`` (live-verified on 8.0.0).
* **Junk** (``.DS_Store``, caches, VCS/IDE folders, ``node_modules``) that
  bloats the bundle and can confuse the importer's file walk.
* **Non-numeric owners**: entries are normalized to uid/gid 0 with empty
  owner names, matching the box's own packages.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

#: Files the importer needs for the widget to render and configure. A bundle
#: missing any of these uploads but the widget is dead on arrival.
REQUIRED_WIDGET_FILES = (
    "info.json",
    "view.html",
    "edit.html",
    "view.controller.js",
    "edit.controller.js",
)

#: Directories that never belong in a widget bundle (build / VCS / IDE junk).
_EXCLUDE_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "node_modules",
        "dist",
        "build",
        "__MACOSX",
    }
)

#: Files / suffixes that never belong in a widget bundle. ``._*`` are macOS
#: AppleDouble resource files (see module docstring).
_EXCLUDE_FILES = frozenset({".DS_Store"})
_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".swp")


def pack_widget(source_dir: str, output: str | None = None) -> str:
    """Bundle a widget source folder into a SOAR-importable ``.tgz``.

    The archive contains exactly one top-level directory named
    ``<name>-<version>`` (from the folder's ``info.json``), e.g.
    ``fortiGuardThreatMap-1.0.0/info.json`` -- the layout every Content Hub
    widget archive uses and the layout the importer's file walk expects.

    Args:
        source_dir: path to the widget folder (the one containing ``info.json``).
        output: destination ``.tgz`` path, or a directory to write
            ``<name>-<version>.tgz`` into. Defaults to ``<name>-<version>.tgz``
            next to the source folder.

    Returns:
        The path to the written ``.tgz``.

    Raises:
        FileNotFoundError: if ``source_dir`` doesn't exist.
        ValueError: if ``source_dir`` has no/invalid ``info.json``, or is
            missing one of the required widget files.
    """
    src = Path(source_dir).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"widget source folder not found: {src}")
    info_path = src / "info.json"
    if not info_path.exists():
        raise ValueError(f"{src} has no info.json -- not a widget source folder")
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{info_path} is not valid JSON: {exc}") from exc
    name = info.get("name")
    version = info.get("version")
    if not name or not version:
        raise ValueError(f"{info_path} must define non-empty 'name' and 'version'")

    missing = [f for f in REQUIRED_WIDGET_FILES if not (src / f).exists()]
    if missing:
        raise ValueError(f"{src} is missing required widget file(s): {', '.join(missing)}")

    root = f"{name}-{version}"
    out = Path(output) if output else src.parent / f"{root}.tgz"
    if out.is_dir() or (not out.suffix and not out.exists()):
        out = out / f"{root}.tgz"

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        if any(p in _EXCLUDE_DIRS for p in parts):
            return None
        name_part = parts[-1]
        if name_part in _EXCLUDE_FILES or name_part.startswith("._"):
            return None
        if name_part.endswith(_EXCLUDE_SUFFIXES):
            return None
        # Numeric owner only, like the box's own packages.
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        return info

    with tarfile.open(out, "w:gz") as tar:
        # arcname from info.json so the archive's layout does not depend on
        # what the developer happened to call their working folder.
        tar.add(src, arcname=root, filter=_filter)
    return str(out)
