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
import re
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
    out.parent.mkdir(parents=True, exist_ok=True)

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


def _dotless(version: str) -> int | None:
    """The appliance's version number: dots dropped, build suffix ignored (``"8.0.1"`` -> 801)."""
    core = str(version).strip().split("-", 1)[0]
    digits = core.replace(".", "")
    return int(digits) if digits.isdigit() else None


def widget_compatibility_error(compatibility: list[str] | str | None, platform_version: str) -> str | None:
    """Why the box would refuse a widget with this ``metadata.compatibility``, or ``None``.

    Mirrors ``WidgetController::checkCompatibilityVersion`` (live-read on
    8.0.0-6034): take the **highest** listed version, drop the dots, and refuse
    when it is below 702 or above the box's own version. A list is therefore
    *not* "any of these": ``["8.0.0", "8.0.1"]`` is refused by an 8.0.0 box.
    An unparseable platform version (e.g. a ``dev`` build) or an empty list is
    not judged.
    """
    platform = _dotless(platform_version)
    if platform is None:
        return None
    listed = [compatibility] if isinstance(compatibility, str) else list(compatibility or [])
    numbers = [n for n in (_dotless(v) for v in listed) if n is not None]
    if not numbers:
        return None  # nothing to judge; leave it to the box
    top = max(numbers)
    if top < 702:
        return f"metadata.compatibility {listed!r}: highest version is below 7.0.2; the box refuses it"
    if top > platform:
        newest = listed[numbers.index(top)] if len(numbers) == len(listed) else str(top)
        return (
            f"metadata.compatibility {listed!r} lists {newest}, newer than the box's {platform_version}; "
            "the box refuses a widget whose HIGHEST listed version is above its own "
            f"(list only versions <= {platform_version.split('-', 1)[0]})"
        )
    return None


def widget_info_from_tgz(tgz: str | Path) -> dict:
    """The ``info.json`` of a widget ``.tgz`` (single ``<name>-<version>/`` root)."""
    with tarfile.open(tgz, "r:gz") as tf:
        hits = [m for m in tf.getmembers() if m.isfile() and m.name.count("/") == 1 and m.name.endswith("/info.json")]
        if len(hits) != 1:
            raise ValueError(f"{Path(tgz).name}: expected exactly one <root>/info.json, found {len(hits)}")
        fh = tf.extractfile(hits[0])
        assert fh is not None
        return json.loads(fh.read())


# --------------------------------------------------------------------------
# Version bumping. A widget's version is baked into more than info.json: the
# controller registrations are ``<name><dotless-version>[Dev]Ctrl`` and
# ``edit<Name><dotless-version>[Dev]Ctrl`` (the platform derives the expected
# name from info.json at install time, so a mismatch mounts nothing, with no
# error), and asset paths / bare widget ids carry ``<name>-X.Y.Z``. These rules
# mirror the widget harness's ``rewriteForVersion`` (packager.js).
# --------------------------------------------------------------------------

_TEXT_SUFFIXES = (".js", ".html", ".htm", ".css", ".json", ".ts")


def _variations(name: str) -> list[str]:
    out: list[str] = []
    for v in (name, name[:1].upper() + name[1:], name[:1].lower() + name[1:]):
        if v and v not in out:
            out.append(v)
    return out


def rewrite_widget_text(text: str, name: str, version: str, *, dev: bool | None = None) -> str:
    """Point every version-bearing reference in ``text`` at ``version``.

    - ``<name>-X.Y.Z`` followed by ``/`` or a non-version character (asset
      paths and bare widget ids) becomes ``<name>-<version>``;
    - ``edit<Name><digits>[Dev]Ctrl`` and ``<name><digits>[Dev]Ctrl`` (every
      case form of the name) get the new dotless version. The ``Dev`` suffix
      is kept as found unless ``dev`` forces it on or off;
    - a bare ``<name>Ctrl`` / ``edit<Name>Ctrl`` gets the version inserted.

    Idempotent: running it twice with the same version changes nothing.
    """
    num = _dotless_str(version)
    # `<name>-X.Y[.Z]` with no suffix group: the harness's rewrite regex had an
    # optional `[-.][A-Za-z0-9.]+` tail that also swallowed `.tgz`/`.min`, so
    # `<name>-1.0.0.tgz` lost its extension. Stop at the last numeric segment.
    path_re = re.compile(rf"\b{re.escape(name)}-\d+(?:\.\d+)+(?![\d])")
    text = path_re.sub(f"{name}-{version}", text)

    def suffix(found: str | None) -> str:
        if dev is None:
            return found or ""
        return "Dev" if dev else ""

    for w in _variations(name):
        esc = re.escape(w)
        text = re.sub(rf"edit{esc}\d+(Dev)?Ctrl", lambda m, w=w: f"edit{w}{num}{suffix(m.group(1))}Ctrl", text)
        text = re.sub(rf"(?<!edit){esc}\d+(Dev)?Ctrl", lambda m, w=w: f"{w}{num}{suffix(m.group(1))}Ctrl", text)
        text = re.sub(rf"\bedit{esc}Ctrl\b", lambda m, w=w: f"edit{w}{num}{suffix(None)}Ctrl", text)
        text = re.sub(rf"(?<!edit)\b{esc}Ctrl\b", lambda m, w=w: f"{w}{num}{suffix(None)}Ctrl", text)
    return text


def _dotless_str(version: str) -> str:
    return str(version).split("-", 1)[0].replace(".", "")


def rewrite_widget_version(widget_dir: str | Path, version: str) -> list[Path]:
    """Re-version a widget folder in place: info.json plus every text reference.

    Returns the files that changed. The folder itself is not renamed; the
    ``<name>-<version>`` archive root comes from :func:`pack_widget`.
    """
    root = Path(widget_dir)
    info_path = root / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    name = info["name"]
    changed: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name == "info.json" or not p.name.endswith(_TEXT_SUFFIXES):
            continue
        if any(part in _EXCLUDE_DIRS for part in p.relative_to(root).parts) or p.name.startswith("._"):
            continue
        try:
            before = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        after = rewrite_widget_text(before, name, version)
        if after != before:
            p.write_text(after, encoding="utf-8")
            changed.append(p)
    info["version"] = version
    info_path.write_text(json.dumps(info, indent=4) + "\n", encoding="utf-8")
    changed.append(info_path)
    return changed


def rebundle_widget_with_version(tgz: str | Path, version: str, out_dir: str | Path | None = None) -> str:
    """A copy of a widget ``.tgz`` re-versioned to ``version`` (source untouched).

    Extracts, runs :func:`rewrite_widget_version`, and re-packs with
    :func:`pack_widget`, so the archive root becomes ``<name>-<version>/``.
    """
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="pyfsr-widget-bump-"))
    with tarfile.open(tgz, "r:gz") as tf:
        tf.extractall(work, filter="data")
    roots = [p for p in work.iterdir() if p.is_dir() and (p / "info.json").exists()]
    if len(roots) != 1:
        raise ValueError(f"{Path(tgz).name}: expected one <name>-<version>/ root with info.json, found {len(roots)}")
    rewrite_widget_version(roots[0], version)
    problems = widget_controller_problems(roots[0])
    if problems:
        raise ValueError(f"{Path(tgz).name} re-versioned to {version} but: " + "; ".join(problems))
    dest = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="pyfsr-widget-out-"))
    return pack_widget(str(roots[0]), str(dest))


def next_free_version(current: str, taken: list[str] | set[str], *, must_exceed: list[str] | None = None) -> str:
    """Patch-bump ``current`` until it is free on the box.

    Free means: not an existing version, no existing version with the same
    dotless number (``1.0.10`` and ``10.1.0`` both register ``...1010Ctrl``), and,
    when ``must_exceed`` is given, a dotless number above every one of those
    (the connector importer only installs over an older version, compared as
    dotless integers, so ``1.0.10`` beats ``1.1.0``).
    """
    taken_set = {str(t) for t in taken}
    taken_num = {_dotless(t) for t in taken_set} - {None}
    floor = max((n for n in (_dotless(v) for v in (must_exceed or [])) if n is not None), default=None)

    def semver(v: str) -> list[int]:
        out = [int(x) if x.isdigit() else 0 for x in str(v).split("-", 1)[0].split(".")]
        return (out + [0, 0, 0])[:3]

    # Start from the highest version anyone holds, so a pack at 1.0.0 against
    # an installed 1.1.0 lands on 1.1.1, not 1.0.11.
    parts = max([semver(current)] + [semver(v) for v in taken_set | set(must_exceed or [])])
    for _ in range(10_000):
        parts[2] += 1
        cand = ".".join(map(str, parts[:3]))
        n = _dotless(cand)
        if cand in taken_set or n in taken_num:
            continue
        if floor is not None and (n is None or n <= floor):
            continue
        return cand
    raise ValueError(f"no free version found above {current}")


def widget_controller_problems(widget_dir: str | Path) -> list[str]:
    """Controller registrations that do not match info.json's version.

    The platform resolves ``<name><dotless>[Dev]Ctrl`` / ``edit<Name><dotless>[Dev]Ctrl``
    from info.json; a registration with other digits mounts nothing, silently.
    Mirrors the widget harness's ``validateControllers``. Empty list = consistent.
    """
    root = Path(widget_dir)
    info = json.loads((root / "info.json").read_text(encoding="utf-8"))
    name, version = str(info.get("name") or ""), str(info.get("version") or "")
    if not name or not version:
        return ["info.json has no name/version"]
    digits = _dotless_str(version)
    alt = "|".join(re.escape(v) for v in _variations(name))
    pats = {
        "view.controller.js": re.compile(rf"^(?:{alt})(\d+)(?:Dev)?Ctrl$"),
        "edit.controller.js": re.compile(rf"^edit(?:{alt})(\d+)(?:Dev)?Ctrl$"),
    }
    out: list[str] = []
    for fname, pat in pats.items():
        p = root / fname
        if not p.exists():
            out.append(f"{fname} missing")
            continue
        regs = re.findall(r"\.controller\(\s*['\"]([^'\"]+)['\"]", p.read_text(encoding="utf-8"))
        ours = [(r, pat.match(r)) for r in regs]
        matched = [(r, m) for r, m in ours if m]
        if not matched:
            out.append(f"{fname} registers no controller shaped like {pat.pattern} (found {regs})")
        for r, m in matched:
            if m.group(1) != digits:
                out.append(f"{fname} registers {r}, digits {m.group(1)} != {digits} from version {version}")
    return out


# --------------------------------------------------------------------------- #
# Dependency discovery (dashboard -> widgets -> nested widgets / connectors)   #
# --------------------------------------------------------------------------- #

_WIDGET_TYPE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)-(\d+(?:\.\d+)+)$")
# A widget's code that finds another installed widget by name, e.g.
# ``widgets.find(w => w.name === 'killchainphases')``.
_NESTED_NAME_RE = re.compile(r"""\.name\s*===?\s*['"]([A-Za-z][A-Za-z0-9_]*)['"]""")
# A widget referenced by its full type string, e.g. ``type: 'recordSummary-2.1.0'``.
_NESTED_TYPE_RE = re.compile(r"""['"]([A-Za-z][A-Za-z0-9_]*)-(\d+\.\d+\.\d+)['"]""")
# The connector passed first to an execute helper, e.g.
# ``executeAction('fortinet-fortiguard-outbreak', ...)``.
_CONNECTOR_CALL_RE = re.compile(r"""execute\w*\(\s*['"]([a-z0-9][a-z0-9_-]*)['"]""", re.IGNORECASE)


def dashboard_widget_refs(dashboard: dict | list) -> set[tuple[str, str]]:
    """``(name, version)`` of every widget a dashboard record places.

    Walks the whole record (rows, tabs, nested layouts) for ``type`` values
    shaped ``<name>-X.Y.Z``.

    Example:
        >>> sorted(dashboard_widget_refs({"config": {"rows": [{"columns": [
        ...     {"widgets": [{"type": "fortiGuardThreatLandscape-1.0.0"}]},
        ...     {"widgets": [{"type": "chart-3.2.1"}, {"type": "not a widget"}]}]}]}}))
        [('chart', '3.2.1'), ('fortiGuardThreatLandscape', '1.0.0')]
    """
    found: set[tuple[str, str]] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            t = node.get("type")
            if isinstance(t, str):
                m = _WIDGET_TYPE_RE.match(t)
                if m:
                    found.add((m.group(1), m.group(2)))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(dashboard)
    return found


def _widget_code_texts(tgz: str | Path):
    import tarfile

    with tarfile.open(tgz, mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile() and m.name.endswith((".js", ".html")):
                f = tar.extractfile(m)
                if f is not None:
                    yield f.read().decode("utf-8", errors="replace")


def widget_code_refs(tgz: str | Path) -> dict[str, set]:
    """Candidate dependencies named in a widget archive's ``.js``/``.html``.

    Returns ``{"widgets": {name, ...}, "widget_types": {(name, version), ...},
    "connectors": {name, ...}}``. These are *candidates* read from source text:
    check them against the box's installed widgets / connectors before
    trusting them (see :meth:`pyfsr.api.widgets.WidgetsAPI.export_dashboard`).
    The widget's own name is left out.
    """
    own = widget_info_from_tgz(tgz).get("name")
    widgets: set[str] = set()
    types: set[tuple[str, str]] = set()
    connectors: set[str] = set()
    for text in _widget_code_texts(tgz):
        widgets.update(_NESTED_NAME_RE.findall(text))
        types.update(_NESTED_TYPE_RE.findall(text))
        connectors.update(_CONNECTOR_CALL_RE.findall(text))
    widgets.discard(own)
    types = {t for t in types if t[0] != own}
    return {"widgets": widgets, "widget_types": types, "connectors": connectors}


def set_widget_compatibility(tgz: str | Path, compatibility: list[str]) -> bool:
    """Rewrite ``metadata.compatibility`` inside a widget ``.tgz``, in place.

    Returns ``False`` (archive untouched) when it already holds exactly
    ``compatibility``. The appliance refuses a widget whose highest entry is
    above its own version, so lowering the tag (e.g. to ``["7.6.0"]``) lets an
    8.0-tagged widget install on 7.6; it does not make 8.0-only code work there.
    """
    import tempfile

    tgz = Path(tgz)
    info = widget_info_from_tgz(tgz)
    if (info.get("metadata") or {}).get("compatibility") == list(compatibility):
        return False
    work = Path(tempfile.mkdtemp(prefix="pyfsr-widget-compat-"))
    with tarfile.open(tgz, "r:gz") as tf:
        tf.extractall(work, filter="data")
    roots = [p for p in work.iterdir() if p.is_dir() and (p / "info.json").exists()]
    if len(roots) != 1:
        raise ValueError(f"{tgz.name}: expected one <name>-<version>/ root with info.json, found {len(roots)}")
    info_path = roots[0] / "info.json"
    data = json.loads(info_path.read_text())
    data.setdefault("metadata", {})["compatibility"] = list(compatibility)
    info_path.write_text(json.dumps(data, indent=4) + "\n")
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(roots[0], arcname=roots[0].name)
    return True
