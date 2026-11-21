#!/bin/sh
# Preflight for the polaris GPU patch series before pushing to CI.
#
# CI (dxb/build.sh) applies each xnu/patches/*.patch with
#     git apply --check "$PATCH" || warning "Skipping patch"
# so a patch that does not apply is silently dropped and the build can still
# go green. This script treats such a skip as an error.
#
# Usage:
#   sh tools/ci-preflight.sh [--conf-files PATH] [--pg-base PATH] [--base REF]
#
#   --conf-files PATH  upstream bsd/conf/files for the XNU tag the workflow
#                      builds (xnu-8796.141.3). With it, the bsd/conf/files
#                      part of the whole series is applied in order, the way CI
#                      does. That is the only part of our series whose context
#                      comes from a file we did not author.
#   --pg-base PATH     bsd/dev/polaris_gpu.c exactly as 0093 writes it; used to
#                      prove 0094's context matches that file.
#   --base REF         git ref to diff against when deciding which files this
#                      work touched (default: origin/main, else 0093).
#
# Exit status is non-zero if CI would skip a patch or the host tests fail.
# Nothing here touches a device.

set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PATCHDIR="$ROOT/xnu/patches"
CONF_FILES=""
PG_BASE=""
BASE=""

while [ $# -gt 0 ]; do
	case "$1" in
	--conf-files) CONF_FILES=$2; shift 2 ;;
	--pg-base) PG_BASE=$2; shift 2 ;;
	--base) BASE=$2; shift 2 ;;
	-h | --help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
	*) echo "unknown argument: $1" >&2; exit 2 ;;
	esac
done

PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)

BAD=0
ok() { printf 'ok    %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1"; BAD=1; }
note() { printf '      %s\n' "$1"; }

TMP=""
cleanup() { [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null; }
trap cleanup EXIT HUP INT TERM
mktmp() {
	[ -n "$TMP" ] && return 0
	TMP=$(mktemp -d 2>/dev/null) || { bad "cannot create a temp dir"; exit 1; }
}

# ---------------------------------------------------------------- patch naming
# CI globs the directory, so order is lexicographic: names must be zero-padded.
dupes=$(ls "$PATCHDIR"/*.patch | sed "s|$PATCHDIR/||" |
	sed -n 's/^\([0-9]\{4\}\)-.*/\1/p' | sort | uniq -d)
if [ -n "$dupes" ]; then
	bad "duplicate patch numbers: $(echo "$dupes" | tr '\n' ' ')"
else
	ok "patch numbers unique among *.patch"
fi

weird=$(ls "$PATCHDIR" | grep -vE '^[0-9]{4}-[A-Za-z0-9_-]+\.(patch|sh)$' || true)
if [ -n "$weird" ]; then
	bad "unexpected file names in xnu/patches:"
	echo "$weird" | sed 's/^/        /'
else
	ok "patch and script names are four digits plus a slug"
fi

# The workflow does `cp xnu/patches/*.patch xnu/patches/*.sh dxb/patches/`.
# An empty glob makes that cp fail and the job dies before building.
shcount=$(ls "$PATCHDIR"/*.sh 2>/dev/null | wc -l | tr -d ' ')
if [ "$shcount" -gt 0 ]; then
	ok "$shcount staging scripts present (the workflow copies *.sh too)"
else
	bad "no *.sh in xnu/patches: the workflow's 'cp ... *.sh' would fail"
fi

# ------------------------------------------------------------- patch hygiene
# Only files this work touched; the older series predates these conventions.
if [ -z "$BASE" ]; then
	if git -C "$ROOT" rev-parse --verify -q origin/main >/dev/null; then
		BASE=origin/main
	else
		BASE=0093
	fi
fi
changed=$(git -C "$ROOT" diff --name-only "$BASE" -- xnu/patches tools 2>/dev/null || true)
if [ -z "$changed" ]; then
	note "no files differ from $BASE; skipping hygiene checks"
else
	crlf=""
	abspath=""
	for f in $changed; do
		# This script names the very patterns it looks for, so skip it.
		[ "$f" = "tools/ci-preflight.sh" ] && continue
		# What CI receives is the blob, not the CRLF working copy a Windows
		# checkout produces, so inspect the index copy when there is one.
		if git -C "$ROOT" cat-file -e ":$f" 2>/dev/null; then
			if git -C "$ROOT" show ":$f" | grep -q $'\r' 2>/dev/null; then crlf="$crlf $f"; fi
			if git -C "$ROOT" show ":$f" | grep -qE '/e/mix2s|[A-Za-z]:\\\\' 2>/dev/null; then abspath="$abspath $f"; fi
		elif [ -f "$ROOT/$f" ]; then
			if grep -q $'\r' "$ROOT/$f" 2>/dev/null; then crlf="$crlf $f"; fi
			if grep -qE '/e/mix2s|[A-Za-z]:\\\\' "$ROOT/$f" 2>/dev/null; then abspath="$abspath $f"; fi
		fi
	done
	if [ -n "$crlf" ]; then
		bad "CR bytes in files changed since $BASE:"
		for f in $crlf; do note "$f"; done
	else
		ok "no CR bytes in files changed since $BASE"
	fi
	if [ -n "$abspath" ]; then
		bad "absolute workspace paths in:"
		for f in $abspath; do note "$f"; done
	else
		ok "no absolute workspace paths"
	fi
fi

# ------------------------------------------------ conf/files chain (CI order)
# Default the inputs so `sh tools/ci-preflight.sh` alone is meaningful: fetch
# the upstream file the workflow builds against and rebuild the 0093 output of
# polaris_gpu.c from the patch that creates it.
XNU_TAG=xnu-8796.141.3
if [ -z "$CONF_FILES" ]; then
	mktmp
	if CONF_FILES=$TMP/conf-files &&
		curl -sS -o "$CONF_FILES" \
			"https://api.github.com/repos/apple-oss-distributions/xnu/contents/bsd/conf/files?ref=$XNU_TAG" 2>/dev/null &&
		"$PY" - "$CONF_FILES" <<'PY' 2>/dev/null
import base64, json, sys
p = sys.argv[1]
d = json.load(open(p))
open(p, 'wb').write(base64.b64decode(d['content']))
PY
	then
		note "fetched upstream bsd/conf/files for $XNU_TAG"
	else
		note "could not fetch upstream bsd/conf/files; skipping that check"
		CONF_FILES=""
	fi
fi

if [ -z "$PG_BASE" ]; then
	mktmp
	if PG_BASE=$TMP/polaris_gpu.c &&
		"$PY" - "$PATCHDIR"/0093-*.patch "$PG_BASE" <<'PY' 2>/dev/null
import sys
src, dst = sys.argv[1], sys.argv[2]
lines = open(src, encoding='utf-8', errors='replace').read().split('\n')
start = next(i for i, l in enumerate(lines) if l.startswith('diff --git a/bsd/dev/polaris_gpu.c'))
body = []
for l in lines[start + 1:]:
    if l.startswith('diff --git '):
        break
    if l.startswith('+') and not l.startswith('+++'):
        body.append(l[1:])
open(dst, 'w', encoding='utf-8', newline='\n').write('\n'.join(body) + '\n')
PY
	then
		note "rebuilt the 0093 output of polaris_gpu.c from its patch"
	else
		note "could not rebuild polaris_gpu.c; skipping 0094 context check"
		PG_BASE=""
	fi
fi

if [ -n "$CONF_FILES" ]; then
	if [ ! -f "$CONF_FILES" ]; then
		bad "--conf-files $CONF_FILES does not exist"
	else
		mktmp
		mkdir -p "$TMP/chain/bsd/conf"
		cp "$CONF_FILES" "$TMP/chain/bsd/conf/files"
		(
			cd "$TMP/chain" || exit 1
			git init -q . && git config core.autocrlf false && git add -A &&
				git -c user.name=preflight -c user.email=preflight@localhost \
					commit -qm base
		) || { bad "cannot set up the temp tree"; exit 1; }

		applied=0
		skipped=""
		for p in $(grep -lE '^(diff --git a/bsd/conf/files|--- a/bsd/conf/files)' \
			"$PATCHDIR"/*.patch | LC_ALL=C sort); do
			if git -C "$TMP/chain" apply --check --include='bsd/conf/files' \
				--whitespace=nowarn "$p" 2>/dev/null; then
				git -C "$TMP/chain" apply --include='bsd/conf/files' \
					--whitespace=nowarn "$p" 2>/dev/null ||
					bad "apply failed after --check passed: $(basename "$p")"
				applied=$((applied + 1))
			else
				skipped="$skipped $(basename "$p")"
			fi
		done
		if [ -n "$skipped" ]; then
			bad "CI would SKIP these patches (conf/files hunk does not apply):"
			for s in $skipped; do note "$s"; done
		else
			ok "$applied patches apply to the real bsd/conf/files, in order"
			if grep -q '^bsd/dev/polaris_gpu_validate.c' "$TMP/chain/bsd/conf/files" &&
				grep -q '^bsd/dev/polaris_gpu_hash.c' "$TMP/chain/bsd/conf/files"; then
				ok "both new GPU sources are listed in conf/files"
			else
				bad "a new GPU source is missing from the resulting conf/files"
			fi
		fi
	fi
else
	note "skipped the conf/files chain check (pass --conf-files)"
fi

# ----------------------------- 0094's context is exactly what 0093 writes out
if [ -n "$PG_BASE" ]; then
	if [ ! -f "$PG_BASE" ]; then
		bad "--pg-base $PG_BASE does not exist"
	else
		mktmp
		mkdir -p "$TMP/pgbase/bsd/dev"
		git -C "$TMP/pgbase" init -q .
		tr -d '\r' <"$PG_BASE" >"$TMP/pgbase/bsd/dev/polaris_gpu.c"
		if git -C "$TMP/pgbase" apply --check --include='bsd/dev/polaris_gpu.c' \
			"$PATCHDIR"/0094-*.patch 2>/dev/null; then
			ok "0094 applies to the given 0093 output of polaris_gpu.c"
		else
			bad "0094 does not apply to $PG_BASE"
		fi
	fi
fi

# ----------------------------------------------------------------- host tests
if [ -n "${PG_TREE:-}" ]; then
	if sh "$ROOT/tools/run-gpu-host-tests.sh" >"$ROOT/../build/preflight-tests.log" 2>&1; then
		tests=$(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$ROOT/../build/preflight-tests.log" 2>/dev/null | head -1)
		ok "host tests passed (${tests:-0} Python tests; log in build/preflight-tests.log)"
	else
		bad "host tests failed; see ../build/preflight-tests.log"
	fi
else
	note "skipped host tests (set PG_TREE to a patched tree)"
fi

echo
if [ "$BAD" -ne 0 ]; then
	echo "preflight FAILED - CI would not build this cleanly"
	exit 1
fi
echo "preflight passed"
