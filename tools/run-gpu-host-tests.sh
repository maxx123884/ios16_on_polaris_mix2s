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
	tools/test_polaris_rscc_pdc.py -q

echo "== c: polaris_gpu_validate"
CC=${CC:-}
if [ -z "$CC" ]; then
	for candidate in cc gcc clang; do
		if command -v "$candidate" >/dev/null 2>&1; then
			CC=$candidate
			break
		fi
	done
fi
if [ -z "$CC" ]; then
	echo "SKIP: no C compiler found; run this on a CI runner or a machine with cc"
	exit 0
fi

out=${TMPDIR:-/tmp}/pgv.$$
trap 'rm -f "$out"' EXIT INT TERM
"$CC" -std=c99 -Wall -Wextra -Werror -o "$out" \
	tools/test_polaris_gpu_validate.c bsd/dev/polaris_gpu_validate.c
"$out"
