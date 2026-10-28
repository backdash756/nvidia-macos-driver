"""Strict, read-only metadata reader for thin x86_64 Mach-O audit inputs.

This reader exposes metadata and encoded relocation records. It does not prove
that symbol spans are function sizes, resolve runtime bindings, or apply fixups.
Unsupported relocation/binding semantics must remain unknown to the caller.
"""

from collections import defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct
from typing import Optional
import uuid as uuidlib


MH_OBJECT = 1
MH_KEXT_BUNDLE = 11
CPU_TYPE_X86_64 = 0x01000007
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_DYSYMTAB = 0xB
LC_FUNCTION_STARTS = 0x26
LC_DATA_IN_CODE = 0x29
_ZEROFILL_TYPES = frozenset((1, 0xC, 0x12))


@dataclass(frozen=True)
class Section:
    index: int
    segment: str
    name: str
    address: int
    size: int
    offset: int
    flags: int
    relocation_offset: int
    relocation_count: int
    alignment: int

    @property
    def zerofill(self):
        return self.flags & 0xFF in _ZEROFILL_TYPES

    @property
    def executable(self):
        return bool(self.flags & (0x80000000 | 0x400)) or self.name == '__text'


@dataclass(frozen=True)
class Segment:
    name: str
    address: int
    size: int
    file_offset: int
    file_size: int
    maxprot: int
    initprot: int
    flags: int


@dataclass(frozen=True)
class Symbol:
    index: int
    name: str
    address: int
    type: int
    section_index: int
    description: int

    @property
    def is_defined(self):
        return not self.type & 0xE0 and self.type & 0x0E == 0x0E

    @property
    def external(self):
        return bool(self.type & 1)


@dataclass(frozen=True)
class Relocation:
    section_index: Optional[int]
    offset: int
    type: int
    pcrel: bool
    length: int
    external: bool
    symbolnum: Optional[int]
    scattered: bool
    value: Optional[int]
    address: Optional[int]
    source: str

    @property
    def width(self):
        return 1 << self.length


@dataclass
class MachOImage:
    path: str
    data: bytes
    filetype: int
    cpusubtype: int
    flags: int
    segments: tuple
    sections: tuple
    symbols: tuple
    relocations: tuple
    function_starts: tuple
    uuid: Optional[str]
    load_commands: tuple
    data_in_code: tuple
    dynamic_fixups: tuple
    indirect_symbols: tuple

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()

    @property
    def dynamic_relocation_base(self):
        texts = [segment for segment in self.segments if segment.name == '__TEXT']
        if (self.filetype == MH_KEXT_BUNDLE and self.segments
                and self.segments[0].name == '__TEXT' and len(texts) == 1):
            return texts[0].address
        return None

    def section_bytes(self, section):
        if section.zerofill:
            raise ValueError('Zero-fill section has no stored file bytes')
        return self.data[section.offset:section.offset + section.size]

    def sections_at(self, address):
        return [section for section in self.sections
                if section.address <= address < section.address + section.size]

    def section_at(self, address):
        matches = self.sections_at(address)
        return matches[0] if len(matches) == 1 else None

    def text_sections(self):
        return [section for section in self.sections if section.executable and not section.zerofill]

    def symbols_at(self, address, section_index=None):
        return [symbol for symbol in self.symbols if symbol.is_defined
                and symbol.address == address
                and (section_index is None or symbol.section_index == section_index)]

    def symbol_names(self):
        result = defaultdict(list)
        for symbol in self.symbols:
            result[symbol.name].append(symbol)
        return dict(result)

    def function_symbols(self):
        indexes = {section.index for section in self.text_sections()}
        return [symbol for symbol in self.symbols if symbol.is_defined and symbol.name
                and symbol.section_index in indexes]


def _range(data, offset, size, label):
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise ValueError(label + ': file range is out of bounds')
    return data[offset:offset + size]


def _name(raw):
    try:
        return raw.split(b'\0', 1)[0].decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('Non-ASCII Mach-O segment/section name') from error


def _function_starts(payload, base):
    values, index, address = [], 0, base
    terminated = False
    while index < len(payload):
        delta, shift = 0, 0
        while True:
            if index >= len(payload) or shift >= 64:
                raise ValueError('Malformed LC_FUNCTION_STARTS ULEB128')
            byte = payload[index]
            index += 1
            delta |= (byte & 0x7F) << shift
            if delta > 0xFFFFFFFFFFFFFFFF:
                raise ValueError('LC_FUNCTION_STARTS integer overflow')
            if not byte & 0x80:
                break
            shift += 7
        if delta == 0:
            if any(payload[index:]):
                raise ValueError('Nonzero data after LC_FUNCTION_STARTS terminator')
            terminated = True
            break
        address += delta
        if address > 0xFFFFFFFFFFFFFFFF:
            raise ValueError('LC_FUNCTION_STARTS address overflow')
        values.append(address)
    if payload and not terminated:
        raise ValueError('LC_FUNCTION_STARTS lacks its terminator')
    return tuple(values)


def _relocations(data, offset, count, section, symbols, sections, source, dynamic_base=None):
    raw = _range(data, offset, count * 8, 'Relocation table')
    result = []
    for index in range(count):
        first, second = struct.unpack_from('<II', raw, index * 8)
        scattered = bool(first & 0x80000000)
        if scattered:
            relative = first & 0xFFFFFF
            kind, pcrel, length = (first >> 24) & 0xF, bool(first & 0x40000000), (first >> 28) & 3
            external, symbolnum, value = False, None, second
        else:
            relative = first
            symbolnum = second & 0xFFFFFF
            pcrel, length = bool(second & 0x1000000), (second >> 25) & 3
            external, kind, value = bool(second & 0x8000000), (second >> 28) & 0xF, None
            if external and symbolnum >= len(symbols):
                raise ValueError('External relocation refers to an absent symbol')
            if not external and symbolnum > len(sections):
                raise ValueError('Local relocation refers to an absent section')
        if section is not None and relative + (1 << length) > section.size:
            raise ValueError('Section relocation location is out of bounds')
        address = section.address + relative if section else None
        section_index = section.index if section else None
        if section is None and dynamic_base is not None and not scattered:
            # Apple's dyld MachOAnalyzer, fd8d0c4d52320ebf64db34f3cb280310d905c5ae,
            # localRelocBaseAddress/externalRelocBaseAddress: kext bundles use
            # the preferred (__TEXT) load address, unlike ordinary x86_64 images.
            # Only nonnegative offsets with an unambiguous in-section location
            # are mapped here. offset retains the original base-relative value.
            candidate = dynamic_base + relative
            matches = [entry for entry in sections if entry.address <= candidate
                       and candidate + (1 << length) <= entry.address + entry.size]
            if candidate <= 0xFFFFFFFFFFFFFFFF and len(matches) == 1:
                address, section_index = candidate, matches[0].index
        result.append(Relocation(section_index, relative, kind, pcrel, length,
                                 external, symbolnum, scattered, value, address, source))
    return result


def load_macho(path):
    path = Path(path)
    return parse_macho(path.read_bytes(), str(path))


def parse_macho(data, path='<memory>'):
    """Parse metadata without interpreting disassembly or trusting symbol sizes."""
    data = bytes(data)
    header = _range(data, 0, 32, 'Mach-O header')
    magic, cpu, subtype, filetype, ncmds, sizeofcmds, flags, _ = struct.unpack('<8I', header)
    if magic != 0xFEEDFACF or cpu != CPU_TYPE_X86_64 or filetype not in (MH_OBJECT, MH_KEXT_BUNDLE):
        raise ValueError('Expected a thin little-endian x86_64 MH_OBJECT or MH_KEXT_BUNDLE Mach-O')
    _range(data, 32, sizeofcmds, 'Load commands')
    if ncmds * 8 > sizeofcmds:
        raise ValueError('Load-command count exceeds command bytes')
    sections, segments, commands, symtab = [], [], [], None
    start_command, code_command, dysymtab, image_uuid = None, None, None, None
    dynamic_fixups, cursor = [], 32
    for _ in range(ncmds):
        command, size = struct.unpack('<II', _range(data, cursor, 8, 'Load command'))
        if size < 8 or size % 8 or cursor + size > 32 + sizeofcmds:
            raise ValueError('Invalid Mach-O load-command size')
        raw = _range(data, cursor, size, 'Load command')
        info = {'cmd': command, 'cmdsize': size, 'offset': cursor,
                'sha256': hashlib.sha256(raw).hexdigest()}
        if command == LC_SEGMENT_64:
            if size < 72:
                raise ValueError('Truncated LC_SEGMENT_64')
            _, _, name, address, vmsize, fileoff, filesize, maxprot, initprot, nsects, segflags = struct.unpack_from('<II16sQQQQiiII', raw)
            if 72 + nsects * 80 > size:
                raise ValueError('Section count exceeds LC_SEGMENT_64 size')
            _range(data, fileoff, filesize, 'Segment file data')
            if address + vmsize > 0x10000000000000000:
                raise ValueError('Segment virtual-address overflow')
            segments.append(Segment(_name(name), address, vmsize, fileoff, filesize,
                                    maxprot, initprot, segflags))
            for index in range(nsects):
                values = struct.unpack_from('<16s16sQQIIIIIIII', raw, 72 + index * 80)
                secname, segname, addr, secsize, secoff, align, reloff, nreloc, secflags, _, _, _ = values
                section = Section(len(sections) + 1, _name(segname), _name(secname), addr,
                                  secsize, secoff, secflags, reloff, nreloc, align)
                if addr < address or secsize > address + vmsize - addr:
                    raise ValueError('Section virtual range lies outside segment')
                if not section.zerofill:
                    _range(data, secoff, secsize, 'Section file data')
                    if secsize and (secoff < fileoff or secoff + secsize > fileoff + filesize):
                        raise ValueError('Section file range lies outside segment')
                _range(data, reloff, nreloc * 8, 'Section relocation table')
                sections.append(section)
            info.update(name=_name(name), address=address, size=vmsize,
                        file_offset=fileoff, file_size=filesize, maxprot=maxprot,
                        initprot=initprot, flags=segflags, section_count=nsects)
        elif command == LC_SYMTAB:
            if symtab is not None or size != 24:
                raise ValueError('Duplicate or malformed LC_SYMTAB')
            _, _, symoff, nsyms, stroff, strsize = struct.unpack('<6I', raw)
            _range(data, symoff, nsyms * 16, 'Symbol table')
            _range(data, stroff, strsize, 'String table')
            symtab = (symoff, nsyms, stroff, strsize)
            info.update(symbol_offset=symoff, symbol_count=nsyms,
                        string_offset=stroff, string_size=strsize)
        elif command == LC_DYSYMTAB:
            if dysymtab is not None or size != 80:
                raise ValueError('Duplicate or malformed LC_DYSYMTAB')
            dysymtab = struct.unpack('<20I', raw)
            info.update(indirect_symbol_offset=dysymtab[14], indirect_symbol_count=dysymtab[15],
                        external_relocation_offset=dysymtab[16], external_relocation_count=dysymtab[17],
                        local_relocation_offset=dysymtab[18], local_relocation_count=dysymtab[19])
        elif command in (LC_FUNCTION_STARTS, LC_DATA_IN_CODE, 0x1D, 0x80000034, 0x80000033):
            if size != 16:
                raise ValueError('Malformed Mach-O linkedit data command')
            _, _, offset, length = struct.unpack('<4I', raw)
            _range(data, offset, length, 'Linkedit data')
            info.update(data_offset=offset, data_size=length)
            if command == LC_FUNCTION_STARTS:
                if start_command is not None:
                    raise ValueError('Duplicate LC_FUNCTION_STARTS')
                start_command = (offset, length)
            elif command == LC_DATA_IN_CODE:
                if code_command is not None:
                    raise ValueError('Duplicate LC_DATA_IN_CODE')
                code_command = (offset, length)
            elif command == 0x80000034 and length:
                dynamic_fixups.append(dict(info, kind='chained_fixups_unsupported'))
        elif command in (0x22, 0x80000022):
            if size != 48:
                raise ValueError('Malformed LC_DYLD_INFO')
            values = struct.unpack('<12I', raw)
            for index, kind in enumerate(('rebase', 'bind', 'weak_bind', 'lazy_bind', 'exports')):
                offset, length = values[2 + index * 2:4 + index * 2]
                _range(data, offset, length, 'Dyld info')
                info[kind] = {'offset': offset, 'size': length}
                if length and kind != 'exports':
                    dynamic_fixups.append({'kind': kind + '_unsupported', 'offset': offset, 'size': length})
        elif command == 0x1B:
            if size != 24 or image_uuid is not None:
                raise ValueError('Duplicate or malformed LC_UUID')
            image_uuid = str(uuidlib.UUID(bytes=raw[8:24]))
            info['uuid'] = image_uuid
        commands.append(info)
        cursor += size
    if cursor != 32 + sizeofcmds:
        raise ValueError('Load-command size total does not match header')
    if symtab is None:
        raise ValueError('Mach-O audit requires LC_SYMTAB')
    symoff, nsyms, stroff, strsize = symtab
    strings, symbols = data[stroff:stroff + strsize], []
    for index in range(nsyms):
        strx, kind, section_index, desc, address = struct.unpack_from('<IBBHQ', data, symoff + index * 16)
        if strx >= strsize:
            raise ValueError('Symbol string index is out of bounds')
        end = strings.find(b'\0', strx)
        if end < 0:
            raise ValueError('Unterminated Mach-O symbol name')
        try:
            name = strings[strx:end].decode('utf-8')
        except UnicodeDecodeError as error:
            raise ValueError('Symbol name is not valid UTF-8') from error
        symbol = Symbol(index, name, address, kind, section_index, desc)
        if symbol.is_defined:
            if not 1 <= section_index <= len(sections):
                raise ValueError('Defined symbol refers to an absent section')
            section = sections[section_index - 1]
            if not section.address <= address <= section.address + section.size:
                raise ValueError('Defined symbol lies outside its section')
        symbols.append(symbol)
    relocations, indirect = [], []
    for section in sections:
        relocations.extend(_relocations(data, section.relocation_offset,
                                       section.relocation_count, section,
                                       symbols, sections, 'section'))
    if dysymtab is not None:
        texts = [segment for segment in segments if segment.name == '__TEXT']
        dynamic_base = (texts[0].address if filetype == MH_KEXT_BUNDLE and segments
                        and segments[0].name == '__TEXT' and len(texts) == 1 else None)
        for source, offset, count in (('dynamic_external', dysymtab[16], dysymtab[17]),
                                      ('dynamic_local', dysymtab[18], dysymtab[19])):
            relocations.extend(_relocations(data, offset, count, None, symbols,
                                           sections, source, dynamic_base))
        raw = _range(data, dysymtab[14], dysymtab[15] * 4, 'Indirect symbol table')
        indirect = [struct.unpack_from('<I', raw, index * 4)[0] for index in range(dysymtab[15])]
        for value in indirect:
            if not value & 0xC0000000 and value >= len(symbols):
                raise ValueError('Indirect symbol index is out of bounds')
    starts = ()
    if start_command is not None:
        texts = [segment for segment in segments if segment.name == '__TEXT']
        if len(texts) != 1:
            raise ValueError('LC_FUNCTION_STARTS requires one unambiguous __TEXT segment')
        starts = _function_starts(_range(data, *start_command, 'Function-starts data'), texts[0].address)
        for address in starts:
            if not any(section.executable and section.address <= address < section.address + section.size
                       for section in sections):
                raise ValueError('LC_FUNCTION_STARTS points outside executable sections')
    code_entries = []
    if code_command is not None:
        offset, length = code_command
        if length % 8:
            raise ValueError('Malformed LC_DATA_IN_CODE size')
        for index in range(length // 8):
            position, count, kind = struct.unpack_from('<IHH', data, offset + index * 8)
            _range(data, position, count, 'Data-in-code range')
            code_entries.append({'offset': position, 'length': count, 'kind': kind})
    return MachOImage(str(path), data, filetype, subtype, flags, tuple(segments),
                      tuple(sections), tuple(symbols), tuple(relocations), starts,
                      image_uuid, tuple(commands), tuple(code_entries),
                      tuple(dynamic_fixups), tuple(indirect))
