#!/usr/bin/env python3
"""Offline parser for the m1n RPMh command-DB ring dump (no device access).

The kernel probe (patches 0163/0164) dumps the command DB at 0x0C3F0000 into
the console ring, one line per 16 bytes:

  POLARIS GPU: m1n: <off> <w0> <w1> <w2> <w3> |ascii|

This tool reassembles those lines back into a byte buffer, then walks the cmd-db
header and entries to list resource names and the RPMh address a vote must
name. Reads are strictly bounded; this module never reads physical memory,
issues a vote, or touches hardware. The strict ARC/BCM validators in
polaris_arc_db / polaris_bcm_db remain the proof layer; this walker is the
discovery layer that survives a partial/short dump.
"""
import argparse
import re
import struct
from pathlib import Path

HEADER = 144
ENTRY = 24
SLOT = 16
MAGIC = 0x0C0330DB

LINE_RE = re.compile(
    r'm1n:\s*([0-9a-fA-F]{3})\s+'
    r'([0-9a-fA-F]{8})\s+([0-9a-fA-F]{8})\s+'
    r'([0-9a-fA-F]{8})\s+([0-9a-fA-F]{8})'
)

# Names that matter for the GPU bring-up. Matched case-insensitively as a
# substring so a short 8-char DB ID still flags the CX/GX/GMU rails and clocks.
GPU_HINTS = ('gpu', 'gfx', 'gx', 'cx', 'gmu', 'clk', 'gdsc', 'dsp', 'mmx')


class CommandDbParseError(ValueError):
    pass


def reassemble(text: str) -> bytes:
    """Rebuild the DB byte buffer from m1n ring lines.

    Missing lines leave zero bytes; offsets are byte offsets, words are stored
    little-endian.
    """
    words: dict[int, int] = {}
    size = 0
    for match in LINE_RE.finditer(text):
        off = int(match.group(1), 16)
        vals = [int(match.group(i), 16) for i in range(2, 6)]
        for j, value in enumerate(vals):
            words[off + j * 4] = value
        size = max(size, off + 16)
    if not words:
        raise CommandDbParseError('no m1n command-DB lines found in the log')
    buf = bytearray(size)
    for off, value in words.items():
        struct.pack_into('<I', buf, off, value)
    return bytes(buf)


def walk_entries(data: bytes) -> list[dict]:
    """Bounded walk of every cmd-db entry. Does not reject on name/slave."""
    if len(data) < HEADER:
        raise CommandDbParseError(f'dump too small: {len(data)} < header {HEADER}')
    if struct.unpack_from('<I', data, 4)[0] != MAGIC:
        raise CommandDbParseError('command-DB magic mismatch (dump may be truncated)')

    entries: list[dict] = []
    for slot in range(8):
        h = 8 + slot * SLOT
        if h + SLOT > len(data):
            break
        slave, head_off, aux_off, count, version = struct.unpack_from('<5H', data, h)
        if slave == 0:
            break
        entry_start = HEADER + head_off
        aux_start = HEADER + aux_off
        if count == 0:
            continue
        for j in range(count):
            p = entry_start + j * ENTRY
            if p + ENTRY > len(data):
                break
            name_raw = data[p:p + 8]
            name = name_raw.split(b'\0', 1)[0].decode('ascii', 'replace')
            address = struct.unpack_from('<I', data, p + 16)[0]
            length, aoff = struct.unpack_from('<2H', data, p + 20)
            entries.append({
                'slave': slave,
                'name': name,
                'address': address,
                'length': length,
                'aux': aux_start + aoff,
                'version': version,
            })
    return entries


def is_gpu(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in GPU_HINTS)


def load_log(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:8] == b'SWEETLOG':
        # Persistent ring block: reuse the same layout as sweet-log.py.
        _magic, _version, size, total, _seq = struct.unpack_from('<8sIIQQ', raw, 0)
        ring = raw[4096:4096 + size]
        if total <= size:
            text = ring[:total]
        else:
            start = total % size
            text = ring[start:] + ring[:start]
        return text.decode('utf-8', 'replace').replace('\r', '')
    return raw.decode('utf-8', 'replace').replace('\r', '')


def render_lines(data: bytes) -> str:
    """Inverse of reassemble: emit the probe's own line format (for tests)."""
    out = []
    for off in range(0, len(data), 16):
        words = []
        for j in range(4):
            p = off + j * 4
            words.append(struct.unpack_from('<I', data, p)[0] if p + 4 <= len(data) else 0)
        ascii_ = ''.join(
            chr(b) if 32 <= b < 127 else '.' for b in data[off:off + 16]
        ).ljust(16, '.')
        out.append(f'POLARIS GPU: m1n: {off:03x} {words[0]:08x} {words[1]:08x} '
                   f'{words[2]:08x} {words[3]:08x} |{ascii_}|')
    return '\n'.join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path,
                        help='decoded ring .txt, or the SWEETLOG .bin block')
    parser.add_argument('--all', action='store_true',
                        help='list every entry, not just GPU-related ones')
    args = parser.parse_args()

    text = load_log(args.log)
    data = reassemble(text)
    entries = walk_entries(data)

    if not entries:
        print('(no command-DB entries parsed)')
        return

    shown = [e for e in entries if args.all or is_gpu(e['name'])]
    for e in shown:
        flag = '*' if is_gpu(e['name']) else ' '
        print(f"{flag} slave={e['slave']} {e['name']:<16} "
              f"address={e['address']:#010x} len={e['length']} aux={e['aux']:#x}")
    if not args.all:
        gpu = [e for e in entries if is_gpu(e['name'])]
        print(f'\n{len(gpu)} GPU-related of {len(entries)} entries '
              f'(--all to show the rest). Dump is {len(data)} bytes.')


if __name__ == '__main__':
    main()
