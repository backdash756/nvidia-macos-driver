#!/usr/bin/env python3
"""Regress the shared Mach-O checks with real otool text and simulated tools.

Run on Linux or macOS:
    python3 tools/test_nvrm615_macho.py

The success-path tests mock Darwin, the SDK, compiler, and inspection tools.
They verify Python control flow and reports; they do not compile, link, sign,
execute, load, or install any real Darwin artifact.
"""
from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
RECIPE = Path(__file__).resolve().parents[1] / 'build/nvrm615'


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, RECIPE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(RECIPE))
try:
    MACHO = importlib.import_module('macho')
    PREFLIGHT = load_module('nvrm615_macho_preflight', 'preflight.py')
    COMPARE = load_module('nvrm615_macho_compare', 'compare.py')
finally:
    sys.path.pop(0)

# Exact six-column header reported by the user on Tahoe 26.7.1 / x86_64.
REAL_HEADER = (
    'magic       cputype  cpusubtype  caps  filetype    ncmds\n'
    'MH_MAGIC_64 X86_64   ALL         0x00  KEXTBUNDLE 7\n'
)
KEXT_TYPES = ('KEXTBUNDLE', 'KEXT_BUNDLE', 'MH_KEXT_BUNDLE', '11', '0xb', '0x0000000B')
OTHER_TYPES = ('EXECUTE', 'DYLIB', 'BUNDLE', 'MH_EXECUTE', 'MH_DYLIB', 'MH_BUNDLE',
               '2', '6', '8', '0x2', '0x6', '0x8')


def header_with(filetype):
    return REAL_HEADER.replace('KEXTBUNDLE', filetype)


def comparison_image(filetype='KEXTBUNDLE'):
    return {
        'architectures': ['x86_64'],
        'mach_header': MACHO.parse_mach_header(header_with(filetype)),
        'metadata': {
            'CFBundleIdentifier': 'com.nullmoth.NVRM',
            'CFBundleExecutable': 'NVRM',
            'CFBundleVersion': '0.1',
            'CFBundleShortVersionString': '0.1',
            'OSBundleLibraries': {'com.apple.kpi.libkern': '8.0'},
            'IOKitPersonalities': {},
        },
        'imports': ['_kernel_import'],
        'global_exports': ['_export'],
        'signature_verified': True,
        'load_commands': [{'cmd': 'LC_SEGMENT_64', 'details': []}],
        'all_defined_symbols_with_multiplicity': {'_export': 1},
        'unique_function_spans': {'_export': 32},
        'binary_sha256': 'matching-static-fixture',
    }


class MachOHeaderTests(unittest.TestCase):
    def test_preflight_and_comparison_share_the_same_implementation(self):
        for consumer in (PREFLIGHT, COMPARE):
            self.assertIs(consumer.parse_mach_header, MACHO.parse_mach_header)
            self.assertIs(consumer.is_kext_bundle, MACHO.is_kext_bundle)

    def test_exact_tahoe_otool_header_is_a_kext(self):
        parsed = MACHO.parse_mach_header(REAL_HEADER)
        self.assertEqual(parsed['filetype'], 'KEXTBUNDLE')
        self.assertEqual(parsed['header'], REAL_HEADER.splitlines()[1])
        self.assertTrue(MACHO.is_kext_bundle(parsed))

    def test_supported_symbolic_and_numeric_kext_types(self):
        for filetype in KEXT_TYPES:
            with self.subTest(filetype=filetype):
                parsed = MACHO.parse_mach_header(header_with(filetype))
                self.assertEqual(parsed['filetype'], filetype)
                self.assertTrue(MACHO.is_kext_bundle(parsed))

    def test_numeric_mach_magic_and_longer_otool_header(self):
        numeric_magic = header_with('0x0000000B').replace('MH_MAGIC_64', '0xfeedfacf')
        self.assertTrue(MACHO.is_kext_bundle(MACHO.parse_mach_header(numeric_magic)))
        extended = (
            '/tmp/probe:\nMach header\n'
            'magic cputype cpusubtype caps filetype ncmds sizeofcmds flags\n'
            'MH_MAGIC_64 X86_64 ALL 0x00 KEXTBUNDLE 7 1520 NOUNDEFS\n'
        )
        self.assertTrue(MACHO.is_kext_bundle(MACHO.parse_mach_header(extended)))

    def test_executables_dylibs_and_normal_bundles_are_not_kexts(self):
        for filetype in OTHER_TYPES:
            with self.subTest(filetype=filetype):
                parsed = MACHO.parse_mach_header(header_with(filetype))
                self.assertFalse(MACHO.is_kext_bundle(parsed))

    def test_kext_text_in_filename_cannot_override_filetype(self):
        text = '/tmp/KEXT_BUNDLE/KEXTBUNDLE/MH_KEXT_BUNDLE:\nMach header\n' + header_with('EXECUTE')
        parsed = MACHO.parse_mach_header(text)
        self.assertEqual(parsed['filetype'], 'EXECUTE')
        self.assertFalse(MACHO.is_kext_bundle(parsed))

    def test_malformed_and_ambiguous_headers_are_rejected(self):
        cases = {
            'empty': '',
            'filename_only': '/tmp/KEXTBUNDLE: no header\n',
            'missing_filetype_column': REAL_HEADER.replace('filetype', 'not_filetype', 1),
            'missing_magic_column': REAL_HEADER.replace('magic', 'not_magic', 1),
            'bad_magic': REAL_HEADER.replace('MH_MAGIC_64', 'NOT_A_MACH_MAGIC'),
            'truncated_row': REAL_HEADER.rsplit(' 7', 1)[0] + '\n',
            'missing_row': REAL_HEADER.splitlines()[0] + '\n',
            'duplicate_tables': REAL_HEADER + REAL_HEADER,
            'duplicate_column': (
                'magic cputype cpusubtype caps filetype filetype ncmds\n'
                'MH_MAGIC_64 X86_64 ALL 0x00 KEXTBUNDLE KEXTBUNDLE 7\n'
            ),
        }
        for label, text in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                MACHO.parse_mach_header(text)

    def test_comparison_accepts_all_shared_kext_representations(self):
        reference = comparison_image()
        for filetype in KEXT_TYPES:
            with self.subTest(filetype=filetype):
                result = COMPARE.compare(reference, comparison_image(filetype))
                self.assertEqual(result['static_rejections'], [])

    def test_comparison_rejects_other_real_mach_filetypes(self):
        reference = comparison_image()
        for filetype in OTHER_TYPES:
            with self.subTest(filetype=filetype):
                result = COMPARE.compare(reference, comparison_image(filetype))
                self.assertTrue(result['static_rejections'])
                self.assertTrue(any('file type' in item for item in result['static_rejections']))


class SimulatedPreflightTests(unittest.TestCase):
    def run_preflight(self, root, header=REAL_HEADER, nm_returncode=0):
        sdk = root / 'MacOSX.sdk'
        headers = sdk / 'System/Library/Frameworks/Kernel.framework/Headers'
        for name in PREFLIGHT.KEY_HEADERS:
            path = headers / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('/* Simulated SDK header; never compiled. */\n')
        tools = root / 'tools'
        tools.mkdir()
        names = ('xcrun', 'clang', 'clang++', 'ld', 'ar', 'libtool', 'ranlib', 'nm', 'otool',
                 'lipo', 'dwarfdump', 'codesign', 'plutil', 'git', 'make', 'shasum', 'file')
        for name in names:
            path = tools / name
            path.write_text('simulated tool; subprocess execution is mocked\n')
            path.chmod(0o700)
        output = root / 'preflight'
        calls = []

        def fake_run(argv, **kwargs):
            argv = list(map(str, argv))
            calls.append(argv)
            name = Path(argv[0]).name
            stdout, stderr, code = 'simulated version\n', '', 0
            if name == 'xcrun':
                if '--show-sdk-version' in argv:
                    stdout = '15.5\n'
                elif '--show-sdk-path' in argv:
                    stdout = str(sdk) + '\n'
                elif '--find' in argv:
                    stdout = str(tools / argv[-1]) + '\n'
                else:
                    raise AssertionError('Unexpected xcrun command: ' + repr(argv))
            elif name == 'clang++' and '-o' in argv:
                Path(argv[argv.index('-o') + 1]).write_bytes(b'simulated artifact, not executable Mach-O')
            elif name == 'lipo' and '-archs' in argv:
                stdout = 'x86_64\n'
            elif name == 'otool' and '-hv' in argv:
                stdout = header
            elif name == 'nm' and '-u' in argv:
                stdout = '                 U _kernel_import\n'
                code = nm_returncode
                if code:
                    stderr = 'simulated nm failure\n'
            return subprocess.CompletedProcess(argv, code, stdout, stderr)

        argv = ['preflight.py', '--output', str(output), '--sdk', str(sdk)]
        with mock.patch.object(PREFLIGHT.sys, 'argv', argv), \
                mock.patch.object(PREFLIGHT.platform, 'system', return_value='Darwin'), \
                mock.patch.object(PREFLIGHT.platform, 'platform', return_value='simulated Darwin host'), \
                mock.patch.object(PREFLIGHT.platform, 'machine', return_value='x86_64'), \
                mock.patch.object(PREFLIGHT.shutil, 'which', side_effect=lambda name, **kwargs: str(tools / name)), \
                mock.patch.object(PREFLIGHT.subprocess, 'run', side_effect=fake_run), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = PREFLIGHT.main()
        report = json.loads((output / 'preflight.json').read_text())
        return result, report, calls

    def test_real_header_reaches_nm_and_persists_passed_status(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-macho-preflight-') as temporary:
            result, report, calls = self.run_preflight(Path(temporary).resolve())
        self.assertEqual(result, 0)
        self.assertEqual(report['status'], 'passed')
        self.assertNotIn('error', report)
        self.assertEqual(set(report['artifacts']),
                         {'kernel-probe.cpp', 'kernel-probe.o', 'kernel-probe.kext-macho'})
        self.assertTrue(all(len(item['sha256']) == 64 for item in report['artifacts'].values()))
        otool_index = next(i for i, argv in enumerate(calls) if Path(argv[0]).name == 'otool' and '-hv' in argv)
        nm_index = next(i for i, argv in enumerate(calls) if Path(argv[0]).name == 'nm' and '-u' in argv)
        self.assertLess(otool_index, nm_index)
        self.assertEqual(nm_index, len(calls) - 1)
        self.assertEqual(report['commands'][-1]['argv'], calls[nm_index])
        self.assertEqual(report['commands'][-1]['returncode'], 0)

    def test_symbol_inspection_failure_cannot_produce_passed_status(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-macho-nm-failure-') as temporary:
            result, report, calls = self.run_preflight(Path(temporary).resolve(), nm_returncode=1)
        self.assertEqual(result, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertNotIn('artifacts', report)
        self.assertIn('simulated nm failure', report['error'])
        self.assertEqual(report['commands'][-1]['returncode'], 1)
        self.assertEqual(Path(calls[-1][0]).name, 'nm')
        self.assertIn('-u', calls[-1])

    def test_wrong_filetypes_stop_before_symbol_inspection(self):
        for filetype in ('EXECUTE', 'DYLIB', 'BUNDLE'):
            with self.subTest(filetype=filetype), tempfile.TemporaryDirectory(prefix='nvrm615-macho-wrong-type-') as temporary:
                result, report, calls = self.run_preflight(Path(temporary).resolve(), header=header_with(filetype))
            self.assertEqual(result, 1)
            self.assertEqual(report['status'], 'failed')
            self.assertFalse(any(Path(argv[0]).name == 'nm' and '-u' in argv for argv in calls))

    def test_malformed_header_stops_before_symbol_inspection(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-macho-bad-header-') as temporary:
            result, report, calls = self.run_preflight(Path(temporary).resolve(), header='malformed header\n')
        self.assertEqual(result, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(any(Path(argv[0]).name == 'nm' and '-u' in argv for argv in calls))


class SimulatedComparisonTests(unittest.TestCase):
    LOAD_COMMANDS = (
        'Load command 0\n'
        '      cmd LC_SEGMENT_64\n'
        '  cmdsize 152\n'
        '  segname __TEXT\n'
        'Section\n'
        '  sectname __text\n'
        '   segname __TEXT\n'
        '      addr 0x1000\n'
        '      size 0x20\n'
    )

    def test_comparison_main_accepts_real_header_and_writes_review_report(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-macho-comparison-') as temporary:
            root = Path(temporary).resolve()
            reference, candidate = root / 'reference/NVRM.kext', root / 'candidate/NVRM.kext'
            for bundle, version in ((reference, '2.5.4'), (candidate, '0.1')):
                (bundle / 'Contents/MacOS').mkdir(parents=True)
                (bundle / 'Contents/MacOS/NVRM').write_bytes(b'simulated comparison artifact ' + version.encode())
                metadata = copy.deepcopy(comparison_image()['metadata'])
                metadata['CFBundleVersion'] = version
                metadata['CFBundleShortVersionString'] = version
                with (bundle / 'Contents/Info.plist').open('wb') as stream:
                    plistlib.dump(metadata, stream)
            output = root / 'comparison'

            def fake_run(argv, **kwargs):
                argv = list(map(str, argv))
                name = Path(argv[0]).name
                if name == 'lipo':
                    stdout = 'x86_64\n'
                elif name == 'otool' and '-hv' in argv:
                    stdout = REAL_HEADER
                elif name == 'otool' and '-l' in argv:
                    stdout = self.LOAD_COMMANDS
                elif name == 'nm' and '-gU' in argv:
                    stdout = '0000000000001000 T _export\n'
                elif name == 'nm' and '-u' in argv:
                    stdout = '                 U _kernel_import\n'
                elif name == 'nm' and '-a' in argv:
                    stdout = '0000000000001000 T _export\n                 U _kernel_import\n'
                elif name in ('file', 'codesign'):
                    stdout = 'simulated tool output\n'
                else:
                    raise AssertionError('Unexpected comparison command: ' + repr(argv))
                return subprocess.CompletedProcess(argv, 0, stdout, '')

            argv = ['compare.py', '--reference', str(reference), '--candidate', str(candidate), '--output', str(output)]
            with mock.patch.object(COMPARE.sys, 'argv', argv), \
                    mock.patch.object(COMPARE.sys, 'platform', 'darwin'), \
                    mock.patch.object(COMPARE.os, 'geteuid', return_value=1000), \
                    mock.patch.object(COMPARE.shutil, 'which', side_effect=lambda name: '/mock/' + name), \
                    mock.patch.object(COMPARE.subprocess, 'run', side_effect=fake_run), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = COMPARE.main()
            report = json.loads((output / 'comparison.json').read_text())
            text = (output / 'comparison.txt').read_text()
        self.assertEqual(result, 0)
        self.assertEqual(report['reference']['mach_header']['filetype'], 'KEXTBUNDLE')
        self.assertEqual(report['candidate']['mach_header']['filetype'], 'KEXTBUNDLE')
        self.assertEqual(report['comparison']['static_rejections'], [])
        self.assertEqual(report['comparison']['status'], 'review_required')
        self.assertIn('Metadata differs: CFBundleVersion', text)
        self.assertIn('not approval to load', text)


if __name__ == '__main__':
    unittest.main()
