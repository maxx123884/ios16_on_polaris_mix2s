#!/bin/sh
# Host tests for the Polaris GPU handoff work. No phone, no firmware required.
#
#   sh tools/run-gpu-host-tests.sh
#
# Runs the Python suite always, and the C validator test whenever a C compiler
# is present (CI runners have one; a bare Windows box may not).
set -eu

cd "$(dirname "$0")/.."

echo "== python: handoff tooling + existing GPU host tests"
python3 -m unittest tools/test_polaris_gpu_expect_table.py \
	tools/test_polaris_gpu_preflight.py tools/test_polaris_gmu_firmware.py \
	tools/test_polaris_arc_db.py tools/test_polaris_bcm_db.py \
	tools/test_polaris_bw_levels.py tools/test_polaris_gpu_stages.py \
	tools/test_polaris_rscc_pdc.py tools/test_polaris_zap_check.py \
	tools/test_polaris_firmware_set.py tools/test_polaris_payload_blobs.py \
	tools/test_polaris_dt_regs.py tools/test_polaris_gmu_csr.py -q

echo "== c: polaris_gpu_validate"
# Use CC when set, else the first working system compiler, else the pip zig
# toolchain (a Windows box without MSVC still gets to run the C test).
set --
if [ -n "${CC:-}" ]; then
	set -- "$CC"
else
	for candidate in cc gcc clang; do
		if command -v "$candidate" >/dev/null 2>&1; then
			set -- "$candidate"
			break
		fi
	done
fi
if [ $# -eq 0 ] && python3 -c 'import ziglang' >/dev/null 2>&1; then
	# zig needs a writable cache; the profile directory is not always allowed.
	ZIG_GLOBAL_CACHE_DIR=${ZIG_GLOBAL_CACHE_DIR:-${TMPDIR:-/tmp}/zigcache-global}
	ZIG_LOCAL_CACHE_DIR=${ZIG_LOCAL_CACHE_DIR:-${TMPDIR:-/tmp}/zigcache-local}
	export ZIG_GLOBAL_CACHE_DIR ZIG_LOCAL_CACHE_DIR
	set -- python3 -m ziglang cc
fi
if [ $# -eq 0 ]; then
	echo "SKIP: no C compiler found; run this on a CI runner or install ziglang via pip"
	exit 0
fi

# The C sources only exist in a tree with the patches applied; this repo holds
# them inside xnu/patches/0094. Point PG_TREE at such a tree.
TREE=${PG_TREE:-.}
if [ ! -f "$TREE/bsd/dev/polaris_gpu_validate.c" ] || \
   [ ! -f "$TREE/tools/test_polaris_gpu_validate.c" ]; then
	echo "SKIP: no patched tree; C test needs e.g."
	echo "  PG_TREE=../build/verify2 sh tools/run-gpu-host-tests.sh"
	exit 0
fi

out=${TMPDIR:-/tmp}/pgv.$$.exe
outh=${TMPDIR:-/tmp}/pgh.$$.exe
gate=${TMPDIR:-/tmp}/pgg.$$.exe
trap 'rm -f "$out" "$outh" "$gate" 2>/dev/null || true' EXIT INT TERM
"$@" -std=c99 -Wall -Wextra -Werror -o "$out" \
	"$TREE/tools/test_polaris_gpu_validate.c" "$TREE/bsd/dev/polaris_gpu_validate.c"
"$out"

echo "== c: polaris gpu gate (executes polaris_gpu_init, real firmware)"
# This one is a program, not a unit test: it builds the ADT the payload
# produces, maps the six files at the payload's addresses and asserts on what
# the gate reported. PG_FW points at the extracted MIUI firmware; without it
# only the compile check runs, because the scenarios need real bytes.
FW=${PG_FW:-../build/fw-miui}
GATESRC="tools/gate/gate_main.c tools/gate/kernel.c tools/gate/sha256.c"
GATEKERNEL="$TREE/bsd/dev/polaris_gpu.c $TREE/bsd/dev/polaris_gpu_hash.c $TREE/bsd/dev/polaris_gpu_validate.c"
if [ -f "$TREE/bsd/dev/polaris_gpu.c" ]; then
	# shellcheck disable=SC2086
	"$@" -std=gnu11 -Wall -Wextra -Werror -DARM64_BOARD_CONFIG_SWEET \
		-I tools/gate/stub -I "$TREE/bsd/dev" -o "$gate" \
		$GATESRC $GATEKERNEL
	if [ -d "$FW" ]; then
		"$gate" "$FW"
	else
		echo "SKIP: no firmware at $FW; the gate compiled but its scenarios need real bytes"
	fi

	# The host run above is x86_64 LP64/LLP64; the kernel is arm64. Recompile
	# the three files for aarch64 so a pointer-width assumption fails here
	# instead of in CI. Only zig can target macOS from this box.
	case "$*" in
	*ziglang*)
		for unit in polaris_gpu polaris_gpu_hash polaris_gpu_validate; do
			"$@" -target aarch64-macos -std=gnu11 -Wall -Wextra -Werror \
				-DARM64_BOARD_CONFIG_SWEET -I tools/gate/stub \
				-I "$TREE/bsd/dev" -c "$TREE/bsd/dev/$unit.c" \
				-o "${TMPDIR:-/tmp}/$unit.$$.o"
		done
		echo "ok    polaris_gpu*.c compile for aarch64-macos"
		;;
	*)
		echo "SKIP: not zig, cannot target aarch64-macos"
		;;
	esac
else
	echo "SKIP: no patched tree; the gate needs bsd/dev/polaris_gpu.c"
fi

echo "== c: polaris_gpu_hash host harness (stubbed libkern/vm)"
"$@" -std=c99 -Wall -Wextra -Werror -Wpedantic -DARM64_BOARD_CONFIG_SWEET \
	-I "$TREE/tools/hosthash/stub" -I "$TREE/bsd/dev" -o "$outh" \
	"$TREE/tools/hosthash/harness.c" "$TREE/bsd/dev/polaris_gpu_hash.c"
"$outh"
