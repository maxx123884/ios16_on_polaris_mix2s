#!/bin/bash
# Build the boot image for one probe step, then optionally flash it.
#
#   ./make-image.sh <kernel> <step> [--boot]
#
# The polaris options go FIRST in the command line. That is not cosmetic: with them at the
# end of a 273-character line the kernel never saw them, the probe returned before its
# first printf, and two runs were read as crashes when nothing had run at all.
#
set -u

KERNEL=${1:?usage: make-image.sh <kernel> <step> [--boot]}
STEP=${2:?usage: make-image.sh <kernel> <step> [--boot]}
DO_BOOT=${3:-}

ROOT=/e/mix2s/gpu-driver
UP=$ROOT/upstream
FW=$ROOT/build/fw-miui
ZIG="C:/Users/33447/AppData/Roaming/Python/Python313/site-packages/ziglang/zig.exe"

# polaris options first - see the note above.
CMDLINE="polarisgpu=1 polarisgpuhash=1 polarisgxprobe=1 polarisgxm1=$STEP \
debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 \
unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 \
launchd_unsecure_cache=1 wdt=-1 sweetusb=2"

PAYLOAD=$UP/shim/out/payload-step$STEP.bin

echo "[1/3] payload"
cd "$UP" || exit 1
python3 tools/mkpayload.py "$KERNEL" shim/out/sweet-ci96.adt "$PAYLOAD" \
	--cmdline "$CMDLINE" \
	--blob GPU-SQE=$FW/a630_sqe.fw --blob GPU-GMU=$FW/a630_gmu.bin \
	--blob GPU-ZAP-MDT=$FW/a630_zap.mdt --blob GPU-ZAP-B00=$FW/a630_zap.b00 \
	--blob GPU-ZAP-B01=$FW/a630_zap.b01 --blob GPU-ZAP-B02=$FW/a630_zap.b02 \
	--max-payload-size 14589952 --check-polaris-cpus | grep size:

echo "[2/3] shim image"
cd shim || exit 1
ZIG="$ZIG" PLATFORM=polaris ./build.sh "out/payload-step$STEP.bin" | grep img:

echo "[3/3] copy"
cp "$UP/out/shim-polaris-xnu.img" "$ROOT/build/shim-polaris-step$STEP.img"
ls -l "$ROOT/build/shim-polaris-step$STEP.img" | awk '{print "      " $5 " bytes"}'

if [ "$DO_BOOT" = "--boot" ]; then
	echo "[boot] fastboot boot"
	cd "$ROOT" || exit 1
	build/tools/platform-tools/fastboot.exe boot "build/shim-polaris-step$STEP.img"
fi
