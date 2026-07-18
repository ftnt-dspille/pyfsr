#!/usr/bin/env bash
# Exercise the full installable-connector publish contract against a running
# mirror and assert every artifact the offline install path fetches resolves —
# proving a from-scratch container reproduces the connector-publish state
# (RPM in the local yum repo, merged connectors-all.json, staged metadata zip)
# with no manual VM steps. No appliance needed.
#
#   ./smoke-test-connector.sh [BASE_URL] [ADMIN_URL]
#     BASE_URL   nginx front door   (default: http://localhost)
#     ADMIN_URL  admin API          (default: http://localhost:9000)
#     CHM_TOKEN  admin bearer token (only if ADMIN_TOKEN is set on the mirror)
set -euo pipefail
BASE="${1:-http://localhost}"
ADMIN="${2:-http://localhost:9000}"
NAME="smoketestconnector"
VER="1.0.0"
REL="$(date +%s 2>/dev/null || echo 1)"   # unique release so each run re-pulls
SLUG="${NAME}-${VER}"
fail() { echo "FAIL: $*" >&2; exit 1; }

echo "== connector publish smoke test against $BASE (admin $ADMIN) =="

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# 1. build a minimal connector source tgz: <name>/info.json
mkdir -p "$work/$NAME"
cat > "$work/$NAME/info.json" <<JSON
{"name": "$NAME", "version": "$VER", "label": "Smoke Test Connector",
 "publisher": "SmokeTest", "buildNumber": 1, "category": "Utilities",
 "operations": [{"operation": "ping", "title": "Ping"}]}
JSON
tar -C "$work" -czf "$work/$NAME.tgz" "$NAME"
echo "ok   built $NAME.tgz"

# 2. publish it through the admin API (builds the RPM, merges cinfo, stages zip)
auth=()
[[ -n "${CHM_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer $CHM_TOKEN")
resp="$(curl -fsS "${auth[@]}" -F "tgz=@$work/$NAME.tgz" -F "release=$REL" \
  "$ADMIN/api/connector")" || fail "POST /api/connector"
rpm="$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('published') or d).get('rpm_full_name'))")" \
  || fail "response had no rpm_full_name: $resp"
echo "ok   published -> $rpm"

# 3. assert every offline-install artifact resolves over HTTP
python3 - "$BASE" "$NAME" "$VER" "$rpm" <<'PY'
import json, sys, urllib.request
base, name, ver, rpm = sys.argv[1:5]
slug = f"{name}-{ver}"

def get(path, binary=False):
    with urllib.request.urlopen(base + path) as r:
        return r.read() if binary else r.read().decode()

# 3a. the RPM is served from the local override repo
get(f"/connectors-local/x86_64/{rpm}", binary=True)
print(f"ok   /connectors-local/x86_64/{rpm}")

# 3b. connectors-all.json maps <name>_<version> -> this exact RPM
cinfo = json.loads(get("/connectors/info/connectors-all.json"))
key = f"{name}_{ver}"
assert key in cinfo, f"connectors-all.json missing {key}"
assert cinfo[key].get("rpm_full_name") == rpm, f"{key} -> {cinfo[key]} != {rpm}"
print(f"ok   /connectors/info/connectors-all.json has {key} -> {rpm}")

# 3c. the Content-Hub metadata zip + info.json resolve at both build and latest/
for sub in ("1", "latest"):
    zip_bytes = get(f"/content-hub/{slug}/{sub}/{slug}.zip", binary=True)
    assert zip_bytes[:2] == b"PK", f"{sub}/{slug}.zip is not a zip"
    info = json.loads(get(f"/content-hub/{slug}/{sub}/info.json"))
    assert info["name"] == name, f"{sub}/info.json name mismatch"
print(f"ok   /content-hub/{slug}/{{1,latest}}/ metadata zip + info.json")
PY

# 4. clean up: remove the throwaway catalog entry (RPM/cinfo are gitignored;
# leaving the entry would show up in subsequent catalog fetches).
curl -fsS "${auth[@]}" -X DELETE "$ADMIN/api/content/connector/$NAME" >/dev/null 2>&1 \
  && echo "ok   cleaned up catalog entry for $NAME" \
  || echo "ok   (cleanup skipped — entry may still be in local-content/)"

echo "== PASS =="
