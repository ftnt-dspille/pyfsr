#!/usr/bin/env bash
# Rebuild a FortiSOAR connector RPM at a chosen version, using an upstream
# connector tgz as the template. This closes the "installed real 1.0.0 vs
# mirror-advertised X.Y.Z" gap: the output RPM installs through the normal
# yum/content-hub path so the appliance's installed package == what the mirror
# advertises.
#
# Usage:
#   ./build.sh <connector_name> <new_version> [release] [src_tgz]
# Example:
#   ./build.sh http 2.1.0 1 src/http-1.0.0.tgz
#
# Output: dist/cyops-connector-<name>-<version>-<release>.x86_64.rpm
#
# The tgz version bump touches info.json inside the tarball; the spec Version
# and %post mod_version derive from <new_version>, so all three stay in lockstep.
set -euo pipefail

NAME="${1:?connector name, e.g. http}"
VERSION="${2:?new version, e.g. 2.1.0}"
RELEASE="${3:-1}"
SRC_TGZ="${4:-src/${NAME}-1.0.0.tgz}"
# underscored version for the %post connector dir (http_2_1_0). Computed here
# rather than via an rpm `%(... sed ...)` macro, whose backslash the rpm macro
# expander eats — turning `s/\./_/g` into `s/./_/g` and mangling 2.1.0 -> _____.
MOD_VERSION="${VERSION//./_}"

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
[[ -f "$SRC_TGZ" ]] || { echo "source tgz not found: $SRC_TGZ" >&2; exit 1; }

# keep WORK inside the repo tree — Docker Desktop on macOS only shares the
# user's home/project dirs, not /var/folders where mktemp -d lands.
WORK="$HERE/.build-tmp"
rm -rf "$WORK"; mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

echo "==> [1/4] bump ${NAME}/info.json version -> ${VERSION}"
tar xzf "$SRC_TGZ" -C "$WORK"
python3 - "$WORK/$NAME/info.json" "$VERSION" <<'PY'
import json, sys
path, version = sys.argv[1], sys.argv[2]
with open(path) as fh:
    info = json.load(fh)
info["version"] = version
with open(path, "w") as fh:
    json.dump(info, fh, indent=1)
print(f"    info.json version -> {version}")
PY
# repack with the same top-level layout (http/...) the framework expects
( cd "$WORK" && tar czf "rebranded-${NAME}.tgz" "$NAME" )

echo "==> [2/4] render spec (name=${NAME} version=${VERSION} release=${RELEASE})"
sed -e "s/@NAME@/${NAME}/g" -e "s/@VERSION@/${VERSION}/g" -e "s/@RELEASE@/${RELEASE}/g" \
    -e "s/@MOD_VERSION@/${MOD_VERSION}/g" \
    "cyops-connector.spec.in" > "$WORK/cyops-connector-${NAME}.spec"

# The spec is BuildArch: noarch (pure data + scriptlets), so rpmbuild runs
# NATIVELY on any host arch — no --platform/--target x86_64, no slow qemu
# emulation on Apple Silicon. rockylinux:9 matches the appliance's el9 toolchain.
echo "==> [3/4] rpmbuild in rockylinux:9 container (native arch, noarch RPM)"
mkdir -p dist
docker run --rm \
    -v "$WORK":/w -v "$HERE/dist":/dist \
    rockylinux:9 bash -euxc '
        dnf -y install rpm-build >/dev/null
        mkdir -p /root/rpmbuild/{SOURCES,SPECS}
        cp /w/rebranded-'"$NAME"'.tgz /root/rpmbuild/SOURCES/'"$NAME"'.tgz
        cp /w/cyops-connector-'"$NAME"'.spec /root/rpmbuild/SPECS/
        rpmbuild -bb /root/rpmbuild/SPECS/cyops-connector-'"$NAME"'.spec
        cp /root/rpmbuild/RPMS/*/*.rpm /dist/
    '

echo "==> [4/4] built:"
ls -la dist/cyops-connector-${NAME}-${VERSION}-${RELEASE}.noarch.rpm
