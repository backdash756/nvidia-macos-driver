"""Read-only validation of native Darwin static archives and their object inventory.

Every object must be a thin, little-endian x86_64 MH_OBJECT Mach-O image.
This checks container/header consistency, not symbol-index contents, relocation
semantics, or linkability. The final Darwin link remains a separate requirement.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import sys


_AR_MAGIC = b'!<arch>\n'
_INDEX_NAMES = frozenset(('__.SYMDEF', '__.SYMDEF SORTED',
                          '__.SYMDEF_64', '__.SYMDEF_64 SORTED'))


def _examples(values, limit=6):
    entries = sorted(values.items())
    shown = ', '.join(name + (' x' + str(count) if count != 1 else '')
                      for name, count in entries[:limit])
    if len(entries) > limit:
        shown += ', ...'
    return shown or 'none'


def _validate_object(data, name):
    if len(data) < 32:
        raise ValueError('Archive member ' + name + ': truncated Mach-O 64-bit header')
    magic, cpu, _, filetype, ncmds, sizeofcmds, _, _ = struct.unpack_from('<8I', data)
    if magic != 0xFEEDFACF:
        raise ValueError('Archive member ' + name + ': expected thin little-endian MH_MAGIC_64 Mach-O')
    if cpu != 0x01000007:
        raise ValueError('Archive member ' + name + ': expected x86_64 CPU type, got ' + hex(cpu))
    if filetype != 1:
        raise ValueError('Archive member ' + name + ': expected MH_OBJECT, got file type ' + str(filetype))
    if sizeofcmds > len(data) - 32 or ncmds * 8 > sizeofcmds:
        raise ValueError('Archive member ' + name + ': invalid Mach-O load-command bounds')


def _members(data):
    if not data.startswith(_AR_MAGIC):
        raise ValueError('Expected a native ar archive, not a thin/fat archive or another file format')
    offset = len(_AR_MAGIC)
    while offset < len(data):
        if len(data) - offset < 60:
            raise ValueError('Truncated ar member header at offset ' + str(offset))
        header = data[offset:offset + 60]
        if header[58:60] != b'`\n':
            raise ValueError('Invalid ar member header trailer at offset ' + str(offset))
        try:
            raw_name = header[:16].decode('ascii').strip()
            raw_size = header[48:58].decode('ascii').strip()
        except UnicodeDecodeError as error:
            raise ValueError('Non-ASCII ar member metadata') from error
        if not re.fullmatch(r'[0-9]+', raw_size):
            raise ValueError('Invalid ar member size at offset ' + str(offset))
        size = int(raw_size)
        start, end = offset + 60, offset + 60 + size
        if end > len(data):
            raise ValueError('Truncated ar member payload at offset ' + str(offset))
        payload = data[start:end]
        if raw_name.startswith('#1/'):
            length_text = raw_name[3:]
            if not re.fullmatch(r'[0-9]+', length_text):
                raise ValueError('Invalid BSD extended-name length')
            length = int(length_text)
            if length == 0 or length > size:
                raise ValueError('BSD extended-name length exceeds member payload')
            try:
                name = payload[:length].rstrip(b'\0').decode('utf-8')
            except UnicodeDecodeError as error:
                raise ValueError('Invalid BSD extended member name') from error
            payload = payload[length:]
        else:
            if raw_name.startswith('/'):
                raise ValueError('Unsupported GNU archive metadata or long-name reference: ' + raw_name)
            name = raw_name.rstrip('/')
        if not name or '\0' in name or '/' in name or '\\' in name:
            raise ValueError('Invalid or non-basename ar member name: ' + repr(name))
        yield name, payload
        offset = end + (size & 1)
        if offset > len(data):
            raise ValueError('Missing ar member alignment byte')


def validate_archive(path, objects):
    """Validate one libtool archive against the exact expected object manifest.

    NVIDIA's object rules already flatten filenames to basenames. Duplicate
    expected basenames are rejected rather than allowing ambiguous identities.
    Native libtool output must contain one explicit Apple symbol-index member;
    the index is reported separately and is never counted as an NVIDIA object.
    """
    path = Path(path)
    expected = Counter(Path(str(obj)).name for obj in objects)
    if not expected or any(not name.endswith('.o') for name in expected):
        raise ValueError('Expected a nonempty manifest containing object paths only')
    duplicate_expected = Counter({name: count for name, count in expected.items() if count != 1})
    if duplicate_expected:
        raise ValueError('Duplicate expected archive basenames: ' + _examples(duplicate_expected))
    data = path.read_bytes()
    actual, indexes, records = Counter(), [], []
    for name, payload in _members(data):
        is_index = name in _INDEX_NAMES
        if is_index:
            indexes.append(name)
        else:
            _validate_object(payload, name)
            actual[name] += 1
        records.append({'name': name, 'kind': 'symbol_index' if is_index else 'object',
                        'payload_size': len(payload),
                        'sha256': hashlib.sha256(payload).hexdigest()})
    missing, unexpected = expected - actual, actual - expected
    duplicates = Counter({name: count for name, count in actual.items() if count != 1})
    if missing or unexpected or duplicates:
        raise ValueError(
            'Archive object manifest mismatch: expected ' + str(sum(expected.values()))
            + ', actual ' + str(sum(actual.values()))
            + '; missing (' + str(sum(missing.values())) + '): ' + _examples(missing)
            + '; unexpected (' + str(sum(unexpected.values())) + '): ' + _examples(unexpected)
            + '; duplicate names (' + str(len(duplicates)) + '): ' + _examples(duplicates))
    if len(indexes) != 1:
        raise ValueError('Expected exactly one Apple archive symbol index; found ' + str(len(indexes)))
    return {'archive': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
            'size_bytes': len(data), 'member_count': len(records),
            'object_count': sum(actual.values()), 'index_members': indexes,
            'architecture': 'x86_64', 'object_format': 'MH_MAGIC_64 / MH_OBJECT',
            'members': records,
            'limitation': 'Header/inventory checks do not establish symbol-index correctness or final linkability.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True, help='Existing native Darwin archive, read only')
    parser.add_argument('--objects', type=Path, required=True,
                        help='Existing libtool .filelist or exact object manifest, one path per line')
    args = parser.parse_args()
    try:
        objects = args.objects.read_text().splitlines()
        if any(not line or line != line.strip() for line in objects):
            raise ValueError('Object manifest must contain one unpadded object path per line')
        report = validate_archive(args.archive, objects)
    except (OSError, ValueError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
