"""Build and validate a self-hosted Content Hub catalog (``content-hub.json``).

FortiSOAR's Content Hub sync (``csadm package content-hub sync``) reads **one**
manifest from **one** host: ``GET https://{REPOSERVER}/content-hub/content-hub.json``.
That file is a flat JSON array of entries (connectors, widgets, solution packs,
ai_agents). There is no native multi-repo — so "see both Fortinet's store and
ours" is produced by serving a *merged* ``content-hub.json`` from our own host
(``OFFLINEREPO=true``, ``REPOSERVER`` pointed at our VM).

This module is the no-appliance, no-FDN-cert piece pyfsr owns (Option C in
``docs/plans/CONTENT_HUB_SELF_HOSTED_REPO_PLAN.md``): take an upstream catalog,
splice in our own local entries, validate every entry against the live schema,
and emit a spec-valid ``content-hub.json`` plus the directory tree the sync's
fetch contract expects.

The fetch contract a served mirror must satisfy (all live-verified on 8.0.0)::

    /content-hub/content-hub.json                                  # this manifest
    /content-hub/{name}-{version}/{buildNumber}/info.json          # per-item detail
    /content-hub/{name}-{version}/latest/info.json                 # + a "latest" copy
    /content-hub/{name}-{version}/{buildNumber}/{name}-{version}.zip   # artifact
    /content-hub/{name}-{version}/{buildNumber}/images/fsr-icon-large.png

Typical use::

    from pyfsr.content_catalog import ContentCatalog, build_entry

    cat = ContentCatalog.from_url("my-mirror.example.com")        # crawl a live mirror
    # ...or from a saved file: ContentCatalog.from_file("upstream-content-hub.json")
    cat.add(build_entry(
        name="myConnector", type="connector", version="1.0.0", buildNumber=1,
        label="My Connector", description="in-house", publisher="Acme",
    ))
    problems = cat.validate()          # {} when clean
    cat.write_tree("/srv/repo")        # -> /srv/repo/content-hub/content-hub.json + item dirs
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import zipfile
from collections.abc import Iterable
from typing import Any

import requests

from .exceptions import RepoArtifactNotFoundError, RepoUnreachableError

#: ``name``/``version`` become filesystem path segments in the served tree
#: (``{name}-{version}/{build}/…``), so they must not contain path separators or
#: ``..``. Every live-catalog value matches this (e.g. ``abuseipdb`` / ``2.0.0``).
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Path a served mirror exposes the manifest at, relative to the repo host.
_CATALOG_PATH = "/content-hub/content-hub.json"
_HEADERS = {"User-Agent": "pyfsr"}

#: The four entry types the live 8.0.0 catalog carries.
CATALOG_TYPES = frozenset({"connector", "widget", "solutionpack", "ai_agent"})

#: Keys every entry carries, regardless of type (from the live 931-entry catalog).
_COMMON_KEYS = (
    "name",
    "type",
    "version",
    "label",
    "description",
    "buildNumber",
    "availableVersions",
    "infoPath",
    "iconLarge",
    "category",
    "publisher",
    "certified",
)

#: The minimal set a well-formed entry MUST have for the sync to place it and
#: for the artifact/info/icon paths to resolve.
_REQUIRED_KEYS = ("name", "type", "version", "buildNumber", "label")

#: Keys the sync uses to locate artifacts; if absent we can synthesize them from
#: name/version/buildNumber via the path convention.
_PATH_KEYS = ("infoPath", "iconLarge")


def info_path(name: str, version: str, build_number: int | str) -> str:
    """Return the ``infoPath`` for an item, per the live path convention.

    ``/content-hub/{name}-{version}/{buildNumber}`` — ``buildNumber`` is the real
    integer build, not ``"latest"`` (both the numbered dir and a ``latest/`` copy
    exist on the wire).
    """
    return f"/content-hub/{name}-{version}/{build_number}"


def icon_path(name: str, version: str, build_number: int | str) -> str:
    """Return the ``iconLarge`` path for an item (served from ``OSSERVER``)."""
    return f"/content-hub/{name}-{version}/{build_number}/images/fsr-icon-large.png"


def artifact_path(name: str, version: str, build_number: int | str) -> str:
    """Return the artifact-zip path the sync fetches for an item."""
    return f"/content-hub/{name}-{version}/{build_number}/{name}-{version}.zip"


def fetch_catalog(
    host: str,
    *,
    timeout: float = 30.0,
    verify: bool = True,
    cert: str | tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Crawl a live ``content-hub.json`` from ``host`` and return its entry list.

    ``host`` may be a bare host (``secops-content.forticloud.com``), a
    ``scheme://host`` base, or a full URL ending in ``content-hub.json`` — the
    manifest path (:data:`_CATALOG_PATH`) is appended unless already present, and
    a bare host defaults to ``https://``. This is the upstream half of the merge:
    fetch Fortinet's (or any mirror's) catalog, then splice your local entries in
    via :meth:`ContentCatalog.merge`.

    The official ``secops-content.forticloud.com`` host requires a mutual-TLS FDN
    client certificate in online mode (see the plan doc). Pass ``cert`` — a
    combined PEM path, or a ``(cert, key)`` pair — to present it (this is how the
    Option-B mirror crawls the entitled upstream). Without it, a bare fetch of the
    official host fails the handshake; a plain ``OFFLINEREPO`` mirror needs no cert.

    Raises :class:`~pyfsr.exceptions.RepoUnreachableError` on a transport failure
    (DNS/TLS/timeout/refused), :class:`~pyfsr.exceptions.RepoArtifactNotFoundError`
    on a 404, or ``ValueError`` if the host answers with something that isn't a
    JSON array.
    """
    url = _catalog_url(host)
    try:
        resp = requests.get(url, headers=_HEADERS, verify=verify, timeout=timeout, cert=cert)
    except requests.exceptions.RequestException as exc:
        raise RepoUnreachableError(url=url) from exc
    with resp:
        if resp.status_code == 404:
            raise RepoArtifactNotFoundError(url=url)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"{url}: content-hub.json must be a JSON array, got {type(data).__name__}")
    return data


def _catalog_url(host: str) -> str:
    """Normalize ``host`` to a full ``.../content-hub/content-hub.json`` URL."""
    h = host.strip()
    if not h.startswith(("http://", "https://")):
        h = f"https://{h}"
    h = h.rstrip("/")
    if h.endswith("/content-hub.json"):
        return h
    return f"{h}{_CATALOG_PATH}"


def build_entry(
    *,
    name: str,
    type: str,
    version: str,
    buildNumber: int,
    label: str,
    description: str = "",
    publisher: str = "",
    certified: bool = False,
    category: list[str] | str | None = None,
    availableVersions: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Construct a spec-valid catalog entry with path fields filled in.

    Only ``name``/``type``/``version``/``buildNumber``/``label`` are truly
    required; the rest default to empty-but-valid. ``infoPath`` and ``iconLarge``
    are synthesized from the path convention (:func:`info_path` / :func:`icon_path`)
    unless you pass them in ``extra``. Any additional keys — ``operations`` for a
    connector, ``contents``/``dependencies`` for a solution pack, ``inputformat``
    for an ai_agent — are merged verbatim, so this composes with type-specific
    fields.

    The returned dict is the exact shape :meth:`ContentCatalog.add` and
    :func:`validate_entry` expect. It does not itself raise on a bad ``type``;
    run it through :func:`validate_entry` to catch that.
    """
    entry: dict[str, Any] = {
        "name": name,
        "type": type,
        "version": version,
        "buildNumber": buildNumber,
        "label": label,
        "description": description,
        "publisher": publisher,
        "certified": certified,
        # ``category`` is polymorphic (list for solutionpacks, str for others);
        # preserve a string as-is rather than shredding it into characters.
        "category": category if isinstance(category, str) else (list(category) if category else []),
        "availableVersions": list(availableVersions) if availableVersions else [version],
        "infoPath": info_path(name, version, buildNumber),
        "iconLarge": icon_path(name, version, buildNumber),
    }
    entry.update(extra)
    return entry


def _infer_type(info: dict[str, Any]) -> str:
    """Best-effort artifact type from a parsed ``info.json``.

    ``operations`` ⇒ connector; ``contents``/``solutionUuid`` ⇒ solutionpack;
    otherwise widget. Callers can override with an explicit ``type``.
    """
    if "operations" in info:
        return "connector"
    if "contents" in info or "solutionUuid" in info or "solution_uuid" in info:
        return "solutionpack"
    return "widget"


def read_artifact_info(path: str) -> dict[str, Any]:
    """Extract and parse the ``info.json`` bundled inside an artifact archive.

    Works on a connector/widget ``.tgz`` (gzipped tar) or a solution-pack
    ``.zip``. Returns the shallowest ``info.json`` found (connectors nest theirs
    one dir deep, widgets/SPs keep it at the root). Raises ``ValueError`` if the
    archive has no ``info.json`` or isn't a recognized archive type.
    """
    candidates: list[tuple[int, str]] = []  # (depth, member name)
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as tf:
            for member in tf.getmembers():
                if member.isfile() and os.path.basename(member.name) == "info.json":
                    candidates.append((member.name.count("/"), member.name))
            if not candidates:
                raise ValueError(f"{path}: no info.json inside the archive")
            _, name = min(candidates)
            fh = tf.extractfile(name)
            data = json.load(fh) if fh else None
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for zi in zf.infolist():
                if not zi.is_dir() and os.path.basename(zi.filename) == "info.json":
                    candidates.append((zi.filename.count("/"), zi.filename))
            if not candidates:
                raise ValueError(f"{path}: no info.json inside the archive")
            _, name = min(candidates)
            with zf.open(name) as fh:
                data = json.load(fh)
    else:
        raise ValueError(f"{path}: not a .tgz or .zip archive")

    if not isinstance(data, dict):
        raise ValueError(f"{path}: info.json is not a JSON object")
    return data


def entry_from_artifact(
    path: str,
    *,
    type: str | None = None,
    buildNumber: int = 1,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a catalog entry directly from a connector/widget/SP artifact file.

    Reads the archive's bundled ``info.json`` (:func:`read_artifact_info`), maps
    its fields onto a spec-valid entry via :func:`build_entry`, and infers the
    ``type`` (:func:`_infer_type`) unless you pass one. ``buildNumber`` and any
    other keyword ``overrides`` (e.g. ``publisher=``, ``certified=``) win over the
    values read from the archive — so a repackaged in-house build can stamp its
    own build number.

    Raises ``ValueError`` if the archive lacks a usable ``info.json`` or the
    ``name``/``version`` needed to place it. The returned entry still carries the
    synthesized ``infoPath``/``iconLarge`` paths; feed it to
    :meth:`ContentCatalog.add` and, for a downloadable artifact, copy the file to
    the tree via :meth:`ContentCatalog.write_tree` (``artifacts=``).
    """
    info = read_artifact_info(path)
    name = overrides.pop("name", None) or info.get("name")
    version = overrides.pop("version", None) or info.get("version")
    if not name or not version:
        raise ValueError(f"{path}: info.json is missing name/version (name={name!r}, version={version!r})")
    etype = type or _infer_type(info)

    # Carry the descriptive fields the catalog surfaces; everything else in the
    # artifact's info.json (operations, contents, help, …) rides along verbatim.
    passthrough = {k: v for k, v in info.items() if k not in ("name", "version", "type")}
    passthrough.update(overrides)
    return build_entry(
        name=name,
        type=etype,
        version=version,
        buildNumber=buildNumber,
        label=passthrough.pop("label", None) or name,
        description=passthrough.pop("description", "") or "",
        publisher=passthrough.pop("publisher", "") or "",
        certified=bool(passthrough.pop("certified", False)),
        category=passthrough.pop("category", None),
        availableVersions=passthrough.pop("availableVersions", None),
        **passthrough,
    )


def validate_entry(entry: Any) -> list[str]:
    """Return a list of problems with a single catalog entry (empty == valid).

    Checks structural requirements the sync relies on, not editorial quality:

    * entry is a JSON object;
    * every key in :data:`_REQUIRED_KEYS` is present and non-empty;
    * ``type`` is one of :data:`CATALOG_TYPES`;
    * ``buildNumber`` is an int (the sync builds numeric artifact paths from it);
    * ``availableVersions``, when present, is a list; ``category`` is a list or
      string (it is a list for solutionpacks, a string for other types on the wire);
    * ``version`` appears in ``availableVersions`` when that list is non-empty.

    Never raises — a non-dict input is reported as a problem string.
    """
    problems: list[str] = []
    if not isinstance(entry, dict):
        return [f"entry is not a JSON object (got {type(entry).__name__})"]

    for key in _REQUIRED_KEYS:
        val = entry.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            problems.append(f"missing/empty required key {key!r}")

    etype = entry.get("type")
    if etype is not None and etype not in CATALOG_TYPES:
        problems.append(f"unknown type {etype!r} (expected one of {sorted(CATALOG_TYPES)})")

    # ``name``/``version`` are interpolated into filesystem paths by
    # :meth:`ContentCatalog.write_tree`; reject anything that could escape the
    # served root (path separators, ``..``, control chars). This is the primary
    # guard for untrusted entries (e.g. a manual add via the mirror admin API).
    for key in ("name", "version"):
        val = entry.get(key)
        if isinstance(val, str) and val and (not _SLUG_RE.match(val) or ".." in val):
            problems.append(f"{key!r} has illegal characters {val!r} (allowed: letters, digits, . _ -; no '..')")

    build = entry.get("buildNumber")
    if build is not None and not isinstance(build, int):
        problems.append(f"buildNumber must be an int, got {type(build).__name__}")

    avail_val = entry.get("availableVersions")
    if avail_val is not None and not isinstance(avail_val, list):
        problems.append(f"'availableVersions' must be a list, got {type(avail_val).__name__}")

    # ``category`` is polymorphic on the live wire: a list for solutionpacks, a
    # plain string for connector/widget/ai_agent (often ""). Accept either.
    cat_val = entry.get("category")
    if cat_val is not None and not isinstance(cat_val, (list, str)):
        problems.append(f"'category' must be a list or string, got {type(cat_val).__name__}")

    avail = entry.get("availableVersions")
    version = entry.get("version")
    if isinstance(avail, list) and avail and version is not None and version not in avail:
        problems.append(f"version {version!r} not in availableVersions {avail!r}")

    return problems


def _entry_key(entry: dict[str, Any]) -> tuple[str, str]:
    """Identity of an entry for merge/dedup: ``(type, name)``."""
    return (str(entry.get("type", "")), str(entry.get("name", "")))


class ContentCatalog:
    """A mutable, ordered collection of Content Hub entries.

    Backed by a ``(type, name) -> entry`` mapping, so adding an entry with the
    same type+name **replaces** the earlier one (last-write-wins) — this is what
    makes "splice ours over Fortinet's" a one-liner: load upstream, then
    :meth:`add` your overrides. Iteration and :meth:`to_list` preserve insertion
    order.
    """

    def __init__(self, entries: Iterable[dict[str, Any]] | None = None) -> None:
        self._by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for e in entries or []:
            self.add(e)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_list(cls, data: list[dict[str, Any]]) -> ContentCatalog:
        """Build from an in-memory list of entries (a parsed ``content-hub.json``)."""
        return cls(data)

    @classmethod
    def from_file(cls, path: str) -> ContentCatalog:
        """Load a ``content-hub.json`` file (a flat JSON array of entries)."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(f"{path}: content-hub.json must be a JSON array, got {type(data).__name__}")
        return cls(data)

    @classmethod
    def from_url(
        cls,
        host: str,
        *,
        timeout: float = 30.0,
        verify: bool = True,
        cert: str | tuple[str, str] | None = None,
    ) -> ContentCatalog:
        """Crawl a live ``content-hub.json`` from ``host`` into a catalog.

        Thin wrapper over :func:`fetch_catalog` — see it for the ``host`` forms,
        the FDN-cert caveat on the official host (pass ``cert`` to present the
        mutual-TLS client certificate), and the error contract. ``verify`` controls
        TLS verification (default on); set False only for a self-signed internal mirror.
        """
        return cls(fetch_catalog(host, timeout=timeout, verify=verify, cert=cert))

    # -- mutation ----------------------------------------------------------

    def add(self, entry: dict[str, Any]) -> None:
        """Add or replace an entry (keyed by ``type`` + ``name``)."""
        if not isinstance(entry, dict):
            raise TypeError(f"entry must be a dict, got {type(entry).__name__}")
        self._by_key[_entry_key(entry)] = entry

    def merge(self, other: ContentCatalog | Iterable[dict[str, Any]]) -> None:
        """Splice another catalog in, its entries winning on ``type``+``name`` collisions.

        Use to union Fortinet's mirrored catalog with your local one: build the
        local catalog, then ``local.merge(upstream)`` to keep local overrides, or
        ``upstream.merge(local)`` to let local win — merge always lets the
        argument win, so order the call to match your intent.
        """
        entries = other.to_list() if isinstance(other, ContentCatalog) else other
        for e in entries:
            self.add(e)

    def remove(self, *, type: str, name: str) -> bool:
        """Drop an entry by type+name; return whether one was present."""
        return self._by_key.pop((type, name), None) is not None

    # -- access ------------------------------------------------------------

    def to_list(self) -> list[dict[str, Any]]:
        """Return the entries as a plain list, in insertion order."""
        return list(self._by_key.values())

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize to a ``content-hub.json`` string (a flat JSON array)."""
        return json.dumps(self.to_list(), indent=indent, ensure_ascii=False)

    def counts(self) -> dict[str, int]:
        """Return a ``type -> count`` breakdown (matches the live catalog's tally)."""
        out: dict[str, int] = {}
        for e in self._by_key.values():
            out[str(e.get("type", "?"))] = out.get(str(e.get("type", "?")), 0) + 1
        return out

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self):
        return iter(self._by_key.values())

    # -- validation --------------------------------------------------------

    def validate(self) -> dict[str, list[str]]:
        """Validate every entry; return ``"{type}/{name}" -> [problems]`` for bad ones.

        An empty dict means the whole catalog is spec-valid. Duplicate identities
        can't occur (they collapse on :meth:`add`), so this only surfaces
        per-entry structural problems from :func:`validate_entry`.
        """
        out: dict[str, list[str]] = {}
        for (etype, ename), entry in self._by_key.items():
            problems = validate_entry(entry)
            if problems:
                out[f"{etype}/{ename}"] = problems
        return out

    # -- emit --------------------------------------------------------------

    def write_tree(
        self,
        root: str,
        *,
        artifacts: dict[tuple[str, str], str] | None = None,
        icons: dict[tuple[str, str], str] | None = None,
        validate: bool = True,
    ) -> str:
        """Write a served-ready directory tree under ``root`` and return its manifest path.

        Lays out, per the fetch contract::

            {root}/content-hub/content-hub.json
            {root}/content-hub/{name}-{version}/{buildNumber}/info.json
            {root}/content-hub/{name}-{version}/latest/info.json

        Each entry's own dict is written as its ``info.json`` at both the numbered
        build dir and the ``latest/`` copy (matching the live layout where both
        exist). If ``artifacts`` / ``icons`` map an entry's ``(type, name)`` to a
        local file, that file is copied to the artifact/icon path the sync expects.

        With ``validate=True`` (default) a non-empty :meth:`validate` raises
        ``ValueError`` before anything is written, so a bad catalog never produces
        a half-built tree.
        """
        if validate:
            problems = self.validate()
            if problems:
                raise ValueError(f"catalog has {len(problems)} invalid entr(y/ies): {problems}")

        base = os.path.join(root, "content-hub")
        os.makedirs(base, exist_ok=True)

        manifest_path = os.path.join(base, "content-hub.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

        for entry in self._by_key.values():
            name = entry.get("name")
            version = entry.get("version")
            build = entry.get("buildNumber")
            if not (name and version is not None and build is not None):
                continue  # validate=False path: skip un-placeable entries
            item_root = os.path.join(base, f"{name}-{version}")
            # Defense in depth: even with validate=False, never write outside the
            # served root (a crafted name/version could contain ``..`` / separators).
            if not os.path.realpath(item_root).startswith(os.path.realpath(base) + os.sep):
                raise ValueError(f"entry {name!r}/{version!r} resolves outside the served tree; refusing to write")
            build_dir = os.path.join(item_root, str(build))
            latest_dir = os.path.join(item_root, "latest")
            os.makedirs(build_dir, exist_ok=True)
            os.makedirs(latest_dir, exist_ok=True)

            info_json = json.dumps(entry, indent=2, ensure_ascii=False)
            for d in (build_dir, latest_dir):
                with open(os.path.join(d, "info.json"), "w", encoding="utf-8") as fh:
                    fh.write(info_json)

            key = _entry_key(entry)
            if artifacts and key in artifacts:
                # Preserve the source extension — solution packs ship as ``.zip``
                # but connectors/widgets as ``.tgz``; the appliance fetches the
                # artifact at whatever name the catalog implies for its type.
                ext = os.path.splitext(artifacts[key])[1] or ".zip"
                shutil.copyfile(artifacts[key], os.path.join(build_dir, f"{name}-{version}{ext}"))
            if icons and key in icons:
                img_dir = os.path.join(build_dir, "images")
                os.makedirs(img_dir, exist_ok=True)
                shutil.copyfile(icons[key], os.path.join(img_dir, "fsr-icon-large.png"))

        return manifest_path
