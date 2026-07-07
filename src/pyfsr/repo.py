"""Discover and download artifacts from Fortinet's public content repository.

The content repository at ``repo.fortisoar.fortinet.com`` serves every
published connector / widget / solution-pack version as a downloadable archive,
**public and unauthenticated**. These helpers therefore use a standalone HTTP
fetch (no appliance, no :class:`~pyfsr.client.FortiSOAR` token) and work even
with no box in play.

Two halves:

* **Discovery** (what exists + what versions) — connectors have a public
  manifest, so :func:`list_connectors` / :func:`search_connectors` /
  :func:`connector_info` / :func:`connector_versions` give full no-appliance
  discovery. Widgets and solution-packs have **no public manifest**, so only
  their per-version :func:`widget_info` / :func:`solution_pack_info` are
  fetchable here; their *list/search* discovery still needs an appliance via
  ``client.content_hub.search_available_*``.
* **Download** — :func:`download_connector` / :func:`download_widget` /
  :func:`download_solution_pack` fetch a specific-version archive once you know
  the exact name+version (discovered above, or from the appliance).

::

    from pyfsr import repo

    if not repo.reachable():
        raise RuntimeError("content repo unreachable — no FDN access / offline")
    for entry in repo.search_connectors("service"):
        print(entry.name, entry.version, entry.category)
    versions = repo.connector_versions("servicenow")   # -> ['1.0.0', '1.1.0', ...]
    tgz = repo.download_connector("servicenow", "1.0.0")
    client.connectors.install_from_file(tgz, replace=True, wait=True)

Every helper preflights reachability and raises a distinct, actionable error:
:class:`~pyfsr.exceptions.RepoUnreachableError` (can't reach the host) vs
:class:`~pyfsr.exceptions.RepoArtifactNotFoundError` (host answered, but no such
name/version) — so callers can tell "offline" from "bad version".

Artifact URL layouts (live-verified):

- connector manifest:        ``/connectors/info/connectors.json``
- connector ``.tgz``:       ``/xf/solutions/connectors/<name>-<ver>/latest/<name>.tgz``
- connector ``info.json``:  ``/xf/solutions/connectors/<name>-<ver>/latest/info.json``
                              (fallback: ``/content-hub/<name>-<ver>/latest/info.json`` —
                              some connectors, e.g. code-snippet, are only retained there)
- widget ``.tgz``:          ``/fsr-widgets/<name>-<ver>/<name>-<ver>.tgz``
- widget ``info.json``:     ``/fsr-widgets/<name>-<ver>/info.json``
- solution pack ``.zip``:   ``/xf/solutions/solutionpacks/<name>-<ver>/latest/<name>.zip``
- solution pack ``info.json``: ``/xf/solutions/solutionpacks/<name>-<ver>/latest/info.json``
"""

from __future__ import annotations

import os
from typing import Any

import requests

from .exceptions import RepoArtifactNotFoundError, RepoUnreachableError
from .models import ConnectorVersionInfo, RepoConnectorEntry, SolutionPackInfo, WidgetInfo

_REPO_HOST = "https://repo.fortisoar.fortinet.com"

# A cheap, known-stable path used only for the reachability preflight.
_REACHABILITY_PATH = "/connectors/info/connectors.json"

# Every existing caller fetches unauthenticated with just a User-Agent. The
# public host presents a valid CA cert, so TLS is verified by default (matching
# content_hub); pass ``verify=False`` only for a self-signed internal mirror.
_HEADERS = {"User-Agent": "pyfsr"}
_CHUNK = 1 << 16


def _connector_url(name: str, version: str) -> str:
    return f"{_REPO_HOST}/xf/solutions/connectors/{name}-{version}/latest/{name}.tgz"


def _widget_url(name: str, version: str) -> str:
    return f"{_REPO_HOST}/fsr-widgets/{name}-{version}/{name}-{version}.tgz"


def _solution_pack_url(name: str, version: str) -> str:
    return f"{_REPO_HOST}/xf/solutions/solutionpacks/{name}-{version}/latest/{name}.zip"


def _manifest_url() -> str:
    return f"{_REPO_HOST}{_REACHABILITY_PATH}"


def _connector_info_url(name: str, version: str) -> str:
    return f"{_REPO_HOST}/xf/solutions/connectors/{name}-{version}/latest/info.json"


def _connector_info_url_alt(name: str, version: str) -> str:
    # Fallback path: some connectors (e.g. code-snippet) aren't retained under
    # ``/xf/solutions/connectors/`` but are under ``/content-hub/``. The
    # appliance-side ``content_hub.connector_versions`` follows the same
    # ``/content-hub/`` layout. Live-verified: code-snippet 404s on the first
    # path for every version but resolves here with full ``availableVersions``.
    return f"{_REPO_HOST}/content-hub/{name}-{version}/latest/info.json"


def _widget_info_url(name: str, version: str) -> str:
    # Widget info.json lives flat under the version dir (no ``/latest/`` segment),
    # unlike connector/solution-pack info.json.
    return f"{_REPO_HOST}/fsr-widgets/{name}-{version}/info.json"


def _solution_pack_info_url(name: str, version: str) -> str:
    return f"{_REPO_HOST}/xf/solutions/solutionpacks/{name}-{version}/latest/info.json"


def reachable(*, timeout: float = 5.0, verify: bool = True) -> bool:
    """Return whether the content repository answers, without raising.

    A cheap ``GET`` (streamed, body discarded) against a known-stable path. Any
    connection/timeout error — no FDN access, air-gapped, firewalled — returns
    ``False`` rather than propagating, so callers and setup scripts can gate an
    offline install on it. A non-2xx *HTTP* answer still counts as reachable
    (the host is up); only transport failures are ``False``.

    ``verify`` controls TLS verification (default on); set False only for a
    self-signed internal mirror.
    """
    url = f"{_REPO_HOST}{_REACHABILITY_PATH}"
    try:
        resp = requests.get(url, headers=_HEADERS, verify=verify, timeout=timeout, stream=True)
        resp.close()
        return True
    except requests.exceptions.RequestException:
        return False


def _download(
    url: str,
    dest: str | None,
    *,
    default_name: str,
    timeout: float,
    preflight_timeout: float,
    verify: bool,
) -> str:
    """Preflight reachability, then stream ``url`` to a local file; return its path."""
    if not reachable(timeout=preflight_timeout, verify=verify):
        raise RepoUnreachableError(url=url)

    if dest is None:
        target = default_name
    elif os.path.isdir(dest):
        target = os.path.join(dest, default_name)
    else:
        target = dest

    try:
        resp = requests.get(url, headers=_HEADERS, verify=verify, timeout=timeout, stream=True)
    except requests.exceptions.RequestException as exc:  # transport died mid-request
        raise RepoUnreachableError(url=url) from exc

    with resp:
        if resp.status_code == 404:
            raise RepoArtifactNotFoundError(url=url)
        resp.raise_for_status()
        with open(target, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK):
                if chunk:
                    fh.write(chunk)
    return target


def _get_json(url: str, *, timeout: float, preflight_timeout: float, verify: bool) -> Any:
    """Preflight reachability, then ``GET`` ``url`` and return parsed JSON.

    Shared by the discovery helpers. Same error split as :func:`_download`:
    :class:`~pyfsr.exceptions.RepoUnreachableError` if the host can't be reached
    (preflight or mid-request), :class:`~pyfsr.exceptions.RepoArtifactNotFoundError`
    on a 404 (host answered, no such artifact). Other non-2xx answers raise via
    ``raise_for_status``.
    """
    if not reachable(timeout=preflight_timeout, verify=verify):
        raise RepoUnreachableError(url=url)
    try:
        resp = requests.get(url, headers=_HEADERS, verify=verify, timeout=timeout)
    except requests.exceptions.RequestException as exc:  # transport died mid-request
        raise RepoUnreachableError(url=url) from exc
    with resp:
        if resp.status_code == 404:
            raise RepoArtifactNotFoundError(url=url)
        resp.raise_for_status()
        return resp.json()


def download_connector(
    name: str,
    version: str,
    dest: str | None = None,
    *,
    timeout: float = 120.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> str:
    """Download a connector ``.tgz`` and return the local path.

    ``name``/``version`` are the connector slug and exact version (e.g.
    ``"servicenow"``, ``"1.0.0"``). ``dest`` may be a target file path or a
    directory (the archive keeps its repo filename, ``<name>.tgz``); defaults to
    the current directory. The result feeds straight into
    :meth:`~pyfsr.api.connectors.ConnectorsAPI.install_from_file` or
    ``ensure_version(bundle_path=...)``.

    ``verify`` controls TLS verification (default on); set False only for a
    self-signed internal mirror.

    Raises :class:`~pyfsr.exceptions.RepoUnreachableError` if the repo can't be
    reached, or :class:`~pyfsr.exceptions.RepoArtifactNotFoundError` if there is
    no such name/version.
    """
    return _download(
        _connector_url(name, version),
        dest,
        default_name=f"{name}.tgz",
        timeout=timeout,
        preflight_timeout=preflight_timeout,
        verify=verify,
    )


def download_widget(
    name: str,
    version: str,
    dest: str | None = None,
    *,
    timeout: float = 120.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> str:
    """Download a widget ``.tgz`` and return the local path.

    Same contract as :func:`download_connector`; the archive filename is
    ``<name>-<version>.tgz``.
    """
    return _download(
        _widget_url(name, version),
        dest,
        default_name=f"{name}-{version}.tgz",
        timeout=timeout,
        preflight_timeout=preflight_timeout,
        verify=verify,
    )


def download_solution_pack(
    name: str,
    version: str,
    dest: str | None = None,
    *,
    timeout: float = 300.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> str:
    """Download a solution-pack ``.zip`` and return the local path.

    Same contract as :func:`download_connector`; the archive filename is
    ``<name>.zip``.
    """
    return _download(
        _solution_pack_url(name, version),
        dest,
        default_name=f"{name}.zip",
        timeout=timeout,
        preflight_timeout=preflight_timeout,
        verify=verify,
    )


# ---------------------------------------------------------------------------
# Discovery (no appliance needed)
# ---------------------------------------------------------------------------


def list_connectors(
    *,
    timeout: float = 15.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> list[RepoConnectorEntry]:
    """List every connector in Fortinet's public manifest.

    Fetches ``/connectors/info/connectors.json`` — a single, unauthenticated
    document covering all published connectors. The manifest is
    **latest-version-only** (one entry per connector, at its current version);
    for the full publish history of one connector use :func:`connector_versions`.

    Returns typed, dict-compatible :class:`~pyfsr.models.RepoConnectorEntry`
    objects sorted by ``name`` for stable output. ``verify`` controls TLS
    verification (default on); set False only for a self-signed internal mirror.

    Raises :class:`~pyfsr.exceptions.RepoUnreachableError` if the repo can't be
    reached, or :class:`~pyfsr.exceptions.RepoArtifactNotFoundError` if the
    manifest path itself is gone (host answered 404).
    """
    data = _get_json(
        _manifest_url(),
        timeout=timeout,
        preflight_timeout=preflight_timeout,
        verify=verify,
    )
    entries = [RepoConnectorEntry(**v) for v in data.values()]
    entries.sort(key=lambda e: e.name or "")
    return entries


def search_connectors(
    term: str,
    *,
    timeout: float = 15.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> list[RepoConnectorEntry]:
    """Search the public connector manifest by free-text ``term``.

    Client-side, case-insensitive substring match across each entry's
    ``name`` / ``label`` / ``description`` / ``category``. Fetches the same
    single manifest as :func:`list_connectors`, so the reachability/error
    contract is identical. Returns matching
    :class:`~pyfsr.models.RepoConnectorEntry` objects sorted by ``name``;
    an empty list means no match (not an error).
    """
    needle = (term or "").lower()
    entries = list_connectors(timeout=timeout, preflight_timeout=preflight_timeout, verify=verify)
    return [
        e for e in entries if any(needle in str(v or "").lower() for v in (e.name, e.label, e.description, e.category))
    ]


def connector_info(
    name: str,
    version: str,
    *,
    timeout: float = 15.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> ConnectorVersionInfo:
    """Fetch a connector's published ``info.json`` by exact ``name`` + ``version``.

    The no-appliance detail lookup behind :func:`connector_versions` (and the
    no-box twin of ``client.content_hub.connector_versions``). Returns a typed,
    dict-compatible :class:`~pyfsr.models.ConnectorVersionInfo` carrying
    ``availableVersions`` (full publish history), ``operations``,
    ``releaseNotes``, ``publisher``/``certified``, etc. — the extras ride in
    ``extra``.

    ``name``/``version`` are the connector slug and exact version (e.g.
    ``"servicenow"``, ``"1.0.0"``).

    Raises :class:`~pyfsr.exceptions.RepoUnreachableError` if the repo can't be
    reached, or :class:`~pyfsr.exceptions.RepoArtifactNotFoundError` if there is
    no such name/version (a version listed in ``availableVersions`` may no
    longer be retained for download — that shows up here as a 404).
    """
    # Connector info.json lives at one of two paths depending on the connector:
    # ``/xf/solutions/connectors/<name>-<ver>/latest/info.json`` (most) or
    # ``/content-hub/<name>-<ver>/latest/info.json`` (e.g. code-snippet, which
    # 404s on the first for every version). Try the primary, fall back on 404.
    try:
        data = _get_json(
            _connector_info_url(name, version),
            timeout=timeout,
            preflight_timeout=preflight_timeout,
            verify=verify,
        )
    except RepoArtifactNotFoundError:
        data = _get_json(
            _connector_info_url_alt(name, version),
            timeout=timeout,
            preflight_timeout=preflight_timeout,
            verify=verify,
        )
    return ConnectorVersionInfo(**data)


def connector_versions(
    name: str,
    *,
    timeout: float = 15.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> list[str]:
    """Return every published version of connector ``name`` (no appliance).

    The no-box twin of ``client.content_hub.connector_versions``: it looks up
    ``name`` in the public manifest to get the connector's current version,
    fetches that version's ``info.json``, and returns its ``availableVersions``
    list (every version ever published).

    ``name`` is a connector slug (e.g. ``"servicenow"``). Raises ``ValueError``
    if ``name`` isn't in the manifest (box has no FDN access, or name doesn't
    match any connector).

    Note: ``availableVersions`` is publish history, not a guarantee every
    version is still downloadable — a listed version may 404 on
    :func:`download_connector` (surfaced as
    :class:`~pyfsr.exceptions.RepoArtifactNotFoundError`).
    """
    entries = list_connectors(timeout=timeout, preflight_timeout=preflight_timeout, verify=verify)
    match = next((e for e in entries if e.name == name), None)
    if match is None or not match.version:
        raise ValueError(
            f"no connector found in the public manifest named {name!r} "
            "(box may have no FDN access, or name doesn't match any connector)"
        )
    info = connector_info(
        name,
        match.version,
        timeout=timeout,
        preflight_timeout=preflight_timeout,
        verify=verify,
    )
    return list(info.availableVersions or [])


def widget_info(
    name: str,
    version: str,
    *,
    timeout: float = 15.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> WidgetInfo:
    """Fetch a widget's published ``info.json`` by exact ``name`` + ``version``.

    The widget ``info.json`` has a different shape from the connector one: human
    fields nest under a ``metadata`` wrapper (carried in ``extra``) and it
    carries a ``compatibility`` list rather than ``availableVersions`` (widgets
    have no public version-history manifest). Returns a typed, dict-compatible
    :class:`~pyfsr.models.WidgetInfo`.

    Raises :class:`~pyfsr.exceptions.RepoUnreachableError` /
    :class:`~pyfsr.exceptions.RepoArtifactNotFoundError` as usual.
    """
    data = _get_json(
        _widget_info_url(name, version),
        timeout=timeout,
        preflight_timeout=preflight_timeout,
        verify=verify,
    )
    # The wire payload nests the human fields under ``metadata``; flatten the
    # curated ones into the typed view while leaving the full wrapper in extra.
    meta = data.get("metadata") if isinstance(data, dict) else None
    merged = dict(data)
    if isinstance(meta, dict):
        for k in ("description", "publisher", "certified", "compatibility"):
            merged.setdefault(k, meta.get(k))
    return WidgetInfo(**merged)


def solution_pack_info(
    name: str,
    version: str,
    *,
    timeout: float = 15.0,
    preflight_timeout: float = 5.0,
    verify: bool = True,
) -> SolutionPackInfo:
    """Fetch a solution-pack's published ``info.json`` by exact ``name`` + ``version``.

    Carries ``availableVersions`` (full publish history), ``dependencies``, and
    ``fsrMinCompatibility``. Returns a typed, dict-compatible
    :class:`~pyfsr.models.SolutionPackInfo`; the rest (``contents``,
    ``prerequisite``, ``recordTags``, …) rides in ``extra``.

    Note there is **no public manifest** for solution-packs and slug resolution
    is unreliable (Content Hub labels don't map cleanly to repo slugs), so
    *discovery* (name -> slug) still needs
    ``client.content_hub.search_available_packs`` on an appliance; this function
    is the per-version detail lookup once you know the slug+version.

    Raises :class:`~pyfsr.exceptions.RepoUnreachableError` /
    :class:`~pyfsr.exceptions.RepoArtifactNotFoundError` as usual.
    """
    from .models import SolutionPackInfo as _SolutionPackInfo

    data = _get_json(
        _solution_pack_info_url(name, version),
        timeout=timeout,
        preflight_timeout=preflight_timeout,
        verify=verify,
    )
    return _SolutionPackInfo(**data)
