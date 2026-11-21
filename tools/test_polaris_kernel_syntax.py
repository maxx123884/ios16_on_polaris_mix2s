#!/usr/bin/env python3
"""Tests for the host-side kernel syntax check.

The check itself needs a real xnu tree; these tests pin down the parts that
decide whether it can be trusted: the flag set is the kernel's, the source list
is every file our patches add under bsd/dev, the control file really includes
what our sources include, and compiler output is read the way a human reads it.
"""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polaris_kernel_syntax as ks

PATCHDIR = Path(__file__).resolve().parent.parent / 'xnu' / 'patches'


class Flags(unittest.TestCase):
    def test_werror_is_on(self):
        self.assertIn('-Werror', ks.WARNFLAGS)
        self.assertIn('-Weverything', ks.WARNFLAGS)

    def test_pointer_arith_is_opted_in_after_the_group_disable(self):
        # makedefs/MakeInc.def disables a group with -Wno- and then opts back in
        # to -Wpointer-arith; the opt-in has to come after the group.
        self.assertEqual(ks.WARNFLAGS[-1], '-Wpointer-arith')
        self.assertLess(ks.WARNFLAGS.index('-Weverything'),
                        ks.WARNFLAGS.index('-Wpointer-arith'))

    def test_toolchain_gap_only_disables(self):
        for gap in ks.TOOLCHAIN_GAP:
            self.assertTrue(gap.startswith('-Wno-'), gap)
            self.assertNotIn('-W' + gap[len('-Wno-'):], ks.WARNFLAGS)

    def test_bsd_defines_match_makefile_template(self):
        for define in ('-DKERNEL_BUILD=1', '-DMACH_KERNEL=1', '-DBSD_BUILD=1',
                       '-DLP64_DEBUG=0', '-DARM64_BOARD_CONFIG_SWEET=1'):
            self.assertIn(define, ks.BSD_DEFINES)

    def test_instrumentation_is_switched_off(self):
        # zig's clang turns UBSan on by default and imports __ubsan_handle_*;
        # the kernel uses its own list, so the check must not inherit zig's.
        self.assertIn('-fno-sanitize=all', ks.CODEGEN)
        self.assertIn('-fno-stack-protector', ks.CODEGEN)

    def test_bsd_kernel_private_is_deliberately_absent(self):
        # Turning it on pulls sys/tty.h in through sys/systm.h, which needs the
        # generated build environment. If someone adds it, the check starts
        # failing inside XNU's own headers and looks like our bug.
        self.assertNotIn('-DBSD_KERNEL_PRIVATE=1', ks.BSD_DEFINES)

    def test_search_path_starts_with_the_scratch_dir(self):
        flags = ks.compile_flags(Path('/work'), Path('/tree'))
        first = flags.index('-I')
        self.assertEqual(Path(flags[first + 1]), Path('/work'))
        joined = {Path(f[2:]) for f in flags if f.startswith('-I') and f[2:]}
        for wanted in ('/tree/bsd', '/tree/osfmk', '/tree/EXTERNAL_HEADERS'):
            self.assertIn(Path(wanted), joined)


class Sources(unittest.TestCase):
    def test_every_polaris_source_the_patches_add_is_checked(self):
        # 0093-0096 are the handoff series; 0090-0092 add polaris_gpu_diag.c,
        # a read-only diagnostic with no handoff code in it.
        added = set()
        for patch in sorted(PATCHDIR.glob('009[3-6]-*.patch')):
            text = patch.read_text(encoding='utf-8', errors='replace')
            for line in text.splitlines():
                if line.startswith('+++ b/bsd/dev/polaris_gpu') and line.endswith('.c'):
                    added.add(Path(line[len('+++ b/bsd/dev/'):]).name)
        self.assertEqual(added, set(ks.SOURCES))

    def test_the_sources_and_their_header_are_created_by_the_series(self):
        text = ''.join(p.read_text(encoding='utf-8', errors='replace')
                       for p in sorted(PATCHDIR.glob('009*.patch')))
        for name in ks.SOURCES:
            self.assertIn(f'+++ b/bsd/dev/{name}', text)
        self.assertIn('+++ b/bsd/dev/polaris_gpu.h', text)


class Control(unittest.TestCase):
    HEADERS = ('<sys/param.h>', '<sys/systm.h>', '<pexpert/pexpert.h>',
               '<pexpert/device_tree.h>', '<mach/vm_types.h>',
               '<libkern/crypto/sha2.h>')

    def test_baseline_exists_and_includes_the_kernel_headers(self):
        text = (ks.SHIM_DIR / 'baseline.c').read_text()
        for header in self.HEADERS:
            self.assertIn(header, text)

    def test_baseline_declares_nothing_but_a_probe(self):
        # It must not grow into a second copy of our logic.
        body = text_lines((ks.SHIM_DIR / 'baseline.c'))
        code = [l for l in body if l and not l.startswith(('#', '*', '/*'))]
        self.assertEqual(code, ['int pg_baseline_probe;'])

    def test_control_is_a_path_that_exists(self):
        for src in ks.CONTROL_SOURCES:
            self.assertTrue(Path(src).is_file(), src)


def text_lines(path):
    return [line.strip() for line in Path(path).read_text().splitlines()]


def write_macho(path, symbols):
    """A minimal 64-bit Mach-O with one LC_SYMTAB and the given nlist entries.

    symbols: [(name, n_type)] where n_type is N_EXT|N_SECT for a definition and
    N_EXT|N_UNDF for an external. Enough for a symbol-table reader, which is all
    the tool looks at.
    """
    names = b'\0'
    entries = []
    for name, ntype in symbols:
        entries.append((len(names), ntype))
        names += name.encode() + b'\0'
    cmd_size = 24
    symoff = 32 + cmd_size
    stroff = symoff + 16 * len(entries)
    blob = struct.pack('<IiiIIIII', 0xfeedfacf, 0x0100000c, 0, 1, 1,
                       cmd_size, 0, 0)
    blob += struct.pack('<IIIIII', 2, cmd_size, symoff, len(entries),
                        stroff, len(names))
    for strx, ntype in entries:
        blob += struct.pack('<IBBHQ', strx, ntype, 0, 0, 0)
    path.write_bytes(blob + names)


class MachO(unittest.TestCase):
    def test_defined_and_undefined_are_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / 'x.o'
            write_macho(obj, [('_our_entry', 0x0f), ('_ml_io_map', 0x01)])
            defined, undefined = ks.macho_symbols(obj)
            self.assertEqual(defined, ['our_entry'])
            self.assertEqual(undefined, ['ml_io_map'])

    def test_a_local_symbol_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / 'x.o'
            # N_SECT without N_EXT: a static function, invisible to the link.
            write_macho(obj, [('_local_helper', 0x0e), ('_global', 0x0f)])
            defined, undefined = ks.macho_symbols(obj)
            self.assertEqual(defined, ['global'])
            self.assertEqual(undefined, [])

    def test_an_elf_object_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / 'x.o'
            obj.write_bytes(b'\x7fELF' + b'\0' * 60)
            with self.assertRaises(ks.CheckError):
                ks.macho_symbols(obj)


class Definitions(unittest.TestCase):
    def test_kernel_style_definition(self):
        # Return type on the previous line, name at column 0.
        self.assertTrue(ks.looks_like_definition('polaris_touch_i2c_board(void)',
                                                 'polaris_touch_i2c_board'))
        self.assertTrue(ks.looks_like_definition('ml_io_map(', 'ml_io_map'))
        self.assertTrue(ks.looks_like_definition('static int foo(void) {',
                                                 'foo'))

    def test_a_wrapped_call_is_not_a_definition(self):
        # bsd/dev/mem.c:326 wraps a call and fooled the first version.
        line = '\t    PE_parse_boot_argn("kmem", &kmem, sizeof(kmem))) {'
        self.assertFalse(ks.looks_like_definition(line, 'PE_parse_boot_argn'))

    def test_a_call_statement_is_not_a_definition(self):
        self.assertFalse(ks.looks_like_definition('polaris_gpu_init();',
                                                  'polaris_gpu_init'))

    def test_resolution_finds_definitions_and_macros(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / 'bsd' / 'dev').mkdir(parents=True)
            (tree / 'bsd' / 'dev' / 'thing.c').write_text(
                'int\n'
                'thing_open(\n'
                '\tint flags)\n')
            (tree / 'osfmk' / 'kern').mkdir(parents=True)
            (tree / 'osfmk' / 'kern' / 'shim.h').write_text(
                '#define bzero(p, n) memset((p), 0, (n))\n')
            found = ks.resolve_externals(tree, ['thing_open', 'bzero'], set())
            self.assertEqual(found.get('thing_open'), 'bsd/dev/thing.c')
            self.assertEqual(found.get('bzero'), 'osfmk/kern/shim.h')

    def test_resolution_does_not_use_host_tools(self):
        # SETUP/ and tools/ define printf and memcpy for the *host*; a hit
        # there would silently "prove" a kernel symbol that is not there.
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / 'SETUP' / 'config').mkdir(parents=True)
            (tree / 'SETUP' / 'config' / 'tool.c').write_text(
                'int\nprintf(const char *fmt)\n')
            self.assertEqual(ks.resolve_externals(tree, ['printf'], set()), {})

    def test_resolution_skips_our_own_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / 'bsd' / 'dev').mkdir(parents=True)
            (tree / 'bsd' / 'dev' / 'polaris_gpu.c').write_text(
                'int\npg_validate_layout(void)\n')
            got = ks.resolve_externals(tree, ['pg_validate_layout'],
                                       {'polaris_gpu.c'})
            self.assertEqual(got, {})


class Symbols(unittest.TestCase):
    def test_no_objects_is_an_error_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = ks.check_symbols(Path(tmp), Path(tmp))
            self.assertIn('error', got)


class Diagnostics(unittest.TestCase):
    def test_errors_and_warnings_are_separated(self):
        out = ('bsd/dev/polaris_gpu.c:231:1: error: no previous prototype '
               'for function [-Werror,-Wmissing-prototypes]\n'
               'osfmk/kern/kcdata.h:1612:11: warning: mixing declarations '
               '[-Wdeclaration-after-statement]\n'
               '2 errors generated.\n')
        errors, warnings = ks.parse_diagnostics(out)
        self.assertEqual(len(errors), 1)
        self.assertIn('no previous prototype', errors[0])
        self.assertEqual(len(warnings), 1)

    def test_a_summary_line_is_neither(self):
        self.assertEqual(ks.parse_diagnostics('4 errors generated.\n'),
                         ([], []))

    def test_a_tool_failure_on_the_output_path_still_counts(self):
        # zig failed to move the object into its cache on Windows; that line
        # has to be visible, not swallowed, because it also sets exit != 0.
        out = ("bsd/dev/polaris_gpu.c:1:1: error: failed to rename 'a.o' "
               "into 'b.o': FileNotFound\n")
        errors, _ = ks.parse_diagnostics(out)
        self.assertEqual(len(errors), 1)


class SupportDir(unittest.TestCase):
    def test_machine_headers_and_shim_are_placed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / 'tree'
            (tree / 'osfmk' / 'arm').mkdir(parents=True)
            (tree / 'osfmk' / 'arm64').mkdir(parents=True)
            (tree / 'osfmk' / 'arm' / 'machine_routines.h').write_text('/* a */')
            (tree / 'osfmk' / 'arm64' / 'pmap.h').write_text('/* b */')
            work = Path(tmp) / 'work'
            ks.support_dir(work, tree)
            self.assertEqual(sorted(p.name for p in (work / 'machine').iterdir()),
                             ['machine_routines.h', 'pmap.h'])
            self.assertTrue((work / 'string.h').is_file())

    def test_a_tree_without_headers_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'osfmk' / 'arm').mkdir(parents=True)
            with self.assertRaises(ks.CheckError):
                ks.support_dir(Path(tmp) / 'work', Path(tmp))


class Shim(unittest.TestCase):
    def test_shim_declares_the_functions_the_headers_need(self):
        text = (ks.SHIM_DIR / 'string.h').read_text()
        for fn in ('memcpy', 'memset', 'memmove', 'memcmp', 'strnlen'):
            self.assertIn(fn, text)

    def test_shim_has_no_definitions(self):
        after_comment = (ks.SHIM_DIR / 'string.h').read_text().split('*/', 1)[-1]
        self.assertNotIn('{', after_comment)


class Compiler(unittest.TestCase):
    def test_cc_wins(self):
        old = os.environ.get('CC')
        os.environ['CC'] = 'clang -target aarch64-macos'
        try:
            self.assertEqual(ks.compiler(),
                             ['clang', '-target', 'aarch64-macos'])
        finally:
            if old is None:
                del os.environ['CC']
            else:
                os.environ['CC'] = old

    def test_this_repo_is_not_an_xnu_tree(self):
        with self.assertRaises(ks.CheckError):
            ks.tree_state(Path(__file__).resolve().parent.parent)


if __name__ == '__main__':
    unittest.main()
