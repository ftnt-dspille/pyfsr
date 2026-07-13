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
| `admin/app.py`      | admin **GUI + JSON API** to add / remove content (Flask, port 9000) |
| `chctl`             | **CLI** for the admin API — add / list / remove from your laptop |
| `entrypoint.sh`     | build catalog → ensure TLS cert → generate `nginx.conf` → admin → nginx |
| `docker-compose.yml`| run config (ports, env, volume mounts) |
| `build.sh`          | build this checkout's pyfsr wheel into `./wheels`, then `docker build` |
| `smoke-test.sh`     | assert the fetch contract against a running mirror (no appliance) |
| `local-content/`    | your catalog entries (`*.json`); ships a sample connector |
| `certs/`            | `server.crt`/`server.key` (TLS) + `fdn.pem`/`fdn.key` (upstream proxy) |

## Quick start (local, no appliance)

```bash
./build.sh                        # builds the pyfsr wheel + the image
docker compose up -d              # serves on :8080 (http) and :8443 (https)
./smoke-test.sh http://localhost:8080
```

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

On the FortiSOAR box (needs sudo):

```bash
# /etc/environment
product_yum_server=<mirror-host>
# enable direct-HTTPS offline mode so it talks to your host, not FortiCloud
# (OFFLINEREPO=true in the php-fpm env), then:
sudo systemctl restart php-fpm
sudo csadm package content-hub sync --force
```

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

## Status / caveats

- Fetch contract (manifest + per-item `info.json`) is **validated locally** by
  `smoke-test.sh`; merge + local-override-wins verified with the captured live
  sample catalog as a stand-in upstream.
- **Not yet run against a real appliance sync** — that needs sudo / a scratch box
  (`OFFLINEREPO` end-to-end confirm is the last open item in the plan doc).
- Online-mode upstream path mapping (whether the official host serves under
  `/content-hub/` vs `/content/`) may need a `proxy_pass` rewrite; the snapshot
  path avoids this and is the recommended first deployment.
