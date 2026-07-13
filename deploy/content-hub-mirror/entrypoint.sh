#!/usr/bin/env bash
# Container entrypoint for the Content Hub mirror.
#
#   1. build the merged content-hub tree (pyfsr.content_catalog) into /srv
#   2. ensure a TLS server cert exists (self-signed if none mounted)
#   3. generate nginx.conf — with an upstream-proxy fallback only if configured
#   4. exec nginx in the foreground
#
# See README.md for the env vars.
set -euo pipefail

: "${OUTPUT_DIR:=/srv}"
: "${UPSTREAM_HOST:=}"
: "${UPSTREAM_PROXY:=1}"     # set 0 to disable the reverse-proxy fallback
: "${SERVER_CERT:=/etc/nginx/certs/server.crt}"
: "${SERVER_KEY:=/etc/nginx/certs/server.key}"
: "${FDN_CERT:=/etc/nginx/certs/fdn.pem}"
: "${FDN_KEY:=/etc/nginx/certs/fdn.key}"

echo "==> [1/4] building merged catalog"
python3 /app/build_catalog.py

echo "==> [2/4] TLS server certificate"
if [[ ! -f "$SERVER_CERT" || ! -f "$SERVER_KEY" ]]; then
  echo "    no server cert mounted -> generating a self-signed one"
  mkdir -p "$(dirname "$SERVER_CERT")"
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$SERVER_KEY" -out "$SERVER_CERT" \
    -subj "/CN=content-hub-mirror" >/dev/null 2>&1
fi

echo "==> [3/4] generating nginx.conf"
# The upstream fallback is only wired in when a host is set AND the FDN client
# cert is present — otherwise a cache miss simply 404s (Option A, local-only).
UPSTREAM_BLOCK=""
MISS_HANDLER="=404"
if [[ "$UPSTREAM_PROXY" == "1" && -n "$UPSTREAM_HOST" && -f "$FDN_CERT" && -f "$FDN_KEY" ]]; then
  echo "    upstream proxy: https://$UPSTREAM_HOST (FDN client cert present)"
  MISS_HANDLER="@upstream"
  UPSTREAM_BLOCK=$(cat <<NGINX
    location @upstream {
        proxy_pass https://${UPSTREAM_HOST};
        proxy_ssl_certificate     ${FDN_CERT};
        proxy_ssl_certificate_key ${FDN_KEY};
        proxy_ssl_server_name on;
        proxy_ssl_name ${UPSTREAM_HOST};
        proxy_set_header Host ${UPSTREAM_HOST};
        proxy_set_header User-Agent "content-hub-mirror";
    }
NGINX
)
else
  echo "    upstream proxy: disabled (serving local content only)"
fi

cat > /etc/nginx/conf.d/default.conf <<NGINX
server {
    listen 80;
    listen 443 ssl;
    ssl_certificate     ${SERVER_CERT};
    ssl_certificate_key ${SERVER_KEY};
    server_name _;

    # The merged manifest is ALWAYS served locally (never proxied) — it is the
    # union of upstream + our overrides that build_catalog.py produced.
    location = /content-hub/content-hub.json {
        root /srv;
        default_type application/json;
        add_header Cache-Control "no-store";
    }

    # Per-item info.json / artifacts / icons: serve our local copy if we have
    # it, otherwise fall through to the upstream (or 404 when proxy disabled).
    location /content-hub/ {
        root /srv;
        try_files \$uri ${MISS_HANDLER};
    }

    location = /healthz { return 200 "ok\n"; default_type text/plain; }

${UPSTREAM_BLOCK}
}
NGINX

if [[ "${ADMIN_ENABLED:-1}" == "1" ]]; then
  echo "==> starting admin GUI/API on :${ADMIN_PORT:-9000}"
  python3 /app/admin/app.py &
fi

echo "==> [4/4] starting nginx"
exec nginx -g 'daemon off;'
