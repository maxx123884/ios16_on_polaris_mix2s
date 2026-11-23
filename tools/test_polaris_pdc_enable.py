#!/usr/bin/env python3
"""The PDC enable value exists twice: as the string the CSR tool pins, and as
the constant the kernel writes. They drifted once - mode 2 wrote 0x1 while the
tool had said 0x80000001 since it was written - so assert they agree.

    python3 tools/test_polaris_pdc_enable.py
"""
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    csr = (ROOT / 'tools' / 'polaris_gmu_csr.py').read_text(encoding='utf-8')
    m = re.search(r"PDC_GPU_ENABLE_PDC',\s*0x21140,\s*'written (0x[0-9a-fA-F]+)'", csr)
    if not m:
        print('FAIL: polaris_gmu_csr.py no longer states the written value')
        return 1
    tool_value = int(m.group(1), 16)

    tree = os.environ.get('PG_TREE')
    src = (pathlib.Path(tree) / 'bsd' / 'dev' / 'polaris_gpu.c') if tree \
        else (ROOT / 'bsd' / 'dev' / 'polaris_gpu.c')
    if not src.is_file():
        print('SKIP: no patched tree (set PG_TREE)')
        return 0
    text = src.read_text(encoding='utf-8')
    m = re.search(r'#define\s+POLARIS_PDC_ENABLE_VALUE\s+(0x[0-9a-fA-F]+)u', text)
    if not m:
        print('FAIL: polaris_gpu.c has no POLARIS_PDC_ENABLE_VALUE')
        return 1
    c_value = int(m.group(1), 16)

    if tool_value != c_value:
        print(f'FAIL: tool pins {tool_value:#x} but the kernel writes {c_value:#x}')
        return 1
    print(f'ok: PDC_GPU_ENABLE_PDC = {c_value:#x} in both places')
    return 0


if __name__ == '__main__':
    sys.exit(main())
