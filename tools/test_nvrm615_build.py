#!/usr/bin/env python3
"""Exercise the isolated NVRM 615 reference recipe without a macOS build.

Run unit/provenance checks:
    python3 tools/test_nvrm615_build.py

Also materialize the real pinned NVIDIA tree and check the baseline preparation:
    python3 tools/test_nvrm615_build.py --vendor /path/to/open-gpu-kernel-modules

All test outputs use temporary directories outside the repository. The optional
vendor checkout is read-only. No driver is built, installed, loaded, or signed.
"""
import argparse
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / 'build/nvrm615'
SPEC = importlib.util.spec_from_file_location('nvrm615_build', RECIPE / 'build.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
COMPARE_SPEC = importlib.util.spec_from_file_location('nvrm615_compare', RECIPE / 'compare.py')
COMPARE = importlib.util.module_from_spec(COMPARE_SPEC)
with mock.patch.object(sys, 'path', [str(RECIPE), *sys.path]):
    COMPARE_SPEC.loader.exec_module(COMPARE)
PREFLIGHT_SPEC = importlib.util.spec_from_file_location('nvrm615_preflight', RECIPE / 'preflight.py')
PREFLIGHT = importlib.util.module_from_spec(PREFLIGHT_SPEC)
with mock.patch.object(sys, 'path', [str(RECIPE), *sys.path]):
    PREFLIGHT_SPEC.loader.exec_module(PREFLIGHT)
VENDOR = None


def git(*args, cwd=None):
    return subprocess.check_output(['git', *map(str, args)], cwd=cwd, text=True).strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def copy_recipe(destination):
    shutil.copytree(RECIPE, destination,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return destination


class OutputSafetyTests(unittest.TestCase):
    def test_system_repository_and_mount_paths_are_rejected(self):
        paths = ('/', '/tmp', '/Library/NullMoth/build', '/System/build/nvrm',
                 '/usr/local/nvrm', '/Applications/NVRM/build',
                 '/private/etc/nvrm', '/Volumes/Test/Library/build',
                 '/Volumes/Test/System/build', '/Volumes/Test/EFI/build',
                 '/Volumes/Test/efi/build', '/tmp/NVRM.kext/build',
                 str(REPO), str(REPO / 'scratch/build'),
                 str(REPO.parent), '/tmp/nvrm/../candidate')
        for path in paths:
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                RUNNER.output_path(path)

    def test_relative_paths_and_make_metacharacters_are_rejected(self):
        with self.assertRaises(RuntimeError):
            RUNNER.output_path('relative-build')
        for char in ' #$`\"\'\\;&|<>()':
            with self.subTest(char=char), self.assertRaises(RuntimeError):
                RUNNER.output_path('/tmp/nvrm' + char + 'candidate')

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-output-') as temporary:
            path = Path(temporary)
            marker = path / 'existing.txt'
            marker.write_text('preserve me\n')
            with self.assertRaises(RuntimeError):
                RUNNER.output_path(path, fresh=True)
            self.assertEqual(marker.read_text(), 'preserve me\n')
            self.assertEqual(RUNNER.output_path(path / 'fresh', fresh=True), path / 'fresh')

    def test_symlink_output_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-symlink-') as temporary:
            root = Path(temporary)
            (root / 'real').mkdir()
            (root / 'link').symlink_to(root / 'real', target_is_directory=True)
            with self.assertRaises(RuntimeError):
                RUNNER.output_path(root / 'link')

    def test_non_darwin_build_stops_before_touching_outputs(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-platform-') as temporary:
            output = Path(temporary) / 'uncreated'
            args = SimpleNamespace(output=str(output), jobs=4,
                                   reference='/missing-reference', sdk=None)
            with mock.patch.object(RUNNER.platform, 'system', return_value='Linux'):
                with self.assertRaisesRegex(RuntimeError, 'macOS'):
                    RUNNER.build(args)
            self.assertFalse(output.exists())


class CompileCommandTests(unittest.TestCase):
    LINE = ('/usr/bin/clang -O2 -mkernel -DNV_BSD -DNV_UNIX '
            '-I"/tmp/vendor headers" -MMD -MF old.d -MT old.o '
            '-c kapi/src/nvkms-kapi.c -o old.o && sed -e s,x,y, old.d')

    def test_quoted_arguments_and_dependency_tail_are_parsed(self):
        rows = RUNNER.compile_commands('make: entering directory\n' + self.LINE + '\necho done\n')
        self.assertEqual(len(rows), 1)
        argv = rows[0][1]
        self.assertIn('-I/tmp/vendor headers', argv)
        self.assertNotIn('&&', argv)
        self.assertNotIn('sed', argv)
        self.assertEqual(argv[argv.index('-c') + 1], 'kapi/src/nvkms-kapi.c')

    def test_duplicate_objects_and_linux_thunk_are_rejected(self):
        with self.assertRaises(RuntimeError):
            RUNNER.compile_commands(self.LINE + '\n' + self.LINE)
        with self.assertRaises(RuntimeError):
            RUNNER.compile_commands(self.LINE.replace('-O2', '-O2 -mfunction-return=thunk-extern'))

    def test_template_rewrites_only_source_and_output_paths(self):
        rows = RUNNER.compile_commands(self.LINE)
        original = list(rows[0][1])
        argv = RUNNER.template_command(rows, 'kapi/src/nvkms-kapi.c',
                                       Path('/tmp/stage/nvrm_kapi_fields.c'),
                                       Path('/tmp/stage/nvrm_kapi_fields.o'))
        for flag, expected in (('-c', '/tmp/stage/nvrm_kapi_fields.c'),
                               ('-o', '/tmp/stage/nvrm_kapi_fields.o'),
                               ('-MF', '/tmp/stage/nvrm_kapi_fields.o.d'),
                               ('-MT', '/tmp/stage/nvrm_kapi_fields.o')):
            self.assertEqual(argv[argv.index(flag) + 1], expected)
        self.assertEqual(rows[0][1], original)
        before = [arg for i, arg in enumerate(original)
                  if i == 0 or original[i - 1] not in ('-c', '-o', '-MF', '-MT')]
        after = [arg for i, arg in enumerate(argv)
                 if i == 0 or argv[i - 1] not in ('-c', '-o', '-MF', '-MT')]
        self.assertEqual(before, after)

    def test_missing_or_ambiguous_template_is_rejected(self):
        rows = RUNNER.compile_commands(self.LINE)
        with self.assertRaises(RuntimeError):
            RUNNER.template_command(rows, 'missing.c', 'new.c', 'new.o')
        with self.assertRaises(RuntimeError):
            RUNNER.template_command(rows + rows, 'kapi/src/nvkms-kapi.c', 'new.c', 'new.o')


class InputGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nvrm615-guards-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.vendor = self.root / 'vendor'
        self.vendor.mkdir()
        git('init', '-q', cwd=self.vendor)
        (self.vendor / 'tracked.txt').write_text('original\n')
        git('add', 'tracked.txt', cwd=self.vendor)
        git('-c', 'user.name=NVRM Build Test', '-c', 'user.email=nvrm-build-test@example.invalid',
            'commit', '-qm', 'Create isolated test fixture', cwd=self.vendor)
        self.commit = git('rev-parse', 'HEAD', cwd=self.vendor)
        self.output = self.root / 'output'
        self.args = SimpleNamespace(vendor=str(self.vendor), output=str(self.output))

    def test_wrong_vendor_commit_is_rejected_before_output_creation(self):
        with self.assertRaisesRegex(RuntimeError, 'pinned NVIDIA'):
            RUNNER.prepare(self.args)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.vendor / 'tracked.txt').read_text(), 'original\n')

    def test_dirty_vendor_is_preserved_and_rejected(self):
        (self.vendor / 'tracked.txt').write_text('keep my local change\n')
        with mock.patch.object(RUNNER, 'NVIDIA', self.commit):
            with self.assertRaisesRegex(RuntimeError, 'clean'):
                RUNNER.prepare(self.args)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.vendor / 'tracked.txt').read_text(), 'keep my local change\n')

    def test_untracked_vendor_input_is_rejected(self):
        (self.vendor / 'local.patch').write_text('preserve this patch\n')
        with mock.patch.object(RUNNER, 'NVIDIA', self.commit):
            with self.assertRaisesRegex(RuntimeError, 'clean'):
                RUNNER.prepare(self.args)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.vendor / 'local.patch').read_text(), 'preserve this patch\n')

    def test_overlapping_input_and_output_trees_are_rejected(self):
        for output in (self.vendor, self.vendor / 'build'):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                RUNNER.prepare(SimpleNamespace(vendor=str(self.vendor), output=str(output)))
        self.assertEqual((self.vendor / 'tracked.txt').read_text(), 'original\n')

    def test_changed_or_symlinked_hashed_input_is_rejected(self):
        original = self.vendor / 'tracked.txt'
        hashes = {'tracked.txt': RUNNER.sha(original)}
        RUNNER.verify_hashes(self.vendor, hashes)
        original.write_text('changed\n')
        with self.assertRaises(RuntimeError):
            RUNNER.verify_hashes(self.vendor, hashes)
        original.unlink()
        target = self.root / 'identical.txt'
        target.write_text('original\n')
        original.symlink_to(target)
        with self.assertRaises(RuntimeError):
            RUNNER.verify_hashes(self.vendor, hashes)


class PatchProvenanceTests(unittest.TestCase):
    def test_all_patch_hashes_and_source_statements_match_pinned_inputs(self):
        proof = json.loads((RECIPE / 'patches/patch-proof.json').read_text())
        self.assertEqual(proof['nvidia_commit'], RUNNER.NVIDIA)
        self.assertEqual(proof['nullmoth_source_commit'], RUNNER.NULLMOTH)
        self.assertEqual(proof['nvgfx_reference_commit'], RUNNER.NVGFX)
        self.assertEqual(proof['format'], 2)
        self.assertEqual(len(proof['patch_order']), 5)
        self.assertEqual(len({item['normalized_path'] for item in proof['patch_order']}), 5)
        for item in proof['patch_order']:
            original = subprocess.check_output(['git', 'show',
                RUNNER.NULLMOTH + ':' + item['original_path']], cwd=REPO)
            normalized = (RECIPE / 'patches' / item['normalized_path']).read_bytes()
            self.assertEqual(digest(original), item['original_sha256'])
            self.assertEqual(digest(normalized), item['normalized_sha256'])
            def changes(data):
                return [line for line in data.decode().splitlines()
                        if line.startswith(('+', '-')) and not line.startswith(('+++', '---'))
                        and line[1:].strip()]
            if item['normalized_path'] == '615-timer-callback-clear.patch':
                self.assertEqual(item['apply_directory'], 'src/nvidia')
                self.assertEqual(changes(normalized),
                    ['+        pTmrEvent->super.pOSTmrCBdata = NULL;'])
                self.assertIn(changes(normalized)[0], changes(original))
                self.assertNotIn('kern_fsp.c', normalized.decode())
            else:
                self.assertEqual(item['apply_directory'], 'src/nvidia-modeset')
                self.assertEqual(changes(original), changes(normalized))
        self.assertNotIn('nvrm_power', RUNNER.CXX_OBJECTS)
        self.assertNotIn('nvrm_physctl', RUNNER.LINK_OBJECTS)
        self.assertIn('nv_xnu_power', RUNNER.CXX_OBJECTS)


class PatchScopeTests(unittest.TestCase):
    def test_only_explicit_rm_and_nvkms_relative_scopes_are_accepted(self):
        for directory in ('src/nvidia', 'src/nvidia-modeset'):
            self.assertEqual(RUNNER.patch_target({'apply_directory': directory,
                'target_path': 'src/example.c'}), directory + '/src/example.c')
        for directory, target in (('src/common', 'example.c'), ('/src/nvidia', 'example.c'),
                                  ('src/nvidia', '/example.c'), ('src/nvidia', '../example.c'),
                                  ('src/nvidia', 'src/../../example.c'),
                                  ('src/nvidia', 'src/name with spaces.c')):
            with self.subTest(directory=directory, target=target), self.assertRaises(RuntimeError):
                RUNNER.patch_target({'apply_directory': directory, 'target_path': target})


class BinaryParserTests(unittest.TestCase):
    HEADER = ("/tmp/name-containing-KEXT_BUNDLE/NVRM:\nMach header\n"
              "      magic cputype cpusubtype caps filetype ncmds sizeofcmds flags\n"
              "MH_MAGIC_64 X86_64 ALL 0x00 KEXT_BUNDLE 8 1520 NOUNDEFS\n")
    LOAD_COMMANDS = ("Load command 0\n      cmd LC_SEGMENT_64\n  cmdsize 152\n"
                     "  segname __TEXT\nSection\n  sectname __text\n"
                     "  segname __TEXT\n      addr 0x0000000000001000\n"
                     "      size 0x0000000000000050\n"
                     "Load command 1\n      cmd LC_UUID\n  cmdsize 24\n"
                     "     uuid 00000000-0000-0000-0000-000000000000\n")

    def test_nm_inventory_preserves_aliases_and_duplicate_names(self):
        rows = COMPARE.parse_symbols(
            "0000000000001000 T _first\n0000000000001000 T _alias\n"
            "0000000000001010 t _duplicate\n0000000000001020 t _duplicate\n"
            "                 U _IOLog\n0000000000001030 D _data\n")
        self.assertEqual(Counter(row['name'] for row in rows)['_duplicate'], 2)
        self.assertEqual(rows[0]['address'], rows[1]['address'])
        self.assertIsNone(next(row['address'] for row in rows if row['name'] == '_IOLog'))
        self.assertEqual(next(row['kind'] for row in rows if row['name'] == '_IOLog'), 'U')

    def test_function_spans_share_alias_bounds_and_exclude_ambiguous_names(self):
        rows = COMPARE.parse_symbols(
            "0000000000001000 T _first\n0000000000001000 T _alias\n"
            "0000000000001010 t _duplicate\n0000000000001020 t _duplicate\n"
            "0000000000001040 T _last\n0000000000001030 D _data\n"
            "0000000000001060 T _outside_text\n")
        spans = COMPARE.unique_function_spans(rows, 0x1050)
        self.assertEqual(spans, {'_first': 0x10, '_alias': 0x10, '_last': 0x10})

    def test_mach_type_comes_from_the_header_column(self):
        self.assertEqual(COMPARE.parse_mach_header(self.HEADER)['filetype'], 'KEXT_BUNDLE')
        executable = self.HEADER.replace('0x00 KEXT_BUNDLE 8', '0x00 EXECUTE 8')
        self.assertEqual(COMPARE.parse_mach_header(executable)['filetype'], 'EXECUTE')
        with self.assertRaises(ValueError):
            COMPARE.parse_mach_header('/tmp/KEXT_BUNDLE/NVRM: no Mach header')

    def test_load_commands_and_text_bounds_are_parsed(self):
        commands = COMPARE.parse_load_commands(self.LOAD_COMMANDS)
        self.assertEqual([item['cmd'] for item in commands], ['LC_SEGMENT_64', 'LC_UUID'])
        self.assertEqual(COMPARE.text_section_end(self.LOAD_COMMANDS), 0x1050)
        with self.assertRaises(ValueError):
            COMPARE.parse_load_commands('no load commands')
        with self.assertRaises(ValueError):
            COMPARE.text_section_end('no sections')


class BinaryComparisonTests(unittest.TestCase):
    @staticmethod
    def image():
        return {'architectures': ['x86_64'], 'mach_header': {'filetype': 'KEXT_BUNDLE'},
                'metadata': {'CFBundleIdentifier': 'com.nullmoth.NVRM',
                             'CFBundleExecutable': 'NVRM', 'CFBundleVersion': '2.5.4',
                             'CFBundleShortVersionString': '2.5.4',
                             'OSBundleLibraries': {'com.apple.kpi.iokit': '8.0'},
                             'OSBundleRequired': None, 'IOKitPersonalities': {}},
                'imports': ['_IOLog'], 'global_exports': ['_start', '_stop'],
                'signature_verified': True, 'load_commands': [{'cmd': 'LC_UUID', 'details': []}],
                'all_defined_symbols_with_multiplicity': {'_start': 1, '_stop': 1},
                'unique_function_spans': {'_start': 16, '_stop': 16},
                'binary_sha256': 'a' * 64}

    def test_identical_static_fixtures_pass_without_load_approval(self):
        result = COMPARE.compare(self.image(), self.image())
        self.assertEqual(result['status'], 'static_checks_passed')
        self.assertEqual(result['static_rejections'], [])
        self.assertIn('Exit 0 is not approval to load', COMPARE.LIMITATION)

    def test_source_version_difference_requires_review_without_relabeling(self):
        reference, candidate = self.image(), self.image()
        candidate['metadata']['CFBundleVersion'] = '0.1'
        candidate['metadata']['CFBundleShortVersionString'] = '0.1'
        result = COMPARE.compare(reference, candidate)
        self.assertEqual(result['status'], 'review_required')
        self.assertEqual(result['static_rejections'], [])
        self.assertIn('Metadata differs: CFBundleVersion', result['differences_requiring_review'])
        self.assertEqual(candidate['metadata']['CFBundleVersion'], '0.1')

    def test_critical_architecture_type_symbols_dependencies_and_signatures_are_rejected(self):
        mutations = (
            ('architecture', lambda image: image.update(architectures=['arm64'])),
            ('Mach-O type', lambda image: image['mach_header'].update(filetype='EXECUTE')),
            ('bundle identifier', lambda image: image['metadata'].update(CFBundleIdentifier='different.NVRM')),
            ('executable', lambda image: image['metadata'].update(CFBundleExecutable='Other')),
            ('dependency', lambda image: image['metadata'].update(OSBundleLibraries={})),
            ('new import', lambda image: image['imports'].append('___x86_return_thunk')),
            ('missing export', lambda image: image.update(global_exports=['_start'])),
            ('signature', lambda image: image.update(signature_verified=False)),
        )
        for label, mutate in mutations:
            with self.subTest(problem=label):
                reference, candidate = self.image(), self.image()
                mutate(candidate)
                result = COMPARE.compare(reference, candidate)
                self.assertEqual(result['status'], 'rejected')
                self.assertTrue(result['static_rejections'])

    def test_layout_symbol_multiplicity_and_hash_changes_require_review(self):
        candidate = self.image()
        candidate['load_commands'][0]['details'] = ['different UUID']
        candidate['all_defined_symbols_with_multiplicity']['_start'] = 2
        candidate['unique_function_spans']['_start'] = 32
        candidate['binary_sha256'] = 'b' * 64
        result = COMPARE.compare(self.image(), candidate)
        self.assertEqual(result['status'], 'review_required')
        self.assertFalse(result['static_rejections'])
        self.assertEqual(result['function_span_comparison']['changed_spans']['_start'],
                         {'reference': 16, 'candidate': 32})
        self.assertEqual(result['defined_symbols_only_candidate'], {'_start': 1})

    def test_comparison_output_cannot_overwrite_inputs_or_previous_reports(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-compare-') as temporary:
            root = Path(temporary)
            bundle = root / 'NVRM.kext'
            bundle.mkdir()
            for output in (bundle, bundle / 'report'):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    COMPARE.prepare_output(output, [bundle])
            report = root / 'report'
            report.mkdir()
            marker = report / 'existing.txt'
            marker.write_text('keep existing comparison\n')
            with self.assertRaises(ValueError):
                COMPARE.prepare_output(report, [bundle])
            self.assertEqual(marker.read_text(), 'keep existing comparison\n')

    def test_linux_comparison_cli_refuses_before_creating_output(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-compare-platform-') as temporary:
            output = Path(temporary) / 'report'
            argv = ['compare.py', '--reference', '/missing/reference',
                    '--candidate', '/missing/candidate', '--output', str(output)]
            with mock.patch.object(COMPARE.sys, 'platform', 'linux'):
                with mock.patch.object(COMPARE.sys, 'argv', argv), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        COMPARE.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(output.exists())


class PreflightTests(unittest.TestCase):
    def test_linux_preflight_refuses_before_creating_output(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-preflight-platform-') as temporary:
            output = Path(temporary) / 'probe'
            with mock.patch.object(PREFLIGHT.platform, 'system', return_value='Linux'):
                with mock.patch.object(PREFLIGHT.sys, 'argv',
                                       ['preflight.py', '--output', str(output)]):
                    with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                        PREFLIGHT.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(output.exists())

    def test_sdk_with_missing_kernel_headers_stops_before_compiler_probe(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-missing-headers-') as temporary:
            root = Path(temporary)
            sdk = root / 'MacOSX.sdk'
            sdk.mkdir()
            output = root / 'probe'
            output.mkdir()
            report = {'commands': []}
            with mock.patch.object(PREFLIGHT.shutil, 'which', return_value=sys.executable):
                with mock.patch.object(PREFLIGHT, 'command',
                                       return_value=SimpleNamespace(stdout='15.5\n')) as command:
                    with self.assertRaisesRegex(RuntimeError, 'required Kernel.framework headers'):
                        PREFLIGHT.perform_checks(report, output, str(sdk))
            command.assert_called_once()
            self.assertNotIn('tools', report)
            self.assertFalse((output / 'kernel-probe.cpp').exists())
            self.assertFalse((output / 'kernel-probe.o').exists())


@unittest.skipUnless(VENDOR, 'Use --vendor for real pinned-source integration checks')
class PreparedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='nvrm615-integration-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.recipe = copy_recipe(cls.root / 'recipe')
        cls.output = cls.root / 'prepared'
        cls.before_status = git('status', '--porcelain', '--untracked-files=normal', cwd=VENDOR)
        cls.before_head = git('rev-parse', 'HEAD', cwd=VENDOR)
        cls.vendor_hashes = {rel: RUNNER.sha(VENDOR / rel)
            for rel in git('ls-files', cwd=VENDOR).splitlines()}
        cls.vendor_file_inventory = set(cls.vendor_hashes)
        with mock.patch.object(RUNNER, 'HERE', cls.recipe), redirect_stdout(io.StringIO()):
            RUNNER.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(cls.output)))
        cls.manifest = json.loads((cls.output / 'inputs.json').read_text())
        cls.proof = json.loads((cls.recipe / 'patches/patch-proof.json').read_text())

    @classmethod
    def tearDownClass(cls):
        if cls.before_head != git('rev-parse', 'HEAD', cwd=VENDOR):
            raise AssertionError('Vendor HEAD changed during the tests')
        if cls.before_status != git('status', '--porcelain', '--untracked-files=normal', cwd=VENDOR):
            raise AssertionError('Vendor worktree changed during the tests')
        for rel, expected in cls.vendor_hashes.items():
            if RUNNER.sha(VENDOR / rel) != expected:
                raise AssertionError('Vendor source changed: ' + rel)

    def test_complete_prepared_inputs_pass_hash_verification(self):
        with mock.patch.object(RUNNER, 'HERE', self.recipe):
            output, manifest = RUNNER.load_inputs(str(self.output))
        self.assertEqual(output, self.output)
        self.assertEqual(manifest['nullmoth_commit'], RUNNER.NULLMOTH)
        self.assertEqual(manifest['nvidia_commit'], RUNNER.NVIDIA)
        self.assertIs(manifest['diagnostic_enabled'], False)
        self.assertEqual(manifest['patch_proof_format'], 2)
        self.assertEqual(manifest['preserved_sources_sha256'], self.proof['preserved_sources_sha256'])
        self.assertEqual(manifest['recipe_sha256']['macho.py'],
                         RUNNER.sha(self.recipe / 'macho.py'))

    def test_baseline_uses_release_snapshot_without_diagnostics_or_fork_power(self):
        kext = self.output / 'nullmoth/kexts/NVRM'
        for name in ('nvrm_power.cpp', 'nvrm_physctl.c', 'nvrm_physctl_abi.h',
                     'patches/615-yuv420-diagnostic.patch'):
            self.assertFalse((kext / name).exists(), name)
        self.assertTrue((kext / 'nv_xnu_power.cpp').is_file())
        self.assertNotIn('nvkms_yuv420_diag_claim', (kext / 'os-nvkms-xnu.cpp').read_text())
        self.assertNotIn('nvgfx_power_start', (kext / 'NVRM.cpp').read_text())
        for rel in ('kapi/src/nvkms-kapi.c', 'src/nvkms-modepool.c'):
            self.assertNotIn('Y420-AB', (self.output / 'ogkm/src/nvidia-modeset' / rel).read_text())
        actual = (kext / 'NVRM.cpp').read_bytes()
        expected = subprocess.check_output(['git', 'show',
            RUNNER.NULLMOTH + ':kexts/NVRM/NVRM.cpp'], cwd=REPO)
        self.assertEqual(actual, expected)

    def test_all_prepared_patch_and_result_hashes_match_proof(self):
        self.assertEqual(self.manifest['patch_order'], self.proof['patch_order'])
        for item in self.proof['patch_order']:
            self.assertEqual(RUNNER.sha(self.recipe / 'patches' / item['normalized_path']),
                             item['normalized_sha256'])
        for rel, expected in self.proof['patched_sources_sha256'].items():
            self.assertEqual(RUNNER.sha(self.output / 'ogkm' / rel), expected)
        for item in self.proof['patch_order']:
            result = subprocess.run(['git', 'apply', '--check', '--directory=' + item['apply_directory'],
                str(self.recipe / 'patches' / item['normalized_path'])],
                cwd=self.output / 'ogkm', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0, 'Duplicate patch unexpectedly accepted')

    def test_exact_source_inventory_changes_are_limited_to_the_three_baseline_targets(self):
        self.assertEqual(set(self.manifest['vendor_sha256']), self.vendor_file_inventory)
        differences = {rel for rel, expected in self.vendor_hashes.items()
                       if self.manifest['vendor_sha256'][rel] != expected}
        self.assertEqual(differences, {
            'src/nvidia-modeset/kapi/src/nvkms-kapi.c',
            'src/nvidia-modeset/src/nvkms-rm.c',
            'src/nvidia/src/kernel/gpu/timer/timer_ostimer.c',
        })
        self.assertEqual(self.proof['patched_sources_sha256'][
            'src/nvidia-modeset/kapi/src/nvkms-kapi.c'],
            'ff0f957733b1576ad5ddd6e6c60c33f899c71f0010e52b1c078f90011203ae81')
        self.assertEqual(self.proof['patched_sources_sha256'][
            'src/nvidia-modeset/src/nvkms-rm.c'],
            '0118510d6ee1c72b8cef4be93338ad1ca0d681ef15df880a39a9157950bb01cc')

    def test_timer_patch_adds_only_the_original_unconditional_callback_clear(self):
        relative = 'src/nvidia/src/kernel/gpu/timer/timer_ostimer.c'
        pristine = (VENDOR / relative).read_bytes()
        staged = (self.output / 'ogkm' / relative).read_bytes()
        call = b'        status = osDestroyNanoTimer(pGpu->pOsGpuInfo, pTmrEvent->super.pOSTmrCBdata);\n'
        self.assertEqual(pristine.count(call), 1)
        self.assertEqual(staged, pristine.replace(call,
            call + b'        pTmrEvent->super.pOSTmrCBdata = NULL;\n'))
        self.assertEqual(digest(pristine),
            '9710b89d0725d433b993ba803983bde1262096e7f62b81d39d0bc8f816ff5bb3')
        self.assertEqual(digest(staged),
            'da908b3245f9be48f0787eb48fd4d70b721f6d48cd55e99bb99421bb370379f2')

    def test_upstream_fsp_guard_and_clear_remain_byte_identical(self):
        relative = 'src/nvidia/src/kernel/gpu/fsp/kern_fsp.c'
        pristine = (VENDOR / relative).read_bytes()
        self.assertEqual((self.output / 'ogkm' / relative).read_bytes(), pristine)
        self.assertEqual(digest(pristine),
            '84ba2a49c08aa490aad40fe737b42d135e3bf11969e76ca257a8e5f425c51e54')
        self.assertEqual(self.proof['preserved_sources_sha256'][relative], digest(pristine))
        cleanup = pristine.decode().split('kfspCleanupBootState_IMPL', 1)[1]
        cleanup = cleanup.split('portMemFree', 1)[0]
        self.assertIn('if (pKernelFsp->pPollEvent != NULL)', cleanup)
        self.assertIn('tmrEventDestroy(pTmr, pKernelFsp->pPollEvent);', cleanup)
        self.assertIn('pKernelFsp->pPollEvent = NULL;', cleanup)

    def test_timer_patch_once_and_duplicate_application_rejected_by_hash_guard(self):
        item = self.proof['patch_order'][-1]
        relative = RUNNER.patch_target(item)
        with tempfile.TemporaryDirectory(prefix='nvrm615-timer-once-') as temporary:
            root = Path(temporary)
            target = root / relative
            target.parent.mkdir(parents=True)
            target.write_bytes((VENDOR / relative).read_bytes())
            with mock.patch.object(RUNNER, 'HERE', self.recipe):
                RUNNER.apply_baseline_patch(root, item)
                expected = target.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'Input changed'):
                    RUNNER.apply_baseline_patch(root, item)
            self.assertEqual(target.read_bytes(), expected)
            self.assertEqual(digest(expected), item['target_after_sha256'])

    def test_timer_preimage_postimage_and_declared_scope_mismatches_are_rejected(self):
        item = self.proof['patch_order'][-1]
        relative = RUNNER.patch_target(item)
        for mutation in ('preimage', 'postimage', 'declared-target'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                    prefix='nvrm615-timer-tamper-') as temporary:
                root = Path(temporary)
                target = root / relative
                target.parent.mkdir(parents=True)
                pristine = (VENDOR / relative).read_bytes()
                target.write_bytes(pristine)
                changed = dict(item)
                if mutation == 'preimage':
                    target.write_bytes(pristine + b'\n/* changed input */\n')
                elif mutation == 'postimage':
                    changed['target_after_sha256'] = '0' * 64
                else:
                    changed['target_path'] = 'src/kernel/gpu/timer/other.c'
                    (root / RUNNER.patch_target(changed)).write_bytes(pristine)
                with mock.patch.object(RUNNER, 'HERE', self.recipe):
                    with self.assertRaises(RuntimeError):
                        RUNNER.apply_baseline_patch(root, changed)
                if mutation == 'declared-target':
                    self.assertEqual(target.read_bytes(), pristine)

    def test_repeated_prepare_reproduces_source_hashes_patch_order_and_epoch(self):
        output = self.root / 'reproduced'
        with mock.patch.object(RUNNER, 'HERE', self.recipe), redirect_stdout(io.StringIO()):
            RUNNER.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(output)))
        second = json.loads((output / 'inputs.json').read_text())
        for key in ('nullmoth_commit', 'nvidia_commit', 'source_date_epoch',
                    'patch_order', 'patch_proof_format', 'preserved_sources_sha256',
                    'recipe_sha256', 'nullmoth_sha256', 'vendor_sha256', 'diagnostic_enabled'):
            self.assertEqual(second[key], self.manifest[key], key)

    def test_existing_diagnostic_is_applicable_but_not_applied(self):
        patch = REPO / 'kexts/NVRM/patches/615-yuv420-diagnostic.patch'
        self.assertEqual(RUNNER.sha(patch),
            'ed9fd700dd6a83b01d552b9148a2dc983e28eb17a8a2379c9b062c0ba13af0c0')
        subprocess.run(['git', 'apply', '--check', '--directory=src/nvidia-modeset', str(patch)],
                       cwd=self.output / 'ogkm', check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotIn('Y420-AB',
            (self.output / 'ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c').read_text())

    def test_manifest_metadata_change_is_rejected(self):
        path = self.output / 'inputs.json'
        original = path.read_bytes()
        changed = json.loads(original)
        changed['diagnostic_enabled'] = True
        try:
            path.write_text(json.dumps(changed))
            with mock.patch.object(RUNNER, 'HERE', self.recipe):
                with self.assertRaises(RuntimeError):
                    RUNNER.load_inputs(str(self.output))
        finally:
            path.write_bytes(original)

    def test_staged_source_change_is_rejected(self):
        source = self.output / 'nullmoth/kexts/NVRM/os-nvkms-xnu.cpp'
        original = source.read_bytes()
        try:
            source.write_bytes(original + b'\n/* changed fixture */\n')
            with mock.patch.object(RUNNER, 'HERE', self.recipe):
                with self.assertRaisesRegex(RuntimeError, 'Input changed'):
                    RUNNER.load_inputs(str(self.output))
        finally:
            source.write_bytes(original)

    def test_additional_staged_header_is_rejected(self):
        source = self.output / 'nullmoth/kexts/NVRM/unexpected_header.h'
        try:
            source.write_text('/* A new include-search input must invalidate the snapshot. */\n')
            with mock.patch.object(RUNNER, 'HERE', self.recipe):
                with self.assertRaisesRegex(RuntimeError, 'Input file inventory changed'):
                    RUNNER.load_inputs(str(self.output))
        finally:
            source.unlink()

    def test_recipe_change_after_prepare_is_rejected(self):
        path = self.recipe / 'darwin.mk'
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'\n# changed fixture\n')
            with mock.patch.object(RUNNER, 'HERE', self.recipe):
                with self.assertRaisesRegex(RuntimeError, 'recipe changed'):
                    RUNNER.load_inputs(str(self.output))
        finally:
            path.write_bytes(original)

    def test_tampered_patch_is_rejected_before_new_output_creation(self):
        recipe = copy_recipe(self.root / 'tampered-recipe')
        item = self.proof['patch_order'][0]
        patch = recipe / 'patches' / item['normalized_path']
        patch.write_bytes(patch.read_bytes() + b'\n')
        output = self.root / 'tampered-output'
        with mock.patch.object(RUNNER, 'HERE', recipe):
            with self.assertRaisesRegex(RuntimeError, 'patch hash mismatch'):
                RUNNER.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(output)))
        self.assertFalse(output.exists())

    def test_duplicate_patch_manifest_does_not_complete_prepare(self):
        recipe = copy_recipe(self.root / 'duplicate-recipe')
        proof_path = recipe / 'patches/patch-proof.json'
        proof = json.loads(proof_path.read_text())
        proof['patch_order'].append(proof['patch_order'][0])
        proof_path.write_text(json.dumps(proof))
        output = self.root / 'duplicate-output'
        native_run = subprocess.run
        def quiet_run(*args, **kwargs):
            kwargs.setdefault('stdout', subprocess.PIPE)
            kwargs.setdefault('stderr', subprocess.PIPE)
            return native_run(*args, **kwargs)
        with mock.patch.object(RUNNER, 'HERE', recipe):
            with mock.patch.object(RUNNER.subprocess, 'run', side_effect=quiet_run):
                with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                    RUNNER.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(output)))
        self.assertFalse((output / 'inputs.json').exists())

    def test_complete_baseline_sources_pass_native_linux_syntax_and_format_checks(self):
        if not sys.platform.startswith('linux'):
            self.skipTest('This is explicitly a native Linux syntax check, not a Darwin build')
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('A native C compiler is required')
        modeset = self.output / 'ogkm/src/nvidia-modeset'
        vendor_flags = []
        for line in (modeset / 'Makefile').read_text().splitlines():
            if not line.startswith('CFLAGS += '):
                continue
            value = line[len('CFLAGS += '):].replace('$(SRC_COMMON)', '../common')
            if value.startswith('-I '):
                parts = shlex.split(value)
                self.assertEqual(len(parts), 2)
                vendor_flags += ['-I', str((modeset / parts[1]).resolve())]
            elif value.startswith('-D'):
                vendor_flags += shlex.split(value)
        self.assertTrue(vendor_flags)
        syntax_flags = ['-fsyntax-only', '-std=gnu11', '-Wall', '-Wextra',
                        '-Wno-unused-parameter', '-Wno-sign-compare',
                        '-Wno-missing-field-initializers',
                        '-Werror=implicit-function-declaration', '-Werror=format',
                        '-include', str(self.output / 'ogkm/src/common/sdk/nvidia/inc/cpuopsys.h')]
        for relative in ('kapi/src/nvkms-kapi.c', 'src/nvkms-rm.c'):
            with self.subTest(source=relative):
                result = subprocess.run([compiler] + syntax_flags + vendor_flags + [str(modeset / relative)],
                    cwd=modeset, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print('  Native Linux syntax/format check passed: ' + relative)

    def test_real_make_dry_run_has_one_command_per_object(self):
        compiler = shutil.which('clang') or shutil.which('cc')
        make = shutil.which('make')
        if not compiler or not make:
            self.skipTest('A native C compiler and GNU make are needed for Makefile dry runs')
        for component in ('nvidia', 'nvidia-modeset'):
            with self.subTest(component=component):
                cwd = self.output / 'ogkm/src' / component
                args = [make, '-f', 'Makefile', '-f', str(self.recipe / 'darwin.mk'),
                        'NV_VERBOSE=1', 'CC=' + compiler,
                        'CXX=' + (shutil.which('clang++') or shutil.which('c++') or compiler),
                        'TARGET_OS=Darwin', 'TARGET_ARCH=x86_64',
                        'NV_BUILD_USER=reference', 'NV_BUILD_HOST=isolated',
                        'EXTRA_CFLAGS=-arch x86_64 -mmacosx-version-min=15.0 -mkernel -DNV_UNIX -DNV_BSD']
                if component == 'nvidia-modeset':
                    args.append('SHADER_OBJS=')
                dry_run = subprocess.check_output(args + ['-n', '-B', 'reference-objs'],
                    cwd=cwd, text=True, stderr=subprocess.PIPE, timeout=120)
                rows = RUNNER.compile_commands(dry_run)
                objects_text = subprocess.check_output(args + ['reference-print-objs'],
                    cwd=cwd, text=True, stderr=subprocess.PIPE, timeout=120)
                objects = RUNNER.validate_object_manifest(objects_text, rows, component)
                outputs = [argv[argv.index('-o') + 1] for _, argv in rows]
                self.assertGreater(len(rows), 200)
                self.assertEqual(Counter(objects), Counter(outputs))
                template = ('arch/nvalloc/unix/src/osapi.c' if component == 'nvidia'
                            else 'kapi/src/nvkms-kapi.c')
                adapted = RUNNER.template_command(rows, template, '/tmp/local.c', '/tmp/local.o')
                self.assertIn('-mkernel', adapted)
                self.assertIn('-DNV_BSD', adapted)
                self.assertNotIn('-mfunction-return=thunk-extern', adapted)
                print(f'  {component}: {len(rows)} compile commands match the object manifest')


def main():
    global VENDOR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', type=Path)
    args, remaining = parser.parse_known_args()
    if args.vendor:
        VENDOR = args.vendor.expanduser().resolve()
        # The decorator is evaluated before CLI parsing; enable this class here.
        PreparedInputTests.__unittest_skip__ = False
    unittest.main(argv=[sys.argv[0], *remaining])


if __name__ == '__main__':
    main()
