#!/usr/bin/env python3
"""Host-only tests for the firmware-set manifest. No firmware required."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polaris_firmware_set as fset

SIZES = {'a630_sqe.fw': 97, 'a630_gmu.bin': 64, 'a630_zap.mdt': 52,
         'a630_zap.b00': 11, 'a630_zap.b01': 23, 'a630_zap.b02': 7}


def write_set(folder, sizes=None):
    sizes = sizes or SIZES
    for _, filename in fset.BLOBS:
        count = sizes[filename]
        (folder / filename).write_bytes(
            bytes((i * 13 + len(filename)) & 0xff for i in range(count)))


class FirmwareSet(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_set(self.root)

    def test_record_then_verify_round_trip(self):
        manifest = fset.record(self.root, 'unit-test', archive='a.tgz',
                               member='images/vendor.img', path_in_image='/firmware')
        self.assertEqual([f['blob'] for f in manifest['files']], fset.BLOB_ORDER)
        self.assertEqual(manifest['source']['member'], 'images/vendor.img')
        path = self.root / 'set.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        loaded, entries = fset.load(path)
        blobs = fset.verify(self.root, entries)
        self.assertEqual(len(blobs), 6)
        self.assertEqual(blobs[0][1], 'a630_sqe.fw')

    def test_verify_refuses_changed_byte(self):
        entries = fset.validate(fset.record(self.root, 'unit-test'))
        (self.root / 'a630_zap.b01').write_bytes(b'x' * SIZES['a630_zap.b01'])
        with self.assertRaisesRegex(fset.FirmwareSetError, 'does not match the set'):
            fset.verify(self.root, entries)

    def test_verify_refuses_changed_size(self):
        _, entries = fset.load(_write_manifest(self.root))
        (self.root / 'a630_gmu.bin').write_bytes(b'\0' * 65)
        with self.assertRaisesRegex(fset.FirmwareSetError, 'bytes, set pins'):
            fset.verify(self.root, entries)

    def test_verify_refuses_missing_file(self):
        _, entries = fset.load(_write_manifest(self.root))
        (self.root / 'a630_zap.b02').unlink()
        with self.assertRaisesRegex(fset.FirmwareSetError, 'missing or non-regular'):
            fset.verify(self.root, entries)

    def test_validate_refuses_zero_digest(self):
        manifest = fset.record(self.root, 'unit-test')
        manifest['files'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(fset.FirmwareSetError, 'zero digest'):
            fset.validate(manifest)

    def test_validate_refuses_duplicate_and_unknown_blob(self):
        manifest = fset.record(self.root, 'unit-test')
        bad = json.loads(json.dumps(manifest))
        bad['files'][1]['blob'] = 'GPU-SQE'
        with self.assertRaisesRegex(fset.FirmwareSetError, 'duplicate blob'):
            fset.validate(bad)
        bad = json.loads(json.dumps(manifest))
        bad['files'][0]['blob'] = 'GPU-A640-SQE'
        with self.assertRaisesRegex(fset.FirmwareSetError, 'unknown blob'):
            fset.validate(bad)

    def test_validate_refuses_renamed_file(self):
        manifest = fset.record(self.root, 'unit-test')
        manifest['files'][2]['filename'] = 'a640_zap.mdt'
        with self.assertRaisesRegex(fset.FirmwareSetError, 'filename must be'):
            fset.validate(manifest)

    def test_validate_refuses_wrong_blob_count(self):
        manifest = fset.record(self.root, 'unit-test')
        for files in (manifest['files'][:5], manifest['files'] + manifest['files'][:1]):
            bad = {'schema': 1, 'name': 'x', 'files': files}
            with self.assertRaisesRegex(fset.FirmwareSetError, 'exactly 6'):
                fset.validate(bad)

    def test_validate_refuses_bad_schema_and_digest_encoding(self):
        manifest = fset.record(self.root, 'unit-test')
        bad = json.loads(json.dumps(manifest))
        bad['schema'] = 2
        with self.assertRaisesRegex(fset.FirmwareSetError, 'unsupported manifest schema'):
            fset.validate(bad)
        bad = json.loads(json.dumps(manifest))
        bad['files'][0]['sha256'] = bad['files'][0]['sha256'].upper()
        with self.assertRaisesRegex(fset.FirmwareSetError, '64 lowercase hex'):
            fset.validate(bad)

    def test_cli_exit_codes(self):
        manifest_path = _write_manifest(self.root)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(fset.main([str(self.root), '--check',
                                        '--manifest', str(manifest_path)]), 0)
        (self.root / 'a630_sqe.fw').write_bytes(b'short')
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(fset.main([str(self.root), '--check',
                                        '--manifest', str(manifest_path)]), 1)
        self.assertIn('REFUSE', err.getvalue())


def _write_manifest(folder):
    manifest = fset.record(folder, 'unit-test')
    path = folder / 'set.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


if __name__ == '__main__':
    unittest.main()
