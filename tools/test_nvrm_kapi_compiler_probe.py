#!/usr/bin/env python3
"""Exercise isolated KAPI command replay with fake compilers and audit fixtures.

These tests do not demonstrate real Darwin compilation or machine-code equality.
"""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().with_name('nvrm_kapi_compiler_probe.py')
SPEC = importlib.util.spec_from_file_location('nvrm_kapi_probe_tested', SCRIPT)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class ProbeFixture:
    def __init__(self, root):
        self.root = root
        self.build = root / 'reference-004'
        self.source = self.build / 'nullmoth/kexts/NVRM/nvrm_kapi_fields.c'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('/* Simulated pinned source, not compiled C. */\n')
        self.cwd = self.build / 'ogkm/src/nvidia-modeset'
        self.cwd.mkdir(parents=True)
        self.vendor_header = self.cwd / 'recorded-header.h'
        self.vendor_header.write_text('/* Simulated vendor header. */\n')
        self.original_object = self.build / 'glue/nvrm_kapi_fields.o'
        self.original_object.parent.mkdir()
        self.original_object.write_bytes(b'CONTROL-FIXTURE')
        self.compiler = root / 'toolchain/clang'
        self.compiler.parent.mkdir()
        self.compiler.write_text(
            '#!' + sys.executable + '\n'
            'import pathlib, sys\n'
            'args = sys.argv[1:]\n'
            'obj = pathlib.Path(args[args.index("-o") + 1])\n'
            'variant = "-fno-omit-frame-pointer" in args and "-fcf-protection=none" in args\n'
            'obj.write_bytes(b"VARIANT-FIXTURE" if variant else b"CONTROL-FIXTURE")\n'
            'pathlib.Path(args[args.index("-MF") + 1]).write_text("fixture dependency\\n")\n'
            'print("simulated compiler; not a Darwin build")\n'
        )
        self.compiler.chmod(0o700)
        self.sdk = root / 'MacOSX.sdk'
        self.sdk.mkdir()
        self.sdk_header = self.sdk / 'kernel-header.h'
        self.sdk_header.write_text('/* Simulated SDK header. */\n')
        self.reference = root / 'backup/NVRM.kext/Contents/MacOS/NVRM'
        self.reference.parent.mkdir(parents=True)
        self.reference.write_bytes(b'OFFICIAL-FIXTURE')
        self.output = root / 'probe-output'
        self.argv = [str(self.compiler), '-arch', 'x86_64', '-isysroot', str(self.sdk),
                     '-O2', '-fomit-frame-pointer', '-fcf-protection=branch',
                     '-DNV_BSD', '-DNAME=value with spaces', '-MMD',
                     '-MF', str(self.original_object) + '.d', '-MT', str(self.original_object),
                     '-c', str(self.source), '-o', str(self.original_object)]
        self.entry = {'argv': self.argv, 'cwd': str(self.cwd), 'log': 'glue-nvrm_kapi_fields'}
        self.manifest = {
            'format': 1, 'output': str(self.build), 'nullmoth_commit': PROBE.NULLMOTH,
            'nvidia_commit': PROBE.NVIDIA, 'diagnostic_enabled': False, 'source_date_epoch': 1700000000,
            'nullmoth_sha256': {'kexts/NVRM/nvrm_kapi_fields.c': PROBE.sha256(self.source)},
            'vendor_sha256': {'src/nvidia-modeset/recorded-header.h': PROBE.sha256(self.vendor_header)},
        }
        self.preflight = {'status': 'passed',
                          'tools': {'clang': {'path': str(self.compiler), 'sha256': PROBE.sha256(self.compiler)}},
                          'sdk': {'path': str(self.sdk), 'kernel_headers': str(self.sdk),
                                  'key_header_sha256': {'kernel-header.h': PROBE.sha256(self.sdk_header)}}}
        (self.build / 'preflight').mkdir()
        self.save()

    def save(self, entries=None):
        PROBE.save(self.build / 'inputs.json', self.manifest)
        PROBE.save(self.build / 'preflight/preflight.json', self.preflight)
        entries = [self.entry] if entries is None else entries
        (self.build / 'commands.jsonl').write_text(''.join(json.dumps(entry) + '\n' for entry in entries))

    def args(self):
        return SimpleNamespace(build=str(self.build), reference=str(self.reference),
                               output=str(self.output), llvm_objdump=None)

    def simulated_audit(self, reference, candidate, output, env, llvm_objdump=None):
        output.mkdir()
        if Path(reference) == self.original_object:
            status = 'identical'
        else:
            status = 'identical' if Path(candidate).read_bytes() == b'VARIANT-FIXTURE' else 'different'
        PROBE.save(output / 'audit.json', {'functions': [{'symbol': symbol, 'status': status}
                                                       for symbol in PROBE.SYMBOLS]})
        return {'output': str(output), 'returncode': 0, 'argv': ['simulated audit'],
                'stdout': 'simulated classification; no disassembly executed', 'stderr': ''}


class ProbePlanningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nvrm-kapi-probe-')
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ProbeFixture(Path(self.temporary.name).resolve())

    def test_recorded_source_compiler_and_command_are_verified(self):
        template = PROBE.load_template(self.fixture.build)
        self.assertEqual(template['argv'], self.fixture.argv)
        self.assertEqual(template['cwd'], self.fixture.cwd)
        self.assertEqual(template['original_object'], self.fixture.original_object)

    def test_only_output_paths_change_for_control_and_two_flags_for_variant(self):
        template = PROBE.load_template(self.fixture.build)
        control, control_obj = PROBE.planned_command(template, self.fixture.output)
        variant, variant_obj = PROBE.planned_command(template, self.fixture.output, True)
        for flag in PROBE.OUTPUT_FLAGS:
            self.assertNotEqual(control[control.index(flag) + 1], self.fixture.argv[self.fixture.argv.index(flag) + 1])
        differences = [(old, new) for old, new in zip(control, variant) if old != new]
        self.assertEqual(len(differences), 5)
        self.assertIn(('-fomit-frame-pointer', '-fno-omit-frame-pointer'), differences)
        self.assertIn(('-fcf-protection=branch', '-fcf-protection=none'), differences)
        for flag in ('-c', '-isysroot', '-arch'):
            self.assertEqual(control[control.index(flag) + 1], variant[variant.index(flag) + 1])
        self.assertEqual(template['argv'], self.fixture.argv)
        self.assertEqual(control_obj.name, 'control.o')
        self.assertEqual(variant_obj.name, 'variant.o')

    def test_source_or_compiler_changes_stop_before_output_creation(self):
        for target in (self.fixture.source, self.fixture.compiler, self.fixture.sdk_header):
            original = target.read_bytes()
            try:
                target.write_bytes(original + b'changed')
                with self.subTest(target=target), self.assertRaises(ValueError):
                    PROBE.load_template(self.fixture.build)
            finally:
                target.write_bytes(original)
        self.assertFalse(self.fixture.output.exists())

    def test_ambiguous_or_wrong_recorded_command_is_rejected(self):
        self.fixture.save([self.fixture.entry, self.fixture.entry])
        with self.assertRaisesRegex(ValueError, 'exactly one recorded'):
            PROBE.load_template(self.fixture.build)
        self.fixture.save()
        self.fixture.entry['cwd'] = str(self.fixture.build)
        self.fixture.save()
        with self.assertRaisesRegex(ValueError, 'working directory'):
            PROBE.load_template(self.fixture.build)

    def test_hidden_outputs_response_files_and_changed_baseline_flags_are_rejected(self):
        original = list(self.fixture.argv)
        mutations = [original + [option] for option in (
            '@options.rsp', '-save-temps', '-ftime-trace', '-fmodules', '-MJjoined.json',
            '-serialize-diagnostics', '-Xclang', '-fplugin=plugin', '-oother.o', '-MFother.d')]
        mutations += [[PROBE.FLAG_REPLACEMENTS.get(arg, arg) for arg in original]]
        for argv in mutations:
            self.fixture.entry['argv'] = argv
            self.fixture.save()
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                PROBE.load_template(self.fixture.build)
        self.fixture.entry['argv'] = original
        self.fixture.save()

    def test_new_output_cannot_overlap_existing_inputs_or_system_paths(self):
        protected = (self.fixture.build, self.fixture.reference)
        for output in (self.fixture.build / 'new-output', self.fixture.root,
                       self.fixture.reference.parent / 'nested/output', Path('/Library/new-audit')):
            protected_inputs = (self.fixture.build, self.fixture.reference.parent)
            with self.subTest(output=output), self.assertRaises(ValueError):
                PROBE.prepare_output(output, protected_inputs)
        with self.assertRaises(ValueError):
            PROBE.prepare_output(self.fixture.build, protected)
        self.assertFalse(self.fixture.output.exists())

    def test_completed_probe_uses_only_new_outputs_and_records_unchanged_inputs(self):
        before = PROBE.snapshot_tree(self.fixture.build)
        reference_before = self.fixture.reference.read_bytes()
        with mock.patch.object(PROBE, 'run_audit', side_effect=self.fixture.simulated_audit), redirect_stdout(io.StringIO()):
            result = PROBE.perform_probe(self.fixture.args())
        self.assertEqual(result, 0)
        report = json.loads((self.fixture.output / 'probe.json').read_text())
        self.assertEqual(report['status'], 'reproduced')
        self.assertTrue(report['control_reproduces_recorded_object'])
        self.assertTrue(report['flag_variant_matches_official'])
        self.assertTrue(report['baseline_differs_from_official'])
        self.assertTrue(report['inputs_unchanged'])
        self.assertEqual(before, PROBE.snapshot_tree(self.fixture.build))
        self.assertEqual(reference_before, self.fixture.reference.read_bytes())
        commands = [json.loads(line) for line in (self.fixture.output / 'commands.jsonl').read_text().splitlines()]
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertEqual(command['cwd'], str(self.fixture.cwd))
            for flag in PROBE.OUTPUT_FLAGS:
                self.assertTrue(PROBE.is_inside(Path(command['argv'][command['argv'].index(flag) + 1]), self.fixture.output))

    def test_uncertain_machine_code_result_is_not_called_reproduced(self):
        def uncertain(*args, **kwargs):
            record = self.fixture.simulated_audit(*args, **kwargs)
            output = Path(record['output']) / 'audit.json'
            data = json.loads(output.read_text())
            data['functions'][0]['status'] = 'not_comparable'
            PROBE.save(output, data)
            return record
        with mock.patch.object(PROBE, 'run_audit', side_effect=uncertain), redirect_stdout(io.StringIO()):
            result = PROBE.perform_probe(self.fixture.args())
        self.assertEqual(result, 1)
        self.assertEqual(json.loads((self.fixture.output / 'probe.json').read_text())['status'], 'not_verified')

    def test_failed_compile_preserves_failure_report_and_existing_inputs(self):
        self.fixture.compiler.write_text('#!' + sys.executable + '\nimport sys\nsys.stderr.write("fixture compile failure\\n")\nsys.exit(7)\n')
        self.fixture.preflight['tools']['clang']['sha256'] = PROBE.sha256(self.fixture.compiler)
        self.fixture.save()
        before = PROBE.snapshot_tree(self.fixture.build)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = PROBE.perform_probe(self.fixture.args())
        self.assertEqual(result, 2)
        report = json.loads((self.fixture.output / 'probe.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['inputs_unchanged'])
        self.assertIn('fixture compile failure', (self.fixture.output / 'control-compile.stderr.txt').read_text())
        self.assertEqual(before, PROBE.snapshot_tree(self.fixture.build))

    def test_linux_cli_stops_without_creating_output(self):
        with mock.patch.object(PROBE.platform, 'system', return_value='Linux'), \
                mock.patch.object(PROBE.sys, 'argv', ['probe.py', '--build', str(self.fixture.build),
                    '--reference', str(self.fixture.reference), '--output', str(self.fixture.output)]), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            PROBE.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(self.fixture.output.exists())


class AuditContractTests(unittest.TestCase):
    def test_missing_duplicate_or_incomplete_symbol_reports_cannot_pass(self):
        with tempfile.TemporaryDirectory(prefix='nvrm-kapi-audit-contract-') as temporary:
            output = Path(temporary)
            record = {'output': str(output), 'returncode': 0}
            for entries in ([], [{'symbol': PROBE.SYMBOLS[0], 'status': 'identical'}],
                            [{'symbol': PROBE.SYMBOLS[0], 'status': 'identical'}] * 2):
                PROBE.save(output / 'audit.json', {'functions': entries})
                self.assertFalse(PROBE.audit_verified(record))
            self.assertFalse(PROBE.audit_verified({'output': str(output / 'missing'), 'returncode': 0}))


if __name__ == '__main__':
    unittest.main()
