#!/usr/bin/env python3
"""Tests for polaris_gpu_expect_table.py. Host-only, no firmware required."""
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'expect_table', HERE / 'polaris_gpu_expect_table.py')
tool = importlib.util.module_from_spec(spec)
sys.modules['expect_table'] = tool
spec.loader.exec_module(tool)


def write_set(folder, sizes=None):
    """Deterministic pseudo-firmware of the exact device-observed sizes."""
    sizes = sizes or {filename: size for _, filename, size in tool.FILES}
    for _, filename, _ in tool.FILES:
        count = sizes[filename]
        body = bytes((i * 31 + len(filename)) & 0xff for i in range(count))
        (folder / filename).write_bytes(body)
    return


class ExpectTable(unittest.TestCase):
    def test_names_order_and_sizes_match_the_c_enum(self):
        # Same order as enum pg_blob_id; a reorder would silently pair the
        # wrong digest with the wrong ADT entry.
        self.assertEqual([n for n, _, _ in tool.FILES],
                         ['GPU-SQE', 'GPU-GMU', 'GPU-ZAP-MDT',
                          'GPU-ZAP-B00', 'GPU-ZAP-B01', 'GPU-ZAP-B02'])
        self.assertEqual([s for _, _, s in tool.FILES],
                         [32136, 32768, 6684, 148, 6536, 1968])

    def test_table_carries_the_real_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_set(folder)
            blobs = tool.read_blobs(folder)
            table = tool.c_table(blobs)
            first = hashlib.sha256((folder / 'a630_sqe.fw').read_bytes()).hexdigest()
            octets = ', '.join('0x' + first[i:i + 2] for i in range(0, 64, 2))
            self.assertIn(octets, table)
            self.assertEqual(table.count('} }'), 6)
            self.assertNotIn('0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, '
                             '0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, '
                             '0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, '
                             '0x00, 0x00, 0x00, 0x00, 0x00', table)

    def test_wrong_size_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_set(folder)
            (folder / 'a630_gmu.bin').write_bytes(b'\0' * 32769)
            with self.assertRaises(SystemExit) as caught:
                tool.read_blobs(folder)
            self.assertIn('32769', str(caught.exception))

    def test_missing_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_set(folder)
            (folder / 'a630_zap.b01').unlink()
            with self.assertRaises(SystemExit):
                tool.read_blobs(folder)

    def test_apply_rewrites_only_the_initializer(self):
        source = ('header\nstatic const struct pg_expect polaris_gpu_expect'
                  '[PG_BLOB_COUNT] = {\n\t{ 32136, { 0 } },\n};\ntrailer\n')
        updated = tool.replace_table(source, 'TABLE')
        self.assertEqual(updated, 'header\nTABLE\ntrailer\n')

    def test_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_set(folder)
            table = tool.c_table(tool.read_blobs(folder))
            source = 'a\nstatic const struct pg_expect polaris_gpu_expect[X] = {\n\t{ 0, { 0 } }\n};\nb\n'
            once = tool.replace_table(source, table)
            twice = tool.replace_table(once, table)
            self.assertEqual(once, twice)


if __name__ == '__main__':
    unittest.main(verbosity=2)
