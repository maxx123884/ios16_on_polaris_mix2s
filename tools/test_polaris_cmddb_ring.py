"""Round-trip tests for the m1n command-DB ring parser. No device access."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polaris_cmddb_ring import (
    HEADER,
    CommandDbParseError,
    is_gpu,
    load_log,
    reassemble,
    render_lines,
    walk_entries,
)


def mock_db() -> bytes:
    data = bytearray(256)
    struct.pack_into('<II', data, 0, 1, 0x0C0330DB)
    # slave 5, head_off 0, aux_off 48, count 2, version 1
    struct.pack_into('<5H', data, 8, 5, 0, 48, 2, 1)
    names = ('GPU_CX_G', 'GPU_GX_G')
    for i, name in enumerate(names):
        p = HEADER + 24 * i
        data[p:p + 8] = name.encode().ljust(8, b'\0')
        struct.pack_into('<IHH', data, p + 16, 0x50000 + 8 * i, 8, i * 8)
        struct.pack_into('<IHBB', data, HEADER + 48 + i * 8, 1000, 32, i, 0)
    return bytes(data)


class CmdDbRingTests(unittest.TestCase):
    def test_render_and_reassemble_round_trip(self):
        db = mock_db()
        text = render_lines(db)
        self.assertEqual(reassemble(text), db)

    def test_walk_finds_gpu_entries(self):
        entries = walk_entries(mock_db())
        self.assertEqual([e['name'] for e in entries], ['GPU_CX_G', 'GPU_GX_G'])
        self.assertEqual(entries[0]['address'], 0x50000)
        self.assertEqual(entries[1]['address'], 0x50008)
        self.assertTrue(all(is_gpu(e['name']) for e in entries))

    def test_gpu_hint_matches(self):
        self.assertTrue(is_gpu('gpu_cc_cx_gdsc'))
        self.assertTrue(is_gpu('gfx3d_clk'))
        self.assertFalse(is_gpu('mdc_comp'))

    def test_magic_mismatch_raises(self):
        db = bytearray(mock_db())
        struct.pack_into('<I', db, 4, 0x00000000)
        with self.assertRaises(CommandDbParseError):
            walk_entries(bytes(db))

    def test_truncated_dump_raises(self):
        with self.assertRaises(CommandDbParseError):
            walk_entries(mock_db()[:64])

    def test_no_lines_raises(self):
        with self.assertRaises(CommandDbParseError):
            reassemble('no probe output here\n')

    def test_load_plain_text_log(self):
        db = mock_db()
        text = render_lines(db)
        tmp = Path(self.tempdir) / 'ring.txt'
        tmp.write_text('other boot lines\n' + text + '\n', encoding='utf-8')
        loaded = load_log(tmp)
        self.assertEqual(reassemble(loaded), db)

    def setUp(self):
        import tempfile
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
