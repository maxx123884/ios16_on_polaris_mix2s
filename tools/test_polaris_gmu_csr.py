#!/usr/bin/env python3
"""Tests for the GMU CSR address derivation.

The numbers asserted here are the ones the vendor driver would compute from the
same device tree; a wrong rebase shows up as an address outside the window
rather than as a wrong read on hardware.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polaris_dt_regs as dt
import polaris_gmu_csr as csr


class Rebase(unittest.TestCase):
    def test_offset_comes_from_the_device_tree_numbers(self):
        self.assertEqual(csr.GMU2GPU_OFFSET, 0x1A800)
        self.assertEqual(csr.GMU2GPU_OFFSET,
                         (csr.GMU_REG_BASE - csr.GPU_REG_BASE) >> 2)

    def test_known_addresses(self):
        expected = {
            'GPU_RSCC_RSC_STATUS0_DRV0': 0x0508D010,
            'RSCC_PDC_SLAVE_ID_DRV0': 0x0508D02C,
            'RSCC_PDC_SEQ_START_ADDR': 0x0508D020,
            'RSCC_PDC_MATCH_VALUE_LO': 0x0508D024,
            'RSCC_PDC_MATCH_VALUE_HI': 0x0508D028,
            'PDC_GPU_ENABLE_PDC': 0x05084500,
            'RSCC_SEQ_BUSY_DRV0': 0x0508D404,
            'GMU_RSCC_CONTROL_REQ': 0x0508EC1C,
            'GMU_RSCC_CONTROL_ACK': 0x0508EC20,
        }
        for name, addr in expected.items():
            reg = next(c for c in csr.PROBE if c.name == name)
            self.assertEqual(csr.phys(reg), addr, name)

    def test_every_probe_register_is_inside_the_window(self):
        self.assertEqual(csr.check(), [])

    def test_index_below_the_rebase_is_refused(self):
        # Anything below gmu2gpu_offset belongs to the GPU aperture, not the
        # GMU one; the vendor subtraction would underflow.
        with self.assertRaises(csr.CsrError):
            csr.byte_offset(csr.Csr('a GPU CSR', 0x1A000))

    def test_index_past_the_window_is_refused(self):
        with self.assertRaises(csr.CsrError):
            csr.byte_offset(csr.Csr('past the end', csr.LAST_INDEX))

    def test_last_addressable_word_is_inside(self):
        last = csr.Csr('last word', csr.LAST_INDEX - 1)
        self.assertEqual(csr.byte_offset(last), csr.GMU_REG_SIZE - 4)

    def test_window_matches_the_device_tree(self):
        # Same numbers the DT reader extracts, so a DT change breaks this test
        # instead of quietly moving the probe.
        windows = dt.gpu_windows(dt.parse_fdt(_fixture()))
        self.assertEqual(windows['gmu']['base'], csr.GMU_REG_BASE)
        self.assertEqual(windows['gmu']['size'], csr.GMU_REG_SIZE)


def _fixture() -> bytes:
    """The vendor DT windows, built by the reader's own test helper."""
    import test_polaris_dt_regs as t
    return t.build_fdt(t.gpu_tree())


if __name__ == '__main__':
    unittest.main()
