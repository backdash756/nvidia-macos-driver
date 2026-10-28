#!/usr/bin/env python3
"""Audit guards and conservative engine regressions using synthetic models only."""

from collections import Counter
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from nvrm_audit.engine import Context, Instruction, binding_signature, compare_contexts, parse_disassembly
from nvrm_audit.macho import MachOImage, Relocation, Section, Symbol

SPEC = importlib.util.spec_from_file_location('nvrm_code_audit_tested', TOOLS / 'nvrm_code_audit.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def model(raw=b'\x31\xc0\xc3', extra_symbols=(), starts=(), relocs=(), decoded=None):
    section = Section(1, '__TEXT', '__text', 0x100, len(raw), 0, 0x80000400, 0, 0, 0)
    symbols = (Symbol(0, '_field', 0x100, 0xF, 1, 0),) + tuple(extra_symbols)
    image = MachOImage('<synthetic-model>', raw, 11, 3, 0, (), (section,), symbols,
                       tuple(relocs), tuple(starts), None, (), (), (), ())
    if decoded is None:
        decoded = {0x100: Instruction(0x100, raw[:2], 'xorl', '%eax, %eax'),
                   0x102: Instruction(0x102, raw[2:], 'retq', '')}
    nm = Counter((symbol.name, symbol.address) for symbol in symbols)
    return Context(image, {'__TEXT,__text': decoded}, nm)


class EngineSafetyTests(unittest.TestCase):
    def test_missing_symbol_is_in_the_denominator(self):
        reference, candidate = model(), model()
        candidate.functions.clear()
        report = compare_contexts(reference, candidate)
        self.assertEqual(report['counts'], {'examined': 1, 'identical': 0,
                                            'different': 0, 'not_comparable': 1})

    def test_duplicate_names_are_uncertain(self):
        context = model(extra_symbols=(Symbol(1, '_field', 0x102, 0xE, 1, 0),))
        report = compare_contexts(context, context)
        self.assertEqual(report['functions'][0]['status'], 'not_comparable')
        self.assertIn('duplicate', report['functions'][0]['reason'])

    def test_nm_disagreement_cannot_be_counted_identical(self):
        reference, candidate = model(), model()
        candidate.nm.clear()
        self.assertEqual(compare_contexts(reference, candidate)['counts']['not_comparable'], 1)

    def test_decoded_bytes_must_match_input_bytes(self):
        reference, candidate = model(), model()
        candidate.decoded['__TEXT,__text'][1][0] = Instruction(0x100, b'\x29\xc0', 'subl', '%eax, %eax')
        self.assertEqual(compare_contexts(reference, candidate)['counts']['not_comparable'], 1)

    def test_lc_function_starts_prevent_truncation_at_an_internal_label(self):
        def context(opcode, mnemonic):
            raw = b'\xeb\x00' + opcode + b'\xc3'
            instructions = {0x100: Instruction(0x100, b'\xeb\x00', 'jmp', '0x102 <_local>'),
                            0x102: Instruction(0x102, opcode, mnemonic, '%eax, %eax'),
                            0x104: Instruction(0x104, b'\xc3', 'retq', '')}
            return model(raw, (Symbol(1, '_local', 0x102, 0xE, 1, 0),),
                         starts=(0x100,), decoded=instructions)
        report = compare_contexts(context(b'\x31\xc0', 'xorl'), context(b'\x29\xc0', 'subl'))
        rows = {item['symbol']: item for item in report['functions']}
        self.assertEqual(rows['_field']['status'], 'different')
        self.assertEqual(rows['_local']['status'], 'not_comparable')

    def test_unsupported_relocations_preclude_exact_byte_identity(self):
        raw = b'\xe8\x00\x00\x00\x00\xc3'
        instructions = {0x100: Instruction(0x100, raw[:5], 'callq', '0x105'),
                        0x105: Instruction(0x105, b'\xc3', 'retq', '')}
        relocation = Relocation(1, 1, 5, False, 2, True, 0, False, None, 0x101, 'section')
        context = model(raw, relocs=(relocation,), decoded=instructions)
        self.assertEqual(compare_contexts(context, context)['counts']['not_comparable'], 1)

    def test_binding_definition_and_weak_flags_remain_distinct(self):
        undefined = Symbol(0, '_helper', 0, 1, 0, 0)
        defined = Symbol(0, '_helper', 0x100, 0xF, 1, 0)
        self.assertNotEqual(binding_signature(undefined), binding_signature(defined))
        self.assertNotEqual(binding_signature(undefined), binding_signature(replace(undefined, description=0x40)))

    def test_disassembly_without_raw_bytes_is_not_valid_evidence(self):
        self.assertEqual(parse_disassembly(['Disassembly of section __TEXT,__text:\n',
                                             '100: retq\n']), {})


class OutputSafetyTests(unittest.TestCase):
    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix='nvrm-code-output-') as temporary:
            root = Path(temporary).resolve()
            marker = root / 'keep.txt'
            marker.write_text('existing evidence\n')
            with self.assertRaises(ValueError):
                AUDIT.new_output(str(root), [])
            self.assertEqual(marker.read_text(), 'existing evidence\n')

    def test_output_cannot_modify_an_unrelated_existing_build(self):
        with tempfile.TemporaryDirectory(prefix='nvrm-code-protected-') as temporary:
            root = Path(temporary).resolve()
            old = root / 'reference-004'
            old.mkdir()
            (old / 'inputs.json').write_text('{}\n')
            outside = root / 'unrelated.o'
            outside.write_bytes(b'fixture')
            with self.assertRaises(ValueError):
                AUDIT.new_output(str(old / 'new-report'), [outside])
            self.assertFalse((old / 'new-report').exists())


if __name__ == '__main__':
    unittest.main()
