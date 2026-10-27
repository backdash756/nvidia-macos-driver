"""Shared parsing of thin-image Mach-O headers printed by Apple's otool.

mach-o/loader.h defines MH_KEXT_BUNDLE as 0xb (11). Apple's verbose otool
renders it as KEXTBUNDLE; accept the explicit constant spellings as well.
"""

import re


MH_KEXT_BUNDLE = 0xB
_KEXT_NAMES = frozenset(('KEXTBUNDLE', 'KEXT_BUNDLE', 'MH_KEXT_BUNDLE'))
_MAGIC_NAMES = frozenset(('MH_MAGIC', 'MH_MAGIC_64', 'MH_CIGAM', 'MH_CIGAM_64'))
_MAGIC_VALUES = frozenset((0xFEEDFACE, 0xFEEDFACF, 0xCEFAEDFE, 0xCFFAEDFE))


def _unsigned_integer(token):
    if re.fullmatch(r'0[xX][0-9a-fA-F]+', token):
        return int(token, 16)
    if re.fullmatch(r'[0-9]+', token):
        return int(token, 10)
    return None


def parse_mach_header(output):
    """Read exactly one header table, using its actual filetype column.

    A filename or other text containing a type name is never evidence of that
    type. Multiple image headers are rejected; callers require a thin x86_64
    image separately. Trailing sizeofcmds/flags columns are not required.
    """
    lines = output.splitlines()
    tables = [(index, line.split()) for index, line in enumerate(lines)
              if 'magic' in line.split() and 'filetype' in line.split()]
    if len(tables) != 1:
        raise ValueError('Expected exactly one Mach-O header table')
    index, fields = tables[0]
    required = ('magic', 'cputype', 'cpusubtype', 'filetype', 'ncmds')
    if (fields[0] != 'magic' or len(fields) != len(set(fields))
            or not all(field in fields for field in required)):
        raise ValueError('Malformed Mach-O header columns')
    if index + 1 >= len(lines):
        raise ValueError('Missing Mach-O header data row')
    values = lines[index + 1].split()
    # Verbose flags can expand to several tokens or be empty. All other
    # declared columns must have a value, including optional caps/sizeofcmds.
    data_columns = [field for field in fields if field != 'flags']
    if not values or len(values) <= max(fields.index(field) for field in data_columns):
        raise ValueError('Truncated Mach-O header data row')
    magic = values[fields.index('magic')]
    if magic not in _MAGIC_NAMES and _unsigned_integer(magic) not in _MAGIC_VALUES:
        raise ValueError('Invalid Mach-O magic')
    ncmds = _unsigned_integer(values[fields.index('ncmds')])
    if ncmds is None or ncmds > 0xFFFFFFFF:
        raise ValueError('Invalid Mach-O load-command count')
    return {'filetype': values[fields.index('filetype')],
            'header': lines[index + 1].strip()}


def is_kext_bundle(header):
    """Recognize MH_KEXT_BUNDLE only in the parsed filetype field."""
    token = header['filetype']
    return token in _KEXT_NAMES or _unsigned_integer(token) == MH_KEXT_BUNDLE
