#!/usr/bin/env python3
"""A630v2 GMU CSR addresses for the read-only GX probe.

The vendor driver addresses these registers by *word index* into the GMU
aperture, then rebases:

    kgsl_gmu.c:1155   gmu->gmu2gpu_offset = (gmu->reg_phys - device->reg_phys) >> 2
    adreno.c:2914     reg = gmu->reg_virt + ((offsetwords - gmu2gpu_offset) << 2)

So the byte offset is (index - 0x1A800) * 4 for polaris, where 0x1A800 comes
from the device tree (GMU at 0x0506a000, GPU at 0x05000000). Getting this wrong
does not fault: it silently reads a neighbouring block, which is why the rule
is derived here and bounds-checked instead of being copied as a constant.

Indices come from the vendor header drivers/gpu/msm/a6xx_reg.h (branch
polaris-o-oss). Only indices are copied; no GPL code is reproduced.

Nothing here performs an access. This module produces a plan and refuses to
produce one for anything outside the mapped window.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

# From the vendor device tree (tools/polaris_dt_regs.py on the MIUI boot.img):
#   soc/qcom,kgsl-3d0@5000000   reg = 0x05000000
#   soc/qcom,gmu                kgsl_gmu_reg     = 0x0506a000 + 0x30000
GPU_REG_BASE = 0x0500_0000
GMU_REG_BASE = 0x0506_A000
GMU_REG_SIZE = 0x0003_0000
GMU2GPU_OFFSET = (GMU_REG_BASE - GPU_REG_BASE) >> 2  # 0x1A800

# The width of one CSR word, and the largest index the window can address.
WORD = 4
LAST_INDEX = GMU2GPU_OFFSET + GMU_REG_SIZE // WORD


class CsrError(ValueError):
    pass


@dataclass(frozen=True)
class Csr:
    """A GMU register addressed the way the vendor driver addresses it."""
    name: str
    index: int
    note: str = ''


# Indices from a6xx_reg.h; the notes are what the power-on path writes there,
# which is also what a probe should expect to see afterwards.
RSC_STATUS0 = Csr('GPU_RSCC_RSC_STATUS0_DRV0', 0x23404, 'bit 24: SDE clock gating off')
PDC_SLAVE_ID = Csr('RSCC_PDC_SLAVE_ID_DRV0', 0x2340B, 'written 1')
PDC_SEQ_START = Csr('RSCC_PDC_SEQ_START_ADDR', 0x23408, 'written 0x4520')
PDC_MATCH_LO = Csr('RSCC_PDC_MATCH_VALUE_LO', 0x23409, 'written 0x4510')
PDC_MATCH_HI = Csr('RSCC_PDC_MATCH_VALUE_HI', 0x2340A, 'written 0x4514')
PDC_ENABLE = Csr('PDC_GPU_ENABLE_PDC', 0x21140, 'written 0x80000001')
SEQ_BUSY = Csr('RSCC_SEQ_BUSY_DRV0', 0x23501, '0 when the sequencer is idle')
CONTROL_REQ = Csr('GMU_RSCC_CONTROL_REQ', 0x23B07, 'bit 1 set while requesting GX')
CONTROL_ACK = Csr('GMU_RSCC_CONTROL_ACK', 0x23B08, 'bit 1 set when GX is up')

# What the read-only probe looks at, in the order it reads them: the state
# first, then the program the previous owner left behind. Read-only by
# construction - this tuple has no writes to express.
PROBE = (RSC_STATUS0, PDC_SLAVE_ID, PDC_SEQ_START, PDC_MATCH_LO, PDC_MATCH_HI,
         PDC_ENABLE, SEQ_BUSY, CONTROL_REQ, CONTROL_ACK)


def byte_offset(csr: Csr) -> int:
    """Offset inside the GMU mapping, applying the vendor rebase."""
    if csr.index < GMU2GPU_OFFSET:
        raise CsrError(
            f'{csr.name}: index {csr.index:#x} is below gmu2gpu_offset '
            f'{GMU2GPU_OFFSET:#x}; the vendor rebase would underflow, so this '
            'register is not reachable through the GMU aperture')
    off = (csr.index - GMU2GPU_OFFSET) * WORD
    if off >= GMU_REG_SIZE:
        raise CsrError(
            f'{csr.name}: byte offset {off:#x} is outside the '
            f'{GMU_REG_SIZE:#x}-byte GMU window from the device tree')
    return off


def phys(csr: Csr) -> int:
    return GMU_REG_BASE + byte_offset(csr)


def check() -> list[str]:
    """Invariants a mis-derived address would break."""
    problems: list[str] = []
    if GMU2GPU_OFFSET != 0x1A800:
        problems.append(f'gmu2gpu_offset is {GMU2GPU_OFFSET:#x}, expected 0x1a800')
    seen: dict[int, str] = {}
    for csr in PROBE:
        try:
            off = byte_offset(csr)
        except CsrError as exc:
            problems.append(str(exc))
            continue
        if off % WORD:
            problems.append(f'{csr.name}: offset {off:#x} is not word aligned')
        if off in seen:
            problems.append(f'{csr.name} and {seen[off]} share offset {off:#x}')
        seen[off] = csr.name
        if not GMU_REG_BASE <= phys(csr) < GMU_REG_BASE + GMU_REG_SIZE:
            problems.append(f'{csr.name}: {phys(csr):#x} is outside the GMU window')
    if len({c.name for c in PROBE}) != len(PROBE):
        problems.append('duplicate register names in the probe plan')
    return problems


def main() -> int:
    print(f'GPU aperture {GPU_REG_BASE:#010x}, GMU aperture {GMU_REG_BASE:#010x} '
          f'+ {GMU_REG_SIZE:#x}')
    print(f'gmu2gpu_offset = ({GMU_REG_BASE:#x} - {GPU_REG_BASE:#x}) >> 2 = '
          f'{GMU2GPU_OFFSET:#x} words')
    print(f'byte = (index - {GMU2GPU_OFFSET:#x}) * 4, window '
          f'[{GMU_REG_BASE:#x}, {GMU_REG_BASE + GMU_REG_SIZE:#x})')
    print()
    for csr in PROBE:
        print(f'{csr.name:26s} index {csr.index:#07x}  +{byte_offset(csr):#07x}  '
              f'phys {phys(csr):#010x}  {csr.note}')
    problems = check()
    if problems:
        for p in problems:
            print(f'FAIL  {p}', file=sys.stderr)
        return 1
    print()
    print(f'ok    {len(PROBE)} read-only registers, all inside the GMU window')
    return 0


if __name__ == '__main__':
    sys.exit(main())
