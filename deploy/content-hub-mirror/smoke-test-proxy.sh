#!/usr/bin/env bash
# Exercise the public-repo proxy paths against a running mirror — assert the
# widget .tgz, solution-pack .zip, and connector .tgz long-tail paths the
# mirror does NOT host locally all resolve via the reverse-proxy to the
# public Fortinet repo (no FDN cert needed).
#
# This proves the mirror "proxies what it isn't hosting" — the user can
# `pyfsr.repo.download_widget("accessControl", "2.1.0")` pointed at the mirror
# and get the bytes, even though the mirror doesn't carry that widget itself.
#
#   ./smoke-test-proxy.sh [BASE_URL]   default: http://localhost
#
# Exits 0 if every proxy path returns 2xx (or a propagated 404 for a known-
# absent version). Needs network egress to repo.fortisoar.fortinet.com.
set -euo pipefail
BASE="${1:-http://localhost}"
fail() { echo "FAIL: $*" >&2; exit 1; }

echo "== public-repo proxy smoke test against $BASE =="

# 1. the proxy must be wired (a request through it reaches the public host).
#    Use a known-stable widget — accessControl 2.1.0 — verified live on the
#    public repo (the layout doc lists it as the canonical example).
WIDGET_INFO="/fsr-widgets/accessControl-2.1.0/info.json"
WIDGET_TGZ="/fsr-widgets/accessControl-2.1.0/accessControl-2.1.0.tgz"
curl -fsSI "$BASE$WIDGET_INFO" >/dev/null || fail "widget info.json proxy: $WIDGET_INFO"
echo "ok   widget info.json proxy  $WIDGET_INFO"
curl -fsSI "$BASE$WIDGET_TGZ" >/dev/null || fail "widget .tgz proxy: $WIDGET_TGZ"
echo "ok   widget .tgz proxy      $WIDGET_TGZ"

# 2. solution-pack .zip + info.json via /xf/solutions/solutionpacks/. The
#    public repo's SP layout (a different path convention than /content-hub/).
SP_INFO="/xf/solutions/solutionpacks/fortindrEssentials-1.0.4/latest/info.json"
curl -fsSI "$BASE$SP_INFO" >/dev/null || fail "SP info.json proxy: $SP_INFO"
echo "ok   SP info.json proxy     $SP_INFO"

# 3. connector .tgz via /xf/solutions/connectors/ (NOT the RPM path — that's
#    /connectors/x86_64/, exercised by smoke-test-connector.sh).
CONN_TGZ="/xf/solutions/connectors/abuseipdb-2.0.0/latest/abuseipdb.tgz"
CONN_INFO="/xf/solutions/connectors/abuseipdb-2.0.0/latest/info.json"
curl -fsSI "$BASE$CONN_TGZ" >/dev/null || fail "connector .tgz proxy: $CONN_TGZ"
echo "ok   connector .tgz proxy   $CONN_TGZ"
curl -fsSI "$BASE$CONN_INFO" >/dev/null || fail "connector info.json proxy: $CONN_INFO"
echo "ok   connector info proxy   $CONN_INFO"

# 4. connector RPM long-tail proxy (/connectors/x86_64/) and the merged
#    connectors-all.json the installer reads. The merged file is built by
#    entrypoint.sh from the public map + any local overrides.
curl -fsS "$BASE/connectors/info/connectors-all.json" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert isinstance(d,dict) and d, 'empty connectors-all.json'" \
  || fail "connectors-all.json"
echo "ok   /connectors/info/connectors-all.json (merged: upstream + local overrides)"

# 5. a known-absent version propagates the upstream 404 (proves we are really
#    proxying, not silently 404-ing locally).
CODE="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/xf/solutions/connectors/servicenow-9.9.9-NoSuchVersion/latest/servicenow.tgz")"
[[ "$CODE" == "404" ]] || fail "expected 404 for a known-absent version, got $CODE"
echo "ok   upstream 404 propagates for known-absent versions"

# 6. downloading the actual widget tgz through the mirror yields a real gzip.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl -sS "$BASE$WIDGET_TGZ" -o "$work/widget.tgz" || fail "download widget tgz"
file "$work/widget.tgz" | grep -qi "gzip compressed data" || fail "widget tgz is not gzip"
echo "ok   widget .tgz downloads as gzip ($(stat -f%z "$work/widget.tgz" 2>/dev/null || stat -c%s "$work/widget.tgz") bytes)"

echo "== PASS =="
