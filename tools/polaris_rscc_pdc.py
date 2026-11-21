#!/usr/bin/env python3
"""A630 (SDM845v2) GX power-on contract, recorded from vendor kgsl.

This module holds *facts*, not driver code: the RSCC/PDC micro-sequences and
register offsets that Qualcomm's kgsl uses to power the GPU core through the
GMU's own always-on sequencer. It exists so that

  1. the numbers have one canonical home with their provenance attached, and
  2. a wrong-chip or wrong-length sequence is rejected at import/test time
     instead of being discovered on hardware.

Nothing here writes a register. The corresponding XNU backend still has to be
written, and it must not be a transcription of GPL-2.0 source: only these
values (with the cited origin) may cross into the APSL tree.

Provenance: MiCode Xiaomi_Kernel_OpenSource, branch polaris-o-oss,
drivers/gpu/msm/adreno_a6xx.c -- `_load_gmu_rpmh_ucode()`,
`a6xx_rpmh_power_on_gpu()` and `a6xx_gmu_fw_start()`.
"""
from dataclasses import dataclass

# A630v2 as reported by the vendor GPU DT (qcom,chipid).
A630V2_CHIP_ID = 0x06030001
# Sibling parts, listed only so that mixing them up fails loudly.
OTHER_CHIP_IDS = {
    'a640 (SM8150)': 0x06040001,
    'a660 (SC7280)': 0x06030500,
    'a660l (SC7280)': 0x06030501,
    'a830 (SM8750)': 0x07030000,
}

# The GMU drives the GPU core itself: these words program its always-on
# sequencer, so this path does not need the apps-RSC command-db.
RSC_SEQ_WORDS = (0xA7A506A0, 0xA1E6A6E7, 0xA2E081E1, 0xE9A982E2, 0x0020E8A8)
PDC_SEQ_WORDS = (0xFEBEA1E1, 0xA5A4A3A2, 0x8382A6E0, 0xBCE3E284, 0x002081FC)

SEQ_WORDS = 5


class SequenceError(ValueError):
    pass


@dataclass(frozen=True)
class Reg:
    """A symbolic register plus the bit this driver expects to observe."""
    name: str
    bit: int
    note: str = ''


RSC_STATUS0 = Reg('GPU_RSCC_RSC_STATUS0_DRV0', 24, 'SDE clock gating off')
PDC_SLAVE_ID = Reg('RSCC_PDC_SLAVE_ID_DRV0', 0)
PDC_SEQ_START = Reg('RSCC_PDC_SEQ_START_ADDR', 0, 'value 0x4520')
PDC_MATCH_LO = Reg('RSCC_PDC_MATCH_VALUE_LO', 0, 'value 0x4510')
PDC_MATCH_HI = Reg('RSCC_PDC_MATCH_VALUE_HI', 0, 'value 0x4514')
RSC_SEQ_MEM = Reg('RSCC_SEQ_MEM_0_DRV0', 0, '5 words, +0..+4')
PDC_SEQ_MEM = Reg('PDC_GPU_SEQ_MEM_0', 0, '5 words, +0..+4')
CONTROL_REQ = Reg('GMU_RSCC_CONTROL_REQ', 1)
CONTROL_ACK = Reg('GMU_RSCC_CONTROL_ACK', 1)
SEQ_BUSY = Reg('RSCC_SEQ_BUSY_DRV0', 0, 'must read 0')
POWER_COUNTER_EN = Reg('GMU_CX_GMU_POWER_COUNTER_ENABLE', 0)
PDC_ENABLE = Reg('PDC_GPU_ENABLE_PDC', 0, 'value 0x80000001')

PDC_SEQ_ADDR = 0x4520
PDC_MATCH_LO_VALUE = 0x4510
PDC_MATCH_HI_VALUE = 0x4514
PDC_ENABLE_VALUE = 0x80000001
PDC_SLAVE_ID_VALUE = 1
TCS_ENABLE_BANK = 7


@dataclass(frozen=True)
class Step:
    action: str
    reg: Reg
    timeout_us: int | None = None
    expect: str = ''


# Ordered by execution dependency; the C backend must keep this order.
GX_POWER_ON = (
    Step('clear SDE clock gate', RSC_STATUS0, expect='bit24 set'),
    Step('select PDC slave', PDC_SLAVE_ID, expect='1'),
    Step('write PDC entry', PDC_SEQ_START, expect='0x4520'),
    Step('write PDC match lo', PDC_MATCH_LO, expect='0x4510'),
    Step('write PDC match hi', PDC_MATCH_HI, expect='0x4514'),
    Step('load RSC sequence', RSC_SEQ_MEM, expect='5 words'),
    Step('load PDC sequence', PDC_SEQ_MEM, expect='5 words'),
    Step('enable PDC', PDC_ENABLE, expect='0x80000001'),
    Step('request GX rail', CONTROL_REQ, timeout_us=100000, expect='bit1 set'),
    Step('wait for ack', CONTROL_ACK, timeout_us=100000, expect='bit1 set'),
    Step('wait for sequencer idle', SEQ_BUSY, timeout_us=100000, expect='0'),
    Step('clear request', CONTROL_REQ, expect='0'),
    Step('start power counter', POWER_COUNTER_EN, expect='1'),
)


def check_chip(chip_id: int) -> None:
    """Refuse anything that is not an A630v2."""
    if chip_id == A630V2_CHIP_ID:
        return
    for label, value in OTHER_CHIP_IDS.items():
        if chip_id == value:
            raise SequenceError(
                f'{label} sequence is not valid on A630v2; '
                'its GMU/PDC values and firmware differ')
    raise SequenceError(f'unknown chip id 0x{chip_id:08x}')


def check_sequence(name: str, words: tuple[int, ...]) -> None:
    """One sequence must be exactly SEQ_WORDS distinct 32-bit values."""
    if len(words) != SEQ_WORDS:
        raise SequenceError(f'{name} sequence must be {SEQ_WORDS} words, '
                            f'got {len(words)}')
    for w in words:
        if not 0 <= w <= 0xFFFFFFFF:
            raise SequenceError(f'{name} word 0x{w:x} is not 32-bit')
    if all(w == 0 for w in words) or len(set(words)) != SEQ_WORDS:
        raise SequenceError(f'{name} sequence looks like a placeholder')


def check_sequences() -> None:
    """Structural sanity that a transcription error would break."""
    check_sequence('rsc', RSC_SEQ_WORDS)
    check_sequence('pdc', PDC_SEQ_WORDS)
    if RSC_SEQ_WORDS == PDC_SEQ_WORDS:
        raise SequenceError('RSC and PDC sequences must differ')
    # The two sequences live in different memories; sharing a value set is the
    # classic copy-paste error when porting from another SoC.
    if set(RSC_SEQ_WORDS) == set(PDC_SEQ_WORDS):
        raise SequenceError('RSC/PDC word sets identical; suspect copy-paste')
    if not (PDC_MATCH_LO_VALUE < PDC_MATCH_HI_VALUE < PDC_SEQ_ADDR):
        raise SequenceError('PDC match/entry offsets are not ordered')
    for v in (PDC_MATCH_LO_VALUE, PDC_MATCH_HI_VALUE, PDC_SEQ_ADDR):
        if v % 4:
            raise SequenceError(f'PDC offset 0x{v:x} is not word aligned')


def check_plan() -> None:
    """The request/ack/busy handshake must be ordered and bounded."""
    names = [s.reg.name for s in GX_POWER_ON]
    for required in (CONTROL_REQ.name, CONTROL_ACK.name, SEQ_BUSY.name):
        if required not in names:
            raise SequenceError(f'handshake step missing: {required}')
    order = [names.index(n) for n in (CONTROL_REQ.name, CONTROL_ACK.name,
                                      SEQ_BUSY.name)]
    if order != sorted(order):
        raise SequenceError('request/ack/idle out of order')
    for step in GX_POWER_ON:
        if step.timeout_us is not None and step.timeout_us <= 0:
            raise SequenceError(f'{step.action}: non-positive timeout')
    if sum(1 for s in GX_POWER_ON if s.reg.name == CONTROL_REQ.name) != 2:
        raise SequenceError('expected request set + clear')


def firmware_names() -> tuple[str, ...]:
    """The files this contract assumes are present; none is bundled here."""
    return ('a630_sqe.fw', 'a630_gmu.bin', 'a630_zap.mdt',
            'a630_zap.b00', 'a630_zap.b01', 'a630_zap.b02')


check_sequences()
check_plan()


if __name__ == '__main__':
    check_chip(A630V2_CHIP_ID)
    print(f'A630v2 GX power-on: {len(GX_POWER_ON)} steps, '
          f'{len(RSC_SEQ_WORDS)}+{len(PDC_SEQ_WORDS)} sequence words')
    for i, step in enumerate(GX_POWER_ON, 1):
        t = f' <= {step.timeout_us}us' if step.timeout_us else ''
        print(f'{i:2d}. {step.action:<26} {step.reg.name}{t}')
