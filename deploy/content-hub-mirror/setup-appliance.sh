#!/usr/bin/env bash
# Point a FortiSOAR appliance at a self-hosted Content Hub mirror — one command.
#
# Run ON the appliance as root:
#     sudo ./setup-appliance.sh <mirror-host>[:port]
# or FROM your laptop over ssh:
#     ssh <appliance> 'sudo bash -s' -- <mirror-host>[:port] < setup-appliance.sh
#
# What it does (all reversible with --revert):
#   1. trusts the mirror's TLS cert — required for the solutionpacks/install
#      path, which (unlike the content-hub sync) DOES verify TLS. A cert that
#      isn't trusted here is the root cause of the misleading
#      "Please check the network connection to <mirror>" error.
#   2. verifies the trust actually works (a TLS handshake against the mirror
#      using the OS trust store, before anything else is touched).
#   3. sets product_yum_server + fsr_os_server (REPOSERVER/OSSERVER) to the mirror
#   4. enables OFFLINEREPO (direct-HTTPS to your host, not FortiCloud)
#   5. restarts php-fpm so workers pick up the new env
#   6. runs `csadm package content-hub sync --force`
#   7. post-sync verification: the catalog + a per-item info.json both resolve
#      over HTTPS with the OS trust store — proves the SP install path will
#      trust the mirror too.
#
# Options:
#   --cert-file <path>   trust this cert instead of fetching one from the mirror
#                        (use when the mirror isn't reachable yet from the box,
#                        or its cert chain is split across files)
#   --no-verify          skip the post-trust TLS verification (NOT recommended —
#                        this is exactly the step that catches a bad trust install
#                        before the SP install path hits it at runtime)
#   --check              verify the mirror is trusted + env is set, then exit
#                        (no changes; non-zero exit if anything is missing)
#   --revert             restore the pre-mirror state and point the box back at
#                        the public Fortinet repo: /etc/environment, the php-fpm
#                        pool env, /etc/yum/vars/product_yum_server, the trust
#                        anchor and the connector repo file — then verify no
#                        OFFLINEREPO/mirror config survives and re-sync Content
#                        Hub from upstream. Restoring the pool conf matters: its
#                        env[REPOSERVER] overrides /etc/environment, so a revert
#                        that skips it leaves the box on the mirror.
#   --no-sync            with --revert, skip the closing content-hub sync
#   --insecure           don't hard-fail if the cert fetch fails AND skip the
#                        post-trust TLS verification — lets the setup proceed
#                        all the way to the content-hub sync (which itself
#                        skips TLS verify). The SP install path WILL still
#                        fail at runtime — only use this for a quick "is the
#                        mirror up" check, never for a real deployment.
#
# The originals are backed up to /root/content-hub-mirror-backup/ so --revert
# restores them.
set -euo pipefail

BACKUP=/root/content-hub-mirror-backup
ENVF=/etc/environment
CERT_BACKUP="$BACKUP/mirror-cert-path"

die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARNING: $*" >&2; }
say() { echo "  $*"; }

[[ $EUID -eq 0 ]] || die "must run as root (use sudo)"

# ---- arg parse -------------------------------------------------------------
MIRROR=""
CERT_FILE=""
NO_VERIFY=0
CHECK_ONLY=0
INSECURE=0
NO_SYNC=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --revert)    ACTION=revert; shift;;
    --no-sync)   NO_SYNC=1; shift;;
    --cert-file) CERT_FILE="$2"; shift 2;;
    --no-verify) NO_VERIFY=1; shift;;
    --check)    CHECK_ONLY=1; shift;;
    --insecure) INSECURE=1; NO_VERIFY=1; shift;;   # --insecure implies --no-verify
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' >&2; exit 0;;
    *) MIRROR="$1"; shift;;
  esac
done

# ---- --check (read-only) ---------------------------------------------------
if [[ "$CHECK_ONLY" == "1" ]]; then
  echo "==> checking Content Hub mirror setup on this appliance"
  rc=0
  MIRROR="${MIRROR:-$(awk -F= '/^product_yum_server=/{print $2}' "$ENVF" 2>/dev/null || true)}"
  if [[ -z "$MIRROR" ]]; then
    warn "product_yum_server is not set — mirror is not configured"; rc=1
  else
    say "product_yum_server = $MIRROR"
  fi
  OFFLINE="$(awk -F= '/^OFFLINEREPO=/{print $2}' "$ENVF" 2>/dev/null || true)"
  [[ "$OFFLINE" == "true" ]] || { warn "OFFLINEREPO is not 'true'"; rc=1; }
  say "OFFLINEREPO = ${OFFLINE:-<unset>}"
  if [[ -f "$CERT_BACKUP" ]] && [[ -f "$(cat "$CERT_BACKUP")" ]]; then
    say "mirror cert installed at $(cat "$CERT_BACKUP")"
  else
    warn "no mirror cert recorded — run setup without --check first"; rc=1
  fi
  # The real test: does a TLS handshake against the mirror verify with the
  # OS trust store? This is exactly what the SP install path does.
  if [[ -n "$MIRROR" ]]; then
    HOST="${MIRROR%%:*}"; PORT="${MIRROR##*:}"; [[ "$MIRROR" == *:* ]] || PORT=443
    # `timeout` because openssl s_client has no built-in connect timeout and
    # hangs on an unreachable host (e.g. a mis-typed IP).
    if echo | timeout 10 openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" \
         2>/dev/null | grep -q "Verify return code: 0"; then
      say "TLS verify against ${MIRROR}: OK"
    else
      warn "TLS verify against ${MIRROR}: FAILED — the SP install path will reject the mirror"
      rc=1
    fi
  fi
  if [[ -f /etc/yum.repos.d/fsr-mirror-connectors.repo ]]; then
    say "connector repo configured: /etc/yum.repos.d/fsr-mirror-connectors.repo"
  else
    warn "connector repo not configured"; rc=1
  fi
  if [[ $rc -eq 0 ]]; then
    echo "==> check passed"
  else
    echo "==> check FAILED (see above)"
    exit $rc
  fi
  exit 0
fi

# ---- revert ----------------------------------------------------------------
if [[ "${ACTION:-}" == "revert" ]]; then
  echo "==> reverting Content Hub mirror config"
  [[ -d "$BACKUP" ]] || die "no backup at $BACKUP — this box was never set up by this script,
  or the backup was removed. Restore /etc/environment and the php-fpm pool conf by hand."

  # 1. /etc/environment (product_yum_server / fsr_os_server / OFFLINEREPO)
  if [[ -f "$BACKUP/environment" ]]; then
    cp "$BACKUP/environment" "$ENVF"; say "restored $ENVF"
  else
    warn "no environment backup; stripping the mirror lines instead"
    sed -i -E '/^(product_yum_server|fsr_os_server|OFFLINEREPO|REPOSERVER|OSSERVER)=/d' "$ENVF"
  fi

  # 2. the php-fpm pool conf. Setup appends env[REPOSERVER]/env[OSSERVER]/
  # env[OFFLINEREPO] here; without restoring it php-fpm keeps serving the
  # mirror even after /etc/environment is clean — the pool env wins.
  restored_pool=0
  for pool in /etc/php-fpm.d/*.conf; do
    [[ -e "$pool" ]] || continue
    if [[ -f "$BACKUP/$(basename "$pool")" ]]; then
      cp "$BACKUP/$(basename "$pool")" "$pool"; say "restored $pool"; restored_pool=1
    fi
  done
  if [[ "$restored_pool" == "0" ]]; then
    warn "no php-fpm pool backup; stripping the mirror env lines instead"
    sed -i -E '/^env\[(OFFLINEREPO|REPOSERVER|OSSERVER)\]/d' /etc/php-fpm.d/*.conf 2>/dev/null || true
  fi

  # 3. the yum var (connector RPM path)
  if [[ -f "$BACKUP/yum-var-product_yum_server" ]]; then
    cp "$BACKUP/yum-var-product_yum_server" /etc/yum/vars/product_yum_server
    say "restored /etc/yum/vars/product_yum_server"
  elif [[ -f /etc/yum/vars/product_yum_server ]]; then
    # Fall back to whatever the restored /etc/environment now says.
    orig="$(awk -F= '/^product_yum_server=/{print $2}' "$ENVF" 2>/dev/null || true)"
    if [[ -n "$orig" ]]; then
      echo "$orig" > /etc/yum/vars/product_yum_server
      say "reset /etc/yum/vars/product_yum_server -> $orig (from $ENVF)"
    else
      warn "could not determine the original product_yum_server; check /etc/yum/vars/product_yum_server by hand"
    fi
  fi

  # 4. trust anchor + connector repos
  if [[ -f "$CERT_BACKUP" ]]; then
    cert=$(cat "$CERT_BACKUP")
    rm -f "$cert" "$CERT_BACKUP"; say "removed $cert"
    command -v update-ca-trust >/dev/null && update-ca-trust extract >/dev/null 2>&1 \
      || update-ca-certificates --fresh >/dev/null 2>&1 || true
  fi
  rm -f /etc/yum.repos.d/fsr-mirror-connectors.repo; say "removed connector repo"

  # 5. php-fpm picks up the restored pool env
  systemctl restart php-fpm; say "restarted php-fpm"

  # 6. assert nothing still points at a mirror — a silent partial revert is the
  # failure mode this whole block exists to prevent.
  leftover=0
  grep -qE '^OFFLINEREPO=true' "$ENVF" 2>/dev/null && { warn "OFFLINEREPO=true still in $ENVF"; leftover=1; }
  grep -rqE '^env\[OFFLINEREPO\]' /etc/php-fpm.d/ 2>/dev/null && { warn "env[OFFLINEREPO] still in a php-fpm pool"; leftover=1; }
  [[ -f /etc/yum.repos.d/fsr-mirror-connectors.repo ]] && { warn "connector repo still present"; leftover=1; }
  [[ $leftover -eq 0 ]] || die "revert INCOMPLETE — see warnings above"
  say "verified: no OFFLINEREPO / mirror repo left on this box"
  say "repo host is now: $(awk -F= '/^product_yum_server=/{print $2}' "$ENVF" 2>/dev/null || echo '<unset>')"

  # 7. repull the upstream catalog so Content Hub matches the restored host
  if [[ "$NO_SYNC" == "1" ]]; then
    warn "skipping the post-revert sync (--no-sync); run 'csadm package content-hub sync --force' yourself"
  else
    echo "==> re-syncing Content Hub from the restored (public) repo"
    csadm package content-hub sync --force \
      || die "revert applied, but the sync failed — run 'csadm package content-hub sync --force' by hand"
  fi

  echo
  echo "==> reverted."
  echo "NOTE: the sync does NOT remove solutionpacks rows the mirror inserted. Clear them with"
  echo "  DELETE /api/3/delete/solutionpacks {\"ids\":[...], \"nonLocalNonRepoSpClean\":true}"
  echo "A row with installed:true reflects a LOCALLY INSTALLED connector, not a catalog"
  echo "leftover — it reappears on every sync until you remove that connector's RPM."
  exit 0
fi

[[ -n "$MIRROR" ]] || die "usage: setup-appliance.sh <mirror-host>[:port]  (or --revert / --check)"
HOSTPORT="$MIRROR"
if [[ "$MIRROR" == *:* ]]; then HOST="${MIRROR%%:*}"; PORT="${MIRROR##*:}"; else HOST="$MIRROR"; PORT=443; fi
mkdir -p "$BACKUP"

# ---- [1/7] trust the mirror's TLS certificate ------------------------------
echo "==> [1/7] trusting the mirror's TLS certificate"
# The solutionpacks/install endpoint (and any Guzzle/stream client with TLS
# verify on) checks the OS trust store. A self-signed mirror's cert must be
# installed here, or the SP install path fails with a misleading "network
# connection" error. The content-hub sync happens to skip TLS verify, so a
# bad trust install is invisible until a SP install is attempted.
if command -v update-ca-trust >/dev/null 2>&1; then          # RHEL/Rocky (FortiSOAR)
  CERT_DIR=/etc/pki/ca-trust/source/anchors; EXTRACT="update-ca-trust extract"
else                                                          # Debian/Ubuntu
  CERT_DIR=/usr/local/share/ca-certificates; EXTRACT="update-ca-certificates"
fi
mkdir -p "$CERT_DIR"; CERT_PATH="$CERT_DIR/content-hub-mirror-${HOST}.crt"

if [[ -n "$CERT_FILE" ]]; then
  # Caller provided the cert (e.g. the mirror's mounted server.crt, or a
  # chain split across files). Trust it directly without fetching.
  [[ -f "$CERT_FILE" ]] || die "cert file not found: $CERT_FILE"
  cp "$CERT_FILE" "$CERT_PATH"
  $EXTRACT >/dev/null 2>&1 || die "update-ca-trust/certificates failed"
  echo "$CERT_PATH" > "$CERT_BACKUP"
  say "trusted provided cert: $CERT_FILE -> $CERT_PATH"
else
  # Pull the leaf cert the mirror presents and add it to the OS trust store.
  # Harmless for a mirror that already uses a publicly-trusted cert (the
  # extract is a no-op then). openssl s_client doesn't need the cert trusted
  # to *fetch* it — only to *verify* it — so this works even before trust.
  # `timeout` because s_client has no connect timeout and hangs on an
  # unreachable host.
  if echo | timeout 10 openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" 2>/dev/null \
       | openssl x509 > "$CERT_PATH" 2>/dev/null && [[ -s "$CERT_PATH" ]]; then
    $EXTRACT >/dev/null 2>&1 || die "update-ca-trust/certificates failed"
    echo "$CERT_PATH" > "$CERT_BACKUP"
    say "trusted cert fetched from ${HOST}:${PORT} -> $CERT_PATH"
  else
    rm -f "$CERT_PATH"
    if [[ "$INSECURE" == "1" ]]; then
      warn "could not fetch a cert from the mirror on ${HOST}:${PORT}; --insecure given so continuing"
      warn "  the content-hub sync will work (it skips TLS verify), but the SP install path WILL fail"
    else
      die "could not fetch a cert from the mirror on ${HOST}:${PORT}.
  Either:
    (a) make sure the mirror is reachable on that port and presents a cert, or
    (b) re-run with --cert-file <path> pointing at the mirror's server.crt, or
    (c) re-run with --insecure to skip this check (NOT recommended — the SP
        install path will fail at runtime with a misleading 'network
        connection' error)."
    fi
  fi
fi

# ---- [2/7] verify the trust actually works ---------------------------------
echo "==> [2/7] verifying the mirror's TLS cert is trusted by this box"
# This is the exact check the SP install path performs at runtime: a TLS
# handshake with the OS trust store. Doing it here lets us fail loudly BEFORE
# touching /etc/environment or restarting php-fpm, with a message that names
# the actual problem (trust) rather than the misleading runtime symptom
# ("network connection to <mirror>").
if [[ "$NO_VERIFY" == "1" ]]; then
  warn "skipping TLS verification (--no-verify)"
elif ! echo | timeout 10 openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" \
     2>/dev/null | grep -q "Verify return code: 0"; then
  # show the actual openssl error so the operator can see why
  echo | timeout 10 openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" 2>&1 \
    | grep -E "Verify|verify|error" | head -5 >&2
  die "TLS verify against ${HOST}:${PORT} failed even after installing the cert.
  The SP install path will reject the mirror. Common causes:
    - the mirror presents a different cert than the one installed (regenerate
      and re-run, or use --cert-file with the actual cert file)
    - the cert chain is split across files (concatenate them and use --cert-file)
    - the cert is for a different hostname (use a SAN/cert matching ${HOST})"
else
  say "TLS verify against ${HOST}:${PORT}: OK (OS trust store accepts the mirror)"
fi

# ---- [3/7] point REPOSERVER/OSSERVER at the mirror -------------------------
echo "==> [3/7] pointing REPOSERVER/OSSERVER at the mirror ($HOSTPORT)"
cp "$ENVF" "$BACKUP/environment"
# Remove any prior mirror lines, then set ours. product_yum_server -> REPOSERVER
# (content-hub host), fsr_os_server -> OSSERVER (icons).
sed -i -E '/^(product_yum_server|fsr_os_server|OFFLINEREPO|REPOSERVER|OSSERVER)=/d' "$ENVF"
{
  echo "product_yum_server=$HOSTPORT"
  echo "fsr_os_server=$HOSTPORT"
  echo "OFFLINEREPO=true"
} >> "$ENVF"
# Keep the yum var side in sync where present (RPM/connector path). Back up the
# original first so --revert can put the real host back rather than guessing.
if [[ -f /etc/yum/vars/product_yum_server ]]; then
  [[ -f "$BACKUP/yum-var-product_yum_server" ]] || cp /etc/yum/vars/product_yum_server "$BACKUP/yum-var-product_yum_server"
  echo "$HOSTPORT" > /etc/yum/vars/product_yum_server
fi
say "set product_yum_server=$HOSTPORT, fsr_os_server=$HOSTPORT, OFFLINEREPO=true"

# ---- [4/7] enable OFFLINEREPO in the php-fpm pool env ----------------------
echo "==> [4/7] enabling OFFLINEREPO in the php-fpm pool env"
# The pool maps env[REPOSERVER]=$product_yum_server etc.; make sure OFFLINEREPO
# and the two hosts are exported to php-fpm even if the pool doesn't inherit
# /etc/environment.
POOL=$(ls /etc/php-fpm.d/*.conf 2>/dev/null | head -1 || true)
if [[ -n "$POOL" ]]; then
  [[ -f "$BACKUP/$(basename "$POOL")" ]] || cp "$POOL" "$BACKUP/$(basename "$POOL")"
  sed -i -E '/^env\[(OFFLINEREPO|REPOSERVER|OSSERVER)\]/d' "$POOL"
  {
    echo "env[REPOSERVER] = $HOSTPORT"
    echo "env[OSSERVER] = $HOSTPORT"
    echo "env[OFFLINEREPO] = true"
  } >> "$POOL"
  say "updated $POOL"
else
  warn "no php-fpm pool conf found; relying on /etc/environment"
fi

# Connector RPM install source (Option C). Two repos: a local override repo that
# wins (priority=1) so our custom cyops-connector-<name>-<ver> installs over
# Fortinet's, and a proxy to the public Fortinet connector repo for everything
# else (priority=50). dnf treats a lower priority number as higher precedence.
echo "==> [4.5/7] connector install repos -> the mirror"
mkdir -p /etc/yum.repos.d 2>/dev/null || true
cat > /etc/yum.repos.d/fsr-mirror-connectors.repo <<REPO
[fsr-mirror-connectors-override]
name=FortiSOAR Mirror Connectors (override)
baseurl=https://$HOSTPORT/connectors-local/x86_64/
enabled=1
gpgcheck=0
priority=1
sslverify=0
skip_if_unavailable=True
# The mirror is authoritative and its RPM set changes as we (re)build custom
# connectors. Without this, dnf caches the override repo's metadata for the
# default 48h, so a rebuilt/renamed RPM 404s (dnf still asks for the cached
# NEVRA) until the cache expires. metadata_expire=1 makes every install re-read
# the mirror's repomd, so a freshly pushed RPM is picked up immediately.
metadata_expire=1

[fsr-mirror-connectors]
name=FortiSOAR Mirror Connectors (proxy)
baseurl=https://$HOSTPORT/connectors/x86_64/
enabled=1
gpgcheck=0
priority=50
sslverify=0
skip_if_unavailable=True
REPO
say "wrote /etc/yum.repos.d/fsr-mirror-connectors.repo"

# ---- [5/7] restart php-fpm ------------------------------------------------
echo "==> [5/7] restarting php-fpm"
systemctl restart php-fpm && say "php-fpm restarted"

# ---- [6/7] sync Content Hub from the mirror --------------------------------
echo "==> [6/7] syncing Content Hub from the mirror"
csadm package content-hub sync --force \
  || die "sync failed — check the mirror is reachable at https://$HOSTPORT/content-hub/content-hub.json and its cert is trusted (run '$0 --check')"

# ---- [7/7] post-sync verification ------------------------------------------
echo "==> [7/7] post-sync verification (catalog + a per-item info.json over TLS)"
# The sync uses Guzzle with TLS verify OFF, so a successful sync does NOT prove
# the SP install path will work. This step does an actual verified HTTPS GET
# against the mirror (using the OS trust store) — the same code path the SP
# install endpoint uses when it re-downloads the artifact. If this fails, the
# SP install would fail too; fail loudly here with the real cause instead.
if [[ "$NO_VERIFY" == "1" ]]; then
  warn "skipping post-sync verification (--no-verify)"
else
  if ! curl -fsS --cacert "$(cat "$CERT_BACKUP" 2>/dev/null || echo /etc/pki/tls/certs/ca-bundle.crt)" \
       "https://$HOSTPORT/content-hub/content-hub.json" >/dev/null 2>&1; then
    die "verified GET of the catalog failed — the SP install path will fail too.
  Run '$0 --check' for diagnostics, or re-run with --cert-file pointing at the
  mirror's actual cert."
  fi
  say "verified catalog GET against https://$HOSTPORT/content-hub/content-hub.json (TLS verified)"
  # Pick one entry from the synced catalog and confirm its info.json resolves
  # over TLS-verified HTTPS — this is the exact fetch the SP install path makes
  # before downloading the artifact zip.
  sample="$(curl -fsS "https://$HOSTPORT/content-hub/content-hub.json" \
    | python3 -c "import json,sys; e=json.load(sys.stdin)[0]; print(f\"{e['name']}-{e['version']}/{e['buildNumber']}/info.json\")" 2>/dev/null || true)"
  if [[ -n "$sample" ]] && curl -fsS "https://$HOSTPORT/content-hub/$sample" >/dev/null 2>&1; then
    say "verified per-item info.json at /content-hub/$sample (TLS verified)"
  else
    warn "could not verify a per-item info.json (the catalog may be empty on the mirror); SP install trust still OK"
  fi
fi

echo
echo "DONE. FortiSOAR now reads Content Hub from https://$HOSTPORT/"
echo "Verify in the UI (Content Hub) or:"
echo "  curl -s https://$HOSTPORT/content-hub/content-hub.json | head"
echo "Re-verify trust any time with:  sudo $0 --check $HOSTPORT"
echo "Revert any time with:          sudo $0 --revert"
