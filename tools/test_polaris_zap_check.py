#!/usr/bin/env python3
"""Tests for the zap bundle coherence check."""
import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'zap_check', HERE / 'polaris_zap_check.py')
z = importlib.util.module_from_spec(spec)
sys.modules['zap_check'] = z
spec.loader.exec_module(z)


def make_set(mdt_seed=b'mdt', bind=True, sizes=None):
    """Build a synthetic zap folder; returns the temp dir path."""
    tmp = Path(tempfile.mkdtemp(prefix='zapset-'))
    parts = {name: bytes([i]) * size
             for i, (name, size) in enumerate(z.ZAP_PARTS, start=1)}
    body = bytearray(b'\x7fELF\x01\x01\x01' + b'\0' * 32 + mdt_seed)
    if bind:
        for name in z.BOUND_PARTS:
            body += hashlib.sha256(parts[name]).digest()
    parts['a630_zap.mdt'] = bytes(body[:z.ZAP_PARTS[0][1]]).ljust(
        z.ZAP_PARTS[0][1], b'\0')
    if sizes:
        for name, size in sizes.items():
            parts[name] = parts[name][:size].ljust(size, b'\0')
    for name, data in parts.items():
        (tmp / name).write_bytes(data)
    return tmp


class CoherenceTest(unittest.TestCase):
    def test_accepts_coherent_set(self):
        folder = make_set()
        _parts, found = z.check_set(folder)
        self.assertIn('a630_zap.b00', found)
        self.assertIn('a630_zap.b02', found)

    def test_b01_is_not_required_in_the_hash_table(self):
        folder = make_set(bind=False)
        with self.assertRaises(z.ZapSetError):
            z.check_set(folder)

    def test_rejects_part_from_another_revision(self):
        folder = make_set()
        victim = folder / 'a630_zap.b00'
        data = bytearray(victim.read_bytes())
        data[0] ^= 0xFF
        victim.write_bytes(bytes(data))
        with self.assertRaises(z.ZapSetError) as ctx:
            z.check_set(folder)
        self.assertIn('different revisions', str(ctx.exception))

    def test_rejects_wrong_size(self):
        folder = make_set(sizes={'a630_zap.b02': 1000})
        with self.assertRaises(z.ZapSetError) as ctx:
            z.check_set(folder)
        self.assertIn('expected', str(ctx.exception))

    def test_rejects_non_elf_mdt(self):
        folder = make_set()
        (folder / 'a630_zap.mdt').write_bytes(b'\0' * z.ZAP_PARTS[0][1])
        with self.assertRaises(z.ZapSetError) as ctx:
            z.check_set(folder)
        self.assertIn('ELF', str(ctx.exception))

    def test_rejects_missing_file(self):
        folder = make_set()
        (folder / 'a630_zap.b01').unlink()
        with self.assertRaises(z.ZapSetError) as ctx:
            z.check_set(folder)
        self.assertIn('missing', str(ctx.exception))

    def test_main_exit_codes(self):
        good = make_set()
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = z.main(['--firmware', str(good)])
        self.assertEqual(rc, 0)
        bad = make_set(bind=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = z.main(['--firmware', str(bad)])
        self.assertEqual(rc, 1)
        self.assertIn('REFUSE', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
