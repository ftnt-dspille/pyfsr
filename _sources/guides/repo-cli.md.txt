# Content Repository CLI

The `pyfsr repo` command group discovers and downloads artifacts from
Fortinet's **public, unauthenticated** content repository
(`repo.fortisoar.fortinet.com`) — no appliance, no token. It wraps
{mod}`pyfsr.repo`, the standalone counterpart to the appliance-gated
`client.content_hub` searches.

Use it to answer "what connectors exist?", "what versions can I pin?", and to
fetch a specific-version archive so it can be installed with
`client.connectors.install_from_file(...)`.

```sh
pyfsr repo reachable
pyfsr repo search service
pyfsr repo versions servicenow
pyfsr repo info connector servicenow 3.6.0
pyfsr repo download connector servicenow 3.6.0 --dest /tmp
```

Every verb respects `--json` / `--csv` for machine-readable output.

## Discovery scope (what's feasible without an appliance)

The public repo exposes different surfaces per artifact type:

| Artifact | List / search | Versions | Detail (`info.json`) |
|---|---|---|---|
| **Connector** | `list-connectors`, `search` | `versions` | `info connector` |
| **Widget** | — (no public manifest) | — | `info widget` |
| **Solution pack** | — (no public manifest) | via `info solution-pack` | `info solution-pack` |

Connectors have a public manifest (`/connectors/info/connectors.json`), so
full no-appliance discovery works. Widgets and solution-packs have **no public
manifest** — to list or search those, use `client.content_hub.search_available_*`
on an appliance, then come back here for the per-version `info` and `download`.

```python
from pyfsr import repo
from pyfsr import FortiSOAR

# No appliance needed:
for entry in repo.search_connectors("service"):
    print(entry.name, entry.version, entry.category_str)

versions = repo.connector_versions("servicenow")  # ['1.0.0', '1.1.0', ...]
info = repo.connector_info("servicenow", "3.6.0")  # ConnectorVersionInfo
tgz = repo.download_connector("servicenow", "3.6.0")

# Install on an appliance (this part needs a box):
client = FortiSOAR(base_url="fortisoar.example.com", auth=("csadmin", "<your-password>"))
client.connectors.install_from_file(tgz, replace=True, wait=True)
```

## Subcommands

### `reachable`

Cheap reachability check; exits `0` if the repo answers, `1` otherwise. Use it
to gate an offline install script.

```sh
pyfsr repo reachable
```

### `list-connectors`

List every connector in the public manifest (latest version only; ~720 rows).
`--category` filters client-side by a category substring.

```sh
pyfsr repo list-connectors
pyfsr repo list-connectors --category "threat intelligence" --json
```

### `search`

Case-insensitive substring search across each connector's name, label,
description, and category.

```sh
pyfsr repo search code
```

### `versions`

Every published version of one connector (its `info.json` `availableVersions`).
Note this is publish **history**, not a guarantee every version is still
downloadable — a listed version may 404 on `download` (surfaced as
`RepoArtifactNotFoundError`).

```sh
pyfsr repo versions servicenow --json
```

### `info`

The per-version `info.json` for a connector, widget, or solution-pack. The
`kind` positional selects which:

```sh
pyfsr repo info connector servicenow 3.6.0
pyfsr repo info widget accessControl 2.1.0
pyfsr repo info solution-pack fortindrEssentials 1.0.4
```

A connector `info` carries `availableVersions`, `operations`, `releaseNotes`,
`publisher`, and `certified`. A widget `info` has a different shape (a
`compatibility` list; no `availableVersions`). A solution-pack `info` carries
`availableVersions`, `dependencies`, and `fsrMinCompatibility`.

### `download`

Download a specific-version archive (`.tgz` for connectors/widgets, `.zip` for
solution-packs) by exact name + version. `--dest` may be a file or directory
(default: current directory).

```sh
pyfsr repo download connector servicenow 3.6.0 --dest /tmp
pyfsr repo download widget accessControl 2.1.0
pyfsr repo download solution-pack fortindrEssentials 1.0.4 --dest packs/
```

## Errors

`pyfsr repo` distinguishes two failure modes with distinct, nonzero exits:

- **unreachable** — the host can't be reached (no FDN access, air-gapped,
  firewalled). All verbs surface this as `error: content repo unreachable`.
- **no artifact** — the host answered but there's no such name/version (404).
  Surfaces as `error: no artifact at <url>`. This is also what a listed-but-no-
  longer-retained version returns on `download` / `info`.

## When to use which surface

| You want… | Without an appliance | With an appliance |
|---|---|---|
| List / search connectors | `pyfsr repo list-connectors` / `search` | `client.content_hub.search_available_connectors` |
| Connector version history | `pyfsr repo versions` | `client.content_hub.connector_versions` |
| List / search widgets or solution-packs | not available | `client.content_hub.search_available_*` |
| Per-version detail | `pyfsr repo info` | — |
| Download an archive | `pyfsr repo download` | `client.solution_packs.install` (installs directly) |

See {doc}`connectors <connectors>` for the appliance-side connector lifecycle
(install, pin a version, configure) and the
[`repo_discover_and_download.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/repo_discover_and_download.py)
example for a complete no-appliance discover-and-download script.
