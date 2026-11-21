#!/usr/bin/env python3
"""Emit the `struct pg_expect` table for polaris_gpu.c from real firmware.

`bsd/dev/polaris_gpu.c` ships the device-observed *sizes* with zeroed digests,
so `pg_expect_complete()` refuses and the GPU gate stops at the firmware stage.
This tool produces the exact C initializer that proves the bytes, once the six
vendor files are available on the host:

  python3 tools/polaris_gpu_expect_table.py --firmware DIR
  python3 tools/polaris_gpu_expect_table.py --firmware DIR --apply bsd/dev/polaris_gpu.c

`--apply` rewrites only the digests inside the existing initializer, in place,
then the diff can be reviewed like any other patch. Nothing here talks to the
phone, and a file that is not one of the six known blobs is never accepted.
"""
import argparse
import hashlib
import sys
from pathlib import Path

# Order matches enum pg_blob_id / the ADT memory-map names in polaris_gpu.c.
FILES = (
    ('GPU-SQE', 'a630_sqe.fw', 32136),
    ('GPU-GMU', 'a630_gmu.bin', 32768),
    ('GPU-ZAP-MDT', 'a630_zap.mdt', 6684),
    ('GPU-ZAP-B00', 'a630_zap.b00', 148),
    ('GPU-ZAP-B01', 'a630_zap.b01', 6536),
    ('GPU-ZAP-B02', 'a630_zap.b02', 1968),
)


def read_blobs(folder):
    out = []
    for name, filename, expected in FILES:
        path = folder / filename
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f'missing or non-regular firmware: {filename}')
        data = path.read_bytes()
        if len(data) != expected:
            raise SystemExit(
                f'{filename}: {len(data)} bytes, device-observed size is {expected}; '
                'refusing to hash a firmware set this port has never seen')
        out.append((name, filename, data))
    return out


def c_table(blobs):
    lines = ['static const struct pg_expect polaris_gpu_expect[PG_BLOB_COUNT] = {']
    for i, (name, filename, data) in enumerate(blobs):
        digest = hashlib.sha256(data).hexdigest()
        octets = ', '.join('0x' + digest[j:j + 2] for j in range(0, 64, 2))
        comma = ',' if i != len(blobs) - 1 else ''
        lines.append(f'\t/* {name:<11} {filename:<14} */')
        lines.append(f'\t{{ {len(data)}, {{ {octets} }} }}{comma}')
    lines.append('};')
    return '\n'.join(lines)


def replace_table(source, table):
    start = source.find('static const struct pg_expect polaris_gpu_expect[')
    if start < 0:
        raise SystemExit('polaris_gpu_expect initializer not found in the source')
    end = source.find('};', start)
    if end < 0:
        raise SystemExit('unterminated polaris_gpu_expect initializer')
    return source[:start] + table + source[end + 2:]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--firmware', type=Path, required=True,
                        help='directory holding the six Polaris GPU firmware files')
    parser.add_argument('--apply', type=Path,
                        help='rewrite the initializer in this C file in place')
    args = parser.parse_args()

    blobs = read_blobs(args.firmware)
    table = c_table(blobs)
    if args.apply:
        source = args.apply.read_text(encoding='utf-8')
        updated = replace_table(source, table)
        if updated == source:
            print(f'{args.apply}: table already current')
        else:
            args.apply.write_text(updated, encoding='utf-8')
            print(f'{args.apply}: digests updated, review the diff before building')
    else:
        print(table)
    for name, filename, data in blobs:
        print(f'{name}: {len(data)} bytes sha256={hashlib.sha256(data).hexdigest()}',
              file=sys.stderr)


if __name__ == '__main__':
    main()
