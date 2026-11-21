#!/usr/bin/env python3
"""Add the six A630 firmware blobs to an already-built SWEETXNU payload.

The 0090 release ships a boot image whose ramdisk *is* the SWEETXNU payload
(kernel + ADT); the kernel Mach-O and the ADT are not published separately. So
the firmware cannot be added by re-running mkpayload.py --blob, which needs the
Mach-O. This tool does the equivalent transformation on the payload itself:

  * every original byte and segment is preserved, nothing is re-linked;
  * each blob becomes one more segment, placed at the old kernel_end and page
    rounded, exactly as mkpayload.py places --blob images;
  * /chosen/memory-map gets one NAME = <pa, exact bytes> entry per blob, which
    is the only thing polaris_gpu_read_handoff() looks at;
  * the header's nsegs / kernel_end / adt_off / adt_size are rewritten.

Order matters in the shim: it copies segments to phys_base + (vmaddr -
virt_base) and then writes the ADT at kernel_end. Placing blobs below the new
kernel_end keeps them out of the ADT and boot_args, and page rounding gives
each blob a private allocation so one short read cannot leak into the next.

Nothing here powers a GPU or talks to a device. It only rewrites a file.

  polaris_payload_blobs.py PAYLOAD OUT --firmware DIR --manifest SET.json
  polaris_payload_blobs.py PAYLOAD --verify --manifest SET.json
"""
import argparse
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adt  # noqa: E402
import polaris_firmware_set  # noqa: E402

MAGIC = b'SWEETXNU'
VERSION = 1
PAGE = 0x4000
HEADER = 0x10
CMDLINE = 0x48
BOOT_LINE_LENGTH = 608
SEG_TABLE = CMDLINE + BOOT_LINE_LENGTH	# 0x2a8
SEG_SIZE = 32

# The bootloader leaves the payload between ramdisk_addr (0x1000000) and the
# DTB it loads at 0x1dea000. That gap, not the boot partition, is the limit.
MAX_PAYLOAD_SIZE = 0x1dea000 - 0x1000000

# Reserved physical windows this driver must never hand to firmware.
RESERVED = (('zap', 0x8c415000, 0x2000), ('splash', 0x9d400000, 0x2400000))


class PayloadError(Exception):
    pass


def _round_up(value, align):
    return (value + align - 1) & ~(align - 1)


def _overlap(base, size, other, other_size):
    return base < other + other_size and other < base + size


def parse(payload):
    """Decode and cross-check a SWEETXNU payload. Returns a dict of its layout."""
    if len(payload) < SEG_TABLE:
        raise PayloadError('payload shorter than its header')
    magic, version, nsegs = struct.unpack_from('<8sII', payload, 0)
    if magic != MAGIC:
        raise PayloadError('not a SWEETXNU payload')
    if version != VERSION:
        raise PayloadError(f'unsupported payload version {version}')
    if not 0 < nsegs <= 64:
        raise PayloadError(f'implausible segment count {nsegs}')
    table_end = SEG_TABLE + SEG_SIZE * nsegs
    if table_end > len(payload):
        raise PayloadError('segment table runs past the payload')

    (virt_base, entry, adt_off, adt_size, kernel_end,
     phys_base, mem_size) = struct.unpack_from('<7Q', payload, HEADER)
    if not mem_size or virt_base + mem_size < virt_base:
        raise PayloadError('invalid managed RAM range')
    if adt_off & 7 or adt_size < 8 or adt_off + adt_size > len(payload):
        raise PayloadError('ADT is not inside the payload or not 8-byte aligned')

    segs, ranges = [], []
    for i in range(nsegs):
        vmaddr, vmsize, file_off, file_size = struct.unpack_from(
            '<4Q', payload, SEG_TABLE + SEG_SIZE * i)
        if file_off < table_end or file_off + file_size > len(payload):
            raise PayloadError(f'segment {i} data is outside the payload')
        if not file_size or file_size > vmsize:
            raise PayloadError(f'segment {i} has an invalid file/vm size')
        for base, size in ranges:
            if _overlap(file_off, file_size, base, size):
                raise PayloadError(f'segment {i} overlaps another segment in the file')
        ranges.append((file_off, file_size))
        segs.append(dict(vmaddr=vmaddr, vmsize=vmsize,
                         file_off=file_off, file_size=file_size))

    expected_end = _round_up(max(s['vmaddr'] + s['vmsize'] for s in segs), PAGE)
    if kernel_end != expected_end:
        raise PayloadError(f'kernel_end {kernel_end:#x} is not the rounded end '
                           f'of the segments ({expected_end:#x})')

    root, end = adt.parse(payload[adt_off:adt_off + adt_size])
    if end != adt_size:
        raise PayloadError('ADT has trailing bytes')
    chosen = next((c for c in root.children if c.name == 'chosen'), None)
    mmap = chosen and next((c for c in chosen.children if c.name == 'memory-map'), None)
    if not chosen or not mmap:
        raise PayloadError('ADT has no /chosen/memory-map')
    dram = []
    for key in ('dram-base', 'dram-size'):
        value = chosen.prop(key)
        if value is None or len(value) != 8:
            raise PayloadError(f'/chosen lacks a valid {key}')
        dram.append(struct.unpack('<Q', value)[0])
    dram_base, dram_size = dram

    return dict(magic=magic, version=version, nsegs=nsegs, virt_base=virt_base,
                entry=entry, adt_off=adt_off, adt_size=adt_size,
                kernel_end=kernel_end, phys_base=phys_base, mem_size=mem_size,
                segs=segs, root=root, chosen=chosen, mmap=mmap,
                dram_base=dram_base, dram_size=dram_size,
                cmdline=payload[CMDLINE:CMDLINE + BOOT_LINE_LENGTH].split(b'\0')[0])


def memory_map_entry(mmap, name):
    value = mmap.prop(name)
    if value is None:
        return None
    if len(value) != 16:
        raise PayloadError(f'memory-map entry {name} is {len(value)} bytes, expected 16')
    return struct.unpack('<2Q', value)


def add_blobs(payload, blobs, max_size=MAX_PAYLOAD_SIZE):
    """Return (new payload, info). blobs is [(NAME, bytes)] in enum order."""
    info = parse(payload)
    if not blobs:
        raise PayloadError('no blobs to add')

    seen_names = set()
    placements = []
    next_va = info['kernel_end']
    pa_of_va = (info['phys_base'] - info['virt_base']) & ((1 << 64) - 1)

    existing = [(s['vmaddr'] + pa_of_va, _round_up(s['vmsize'], PAGE))
                for s in info['segs']]
    for name, data in blobs:
        if (not isinstance(name, str) or not name or not name.isascii() or
                len(name) > 64 or
                not all(c.isalnum() or c in '-_.' for c in name)):
            raise PayloadError(f'invalid memory-map name: {name!r}')
        if name in seen_names:
            raise PayloadError(f'duplicate blob name: {name}')
        seen_names.add(name)
        if memory_map_entry(info['mmap'], name) is not None:
            raise PayloadError(f'/chosen/memory-map already has {name}')
        if not data:
            raise PayloadError(f'empty firmware blob: {name}')

        pages = _round_up(len(data), PAGE)
        va = next_va
        if va + pages > info['virt_base'] + info['mem_size']:
            raise PayloadError(f'{name} does not fit in the payload managed RAM')
        pa = (va + pa_of_va) & ((1 << 64) - 1)
        if pa + pages > info['dram_base'] + info['dram_size']:
            raise PayloadError(f'{name} falls outside managed RAM at {pa:#x}')
        for label, base, size in RESERVED:
            if _overlap(pa, pages, base, size):
                raise PayloadError(f'{name} overlaps the {label} reservation')
        for base, size in existing:
            if _overlap(pa, pages, base, size):
                raise PayloadError(f'{name} overlaps a segment at {base:#x}')
        existing.append((pa, pages))
        placements.append(dict(name=name, data=data, va=va, pa=pa,
                               bytes=len(data), pages=pages))
        next_va = va + pages

    # The segment table grows into what used to be segment data, so rebuild the
    # file: header, then every segment's bytes in order, then ADT.
    nsegs = info['nsegs'] + len(placements)
    out = bytearray(SEG_TABLE + SEG_SIZE * nsegs)
    table = []
    for seg in info['segs']:
        data = payload[seg['file_off']:seg['file_off'] + seg['file_size']]
        table.append((seg['vmaddr'], seg['vmsize'], len(out), len(data)))
        out += data
        out += b'\0' * (-len(out) & 0xf)
    for p in placements:
        table.append((p['va'], p['pages'], len(out), p['bytes']))
        out += p['data']
        out += b'\0' * (-len(out) & 0xf)

    root = info['root']
    mmap = info['mmap']
    for p in placements:
        mmap.props.append((p['name'], 0, struct.pack('<2Q', p['pa'], p['bytes'])))
    adt_blob = adt.serialize(root)

    adt_off = len(out)
    out += adt_blob
    out += b'\0' * (-len(out) & 0xf)
    if len(out) > max_size:
        raise PayloadError(f'payload {len(out)} bytes exceeds the ABL gap {max_size}')

    struct.pack_into('<8sII', out, 0, MAGIC, VERSION, nsegs)
    struct.pack_into('<7Q', out, HEADER, info['virt_base'], info['entry'],
                     adt_off, len(adt_blob), next_va,
                     info['phys_base'], info['mem_size'])
    out[CMDLINE:CMDLINE + len(info['cmdline'])] = info['cmdline']
    for i, t in enumerate(table):
        struct.pack_into('<4Q', out, SEG_TABLE + SEG_SIZE * i, *t)

    result = dict(payload_size=len(out), max_size=max_size,
                  remaining=max_size - len(out), nsegs=nsegs,
                  adt_size=len(adt_blob), kernel_end=next_va,
                  placements=[{k: v for k, v in p.items() if k != 'data'}
                              for p in placements])
    return bytes(out), result


def verify(payload, manifest_entries=None):
    """Prove a payload's firmware layout and digests from the payload alone."""
    info = parse(payload)
    pa_of_va = (info['phys_base'] - info['virt_base']) & ((1 << 64) - 1)
    found = {}
    for seg in info['segs']:
        pa = (seg['vmaddr'] + pa_of_va) & ((1 << 64) - 1)
        found[(pa, seg['file_size'])] = seg
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise PayloadError(f'payload {len(payload)} exceeds the ABL gap {MAX_PAYLOAD_SIZE}')
    occupancy = [((seg['vmaddr'] + pa_of_va) & ((1 << 64) - 1),
                  _round_up(seg['vmsize'], PAGE)) for seg in info['segs']]
    blobs = []
    for name, _ in polaris_firmware_set.BLOBS:
        entry = memory_map_entry(info['mmap'], name)
        if entry is None:
            continue
        pa, size = entry
        pages = _round_up(size, PAGE)
        if pa & (PAGE - 1) or pa + pages > info['dram_base'] + info['dram_size']:
            raise PayloadError(f'{name} at {pa:#x} is not a page-aligned allocation '
                               'inside managed RAM')
        for label, base, length in RESERVED:
            if _overlap(pa, pages, base, length):
                raise PayloadError(f'{name} overlaps the {label} reservation')
        seg = found.get((pa, size))
        if seg is None:
            raise PayloadError(f'{name} entry {pa:#x}/{size} has no matching segment')
        others = [o for o in occupancy if o != (pa, _round_up(seg['vmsize'], PAGE))]
        if any(_overlap(pa, pages, base, length) for base, length in others):
            raise PayloadError(f'{name} overlaps another segment')
        data = payload[seg['file_off']:seg['file_off'] + size]
        blobs.append(dict(name=name, pa=pa, va=seg['vmaddr'], bytes=size,
                          sha256=hashlib.sha256(data).hexdigest()))
    if manifest_entries:
        by_name = {e['blob']: e for e in manifest_entries}
        if set(by_name) != {b['name'] for b in blobs}:
            raise PayloadError('payload firmware set does not match the manifest')
        for blob in blobs:
            want = by_name[blob['name']]
            if blob['bytes'] != want['bytes']:
                raise PayloadError(f'{blob["name"]} is {blob["bytes"]} bytes, '
                                   f'manifest says {want["bytes"]}')
            if blob['sha256'] != want['sha256']:
                raise PayloadError(f'{blob["name"]} does not match the pinned digest')
    return info, blobs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('payload', type=Path)
    ap.add_argument('out', nargs='?', type=Path)
    ap.add_argument('--verify', action='store_true',
                    help='check an existing payload instead of writing one')
    ap.add_argument('--firmware', type=Path,
                    help='directory holding the six firmware files')
    ap.add_argument('--manifest', type=Path,
                    help='pinned firmware set (size + SHA-256) to obey')
    ap.add_argument('--max-size', type=lambda n: int(n, 0), default=MAX_PAYLOAD_SIZE)
    a = ap.parse_args()

    try:
        payload = a.payload.read_bytes()
        entries = None
        if a.manifest:
            _, entries = polaris_firmware_set.load(a.manifest)
        if a.verify:
            info, blobs = verify(payload, entries)
            print(f'payload {len(payload)} bytes, {info["nsegs"]} segments, '
                  f'kernel_end {info["kernel_end"]:#x}')
            for b in blobs:
                print(f'{b["name"]:12s} va={b["va"]:#018x} pa={b["pa"]:#012x} '
                      f'{b["bytes"]:6d} sha256={b["sha256"]}')
            if not blobs:
                print('no firmware blobs present')
            return
        if not a.out or not a.firmware:
            ap.error('writing needs OUT and --firmware')
        if entries is None:
            ap.error('writing needs --manifest so the bytes are pinned')
        blobs = []
        for blob, filename in polaris_firmware_set.BLOBS:
            want = next(e for e in entries if e['blob'] == blob)
            path = a.firmware / filename
            if not path.is_file() or path.is_symlink():
                raise PayloadError(f'missing or nonregular firmware: {filename}')
            data = path.read_bytes()
            if len(data) != want['bytes'] or \
                    hashlib.sha256(data).hexdigest() != want['sha256']:
                raise PayloadError(f'{filename} does not match the pinned set')
            blobs.append((blob, data))
        out, info = add_blobs(payload, blobs, a.max_size)
        a.out.write_bytes(out)
        print(f'{a.out}: {info["payload_size"]} bytes '
              f'({info["remaining"]} under the {info["max_size"]}-byte ABL gap)')
        for p in info['placements']:
            print(f'{p["name"]:12s} va={p["va"]:#018x} pa={p["pa"]:#012x} '
                  f'{p["bytes"]:6d} -> {p["pages"]} bytes')
        verify(out, entries)
    except (PayloadError, polaris_firmware_set.FirmwareSetError,
            OSError, ValueError, StopIteration, struct.error) as exc:
        ap.error(str(exc))


if __name__ == '__main__':
    main()
