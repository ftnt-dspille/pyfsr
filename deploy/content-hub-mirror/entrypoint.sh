#!/usr/bin/env bash
# Container entrypoint for the Content Hub mirror.
#
#   1. build the merged content-hub tree (pyfsr.content_catalog) into /srv
#   2. ensure a TLS server cert exists (self-signed if none mounted)
#   3. generate nginx.conf — serving local content + reverse-proxy fallbacks
#      for everything the mirror doesn't host:
#        /content-hub/<name>-<ver>/...      -> UPSTREAM_HOST (FDN cert OR plain HTTPS)
#        /fsr-widgets/, /widgets/           -> PUBLIC_FORTINET_HOST (widget .tgz, no cert)
#        /xf/solutions/solutionpacks/       -> PUBLIC_FORTINET_HOST (SP .zip, no cert)
#        /xf/solutions/connectors/          -> PUBLIC_FORTINET_HOST (connector .tgz, no cert)
#        /xf-widgets/, /xf/widgets/         -> PUBLIC_FORTINET_HOST (alt widget paths, no cert)
#        /connectors/                       -> PUBLIC_FORTINET_HOST connector yum repo (no cert)
#   4. exec nginx in the foreground
#
# See README.md for the env vars.
set -euo pipefail

: "${OUTPUT_DIR:=/srv}"
: "${UPSTREAM_HOST:=}"
: "${UPSTREAM_PROXY:=1}"     # set 0 to disable the /content-hub/ reverse-proxy fallback
: "${UPSTREAM_TLS_VERIFY:=1}"  # 0 = skip upstream TLS verify (e.g. self-signed mirror-of-a-mirror)
: "${SERVER_CERT:=/etc/nginx/certs/server.crt}"
: "${SERVER_KEY:=/etc/nginx/certs/server.key}"
: "${FDN_CERT:=/etc/nginx/certs/fdn.pem}"
: "${FDN_KEY:=/etc/nginx/certs/fdn.key}"

# The PUBLIC Fortinet repo (repo.fortisoar.fortinet.com) is open HTTPS — no FDN
# client cert needed. It hosts the artifact long tail for everything the mirror
# itself doesn't carry: widget .tgz at /fsr-widgets/, SP .zip at
# /xf/solutions/solutionpacks/, connector .tgz at /xf/solutions/connectors/,
# and the connector RPM yum repo at /connectors/. Proxying these lets the
# mirror "see both" — local content wins, public repo fills the long tail —
# without needing the entitlement-gated FDN cert at all. Set PUBLIC_PROXY=0
# to serve strictly local content (404 anything we don't host).
: "${PUBLIC_PROXY:=1}"
: "${PUBLIC_FORTINET_HOST:=repo.fortisoar.fortinet.com}"

# --- Option C: connector RPM yum-repo (hybrid proxy + local override) ---------
# /connectors/       -> reverse-proxy the PUBLIC Fortinet connector repo (no cert)
# /connectors-local/ -> a small local yum repo we own (custom/override RPMs), so
#                       our cyops-connector-<name>-<ver> wins via repo priority.
: "${CONNECTORS_PROXY:=${PUBLIC_PROXY}}"   # default = follow PUBLIC_PROXY
: "${CONNECTORS_UPSTREAM:=${PUBLIC_FORTINET_HOST}}"
: "${CONNECTORS_UPSTREAM_PATH:=/prod/connectors}"   # /connectors/x86_64 -> $PATH/x86_64
: "${CONNECTORS_LOCAL_DIR:=/connectors-local}"      # mount custom RPMs under x86_64/
: "${CONNECTORS_PREFETCH:=}"                          # comma/space list of RPM files to pull local
# The installer reads ONE connectors-all.json to learn each connector's exact
# RPM file. We serve a MERGED copy (Fortinet's full map + our custom/override
# entries) at /connectors/info/connectors-all.json so our RPMs are visible.
: "${CONNECTORS_CINFO:=/srv/local-cinfo/connectors-all.json}"

echo "==> [1/4] building merged catalog"
python3 /app/build_catalog.py

# Prefetch any named upstream RPMs into the local repo (handy for override tests),
# then (re)build the local repodata over whatever RPMs are present.
if [[ -n "$CONNECTORS_PREFETCH" ]]; then
  mkdir -p "$CONNECTORS_LOCAL_DIR/x86_64"
  for r in ${CONNECTORS_PREFETCH//,/ }; do
    if wget -q -P "$CONNECTORS_LOCAL_DIR/x86_64" \
         "https://${CONNECTORS_UPSTREAM}${CONNECTORS_UPSTREAM_PATH}/x86_64/${r}"; then
      echo "    prefetched connector RPM: $r"
    else
      echo "    WARNING: could not prefetch $r"
    fi
  done
fi
if ls "$CONNECTORS_LOCAL_DIR/x86_64/"*.rpm >/dev/null 2>&1; then
  echo "    building local connector repodata over $(ls "$CONNECTORS_LOCAL_DIR/x86_64/"*.rpm | wc -l) RPM(s)"
  createrepo_c "$CONNECTORS_LOCAL_DIR/x86_64" >/dev/null
fi

# Seed the merged connectors-all.json (Fortinet's full map) if we don't have one
# yet, then re-merge an entry for every local RPM so our overrides survive a
# restart even if the cinfo file lives on an ephemeral layer. publish_connector
# also merges at publish time; this just makes startup self-healing.
mkdir -p "$(dirname "$CONNECTORS_CINFO")"
if [[ ! -f "$CONNECTORS_CINFO" ]]; then
  if wget -q -O "$CONNECTORS_CINFO" \
       "https://${CONNECTORS_UPSTREAM}/connectors/info/connectors-all.json"; then
    echo "    seeded connectors-all.json from https://${CONNECTORS_UPSTREAM}"
  else
    echo "    WARNING: could not seed connectors-all.json from upstream; starting empty"
    echo '{}' > "$CONNECTORS_CINFO"
  fi
fi
if ls "$CONNECTORS_LOCAL_DIR/x86_64/"*.rpm >/dev/null 2>&1; then
  CONNECTORS_CINFO="$CONNECTORS_CINFO" CONNECTORS_LOCAL_DIR="$CONNECTORS_LOCAL_DIR" python3 - <<'PY'
import json, os, re
cinfo = os.environ["CONNECTORS_CINFO"]
arch = os.path.join(os.environ["CONNECTORS_LOCAL_DIR"], "x86_64")
with open(cinfo) as fh:
    data = json.load(fh)
# cyops-connector-<name>-<version>-<release>.<arch>.rpm -> <name>_<version>
pat = re.compile(r"^cyops-connector-(?P<name>.+)-(?P<ver>[^-]+)-(?P<rel>[^-]+)\.\w+\.rpm$")
n = 0
for f in os.listdir(arch):
    m = pat.match(f)
    if not m:
        continue
    data[f"{m['name']}_{m['ver']}"] = {"rpm_full_name": f}
    n += 1
with open(cinfo, "w") as fh:
    json.dump(data, fh)
print(f"    re-merged {n} local RPM(s) into connectors-all.json")
PY
fi

echo "==> [2/4] TLS server certificate"
if [[ ! -f "$SERVER_CERT" || ! -f "$SERVER_KEY" ]]; then
  echo "    no server cert mounted -> generating a self-signed one"
  mkdir -p "$(dirname "$SERVER_CERT")"
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$SERVER_KEY" -out "$SERVER_CERT" \
    -subj "/CN=content-hub-mirror" >/dev/null 2>&1
fi

echo "==> [3/4] generating nginx.conf"
# /content-hub/ upstream fallback. Two modes:
#   * FDN cert present  -> mTLS to UPSTREAM_HOST (entitled secops-content.forticloud.com)
#   * no FDN cert        -> plain HTTPS to UPSTREAM_HOST (e.g. another mirror, or the
#                          public repo for the connector bundles it carries there)
# Both require UPSTREAM_HOST set; otherwise a miss simply 404s (Option A, local-only).
#
# `UPSTREAM_TLS_VERIFY=0` skips upstream cert verification — use ONLY for a
# self-signed upstream you control (mirror-of-a-mirror); the public Fortinet
# host and secops-content both present valid CA certs, so leave verify ON.
UPSTREAM_BLOCK=""
MISS_HANDLER="=404"
if [[ "$UPSTREAM_PROXY" == "1" && -n "$UPSTREAM_HOST" ]]; then
  if [[ -f "$FDN_CERT" && -f "$FDN_KEY" ]]; then
    echo "    /content-hub/ upstream: https://$UPSTREAM_HOST (FDN mTLS client cert)"
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
    echo "    /content-hub/ upstream: https://$UPSTREAM_HOST (plain HTTPS, no FDN cert)"
    UPSTREAM_BLOCK=$(cat <<NGINX
    location @upstream {
        proxy_pass https://${UPSTREAM_HOST};
        proxy_ssl_server_name on;
        proxy_ssl_name ${UPSTREAM_HOST};
        proxy_set_header Host ${UPSTREAM_HOST};
        proxy_set_header User-Agent "content-hub-mirror";
        $([[ "$UPSTREAM_TLS_VERIFY" == "0" ]] && echo "        proxy_ssl_verify off;")
    }
NGINX
)
  fi
  MISS_HANDLER="@upstream"
else
  echo "    /content-hub/ upstream: disabled (serving local content only)"
fi

# Public-repo proxy block — widget .tgz, SP .zip, connector .tgz, and the
# connector RPM yum repo. All live on the open PUBLIC_FORTINET_HOST (no FDN
# client cert needed). Each is a pure reverse-proxy: a miss through one of
# these is a real 404 from the public host, surfaced as-is. Set PUBLIC_PROXY=0
# to disable all of them (404 anything we don't host locally).
PUBLIC_BLOCK=""
if [[ "$PUBLIC_PROXY" == "1" && -n "$PUBLIC_FORTINET_HOST" ]]; then
  echo "    public-repo proxy: https://$PUBLIC_FORTINET_HOST (widget/SP/connector-tgz + connector RPM)"
  # nginx proxy_ssl_verify defaults to ON; the public Fortinet host presents a
  # valid CA cert, so we leave verification on. To proxy to a self-signed
  # mirror-of-a-mirror, point PUBLIC_FORTINET_HOST at it and add
  # `proxy_ssl_verify off;` to each block below.
  PUBLIC_BLOCK=$(cat <<NGINX

    # Widget .tgz (current path) + info.json — what pyfsr.repo.download_widget
    # and the Content Hub widget install pull from.
    location /fsr-widgets/ {
        proxy_pass https://${PUBLIC_FORTINET_HOST}/fsr-widgets/;
        proxy_ssl_server_name on;
        proxy_ssl_name ${PUBLIC_FORTINET_HOST};
        proxy_set_header Host ${PUBLIC_FORTINET_HOST};
        proxy_set_header User-Agent "content-hub-mirror";
    }

    # Widget .tgz (older 7.x path) — kept for older widget versions that only
    # live here, not under /fsr-widgets/.
    location /widgets/ {
        proxy_pass https://${PUBLIC_FORTINET_HOST}/widgets/;
        proxy_ssl_server_name on;
        proxy_ssl_name ${PUBLIC_FORTINET_HOST};
        proxy_set_header Host ${PUBLIC_FORTINET_HOST};
        proxy_set_header User-Agent "content-hub-mirror";
    }

    # Next-gen widget paths (alternate names the public repo also serves).
    location /xf-widgets/ {
        proxy_pass https://${PUBLIC_FORTINET_HOST}/xf-widgets/;
        proxy_ssl_server_name on;
        proxy_ssl_name ${PUBLIC_FORTINET_HOST};
        proxy_set_header Host ${PUBLIC_FORTINET_HOST};
        proxy_set_header User-Agent "content-hub-mirror";
    }

    # Solution-pack .zip + info.json — the public repo's SP layout (different
    # from the /content-hub/<name>-<ver>/<build>/ convention the appliance's
    # OFFLINEREPO sync uses; both exist on the public host). pyfsr.repo and
    # any direct-URL SP download go through here.
    location /xf/solutions/solutionpacks/ {
        proxy_pass https://${PUBLIC_FORTINET_HOST}/xf/solutions/solutionpacks/;
        proxy_ssl_server_name on;
        proxy_ssl_name ${PUBLIC_FORTINET_HOST};
        proxy_set_header Host ${PUBLIC_FORTINET_HOST};
        proxy_set_header User-Agent "content-hub-mirror";
    }

    # Connector .tgz (next-gen /xf/solutions/ layout) — the public repo's
    # non-RPM connector distribution path. pyfsr.repo.download_connector hits
    # this; the RPM path (/connectors/x86_64/) is wired separately below.
    location /xf/solutions/connectors/ {
        proxy_pass https://${PUBLIC_FORTINET_HOST}/xf/solutions/connectors/;
        proxy_ssl_server_name on;
        proxy_ssl_name ${PUBLIC_FORTINET_HOST};
        proxy_set_header Host ${PUBLIC_FORTINET_HOST};
        proxy_set_header User-Agent "content-hub-mirror";
    }
NGINX
)
else
  echo "    public-repo proxy: disabled (serving local content only)"
fi

# Connector-repo blocks (Option C). The local override repo is served whenever a
# repodata index exists; the proxy to the public Fortinet connector repo is added
# unless disabled. Neither needs a client cert (the public host is open).
CONNECTORS_BLOCK=""
if [[ -f "$CONNECTORS_LOCAL_DIR/x86_64/repodata/repomd.xml" ]]; then
  echo "    local connector repo: /connectors-local/x86_64 (override)"
  CONNECTORS_BLOCK+=$(cat <<NGINX

    location /connectors-local/ {
        alias ${CONNECTORS_LOCAL_DIR}/;
        autoindex on;
    }
NGINX
)
fi
# Serve our MERGED connectors-all.json (exact match beats the /connectors/ proxy
# prefix below), so the installer sees our custom/override RPMs alongside the
# upstream long tail. Without this, /connectors/info/connectors-all.json would
# proxy straight to Fortinet and our RPMs would be invisible.
if [[ -f "$CONNECTORS_CINFO" ]]; then
  echo "    connectors-all.json: serving merged map from $CONNECTORS_CINFO"
  CONNECTORS_BLOCK+=$(cat <<NGINX

    location = /connectors/info/connectors-all.json {
        alias ${CONNECTORS_CINFO};
        default_type application/json;
        add_header Cache-Control "no-store";
    }
NGINX
)
fi
if [[ "$CONNECTORS_PROXY" == "1" ]]; then
  echo "    connector RPM proxy: https://${CONNECTORS_UPSTREAM}${CONNECTORS_UPSTREAM_PATH} (long tail)"
  CONNECTORS_BLOCK+=$(cat <<NGINX

    location /connectors/ {
        proxy_pass https://${CONNECTORS_UPSTREAM}${CONNECTORS_UPSTREAM_PATH}/;
        proxy_ssl_server_name on;
        proxy_ssl_name ${CONNECTORS_UPSTREAM};
        proxy_set_header Host ${CONNECTORS_UPSTREAM};
        proxy_set_header User-Agent "content-hub-mirror";
    }
NGINX
)
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

    # Per-item info.json / artifacts / icons: serve our local copy from the
    # rebuilt-on-start tree (/srv), then the persistent published-connector tree
    # (/published, where chctl add-connector stages metadata zips), then fall
    # through to the upstream (or 404 when proxy disabled).
    location /content-hub/ {
        root /srv;
        try_files \$uri @published_ch;
    }
    location @published_ch {
        root /published;
        try_files \$uri ${MISS_HANDLER};
    }

    location = /healthz { return 200 "ok\n"; default_type text/plain; }
${UPSTREAM_BLOCK}
${PUBLIC_BLOCK}
${CONNECTORS_BLOCK}
}
NGINX

if [[ "${ADMIN_ENABLED:-1}" == "1" ]]; then
  echo "==> starting admin GUI/API on :${ADMIN_PORT:-9000}"
  python3 /app/admin/app.py &
fi

echo "==> [4/4] starting nginx"
exec nginx -g 'daemon off;'
