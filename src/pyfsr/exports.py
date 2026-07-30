"""Read and validate FortiSOAR Export Wizard bundles -- offline, no appliance needed.

A FortiSOAR ``.zip`` comes in two flavours that share one payload format:

* a **configuration export** -- ``info.json`` carries ``exported_from`` /
  ``exported_by`` and nothing that names the bundle. It installs through
  :meth:`~pyfsr.api.import_config.ImportConfigAPI.import_file`.
* a **solution pack** -- the same payload plus an *identity* (``name`` +
  ``version``, which is what makes a bundle installable by name, upgradable and
  uninstallable) and publishing metadata (``label``, ``publisher``,
  ``dependencies``, ``iconLarge``, ``postInstallConfig``, ...). It installs
  through :meth:`~pyfsr.api.solution_packs.SolutionPackAPI.install_from_file`.

Everything under the single ``export_<uuid>/`` root directory follows the same
rules in both: one directory per content category, a ``data.json`` manifest in
the categories that need installers, and payload JSON named after the record.

What this module checks is *structural* -- that the manifest and the files agree,
that every declared installer resolves, that category keys map to directories
that exist. It deliberately does **not** try to decide whether a field is valid
for some target, because that depends on the target's schema; for that, run the
export through :meth:`~pyfsr.api.import_config.ImportConfigAPI.dry_run`, which
lets the appliance itself analyse the bundle.

Example:
    >>> from pyfsr.exports import Export
    >>> arc = Export.open("myPack-1.0.0.zip")          # doctest: +SKIP
    >>> arc.kind                                        # doctest: +SKIP
    <ExportKind.SOLUTION_PACK: 'solutionpack'>
    >>> [f.code for f in arc.problems() if f.is_error]  # doctest: +SKIP
    []
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    "Export",
    "ExportKind",
    "ExportError",
    "ExportValidationError",
    "Finding",
    "Severity",
    "CATEGORY_DIRS",
]


class ExportError(Exception):
    """The file could not be read as a FortiSOAR export bundle at all."""


class ExportValidationError(Exception):
    """:meth:`Export.validate` found error-severity problems."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        detail = "\n".join(f"  {f}" for f in findings)
        super().__init__(f"{len(findings)} problem(s) in export:\n{detail}")


class ExportKind(str, Enum):
    """Which of the two bundle flavours an export is."""

    CONFIG_EXPORT = "export"
    SOLUTION_PACK = "solutionpack"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    """One problem found in an export.

    ``code`` is stable and machine-matchable; ``message`` is for humans.
    """

    severity: Severity
    code: str
    message: str
    path: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    def __str__(self) -> str:
        where = f" [{self.path}]" if self.path else ""
        return f"{self.severity.value.upper():7} {self.code:24} {self.message}{where}"


#: ``info.json`` ``contents`` keys are *not* the directory names. Observed across
#: shipped Fortinet packs and appliance-built exports on 8.0.0.
CATEGORY_DIRS: dict[str, str] = {
    "playbooks": "playbooks",
    # Install hooks. Both are ordinary playbook collections that the importer
    # runs around the content: preInstall first, postInstall last (the app's
    # CONFIG_IMPORT_ORDER). The importer reads them from a directory named
    # after the section key.
    "preInstall": "preInstall",
    "postInstall": "postInstall",
    "fixtures": "fixtures",
    "externalTemplates": "externalTemplates",
    "actors": "actors",
    "ruleChannels": "ruleChannels",
    "appSettings": "appSettings",
    "exportTemplates": "exportTemplates",
    "schedules": "schedules",
    "ai_agents": "ai_agents",
    "mcp_configurations": "mcp_configurations",
    "globalVariables": "playbooks",  # ships as playbooks/globalVariables.json
    "playbookBlocks": "playbookBlocks",
    "preprocessingRules": "preprocessingRules",
    "connectors": "connectors",
    "widgets": "widgets",
    "dashboards": "dashboards",
    "modules": "modules",
    "picklistNames": "picklists",
    "recordSets": "records",
    # A view *template* is not a view: it ships as the per-module layout files
    # under ``modules/<apiName>/<view>-layout.json``. Only ``views`` (navigation)
    # lives in ``views/``.
    "viewTemplates": "modules",
    "views": "views",
    "roles": "roles",
    "teams": "teams",
    "reports": "reports",
    "rules": "rules",
}

#: Directories that legitimately appear without a ``contents`` key of their own.
UNCLAIMED_DIRS_OK = {"images"}

#: Sections the importer reads with the playbook loader: a directory per
#: collection, each holding ``collection.metadata.json`` plus the playbook files.
PLAYBOOK_SECTIONS = ("playbooks", "preInstall", "postInstall")

#: The sidecar that makes a directory a *collection*. The loader only descends
#: into a folder that has one, so a collection missing it is skipped in silence.
COLLECTION_METADATA = "collection.metadata.json"

#: Categories whose ``data.json`` may declare an installer to unpack.
INSTALLER_CATEGORIES = {"connectors", "widgets"}

#: Connectors that ship with the platform. Content may call these freely without
#: declaring them, so they are never reported as an external requirement.
BUILTIN_CONNECTORS = {
    "cyops_utilities",
    "cyops-schedule-report",
    "code-snippet",
    "email-notification",
    "smtp-notification",
}

_VERSION_RE = re.compile(r"^\d+(\.\d+)*([.-][A-Za-z0-9]+)?$")


@dataclass(frozen=True)
class Installer:
    """An entry in a category ``data.json`` that carries or references a payload.

    ``install_mode == "tgz"`` means the tarball travels *inside* the export at
    ``<category>/<installer_path>``; ``"rpm"`` means the target fetches it from a
    repository, so nothing is bundled and nothing can be checked offline.
    """

    category: str
    name: str
    version: str | None
    install_mode: str | None
    installer_path: str | None
    member: str | None  # full in-zip path, when bundled and resolvable


@dataclass
class Export:
    """An opened FortiSOAR export bundle.

    Use :meth:`open`. The zip stays open for the object's lifetime; use it as a
    context manager, or call :meth:`close`, if you care about the handle.
    """

    path: Path
    zf: zipfile.ZipFile
    root: str
    info: dict[str, Any]
    members: list[str] = field(default_factory=list)

    # -- construction ----------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> Export:
        """Open ``path`` and parse its ``info.json``.

        Raises :class:`ExportError` if the file is not a zip, has no
        ``info.json``, or has more than one export root.
        """
        p = Path(path)
        try:
            zf = zipfile.ZipFile(p)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ExportError(f"{p}: not a readable zip ({exc})") from exc

        names = [n for n in zf.namelist() if not n.endswith("/")]
        infos = [n for n in names if posixpath.basename(n) == "info.json" and n.count("/") <= 1]
        if not infos:
            zf.close()
            raise ExportError(f"{p}: no info.json at the export root -- not a FortiSOAR export")
        if len(infos) > 1:
            zf.close()
            raise ExportError(f"{p}: {len(infos)} export roots ({', '.join(sorted(infos))}); expected exactly one")

        root = infos[0][: -len("info.json")]
        try:
            info = json.loads(zf.read(infos[0]))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            zf.close()
            raise ExportError(f"{p}: info.json is not valid JSON ({exc})") from exc
        if not isinstance(info, dict):
            zf.close()
            raise ExportError(f"{p}: info.json is a {type(info).__name__}, expected an object")

        return cls(path=p, zf=zf, root=root, info=info, members=[n[len(root) :] for n in names if n.startswith(root)])

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> Export:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- shape -----------------------------------------------------------

    @property
    def kind(self) -> ExportKind:
        """Solution pack (has an identity) or plain configuration export."""
        if self.info.get("type") == "solutionpack" or (self.info.get("name") and self.info.get("version")):
            return ExportKind.SOLUTION_PACK
        return ExportKind.CONFIG_EXPORT

    @property
    def name(self) -> str | None:
        return self.info.get("name")

    @property
    def version(self) -> str | None:
        return self.info.get("version")

    @property
    def label(self) -> str | None:
        return self.info.get("label")

    @property
    def min_compatibility(self) -> str | None:
        return self.info.get("fsrMinCompatibility")

    @property
    def contents(self) -> dict[str, Any]:
        """The raw ``contents`` map. Values are lists for most categories and
        dicts keyed by apiName for ``modules`` / ``viewTemplates`` / ``views``."""
        c = self.info.get("contents")
        return c if isinstance(c, dict) else {}

    def entries(self, category: str) -> list[Any]:
        """The entries of one ``contents`` category, list- or dict-shaped alike."""
        val = self.contents.get(category)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            return list(val.values())
        return []

    def directories(self) -> set[str]:
        """Top-level directories present under the export root."""
        return {m.split("/")[0] for m in self.members if "/" in m}

    def read_json(self, member: str) -> Any:
        """Read one member (path relative to the export root) as JSON."""
        return json.loads(self.zf.read(self.root + member))

    def data_manifest(self, category: str) -> list[dict[str, Any]]:
        """The ``<category>/data.json`` rows, or ``[]`` when there is no manifest."""
        member = f"{CATEGORY_DIRS.get(category, category)}/data.json"
        if member not in self.members:
            return []
        try:
            data = self.read_json(member)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            return []
        rows = data if isinstance(data, list) else [data]
        return [r for r in rows if isinstance(r, dict)]

    def installers(self) -> list[Installer]:
        """Every installer entry declared by a category ``data.json``."""
        out: list[Installer] = []
        for category in INSTALLER_CATEGORIES:
            directory = CATEGORY_DIRS.get(category, category)
            for row in self.data_manifest(category):
                ipath = row.get("installer_path")
                member = None
                if ipath:
                    candidate = f"{directory}/{ipath}"
                    if candidate in self.members:
                        member = candidate
                    elif ipath in self.members:
                        member = ipath
                out.append(
                    Installer(
                        category=category,
                        name=row.get("name") or row.get("apiName") or "<unnamed>",
                        version=row.get("version"),
                        install_mode=row.get("install_mode"),
                        installer_path=ipath,
                        member=member,
                    )
                )
        return out

    def playbook_members(self) -> Iterator[str]:
        """Every playbook payload file (skips the collection metadata sidecars)."""
        for m in self.members:
            if m.startswith("playbooks/") and m.endswith(".json") and not m.endswith("collection.metadata.json"):
                yield m

    # -- validation ------------------------------------------------------

    def problems(self, *, target_version: str | None = None) -> list[Finding]:
        """Every structural problem found, most severe first.

        ``target_version`` enables the ``fsrMinCompatibility`` check against the
        appliance you intend to import into.
        """
        f: list[Finding] = []
        f += self._check_identity()
        f += self._check_categories()
        f += self._check_installers()
        f += self._check_payload_json()
        f += self._check_references()
        if self.kind is ExportKind.SOLUTION_PACK:
            f += self._check_pack_extras()
        if target_version:
            f += self._check_compatibility(target_version)
        order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        return sorted(f, key=lambda x: (order[x.severity], x.code))

    def validate(self, *, target_version: str | None = None, strict: bool = False) -> list[Finding]:
        """Like :meth:`problems`, but raises on errors.

        Returns the non-fatal findings so a caller can log them. With
        ``strict=True`` warnings are fatal too.

        Raises:
            ExportValidationError: if any error (or, when strict, warning) was found.
        """
        found = self.problems(target_version=target_version)
        fatal = [x for x in found if x.is_error or (strict and x.severity is Severity.WARNING)]
        if fatal:
            raise ExportValidationError(fatal)
        return found

    # -- individual checks ------------------------------------------------

    def _check_identity(self) -> list[Finding]:
        out: list[Finding] = []
        if self.kind is ExportKind.CONFIG_EXPORT:
            out.append(
                Finding(
                    Severity.INFO,
                    "kind.config_export",
                    "no name/version, so this bundle has no identity: install it with "
                    "import_config.import_file(), not solution_packs.install_from_file()",
                )
            )
            return out

        out.append(
            Finding(
                Severity.INFO,
                "kind.solution_pack",
                f"solution pack {self.name} {self.version}: install with solution_packs.install_from_file()",
            )
        )
        if self.info.get("type") != "solutionpack":
            out.append(
                Finding(
                    Severity.ERROR,
                    "pack.type_missing",
                    f"has name/version but type is {self.info.get('type')!r}, not 'solutionpack' -- "
                    "the target will not register it as a pack",
                )
            )
        if not self.version or not _VERSION_RE.match(str(self.version)):
            out.append(
                Finding(Severity.ERROR, "pack.version_invalid", f"version {self.version!r} is not a version string")
            )
        certified = self.info.get("certified")
        if isinstance(certified, bool):
            out.append(
                Finding(
                    Severity.WARNING,
                    "pack.certified_type",
                    'certified is a JSON boolean; shipped packs use the string "true"/"false"',
                )
            )
        return out

    def _check_categories(self) -> list[Finding]:
        out: list[Finding] = []
        dirs = self.directories()
        claimed: set[str] = set()
        for category, val in self.contents.items():
            if not isinstance(val, (list, dict)):
                out.append(
                    Finding(
                        Severity.ERROR,
                        "contents.bad_shape",
                        f"contents.{category} is a {type(val).__name__}; expected a list or an object",
                    )
                )
                continue
            directory = CATEGORY_DIRS.get(category)
            if not self.entries(category):
                # Shipped packs do declare empty categories while still shipping the
                # directory (the category's data.json is the operative manifest), so
                # this is worth surfacing but is not a defect.
                if directory:
                    claimed.add(directory)
                out.append(
                    Finding(Severity.INFO, "contents.empty_category", f"contents.{category} is declared but empty")
                )
                continue
            if directory is None:
                out.append(
                    Finding(
                        Severity.INFO,
                        "contents.unknown_category",
                        f"contents.{category} is not a category this validator knows; its files were not checked",
                    )
                )
                continue
            claimed.add(directory)
            if directory not in dirs:
                out.append(
                    Finding(
                        Severity.ERROR,
                        "contents.dir_missing",
                        f"contents.{category} declares {len(self.entries(category))} item(s) "
                        f"but the export has no {directory}/ directory",
                    )
                )

        for d in sorted(dirs - claimed - UNCLAIMED_DIRS_OK):
            out.append(
                Finding(
                    Severity.WARNING,
                    "payload.unclaimed_dir",
                    f"{d}/ carries files that no contents category claims; the import will ignore them",
                    path=d,
                )
            )
        return out

    def _check_installers(self) -> list[Finding]:
        out: list[Finding] = []
        for inst in self.installers():
            label = f"{inst.category[:-1]} {inst.name}"
            if inst.install_mode is None:
                out.append(
                    Finding(
                        Severity.ERROR,
                        "installer.mode_missing",
                        f"{label} declares no install_mode, so the target can neither fetch nor unpack it "
                        "(set includeInstall on the export template to bundle a tgz)",
                        path=f"{CATEGORY_DIRS.get(inst.category, inst.category)}/data.json",
                    )
                )
                continue
            if inst.install_mode == "rpm":
                if inst.installer_path:
                    out.append(
                        Finding(
                            Severity.WARNING,
                            "installer.rpm_with_path",
                            f"{label} is install_mode=rpm but also names installer_path {inst.installer_path!r}",
                        )
                    )
                continue
            if inst.install_mode != "tgz":
                out.append(
                    Finding(
                        Severity.WARNING,
                        "installer.mode_unknown",
                        f"{label} has install_mode={inst.install_mode!r}; only 'rpm' and 'tgz' are known",
                    )
                )
                continue
            if not inst.installer_path:
                out.append(
                    Finding(
                        Severity.ERROR,
                        "installer.path_missing",
                        f"{label} is install_mode=tgz but names no installer_path",
                    )
                )
                continue
            if not inst.member:
                out.append(
                    Finding(
                        Severity.ERROR,
                        "installer.file_missing",
                        f"{label} declares installer {inst.installer_path!r} but the export does not contain it",
                        path=f"{CATEGORY_DIRS.get(inst.category, inst.category)}/{inst.installer_path}",
                    )
                )
                continue
            out += self._check_tarball(inst)
        return out

    def _check_tarball(self, inst: Installer) -> list[Finding]:
        """Open a bundled tgz and cross-check its own manifest against ours."""
        assert inst.member
        try:
            raw = self.zf.read(self.root + inst.member)
            tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
        except (tarfile.TarError, OSError, KeyError) as exc:
            return [
                Finding(
                    Severity.ERROR,
                    "installer.unreadable",
                    f"{inst.name}: bundled installer is not a readable gzip tarball ({exc})",
                    path=inst.member,
                )
            ]
        with tf:
            manifest = next((m for m in tf.getnames() if posixpath.basename(m) == "info.json"), None)
            if manifest is None:
                return [
                    Finding(
                        Severity.WARNING,
                        "installer.no_manifest",
                        f"{inst.name}: bundled installer has no info.json to cross-check",
                        path=inst.member,
                    )
                ]
            try:
                handle = tf.extractfile(manifest)
                inner = json.loads(handle.read()) if handle else {}
            except (json.JSONDecodeError, tarfile.TarError, UnicodeDecodeError) as exc:
                return [
                    Finding(
                        Severity.ERROR,
                        "installer.bad_manifest",
                        f"{inst.name}: bundled installer's info.json is unreadable ({exc})",
                        path=inst.member,
                    )
                ]
        out: list[Finding] = []
        inner_version = str(inner.get("version") or "")
        if inst.version and inner_version and inner_version != str(inst.version):
            out.append(
                Finding(
                    Severity.ERROR,
                    "installer.version_mismatch",
                    f"{inst.name}: data.json says version {inst.version} but the bundled tarball is {inner_version}",
                    path=inst.member,
                )
            )
        inner_name = inner.get("name")
        if inner_name and inst.category == "connectors":
            declared = {e.get("apiName") for e in self.entries("connectors") if isinstance(e, dict)}
            if declared and inner_name not in declared:
                out.append(
                    Finding(
                        Severity.ERROR,
                        "installer.name_mismatch",
                        f"bundled installer contains connector {inner_name!r}, which contents.connectors "
                        "does not declare -- the wrong product was packaged",
                        path=inst.member,
                    )
                )
        return out

    def _check_payload_json(self) -> list[Finding]:
        out: list[Finding] = []
        for m in self.members:
            if not m.endswith(".json"):
                continue
            try:
                self.read_json(m)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                out.append(Finding(Severity.ERROR, "payload.bad_json", f"{m} is not valid JSON ({exc})", path=m))
        return out

    def _check_references(self) -> list[Finding]:
        """Cross-check what the payload actually uses against what is declared."""
        out: list[Finding] = []
        declared_collections = {
            e.get("name") for e in self.entries("playbooks") if isinstance(e, dict) and e.get("name")
        }
        present_collections = {m.split("/")[1] for m in self.members if m.startswith("playbooks/") and "/" in m[10:]}
        for missing in sorted(declared_collections - present_collections):
            out.append(
                Finding(
                    Severity.ERROR,
                    "playbooks.collection_missing",
                    f"contents.playbooks declares collection {missing!r} but no such directory is in the export",
                )
            )
        for extra in sorted(present_collections - declared_collections):
            out.append(
                Finding(
                    Severity.WARNING,
                    "playbooks.collection_undeclared",
                    f"playbooks/{extra}/ is in the export but contents.playbooks does not declare it",
                    path=f"playbooks/{extra}",
                )
            )

        # The playbook loader only descends into a folder that has the metadata
        # sidecar. Without it the whole collection is skipped without a word --
        # the import "succeeds" and the content simply is not there.
        for section in PLAYBOOK_SECTIONS:
            directory = CATEGORY_DIRS[section]
            folders: set[str] = set()
            for m in self.members:
                parts = m.split("/")
                if len(parts) == 3 and parts[0] == directory:
                    folders.add(parts[1])
            for folder in sorted(folders):
                if f"{directory}/{folder}/{COLLECTION_METADATA}" not in self.members:
                    out.append(
                        Finding(
                            Severity.ERROR,
                            "collection.metadata_missing",
                            f"{directory}/{folder}/ has no {COLLECTION_METADATA}, so the importer "
                            "will skip the entire collection without reporting anything",
                            path=f"{directory}/{folder}",
                        )
                    )

        # A view template entry names its module and which layouts it carries;
        # each one ships as modules/<apiName>/<view>-layout.json.
        for entry in self.entries("viewTemplates"):
            if not isinstance(entry, dict) or not entry.get("apiName"):
                continue
            api = entry["apiName"]
            for view in entry.get("views") or []:
                # detail/form/list ship as "<view>-layout.json"; settings is the
                # exception and ships as plain "settings.json".
                leaf = "settings.json" if view == "settings" else f"{view}-layout.json"
                member = f"modules/{api}/{leaf}"
                if member not in self.members:
                    out.append(
                        Finding(
                            Severity.ERROR,
                            "viewTemplates.layout_missing",
                            f"contents.viewTemplates says {api} ships a {view!r} layout, "
                            f"but {member} is not in the export",
                            path=member,
                        )
                    )

        for missing in sorted(self.external_connectors()):
            out.append(
                Finding(
                    Severity.INFO,
                    "connectors.external_requirement",
                    f"playbook steps call connector {missing!r}, which the export neither ships nor declares; "
                    "the target must already have it installed and configured",
                )
            )
        return out

    def external_connectors(self) -> set[str]:
        """Connectors the content calls that the export does not account for.

        Not a defect -- shipped Fortinet packs rely on the target already having
        these -- but it *is* the install prerequisite list, and nothing else
        computes it. Platform builtins and Jinja-templated (dynamically
        dispatched) connector names are excluded.
        """
        declared = {e.get("apiName") for e in self.entries("connectors") if isinstance(e, dict) and e.get("apiName")}
        return {c for c in self._connectors_used() if c not in declared and c not in BUILTIN_CONNECTORS}

    def _connectors_used(self) -> set[str]:
        """Every connector apiName referenced by a playbook step in the export."""
        used: set[str] = set()
        for m in self.playbook_members():
            try:
                doc = self.read_json(m)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            used |= _walk_for_connectors(doc)
        return used

    def _check_pack_extras(self) -> list[Finding]:
        """The parts a pack has that a plain export does not."""
        out: list[Finding] = []
        icon = self.info.get("iconLarge")
        if icon and not any(m.endswith(f"/{icon}") or m == icon for m in self.members):
            out.append(
                Finding(
                    Severity.ERROR,
                    "pack.icon_missing",
                    f"iconLarge names {icon!r} but no such file is in the export",
                )
            )

        out += self._check_post_install()

        deps = self.info.get("dependencies")
        if isinstance(deps, list):
            for d in deps:
                if not isinstance(d, dict) or not d.get("name"):
                    out.append(
                        Finding(Severity.ERROR, "pack.dependency_malformed", f"dependency entry {d!r} has no name")
                    )
        return out

    def _check_post_install(self) -> list[Finding]:
        """The post-install configuration wizard hook.

        The marketplace UI renders the "Configure" button only when
        ``widgets[0].name`` **and** ``widgets[0].version`` are both set, reads
        ``buttonLabel`` for its caption, and auto-opens the widget when
        ``autoLaunch`` is set. It only ever looks at ``widgets[0]``.
        """
        out: list[Finding] = []
        post = self.info.get("postInstallConfig")
        if not isinstance(post, dict) or not post.get("enabled"):
            return out

        widgets = post.get("widgets")
        if not isinstance(widgets, list) or not widgets:
            return [
                Finding(
                    Severity.ERROR,
                    "pack.postinstall_no_widget",
                    "postInstallConfig is enabled but names no widget, so no post-install button appears",
                )
            ]
        if len(widgets) > 1:
            out.append(
                Finding(
                    Severity.WARNING,
                    "pack.postinstall_extra_widgets",
                    f"postInstallConfig lists {len(widgets)} widgets but only the first is ever used",
                )
            )

        declared = {e.get("apiName") or e.get("name") for e in self.entries("widgets") if isinstance(e, dict)}
        for w in widgets:
            if not isinstance(w, dict):
                out.append(
                    Finding(Severity.ERROR, "pack.postinstall_malformed", f"widget entry {w!r} is not an object")
                )
                continue
            wname = w.get("name")
            if not wname:
                out.append(Finding(Severity.ERROR, "pack.postinstall_widget_unnamed", "widget entry has no name"))
                continue
            if not w.get("version"):
                out.append(
                    Finding(
                        Severity.ERROR,
                        "pack.postinstall_widget_unversioned",
                        f"postInstall widget {wname!r} has no version; the UI requires name *and* version "
                        "before it will show the post-install button",
                    )
                )
            # Every pack in the corpus that enables this ships its own wizard widget.
            if wname not in declared:
                out.append(
                    Finding(
                        Severity.ERROR,
                        "pack.postinstall_widget_missing",
                        f"postInstallConfig runs widget {wname!r} but the pack does not ship it",
                    )
                )
            if w.get("autoLaunchTriggered"):
                out.append(
                    Finding(
                        Severity.WARNING,
                        "pack.postinstall_state_leaked",
                        f"widget {wname!r} carries autoLaunchTriggered=true -- that is per-appliance runtime "
                        "state, and shipping it means the wizard never auto-opens on the target",
                    )
                )
        return out

    def _check_compatibility(self, target_version: str) -> list[Finding]:
        want = self.min_compatibility
        if not want:
            return [Finding(Severity.WARNING, "compat.unspecified", "no fsrMinCompatibility; cannot check the target")]
        if _version_tuple(target_version) < _version_tuple(want):
            return [
                Finding(
                    Severity.ERROR,
                    "compat.target_too_old",
                    f"export needs FortiSOAR >= {want} but the target is {target_version}",
                )
            ]
        return []


def _version_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(v).split("."):
        m = re.match(r"\d+", chunk)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def _walk_for_connectors(node: Any) -> set[str]:
    """Collect connector apiNames from a playbook document.

    A connector step keeps its target in ``step.arguments.connector`` (older
    content) or in the step's ``connector``/``name`` pairing; both are just keys
    somewhere in a deep structure, so walk rather than assume a shape.
    """
    found: set[str] = set()
    if isinstance(node, dict):
        for key in ("connector", "connector_name", "connectorName"):
            val = node.get(key)
            # A Jinja-templated name is chosen at runtime -- there is no static answer.
            if isinstance(val, str) and val and "{{" not in val:
                found.add(val)
        for val in node.values():
            found |= _walk_for_connectors(val)
    elif isinstance(node, list):
        for val in node:
            found |= _walk_for_connectors(val)
    return found
