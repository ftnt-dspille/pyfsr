#!/usr/bin/env bash
# Exercise the Content Hub fetch contract against a running mirror and assert the
# key paths resolve. Proves the HTTP contract locally (no appliance needed).
#
#   ./smoke-test.sh [BASE_URL]      default: http://localhost:8080
set -euo pipefail
BASE="${1:-http://localhost:8080}"
fail() { echo "FAIL: $*" >&2; exit 1; }

echo "== mirror smoke test against $BASE =="

# 1. health
curl -fsS "$BASE/healthz" >/dev/null || fail "healthz"
echo "ok   /healthz"

# 2. merged manifest is a JSON array
manifest="$(curl -fsS "$BASE/content-hub/content-hub.json")" || fail "content-hub.json"
echo "$manifest" | python3 -c "import json,sys; d=json.load(sys.stdin); assert isinstance(d,list) and d, 'not a non-empty array'" \
  || fail "manifest is not a non-empty JSON array"
n="$(echo "$manifest" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")"
echo "ok   /content-hub/content-hub.json ($n entries)"

# 3. every entry's info.json resolves at its numbered build + latest/
python3 - "$BASE" <<'PY'
import json, sys, urllib.request
base = sys.argv[1]
with urllib.request.urlopen(base + "/content-hub/content-hub.json") as r:
    entries = json.load(r)
checked = 0
for e in entries:
    name, ver, build = e["name"], e["version"], e["buildNumber"]
    for path in (f"/content-hub/{name}-{ver}/{build}/info.json",
                 f"/content-hub/{name}-{ver}/latest/info.json"):
        with urllib.request.urlopen(base + path) as r:
            got = json.load(r)
        assert got["name"] == name, f"{path}: name mismatch {got.get('name')} != {name}"
    checked += 1
print(f"ok   info.json resolves for all {checked} local entr(y/ies) (numbered + latest/)")
PY

echo "== PASS =="
