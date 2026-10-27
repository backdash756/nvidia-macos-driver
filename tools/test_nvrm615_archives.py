#!/usr/bin/env python3
"""Test archive validation using synthetic ar/Mach-O fixtures, not Darwin builds."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
RECIPE = Path(__file__).resolve().parents[1] / 'build/nvrm615'
SPEC = importlib.util.spec_from_file_location('nvrm615_archives', RECIPE / 'archives.py')
ARCHIVES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVES)


def object_bytes(cpu=0x01000007, filetype=1, magic=0xFEEDFACF, ncmds=0, sizeofcmds=0):
    return struct.pack('<8I', magic, cpu, 3, filetype, ncmds, sizeofcmds, 0, 0)


def ar_member(name, payload, extended=False):
    name_bytes = name.encode()
    if extended or len(name_bytes) > 16:
        # Native BSD archives may NUL-pad an extended name to an aligned size.
        name_size = (len(name_bytes) + 7) // 8 * 8
        payload = name_bytes.ljust(name_size, b'\0') + payload
        label = '#1/' + str(name_size)
    else:
        label = name
    header = (label.ljust(16) + '0'.ljust(12) + '0'.ljust(6) + '0'.ljust(6)
              + '100644'.ljust(8) + str(len(payload)).ljust(10) + '`\n').encode()
    assert len(header) == 60
    return header + payload + (b'\n' if len(payload) & 1 else b'')


def archive_bytes(names, index='__.SYMDEF', payload=None):
    chunks = [b'!<arch>\n']
    if index is not None:
        chunks.append(ar_member(index, b'index fixture'))
    for name in names:
        chunks.append(ar_member(name, object_bytes() if payload is None else payload))
    return b''.join(chunks)


class ArchiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nvrm615-archive-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'libnvkernel.a'

    def validate(self, data, objects=('one.o',)):
        self.path.write_bytes(data)
        return ARCHIVES.validate_archive(self.path, objects)

    def test_full_rm_and_nvkms_inventories_exclude_the_symbol_index(self):
        for count in (1100, 225):
            with self.subTest(objects=count):
                names = ['g_fixture_' + str(i).zfill(4) + '.o' for i in range(count)]
                data = archive_bytes(names)
                report = self.validate(data, ['_out/Darwin_x86_64/' + name for name in names])
                self.assertEqual(report['object_count'], count)
                self.assertEqual(report['member_count'], count + 1)
                self.assertEqual(report['index_members'], ['__.SYMDEF'])
                self.assertEqual(report['sha256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(report['architecture'], 'x86_64')
                self.assertEqual(report['object_format'], 'MH_MAGIC_64 / MH_OBJECT')
                self.assertEqual(self.path.read_bytes(), data)

    def test_all_explicit_apple_index_variants_are_supported(self):
        for index in ('__.SYMDEF', '__.SYMDEF SORTED', '__.SYMDEF_64', '__.SYMDEF_64 SORTED'):
            with self.subTest(index=index):
                report = self.validate(archive_bytes(['one.o'], index=index))
                self.assertEqual(report['index_members'], [index])
                self.assertEqual(report['object_count'], 1)

    def test_bsd_extended_names_and_nul_padding_are_supported(self):
        name = 'g_access_cntr_buffer_nvoc.o'
        report = self.validate(archive_bytes([name]), ['_out/Darwin_x86_64/' + name])
        self.assertEqual(report['members'][1]['name'], name)
        self.assertEqual(report['members'][1]['payload_size'], 32)

    def test_short_sysv_style_object_name_is_supported(self):
        data = b'!<arch>\n' + ar_member('__.SYMDEF', b'index') + ar_member('one.o/', object_bytes())
        self.assertEqual(self.validate(data)['object_count'], 1)

    def test_missing_extra_and_duplicate_objects_are_rejected(self):
        cases = (
            (['one.o'], ['one.o', 'two.o'], 'missing (1): two.o'),
            (['one.o', 'extra.o'], ['one.o'], 'unexpected (1): extra.o'),
            (['one.o', 'one.o'], ['one.o'], 'duplicate names (1): one.o x2'),
        )
        for names, expected, message in cases:
            with self.subTest(names=names, expected=expected):
                self.path.write_bytes(archive_bytes(names))
                with self.assertRaises(ValueError) as caught:
                    ARCHIVES.validate_archive(self.path, expected)
                self.assertIn(message, str(caught.exception))

    def test_duplicate_expected_basenames_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate expected archive basenames'):
            self.validate(archive_bytes(['one.o']), ['path/a/one.o', 'path/b/one.o'])

    def test_empty_or_non_object_expected_manifest_is_rejected(self):
        for objects in ([], ['one.c']):
            with self.subTest(objects=objects), self.assertRaises(ValueError):
                self.validate(archive_bytes(['one.o']), objects)

    def test_index_is_required_and_duplicate_indexes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'symbol index; found 0'):
            self.validate(archive_bytes(['one.o'], index=None))
        data = archive_bytes(['one.o']) + ar_member('__.SYMDEF SORTED', b'second index')
        with self.assertRaisesRegex(ValueError, 'symbol index; found 2'):
            self.validate(data)

    def test_index_prefix_does_not_hide_an_unknown_member(self):
        data = archive_bytes(['one.o', '__.SYMDEF_extra.o'])
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            self.validate(data)

    def test_arm64_elf_fat_and_32bit_objects_are_rejected(self):
        cases = (
            (object_bytes(cpu=0x0100000C), 'expected x86_64'),
            (b'\x7fELF' + b'\0' * 40, 'MH_MAGIC_64'),
            (b'\xca\xfe\xba\xbe' + b'\0' * 40, 'MH_MAGIC_64'),
            (object_bytes(magic=0xFEEDFACE), 'MH_MAGIC_64'),
        )
        for payload, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.validate(archive_bytes(['one.o'], payload=payload))

    def test_wrong_mach_o_filetype_and_bad_command_bounds_are_rejected(self):
        for payload, message in (
            (object_bytes(filetype=6), 'expected MH_OBJECT'),
            (object_bytes(ncmds=1, sizeofcmds=8), 'load-command bounds'),
            (object_bytes(ncmds=1, sizeofcmds=0), 'load-command bounds'),
            (b'\xcf\xfa\xed\xfe', 'truncated Mach-O'),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.validate(archive_bytes(['one.o'], payload=payload))

    def test_malformed_archive_headers_and_payloads_are_rejected(self):
        valid = archive_bytes(['one.o'])
        broken_size = valid[:8 + 48] + b'invalid   ' + valid[8 + 58:]
        broken_trailer = valid[:8 + 58] + b'XX' + valid[8 + 60:]
        cases = (b'', b'!<thin>\n', b'\xca\xfe\xba\xbe', b'!<arch>\nshort',
                 broken_size, broken_trailer, valid[:-8])
        for data in cases:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.validate(data)

    def test_bad_extended_names_and_unsupported_gnu_metadata_are_rejected(self):
        for label, payload in (('#1/nope', b'name'), ('#1/100', b'short'),
                               ('#1/0', b''), ('/', b'index'), ('//', b'names'),
                               ('/123', object_bytes())):
            data = b'!<arch>\n' + ar_member(label, payload)
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.validate(data)

    def test_missing_alignment_byte_is_rejected(self):
        data = b'!<arch>\n' + ar_member('__.SYMDEF', b'odd')[:-1]
        with self.assertRaisesRegex(ValueError, 'alignment'):
            self.validate(data)

    def test_manifest_error_examples_are_bounded(self):
        names = ['extra_' + str(i).zfill(3) + '.o' for i in range(100)]
        self.path.write_bytes(archive_bytes(names))
        with self.assertRaises(ValueError) as caught:
            ARCHIVES.validate_archive(self.path, ['missing_' + str(i).zfill(3) + '.o' for i in range(100)])
        message = str(caught.exception)
        self.assertIn('missing (100)', message)
        self.assertIn('unexpected (100)', message)
        self.assertIn('...', message)
        self.assertLess(len(message), 600)

    def test_readonly_cli_prints_report_without_modifying_inputs(self):
        data = archive_bytes(['one.o'])
        self.path.write_bytes(data)
        objects = self.root / 'libnvkernel.a.filelist'
        objects.write_text('_out/Darwin_x86_64/one.o\n')
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', ['archives.py', '--archive', str(self.path), '--objects', str(objects)]), redirect_stdout(output):
            result = ARCHIVES.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())['object_count'], 1)
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(objects.read_text(), '_out/Darwin_x86_64/one.o\n')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['libnvkernel.a', 'libnvkernel.a.filelist'])

    def test_cli_failure_is_nonzero_and_does_not_write_a_report(self):
        self.path.write_bytes(archive_bytes(['one.o']))
        objects = self.root / 'bad.filelist'
        objects.write_text('one.o\nunknown output\n')
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', ['archives.py', '--archive', str(self.path), '--objects', str(objects)]), redirect_stdout(output), redirect_stderr(errors):
            result = ARCHIVES.main()
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('STOP:', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
