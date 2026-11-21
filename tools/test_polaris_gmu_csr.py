#!/usr/bin/env python3
"""Tests for the GMU/PDC CSR address derivation.

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
            'RSCC_SEQ_BUSY_DRV0': 0x0508D404,
            'GMU_RSCC_CONTROL_REQ': 0x0508EC1C,
            'GMU_RSCC_CONTROL_ACK': 0x0508EC20,
        }
        for name, addr in expected.items():
            reg = next(c for c in csr.PROBE if c.name == name)
            self.assertEqual(csr.phys(reg), addr, name)

    def test_pdc_register_lives_in_the_pdc_window(self):
        # _regwrite() adds index << 2 to the ioremap of kgsl_gmu_pdc_reg, i.e.
        # 0x0b200000 + 0x21140 * 4. It is NOT reachable through the GMU window:
        # 0x0506a000 + (0x21140 - 0x1a800) * 4 would be 0x05084500, an
        # unrelated GMU register that does not fault when written.
        self.assertEqual(csr.phys(csr.PDC_ENABLE), 0x0B284500)
        self.assertNotEqual(csr.phys(csr.PDC_ENABLE), 0x05084500)
        self.assertEqual(csr.byte_offset(csr.PDC_ENABLE), 0x21140 * 4)

    def test_every_probe_register_is_inside_its_window(self):
        self.assertEqual(csr.check(), [])

    def test_index_below_the_rebase_is_refused(self):
        # Anything below gmu2gpu_offset belongs to the GPU aperture, not the
        # GMU one; the vendor subtraction would underflow.
        with self.assertRaises(csr.CsrError):
            csr.byte_offset(csr.Csr('a GPU CSR', 0x1A000))

    def test_index_past_the_window_is_refused(self):
        with self.assertRaises(csr.CsrError):
            csr.byte_offset(csr.Csr('past the end', csr.LAST_INDEX))

    def test_pdc_index_past_the_window_is_refused(self):
        with self.assertRaises(csr.CsrError):
            csr.byte_offset(csr.Csr('past the PDC end',
                                    csr.PDC_REG_SIZE // csr.WORD,
                                    aperture=csr.PDC))

    def test_last_addressable_word_is_inside(self):
        last = csr.Csr('last word', csr.LAST_INDEX - 1)
        self.assertEqual(csr.byte_offset(last), csr.GMU_REG_SIZE - 4)

    def test_window_matches_the_device_tree(self):
        # Same numbers the DT reader extracts, so a DT change breaks this test
        # instead of quietly moving the probe.
        windows = dt.gpu_windows(dt.parse_fdt(_fixture()))
        self.assertEqual(windows['gmu']['base'], csr.GMU_REG_BASE)
        self.assertEqual(windows['gmu']['size'], csr.GMU_REG_SIZE)

    def test_gmu_and_pdc_windows_do_not_alias(self):
        gmu_base, gmu_size = csr.APERTURES[csr.GMU]
        pdc_base, pdc_size = csr.APERTURES[csr.PDC]
        self.assertFalse(gmu_base < pdc_base + pdc_size and
                         pdc_base < gmu_base + gmu_size)


class CrossCheck(unittest.TestCase):
    """An independent implementation must land on the same addresses."""

    def test_independent_words_rebase_to_our_indices(self):
        for name, word in csr.INDEPENDENT_WORDS.items():
            reg = next(c for c in csr.PROBE if c.name == name)
            self.assertEqual(reg.index - csr.GMU2GPU_OFFSET, word, name)

    def test_independent_word_addresses(self):
        # GMU_WORD(n) in that implementation is 4 * n from 0x0506a000.
        for name, word in csr.INDEPENDENT_WORDS.items():
            reg = next(c for c in csr.PROBE if c.name == name)
            self.assertEqual(csr.phys(reg), csr.GMU_REG_BASE + word * 4, name)


def _fixture() -> bytes:
    """The vendor DT windows, built by the reader's own test helper."""
    import test_polaris_dt_regs as t
    return t.build_fdt(t.gpu_tree())


if __name__ == '__main__':
    unittest.main()
