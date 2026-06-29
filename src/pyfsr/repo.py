"""Download artifacts from Fortinet's public content repository.

The content repository at ``repo.fortisoar.fortinet.com`` serves every
published connector / widget / solution-pack version as a downloadable archive.
It is **public and unauthenticated** — these helpers therefore use a standalone
HTTP fetch (no appliance, no :class:`~pyfsr.client.FortiSOAR` token) and work
even with no box in play.

The missing half this module fills: the SDK could already *consume* a local
bundle to pin a version (``connectors.install_from_file`` /
``connectors.ensure_version(bundle_path=...)``) but nothing *fetched* a
specific-version artifact so it could be installed directly. Now::

    from pyfsr import repo

    if not repo.reachable():
        raise RuntimeError("content repo unreachable — no FDN access / offline")
    tgz = repo.download_connector("servicenow", "1.0.0")   # -> local .tgz path
    client.connectors.install_from_file(tgz, replace=True, wait=True)

Every ``download_*`` preflights reachability and raises a distinct, actionable
error: :class:`~pyfsr.exceptions.RepoUnreachableError` (can't reach the host)
vs :class:`~pyfsr.exceptions.RepoArtifactNotFoundError` (host answered, but no
such name/version) — so callers can tell "offline" from "bad version".

Artifact URL layouts (live-verified 2026-06-28):

- connector ``.tgz``: ``/xf/solutions/connectors/<name>-<ver>/latest/<name>.tgz``
- widget ``.tgz``:    ``/fsr-widgets/<name>-<ver>/<name>-<ver>.tgz``
- solution pack ``.zip``: ``/xf/solutions/solutionpacks/<name>-<ver>/latest/<name>.zip``
"""

from __future__ import annotations

import os

import requests

from .exceptions import RepoArtifactNotFoundError, RepoUnreachableError

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
