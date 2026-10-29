#!/usr/bin/env python3
"""Regress KMOD symbol/record verification with synthetic Mach-O containers.

These tests are static format fixtures, not a Darwin NVRM build or loader test.
The fixed layout follows Apple's published pack(4) kmod_info_64_v1 definition.
"""
from contextlib import redirect_stderr, redirect_stdout
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
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nvrm615meta_kmod_tested', ROOT / 'build/nvrm615meta/kmod.py')
KMOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(KMOD)


def fixed(value, width=64):
    if isinstance(value, str):
        value = value.encode()
    return value.ljust(width, b'\0')


def relocation(section_offset, width=8):
    length = {1: 0, 2: 1, 4: 2, 8: 3}[width]
    return struct.pack('<II', section_offset, 1 | (length << 25))


def fixture(version='2.5.4', name='com.nullmoth.NVRM', info_version=1,
            base=0x100000000, record_offset=0x220, section_size=0x200,
            section_flags=0, symbol_name='_kmod_info', symbol_kind=0xF,
            symbol_address=None, duplicate=False, inner_symbol=False,
            relocs=b'', dyld=False, unmapped=False, overlapping=False,
            filetype=11, readable=True):
    data = bytearray(0x500)
    record = struct.pack('<QiI64s64siQQQQQQ', 0, info_version, 0xFFFFFFFF,
                         fixed(name), fixed(version), -1, 0, 0, 0, 0, 0x1230, 0x1240)
    assert len(record) == 196
    data[record_offset:record_offset + 196] = record
    # A decoy matching version outside the actual KMOD record must not pass.
    data[0x480:0x486] = b'2.5.4\0'
    address = base + record_offset if symbol_address is None else symbol_address
    symbols = [(symbol_name, symbol_kind, 1 if symbol_kind & 0x0E == 0x0E else 0, address)]
    if duplicate:
        symbols.append(('_kmod_info', 0xF, 1, address))
    if inner_symbol:
        symbols.append(('_unrelated', 0xF, 1, base + record_offset + 80))
    strings, entries = bytearray(b'\0'), []
    for symbol, kind, section, value in symbols:
        index = len(strings)
        strings.extend(symbol.encode() + b'\0')
        entries.append(struct.pack('<IBBHQ', index, kind, section, 0, value))

    def append(payload):
        offset = len(data)
        data.extend(payload)
        return offset

    reloff = append(relocs) if relocs else 0
    symoff = append(b''.join(entries))
    stroff = append(strings)
    extra = []
    if dyld:
        offset = append(b'\0')
        extra.append(struct.pack('<12I', 0x80000022, 48, offset, 1, 0, 0, 0, 0, 0, 0, 0, 0))
    if unmapped:
        offset = append(relocation(0x80))
        values = [0xB, 80] + [0] * 18
        values[18:20] = [offset, 1]
        extra.append(struct.pack('<20I', *values))
    sections = [struct.pack('<16s16sQQIIIIIIII', fixed('__data', 16), fixed('__DATA', 16),
                base + 0x200, section_size, 0x200, 2, reloff, len(relocs) // 8,
                section_flags, 0, 0, 0)]
    if overlapping:
        sections.append(struct.pack('<16s16sQQIIIIIIII', fixed('__other', 16), fixed('__DATA', 16),
            base + record_offset + 80, 64, record_offset + 80, 2, 0, 0, 0, 0, 0, 0))
    segment = struct.pack('<II16sQQQQiiII', 0x19, 72 + len(sections) * 80,
                          fixed('__DATA', 16), base, 0x1000, 0, len(data),
                          3, 3 if readable else 0, len(sections), 0) + b''.join(sections)
    symtab = struct.pack('<6I', 2, 24, symoff, len(entries), stroff, len(strings))
    commands = [segment, symtab] + extra
    raw = b''.join(commands)
    assert 32 + len(raw) <= 0x200
    data[:32] = struct.pack('<8I', 0xFEEDFACF, 0x01000007, 3, filetype,
                            len(commands), len(raw), 0, 0)
    data[32:32 + len(raw)] = raw
    return bytes(data)


class KmodRecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nvrm615meta-kmod-')
        self.addCleanup(self.temporary.cleanup)
        self.binary = Path(self.temporary.name) / 'NVRM'

    def inspect(self, raw=None, **kwargs):
        self.binary.write_bytes(fixture() if raw is None else raw)
        return KMOD.inspect_kmod(self.binary, **kwargs)

    def rejected(self, text, **kwargs):
        self.binary.write_bytes(fixture(**kwargs))
        original, mtime = self.binary.read_bytes(), self.binary.stat().st_mtime_ns
        with self.assertRaisesRegex(ValueError, text):
            KMOD.inspect_kmod(self.binary)
        self.assertEqual(self.binary.read_bytes(), original)
        self.assertEqual(self.binary.stat().st_mtime_ns, mtime)

    def test_real_symbol_record_and_explicit_layout_are_reported(self):
        self.binary.write_bytes(fixture())
        original, mtime = self.binary.read_bytes(), self.binary.stat().st_mtime_ns
        result = KMOD.inspect_kmod(self.binary)
        self.assertEqual(result['status'], 'verified_static_metadata')
        self.assertEqual(result['name'], 'com.nullmoth.NVRM')
        self.assertEqual(result['version'], '2.5.4')
        self.assertEqual(result['info_version'], 1)
        self.assertEqual(result['record_size'], 196)
        self.assertEqual(result['record_file_offset'], 0x220)
        self.assertEqual(result['field_file_offsets']['name'], 0x230)
        self.assertEqual(result['field_file_offsets']['version'], 0x270)
        self.assertEqual(result['field_offsets']['reference_count'], 144)
        self.assertEqual(result['field_offsets']['reference_list'], 148)
        self.assertEqual(result['field_offsets']['start'], 180)
        self.assertEqual(result['field_offsets']['stop'], 188)
        self.assertEqual(result['encoded_fields']['id'], 0xFFFFFFFF)
        self.assertEqual(result['encoded_fields']['reference_count'], -1)
        self.assertEqual(result['encoded_fields']['start'], 0x1230)
        self.assertIn('pack(push, 4)', result['layout_source']['definition'])
        self.assertEqual(self.binary.read_bytes(), original)
        self.assertEqual(self.binary.stat().st_mtime_ns, mtime)

    def test_wrong_version_is_rejected_despite_matching_decoy_string(self):
        self.rejected('version mismatch', version='0.1')

    def test_expected_version_can_verify_an_unchanged_reference(self):
        result = self.inspect(fixture(version='0.1'), expected_version='0.1')
        self.assertEqual(result['version'], '0.1')

    def test_unrelated_or_missing_symbol_is_not_a_strings_fallback(self):
        self.rejected('unique defined external', symbol_name='_unrelated')

    def test_duplicate_named_definition_is_ambiguous(self):
        self.rejected('unique defined external', duplicate=True)

    def test_undefined_and_nonexternal_symbols_are_not_declarations(self):
        for kind in (1, 0xE):
            with self.subTest(kind=kind):
                self.rejected('unique defined external', symbol_kind=kind,
                              symbol_address=0 if kind == 1 else None)

    def test_zero_fill_and_executable_sections_are_rejected(self):
        for flags in (1, 0x80000400):
            with self.subTest(flags=flags):
                self.rejected('stored non-executable', section_flags=flags)

    def test_complete_structure_must_fit_before_the_section_end(self):
        self.rejected('Complete 196-byte', section_size=195)
        self.rejected('Complete 196-byte', symbol_address=0x100000400)

    def test_an_inner_symbol_prevents_claiming_the_full_record_extent(self):
        self.rejected('Another defined symbol', inner_symbol=True)

    def test_name_and_version_must_terminate_inside_their_arrays(self):
        self.rejected('version is not NUL-terminated', version=b'2' * 64)
        self.rejected('name is not NUL-terminated', name=b'n' * 64)

    def test_padding_and_ascii_are_checked(self):
        self.rejected('nonzero bytes after', version=b'2.5.4\0hidden')
        self.rejected('not ASCII', version=b'\xff')

    def test_name_and_info_version_are_part_of_the_record_proof(self):
        self.rejected('name mismatch', name='other.kext')
        self.rejected('Unsupported KMOD info_version', info_version=2)

    def test_virtual_load_address_changes_do_not_change_file_mapping(self):
        for base in (0, 0x200000, 0x100000000, 0xFFFFFF8000000000):
            with self.subTest(base=base):
                result = self.inspect(fixture(base=base))
                self.assertEqual(result['record_address'], base + 0x220)
                self.assertEqual(result['record_file_offset'], 0x220)
                self.assertEqual(result['version'], '2.5.4')

    def test_nonzero_symbol_displacement_and_pack_alignment_are_checked(self):
        result = self.inspect(fixture(record_offset=0x234))
        self.assertEqual(result['record_file_offset'], 0x234)
        self.rejected(r'pack\(4\) alignment', record_offset=0x221)

    def test_overlapping_or_unreadable_segment_mappings_are_rejected(self):
        self.rejected('mapping is ambiguous', overlapping=True)
        self.rejected('readable declared segment', readable=False)

    def test_prelink_objects_do_not_satisfy_the_linked_image_requirement(self):
        self.rejected('linked MH_KEXT_BUNDLE', filetype=1)

    def test_relocations_in_name_version_or_header_bytes_are_rejected(self):
        for offset in (8, 12, 16, 80, 136):
            with self.subTest(offset=offset):
                self.rejected('overlaps fixed KMOD', relocs=relocation(0x20 + offset))

    def test_pointer_fixups_are_retained_separately_from_literal_version_proof(self):
        raw = fixture(relocs=relocation(0x20 + 180))
        result = self.inspect(raw)
        self.assertEqual(len(result['pointer_fixups']), 1)
        self.assertEqual(result['pointer_fixups'][0]['address'], 0x100000220 + 180)
        self.assertEqual(result['version'], '2.5.4')
        self.rejected('supported pointer', relocs=relocation(0x20 + 144))
        self.rejected('supported pointer', relocs=relocation(0x20 + 180, width=4))

    def test_unmapped_and_unknown_dyld_fixups_cannot_silently_match(self):
        self.rejected('Unsupported/unmapped', unmapped=True)
        self.rejected('Unsupported/unmapped', dyld=True)

    def test_cli_prints_structural_json_and_keeps_failures_explicit(self):
        self.binary.write_bytes(fixture())
        output = io.StringIO()
        with mock.patch.object(KMOD.sys, 'argv', ['kmod.py', '--binary', str(self.binary)]), redirect_stdout(output):
            self.assertEqual(KMOD.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['version'], '2.5.4')
        self.binary.write_bytes(fixture(version='0.1'))
        with mock.patch.object(KMOD.sys, 'argv', ['kmod.py', '--binary', str(self.binary)]), redirect_stderr(io.StringIO()):
            self.assertEqual(KMOD.main(), 2)


if __name__ == '__main__':
    unittest.main()
