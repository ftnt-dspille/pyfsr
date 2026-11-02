# Self-hosted Content Hub mirror

FortiSOAR's **Content Hub** sync (`csadm package content-hub sync`) reads a
single manifest — `content-hub.json` — from a single host. There is no native
multi-repo. So to host your own connectors / widgets / solution packs, and to
"see both Fortinet's store and yours", you serve one **merged** `content-hub.json`
from a host you control.

pyfsr gives you the two halves:

- {mod}`pyfsr.content_catalog` — build, validate, crawl, and merge a
  `content-hub.json` plus the served directory tree. No appliance, no FortiCloud
  entitlement certificate needed.
- `deploy/content-hub-mirror/` — a ready Docker container that serves the merged
  catalog, reverse-proxies un-overridden requests to an upstream, and ships a web
  GUI + `chctl` CLI to add content, plus `setup-appliance.sh` to point a
  FortiSOAR box at it.

For read-only *discovery/download* against Fortinet's **public** repo (no
self-hosting), see {doc}`repo-cli` / {mod}`pyfsr.repo` instead. This guide is
about hosting your own.

## The catalog library

An entry is a plain dict in the shape the live 8.0 catalog uses.
{func}`~pyfsr.content_catalog.build_entry` fills in the path conventions
(`infoPath`, `iconLarge`) so you don't hand-write them, and
{func}`~pyfsr.content_catalog.validate_entry` checks an entry is structurally
sound and path-safe.

```{doctest}
>>> from pyfsr.content_catalog import build_entry, validate_entry, ContentCatalog
>>> entry = build_entry(
...     name="acmeEnrichment", type="connector", version="1.0.0", buildNumber=1,
...     label="Acme Enrichment", publisher="Acme", category="Threat Intelligence",
... )
>>> entry["infoPath"]
'/content-hub/acmeEnrichment-1.0.0/1'
>>> validate_entry(entry)
[]
```

{class}`~pyfsr.content_catalog.ContentCatalog` collects entries, keyed by
`(type, name)` so adding the same identity **replaces** it — this is what makes
"splice our overrides over upstream" a one-liner. `validate()` returns a
`{"type/name": [problems]}` map (empty means clean).

```{doctest}
>>> cat = ContentCatalog([entry])
>>> cat.add(build_entry(name="myWidget", type="widget", version="2.0.0",
...                      buildNumber=1, label="My Widget"))
>>> len(cat)
2
>>> cat.counts()
{'connector': 1, 'widget': 1}
>>> cat.validate()
{}
```

### Building an entry from an artifact

You rarely need to hand-write fields — point
{func}`~pyfsr.content_catalog.entry_from_artifact` at a connector/widget `.tgz`
or a solution-pack `.zip` and it reads the archive's bundled `info.json`, infers
the type, and returns a valid entry.

```python
from pyfsr.content_catalog import entry_from_artifact

entry = entry_from_artifact("myConnector-1.0.0.tgz")   # type + metadata auto-detected
# entry["type"] == "connector", entry["name"] == "myConnector", ...
```

### Merging an upstream catalog

To keep Fortinet's store visible alongside yours, load an upstream
`content-hub.json` (a saved snapshot, or a live crawl) and merge your local
entries in. {meth}`~pyfsr.content_catalog.ContentCatalog.merge` lets the argument
win, so order the call to match your intent.

```python
from pyfsr.content_catalog import ContentCatalog, build_entry

upstream = ContentCatalog.from_file("upstream-content-hub.json")   # Fortinet's, mirrored
local = ContentCatalog([build_entry(name="acmeEnrichment", type="connector",
                                    version="1.0.0", buildNumber=1, label="Acme")])
upstream.merge(local)                 # local overrides win on (type, name) collisions
upstream.write_tree("/srv")           # -> /srv/content-hub/content-hub.json + item dirs
```

{meth}`~pyfsr.content_catalog.ContentCatalog.from_url` crawls a live manifest over
HTTP; pass `cert=` (a combined PEM, or a `(cert, key)` pair) to present the
FortiCloud **FDN mutual-TLS client certificate** the official
`secops-content.forticloud.com` host requires. A plain mirror needs no cert.

```python
upstream = ContentCatalog.from_url("my-mirror.example.com")   # your own mirror
# official host needs the entitlement cert:
# ContentCatalog.from_url("secops-content.forticloud.com", cert=("fdn.pem", "fdn.key"))
```

{meth}`~pyfsr.content_catalog.ContentCatalog.write_tree` lays out exactly what the
appliance sync fetches:

```text
{root}/content-hub/content-hub.json
{root}/content-hub/{name}-{version}/{buildNumber}/info.json
{root}/content-hub/{name}-{version}/latest/info.json
{root}/content-hub/{name}-{version}/{buildNumber}/{name}-{version}.{zip|tgz}   # if artifacts=
```

## Running the mirror container

The container in `deploy/content-hub-mirror/` wraps all of the above: it builds
the merged tree at startup (and on every content change), serves it with nginx,
and — when configured — reverse-proxies un-overridden requests to an upstream
using the FDN cert.

```sh
cd deploy/content-hub-mirror
./build.sh                 # builds this checkout's pyfsr wheel + the image
docker compose up -d       # serves :8080 (http), :8443 (https), :9000 (admin)
./smoke-test.sh http://localhost:8080
```

With no upstream configured it serves **only your local content** (Option A).
To show Fortinet's catalog too, give it an upstream — either a saved snapshot
(no cert needed) or a live host (needs the FDN cert):

```sh
# a content-hub.json crawled once through an entitled box:
UPSTREAM_SNAPSHOT=/upstream/content-hub.json docker compose up -d
# ...or crawl the live entitled host (certs/fdn.pem + certs/fdn.key present):
UPSTREAM_HOST=secops-content.forticloud.com docker compose up -d
```

## Adding content — GUI, CLI, or files

Three ways, all of which merge into the served catalog and rebuild live (no
restart):

**Web GUI** — open `http://<mirror-host>:9000/`. Upload a `.tgz`/`.zip` (type +
metadata auto-detected) or fill fields manually; the table lists current content
with remove buttons.

**CLI (`chctl`)** — talks to the same admin API, so it works from your laptop
against a remote mirror:

```sh
export CHM_URL=http://<mirror-host>:9000
export CHM_TOKEN=<token>            # only if ADMIN_TOKEN is set on the mirror

chctl list
chctl add ./myConnector-1.0.0.tgz               # auto-detects type + metadata
chctl add-entry --name acme --type connector --version 1.0.0 --label "Acme"
chctl remove connector acme
```

**Drop files** — put entry JSON into `local-content/` and artifacts into
`artifacts/`, then `chctl rebuild`.

```{note}
The admin API on `:9000` is unauthenticated unless you set `ADMIN_TOKEN`. Set a
token (and bind to a trusted network) before exposing the mirror off localhost.
```

## Pointing a FortiSOAR appliance at the mirror

`setup-appliance.sh` does the appliance-side steps in one command (needs root on
the appliance): trust the mirror's TLS cert, set `product_yum_server` /
`fsr_os_server` to the mirror, enable `OFFLINEREPO`, restart php-fpm, and run the
sync.

```sh
# on the appliance:
sudo ./setup-appliance.sh <mirror-host>[:port]
# or over ssh from your laptop:
ssh <appliance> 'sudo bash -s' -- <mirror-host> < setup-appliance.sh
```

After it runs, the appliance's Content Hub reads from your mirror — your content
plus (if you seeded an upstream) Fortinet's. It is fully reversible:

```sh
sudo ./setup-appliance.sh --revert
```

```{note}
With `OFFLINEREPO=true` and **no upstream** configured, the appliance's Content
Hub shows only your local content and marks Fortinet's catalog absent. Seed an
upstream snapshot first if you want both visible. Downloading an *upstream*
artifact through the mirror requires the FDN client cert on the mirror (the
reverse-proxy path); your own artifacts serve directly.
```

## How it fits together

```text
 build_entry / entry_from_artifact ─┐
                                     ├─> ContentCatalog.merge ─> write_tree ─> /srv/content-hub/
 ContentCatalog.from_url(upstream) ─┘                                              │
                                                                                   ▼
                              nginx (mirror container)  ── GET content-hub.json ──> FortiSOAR sync
                                     │                                              (OFFLINEREPO=true)
                     un-overridden ──┴──> reverse-proxy ─> upstream (FDN mTLS cert)
```

See {mod}`pyfsr.content_catalog` for the full API and
`deploy/content-hub-mirror/README.md` for container operations.
