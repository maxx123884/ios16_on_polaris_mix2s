#!/usr/bin/env python3
"""Tests for the device-tree window reader.

The fixtures are built here instead of committing a vendor blob: the point is
that the *matching* is right, and a 500 KB proprietary file in the repo would
not prove anything more.
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polaris_dt_regs as dt


def _padded(b: bytes) -> bytes:
    return b + b'\0' * ((4 - len(b) % 4) % 4)


def build_fdt(tree: dict) -> bytes:
    """Serialise {name, props?, children?} into a minimal valid FDT."""
    strings = bytearray()
    offsets: dict[str, int] = {}

    def name_off(name: str) -> int:
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode() + b'\0')
        return offsets[name]

    body = bytearray()

    def emit(node: dict) -> None:
        body.extend(struct.pack('>I', dt.FDT_BEGIN_NODE))
        body.extend(_padded(node['name'].encode() + b'\0'))
        for key, value in node.get('props', {}).items():
            body.extend(struct.pack('>III', dt.FDT_PROP, len(value),
                                    name_off(key)))
            body.extend(_padded(value))
        for child in node.get('children', []):
            emit(child)
        body.extend(struct.pack('>I', dt.FDT_END_NODE))

    emit(tree)
    body.extend(struct.pack('>I', dt.FDT_END))

    rsv = struct.pack('>QQ', 0, 0)
    off_struct = 40 + len(rsv)
    off_strings = off_struct + len(body)
    total = off_strings + len(strings)
    header = struct.pack('>10I', 0xd00dfeed, total, off_struct, off_strings,
                         40, 17, 16, 0, len(strings), len(body))
    return header + rsv + bytes(body) + bytes(strings)


def _reg(cells: list[int]) -> bytes:
    return b''.join(struct.pack('>I', c) for c in cells)


def gpu_tree(gpucc_compat: bytes = b'qcom,gpucc-sdm845-v2',
             gmu_regs: list[int] | None = None) -> dict:
    gmu_regs = gmu_regs if gmu_regs is not None else [
        0x0506A000, 0x30000, 0x0B200000, 0x300000]
    return {
        'name': '', 'props': {}, 'children': [
            {'name': 'soc', 'props': {}, 'children': [
                {'name': 'qcom,gpucc@5090000',
                 'props': {'compatible': gpucc_compat + b'\0',
                           'reg': _reg([0x05090000, 0x9000])}},
                {'name': 'qcom,gmu',
                 'props': {'compatible': b'qcom,gpu-gmu\0',
                           'reg': _reg(gmu_regs),
                           'reg-names': b'kgsl_gmu_reg\0kgsl_gmu_pdc_reg\0'}},
            ]},
            {'name': 'reserved-memory',
             'props': {'#address-cells': _reg([2]), '#size-cells': _reg([2])},
             'children': [
                {'name': 'gpu_region@0x8c415000',
                 'props': {'compatible': b'removed-dma-pool\0',
                           'reg': _reg([0, 0x8C415000, 0, 0x2000])}},
            ]},
        ],
    }


class Windows(unittest.TestCase):
    def test_extracts_the_four_windows(self):
        windows = dt.gpu_windows(dt.parse_fdt(build_fdt(gpu_tree())))
        self.assertEqual(windows['gpucc']['base'], 0x05090000)
        self.assertEqual(windows['gpucc']['size'], 0x9000)
        self.assertEqual(windows['gmu']['base'], 0x0506A000)
        self.assertEqual(windows['gmu']['size'], 0x30000)
        self.assertEqual(windows['gmu_pdc']['base'], 0x0B200000)
        self.assertEqual(windows['gmu_pdc']['size'], 0x300000)
        # /reserved-memory is #address-cells=2 here, so a 64-bit base must be
        # joined from its two cells instead of being read as a size.
        self.assertEqual(windows['zap_region']['base'], 0x8C415000)
        self.assertEqual(windows['zap_region']['size'], 0x2000)
        self.assertEqual(dt.check(windows), [])

    def test_provenance_is_recorded(self):
        windows = dt.gpu_windows(dt.parse_fdt(build_fdt(gpu_tree())))
        self.assertEqual(windows['gmu']['node'], 'soc/qcom,gmu')
        self.assertEqual(windows['gmu']['reg_name'], 'kgsl_gmu_reg')

    def test_plain_sdm845_gpucc_is_rejected(self):
        tree = gpu_tree(gpucc_compat=b'qcom,gpucc-sdm845')
        windows = dt.gpu_windows(dt.parse_fdt(build_fdt(tree)))
        problems = dt.check(windows)
        self.assertTrue(any('sdm845-v2' in p for p in problems), problems)

    def test_missing_gmu_is_reported(self):
        tree = gpu_tree()
        tree['children'][0]['children'] = tree['children'][0]['children'][:1]
        windows = dt.gpu_windows(dt.parse_fdt(build_fdt(tree)))
        problems = dt.check(windows)
        self.assertTrue(any('gmu' in p for p in problems), problems)

    def test_unaligned_base_is_reported(self):
        tree = gpu_tree(gmu_regs=[0x0506A004, 0x30000,
                                  0x0B200000, 0x300000])
        windows = dt.gpu_windows(dt.parse_fdt(build_fdt(tree)))
        self.assertTrue(any('4K aligned' in p for p in dt.check(windows)))

    def test_overlap_is_a_note_not_a_failure(self):
        # The vendor tree itself describes kgsl_gmu_reg as 0x506a000+0x30000,
        # which covers gpucc at 0x5090000; that must not fail the check.
        windows = dt.gpu_windows(dt.parse_fdt(build_fdt(gpu_tree())))
        self.assertEqual(dt.check(windows), [])
        self.assertTrue(any('gpucc' in n for n in dt.overlaps(windows)))

    def test_odd_reg_cell_count_is_a_dt_error(self):
        tree = gpu_tree()
        tree['children'][0]['children'][0]['props']['reg'] = _reg([1, 2, 3])
        with self.assertRaises(dt.DtError):
            dt.gpu_windows(dt.parse_fdt(build_fdt(tree)))

    def test_not_an_fdt(self):
        with self.assertRaises(dt.DtError):
            dt.parse_fdt(b'ANDROID!' + b'\0' * 64)


class BootImage(unittest.TestCase):
    def test_finds_the_fdt_appended_to_a_boot_kernel(self):
        fdt = build_fdt(gpu_tree())
        kernel = b'\x00' * 4096 + fdt + b'\x00' * 128
        image = bytearray(b'ANDROID!' + struct.pack('<I', len(kernel)) +
                          b'\0' * (2048 - 12))
        image.extend(kernel)
        found = dt.find_fdt(bytes(image))
        self.assertEqual(found, fdt)
        windows = dt.gpu_windows(dt.parse_fdt(found))
        self.assertEqual(windows['gmu_pdc']['base'], 0x0B200000)

    def test_boot_image_without_fdt(self):
        image = b'ANDROID!' + struct.pack('<I', 4096) + b'\0' * (2048 + 4096 - 12)
        with self.assertRaises(dt.DtError):
            dt.find_fdt(image)

    def test_garbage_file(self):
        with self.assertRaises(dt.DtError):
            dt.find_fdt(b'not a boot image')


if __name__ == '__main__':
    unittest.main()
