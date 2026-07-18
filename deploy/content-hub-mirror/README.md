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

## What it proxies (the "see both" promise)

The mirror is the single `REPOSERVER` for the appliance. Anything it doesn't
host locally it proxies, so the appliance sees one merged store:

| path on the mirror | served from | needs a cert? |
|---|---|---|
| `/content-hub/content-hub.json`        | local merged manifest (always) | — |
| `/content-hub/<name>-<ver>/...`        | local first, then `UPSTREAM_HOST` (Option B fallback) | FDN mTLS for the entitled host; **plain HTTPS for any other upstream** (e.g. the public `repo.fortisoar.fortinet.com`, or another mirror) |
| `/fsr-widgets/<name>-<ver>/...`        | `repo.fortisoar.fortinet.com` (widget `.tgz` long tail) | no cert (public host) |
| `/widgets/<name>-<ver>/...`            | `repo.fortisoar.fortinet.com` (older widget `.tgz`) | no cert |
| `/xf-widgets/`, `/xf/widgets/...`      | `repo.fortisoar.fortinet.com` (alt widget paths) | no cert |
| `/xf/solutions/solutionpacks/...`      | `repo.fortisoar.fortinet.com` (SP `.zip` long tail) | no cert |
| `/xf/solutions/connectors/...`         | `repo.fortisoar.fortinet.com` (connector `.tgz` long tail) | no cert |
| `/connectors/x86_64/...`               | `repo.fortisoar.fortinet.com` connector RPM yum repo | no cert |
| `/connectors/info/connectors-all.json` | local **merged** map (Fortinet's + your overrides) | — |
| `/connectors-local/x86_64/...`         | local override RPM repo (your `chctl add-connector` output) | — |

Set `PUBLIC_PROXY=0` to 404 anything the mirror doesn't host locally (Option A,
strict local-only). Set `UPSTREAM_PROXY=0` to disable just the
`/content-hub/` fallback while keeping the widget/SP/connector long-tail proxies.

## What's here

| file | role |
|------|------|
| `Dockerfile`        | python + nginx image; installs `pyfsr` (from `./wheels` if present) |
| `build_catalog.py`  | build step: merge upstream + `./local-content` + `./artifacts` → `/srv/content-hub/` |
| `admin/app.py`      | admin **GUI + JSON API** to add / remove content **and publish installable connectors** (Flask, port 9000) |
| `chctl`             | **CLI** for the admin API — add / list / remove / `add-connector` from your laptop |
| `connector_publish.py` | build an installable connector **RPM** from a source `.tgz` and publish it to the mirror (behind `add-connector`) |
| `connector-build/`  | the RPM recipe (`cyops-connector.spec.in` + `build.sh`) `connector_publish.py` uses; also a standalone rebuild tool |
| `setup-appliance.sh`| point a FortiSOAR box at this mirror — installs + verifies TLS trust, env vars, connector repo, sync (see below) |
| `entrypoint.sh`     | build catalog → seed connector repos → ensure TLS cert → generate `nginx.conf` → admin → nginx |
| `docker-compose.yml`| run config (ports, env, volume mounts) |
| `build.sh`          | build this checkout's pyfsr wheel into `./wheels`, then `docker build` |
| `smoke-test.sh`     | assert the catalog fetch contract against a running mirror (no appliance) |
| `smoke-test-connector.sh` | publish a throwaway connector and assert every offline-install artifact resolves (RPM, `connectors-all.json`, metadata zip) |
| `smoke-test-proxy.sh` | assert the widget/SP/connector-tgz long-tail paths proxy to the public Fortinet repo (no cert, no appliance) |
| `local-content/`    | your catalog entries (`*.json`); ships a sample connector |
| `certs/`            | `server.crt`/`server.key` (TLS) + `fdn.pem`/`fdn.key` (upstream proxy) |

## Quick start (local, no appliance)

```bash
./build.sh                        # builds the pyfsr wheel + the image
docker compose up -d              # serves on :80 (http) and :443 (https)
./smoke-test.sh http://localhost          # catalog fetch contract (manifest + info.json)
./smoke-test-connector.sh http://localhost http://localhost:9000   # installable-connector publish path
./smoke-test-proxy.sh http://localhost    # widget/SP/connector-tgz long-tail proxy (needs network)
```

> The compose file publishes the privileged ports `:80`/`:443` by default (an
> appliance's content-hub sync expects the mirror on `:443`). Override with
> `HTTP_PORT`/`HTTPS_PORT` — e.g. `HTTP_PORT=8080 HTTPS_PORT=8443 docker compose up -d`
> — for local dev where you can't bind `:80`.

`smoke-test.sh` fetches the merged manifest and confirms every entry's
`info.json` resolves at both its numbered-build and `latest/` path. With no
upstream configured you get **local-only** mode (Option A) — just your
`local-content/` entries. `smoke-test-proxy.sh` exercises the public-repo
reverse-proxy paths (widget `.tgz`, SP `.zip`, connector `.tgz` long tails) and
needs network egress to `repo.fortisoar.fortinet.com`.

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

`./smoke-test-connector.sh [BASE_URL] [ADMIN_URL]` runs this whole path against a
running mirror — it builds a throwaway connector, publishes it, and asserts the
RPM, the `connectors-all.json` entry, and the staged metadata zip all resolve
over HTTP, so you can confirm a from-scratch container reproduces the publish
state without touching an appliance.

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

The mirror proxies everything it doesn't host, so the appliance always sees a
single merged store. Three layers of upstream:

1. **Catalog merge (no cert, recommended first deploy).** Crawl the catalog
   once through an entitled appliance and save the `content-hub.json`, then:
   ```bash
   UPSTREAM_SNAPSHOT=/upstream/content-hub.json docker compose up -d
   ```
   (mount the file into the container and point `UPSTREAM_SNAPSHOT` at it).
   The merged manifest is always served locally; per-item `info.json` / artifact
   paths fall through to the proxies below.

2. **Live catalog crawl (needs the FDN cert).** Put the appliance's FDN client
   cert at `certs/fdn.pem` + `certs/fdn.key` and set:
   ```bash
   UPSTREAM_HOST=secops-content.forticloud.com docker compose up -d
   ```

3. **Long-tail proxy (no cert, always on by default).** Whatever the catalog
   says, any per-item fetch the mirror doesn't have locally is reverse-proxied
   to the **public** Fortinet repo (`repo.fortisoar.fortinet.com` — open HTTPS,
   no FDN cert) for the widget `.tgz` (`/fsr-widgets/`, `/widgets/`), SP `.zip`
   (`/xf/solutions/solutionpacks/`), connector `.tgz` (`/xf/solutions/connectors/`),
   and connector RPM (`/connectors/x86_64/`) long tails. With `UPSTREAM_PROXY=1`
   the `/content-hub/<name>-<ver>/...` fallback also goes through, so a miss
   there is transparently fetched from `UPSTREAM_HOST` — presenting the FDN mTLS
   cert if present, **or plain HTTPS if not** (e.g. pointing `UPSTREAM_HOST`
   at the public repo for the connector bundles it carries under `/content-hub/`).

With `UPSTREAM_PROXY=1` (default), any per-item `info.json` / artifact / icon the
mirror doesn't have locally is reverse-proxied to `UPSTREAM_HOST` — using the
FDN client cert if present, **or plain HTTPS if not** — so the big upstream
artifact zips stream through without mirroring them. Set `UPSTREAM_PROXY=0` to
serve strictly local content for `/content-hub/` paths (404 on misses, but keep
the widget/SP/connector long-tail proxies — set `PUBLIC_PROXY=0` to disable
those too).

The **FDN cert lives on the appliance** at
`/opt/cyops-auth/certs/fdn_client_keystore.p12` (a PKCS#12; split into
cert/key PEMs with `openssl pkcs12`). It is per-appliance and entitlement-gated —
keep it out of git (see `.gitignore`).

> The public-repo proxy + a snapshot catalog gets you "both Fortinet's store
> and mine" with **no FDN cert on the mirror at all** — the cert is only
> needed to crawl the entitled catalog live or to proxy to
> `secops-content.forticloud.com` itself.

## Pointing an appliance at the mirror

`setup-appliance.sh` does the whole switch-over on the FortiSOAR box (needs
sudo). Run it on the appliance, or pipe it in over ssh:

```bash
sudo ./setup-appliance.sh <mirror-host>[:port]
# or remotely:
ssh <appliance> 'sudo bash -s' -- <mirror-host> < setup-appliance.sh
```

It runs **seven steps**, each reversible with `--revert`:

1. **Trusts the mirror's TLS cert** in the OS trust store (`update-ca-trust
   extract` on Rocky). *This is the step the original setup was missing in
   practice* — the content-hub sync skips TLS verify (so it succeeds either
   way), but the `solutionpacks/install` endpoint **does** verify TLS, and a
   self-signed mirror's cert that isn't trusted here is the root cause of the
   misleading `Please check the network connection to <mirror>` error.
2. **Verifies the trust works** — a real TLS handshake against the mirror with
   the OS trust store, *before* anything else is touched. Fails loudly with
   the actual cause (trust) instead of the runtime symptom (network).
3. Sets `product_yum_server`/`fsr_os_server` (REPOSERVER/OSSERVER) at the mirror
4. Enables `OFFLINEREPO` (direct-HTTPS to your host, not FortiCloud) in
   `/etc/environment` **and** the php-fpm pool env
5. Writes `/etc/yum.repos.d/fsr-mirror-connectors.repo` — the mirror's local
   connector repo at `priority=1` (so a custom `cyops-connector-<name>-<ver>`
   installs over Fortinet's) plus the proxied upstream at `priority=50`, with
   `metadata_expire=1` so a swapped RPM isn't served stale
6. Restarts `php-fpm`
7. Runs `csadm package content-hub sync --force`
8. **Post-sync verification** — a verified HTTPS GET of the catalog + one
   per-item `info.json` using the OS trust store. This is the exact code path
   the SP install endpoint uses; if it fails here, the SP install would have
   failed too, and you get the real cause instead of a runtime misdirection.

Options:

| flag | effect |
|---|---|
| `--cert-file <path>` | trust this cert instead of fetching one from the mirror (use when the mirror isn't reachable from the box yet, or its chain is split across files) |
| `--check` | read-only: verify the mirror is trusted + env is set, then exit (non-zero if anything is missing). Re-run any time. |
| `--no-verify` | skip the post-trust TLS verification (NOT recommended — this is exactly the step that catches a bad trust install before the SP install path hits it) |
| `--insecure` | don't hard-fail if the cert fetch fails AND skip the post-trust TLS verification — lets the setup proceed to the sync. The SP install path WILL still fail at runtime — only use for a quick "is the mirror up" check. |
| `--revert` | restore the pre-mirror state (env, trust, repos, php-fpm) |

If the mirror uses a self-signed TLS cert (the default when none is mounted),
either install `certs/server.crt` in the appliance trust store (the script does
this automatically by fetching the cert from the mirror) or provide a cert the
box already trusts. Full env-var propagation chain:
`Miscellaneous/fortisoar/troubleshooting/tools/fsr_diagnose.sh` §content-hub.

## Environment variables

| var | default | meaning |
|-----|---------|---------|
| `UPSTREAM_SNAPSHOT` | — | path to a saved upstream `content-hub.json` (preferred; no cert) |
| `UPSTREAM_HOST`     | — | live upstream host to crawl + proxy `/content-hub/` misses to |
| `UPSTREAM_PROXY`    | `1` | reverse-proxy `/content-hub/` cache-misses to `UPSTREAM_HOST` (`0` = 404 them) |
| `UPSTREAM_TLS_VERIFY` | `1` | verify `UPSTREAM_HOST` TLS at proxy time (set `0` ONLY for a self-signed mirror-of-a-mirror) |
| `TLS_VERIFY`        | `1` | verify upstream TLS during the build-time crawl (`0` = self-signed) |
| `FDN_CERT`/`FDN_KEY`| `/etc/nginx/certs/fdn.{pem,key}` | FDN client cert — present → mTLS to `UPSTREAM_HOST`; absent → plain HTTPS to it |
| `SERVER_CERT`/`SERVER_KEY` | `/etc/nginx/certs/server.{crt,key}` | mirror's TLS cert (self-signed if absent) |
| `PUBLIC_PROXY`     | `1` | reverse-proxy widget `.tgz` / SP `.zip` / connector `.tgz` long-tail paths to the public Fortinet repo (`0` = 404 them) |
| `PUBLIC_FORTINET_HOST` | `repo.fortisoar.fortinet.com` | public host serving the widget/SP/connector long tail (no cert needed) |
| `LOCAL_CONTENT_DIR` | `/local-content` | dir of your entry JSON files |
| `ARTIFACTS_DIR`     | `/artifacts` | dir of downloadable `{name}-{version}.tgz/.zip` |
| `OUTPUT_DIR`        | `/srv` | where the served `content-hub/` tree is written |
| `ADMIN_ENABLED`     | `1` | run the admin GUI/API (`0` to disable) |
| `ADMIN_TOKEN`       | — | require `Bearer <token>` on the admin API (set off-localhost) |
| `ADMIN_PORT`        | `9000` | admin GUI/API port |
| `CONNECTORS_PROXY`  | follows `PUBLIC_PROXY` | reverse-proxy connector-RPM misses to the public Fortinet connector repo (`0` = local only) |
| `CONNECTORS_UPSTREAM` | `repo.fortisoar.fortinet.com` | public upstream connector yum host (no cert) |
| `CONNECTORS_UPSTREAM_PATH` | `/prod/connectors` | path to the upstream connector repo |
| `CONNECTORS_PREFETCH` | — | space-separated `cyops-connector-*` RPM filenames to prefetch into the local repo at start |

The connector RPMs published via `add-connector` live under the mounted
`./connectors-local` volume; `./published` persists the staged metadata zips and
merged `connectors-all.json` across restarts.

## Status / caveats

- Fetch contract (manifest + per-item `info.json`) is **validated locally** by
  `smoke-test.sh`; merge + local-override-wins verified with a captured live
  sample catalog as a stand-in upstream. The long-tail proxy paths
  (widget `.tgz`, SP `.zip`, connector `.tgz`, connector RPM) are validated
  locally by `smoke-test-proxy.sh` against the **live public Fortinet repo**
  (no appliance, no FDN cert).
- **Live-verified on 8.0.0** end to end: `OFFLINEREPO` catalog sync from the
  mirror, and the full installable-connector loop (`add-connector` → the
  appliance installs the RPM from the mirror → in-place version update → the
  connector executes).
- `setup-appliance.sh` now installs + **verifies** the mirror's TLS cert before
  the sync runs, with a `--check` mode for ongoing verification — closes the
  "SP install fails with a misleading 'network connection' error" gap in the
  prior setup (the content-hub sync skips TLS verify, so a missing trust was
  invisible until a SP install).
- Overriding an *existing* upstream item requires the entry's `category` to be a
  valid "Solution Pack Category" (else the appliance's bulkupsert silently
  rejects the whole entry with `FSR_CH_0000001`), and a `publishedDate` newer
  than upstream's for a scheduled (non-`--force`) sync to overwrite it.
- Online-mode upstream path mapping (whether the official entitled host serves
  under `/content-hub/` vs `/content/`) may need a `proxy_pass` rewrite if you
  point `UPSTREAM_HOST` at `secops-content.forticloud.com`; the snapshot path
  + the public-repo long-tail proxy avoid this and are the recommended first
  deployment.
