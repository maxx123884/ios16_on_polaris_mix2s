#!/usr/bin/env python3
"""A630v2 GMU/PDC CSR addresses for the read-only GX probe.

Two apertures, two rules, and they are not interchangeable:

  GMU  (device tree ``kgsl_gmu_reg``, 0x0506a000 + 0x30000)
       addressed by *vendor word index*, rebased because the vendor pointer is
       the GPU aperture and the DT maps the GMU one:

           kgsl_gmu.c:1155   gmu2gpu_offset = (gmu->reg_phys - device->reg_phys) >> 2
           adreno.c:2914     reg = gmu->reg_virt + ((offsetwords - gmu2gpu_offset) << 2)

       => byte = (index - 0x1A800) * 4 on polaris.

  PDC  (device tree ``kgsl_gmu_pdc_reg``, 0x0b200000 + 0x300000)
       addressed with no rebase at all, because the vendor writes it through a
       separate ioremap of that resource:

           kgsl_gmu.c:921    gmu->pdc_reg_virt = devm_ioremap(res->start, ...)
           a6xx.c:1173       reg = regbase + (offsetwords << 2)   /* _regwrite */

       => byte = index * 4.

Treating a PDC index as a GMU one does not fault: it lands on an unrelated
register inside the GMU window, which is why both rules are derived here from
the DT and the vendor source and bounds-checked, instead of being copied as
constants.

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
#   soc/qcom,gmu                kgsl_gmu_pdc_reg = 0x0b200000 + 0x300000
GPU_REG_BASE = 0x0500_0000
GMU_REG_BASE = 0x0506_A000
GMU_REG_SIZE = 0x0003_0000
PDC_REG_BASE = 0x0B20_0000
PDC_REG_SIZE = 0x0030_0000
GMU2GPU_OFFSET = (GMU_REG_BASE - GPU_REG_BASE) >> 2  # 0x1A800

# The width of one CSR word, and the largest index the GMU window can address.
WORD = 4
LAST_INDEX = GMU2GPU_OFFSET + GMU_REG_SIZE // WORD

GMU = 'gmu'
PDC = 'pdc'
APERTURES = {GMU: (GMU_REG_BASE, GMU_REG_SIZE), PDC: (PDC_REG_BASE, PDC_REG_SIZE)}


class CsrError(ValueError):
    pass


@dataclass(frozen=True)
class Csr:
    """A register addressed the way the vendor driver addresses it."""
    name: str
    index: int
    note: str = ''
    aperture: str = GMU


# Indices from a6xx_reg.h; the notes are what the power-on path writes there,
# which is also what a probe should expect to see afterwards.
RSC_STATUS0 = Csr('GPU_RSCC_RSC_STATUS0_DRV0', 0x23404, 'bit 24 SDE gating off; bit 16 channel asleep')
PDC_SLAVE_ID = Csr('RSCC_PDC_SLAVE_ID_DRV0', 0x2340B, 'written 1')
PDC_SEQ_START = Csr('RSCC_PDC_SEQ_START_ADDR', 0x23408, 'written 0x4520')
PDC_MATCH_LO = Csr('RSCC_PDC_MATCH_VALUE_LO', 0x23409, 'written 0x4510')
PDC_MATCH_HI = Csr('RSCC_PDC_MATCH_VALUE_HI', 0x2340A, 'written 0x4514')
PDC_ENABLE = Csr('PDC_GPU_ENABLE_PDC', 0x21140, 'written 0x80000001', aperture=PDC)
SEQ_BUSY = Csr('RSCC_SEQ_BUSY_DRV0', 0x23501, '0 when the sequencer is idle')
CONTROL_REQ = Csr('GMU_RSCC_CONTROL_REQ', 0x23B07, '2 while waking, 0 after')
CONTROL_ACK = Csr('GMU_RSCC_CONTROL_ACK', 0x23B08, '2 when the wake was accepted')

# What the read-only probe looks at, in the order it reads them: the state
# first, then the program the previous owner left behind. Read-only by
# construction - this tuple has no writes to express.
PROBE = (RSC_STATUS0, PDC_SLAVE_ID, PDC_SEQ_START, PDC_MATCH_LO, PDC_MATCH_HI,
         PDC_ENABLE, SEQ_BUSY, CONTROL_REQ, CONTROL_ACK)

# An independent implementation of the same bring-up (a third-party polaris
# fork, branch d5ec4cdc) hard-codes the GMU registers as word indices already
# relative to GMU_BASE: GMU_WORD(0x8c04) for RSC_STATUS0 and so on. Subtracting
# the rebase from our indices must reproduce those numbers exactly; if a future
# edit breaks the derivation, this table is what catches it.
INDEPENDENT_WORDS = {
    'GPU_RSCC_RSC_STATUS0_DRV0': 0x8C04,
    'RSCC_PDC_SLAVE_ID_DRV0': 0x8C0B,
    'RSCC_PDC_SEQ_START_ADDR': 0x8C08,
    'RSCC_PDC_MATCH_VALUE_LO': 0x8C09,
    'RSCC_PDC_MATCH_VALUE_HI': 0x8C0A,
    'RSCC_SEQ_BUSY_DRV0': 0x8D01,
    'GMU_RSCC_CONTROL_REQ': 0x9307,
    'GMU_RSCC_CONTROL_ACK': 0x9308,
}


def byte_offset(csr: Csr) -> int:
    """Offset inside the mapped aperture, applying the rule that aperture uses."""
    if csr.aperture not in APERTURES:
        raise CsrError(f'{csr.name}: unknown aperture {csr.aperture!r}')
    base, size = APERTURES[csr.aperture]

    if csr.aperture == PDC:
        # _regwrite(pdc_reg_virt, index, value) - no rebase.
        off = csr.index * WORD
    else:
        if csr.index < GMU2GPU_OFFSET:
            raise CsrError(
                f'{csr.name}: index {csr.index:#x} is below gmu2gpu_offset '
                f'{GMU2GPU_OFFSET:#x}; the vendor rebase would underflow, so this '
                'register is not reachable through the GMU aperture')
        off = (csr.index - GMU2GPU_OFFSET) * WORD

    if off >= size:
        raise CsrError(
            f'{csr.name}: byte offset {off:#x} is outside the {size:#x}-byte '
            f'{csr.aperture} window at {base:#x} from the device tree')
    return off


def phys(csr: Csr) -> int:
    base, _ = APERTURES[csr.aperture]
    return base + byte_offset(csr)


def check() -> list[str]:
    """Invariants a mis-derived address would break."""
    problems: list[str] = []
    if GMU2GPU_OFFSET != 0x1A800:
        problems.append(f'gmu2gpu_offset is {GMU2GPU_OFFSET:#x}, expected 0x1a800')
    if (PDC_REG_BASE, PDC_REG_SIZE) != (0x0B20_0000, 0x0030_0000):
        problems.append('PDC window does not match the device tree')
    seen: dict[str, dict[int, str]] = {GMU: {}, PDC: {}}
    for csr in PROBE:
        try:
            off = byte_offset(csr)
        except CsrError as exc:
            problems.append(str(exc))
            continue
        base, size = APERTURES[csr.aperture]
        if off % WORD:
            problems.append(f'{csr.name}: offset {off:#x} is not word aligned')
        if off in seen[csr.aperture]:
            problems.append(f'{csr.name} and {seen[csr.aperture][off]} share '
                            f'offset {off:#x} in the {csr.aperture} window')
        seen[csr.aperture][off] = csr.name
        if not base <= phys(csr) < base + size:
            problems.append(f'{csr.name}: {phys(csr):#x} is outside the '
                            f'{csr.aperture} window')
    if len({c.name for c in PROBE}) != len(PROBE):
        problems.append('duplicate register names in the probe plan')
    for name, word in INDEPENDENT_WORDS.items():
        reg = next((c for c in PROBE if c.name == name), None)
        if reg is None:
            problems.append(f'{name} is missing from the probe plan')
        elif reg.aperture != GMU or reg.index - GMU2GPU_OFFSET != word:
            problems.append(f'{name}: index {reg.index:#x} does not rebase to the '
                            f'independent word {word:#x}')
    return problems


def main() -> int:
    print(f'GPU aperture {GPU_REG_BASE:#010x}; GMU aperture {GMU_REG_BASE:#010x} '
          f'+ {GMU_REG_SIZE:#x}; PDC aperture {PDC_REG_BASE:#010x} + {PDC_REG_SIZE:#x}')
    print(f'gmu2gpu_offset = ({GMU_REG_BASE:#x} - {GPU_REG_BASE:#x}) >> 2 = '
          f'{GMU2GPU_OFFSET:#x} words  =>  byte = (index - {GMU2GPU_OFFSET:#x}) * 4')
    print('PDC is a separate ioremap and is not rebased: byte = index * 4')
    print()
    for csr in PROBE:
        print(f'{csr.name:26s} {csr.aperture:3s} index {csr.index:#07x}  '
              f'+{byte_offset(csr):#07x}  phys {phys(csr):#010x}  {csr.note}')
    problems = check()
    if problems:
        for p in problems:
            print(f'FAIL  {p}', file=sys.stderr)
        return 1
    print()
    print(f'ok    {len(PROBE)} read-only registers, each inside its own window, '
          f'{len(INDEPENDENT_WORDS)} confirmed against an independent implementation')
    return 0


if __name__ == '__main__':
    sys.exit(main())
