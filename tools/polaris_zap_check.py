#!/usr/bin/env python3
"""Check that a zap set is one coherent, signed bundle.

A Qualcomm `.mdt` is an ELF whose hash table pins the digests of its split
segments. For `a630_zap.*` the `.b00` (metadata) and `.b02` (code) digests are
embedded in the `.mdt` itself, while `.b01` is the hash segment and is not
hashed. If a folder mixes files from different MIUI/firmware revisions, the
digests will not line up -- this catches that before anything is packed into a
boot payload.

    python3 tools/polaris_zap_check.py --firmware DIR

Exit status is 0 only for a coherent set of the expected sizes.
"""
import argparse
import hashlib
import sys
from pathlib import Path

# The four parts mkpayload expects, with the sizes observed on Polaris.
ZAP_PARTS = (
    ('a630_zap.mdt', 6684),
    ('a630_zap.b00', 148),
    ('a630_zap.b01', 6536),
    ('a630_zap.b02', 1968),
)
# Segment digests the mdt is required to contain.
BOUND_PARTS = ('a630_zap.b00', 'a630_zap.b02')
ELF_MAGIC = b'\x7fELF'


class ZapSetError(ValueError):
    pass


def read_parts(folder):
    out = {}
    for name, size in ZAP_PARTS:
        path = Path(folder) / name
        if not path.is_file() or path.is_symlink():
            raise ZapSetError(f'missing or non-regular file: {name}')
        data = path.read_bytes()
        if len(data) != size:
            raise ZapSetError(f'{name}: {len(data)} bytes, expected {size}')
        out[name] = data
    return out


def check_set(folder):
    """Return the dict of parts, or raise ZapSetError with the reason."""
    parts = read_parts(folder)
    mdt = parts['a630_zap.mdt']
    if not mdt.startswith(ELF_MAGIC):
        raise ZapSetError('a630_zap.mdt is not an ELF image')
    if mdt[4] != 1:
        raise ZapSetError('a630_zap.mdt is not ELF32 (class 1)')
    found = {}
    for name in BOUND_PARTS:
        digest = hashlib.sha256(parts[name]).digest()
        offset = mdt.find(digest)
        if offset < 0:
            raise ZapSetError(
                f'{name} digest is not embedded in a630_zap.mdt; '
                'the four files are from different revisions')
        found[name] = offset
    return parts, found


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--firmware', type=Path, required=True,
                        help='directory holding a630_zap.{mdt,b00,b01,b02}')
    args = parser.parse_args(argv)
    try:
        parts, found = check_set(args.firmware)
    except ZapSetError as exc:
        print(f'REFUSE: {exc}', file=sys.stderr)
        return 1
    print('zap set is coherent (mdt binds b00 and b02):')
    for name, _size in ZAP_PARTS:
        if name in found:
            print(f'  {name:<14} digest at mdt+{found[name]}')
        else:
            print(f'  {name:<14} not hashed by design (hash segment)')
    for name, data in parts.items():
        print(f'  {name:<14} {len(data):5d} B  {hashlib.sha256(data).hexdigest()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
