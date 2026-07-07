"""Discover and download a connector from Fortinet's public content repo.

No appliance, no credentials — ``pyfsr.repo`` talks to the public, unauthenticated
``repo.fortisoar.fortinet.com`` directly. This is the no-box end-to-end:

  1. gate on reachability (exit cleanly if the host can't be reached),
  2. search the public connector manifest for a connector,
  3. list every published version of it,
  4. fetch its per-version ``info.json`` (operations, release notes, …),
  5. download a specific version's ``.tgz`` so it can be installed with
     ``client.connectors.install_from_file(...)``.

Discovery scope (live-verified): connectors have a public manifest, so
``list_connectors`` / ``search_connectors`` / ``connector_versions`` /
``connector_info`` give full no-appliance discovery. Widgets and solution-packs
have **no public manifest** — use ``client.content_hub.search_available_*`` on an
appliance to discover those, then ``repo.widget_info`` /
``repo.solution_pack_info`` / ``repo.download_*`` once you know the slug+version.

Configure via env (or edit the constants below):
  CONNECTOR -> connector to look up (default: anyrun)
  VERSION   -> exact version to download (default: the connector's latest)
  DEST      -> where to write the .tgz (default: current directory)
"""

from __future__ import annotations

import os
import sys

from pyfsr import repo

CONNECTOR = os.environ.get("CONNECTOR", "anyrun")
VERSION = os.environ.get("VERSION", "")  # blank -> connector's latest version
DEST = os.environ.get("DEST", ".")


def main() -> int:
    if not repo.reachable():
        print("content repo unreachable - no FDN access / offline", file=sys.stderr)
        return 1

    # 1. Search the manifest.
    matches = repo.search_connectors(CONNECTOR)
    if not matches:
        print(f"no connectors matched {CONNECTOR!r}", file=sys.stderr)
        return 1
    print(f"matches for {CONNECTOR!r}:")
    for e in matches:
        print(f"  {e.name:24s} {e.version:10s} {e.category_str or ''}")
    entry = matches[0]
    print(f"-> using {entry.name!r} (latest {entry.version})")

    # 2. Every published version.
    versions = repo.connector_versions(entry.name)
    print(f"\npublished versions for {entry.name!r}: {versions}")

    # 3. Per-version detail (operations, release notes, ...).
    target_version = VERSION or entry.version
    info = repo.connector_info(entry.name, target_version)
    print(f"\ninfo.json for {entry.name}-{target_version}:")
    print(f"  publisher: {info.publisher}  certified: {info.certified}")
    print(f"  operations: {len(info['operations']) if info['operations'] else 0}")
    print(f"  releaseNotes: {(info.releaseNotes or '')[:80]}")

    # 4. Download the specific version. Note: ``availableVersions`` is publish
    # history, not a guarantee the version is still retained for download — a
    # listed version may 404 (surfaced as RepoArtifactNotFoundError). We catch
    # that here rather than crash, since it's a normal repo state.
    from pyfsr.exceptions import RepoArtifactNotFoundError

    try:
        path = repo.download_connector(entry.name, target_version, DEST)
    except RepoArtifactNotFoundError as exc:
        print(f"\n{entry.name}-{target_version} is not retained for download ({exc})", file=sys.stderr)
        print("try an older version from the list above, or a different connector.", file=sys.stderr)
        return 1
    print(f"\ndownloaded: {path}")
    print(f"install with: client.connectors.install_from_file({path!r}, replace=True, wait=True)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
