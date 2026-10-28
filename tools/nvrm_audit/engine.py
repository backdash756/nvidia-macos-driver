"""Conservative instruction/reference comparison; uncertainty is never identity."""

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import re
import sys


def digest(value):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class Instruction:
    address: int
    raw: bytes
    mnemonic: str
    operands: str

    @property
    def end(self):
        return self.address + len(self.raw)

    def display(self, base=0):
        return '+0x%x: %s  %s %s' % (self.address - base, self.raw.hex(' '),
                                      self.mnemonic, self.operands)


def parse_disassembly(lines):
    """Read LLVM's default raw-byte AT&T output, retaining section identities."""
    result, section = defaultdict(dict), None
    for line in lines:
        heading = re.fullmatch(r'Disassembly of section (.+):\s*', line.strip())
        if heading:
            section = heading.group(1)
            continue
        match = re.match(r'^\s*([0-9a-fA-F]+):\s*((?:[0-9a-fA-F]{2}\s+)+)(.*)$', line)
        if not match:
            continue
        if section is None:
            raise ValueError('Instruction row without a section heading')
        address = int(match.group(1), 16)
        raw = bytes.fromhex(match.group(2))
        text = match.group(3).split('##', 1)[0].strip()
        pieces = text.split(None, 1)
        if not pieces:
            raise ValueError('Instruction bytes without decoded instruction text')
        instruction = Instruction(address, raw, sys.intern(pieces[0]),
                                  re.sub(r'\s+', ' ', pieces[1].strip()) if len(pieces) > 1 else '')
        if address in result[section]:
            raise ValueError('Duplicate disassembly address in ' + section)
        result[section][address] = instruction
    return dict(result)


def parse_nm(lines):
    rows = Counter()
    for line in lines:
        match = re.fullmatch(r'\s*([0-9a-fA-F]+)\s+[tT]\s+(.+?)\s*', line)
        if match:
            rows[(match.group(2), int(match.group(1), 16))] += 1
    return rows


def branch_field(insn):
    raw = insn.raw
    if len(raw) == 5 and raw[0] in (0xE8, 0xE9):
        return 1, 4
    if len(raw) == 2 and (raw[0] == 0xEB or 0x70 <= raw[0] <= 0x7F or 0xE0 <= raw[0] <= 0xE3):
        return 1, 1
    if len(raw) == 6 and raw[0] == 0x0F and 0x80 <= raw[1] <= 0x8F:
        return 2, 4
    return None


def branch_target(insn):
    field = branch_field(insn)
    if field is None:
        return None
    offset, width = field
    return insn.end + int.from_bytes(insn.raw[offset:offset + width], 'little', signed=True)


def terminal(insn):
    return insn.mnemonic in ('ret', 'retq', 'retl', 'ud2', 'jmp', 'jmpq')


def rip_field(insn):
    """Locate legacy x86 ModRM mod=00/rm=101, never guess the last immediate."""
    raw, cursor = insn.raw, 0
    while cursor < len(raw) and (raw[cursor] in (0xF0, 0xF2, 0xF3, 0x2E, 0x36, 0x3E,
                                                0x26, 0x64, 0x65, 0x66, 0x67)
                                 or 0x40 <= raw[cursor] <= 0x4F):
        if raw[cursor] == 0x67:
            raise ValueError('Address-size override on RIP reference is unsupported')
        if raw[cursor] in (0x64, 0x65):
            raise ValueError('FS/GS-based RIP target is not a static image address')
        cursor += 1
    if cursor >= len(raw) or raw[cursor] in (0xC4, 0xC5, 0x62):
        raise ValueError('Vector/unknown RIP encoding is not proven')
    if raw[cursor] == 0x0F:
        cursor += 2
        if cursor <= len(raw) and raw[cursor - 1] in (0x38, 0x3A):
            cursor += 1
    else:
        cursor += 1
    if cursor >= len(raw) or raw[cursor] & 0xC7 != 5 or cursor + 5 > len(raw):
        raise ValueError('RIP operand does not match a supported ModRM displacement')
    return cursor + 1, 4


def memory_width(mnemonic):
    if mnemonic.startswith('lea'):
        return 0
    if mnemonic in ('call', 'callq', 'jmp', 'jmpq'):
        return 8
    if mnemonic.startswith('movzb'):
        return 1
    if mnemonic.startswith('movzw'):
        return 2
    if mnemonic == 'movslq':
        return 4
    if mnemonic in ('movss', 'comiss', 'ucomiss'):
        return 4
    if mnemonic in ('movsd', 'comisd', 'ucomisd'):
        return 8
    bases = ('mov', 'cmp', 'test', 'add', 'sub', 'and', 'or', 'xor', 'adc', 'sbb',
             'inc', 'dec', 'neg', 'not', 'imul', 'mul', 'idiv', 'div', 'xchg', 'cmpxchg')
    if mnemonic and mnemonic[-1] in 'bwlq' and mnemonic[:-1] in bases:
        return {'b': 1, 'w': 2, 'l': 4, 'q': 8}[mnemonic[-1]]
    raise ValueError('Memory access width is not proven for ' + mnemonic)


def binding_signature(symbol):
    """Keep weak/reference/visibility flags; never equate names alone.

    Undefined common symbols carry a storage size in n_value. Defined target
    addresses are handled by the symbol/range proof rather than discarded here.
    Indirect/absolute symbol semantics are outside the supported resolver.
    """
    if symbol.type & 0x0E not in (0, 0x0E):
        raise ValueError('indirect/absolute relocation symbol semantics are unsupported')
    common_size = symbol.address if symbol.type & 0x0E == 0 and symbol.address else None
    return (symbol.type & 0x1F, symbol.description, common_size)


class Context:
    def __init__(self, image, disassembly, nm):
        self.image, self.disassembly, self.nm = image, disassembly, nm
        self.functions = defaultdict(list)
        self.names = image.symbol_names()
        self.address_symbols = defaultdict(list)
        self.section_starts = defaultdict(list)
        for symbol in image.symbols:
            if symbol.is_defined:
                self.address_symbols[(symbol.section_index, symbol.address)].append(symbol)
                self.section_starts[symbol.section_index].append(symbol.address)
        for symbol in image.function_symbols():
            self.functions[symbol.name].append(symbol)
        for section in image.sections:
            self.section_starts[section.index] = sorted(set(self.section_starts[section.index]
                + [a for a in image.function_starts if section.address <= a < section.address + section.size]
                + [section.address + section.size]))
        self.function_boundaries = {section.index: sorted(
            [a for a in image.function_starts if section.address <= a < section.address + section.size]
            + [section.address + section.size]) for section in image.text_sections()}
        self.relocs = defaultdict(list)
        self.unknown_relocations = []
        for relocation in image.relocations:
            if relocation.address is None:
                self.unknown_relocations.append(relocation)
            else:
                self.relocs[relocation.section_index].append(relocation)
        self.reloc_starts = {}
        for section, values in self.relocs.items():
            values.sort(key=lambda rel: rel.address)
            self.reloc_starts[section] = [rel.address for rel in values]
        self.decoded = {}
        for key, values in disassembly.items():
            addresses = sorted(values)
            self.decoded[key] = (addresses, [values[a] for a in addresses])
        targets = {target for values in disassembly.values() for insn in values.values()
                   for target in (branch_target(insn),) if target is not None}
        self.padding_reference_uncertain = bool(image.dynamic_fixups or self.unknown_relocations)
        # Relocation *targets*, including address-taken entries in data, can
        # point into a nominal NOP suffix. Checking only relocation sites is
        # insufficient. Collect static references conservatively; unresolved
        # in-image relocation semantics disable padding removal.
        for relocation in image.relocations:
            if relocation.address is None or relocation.scattered:
                self.padding_reference_uncertain = True
                continue
            section = image.section_at(relocation.address)
            if section is None or section.zerofill:
                self.padding_reference_uncertain = True
                continue
            offset = section.offset + relocation.address - section.address
            encoded = image.data[offset:offset + relocation.width]
            if relocation.type == 0 and not relocation.pcrel and relocation.width == 8:
                value = int.from_bytes(encoded, 'little')
            elif relocation.type in (1, 2, 3, 4) and relocation.pcrel and relocation.width == 4:
                value = int.from_bytes(encoded, 'little', signed=True)
            else:
                self.padding_reference_uncertain = True
                continue
            if relocation.external:
                referent = image.symbols[relocation.symbolnum]
                definitions = [sym for sym in self.names.get(referent.name, []) if sym.is_defined]
                for definition in definitions:
                    targets.add((definition.address + value) & 0xFFFFFFFFFFFFFFFF)
                # An undefined import is not a statically identified entry in
                # this image. Runtime symbol-provider behavior is out of scope.
            elif relocation.type == 0:
                targets.add(value)
                if relocation.symbolnum:
                    targets.add(image.sections[relocation.symbolnum - 1].address + value)
            else:
                targets.add(relocation.address + relocation.width + value)
        for values in disassembly.values():
            for insn in values.values():
                if '(%rip)' not in insn.operands:
                    continue
                try:
                    field, width = rip_field(insn)
                    targets.add(insn.end + int.from_bytes(insn.raw[field:field + width], 'little', signed=True))
                except ValueError:
                    self.padding_reference_uncertain = True
        self.branch_targets = sorted(targets)
        self.data_cache = {}

    def relocations(self, section, start, end):
        values = self.relocs.get(section.index, [])
        offsets = self.reloc_starts.get(section.index, [])
        lo, hi = bisect_left(offsets, start - 8), bisect_left(offsets, end)
        return [rel for rel in values[lo:hi] if rel.address + rel.width > start]

    def span_end(self, symbol):
        section = self.image.sections[symbol.section_index - 1]
        starts = (self.function_boundaries[symbol.section_index] if section.executable and self.image.function_starts
                  else self.section_starts[symbol.section_index])
        index = bisect_right(starts, symbol.address)
        return starts[index] if index < len(starts) else self.image.sections[symbol.section_index - 1].address + self.image.sections[symbol.section_index - 1].size

    def function(self, name):
        matches = self.functions.get(name, [])
        if not matches:
            raise ValueError('missing function symbol')
        if len(matches) != 1:
            raise ValueError('duplicate function symbol name')
        symbol = matches[0]
        if len(self.address_symbols[(symbol.section_index, symbol.address)]) != 1:
            raise ValueError('aliased entry address is ambiguous')
        if self.nm[(name, symbol.address)] != 1:
            raise ValueError('nm and Mach-O symbol identity disagree')
        if self.image.function_starts and symbol.address not in self.image.function_starts:
            raise ValueError('symbol is not a recorded LC_FUNCTION_STARTS entry')
        if self.image.dynamic_fixups or self.unknown_relocations:
            raise ValueError('unmapped relocation or unsupported dyld/chained binding metadata')
        section = self.image.sections[symbol.section_index - 1]
        end = self.span_end(symbol)
        file_start = section.offset + symbol.address - section.address
        file_end = section.offset + end - section.address
        if any(entry['offset'] < file_end and entry['offset'] + entry['length'] > file_start
               for entry in self.image.data_in_code):
            raise ValueError('span intersects LC_DATA_IN_CODE')
        key = section.segment + ',' + section.name
        addresses, decoded = self.decoded.get(key, ([], []))
        body = decoded[bisect_left(addresses, symbol.address):bisect_left(addresses, end)]
        position = symbol.address
        for insn in body:
            offset = section.offset + insn.address - section.address
            if insn.address != position or insn.end > end or insn.raw != self.image.data[offset:offset + len(insn.raw)]:
                raise ValueError('disassembly does not cover the exact stored instruction bytes')
            if insn.mnemonic in ('<unknown>', '.byte', '(bad)'):
                raise ValueError('undecodable instruction in symbol span')
            position = insn.end
        if not body or position != end:
            raise ValueError('incomplete disassembly of symbol-bounded span')
        padding = []
        while body and body[-1].mnemonic in ('nop', 'nopl', 'nopw', 'nopq'):
            padding.insert(0, body.pop())
        if padding:
            if (self.padding_reference_uncertain or not body or not terminal(body[-1])
                    or (bisect_left(self.branch_targets, padding[0].address) < len(self.branch_targets)
                        and self.branch_targets[bisect_left(self.branch_targets, padding[0].address)] < padding[-1].end)
                    or self.relocations(section, padding[0].address, padding[-1].end)
                    or any(insn.mnemonic.startswith('jmp') and '*' in insn.operands for insn in body)):
                body += padding
                padding = []
        if not body or not terminal(body[-1]):
            raise ValueError('span end is not a proven terminal; possible data/zero padding is retained')
        if any(insn.mnemonic.startswith('jmp') and '*' in insn.operands for insn in body):
            raise ValueError('indirect jump prevents a complete function-extent proof')
        return symbol, section, body, padding, end

    def named_code(self, target):
        section = self.image.section_at(target)
        if section is None or not section.executable:
            return None
        symbols = self.address_symbols.get((section.index, target), [])
        if len(symbols) != 1 or len(self.names[symbols[0].name]) != 1:
            return None
        symbol = symbols[0]
        if symbol.name not in self.functions or (self.image.function_starts and target not in self.image.function_starts):
            return None
        return ('code_symbol', symbol.name, 0, binding_signature(symbol))

    def data_signature(self, target, width):
        """Require a uniquely named, byte-identical bounded region; no anonymous fallback."""
        section = self.image.section_at(target)
        if section is None or section.executable:
            raise ValueError('RIP target is not a uniquely bounded data region')
        starts = self.section_starts[section.index]
        index = bisect_right(starts, target) - 1
        if index < 0:
            raise ValueError('anonymous RIP data target has no proven symbol identity')
        start = starts[index]
        symbols = self.address_symbols.get((section.index, start), [])
        if len(symbols) != 1 or len(self.names[symbols[0].name]) != 1:
            raise ValueError('RIP data target has no unique symbol identity')
        symbol = symbols[0]
        end = self.span_end(symbol)
        if target + width > end:
            raise ValueError('RIP memory access crosses its proven data-symbol span')
        key = (section.index, start, end)
        if key not in self.data_cache:
            if self.relocations(section, start, end):
                raise ValueError('data region contains fixups requiring separate proof')
            data_hash = 'zero-fill' if section.zerofill else digest(self.image.data[section.offset + start - section.address:section.offset + end - section.address])
            self.data_cache[key] = (section.segment, section.name, section.flags, end - start, data_hash, binding_signature(symbol))
        return ('data_symbol', symbol.name, target - start, self.data_cache[key])

    def relocation_target(self, relocation, addend, role):
        if relocation.scattered or not relocation.external or relocation.symbolnum is None:
            raise ValueError('local/scattered relocation semantics are not normalized')
        symbol = self.image.symbols[relocation.symbolnum]
        if not symbol.name or len(self.names[symbol.name]) != 1:
            raise ValueError('relocation symbol identity is ambiguous')
        if role == 'branch' and relocation.type == 2 and relocation.pcrel and relocation.width == 4:
            return ('code_symbol', symbol.name, addend, binding_signature(symbol))
        # External GOT references keep the relocation kind and symbol/addend;
        # unlike raw placeholder bytes, different imports remain different.
        if role == 'rip' and relocation.type in (3, 4) and relocation.pcrel and relocation.width == 4:
            return ('got_relocation', relocation.type, symbol.name, addend, binding_signature(symbol))
        raise ValueError('unsupported relocation type/role; no equivalence assumed')

    def canonical(self, symbol, section, body):
        signatures, references = [], []
        start, end = symbol.address, body[-1].end
        boundaries = {insn.address for insn in body}
        for insn in body:
            relocs = self.relocations(section, insn.address, insn.end)
            raw, operands = bytearray(insn.raw), insn.operands
            field, role, target = None, None, None
            encoded_branch = branch_field(insn)
            if encoded_branch is not None:
                field, role = encoded_branch, 'branch'
            elif '(%eip)' in operands:
                raise ValueError('EIP-relative address-size semantics are unsupported')
            elif '(%rip)' in operands:
                field, role = rip_field(insn), 'rip'
            elif relocs:
                raise ValueError('relocation overlaps an unsupported instruction encoding')
            elif insn.mnemonic.startswith(('call', 'j', 'loop', 'xbegin')) and '*' not in operands:
                raise ValueError('unsupported direct control-transfer encoding')
            if field is not None:
                offset, width = field
                displacement = int.from_bytes(raw[offset:offset + width], 'little', signed=True)
                if relocs:
                    if len(relocs) != 1 or relocs[0].address != insn.address + offset or relocs[0].width != width:
                        raise ValueError('relocation does not match the encoded address field exactly')
                    target = self.relocation_target(relocs[0], displacement, role)
                else:
                    absolute = insn.end + displacement
                    if role == 'branch':
                        if start <= absolute < end:
                            if absolute not in boundaries:
                                raise ValueError('branch target is not an instruction boundary')
                            target = ('local_branch', absolute - start)
                        else:
                            target = self.named_code(absolute)
                            if target is None:
                                raise ValueError('direct branch has no exact unique target symbol')
                    else:
                        target = self.named_code(absolute)
                        if target is None:
                            target = self.data_signature(absolute, memory_width(insn.mnemonic))
                raw[offset:offset + width] = b'\0' * width
                if role == 'branch':
                    operands = '@ADDRESS'
                else:
                    pattern = r'(?:-?(?:0x[0-9a-fA-F]+|[0-9]+))?\(%rip\)'
                    matches = list(re.finditer(pattern, operands))
                    if len(matches) != 1:
                        raise ValueError('ambiguous RIP operand rendering')
                    rendered = matches[0].group(0).split('(')[0]
                    if (int(rendered, 0) if rendered else 0) != displacement:
                        raise ValueError('RIP rendering and encoded displacement disagree')
                    operands = operands[:matches[0].start()] + '@ADDRESS(%rip)' + operands[matches[0].end():]
                references.append({'offset': insn.address - start, 'role': role,
                                   'target': target, 'relocated': bool(relocs)})
            signatures.append((raw.hex(), insn.mnemonic, operands, target))
        return signatures, references


def _evidence(symbol, body, padding, end, signatures, references):
    normalized = json.dumps(signatures, separators=(',', ':')).encode()
    return {'address': symbol.address, 'symbol_span_end': end,
            'boundary_basis': 'next section symbol / LC_FUNCTION_STARTS / section end; not a function-size claim',
            'body_end': body[-1].end, 'instruction_count': len(body),
            'body_bytes_sha256': digest(b''.join(insn.raw for insn in body)),
            'signature_sha256': digest(normalized),
            'removed_nop_padding_bytes': sum(len(insn.raw) for insn in padding),
            'references': references}


def compare_contexts(reference, candidate, selected=None):
    names = sorted(set(selected) if selected else set(reference.functions) | set(candidate.functions))
    records = []
    for name in names:
        record = {'symbol': name, 'status': 'not_comparable',
                  'identities': {label: [{'address': sym.address, 'section_index': sym.section_index,
                                           'type': sym.type, 'description': sym.description}
                                          for sym in ctx.functions.get(name, [])]
                                 for label, ctx in (('reference', reference), ('candidate', candidate))}}
        try:
            rs, rsection, rb, rp, re = reference.function(name)
            cs, csection, cb, cp, ce = candidate.function(name)
            record['span_examples'] = {
                'reference': [insn.display(rs.address) for insn in rb[:6]],
                'candidate': [insn.display(cs.address) for insn in cb[:6]]}
            if binding_signature(rs) != binding_signature(cs):
                raise ValueError('entry symbol binding flags differ')
            rsign, rrefs = reference.canonical(rs, rsection, rb)
            csign, crefs = candidate.canonical(cs, csection, cb)
            record['reference'] = _evidence(rs, rb, rp, re, rsign, rrefs)
            record['candidate'] = _evidence(cs, cb, cp, ce, csign, crefs)
            address_differences = []
            for index, (left, right) in enumerate(zip(rb, cb)):
                if (left.raw != right.raw or left.operands != right.operands) and index < len(rsign) and rsign[index] == csign[index]:
                    address_differences.append(index)
            if rsign == csign:
                record.update(status='identical', identity='symbolic' if address_differences else 'exact',
                              reason='strict instructions and proven address-reference identities match')
                record['layout'] = {'entry_address_changed': rs.address != cs.address,
                                    'nop_padding_changed': b''.join(i.raw for i in rp) != b''.join(i.raw for i in cp),
                                    'address_dependent_instruction_indices': address_differences}
            else:
                record.update(status='different', reason='instruction encoding and/or proven reference evidence differs')
                mismatch = [index for index in range(max(len(rsign), len(csign)))
                            if index >= len(rsign) or index >= len(csign) or rsign[index] != csign[index]]
                record['difference_categories'] = []
                if [(ref['role'], ref['target']) for ref in rrefs] != [(ref['role'], ref['target']) for ref in crefs]:
                    record['difference_categories'].append('address_reference_difference')
                    if any(ref['target'] and ref['target'][0] == 'data_symbol' for ref in rrefs + crefs):
                        record['difference_categories'].append('referenced_data_evidence_difference')
                if [sig[:3] for sig in rsign] != [sig[:3] for sig in csign]:
                    record['difference_categories'].append('instruction_or_encoding_difference')
                record['examples'] = [{'index': index,
                                       'reference': rb[index].display(rs.address) if index < len(rb) else '<no instruction>',
                                       'candidate': cb[index].display(cs.address) if index < len(cb) else '<no instruction>'}
                                      for index in mismatch[:8]]
        except (ValueError, KeyError) as error:
            record['reason'] = str(error)
        records.append(record)
    counts = Counter(record['status'] for record in records)
    return {'counts': {'examined': len(records), 'identical': counts['identical'],
                       'different': counts['different'], 'not_comparable': counts['not_comparable']},
            'functions': records,
            'address_dependent_summary': {
                'different_reference_functions': sum('address_reference_difference' in record.get('difference_categories', []) for record in records),
                'nop_padding_difference_functions': sum(record.get('layout', {}).get('nop_padding_changed', False) for record in records),
                'symbolically_identical_functions': sum(record.get('identity') == 'symbolic' for record in records),
                'layout_or_nop_padding_only': sum(record['status'] == 'identical' and any(record.get('layout', {}).values()) for record in records),
                'unknown_reasons': dict(Counter(record['reason'] for record in records if record['status'] == 'not_comparable'))}}
