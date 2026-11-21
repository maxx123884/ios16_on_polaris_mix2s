#!/bin/sh
# Push this branch to your fork and report what CI did with it.
#
#   sh tools/push-to-ci.sh <github-username>
#
# Why a script: the push needs your GitHub login, so it has to run in your
# terminal. Everything else - fork sanity, remote setup, push, then reading
# the run back from the public Actions API - is automated here.
#
# Nothing in this script touches a device or writes outside the git remote.

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
REPO=ios16_on_polaris_mix2s
UPSTREAM=luuu20309/$REPO

die() { printf 'error: %s\n' "$1" >&2; exit 1; }
say() { printf '%s\n' "$1"; }

[ $# -eq 1 ] || die "usage: sh tools/push-to-ci.sh <github-username>"
USER=$1
case "$USER" in
*[!A-Za-z0-9-]*) die "that does not look like a GitHub username: $USER" ;;
esac

FORK="$USER/$REPO"

# ---------------------------------------------------------------- fork exists?
say "== checking fork $FORK"
code=$(curl -sS -o /dev/null -w '%{http_code}' \
	"https://api.github.com/repos/$FORK" 2>/dev/null || echo 000)
case "$code" in
200) say "   fork found" ;;
404)
	cat >&2 <<EOF
error: $FORK not found.

Fork it first: https://github.com/$UPSTREAM/fork
Then open the fork's Actions tab once and click
"I understand my workflows, go ahead and enable them"
(forked workflows are disabled until you do).
EOF
	exit 1
	;;
000) say "   could not reach the API; continuing, git will tell us" ;;
*) say "   API answered $code; continuing anyway" ;;
esac

# ------------------------------------------------------------------- preflight
say "== preflight"
if [ -f "$ROOT/tools/ci-preflight.sh" ]; then
	sh "$ROOT/tools/ci-preflight.sh" || die "preflight failed; fix it before pushing"
else
	say "   (no preflight script; skipping)"
fi

# ----------------------------------------------------------------------- push
say "== pushing $(git -C "$ROOT" rev-parse --short HEAD) to $FORK main"
if [ "${DRY:-0}" = "1" ]; then
	say "   DRY=1: stopping before the push. The commands would be:"
	say "   git -C '$ROOT' remote add fork https://github.com/$FORK.git"
	say "   git -C '$ROOT' push fork main"
	exit 0
fi
if git -C "$ROOT" remote get-url fork >/dev/null 2>&1; then
	git -C "$ROOT" remote set-url fork "https://github.com/$FORK.git"
else
	git -C "$ROOT" remote add fork "https://github.com/$FORK.git"
fi
git -C "$ROOT" push fork main || {
	cat >&2 <<EOF

push refused. If the fork's main has commits you do not have, re-run with:
  git -C "$ROOT" push --force-with-lease fork main
only if you are sure those commits are not wanted.
EOF
	exit 1
}

# ------------------------------------------------------------ read the run back
say "== waiting for the workflow run"
i=0
run=""
while [ $i -lt 30 ]; do
	sleep 10
	i=$((i + 1))
	run=$(curl -sS -H 'Accept: application/vnd.github+json' \
		"https://api.github.com/repos/$FORK/actions/runs?per_page=1" 2>/dev/null ||
		echo '{}')
	state=$(printf '%s' "$run" | python -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("none"); raise SystemExit
r=d.get("workflow_runs") or []
if not r: print("none"); raise SystemExit
w=r[0]
print(w["id"], w["status"], w["conclusion"] or "-", w["head_sha"][:7], w["html_url"])
' 2>/dev/null || echo none)
	[ "$state" = none ] && continue
	set -- $state
	rid=$1 status=$2 conclusion=$3 sha=$4 url=$5
	say "   $sha $status $conclusion"
	if [ "$status" = completed ]; then
		say "   $url"
		say ""
		say "== job steps"
		curl -sS -H 'Accept: application/vnd.github+json' \
			"https://api.github.com/repos/$FORK/actions/runs/$rid/jobs" 2>/dev/null |
			python -c '
import json,sys
d=json.load(sys.stdin)
for j in d.get("jobs", []):
    print("   job:", j["name"], "->", j["conclusion"])
    for s in j.get("steps", []):
        if s["conclusion"] not in ("success", "skipped"):
            print("      step:", s["number"], s["name"], "->", s["conclusion"])
'
		say ""
		say "Read the log for these three, in order:"
		say "  1) 'Applying patch: ...0096-polaris-gpu-firmware-hash.patch' appears"
		say "  2) 'Skipping patch' does not appear for 0093..0096"
		say "  3) a compile line for polaris_gpu_hash.c exists"
		say "Green alone does not prove 0096 ran: the workflow skips patches"
		say "that do not apply and keeps building."
		exit 0
	fi
done
say "   still running after 5 minutes; open the Actions tab of $FORK"
