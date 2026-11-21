#!/usr/bin/env python3
"""Tests for the recorded A630 GX power-on contract."""
import unittest

import polaris_rscc_pdc as p


class ChipGateTest(unittest.TestCase):
    def test_accepts_a630v2(self):
        p.check_chip(p.A630V2_CHIP_ID)

    def test_refuses_sibling_gpus(self):
        for label, chip in p.OTHER_CHIP_IDS.items():
            with self.subTest(label=label), self.assertRaises(p.SequenceError):
                p.check_chip(chip)

    def test_refuses_zero_and_random(self):
        for chip in (0, 0xdeadbeef, p.A630V2_CHIP_ID + 1):
            with self.assertRaises(p.SequenceError):
                p.check_chip(chip)

    def test_error_names_the_offending_part(self):
        with self.assertRaises(p.SequenceError) as ctx:
            p.check_chip(p.OTHER_CHIP_IDS['a640 (SM8150)'])
        self.assertIn('a640', str(ctx.exception))


class SequenceShapeTest(unittest.TestCase):
    def test_both_sequences_are_five_words(self):
        self.assertEqual(len(p.RSC_SEQ_WORDS), p.SEQ_WORDS)
        self.assertEqual(len(p.PDC_SEQ_WORDS), p.SEQ_WORDS)

    def test_rejects_wrong_length(self):
        for words in (p.RSC_SEQ_WORDS[:4], p.PDC_SEQ_WORDS + (0,), ()):
            with self.assertRaises(p.SequenceError):
                p.check_sequence('synthetic', words)

    def test_rejects_placeholder(self):
        for words in ((0, 0, 0, 0, 0), (1, 1, 1, 1, 1),
                      (p.RSC_SEQ_WORDS[0],) * p.SEQ_WORDS):
            with self.assertRaises(p.SequenceError):
                p.check_sequence('synthetic', words)

    def test_rejects_out_of_range_word(self):
        bad = (p.RSC_SEQ_WORDS[0], p.RSC_SEQ_WORDS[1], p.RSC_SEQ_WORDS[2],
               p.RSC_SEQ_WORDS[3], 0x1_0000_0000)
        with self.assertRaises(p.SequenceError):
            p.check_sequence('synthetic', bad)

    def test_real_sequences_are_distinct_values(self):
        self.assertEqual(len(set(p.RSC_SEQ_WORDS)), p.SEQ_WORDS)
        self.assertEqual(len(set(p.PDC_SEQ_WORDS)), p.SEQ_WORDS)

    def test_pdc_offsets_ordered_and_aligned(self):
        self.assertLess(p.PDC_MATCH_LO_VALUE, p.PDC_MATCH_HI_VALUE)
        self.assertLess(p.PDC_MATCH_HI_VALUE, p.PDC_SEQ_ADDR)
        for v in (p.PDC_MATCH_LO_VALUE, p.PDC_MATCH_HI_VALUE, p.PDC_SEQ_ADDR):
            self.assertEqual(v % 4, 0)

    def test_pdc_entries_are_the_documented_values(self):
        self.assertEqual(p.PDC_SEQ_ADDR, 0x4520)
        self.assertEqual(p.PDC_MATCH_LO_VALUE, 0x4510)
        self.assertEqual(p.PDC_MATCH_HI_VALUE, 0x4514)
        self.assertEqual(p.PDC_ENABLE_VALUE, 0x80000001)


class PlanTest(unittest.TestCase):
    def test_handshake_is_bounded(self):
        for step in p.GX_POWER_ON:
            if step.timeout_us is not None:
                self.assertGreater(step.timeout_us, 0)

    def test_ack_timeout_is_not_shorter_than_req(self):
        timeouts = {s.reg.name: s.timeout_us for s in p.GX_POWER_ON
                    if s.timeout_us}
        self.assertGreaterEqual(timeouts[p.CONTROL_ACK.name],
                                timeouts[p.CONTROL_REQ.name])

    def test_request_is_set_then_cleared(self):
        req = [s for s in p.GX_POWER_ON if s.reg.name == p.CONTROL_REQ.name]
        self.assertEqual(len(req), 2)
        self.assertIn('bit1 set', req[0].expect)
        self.assertEqual(req[1].expect, '0')

    def test_power_counter_is_last(self):
        self.assertEqual(p.GX_POWER_ON[-1].reg.name, p.POWER_COUNTER_EN.name)

    def test_no_duplicate_register_writes_that_would_lose_bits(self):
        names = [s.reg.name for s in p.GX_POWER_ON]
        for name in names:
            if name == p.CONTROL_REQ.name:
                continue
            self.assertEqual(names.count(name), 1, f'{name} written twice')

    def test_firmware_list_is_exactly_the_six_blobs(self):
        self.assertEqual(len(p.firmware_names()), 6)
        self.assertIn('a630_gmu.bin', p.firmware_names())
        for bad in ('a640_gmu.bin', 'a660_gmu.bin', 'a830_gmu.bin'):
            self.assertNotIn(bad, p.firmware_names())


if __name__ == '__main__':
    unittest.main()
