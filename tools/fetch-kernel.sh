#!/bin/bash
# Fetch the kernel from the build VM quickly.
#
# Measured on this pair of machines: one scp stream moves 22-53 KB/s from VM to host,
# eight reach 359 KB/s and sixteen 432 KB/s. The throttle is per connection, not per
# byte, so splitting the file is the whole trick; gzip -9 takes the kernel from 12.3 MB
# to about 4.7 MB on top of that. Net effect: four minutes become twelve seconds.
#
#   ./fetch-kernel.sh [remote-path] [local-path] [streams]
#
set -u

VM=znax@192.168.217.128
KEY=/e/mix2s/.ssh/buildhost
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes"
SCP="scp -q -i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

REMOTE=${1:-/Users/znax/polaris/dxb/build/xnu.obj/RELEASE_ARM64_SWEET/kernel.release.sweet}
LOCAL=${2:-./kernel.release.sweet}
STREAMS=${3:-16}
TMP=$(mktemp -d)

echo "[1/4] compress and split on the VM ($STREAMS parts)"
# find -delete, not rm with a glob: zsh aborts the whole chain on an unmatched glob.
N=$($SSH $VM "cd /tmp; find /tmp -maxdepth 1 -name 'kf.gz' -delete; find /tmp -maxdepth 1 -name 'kf.[0-9]*' -delete; gzip -9 -c '$REMOTE' > kf.gz && split -n $STREAMS -d kf.gz kf. && ls kf.[0-9]* | wc -l" 2>/dev/null | tr -d ' ')
if [ -z "$N" ] || [ "$N" -lt 1 ]; then
	echo "split produced nothing - remote compress failed" >&2
	rm -rf "$TMP"
	exit 1
fi
echo "      $N parts, $(($($SSH $VM "stat -f %z /tmp/kf.gz" 2>/dev/null || echo 0) / 1024)) KiB compressed"

echo "[2/4] fetching in parallel"
START=$(date +%s%N)
i=0
while [ $i -lt "$N" ]; do
	idx=$(printf '%02d' $i)
	( $SCP $VM:/tmp/kf.$idx $TMP/$idx >/dev/null 2>&1 ) &
	i=$((i + 1))
done
wait
END=$(date +%s%N)

echo "[3/4] joining"
( for f in $(ls -1 "$TMP" | sort); do cat "$TMP/$f"; done ) | gunzip > "$LOCAL"

GOT=$(stat -c %s "$LOCAL" 2>/dev/null || echo 0)
MS=$(( (END - START) / 1000000 ))
WIRE=$($SSH $VM "stat -f %z /tmp/kf.gz" 2>/dev/null || echo 0)
echo "[4/4] $GOT bytes (${WIRE} on the wire) in ${MS}ms = $((WIRE * 1000 / (MS + 1) / 1024)) KB/s -> $LOCAL"

rm -rf "$TMP"
