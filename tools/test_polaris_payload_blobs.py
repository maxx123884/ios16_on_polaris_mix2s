#!/usr/bin/env python3
"""Host-only tests for adding firmware blobs to a SWEETXNU payload.

No kernel, no Mach-O, no device: the fixtures are hand-built payloads.
"""
import hashlib
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adt
import polaris_payload_blobs as plb

VIRT = 0xfffffe0006000000
PHYS = 0x100000000
MEM = 0x20000000

NAMES = [blob for blob, _ in __import__('polaris_firmware_set').BLOBS]
SIZES = [97, 32768, 6684, 148, 6536, 1968]


def make_adt(dram_base=PHYS, dram_size=0x7cc00000, entries=()):
    root = adt.Node()
    root.props = [('name', 0, b'\0'), ('AAPL,phandle', 0, struct.pack('<I', 1))]
    chosen = adt.Node()
    chosen.props = [('name', 0, b'chosen')]
    chosen.props.append(('dram-base', 0, struct.pack('<Q', dram_base)))
    chosen.props.append(('dram-size', 0, struct.pack('<Q', dram_size)))
    mmap = adt.Node()
    mmap.props = [('name', 0, b'memory-map')]
    for name, pa, size in entries:
        mmap.props.append((name, 0, struct.pack('<2Q', pa, size)))
    chosen.children = [mmap]
    root.children = [chosen]
    return adt.serialize(root)


def make_payload(segs=None, adt_blob=None, virt_base=VIRT, phys_base=PHYS,
                 mem_size=MEM, kernel_end=None, cmdline=b'test=1',
                 dram_size=0x7cc00000):
    """segs = [(va_offset, vmsize, bytes)] with va = virt_base + va_offset."""
    segs = segs or [(0x4000, 0x4000, bytes(range(256)) * 4)]
    adt_blob = adt_blob if adt_blob is not None else make_adt(dram_size=dram_size)
    nsegs = len(segs)
    out = bytearray(plb.SEG_TABLE + plb.SEG_SIZE * nsegs)
    table = []
    for off, vmsize, data in segs:
        table.append((virt_base + off, vmsize, len(out), len(data)))
        out += data
        out += b'\0' * (-len(out) & 0xf)
    adt_off = len(out)
    out += adt_blob
    out += b'\0' * (-len(out) & 0xf)
    if kernel_end is None:
        kernel_end = plb._round_up(max(va + vs for va, vs, _, _ in table), plb.PAGE)
    struct.pack_into('<8sII', out, 0, plb.MAGIC, plb.VERSION, nsegs)
    struct.pack_into('<7Q', out, plb.HEADER, virt_base, virt_base + 0x4000,
                     adt_off, len(adt_blob), kernel_end, phys_base, mem_size)
    out[plb.CMDLINE:plb.CMDLINE + len(cmdline)] = cmdline
    for i, t in enumerate(table):
        struct.pack_into('<4Q', out, plb.SEG_TABLE + plb.SEG_SIZE * i, *t)
    return bytes(out)


def blobs(sizes=SIZES):
    return [(name, bytes((i * 7 + len(name)) & 0xff for i in range(size)))
            for name, size in zip(NAMES, sizes)]


def manifest(sizes=SIZES):
    return [dict(blob=name, bytes=size,
                 sha256=hashlib.sha256(data).hexdigest())
            for (name, data), size in zip(blobs(sizes), sizes)]


class AddBlobs(unittest.TestCase):
    def test_six_segments_with_matching_adt_entries(self):
        base = make_payload()
        out, info = plb.add_blobs(base, blobs())
        parsed = plb.parse(out)
        self.assertEqual(parsed['nsegs'], 1 + 6)
        self.assertEqual(info['nsegs'], 7)
        pa_of_va = (PHYS - VIRT) & ((1 << 64) - 1)
        for i, (name, data) in enumerate(blobs()):
            entry = plb.memory_map_entry(parsed['mmap'], name)
            self.assertIsNotNone(entry, name)
            pa, size = entry
            self.assertEqual(size, len(data))
            self.assertEqual(pa & (plb.PAGE - 1), 0)
            seg = [s for s in parsed['segs'] if s['vmaddr'] == (pa - pa_of_va) & ((1 << 64) - 1)]
            self.assertEqual(len(seg), 1)
            self.assertEqual(seg[0]['file_size'], len(data))
            self.assertEqual(seg[0]['vmsize'], plb._round_up(len(data), plb.PAGE))
            self.assertEqual(out[seg[0]['file_off']:seg[0]['file_off'] + len(data)], data)
        self.assertEqual(parsed['kernel_end'], info['kernel_end'])
        self.assertEqual(parsed['kernel_end'] - base_kernel_end(base),
                         sum(plb._round_up(s, plb.PAGE) for s in SIZES))

    def test_original_segments_survive_byte_for_byte(self):
        base = make_payload()
        before = plb.parse(base)
        out, _ = plb.add_blobs(base, blobs())
        after = plb.parse(out)
        for old, new in zip(before['segs'], after['segs'][:before['nsegs']]):
            self.assertEqual(
                base[old['file_off']:old['file_off'] + old['file_size']],
                out[new['file_off']:new['file_off'] + new['file_size']])
            self.assertEqual((old['vmaddr'], old['vmsize']), (new['vmaddr'], new['vmsize']))

    def test_verify_recovers_digests_and_refuses_a_flipped_byte(self):
        out, _ = plb.add_blobs(make_payload(), blobs())
        _, found = plb.verify(out, manifest())
        self.assertEqual([b['name'] for b in found], NAMES)
        idx = next(s['file_off'] for s in plb.parse(out)['segs']
                   if s['file_size'] == SIZES[3])	# GPU-ZAP-B00
        broken = bytearray(out)
        broken[idx] ^= 0xff
        with self.assertRaisesRegex(plb.PayloadError, 'pinned digest'):
            plb.verify(bytes(broken), manifest())

    def test_verify_refuses_a_manifest_of_another_set(self):
        out, _ = plb.add_blobs(make_payload(), blobs())
        other = manifest()
        other[0]['bytes'] += 4
        with self.assertRaisesRegex(plb.PayloadError, 'manifest says'):
            plb.verify(out, other)

    def test_already_present_entry_is_refused(self):
        base = make_payload(adt_blob=make_adt(entries=[('GPU-GMU', PHYS + 0x9000, 3)]))
        with self.assertRaisesRegex(plb.PayloadError, 'already has GPU-GMU'):
            plb.add_blobs(base, blobs())

    def test_duplicate_and_invalid_names_are_refused(self):
        with self.assertRaisesRegex(plb.PayloadError, 'duplicate blob name'):
            plb.add_blobs(make_payload(), [('GPU-SQE', b'a'), ('GPU-SQE', b'b')])
        for bad in ('', 'bad name', 'x' * 65, 'na\u00efve'):
            with self.assertRaisesRegex(plb.PayloadError, 'invalid memory-map name'):
                plb.add_blobs(make_payload(), [(bad, b'a')])

    def test_empty_blob_is_refused(self):
        with self.assertRaisesRegex(plb.PayloadError, 'empty firmware blob'):
            plb.add_blobs(make_payload(), [('GPU-SQE', b'')])

    def test_blob_may_not_leave_managed_ram(self):
        base = make_payload(dram_size=0x4000 * 2)
        with self.assertRaisesRegex(plb.PayloadError, 'outside managed RAM'):
            plb.add_blobs(base, blobs())

    def test_blob_may_not_land_in_a_reserved_window(self):
        # Kernel ends 0x8000 above 0x9d3f0000, so the second page-rounded blob
        # starts exactly at the splash framebuffer reservation.
        base = make_payload(phys_base=0x9d3f0000, adt_blob=make_adt(
            dram_base=0x9d3f0000, dram_size=0x2400000))
        with self.assertRaisesRegex(plb.PayloadError, 'overlaps the splash'):
            plb.add_blobs(base, blobs())

    def test_max_size_is_enforced_before_writing(self):
        base = make_payload()
        with self.assertRaisesRegex(plb.PayloadError, 'exceeds the ABL gap'):
            plb.add_blobs(base, blobs(), max_size=len(base) + 16)

    def test_added_payload_stays_self_consistent(self):
        out, info = plb.add_blobs(make_payload(), blobs())
        parsed = plb.parse(out)
        self.assertEqual(parsed['cmdline'], b'test=1')
        self.assertEqual(info['remaining'], plb.MAX_PAYLOAD_SIZE - len(out))
        self.assertNotEqual(len(out) % plb.PAGE, 0)


class Parse(unittest.TestCase):
    def test_bad_magic(self):
        bad = bytearray(make_payload())
        bad[0] = ord('X')
        with self.assertRaisesRegex(plb.PayloadError, 'not a SWEETXNU'):
            plb.parse(bytes(bad))

    def test_bad_version(self):
        bad = bytearray(make_payload())
        struct.pack_into('<I', bad, 8, 2)
        with self.assertRaisesRegex(plb.PayloadError, 'unsupported payload version'):
            plb.parse(bytes(bad))

    def test_kernel_end_must_match_segments(self):
        base = make_payload()
        bad = bytearray(base)
        struct.pack_into('<Q', bad, plb.HEADER + 0x20, VIRT + 0x20000)
        with self.assertRaisesRegex(plb.PayloadError, 'kernel_end'):
            plb.parse(bytes(bad))

    def test_segment_data_must_be_inside(self):
        base = make_payload()
        bad = bytearray(base)
        struct.pack_into('<Q', bad, plb.SEG_TABLE + 16, len(base) + 1)
        with self.assertRaisesRegex(plb.PayloadError, 'outside the payload'):
            plb.parse(bytes(bad))

    def test_adt_must_have_memory_map(self):
        root = adt.Node()
        root.props = [('name', 0, b'\0')]
        chosen = adt.Node()
        chosen.props = [('name', 0, b'chosen'),
                        ('dram-base', 0, struct.pack('<Q', PHYS)),
                        ('dram-size', 0, struct.pack('<Q', 0x10000000))]
        root.children = [chosen]
        with self.assertRaisesRegex(plb.PayloadError, 'memory-map'):
            plb.parse(make_payload(adt_blob=adt.serialize(root)))


def base_kernel_end(payload):
    return plb.parse(payload)['kernel_end']


if __name__ == '__main__':
    unittest.main()
