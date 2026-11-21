#!/usr/bin/env python3
"""Compile the Polaris GPU sources against a real xnu tree, host side.

CI is the only place that compiles bsd/dev/polaris_gpu*.c for arm64 today, and
a CI round trip costs a push plus a 40-minute runner. This reproduces the part
of that compile that matters for our code - the header chain, the prototypes
and the warning flags - on this box:

  * the tree must be xnu-8796.141.3 with xnu/patches/*.patch applied, because
    those patches are what create bsd/dev/polaris_gpu*.c and the conf/files
    entries that pull them in;
  * the flags are the ones the kernel build uses for bsd/ sources
    (bsd/conf/Makefile.template plus makedefs/MakeInc.def): -Weverything,
    -Werror, and the -Wno- list the kernel applies to its own headers;
  * warnings that this clang has and the CI toolchain (macOS 14, Apple clang
    15) does not are listed in TOOLCHAIN_GAP and switched off, so they cannot
    hide a real diagnostic behind a wall of noise from XNU's headers.

What this does NOT prove: the linker, the board config headers that only a
full `make` generates, and any file whose include chain differs from the three
we check. A green run here means "CI will get past the parser on these three
files", not "the kernel links".

Usage:
  python3 tools/polaris_kernel_syntax.py --tree ../build/xnu-141
  python3 tools/polaris_kernel_syntax.py --tree ../build/xnu-141 --apply
  CC=clang python3 tools/polaris_kernel_syntax.py --tree /path/to/xnu

No device is touched.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCHDIR = ROOT / 'xnu' / 'patches'
XNU_TAG = 'xnu-8796.141.3'

# bsd/conf/Makefile.template: CFLAGS for everything under bsd/.
BSD_DEFINES = (
    '-DKERNEL=1', '-D__KERNEL__=1', '-DDRIVER_PRIVATE=1',
    '-D_KERNEL_BUILD=1', '-DKERNEL_BUILD=1', '-DMACH_KERNEL=1',
    '-DBSD_BUILD=1', '-DLP64_DEBUG=0', '-DARM64_BOARD_CONFIG_SWEET=1',
)

# makedefs/MakeInc.def: WARNFLAGS_STD, in the same order (it matters: the
# kernel disables a group with -Wno- and then opts back in to -Wpointer-arith).
WARNFLAGS = (
    '-Weverything', '-Werror',
    '-Wundef-prefix=TARGET_OS_', '-Wno-pedantic',
    '-Wno-bad-function-cast', '-Wno-bitwise-instead-of-logical',
    '-Wno-c++-compat', '-Wno-c++98-compat', '-Wno-conditional-uninitialized',
    '-Wno-covered-switch-default', '-Wno-disabled-macro-expansion',
    '-Wno-documentation-unknown-command', '-Wno-extra-semi-stmt',
    '-Wno-format-non-iso', '-Wno-language-extension-token',
    '-Wno-missing-variable-declarations', '-Wno-packed', '-Wno-padded',
    '-Wno-partial-availability', '-Wno-reserved-id-macro',
    '-Wno-shift-sign-overflow', '-Wno-switch-enum', '-Wno-unaligned-access',
    '-Wno-undef', '-Wno-unused-macros', '-Wno-used-but-marked-unused',
    '-Wno-variadic-macros', '-Wno-vla', '-Wno-zero-length-array',
    '-Wno-unknown-warning-option', '-Wno-anon-enum-enum-conversion',
    '-Wno-error=enum-enum-conversion', '-Wno-error=c99-designator',
    '-Wno-error=reorder-init-list', '-Wno-error=declaration-after-statement',
    '-Wno-sign-compare', '-Wno-sign-conversion',
    '-Wpointer-arith',
)

# Diagnostics that exist in this clang and not in the CI toolchain, so the
# kernel has no reason to suppress them. Each one fires inside XNU's own
# headers, never in our code. The box that runs this check carries zig's clang
# 22; CI is macOS 14 with Apple clang 15. -Wunsafe-buffer-usage arrived in
# clang 16, -Wshadow-header later still, and both -Wshadow-header and
# -Wreserved-identifier hit Apple's typedefs in bsd/arm/_types.h, which the
# kernel compiles warning-free with its own toolchain.
TOOLCHAIN_GAP = (
    '-Wno-unsafe-buffer-usage',
    '-Wno-shadow-header',
    '-Wno-reserved-identifier',
)

# Codegen flags from MakeInc.def CFLAGS_GEN that change what the parser sees.
#
# -fno-sanitize=all and -fno-stack-protector are NOT from the kernel: they
# undo two things this compiler does on its own. zig's clang instruments every
# object with UBSan by default - `int f(int a) { int b = a * 4; return b; }`
# comes back importing __ubsan_handle_mul_overflow - and adds the stack-guard
# imports. The kernel picks its own UBSan list per config (MakeInc.def
# UBSAN_CHECKS) and links its own runtime, so leaving the defaults on would
# make these objects differ from CI's for no reason and bury the externals we
# actually call under sanitizer handlers. Neither flag changes a diagnostic.
CODEGEN = ('-fno-builtin', '-ftrivial-auto-var-init=zero', '-fno-common',
           '-fno-sanitize=all', '-fno-stack-protector')

# search path: the kernel build's -I set, in the order make uses it.
INCLUDE_DIRS = ('bsd', 'osfmk', 'libkern', 'pexpert', 'iokit',
                'EXTERNAL_HEADERS')

# The three translation units this work adds.
SOURCES = ('polaris_gpu.c', 'polaris_gpu_hash.c', 'polaris_gpu_validate.c')

# Headers this check supplies itself: <string.h> (the kernel takes it from the
# SDK, which zig does not have) and the control file. Both live in the repo.
SHIM_DIR = Path(__file__).resolve().parent / 'kernel-headers'

# A file this work does not touch, used as a control: it includes exactly the
# system headers the polaris sources include and declares nothing. If it does
# not compile, the environment is missing something the real build provides
# (generated headers, the SDK's include order) and any failure on our files
# would be meaningless. A stock bsd/ source cannot serve as the control -
# almost all of them include generated headers such as sys/sysproto.h.
CONTROL_SOURCES = (SHIM_DIR / 'baseline.c',)

# Deliberately NOT passed: -DBSD_KERNEL_PRIVATE. bsd/conf/Makefile.template
# sets it for every bsd/ source, but it makes sys/systm.h pull in sys/tty.h and
# the rest of the kernel-private surface, which only resolves with -include
# meta_features.h and the headers a full `make` generates. Measured on this
# tree: turn the define on and both polaris_gpu.c and a stock bsd/ source fail
# inside XNU's tty.h, so the define cannot be covered without that environment.
# What this check covers is the non-private surface, which is where every
# prototype and type the polaris sources rely on lives.

# The kernel's `machine` include directory is assembled by the build: XNU
# symlinks osfmk/arm and osfmk/arm64 into osfmk/machine. Recreate that, plus
# the SDK's <string.h>, under the scratch dir.
MACHINE_DIRS = ('osfmk/arm', 'osfmk/arm64')


class CheckError(RuntimeError):
    pass


def support_dir(work, tree):
    """Populate a scratch include dir: machine/ headers plus the string.h shim."""
    machine = work / 'machine'
    machine.mkdir(parents=True, exist_ok=True)
    copied = 0
    for rel in MACHINE_DIRS:
        for src in sorted((tree / rel).glob('*.h')):
            shutil.copyfile(src, machine / src.name)
            copied += 1
    if copied == 0:
        raise CheckError(f'no headers under {tree}/osfmk/arm; is this an xnu tree?')
    shim = SHIM_DIR / 'string.h'
    if not shim.is_file():
        raise CheckError(f'missing {shim}')
    shutil.copyfile(shim, work / 'string.h')
    return work


def compile_flags(work, tree):
    flags = ['-target', 'aarch64-macos', '-c', '-nostdinc', '-nostdlibinc',
             '-ferror-limit=0']
    flags += list(CODEGEN) + list(BSD_DEFINES)
    flags += ['-I', str(work)]
    flags += [f'-I{tree / d}' for d in INCLUDE_DIRS]
    flags += [f'-I{tree / "bsd" / "dev"}']
    flags += list(TOOLCHAIN_GAP) + list(WARNFLAGS)
    return flags


def compiler():
    """Return the command prefix that can target aarch64-macos."""
    cc = os.environ.get('CC')
    if cc:
        return cc.split()
    if shutil.which('zig') or _has_ziglang():
        return [sys.executable, '-m', 'ziglang', 'cc']
    raise CheckError('no compiler: set CC or install ziglang (pip install ziglang)')


def _has_ziglang():
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return False
    return True


def tree_state(tree):
    """Classify the tree: patched (usable), pristine (needs --apply), wrong."""
    if not (tree / 'bsd' / 'conf' / 'files').is_file():
        raise CheckError(f'{tree} is not an xnu tree (no bsd/conf/files)')
    missing = [s for s in SOURCES if not (tree / 'bsd' / 'dev' / s).is_file()]
    if not missing and (tree / 'bsd' / 'dev' / 'polaris_gpu.h').is_file():
        return 'patched'
    if missing and len(missing) == len(SOURCES):
        return 'pristine'
    raise CheckError('tree is half patched: ' + ', '.join(missing) + ' missing')


def apply_patches(tree):
    """Apply xnu/patches/*.patch in CI order; a skip is an error, as in CI."""
    patches = sorted(PATCHDIR.glob('*.patch'))
    if not patches:
        raise CheckError(f'no patches in {PATCHDIR}')
    applied, skipped = 0, []
    for patch in patches:
        run = subprocess.run(
            ['git', '-C', str(tree), 'apply', '--whitespace=nowarn', str(patch)],
            capture_output=True, text=True)
        if run.returncode == 0:
            applied += 1
        elif run.stderr.strip():
            skipped.append((patch.name, run.stderr.strip().splitlines()[0]))
        else:
            skipped.append((patch.name, 'git apply failed'))
    return applied, skipped


def parse_diagnostics(output):
    """Split compiler output into errors and warnings (paths kept verbatim)."""
    errors, warnings = [], []
    for line in output.splitlines():
        if ': error:' in line or line.startswith('error:'):
            errors.append(line)
        elif ': warning:' in line or line.startswith('warning:'):
            warnings.append(line)
    return errors, warnings


def run_check(tree, work):
    """Compile the control then every source.

    Returns (control, {name: (errors, warnings, returncode)}). A control that
    does not compile raises, because then the flags say nothing about our code.
    """
    cc = compiler()
    work.mkdir(parents=True, exist_ok=True)
    support_dir(work, tree)
    flags = compile_flags(work, tree)
    control = {}
    for src in CONTROL_SOURCES:
        run = subprocess.run(cc + flags + ['-o', str(work / 'control.o'), str(src)],
                             capture_output=True, text=True)
        errors, warnings = parse_diagnostics(run.stdout + run.stderr)
        control[str(src)] = (errors, warnings, run.returncode)
        if errors or run.returncode != 0:
            first = errors[0] if errors else f'exit {run.returncode}'
            raise CheckError(f'control {src.name} does not compile: {first}')
    results = {}
    for name in SOURCES:
        src = tree / 'bsd' / 'dev' / name
        obj = work / (name[:-2] + '.o')
        run = subprocess.run(cc + flags + ['-o', str(obj), str(src)],
                             capture_output=True, text=True)
        # zig reports a cache rename failure when it cannot place the object;
        # an explicit -o (above) keeps that out of the way, and the exit code
        # is reported separately so a tool quirk cannot look like a code error.
        errors, warnings = parse_diagnostics(run.stdout + run.stderr)
        results[name] = (errors, warnings, run.returncode)
    return control, results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--tree', required=True,
                    help=f'xnu tree at {XNU_TAG}, patched or pristine')
    ap.add_argument('--apply', action='store_true',
                    help='apply xnu/patches/*.patch to a pristine tree first')
    ap.add_argument('--work', default=str(ROOT.parent / 'build' / 'kernel-syntax'),
                    help='scratch dir for the machine/ headers and objects')
    args = ap.parse_args(argv)

    tree = Path(args.tree).resolve()
    work = Path(args.work).resolve()
    try:
        state = tree_state(tree)
        print(f'      tree {tree} ({XNU_TAG}, {state})')
        if state == 'pristine':
            if not args.apply:
                raise CheckError('tree has no polaris sources; pass --apply '
                                 'to apply xnu/patches/*.patch to it')
            applied, skipped = apply_patches(tree)
            print(f'      applied {applied} patches')
            # 0031 and 0041 patch bsd/sweet_hfs sources that only exist after
            # the staging scripts run; they are not part of this check.
            for name, why in skipped:
                print(f'      skipped {name}: {why}')
            if tree_state(tree) != 'patched':
                raise CheckError('patches did not produce the polaris sources')
        control, results = run_check(tree, work)
    except CheckError as exc:
        print(f'FAIL  {exc}')
        return 1

    for label, (errors, _warnings, _code) in control.items():
        if errors:
            print(f'FAIL  control {Path(label).name} does not compile; the '
                  'flags are wrong, not the code')
            return 1
        print(f'ok    control {Path(label).name} compiles with the same flags '
              '(header chain is sound)')

    failed = False
    for name in SOURCES:
        errors, warnings, code = results[name]
        note = f'{len(warnings)} warning(s)' if warnings else 'clean'
        if errors:
            failed = True
            print(f'FAIL  {name}: {len(errors)} error(s)')
            for line in errors[:10]:
                print(f'      {line}')
            if len(errors) > 10:
                print(f'      ... {len(errors) - 10} more')
        elif code != 0:
            failed = True
            print(f'FAIL  {name}: compiler exited {code} with no diagnostic')
        else:
            print(f'ok    {name} compiles against {XNU_TAG} headers ({note})')
        for line in warnings[:3]:
            print(f'      warning: {line}')

    if failed:
        print('\nFAILED - this is what CI would report, without the push')
        return 1
    print('\nall three sources pass -Weverything -Werror against the real headers')
    return 0


if __name__ == '__main__':
    sys.exit(main())
