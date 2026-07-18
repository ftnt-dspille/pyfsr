#!/usr/bin/env bash
# Build the current pyfsr into ./wheels so the image ships this checkout's
# content_catalog module (not whatever is on PyPI), then build the image.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"

echo "==> building pyfsr wheel from $repo_root"
rm -rf "$here/wheels"
mkdir -p "$here/wheels"
# Prefer uv (the project's venv has no pip); fall back to plain pip for callers
# without uv installed.
if command -v uv >/dev/null 2>&1; then
  uv build --wheel --out-dir "$here/wheels" "$repo_root"
else
  python -m pip wheel --no-deps -w "$here/wheels" "$repo_root"
fi
ls -1 "$here/wheels"

echo "==> building image pyfsr/content-hub-mirror:latest"
docker build -t pyfsr/content-hub-mirror:latest "$here"
