#!/usr/bin/env bash
# Point a FortiSOAR appliance at a self-hosted Content Hub mirror — one command.
#
# Run ON the appliance as root:
#     sudo ./setup-appliance.sh <mirror-host>[:port]
# or FROM your laptop over ssh:
#     ssh <appliance> 'sudo bash -s' -- <mirror-host>[:port] < setup-appliance.sh
#
# What it does (all reversible with --revert):
#   1. trusts the mirror's TLS cert (self-signed mirrors otherwise fail the sync)
#   2. sets product_yum_server + fsr_os_server (REPOSERVER/OSSERVER) to the mirror
#   3. enables OFFLINEREPO (direct-HTTPS to your host, not FortiCloud)
#   4. restarts php-fpm so workers pick up the new env
#   5. runs `csadm package content-hub sync --force`
#
# The originals are backed up to /root/content-hub-mirror-backup/ so --revert
# restores them.
set -euo pipefail

BACKUP=/root/content-hub-mirror-backup
ENVF=/etc/environment

die() { echo "ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "must run as root (use sudo)"

# ---- revert ---------------------------------------------------------------
if [[ "${1:-}" == "--revert" ]]; then
  echo "==> reverting Content Hub mirror config"
  [[ -f "$BACKUP/environment" ]] && cp "$BACKUP/environment" "$ENVF" && echo "  restored $ENVF"
  if [[ -f "$BACKUP/mirror-cert-path" ]]; then
    cert=$(cat "$BACKUP/mirror-cert-path"); rm -f "$cert" && echo "  removed $cert"
    command -v update-ca-trust >/dev/null && update-ca-trust extract || update-ca-certificates --fresh >/dev/null 2>&1 || true
  fi
  rm -f /etc/yum.repos.d/fsr-mirror-connectors.repo && echo "  removed connector repo"
  systemctl restart php-fpm && echo "  restarted php-fpm"
  echo "==> reverted. Re-run 'csadm package content-hub sync --force' to repull from FortiCloud."
  exit 0
fi

MIRROR="${1:-}"
[[ -n "$MIRROR" ]] || die "usage: setup-appliance.sh <mirror-host>[:port]  (or --revert)"
HOSTPORT="$MIRROR"
if [[ "$MIRROR" == *:* ]]; then HOST="${MIRROR%%:*}"; PORT="${MIRROR##*:}"; else HOST="$MIRROR"; PORT=443; fi
mkdir -p "$BACKUP"

echo "==> [1/5] trusting the mirror's TLS certificate"
# Pull the leaf cert the mirror presents and add it to the OS trust store so the
# appliance's HTTPS client accepts a self-signed mirror. (Harmless for a mirror
# that already uses a publicly-trusted cert.)
if command -v update-ca-trust >/dev/null 2>&1; then          # RHEL/Rocky (FortiSOAR)
  CERT_DIR=/etc/pki/ca-trust/source/anchors; EXTRACT="update-ca-trust extract"
else                                                          # Debian/Ubuntu
  CERT_DIR=/usr/local/share/ca-certificates; EXTRACT="update-ca-certificates"
fi
mkdir -p "$CERT_DIR"; CERT_PATH="$CERT_DIR/content-hub-mirror-${HOST}.crt"
if echo | openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" 2>/dev/null \
     | openssl x509 > "$CERT_PATH" 2>/dev/null && [[ -s "$CERT_PATH" ]]; then
  echo "$CERT_PATH" > "$BACKUP/mirror-cert-path"; $EXTRACT
  echo "  trusted cert from ${HOST}:${PORT} -> $CERT_PATH"
else
  rm -f "$CERT_PATH"
  echo "  WARNING: could not fetch a cert from the mirror on 443; if it uses a"
  echo "           self-signed cert the sync will fail — install it manually."
fi

echo "==> [2/5] pointing REPOSERVER/OSSERVER at the mirror ($HOSTPORT)"
cp "$ENVF" "$BACKUP/environment"
# Remove any prior mirror lines, then set ours. product_yum_server -> REPOSERVER
# (content-hub host), fsr_os_server -> OSSERVER (icons).
sed -i -E '/^(product_yum_server|fsr_os_server|OFFLINEREPO|REPOSERVER|OSSERVER)=/d' "$ENVF"
{
  echo "product_yum_server=$HOSTPORT"
  echo "fsr_os_server=$HOSTPORT"
  echo "OFFLINEREPO=true"
} >> "$ENVF"
# Keep the yum var side in sync where present (RPM/connector path).
[[ -f /etc/yum/vars/product_yum_server ]] && echo "$HOSTPORT" > /etc/yum/vars/product_yum_server || true
echo "  set product_yum_server=$HOSTPORT, fsr_os_server=$HOSTPORT, OFFLINEREPO=true"

echo "==> [3/5] enabling OFFLINEREPO in the php-fpm pool env"
# The pool maps env[REPOSERVER]=$product_yum_server etc.; make sure OFFLINEREPO
# and the two hosts are exported to php-fpm even if the pool doesn't inherit
# /etc/environment.
POOL=$(ls /etc/php-fpm.d/*.conf 2>/dev/null | head -1 || true)
if [[ -n "$POOL" ]]; then
  cp "$POOL" "$BACKUP/$(basename "$POOL")"
  sed -i -E '/^env\[(OFFLINEREPO|REPOSERVER|OSSERVER)\]/d' "$POOL"
  {
    echo "env[REPOSERVER] = $HOSTPORT"
    echo "env[OSSERVER] = $HOSTPORT"
    echo "env[OFFLINEREPO] = true"
  } >> "$POOL"
  echo "  updated $POOL"
else
  echo "  (no php-fpm pool conf found; relying on /etc/environment)"
fi

# Connector RPM install source (Option C). Two repos: a local override repo that
# wins (priority=1) so our custom cyops-connector-<name>-<ver> installs over
# Fortinet's, and a proxy to the public Fortinet connector repo for everything
# else (priority=50). dnf treats a lower priority number as higher precedence.
echo "==> connector install repos -> the mirror"
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
echo "  wrote /etc/yum.repos.d/fsr-mirror-connectors.repo"

echo "==> [4/5] restarting php-fpm"
systemctl restart php-fpm && echo "  php-fpm restarted"

echo "==> [5/5] syncing Content Hub from the mirror"
csadm package content-hub sync --force || die "sync failed — check the mirror is reachable at https://$HOSTPORT/content-hub/content-hub.json and its cert is trusted"

echo
echo "DONE. FortiSOAR now reads Content Hub from https://$HOSTPORT/"
echo "Verify in the UI (Content Hub) or:"
echo "  curl -s https://$HOSTPORT/content-hub/content-hub.json | head"
echo "Revert any time with:  sudo $0 --revert"
