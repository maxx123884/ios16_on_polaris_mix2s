#!/bin/bash
# Build the Polaris sweet kernel on a macOS host, the same way CI does.
#
#   bash build-on-mac.sh [workdir]
#
# It is deliberately the CI's recipe, not a new one:
#   - blacktop/darwin-xnu-build at the SHA the workflow pins
#   - our xnu/patches/* copied next to blacktop's own patches
#   - the same sed that adds arm64 to VALID_ARCHS (libfirehose_kernel is arm64e-only,
#     the SWEET kernel is plain arm64)
#   - RELEASE + MACHINE_CONFIG=SWEET, because a DEVELOPMENT build overruns the DTB
#
# On success it prints the kernel path and whether it carries a marker string from
# the newest patch, so a stale tree fails loudly instead of quietly.
set -euo pipefail

DXB_REPO="https://github.com/blacktop/darwin-xnu-build"
DXB_SHA="663906528d1cd5ea8fde7de6f16de73db9511549"
MACOS_VERSION="${MACOS_VERSION:-13.5}"
KERNEL_CONFIG="${KERNEL_CONFIG:-RELEASE}"
MACHINE_CONFIG="${MACHINE_CONFIG:-SWEET}"
MARKER="${MARKER:-m1a cx before}"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
WORK="${1:-$REPO/out/mac-build}"

echo "== repo $REPO"
echo "== work $WORK"
[ -d "$REPO/xnu/patches" ] || { echo "no xnu/patches in $REPO" >&2; exit 1; }

echo "== toolchain"
sw_vers
xcodebuild -version
echo "-- iig: $(xcrun --find iig 2>/dev/null || echo 'NOT FOUND')"
ls -d /Library/Developer/KDKs/* 2>/dev/null || echo "-- no KDK installed"
XC_VER="$(xcodebuild -version | head -1 | awk '{print $2}')"
case "$XC_VER" in
    16.*) echo "!! Xcode $XC_VER emits IORPC.kernelContent, which xnu-8796 lacks; expect iig to fail. Use Xcode 15.x." >&2 ;;
esac

echo "== fetch build script at $DXB_SHA"
mkdir -p "$WORK"
cd "$WORK"
if [ ! -d dxb/.git ]; then
    git init -q dxb
    git -C dxb remote add origin "$DXB_REPO" 2>/dev/null || true
fi
git -C dxb fetch -q --depth 1 "$DXB_REPO" "$DXB_SHA"
git -C dxb checkout -q FETCH_HEAD
git -C dxb log -1 --format='   %h %s'

echo "== apply our patches ($(ls "$REPO"/xnu/patches/*.patch | wc -l | tr -d ' ') of them)"
cp "$REPO"/xnu/patches/*.patch "$REPO"/xnu/patches/*.sh dxb/patches/ 2>/dev/null || \
    cp "$REPO"/xnu/patches/*.patch dxb/patches/

echo "== widen VALID_ARCHS so plain arm64 builds"
if grep -q 'VALID_ARCHS="x86_64 arm64e arm64"' dxb/build.sh; then
    echo "   already widened"
else
    sed -i '' 's/ARCHS="x86_64 arm64e" VALID_ARCHS="x86_64 arm64e"/ARCHS="x86_64 arm64e arm64" VALID_ARCHS="x86_64 arm64e arm64"/' dxb/build.sh
    grep -c 'VALID_ARCHS="x86_64 arm64e arm64"' dxb/build.sh
fi

echo "== build (this is the long part)"
cd dxb
export MACOS_VERSION KERNEL_CONFIG MACHINE_CONFIG
# keep going so one run reports every compile failure
export MAKEFLAGS="${MAKEFLAGS:--k}"
set +e
bash build.sh 2>&1 | tee "$WORK/build.log"
STATUS=${PIPESTATUS[0]}
set -e
echo "== build exit $STATUS (log $WORK/build.log)"

KERNEL="$(find "$WORK/dxb" -name "kernel.${KERNEL_CONFIG,,}.sweet" -o -name "kernel.${KERNEL_CONFIG,,}.*" 2>/dev/null | head -1)"
if [ -z "$KERNEL" ]; then
    echo "!! no kernel produced; last 40 lines of the log:" >&2
    tail -40 "$WORK/build.log" >&2
    exit 1
fi

echo "== kernel $KERNEL ($(stat -f%z "$KERNEL") bytes)"
if strings -a "$KERNEL" | grep -q "$MARKER"; then
    echo "== marker '$MARKER' present: this is the patched tree"
else
    echo "!! marker '$MARKER' missing: the tree is stale or the patches did not apply" >&2
    exit 1
fi
echo "== done: copy that kernel back, it is what the boot image needs"
