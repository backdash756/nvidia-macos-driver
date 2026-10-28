#!/usr/bin/env python3
"""Exercise Mach-O metadata parsing with synthetic containers, not code parity."""

from dataclasses import replace
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nvrm_audit import macho


def fixed(text):
    return text.encode().ljust(16, b'\0')


def uleb(value):
    result = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def relocation(offset=4, symbol=5, kind=2, pcrel=True, length=2, external=True):
    return struct.pack('<II', offset, symbol | (int(pcrel) << 24) | (length << 25)
                       | (int(external) << 27) | (kind << 28))


def fixture(filetype=11, relocs=b'', external_relocs=b'', local_relocs=b'',
            starts=None, data_in_code=False, dyld=False, chained=False,
            indirect=None, segment_name='__TEXT', uuid=False):
    """Create metadata fixtures only; these bytes have never been linked/loaded."""
    data = bytearray(0x218)
    data[0x200:0x210] = b'\x90\xc3' + b'\x90' * 14
    data[0x210:0x218] = b'abcdefgh'

    def append(payload):
        offset = len(data)
        data.extend(payload)
        return offset

    reloff = append(relocs) if relocs else 0
    names = ('_first', '_alias', '_duplicate', '_duplicate', '_constant', '_external')
    strings, indexes = bytearray(b'\0'), []
    for name in names:
        indexes.append(len(strings))
        strings.extend(name.encode() + b'\0')
    entries = []
    for index, (address, section) in enumerate(((0x1200, 1), (0x1200, 1),
                                                (0x1208, 1), (0x120C, 1),
                                                (0x1210, 2), (0, 0))):
        entries.append(struct.pack('<IBBHQ', indexes[index], 0xF if section else 1,
                                   section, 0, address))
    symoff = append(b''.join(entries))
    stroff = append(strings)
    extra_commands = []
    if starts is not None:
        startoff = append(starts)
        extra_commands.append(struct.pack('<4I', 0x26, 16, startoff, len(starts)))
    if data_in_code:
        codeoff = append(struct.pack('<IHH', 0x204, 2, 1))
        extra_commands.append(struct.pack('<4I', 0x29, 16, codeoff, 8))
    if external_relocs or local_relocs or indirect is not None:
        ext_off = append(external_relocs) if external_relocs else 0
        local_off = append(local_relocs) if local_relocs else 0
        indirect_off = append(b''.join(struct.pack('<I', value) for value in indirect)) if indirect else 0
        values = [0xB, 80] + [0] * 18
        values[14:20] = [indirect_off, len(indirect or []), ext_off,
                         len(external_relocs) // 8, local_off, len(local_relocs) // 8]
        extra_commands.append(struct.pack('<20I', *values))
    if dyld:
        bindoff = append(b'\0')
        extra_commands.append(struct.pack('<12I', 0x80000022, 48, 0, 0,
                                           bindoff, 1, 0, 0, 0, 0, 0, 0))
    if chained:
        fixoff = append(b'unsupported-fixups')
        extra_commands.append(struct.pack('<4I', 0x80000034, 16, fixoff, 18))
    if uuid:
        extra_commands.append(struct.pack('<II', 0x1B, 24) + bytes(range(16)))
    segment = struct.pack('<II16sQQQQiiII', 0x19, 232, fixed(segment_name),
                          0x1000, 0x2000, 0, len(data), 7, 5, 2, 0)
    text = struct.pack('<16s16sQQIIIIIIII', fixed('__text'), fixed('__TEXT'),
                       0x1200, 16, 0x200, 4, reloff, len(relocs) // 8,
                       0x80000400, 0, 0, 0)
    const = struct.pack('<16s16sQQIIIIIIII', fixed('__const'), fixed('__TEXT'),
                        0x1210, 8, 0x210, 3, 0, 0, 0, 0, 0, 0)
    symtab = struct.pack('<6I', 2, 24, symoff, 6, stroff, len(strings))
    commands = [segment + text + const, symtab] + extra_commands
    raw_commands = b''.join(commands)
    assert 32 + len(raw_commands) <= 0x200
    data[:32] = struct.pack('<8I', 0xFEEDFACF, 0x01000007, 3, filetype,
                            len(commands), len(raw_commands), 0, 0)
    data[32:32 + len(raw_commands)] = raw_commands
    return bytes(data), {'symoff': symoff, 'stroff': stroff, 'reloff': reloff}


class MachOReaderTests(unittest.TestCase):
    def test_sections_symbols_aliases_and_bytes_preserve_identity(self):
        raw, _ = fixture()
        image = macho.parse_macho(raw)
        self.assertEqual(image.filetype, macho.MH_KEXT_BUNDLE)
        self.assertEqual(image.sections[0].index, 1)
        self.assertEqual(image.sections[0].segment, '__TEXT')
        self.assertEqual(image.section_bytes(image.sections[0]), b'\x90\xc3' + b'\x90' * 14)
        self.assertEqual([s.name for s in image.symbols_at(0x1200)], ['_first', '_alias'])
        self.assertEqual(len(image.symbol_names()['_duplicate']), 2)
        self.assertEqual(len(image.function_symbols()), 4)
        self.assertEqual(image.section_at(0x1210).name, '__const')
        self.assertIsNone(image.section_at(0x9999))
        self.assertFalse(image.symbols[-1].is_defined)
        self.assertEqual(len(image.sha256), 64)

    def test_object_and_file_loading_are_read_only(self):
        raw, _ = fixture(filetype=macho.MH_OBJECT)
        with tempfile.TemporaryDirectory(prefix='nvrm-macho-reader-') as temporary:
            path = Path(temporary) / 'probe.o'
            path.write_bytes(raw)
            image = macho.load_macho(path)
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(image.path, str(path))
        self.assertEqual(image.filetype, macho.MH_OBJECT)
        self.assertIsNone(image.dynamic_relocation_base)

    def test_wrong_architecture_magic_type_and_truncated_headers_fail(self):
        raw, _ = fixture()
        for field, value in ((0, 0xCAFEBABE), (1, 0x0100000C), (3, 2)):
            changed = bytearray(raw)
            struct.pack_into('<I', changed, field * 4, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                macho.parse_macho(changed)
        with self.assertRaises(ValueError):
            macho.parse_macho(raw[:31])

    def test_load_command_count_size_and_total_are_checked(self):
        raw, _ = fixture()
        mutations = ((16, 999), (36, 0), (36, 10), (20, 8))
        for offset, value in mutations:
            changed = bytearray(raw)
            struct.pack_into('<I', changed, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                macho.parse_macho(changed)

    def test_section_and_symbol_ranges_are_checked(self):
        raw, layout = fixture()
        changes = []
        data = bytearray(raw)
        struct.pack_into('<I', data, 32 + 72 + 48, len(raw) + 1)
        changes.append(data)
        data = bytearray(raw)
        struct.pack_into('<I', data, layout['symoff'], 0xFFFFFF)
        changes.append(data)
        data = bytearray(raw)
        data[layout['symoff'] + 5] = 20
        changes.append(data)
        data = bytearray(raw)
        struct.pack_into('<Q', data, layout['symoff'] + 8, 0x5000)
        changes.append(data)
        for index, data in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(ValueError):
                macho.parse_macho(data)

    def test_unterminated_and_invalid_symbol_strings_fail(self):
        raw, layout = fixture()
        changed = bytearray(raw)
        changed[-1] = 65
        with self.assertRaises(ValueError):
            macho.parse_macho(changed)
        changed = bytearray(raw)
        changed[layout['stroff'] + 1] = 0xFF
        with self.assertRaises(ValueError):
            macho.parse_macho(changed)

    def test_function_start_deltas_are_not_symbol_span_sizes(self):
        starts = uleb(0x200) + uleb(8) + uleb(4) + b'\0\0'
        image = macho.parse_macho(fixture(starts=starts)[0])
        self.assertEqual(image.function_starts, (0x1200, 0x1208, 0x120C))
        self.assertFalse(hasattr(image.symbols[0], 'size'))

    def test_bad_function_start_streams_or_noncode_locations_fail(self):
        for payload in (b'\x80', uleb(0x200), uleb(0x200) + b'\0\x01',
                        b'\xFF' * 10 + b'\x01\0', uleb(0x210) + b'\0'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                macho.parse_macho(fixture(starts=payload)[0])
        with self.assertRaises(ValueError):
            macho.parse_macho(fixture(starts=uleb(0x200) + b'\0', segment_name='')[0])

    def test_external_section_relocation_fields_are_exposed(self):
        image = macho.parse_macho(fixture(relocs=relocation())[0])
        record = image.relocations[0]
        self.assertEqual((record.section_index, record.offset, record.address), (1, 4, 0x1204))
        self.assertEqual((record.type, record.pcrel, record.length, record.width), (2, True, 2, 4))
        self.assertTrue(record.external)
        self.assertEqual(image.symbols[record.symbolnum].name, '_external')

    def test_unsupported_pair_and_scattered_relocations_are_not_hidden(self):
        pair = relocation(kind=5) + relocation(kind=0)
        image = macho.parse_macho(fixture(relocs=pair)[0])
        self.assertEqual([entry.type for entry in image.relocations], [5, 0])
        scattered = struct.pack('<II', 0x80000000 | (2 << 28) | (1 << 24) | 4, 0x1210)
        image = macho.parse_macho(fixture(relocs=scattered)[0])
        self.assertTrue(image.relocations[0].scattered)
        self.assertIsNone(image.relocations[0].symbolnum)
        self.assertEqual(image.relocations[0].value, 0x1210)

    def test_relocation_ranges_and_symbol_references_are_checked(self):
        for record in (relocation(offset=15), relocation(symbol=99),
                       relocation(symbol=99, external=False)):
            with self.subTest(record=record), self.assertRaises(ValueError):
                macho.parse_macho(fixture(relocs=record)[0])

    def test_kext_dynamic_relocations_use_preferred_text_base(self):
        image = macho.parse_macho(fixture(external_relocs=relocation(offset=0x204),
                                          local_relocs=relocation(offset=0x208, external=False,
                                                                  symbol=2, kind=0))[0])
        self.assertEqual(image.dynamic_relocation_base, 0x1000)
        self.assertEqual([record.source for record in image.relocations],
                         ['dynamic_external', 'dynamic_local'])
        self.assertEqual([record.address for record in image.relocations], [0x1204, 0x1208])
        self.assertEqual([record.section_index for record in image.relocations], [1, 1])
        self.assertEqual(image.relocations[0].offset, 0x204)

    def test_unknown_dynamic_bases_or_locations_stay_unmapped(self):
        for options in ({'filetype': 1}, {'segment_name': ''}, {}):
            offset = 0x900 if not options else 0x204
            image = macho.parse_macho(fixture(external_relocs=relocation(offset=offset), **options)[0])
            self.assertIsNone(image.relocations[0].address)
        image = macho.parse_macho(fixture(external_relocs=struct.pack('<II', 0x80000004, 0x1210))[0])
        self.assertTrue(image.relocations[0].scattered)
        self.assertIsNone(image.relocations[0].address)

    def test_indirect_symbol_and_runtime_binding_metadata_remain_visible(self):
        image = macho.parse_macho(fixture(indirect=[5, 0x80000000], dyld=True, chained=True)[0])
        self.assertEqual(image.indirect_symbols, (5, 0x80000000))
        self.assertEqual([entry['kind'] for entry in image.dynamic_fixups],
                         ['bind_unsupported', 'chained_fixups_unsupported'])
        with self.assertRaises(ValueError):
            macho.parse_macho(fixture(indirect=[99])[0])

    def test_data_in_code_and_uuid_are_exposed(self):
        image = macho.parse_macho(fixture(data_in_code=True, uuid=True)[0])
        self.assertEqual(image.data_in_code, ({'offset': 0x204, 'length': 2, 'kind': 1},))
        self.assertEqual(image.uuid, '00010203-0405-0607-0809-0a0b0c0d0e0f')

    def test_ambiguous_section_lookup_does_not_choose_one(self):
        image = macho.parse_macho(fixture()[0])
        image.sections = image.sections + (replace(image.sections[0], index=3),)
        self.assertIsNone(image.section_at(0x1200))
        self.assertEqual(len(image.sections_at(0x1200)), 2)


if __name__ == '__main__':
    unittest.main()
