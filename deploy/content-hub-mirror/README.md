# Content Hub mirror (self-hosted repo, "see both" merge)

A single Docker container that serves a FortiSOAR **Content Hub** repo: your own
connectors / widgets / solution packs **plus** (optionally) Fortinet's official
store, unioned into one catalog. Point an appliance's `REPOSERVER` at it and
Content Hub shows both.

This is Option B of `docs/plans/CONTENT_HUB_SELF_HOSTED_REPO_PLAN.md`. The
mechanism was verified from cyops-api source on 8.0.0: the appliance sync reads
**one** manifest from **one** host —

```
GET https://{REPOSERVER}/content-hub/content-hub.json                         # catalog
GET https://{REPOSERVER}/content-hub/{name}-{ver}/{build}/info.json           # per item
GET https://{REPOSERVER}/content-hub/{name}-{ver}/{build}/{name}-{ver}.zip    # artifact
GET https://{OSSERVER}/content-hub/{name}-{ver}/{build}/images/fsr-icon-large.png
```

There is no native multi-repo, so "both" is produced as a **merged
`content-hub.json`**: this container builds it with `pyfsr.content_catalog`
(union of upstream + your local entries, local winning on `type`+`name`
collisions), serves your local content directly, and reverse-proxies any
un-overridden request to the upstream — presenting the FortiCloud **FDN mutual-TLS
client certificate** the official host requires.

## What's here

| file | role |
|------|------|
| `Dockerfile`        | python + nginx image; installs `pyfsr` (from `./wheels` if present) |
| `build_catalog.py`  | build step: merge upstream + `./local-content` + `./artifacts` → `/srv/content-hub/` |
| `admin/app.py`      | admin **GUI + JSON API** to add / remove content **and publish installable connectors** (Flask, port 9000) |
| `chctl`             | **CLI** for the admin API — add / list / remove / `add-connector` from your laptop |
| `connector_publish.py` | build an installable connector **RPM** from a source `.tgz` and publish it to the mirror (behind `add-connector`) |
| `connector-build/`  | the RPM recipe (`cyops-connector.spec.in` + `build.sh`) `connector_publish.py` uses; also a standalone rebuild tool |
| `setup-appliance.sh`| point a FortiSOAR box at this mirror (content-hub + connector yum `.repo`) |
| `entrypoint.sh`     | build catalog → seed connector repos → ensure TLS cert → generate `nginx.conf` → admin → nginx |
| `docker-compose.yml`| run config (ports, env, volume mounts) |
| `build.sh`          | build this checkout's pyfsr wheel into `./wheels`, then `docker build` |
| `smoke-test.sh`     | assert the fetch contract against a running mirror (no appliance) |
| `local-content/`    | your catalog entries (`*.json`); ships a sample connector |
| `certs/`            | `server.crt`/`server.key` (TLS) + `fdn.pem`/`fdn.key` (upstream proxy) |

## Quick start (local, no appliance)

```bash
./build.sh                        # builds the pyfsr wheel + the image
docker compose up -d              # serves on :80 (http) and :443 (https)
./smoke-test.sh http://localhost
```

> The compose file publishes the privileged ports `:80`/`:443` by default (an
> appliance's content-hub sync expects the mirror on `:443`). Override with
> `HTTP_PORT`/`HTTPS_PORT` — e.g. `HTTP_PORT=8080 HTTPS_PORT=8443 docker compose up -d`
> — for local dev where you can't bind `:80`.

`smoke-test.sh` fetches the merged manifest and confirms every entry's
`info.json` resolves at both its numbered-build and `latest/` path. With no
upstream configured you get **local-only** mode (Option A) — just your
`local-content/` entries.

> The published `pyfsr` on PyPI may not yet include `content_catalog`; `build.sh`
> builds a wheel from this checkout so the image always has it. Without it the
> Dockerfile falls back to `pip install pyfsr`.

## Adding your content

Three ways, easiest first. All of them end up merged into the served catalog and
(for uploaded artifacts) made downloadable — no restart needed.

### 1. Web GUI

Open **`http://<mirror-host>:9000/`**. Two forms:
- **Add by artifact** — upload a connector/widget `.tgz` or solution-pack `.zip`;
  type + metadata are auto-detected from the archive's `info.json`, and the file
  is staged as the downloadable artifact.
- **Add manually** — fill name / type / version / label / … for a
  catalog-only entry.

The table below lists current content with a **remove** button, plus a
**Rebuild** button. If the mirror has an `ADMIN_TOKEN`, append it once as
`http://host:9000/#<token>` (it's saved to localStorage).

### 2. CLI (`chctl`)

`chctl` talks to the same admin API, so it works from your laptop against a
remote mirror:

```bash
export CHM_URL=http://<mirror-host>:9000     # admin API base
export CHM_TOKEN=<token>                       # only if ADMIN_TOKEN is set

chctl list
chctl add ./myconn-1.0.0.tgz                   # auto-detects type + metadata
chctl add ./mypack-2.0.0.zip --type solutionpack --build 5
chctl add-entry --name acme --type connector --version 1.0.0 --label "Acme"
chctl remove connector acme
chctl rebuild
```

`chctl` ships inside the image too (`docker exec <container> chctl list`).

### 3. Drop files directly

Put one JSON file per entry into `local-content/` (a single entry object or a
list) and the matching `{name}-{version}.tgz`/`.zip` into `artifacts/`, then
`chctl rebuild` (or restart). Build entries with the SDK so the path fields are
correct — or skip this and just `chctl add` the artifact:

```python
from pyfsr.content_catalog import build_entry, entry_from_artifact
import json

# from an artifact (type + fields read from its info.json):
entry = entry_from_artifact("myconn-1.0.0.tgz")
# ...or by hand:
entry = build_entry(
    name="acmeEnrichment", type="connector", version="1.0.0", buildNumber=1,
    label="Acme Enrichment", publisher="Acme", category="Threat Intelligence",
    operations=[{"operation": "lookup", "title": "Lookup Indicator"}],
)
json.dump(entry, open("local-content/acmeEnrichment.json", "w"), indent=2)
```

## Publishing an *installable* connector

The section above adds a **catalog entry** — enough for the Content Hub UI to
*show* an item and hand out its metadata zip. **Installing** a connector on the
appliance needs more: under offline mode the appliance installs connectors as
**yum RPMs** (`cyops-connector-<name>`), not from a `.tgz`. So a catalog entry
alone lets the UI list your connector but its **Install** button will fail.

`add-connector` closes that loop. Hand it a connector **source tarball** (the
top-level `<name>/` dir with an `info.json`, i.e. `tar czf http.tgz http/`) and
the mirror does everything the offline install path needs:

1. builds a thin `cyops-connector-<name>-<version>-<release>.rpm` (payload = your
   tgz; the RPM's `%post` activates it) and drops it in the mirror's local yum
   repo (`createrepo_c`, priority-wins over upstream);
2. merges `<name>_<version> → {rpm_full_name}` into `connectors-all.json` (the
   map the installer reads to learn the exact RPM to pull);
3. stages the Content-Hub metadata zip at both `/{build}/` and `/latest/`.

You never touch `rpmbuild` — the mirror builds the RPM for you.

```bash
# CLI (from your laptop, against the admin API):
chctl add-connector ./http.tgz                 # build + publish the RPM
chctl add-connector ./http.tgz --release 5     # bump release to force a re-pull

# or the raw API:
curl -F tgz=@http.tgz -F release=5 http://<mirror-host>:9000/api/connector
```

The Web GUI exposes the same as an **Add connector (installable)** upload.

Then, on an appliance pointed at the mirror (see below), a normal
`client.connectors.install(name, version)` — or the Content Hub **Install**
button — pulls the RPM from the mirror and registers the connector. The RPM is
`BuildArch: noarch` (pure data + scriptlets), so it builds on any host arch and
still installs on the x86_64 appliance. This path is **live-verified on 8.0.0**
end to end (install → in-place update → the connector executes from the mirror).

> The `name`, `version`, and `release` are validated against a package-name-safe
> charset before they reach any path or the RPM spec, so an uploaded `info.json`
> can't traverse directories or inject spec/shell into the build.

For the underlying RPM recipe (and a standalone rebuild tool for rebranding an
existing upstream connector RPM at a different version), see
[`connector-build/README.md`](connector-build/README.md).

## Seeing Fortinet's store too ("both")

Give the mirror an upstream, one of two ways:

1. **Snapshot (no cert on this host).** Crawl the catalog once through an
   entitled appliance and save the `content-hub.json`, then:
   ```bash
   UPSTREAM_SNAPSHOT=/upstream/content-hub.json docker compose up -d
   ```
   (mount the file into the container and point `UPSTREAM_SNAPSHOT` at it).

2. **Live crawl (needs the FDN cert).** Put the appliance's FDN client cert at
   `certs/fdn.pem` + `certs/fdn.key` and set:
   ```bash
   UPSTREAM_HOST=secops-content.forticloud.com docker compose up -d
   ```

With `UPSTREAM_PROXY=1` (default), any per-item `info.json` / artifact / icon the
mirror doesn't have locally is reverse-proxied to `UPSTREAM_HOST` using that same
FDN cert — so the big upstream artifact zips stream through without mirroring
them. Set `UPSTREAM_PROXY=0` to serve strictly local content (404 on misses).

The **FDN cert lives on the appliance** at
`/opt/cyops-auth/certs/fdn_client_keystore.p12` (a PKCS#12; split into
cert/key PEMs with `openssl pkcs12`). It is per-appliance and entitlement-gated —
keep it out of git (see `.gitignore`).

## Pointing an appliance at the mirror

`setup-appliance.sh` does the whole switch-over on the FortiSOAR box (needs
sudo). Run it on the appliance, or pipe it in over ssh:

```bash
sudo ./setup-appliance.sh <mirror-host>[:port]
# or remotely:
ssh <appliance> 'sudo bash -s' -- <mirror-host> < setup-appliance.sh
```

It (1) points `product_yum_server`/`fsr_os_server` (REPOSERVER/OSSERVER) at the
mirror, (2) enables `OFFLINEREPO` (direct-HTTPS to your host, not FortiCloud) in
the php-fpm pool env, (3) writes `/etc/yum.repos.d/fsr-mirror-connectors.repo` —
the mirror's local connector repo at `priority=1` (so a custom
`cyops-connector-<name>-<ver>` installs over Fortinet's) plus the proxied
upstream at `priority=50`, with `metadata_expire=1` so a swapped RPM isn't served
stale — and (4) runs `csadm package content-hub sync --force`. Pass `--revert`
to undo it.

If the mirror uses a self-signed TLS cert (the default when none is mounted),
either install `certs/server.crt` in the appliance trust store or provide a cert
the box already trusts. Full env-var propagation chain:
`Miscellaneous/fortisoar/troubleshooting/tools/fsr_diagnose.sh` §content-hub.

## Environment variables

| var | default | meaning |
|-----|---------|---------|
| `UPSTREAM_SNAPSHOT` | — | path to a saved upstream `content-hub.json` (preferred; no cert) |
| `UPSTREAM_HOST`     | — | live upstream host to crawl at build (needs FDN cert) |
| `UPSTREAM_PROXY`    | `1` | reverse-proxy cache-misses to upstream (`0` = 404 them) |
| `TLS_VERIFY`        | `1` | verify upstream TLS during the crawl (`0` = self-signed) |
| `FDN_CERT`/`FDN_KEY`| `/etc/nginx/certs/fdn.{pem,key}` | FDN client cert for the upstream |
| `SERVER_CERT`/`SERVER_KEY` | `/etc/nginx/certs/server.{crt,key}` | mirror's TLS cert (self-signed if absent) |
| `LOCAL_CONTENT_DIR` | `/local-content` | dir of your entry JSON files |
| `ARTIFACTS_DIR`     | `/artifacts` | dir of downloadable `{name}-{version}.tgz/.zip` |
| `OUTPUT_DIR`        | `/srv` | where the served `content-hub/` tree is written |
| `ADMIN_ENABLED`     | `1` | run the admin GUI/API (`0` to disable) |
| `ADMIN_TOKEN`       | — | require `Bearer <token>` on the admin API (set off-localhost) |
| `ADMIN_PORT`        | `9000` | admin GUI/API port |
| `CONNECTORS_PROXY`  | `1` | reverse-proxy connector-RPM misses to the public Fortinet connector repo (`0` = local only) |
| `CONNECTORS_UPSTREAM` | `repo.fortisoar.fortinet.com` | public upstream connector yum host (no cert) |
| `CONNECTORS_UPSTREAM_PATH` | `/prod/connectors` | path to the upstream connector repo |
| `CONNECTORS_PREFETCH` | — | space-separated `cyops-connector-*` RPM URLs to prefetch into the local repo at start |

The connector RPMs published via `add-connector` live under the mounted
`./connectors-local` volume; `./published` persists the staged metadata zips and
merged `connectors-all.json` across restarts.

## Status / caveats

- Fetch contract (manifest + per-item `info.json`) is **validated locally** by
  `smoke-test.sh`; merge + local-override-wins verified with a captured live
  sample catalog as a stand-in upstream.
- **Live-verified on 8.0.0** end to end: `OFFLINEREPO` catalog sync from the
  mirror, and the full installable-connector loop (`add-connector` → the
  appliance installs the RPM from the mirror → in-place version update → the
  connector executes).
- Overriding an *existing* upstream item requires the entry's `category` to be a
  valid "Solution Pack Category" (else the appliance's bulkupsert silently
  rejects the whole entry with `FSR_CH_0000001`), and a `publishedDate` newer
  than upstream's for a scheduled (non-`--force`) sync to overwrite it.
- Online-mode upstream path mapping (whether the official host serves under
  `/content-hub/` vs `/content/`) may need a `proxy_pass` rewrite; the snapshot
  path avoids this and is the recommended first deployment.
