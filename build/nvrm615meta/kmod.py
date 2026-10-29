#!/usr/bin/env python3
"""Verify linked x86_64 KMOD metadata at its actual Mach-O data symbol.

This reads the exported _kmod_info record, not arbitrary strings in an image.
It neither loads an input nor applies relocations or changes its contents.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools'))
from nvrm_audit.macho import MH_KEXT_BUNDLE, load_macho

# Apple's compatibility definition deliberately uses pack(4), including the
# unaligned 64-bit reference_list/address/start/stop fields. Do not substitute
# a host-native or naturally aligned structure for this on-disk definition.
LAYOUT_SOURCE = {
    'commit': '8d741a5de7ff4191bf97d57b9f54c2f6d4a15585',
    'path': 'osfmk/mach/kmod.h',
    'url': 'https://github.com/apple-oss-distributions/xnu/blob/8d741a5de7ff4191bf97d57b9f54c2f6d4a15585/osfmk/mach/kmod.h',
    'sha256': 'a8fcc1de6620c9484ff96f699d20d37e2b1504615f2acfc547e8f0d14198a2f7',
    'definition': 'kmod_info_64_v1_t; #pragma pack(push, 4); KMOD_INFO_VERSION 1',
}
RECORD_SIZE = 196
NAME_SIZE = 64
OFFSETS = {'next': 0, 'info_version': 8, 'id': 12, 'name': 16, 'version': 80,
           'reference_count': 144, 'reference_list': 148, 'address': 156,
           'size': 164, 'hdr_size': 172, 'start': 180, 'stop': 188}
POINTER_FIELDS = ('next', 'reference_list', 'address', 'start', 'stop')
LIMITATIONS = [
    'This proves only the supported linked static KMOD record and selected version/name fields, not runtime metadata or loader acceptance.',
    'Pointer fields are reported as encoded file values; this tool does not resolve or validate every pointer relocation or entry-point behavior.',
    'The pack(4) version-1 x86_64 layout is checked explicitly. Unsupported or ambiguous symbol, section or fixup metadata is an error.',
    'Matching KMOD and plist versions is not proof of all other component ABI layouts, hardware behavior or release provenance.',
]


def fixed_string(record, name):
    """Require the declared fixed array, including its NUL and zero padding."""
    start = OFFSETS[name]
    field = record[start:start + NAME_SIZE]
    end = field.find(b'\0')
    if end < 0:
        raise ValueError('KMOD ' + name + ' is not NUL-terminated within its 64-byte field')
    if any(field[end:]):
        raise ValueError('KMOD ' + name + ' has nonzero bytes after its terminator')
    try:
        return field[:end].decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('KMOD ' + name + ' is not ASCII') from error


def inspect_kmod(binary, expected_version='2.5.4', expected_name='com.nullmoth.NVRM'):
    """Read a unique _kmod_info declaration from a linked thin x86_64 kext.

    Invalid/unsupported evidence raises ValueError; no fuzzy symbol lookup or
    strings-based fallback is provided. The existing Mach-O reader validates
    container ranges before this version-1 record interpretation is attempted.
    """
    path = Path(binary).expanduser().resolve(strict=True)
    image = load_macho(path)
    if image.filetype != MH_KEXT_BUNDLE:
        raise ValueError('KMOD verification requires a linked MH_KEXT_BUNDLE image')
    matches = [symbol for symbol in image.symbols if symbol.name == '_kmod_info']
    if len(matches) != 1 or not matches[0].is_defined or not matches[0].external:
        raise ValueError('Expected one unique defined external _kmod_info symbol')
    symbol = matches[0]
    section = image.sections[symbol.section_index - 1]
    start, end = symbol.address, symbol.address + RECORD_SIZE
    if start % 4:
        raise ValueError('KMOD record does not satisfy the pack(4) alignment')
    if section.executable or section.zerofill:
        raise ValueError('KMOD record must occupy stored non-executable section data')
    if start < section.address or end > section.address + section.size:
        raise ValueError('Complete 196-byte KMOD record does not fit inside its declared section')
    overlapping = [value for value in image.sections
                   if value.address < end and start < value.address + value.size]
    if len(overlapping) != 1 or overlapping[0].index != section.index:
        raise ValueError('KMOD virtual-address mapping is ambiguous between sections')
    segments = [value for value in image.segments if value.name == section.segment
                and value.address <= start and end <= value.address + value.size]
    if len(segments) != 1 or not segments[0].initprot & 1:
        raise ValueError('KMOD record must belong to one readable declared segment')
    if any(value.is_defined and value.section_index == section.index
           and start < value.address < end for value in image.symbols):
        raise ValueError('Another defined symbol begins inside the required KMOD record extent')
    if image.dynamic_fixups or any(value.address is None for value in image.relocations):
        raise ValueError('Unsupported/unmapped fixups prevent proof of KMOD field bytes')
    pointer_fixups = []
    protected_start, protected_end = start + OFFSETS['info_version'], start + OFFSETS['version'] + NAME_SIZE
    for relocation in image.relocations:
        if relocation.address < end and start < relocation.address + relocation.width:
            if relocation.address < protected_end and protected_start < relocation.address + relocation.width:
                raise ValueError('Relocation overlaps fixed KMOD info/name/version fields')
            # Record only known pointer locations; a write into another scalar
            # field cannot establish the supported static record interpretation.
            if (relocation.address - start not in [OFFSETS[name] for name in POINTER_FIELDS]
                    or relocation.width != 8):
                raise ValueError('KMOD relocation does not cover one supported pointer field')
            pointer_fixups.append(asdict(relocation))
    offset = section.offset + start - section.address
    record = image.data[offset:offset + RECORD_SIZE]
    if len(record) != RECORD_SIZE:
        raise ValueError('KMOD record is truncated in the stored file')
    info_version, identifier = struct.unpack_from('<iI', record, OFFSETS['info_version'])
    if info_version != 1:
        raise ValueError('Unsupported KMOD info_version: ' + str(info_version))
    name, version = fixed_string(record, 'name'), fixed_string(record, 'version')
    if name != expected_name:
        raise ValueError('Embedded KMOD name mismatch: ' + repr(name))
    if version != expected_version:
        raise ValueError('Embedded KMOD version mismatch: ' + repr(version))
    encoded_values = {name: struct.unpack_from('<Q', record, OFFSETS[name])[0]
                      for name in POINTER_FIELDS + ('size', 'hdr_size')}
    encoded_values.update(id=identifier,
                          reference_count=struct.unpack_from('<i', record, OFFSETS['reference_count'])[0])
    # Re-read only to detect concurrent input replacement; neither read writes.
    if hashlib.sha256(path.read_bytes()).hexdigest() != image.sha256:
        raise ValueError('Input changed during KMOD verification')
    return {'schema': 1, 'status': 'verified_static_metadata', 'binary': str(path),
            'binary_sha256': image.sha256, 'record_sha256': hashlib.sha256(record).hexdigest(),
            'name': name, 'version': version, 'info_version': info_version,
            'symbol': asdict(symbol), 'section': asdict(section),
            'record_address': start, 'record_file_offset': offset, 'record_size': RECORD_SIZE,
            'field_offsets': dict(OFFSETS),
            'field_file_offsets': {name: offset + value for name, value in OFFSETS.items()},
            'layout_source': dict(LAYOUT_SOURCE), 'encoded_fields': encoded_values,
            'pointer_fixups': pointer_fixups, 'limitations': list(LIMITATIONS)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--expected-version', default='2.5.4')
    parser.add_argument('--expected-name', default='com.nullmoth.NVRM')
    args = parser.parse_args()
    try:
        result = inspect_kmod(args.binary, args.expected_version, args.expected_name)
    except (OSError, ValueError, KeyError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
