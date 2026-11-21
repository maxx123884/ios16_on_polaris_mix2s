#!/usr/bin/env python3
"""Read the GPU register windows out of Polaris' vendor device tree.

The addresses in the GPU driver are not things to remember by hand: they come
from the device tree QCDT blob that Qualcomm appends to the MIUI boot image
kernel. This module parses an FDT (raw, or the blob appended to a boot.img
kernel) and returns the windows with the node path and compatible string they
came from, so every address has a provenance that can be re-derived.

    python tools/polaris_dt_regs.py <boot.img|dtb>

Nothing here talks to a device; it only reads a file you supply.
"""
from __future__ import annotations

import struct
import sys

FDT_MAGIC = b'\xd0\x0d\xfe\xed'
ANDROID_MAGIC = b'ANDROID!'

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9

# Node compatibles that identify the GPU blocks on SDM845v2 (polaris). The v2
# suffix is not decoration: GPUs with the plain sdm845 compatible string have
# different GPUCC register layouts.
GPUCC_COMPAT = 'qcom,gpucc-sdm845-v2'
GMU_COMPAT = 'qcom,gpu-gmu'


class DtError(ValueError):
    pass


def _cstr(data: bytes, off: int) -> str:
    end = data.index(b'\0', off)
    return data[off:end].decode('utf-8', 'replace')


def parse_fdt(data: bytes) -> list[dict]:
    """Return every node as {path, name, props}; props map name -> bytes."""
    if data[:4] != FDT_MAGIC:
        raise DtError('not an FDT: bad magic')
    total = struct.unpack_from('>I', data, 4)[0]
    off_struct, off_strings = struct.unpack_from('>II', data, 8)
    if total > len(data):
        raise DtError(f'FDT totalsize {total} exceeds the {len(data)} bytes given')

    nodes: list[dict] = []
    stack: list[str] = []
    p = off_struct
    while True:
        tag = struct.unpack_from('>I', data, p)[0]
        p += 4
        if tag == FDT_BEGIN_NODE:
            name = _cstr(data, p)
            p = (p + len(name) + 1 + 3) & ~3
            stack.append(name)
            node = {'name': name, 'path': '/'.join(n for n in stack if n),
                    'props': {}}
            nodes.append(node)
        elif tag == FDT_END_NODE:
            if not stack:
                raise DtError('unbalanced FDT_END_NODE')
            stack.pop()
            if not stack:
                break
        elif tag == FDT_PROP:
            length, name_off = struct.unpack_from('>II', data, p)
            p += 8
            value = data[p:p + length]
            p = (p + length + 3) & ~3
            if not nodes:
                raise DtError('property before any node')
            nodes[-1]['props'][_cstr(data, off_strings + name_off)] = value
        elif tag == FDT_NOP:
            continue
        elif tag == FDT_END:
            break
        else:
            raise DtError(f'bad FDT tag {tag:#x} at {p - 4}')
    return nodes


def _compatible(node: dict) -> list[str]:
    raw = node['props'].get('compatible', b'')
    return [s for s in raw.decode('utf-8', 'replace').split('\0') if s]


def _cells(node: dict) -> list[int]:
    """Return the reg property as 32-bit cells (address-cells follows the root)."""
    raw = node['props'].get('reg')
    if raw is None:
        return []
    if len(raw) % 4:
        raise DtError(f"{node['path']}: reg length {len(raw)} is not a whole "
                      'number of cells')
    return [struct.unpack_from('>I', raw, i)[0] for i in range(0, len(raw), 4)]


def _cell_prop(node: dict, name: str, default: int) -> int:
    raw = node['props'].get(name)
    if not raw or len(raw) != 4:
        return default
    return struct.unpack('>I', raw)[0]


def _windows(node: dict, names: list[str], parent: dict | None
             ) -> dict[str, tuple[int, int]]:
    cells = _cells(node)
    if not cells:
        return {}
    # reg is laid out with the parent's #address-cells / #size-cells. They are
    # not uniform in this tree: /soc uses 1+1 on polaris while /reserved-memory
    # uses 2+2, so reading reg as fixed pairs silently turns a 64-bit base into
    # a size. Take the counts from the parent and refuse an off-by-cells reg.
    addr = _cell_prop(parent or node, '#address-cells', 1)
    size = _cell_prop(parent or node, '#size-cells', 1)
    stride = addr + size
    if stride <= 0 or len(cells) % stride:
        raise DtError(f"{node['path']}: reg has {len(cells)} cells, not a "
                      f"multiple of {stride} (#address-cells={addr}, "
                      f'#size-cells={size})')

    def join(part: list[int]) -> int:
        value = 0
        for c in part:
            value = (value << 32) | c
        return value

    pairs = []
    for i in range(0, len(cells), stride):
        pairs.append((join(cells[i:i + addr]), join(cells[i + addr:i + stride])))
    if names and len(names) == len(pairs):
        return dict(zip(names, pairs))
    return {f'reg{i}': p for i, p in enumerate(pairs)}


def gpu_windows(nodes: list[dict]) -> dict[str, dict]:
    """Pick the GPUCC, GMU and zap windows, each with its provenance."""
    found: dict[str, dict] = {}
    index = {n['path']: n for n in nodes}

    def parent_of(node: dict) -> dict | None:
        path = node['path'].rsplit('/', 1)[0]
        return index.get(path)

    for node in nodes:
        comp = _compatible(node)
        if GPUCC_COMPAT in comp or ('qcom,gpucc-sdm845' in comp):
            win = _windows(node, [], parent_of(node))
            base, size = win['reg0']
            found['gpucc'] = {
                'base': base, 'size': size,
                'compatible': GPUCC_COMPAT if GPUCC_COMPAT in comp else comp[0],
                'node': node['path'],
            }
        elif GMU_COMPAT in comp:
            names = [s for s in
                     node['props'].get('reg-names', b'').decode('utf-8', 'replace')
                     .split('\0') if s]
            win = _windows(node, names, parent_of(node))
            for key, name in (('gmu', 'kgsl_gmu_reg'),
                              ('gmu_pdc', 'kgsl_gmu_pdc_reg')):
                if name in win:
                    base, size = win[name]
                    found[key] = {'base': base, 'size': size,
                                  'compatible': GMU_COMPAT,
                                  'node': node['path'], 'reg_name': name}

    for node in nodes:
        comp = _compatible(node)
        if 'removed-dma-pool' in comp and node['name'].startswith('gpu_region@'):
            base, size = _windows(node, [], parent_of(node))['reg0']
            found['zap_region'] = {'base': base, 'size': size,
                                   'compatible': 'removed-dma-pool',
                                   'node': node['path']}
    return found


def find_fdt(data: bytes) -> bytes:
    """Return the FDT from a raw blob or from the tail of a boot.img kernel."""
    if data[:4] == FDT_MAGIC:
        return data
    if data[:8] == ANDROID_MAGIC:
        kernel_size = struct.unpack_from('<I', data, 8)[0]
        kernel = data[2048:2048 + kernel_size]
        off = kernel.find(FDT_MAGIC)
        if off < 0:
            raise DtError('boot image kernel has no appended FDT')
        total = struct.unpack_from('>I', kernel, off + 4)[0]
        return kernel[off:off + total]
    off = data.find(FDT_MAGIC)
    if off < 0:
        raise DtError('no FDT magic in the file given')
    total = struct.unpack_from('>I', data, off + 4)[0]
    return data[off:off + total]


def check(windows: dict[str, dict]) -> list[str]:
    """Structural checks that a wrong file or a wrong SoC would break."""
    problems: list[str] = []
    for need in ('gpucc', 'gmu', 'gmu_pdc', 'zap_region'):
        if need not in windows:
            problems.append(f'{need} window missing from this device tree')
    if problems:
        return problems

    for name, w in windows.items():
        if w['size'] == 0:
            problems.append(f'{name}: zero size')
        if w['base'] % 0x1000:
            problems.append(f"{name}: base {w['base']:#x} is not 4K aligned")
        if w['base'] + w['size'] > 0x1_0000_0000:
            problems.append(f"{name}: window runs past the 32-bit address space")
        if name == 'gpucc' and w['compatible'] != GPUCC_COMPAT:
            problems.append(
                f"gpucc compatible is {w['compatible']}, not {GPUCC_COMPAT}: "
                'a plain sdm845 GPUCC has a different register layout')

    return problems


def overlaps(windows: dict[str, dict]) -> list[str]:
    """Windows can legitimately overlap in this device tree.

    The vendor DT declares kgsl_gmu_reg as 0x506a000+0x30000, which reaches
    0x509a000 and therefore covers gpucc at 0x5090000. The hardware has
    separate sub-apertures there, so this is a description detail to know
    about, not an error to reject.
    """
    notes: list[str] = []
    names = sorted(windows)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            wa, wb = windows[a], windows[b]
            if wa['base'] < wb['base'] + wb['size'] and \
                    wb['base'] < wa['base'] + wa['size']:
                notes.append(f'{a} and {b} windows overlap')
    return notes


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print('usage: python tools/polaris_dt_regs.py <boot.img|dtb>',
              file=sys.stderr)
        return 2
    data = open(argv[1], 'rb').read()
    try:
        windows = gpu_windows(parse_fdt(find_fdt(data)))
    except DtError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1

    for name in ('gpucc', 'gmu', 'gmu_pdc', 'zap_region'):
        w = windows.get(name)
        if not w:
            continue
        print(f'{name:11s} {w["base"]:#010x} + {w["size"]:#08x}  '
              f'[{w.get("reg_name", w["compatible"])}]  {w["node"]}')

    for note in overlaps(windows):
        print(f'note  {note}')

    problems = check(windows)
    if problems:
        for p in problems:
            print(f'FAIL  {p}', file=sys.stderr)
        return 1
    print('ok    windows are aligned and the GPUCC is sdm845-v2')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
