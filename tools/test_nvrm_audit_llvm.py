#!/usr/bin/env python3
"""Exercise the audit with cross-generated Mach-O objects and real LLVM tools.

These are small synthetic fixtures, not either NVRM binary. Running this suite
on Linux does not establish real NVRM code equality or a successful macOS build.
The generated Mach-O objects are never executed, linked into a driver or loaded.

Optional tool overrides:
    python3 tools/test_nvrm_audit_llvm.py --clang /path/clang \
        --llvm-objdump /path/llvm-objdump --nm /path/llvm-nm
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
AUDIT = REPO / 'tools/nvrm_code_audit.py'
LLVM_LOCAL = Path('/workspace/.nvidia-tools/llvm/usr/lib/llvm-19/bin')
TOOLS = {}


def find_tool(name, fallback):
    return shutil.which(name) or (str(fallback) if fallback.is_file() else None)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RealLLVMObjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nvrm-audit-real-llvm-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def object(self, name, text, flags=(), assembly=False):
        source = self.root / (name + ('.s' if assembly else '.c'))
        source.write_text(text)
        obj = self.root / (name + '.o')
        argv = [TOOLS['clang'], '-target', 'x86_64-apple-macos15', '-O2',
                '-fno-asynchronous-unwind-tables', '-fno-unwind-tables']
        if not assembly:
            argv += ['-fno-omit-frame-pointer', '-fcf-protection=none']
        argv += list(flags) + ['-c', str(source), '-o', str(obj)]
        process = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(obj.read_bytes()[:4], b'\xcf\xfa\xed\xfe')
        return obj

    def audit(self, reference, candidate, symbols=('_field',), output=None, objdump=None):
        output = output or self.root / 'report'
        before = {path: sha256(path) for path in (reference, candidate)}
        argv = [sys.executable, str(AUDIT), '--reference', str(reference),
                '--candidate', str(candidate), '--output', str(output),
                '--llvm-objdump', objdump or TOOLS['llvm-objdump'], '--nm', TOOLS['nm'],
                '--allow-object', '--symbols', *symbols]
        result = subprocess.run(argv, cwd=str(self.root), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=90)
        for path, digest in before.items():
            self.assertEqual(sha256(path), digest, 'Audit modified an input object')
        return result, output

    def result(self, reference, candidate):
        process, output = self.audit(reference, candidate)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue((output / 'summary.txt').is_file())
        report = json.loads((output / 'audit.json').read_text())
        entry = next(item for item in report['functions'] if item['symbol'] == '_field')
        return report, entry

    def test_real_cross_compiled_field_access_is_identical(self):
        source = 'int field(const void *p) { return p ? *(const int *)((const char *)p + 0x10) : 0; }\n'
        reference = self.object('reference', source)
        candidate = self.object('candidate', source)
        report, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'identical', entry)
        self.assertEqual(report['counts']['identical'], 1)

    def test_real_structure_displacement_change_is_not_identical(self):
        reference = self.object('reference', 'int field(const char *p) { return *(const int *)(p + 0x10); }\n')
        candidate = self.object('candidate', 'int field(const char *p) { return *(const int *)(p + 0x14); }\n')
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'different', entry)

    def test_real_immediate_change_is_not_normalized_away(self):
        reference = self.object('reference', 'int field(int value) { return value + 7; }\n')
        candidate = self.object('candidate', 'int field(int value) { return value + 8; }\n')
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'different', entry)

    def test_real_large_immediate_change_is_not_treated_as_an_address(self):
        reference = self.object('reference', '.text\n.globl _field\n_field:\nmovabsq $0x1122334455667788, %rax\nretq\n', assembly=True)
        candidate = self.object('candidate', '.text\n.globl _field\n_field:\nmovabsq $0x1122334455667789, %rax\nretq\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'different', entry)

    def test_real_register_change_is_not_normalized_away(self):
        reference = self.object('reference', '.text\n.globl _field\n_field:\nmovl 0x10(%rdi), %eax\nretq\n', assembly=True)
        candidate = self.object('candidate', '.text\n.globl _field\n_field:\nmovl 0x10(%rdi), %edx\nretq\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'different', entry)

    def test_real_instruction_kind_change_is_not_normalized_away(self):
        reference = self.object('reference', '.text\n.globl _field\n_field:\naddl $7, %eax\nretq\n', assembly=True)
        candidate = self.object('candidate', '.text\n.globl _field\n_field:\nsubl $7, %eax\nretq\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'different', entry)

    def test_equal_placeholder_bytes_cannot_hide_different_data_relocations(self):
        reference = self.object('reference', 'extern int data_a; int field(void) { return data_a; }\n')
        candidate = self.object('candidate', 'extern int data_b; int field(void) { return data_b; }\n')
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_equal_placeholder_bytes_cannot_hide_different_branch_relocations(self):
        reference = self.object('reference', 'extern int helper_a(int); int field(int n) { return helper_a(n); }\n')
        candidate = self.object('candidate', 'extern int helper_b(int); int field(int n) { return helper_b(n); }\n')
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_matching_external_branch_relocations_remain_comparable(self):
        source = 'extern int helper(int); int field(int n) { return helper(n); }\n'
        reference = self.object('reference', source)
        candidate = self.object('candidate', source)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'identical', entry)

    def test_matching_named_static_data_region_is_comparable(self):
        # The encoded RIP target is __data address 7, without a linker relocation.
        source = ('.text\n.globl _field\n_field:\n'
                  '.byte 0x8b, 0x05, 0x01, 0, 0, 0\nretq\n'
                  '.data\n.globl _target_data\n_target_data:\n.long 7\n')
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source, assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'identical', entry)

    def test_named_static_initializer_change_cannot_hide_behind_equal_rip_code(self):
        code = ('.text\n.globl _field\n_field:\n'
                '.byte 0x8b, 0x05, 0x01, 0, 0, 0\nretq\n'
                '.data\n.globl _target_data\n_target_data:\n.long ')
        reference = self.object('reference', code + '7\n', assembly=True)
        candidate = self.object('candidate', code + '8\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_rip_displacement_before_immediate_does_not_mask_the_immediate(self):
        # C7 /0 carries disp32 followed by imm32: the RIP field is not the last four bytes.
        code = ('.text\n.globl _field\n_field:\n.byte 0xc7, 0x05, 1, 0, 0, 0, ')
        tail = ', 0, 0, 0\nretq\n.data\n.globl _target_data\n_target_data:\n.long 0\n'
        reference = self.object('reference', code + '7' + tail, assembly=True)
        candidate = self.object('candidate', code + '8' + tail, assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'different', entry)

    def test_relocated_rip_store_does_not_mask_its_following_immediate(self):
        code = '.text\n.globl _field\n_field:\nmovl $'
        tail = ', _target_data(%rip)\nretq\n.data\n.globl _target_data\n_target_data:\n.long 0\n'
        reference = self.object('reference', code + '7' + tail, assembly=True)
        candidate = self.object('candidate', code + '8' + tail, assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_aliased_function_entry_is_explicitly_not_comparable(self):
        source = '.text\n.globl _field\n.globl _alias\n_field:\n_alias:\nmovl $7, %eax\nretq\n'
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source, assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'not_comparable', entry)
        self.assertIn('alias', entry['reason'])

    def test_branch_targeted_nop_suffix_is_never_stripped(self):
        source = ('.text\n.globl _field\n_field:\nmovl $7, %eax\n'
                  'jne Lpadding\nretq\nLpadding:\nnop\n')
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source + 'nop\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'not_comparable', entry)

    def test_strong_and_weak_external_calls_cannot_compare_identical(self):
        source = '.text\n.globl _field\n_field:\ncallq _helper\nretq\n'
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source + '.weak_reference _helper\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_strong_and_weak_external_data_cannot_compare_identical(self):
        source = ('.text\n.globl _field\n_field:\n'
                  'movq _data@GOTPCREL(%rip), %rax\nmovl (%rax), %eax\nretq\n')
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source + '.weak_reference _data\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_hlt_does_not_prove_a_following_nop_suffix_unreachable(self):
        source = '.text\n.globl _field\n_field:\nhlt\nnop\n'
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source + 'nop\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_int3_does_not_prove_a_following_nop_suffix_unreachable(self):
        source = '.text\n.globl _field\n_field:\nint3\nnop\n'
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source + 'nop\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_undefined_and_defined_branch_targets_cannot_compare_identical(self):
        source = '.text\n.globl _field\n_field:\ncallq _helper\nretq\n'
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source + '.globl _helper\n_helper:\nretq\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def runtime_segment_reference(self, prefix):
        # RIP's encoded image offset is insufficient: FS/GS adds runtime state.
        source = ('.text\n.globl _field\n_field:\n.byte ' + prefix
                  + ', 0x48, 0x8b, 0x05, 1, 0, 0, 0\nretq\n'
                  '.data\n.globl _target_data\n_target_data:\n.quad 7\n')
        reference = self.object('reference', source, assembly=True)
        candidate = self.object('candidate', source, assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'not_comparable', entry)
        self.assertIn('FS/GS', entry['reason'])

    def test_fs_rip_reference_is_not_a_proven_static_image_reference(self):
        self.runtime_segment_reference('0x64')

    def test_gs_rip_reference_is_not_a_proven_static_image_reference(self):
        self.runtime_segment_reference('0x65')

    def test_data_pointer_into_nop_suffix_prevents_padding_removal(self):
        body = '.text\n.globl _field\n_field:\nretq\nnop\n'
        pointer = '.data\n.globl _pointer\n_pointer:\n.quad _field + 1\n'
        reference = self.object('reference', body + pointer, assembly=True)
        candidate = self.object('candidate', body + 'nop\n' + pointer, assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_other_function_lea_into_nop_suffix_prevents_padding_removal(self):
        def source(padding_count):
            # Encode a resolved LEA to _field+1 with no relocation fallback.
            # _address begins after RET and padding; its LEA is seven bytes.
            displacement = (1 - ((1 + padding_count) + 7)) & 0xffffffff
            encoded = ', '.join(hex(byte) for byte in displacement.to_bytes(4, 'little'))
            return ('.text\n.globl _field\n_field:\nretq\n' + 'nop\n' * padding_count
                    + '.globl _address\n_address:\n.byte 0x48, 0x8d, 0x05, ' + encoded + '\nretq\n')
        reference = self.object('reference', source(1), assembly=True)
        candidate = self.object('candidate', source(2), assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_real_frame_pointer_and_cet_delta_is_visible(self):
        source = 'int field(const char *p) { return *(const int *)(p + 0x10); }\n'
        reference = self.object('reference', source)
        candidate = self.object('candidate', source,
                                flags=('-fomit-frame-pointer', '-fcf-protection=branch'))
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'different', entry)

    def test_real_local_branch_addresses_can_move_without_code_changes(self):
        body = ('.globl _field\n_field:\ntestl %edi, %edi\nje Lzero\n'
                'movl $7, %eax\nretq\nLzero:\nxorl %eax, %eax\nretq\n')
        reference = self.object('reference', '.text\n' + body, assembly=True)
        candidate = self.object('candidate', '.text\n.space 16, 0x90\n' + body, assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'identical', entry)

    def test_real_unreachable_nop_alignment_is_separate_from_body(self):
        body = '.text\n.globl _field\n_field:\nmovl $7, %eax\nretq\n'
        reference = self.object('reference', body + '.p2align 4, 0x90\n', assembly=True)
        candidate = self.object('candidate', body + '.p2align 5, 0x90\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'identical', entry)

    def test_real_zero_bytes_decoded_as_add_are_never_dropped_as_padding(self):
        body = '.text\n.globl _field\n_field:\nmovl $7, %eax\nretq\n'
        reference = self.object('reference', body, assembly=True)
        candidate = self.object('candidate', body + '.byte 0, 0, 0, 0\n', assembly=True)
        _, entry = self.result(reference, candidate)
        self.assertNotEqual(entry['status'], 'identical', entry)

    def test_real_missing_function_is_explicitly_not_comparable(self):
        reference = self.object('reference', 'int field(void) { return 7; }\n')
        candidate = self.object('candidate', 'int other(void) { return 7; }\n')
        report, entry = self.result(reference, candidate)
        self.assertEqual(entry['status'], 'not_comparable', entry)
        self.assertGreaterEqual(report['counts']['not_comparable'], 1)
        self.assertEqual(report['counts']['identical'], 0)

    def test_existing_report_directory_is_not_overwritten(self):
        reference = self.object('reference', 'int field(void) { return 7; }\n')
        candidate = self.object('candidate', 'int field(void) { return 7; }\n')
        output = self.root / 'existing-report'
        output.mkdir()
        marker = output / 'keep.txt'
        marker.write_text('preserve this report\n')
        process, _ = self.audit(reference, candidate, output=output)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(marker.read_text(), 'preserve this report\n')

    def test_disassembler_failure_is_operational_error(self):
        reference = self.object('reference', 'int field(void) { return 7; }\n')
        candidate = self.object('candidate', 'int field(void) { return 7; }\n')
        failing = self.root / 'failing-objdump'
        failing.write_text('#!/bin/sh\nexit 7\n')
        failing.chmod(0o700)
        process, _ = self.audit(reference, candidate, objdump=str(failing))
        self.assertEqual(process.returncode, 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clang', default=find_tool('clang', LLVM_LOCAL / 'clang'))
    parser.add_argument('--llvm-objdump', default=find_tool('llvm-objdump', LLVM_LOCAL / 'llvm-objdump'))
    parser.add_argument('--nm', default=find_tool('llvm-nm', LLVM_LOCAL / 'llvm-nm'))
    args, remaining = parser.parse_known_args()
    TOOLS.update({'clang': args.clang, 'llvm-objdump': args.llvm_objdump, 'nm': args.nm})
    if not all(TOOLS.values()):
        RealLLVMObjectTests.__unittest_skip__ = True
        RealLLVMObjectTests.__unittest_skip_why__ = 'Optional clang/LLVM tools are unavailable'
    unittest.main(argv=[sys.argv[0], *remaining])


if __name__ == '__main__':
    main()
