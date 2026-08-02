#!/usr/bin/env bash
#
# Cut a pyfsr release: preflight, tag, GitHub release, then wait until the
# version is actually installable from PyPI.
#
# Usage:
#   scripts/release.sh 0.18.8            # full release
#   scripts/release.sh 0.18.8 --check    # preflight only, changes nothing
#
# The published version comes entirely from the git tag (hatch-vcs); there is
# no version string in any file. See RELEASING.md.

set -euo pipefail

VERSION="${1:-}"
MODE="${2:-}"

if [[ -z "$VERSION" ]]; then
    echo "usage: $0 <version> [--check]   e.g. $0 0.18.8" >&2
    exit 2
fi

VERSION="${VERSION#v}"
TAG="v${VERSION}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

die() { echo "release: $*" >&2; exit 1; }
ok()  { echo "  ok   $*"; }
step() { echo; echo "== $*"; }

# ---------------------------------------------------------------- preflight --

step "Preflight for ${TAG}"

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([abc.].*)?$ ]] \
    || die "'$VERSION' is not a PEP 440 release version (expected e.g. 0.18.8)"
ok "version string ${VERSION}"

command -v gh >/dev/null || die "gh CLI not found (brew install gh)"
gh auth status >/dev/null 2>&1 || die "gh is not authenticated (gh auth login)"
ok "gh authenticated"

# Guard the repo itself. Every check below reads git through $(...), where a
# failure is swallowed by set -e -- outside a repo, `git status --porcelain`
# fails, expands to empty, and reads as "clean".
git rev-parse --git-dir >/dev/null 2>&1 \
    || die "${REPO_ROOT} is not a git repository"
ok "git repository at ${REPO_ROOT}"

# Clean tree. A dirty tree at release time means the tag does not describe what
# was tested -- this is the check that has bitten most often.
if [[ -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    die "working tree is dirty -- commit or stash before releasing"
fi
ok "working tree clean"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BRANCH" == "main" ]] || die "on branch '$BRANCH' -- releases are cut from main"
git fetch --quiet origin main
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
[[ "$LOCAL" == "$REMOTE" ]] \
    || die "main is not in sync with origin/main (local ${LOCAL:0:8}, remote ${REMOTE:0:8})"
ok "on main, in sync with origin (${LOCAL:0:8})"

# Tag must be free, locally and on the remote.
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
    die "tag ${TAG} already exists locally"
fi
if git ls-remote --exit-code --tags origin "refs/tags/${TAG}" >/dev/null 2>&1; then
    die "tag ${TAG} already exists on origin"
fi
ok "tag ${TAG} is free"

# CHANGELOG must carry a section for this version -- the GitHub release notes
# are cut from it, so a missing section means an empty release.
NOTES_FILE="$(mktemp -t pyfsr-relnotes)"
trap 'rm -f "$NOTES_FILE"' EXIT
awk -v ver="$VERSION" '
    $0 ~ "^## \\[" ver "\\]" { grab = 1; next }
    grab && /^## / { exit }
    grab { print }
' CHANGELOG.md > "$NOTES_FILE"
[[ -s "$NOTES_FILE" ]] \
    || die "CHANGELOG.md has no '## [${VERSION}]' section with content"
ok "CHANGELOG section found ($(grep -c . "$NOTES_FILE") non-blank lines)"

# PyPI must not already have this version -- PyPI rejects re-uploads, and the
# failure surfaces only after CI has run the whole test suite.
PYPI_JSON="$(curl -fsSL "https://pypi.org/pypi/pyfsr/json")" \
    || die "could not reach the PyPI JSON API"
if python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
sys.exit(0 if '$VERSION' in data['releases'] else 1)
" <<<"$PYPI_JSON"; then
    die "pyfsr ${VERSION} is already on PyPI -- pick a higher version"
fi
LATEST="$(python3 -c "
import json, sys
print(json.loads(sys.stdin.read())['info']['version'])
" <<<"$PYPI_JSON")"
ok "PyPI latest is ${LATEST}; ${VERSION} is free"

# CI must be green on the exact commit being tagged.
CI_STATE="$(gh api "repos/{owner}/{repo}/commits/${LOCAL}/check-runs" \
    --jq '[.check_runs[] | select(.status != "completed" or (.conclusion | IN("success","neutral","skipped") | not))] | length')"
TOTAL_RUNS="$(gh api "repos/{owner}/{repo}/commits/${LOCAL}/check-runs" --jq '.total_count')"
[[ "$TOTAL_RUNS" -gt 0 ]] \
    || die "no check runs found for ${LOCAL:0:8} -- CI has not reported on this commit"
[[ "$CI_STATE" == "0" ]] \
    || die "${CI_STATE} of ${TOTAL_RUNS} check runs are failing or still running on ${LOCAL:0:8}"
ok "all ${TOTAL_RUNS} check runs green on ${LOCAL:0:8}"

if [[ "$MODE" == "--check" ]]; then
    echo
    echo "Preflight passed. Re-run without --check to cut ${TAG}."
    exit 0
fi

# ------------------------------------------------------------------ release --

step "Tagging ${TAG}"
git tag -a "$TAG" -m "pyfsr ${VERSION}"
git push origin "$TAG"
ok "pushed ${TAG}"

step "Creating GitHub release"
# The publish workflow fires on release *created*, runs the test suite, builds
# with fetch-depth 0 so hatch-vcs sees the tag, and uploads via OIDC.
gh release create "$TAG" \
    --title "pyfsr ${VERSION}" \
    --notes-file "$NOTES_FILE"
ok "release created -- publish workflow is running"

# ------------------------------------------------------------------- verify --

step "Waiting for ${VERSION} on PyPI"
# Two distinct waits: the JSON API learns about the version first, but the
# simple index (what pip/uv actually resolve against) lags behind it. Polling
# only the JSON API reports success while an install still fails.
DEADLINE=$(( $(date +%s) + 900 ))
until curl -fsSL "https://pypi.org/pypi/pyfsr/${VERSION}/json" >/dev/null 2>&1; do
    [[ $(date +%s) -lt $DEADLINE ]] \
        || die "timed out after 15m waiting for ${VERSION} on the PyPI JSON API -- check the publish workflow (gh run list)"
    printf '.'
    sleep 20
done
echo
ok "JSON API reports ${VERSION}"

step "Confirming ${VERSION} is installable"
TMPDIR_DL="$(mktemp -d -t pyfsr-verify)"
trap 'rm -f "$NOTES_FILE"; rm -rf "$TMPDIR_DL"' EXIT
DEADLINE=$(( $(date +%s) + 900 ))
until pip download --no-deps --quiet --dest "$TMPDIR_DL" "pyfsr==${VERSION}" >/dev/null 2>&1; do
    [[ $(date +%s) -lt $DEADLINE ]] \
        || die "timed out after 15m -- ${VERSION} is in the JSON API but not resolvable from the index"
    printf '.'
    sleep 20
done
echo
ok "pip resolved and downloaded pyfsr==${VERSION}"

echo
echo "Released pyfsr ${VERSION}: https://pypi.org/project/pyfsr/${VERSION}/"
