#!/usr/bin/env python3
"""Regress the paired YUV420 source integration without building a Darwin kext.

    python3 tools/test_nvrm615diag_build.py --vendor /path/to/open-gpu-615

Both source preparations and all mutations use new temporary trees. The vendor
checkout and pre-existing builds are read only. Native syntax checks do not
establish Apple code generation, a final link, an ABI or hardware behavior.
"""
import argparse
from contextlib import redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nvrm615diag_build_tested', REPO / 'build/nvrm615diag/build.py')
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)
BASE = DIAG.BASE
VENDOR = None
TIMER = 'src/nvidia/src/kernel/gpu/timer/timer_ostimer.c'
FSP = 'src/nvidia/src/kernel/gpu/fsp/kern_fsp.c'
PATCH_SHA = 'ed9fd700dd6a83b01d552b9148a2dc983e28eb17a8a2379c9b062c0ba13af0c0'
PARENT_COMMIT = '61c581ed679ebec7be9685eecf0bad991671da82'
KAPI = 'ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c'


def historical_proof():
    return json.loads(subprocess.check_output(['git', 'show',
        PARENT_COMMIT + ':build/nvrm615diag/proof.json'], cwd=REPO, text=True))


def git(*args, cwd=REPO):
    return subprocess.check_output(['git', *map(str, args)], cwd=cwd, text=True).strip()


def hashes(root):
    return BASE.tree_hashes(root)


class DiagnosticGuardsTests(unittest.TestCase):
    def test_output_requires_a_new_non_system_directory(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615diag-guards-') as temporary:
            root = Path(temporary)
            marker = root / 'keep.txt'
            marker.write_text('keep existing output\n')
            with self.assertRaises(RuntimeError):
                DIAG.new_output(str(root))
            for path in ('relative', '/System/NVRM/new', '/Volumes/Test/EFI/new',
                         str(root / 'NVRM.kext/new'), str(root / 'bad path')):
                with self.subTest(path=path), self.assertRaises(RuntimeError):
                    DIAG.new_output(path)
            self.assertEqual(marker.read_text(), 'keep existing output\n')

    def test_output_inside_any_existing_build_is_rejected_without_creating_it(self):
        for marker in ('inputs.json', 'build-state.json'):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(
                    prefix='nvrm615diag-existing-') as temporary:
                root = Path(temporary)
                (root / marker).write_text('{}\n')
                target = root / 'nested/diagnostic'
                with self.assertRaisesRegex(RuntimeError, 'inside an existing build'):
                    DIAG.new_output(str(target))
                self.assertFalse(target.exists())

    def test_linux_build_stops_before_source_or_output_access(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615diag-platform-') as temporary:
            target = Path(temporary) / 'not-created'
            args = SimpleNamespace(output=str(target))
            with mock.patch.object(DIAG.platform, 'system', return_value='Linux'), \
                    mock.patch.object(DIAG, 'load_inputs') as loader, \
                    mock.patch.object(BASE, 'build') as builder:
                with self.assertRaisesRegex(RuntimeError, 'macOS'):
                    DIAG.build(args)
            loader.assert_not_called()
            builder.assert_not_called()
            self.assertFalse(target.exists())

    def test_shared_build_keeps_the_reference_loader_unless_explicitly_supplied(self):
        args = SimpleNamespace(output='/unused/unit-fixture')
        reference = mock.Mock(side_effect=RuntimeError('reference loader reached'))
        experiment = mock.Mock(side_effect=RuntimeError('experiment loader reached'))
        # Stop at the selected loader; no compiler, filesystem mutation or
        # Darwin executable is invoked by this routing regression.
        with mock.patch.object(BASE.platform, 'system', return_value='Darwin'), \
                mock.patch.object(BASE.os, 'geteuid', return_value=1000), \
                mock.patch.object(BASE, 'load_inputs', reference):
            with self.assertRaisesRegex(RuntimeError, 'reference loader reached'):
                BASE.build(args)
            with self.assertRaisesRegex(RuntimeError, 'experiment loader reached'):
                BASE.build(args, input_loader=experiment)
        reference.assert_called_once_with(args.output)
        experiment.assert_called_once_with(args.output)

    def test_checked_in_diagnostic_and_hook_retain_the_original_provenance(self):
        proof = DIAG.load_proof()
        self.assertEqual(proof['format'], 2)
        self.assertEqual(proof['experiment_base_commit'], DIAG.BASE_COMMIT)
        self.assertEqual(proof['diagnostic_source_commit'], DIAG.DIAG_COMMIT)
        self.assertEqual(proof['diagnostic_patch']['sha256'], PATCH_SHA)
        self.assertEqual(BASE.sha(REPO / proof['diagnostic_patch']['path']), PATCH_SHA)
        original = subprocess.check_output(['git', 'show', DIAG.DIAG_COMMIT + ':'
            + proof['xnu_hook']['source_path']], cwd=REPO)
        self.assertEqual(hashlib.sha256(original).hexdigest(), proof['xnu_hook']['source_sha256'])
        self.assertEqual(len(proof['expected_changed_objects']), 3)
        self.assertEqual(len(set(proof['expected_changed_objects'])), 3)
        self.assertEqual(proof['patch_order'][-3:], [
            'diagnostic:615-yuv420-diagnostic.patch', 'diagnostic:xnu-yuv420-hook.patch',
            'diagnostic:615-yuv420-trigger-overview.patch'])

    def test_followup_retains_original_patch_history_and_three_object_allowlist(self):
        current, original = DIAG.load_proof(), historical_proof()
        for key in ('diagnostic_patch', 'diagnostic_source_commit', 'xnu_hook',
                    'expected_changed_objects', 'allowed_extra_exports',
                    'reference_recipe_sha256', 'baseline_patch_proof_sha256'):
            self.assertEqual(current[key], original[key], key)
        self.assertEqual(current['historical_source_changes'], original['source_changes'])
        self.assertEqual(current['patch_order'][:-1], original['patch_order'])
        followup = current['followup_patch']
        self.assertEqual(followup['parent_commit'], PARENT_COMMIT)
        self.assertEqual(followup['apply_directory'], 'src/nvidia-modeset')
        self.assertEqual(set(followup['source_changes']), {KAPI})
        transition = followup['source_changes'][KAPI]
        self.assertEqual(transition['before_sha256'], original['source_changes'][KAPI]['after_sha256'])
        self.assertEqual(transition['after_sha256'], current['source_changes'][KAPI]['after_sha256'])
        self.assertNotEqual(transition['before_sha256'], transition['after_sha256'])
        for relative, change in original['source_changes'].items():
            self.assertEqual(current['source_changes'][relative]['before_sha256'], change['before_sha256'])
            if relative != KAPI:
                self.assertEqual(current['source_changes'][relative], change)
        self.assertEqual(BASE.sha(DIAG.HERE / followup['path']), followup['sha256'])

    def test_followup_patch_and_declared_transition_tampering_are_rejected(self):
        for mutation in ('patch-bytes', 'before-hash', 'after-hash', 'parent'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                    prefix='nvrm615diag-followup-proof-') as temporary:
                recipe = Path(temporary) / 'recipe'
                shutil.copytree(DIAG.HERE, recipe,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                proof_path = recipe / 'proof.json'
                proof = json.loads(proof_path.read_text())
                followup = proof['followup_patch']
                if mutation == 'patch-bytes':
                    patch = recipe / followup['path']
                    patch.write_bytes(patch.read_bytes() + b'\n')
                elif mutation == 'before-hash':
                    followup['source_changes'][KAPI]['before_sha256'] = '0' * 64
                elif mutation == 'after-hash':
                    followup['source_changes'][KAPI]['after_sha256'] = '0' * 64
                else:
                    followup['parent_commit'] = '0' * 40
                BASE.save(proof_path, proof)
                with mock.patch.object(DIAG, 'HERE', recipe), self.assertRaises(RuntimeError):
                    DIAG.load_proof()


@unittest.skipUnless(VENDOR, 'Use --vendor for real pinned-source integration checks')
class PreparedDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='nvrm615diag-integration-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.vendor_head = git('rev-parse', 'HEAD', cwd=VENDOR)
        cls.vendor_status = git('status', '--porcelain', '--untracked-files=normal', cwd=VENDOR)
        cls.vendor_hashes = {relative: BASE.sha(VENDOR / relative)
                             for relative in git('ls-files', cwd=VENDOR).splitlines()}
        # Freeze recipes while other work in the shared repository can continue.
        cls.diag_recipe = cls.root / 'diagnostic-recipe'
        cls.base_recipe = cls.root / 'reference-recipe'
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
        shutil.copytree(DIAG.HERE, cls.diag_recipe, ignore=ignore)
        shutil.copytree(BASE.HERE, cls.base_recipe, ignore=ignore)
        diag_patch = mock.patch.object(DIAG, 'HERE', cls.diag_recipe)
        base_patch = mock.patch.object(BASE, 'HERE', cls.base_recipe)
        diag_patch.start()
        base_patch.start()
        cls.addClassCleanup(diag_patch.stop)
        cls.addClassCleanup(base_patch.stop)
        cls.reference = cls.root / 'reference'
        cls.output = cls.root / 'diagnostic'
        with redirect_stdout(io.StringIO()):
            BASE.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(cls.reference)))
            DIAG.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(cls.output)))
        cls.reference_manifest = json.loads((cls.reference / 'inputs.json').read_text())
        cls.manifest = json.loads((cls.output / 'inputs.json').read_text())
        cls.proof = DIAG.load_proof()

    @classmethod
    def tearDownClass(cls):
        if git('rev-parse', 'HEAD', cwd=VENDOR) != cls.vendor_head:
            raise AssertionError('The vendor HEAD changed')
        if git('status', '--porcelain', '--untracked-files=normal', cwd=VENDOR) != cls.vendor_status:
            raise AssertionError('The vendor checkout status changed')
        for relative, expected in cls.vendor_hashes.items():
            if BASE.sha(VENDOR / relative) != expected:
                raise AssertionError('The vendor source changed: ' + relative)

    def test_manifest_has_a_separate_experiment_role_and_preserved_baseline(self):
        out, manifest = DIAG.load_inputs(str(self.output))
        self.assertEqual(out, self.output)
        self.assertEqual(manifest['format'], 2)
        self.assertEqual(manifest['build_kind'], 'yuv420-ab-experiment')
        self.assertIs(manifest['diagnostic_enabled'], True)
        self.assertEqual(manifest['nullmoth_commit'], BASE.NULLMOTH)
        self.assertEqual(manifest['nvidia_commit'], BASE.NVIDIA)
        baseline = json.loads((self.output / 'baseline-inputs.json').read_text())
        self.assertEqual(baseline['format'], 1)
        self.assertIs(baseline['diagnostic_enabled'], False)
        self.assertEqual(BASE.sha(self.output / 'baseline-inputs.json'),
                         manifest['baseline_manifest_sha256'])
        for key in ('vendor_sha256', 'nullmoth_sha256', 'patch_order', 'source_date_epoch'):
            self.assertEqual(baseline[key], self.reference_manifest[key])
        state = json.loads((self.output / 'diagnostic-state.json').read_text())
        self.assertEqual(state['status'], 'prepared')
        self.assertIs(state['hardware_approved'], False)
        self.assertFalse((self.output / 'build-state.json').exists())
        self.assertFalse((self.output / 'NVRM.kext').exists())

    def test_reference_loader_still_accepts_reference_and_rejects_the_experiment(self):
        out, manifest = BASE.load_inputs(str(self.reference))
        self.assertEqual(out, self.reference)
        self.assertIs(manifest['diagnostic_enabled'], False)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected input manifest'):
            BASE.load_inputs(str(self.output))
        with self.assertRaises((RuntimeError, KeyError)):
            DIAG.load_inputs(str(self.reference))
        source = self.reference / 'nullmoth/kexts/NVRM/os-nvkms-xnu.cpp'
        self.assertNotIn('nvkms_yuv420_diag_claim', source.read_text())
        self.assertNotIn('Y420-AB',
            (self.reference / 'ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c').read_text())

    def test_exactly_the_two_nvkms_sources_and_paired_hook_change(self):
        changes = set()
        for folder, key in (('ogkm', 'vendor_sha256'), ('nullmoth', 'nullmoth_sha256')):
            before, after = self.reference_manifest[key], self.manifest[key]
            self.assertEqual(set(before), set(after))
            changes.update(folder + '/' + name for name in before if before[name] != after[name])
            self.assertEqual(hashes(self.output / folder), after)
        self.assertEqual(changes, set(self.proof['source_changes']))
        self.assertEqual(len(changes), 3)
        for relative, change in self.proof['source_changes'].items():
            self.assertEqual(BASE.sha(self.reference / relative), change['before_sha256'])
            self.assertEqual(BASE.sha(self.output / relative), change['after_sha256'])
        self.assertEqual(BASE.sha(self.output / 'nullmoth/kexts/NVRM/os-nvkms-xnu.cpp'),
                         self.proof['xnu_hook']['source_sha256'])

    def test_trigger_uses_only_successful_non_end_result_and_the_existing_claim(self):
        source = (self.output / KAPI).read_text()
        trigger = re.search(r'if \((!paramsValidate\.reply\.end.*?)\)\s*\{\s*'
                            r'Yuv420DiagEnumerate\(device, display\);', source, re.S)
        self.assertIsNotNone(trigger)
        condition = ' '.join(trigger.group(1).split())
        self.assertEqual(condition,
                         '!paramsValidate.reply.end && nvkms_yuv420_diag_claim(display)')
        self.assertIn('#define Y420_DIAG_4K30_LOG_LIMIT 8', source)
        self.assertIn('overview=4K30', source)
        self.assertIn('matches30=%u logged30=%u', source)

    def test_followup_is_a_separate_strict_single_file_transition(self):
        followup = self.proof['followup_patch']
        relative = KAPI[len('ogkm/'):]
        expected = followup['source_changes'][KAPI]
        patch = self.diag_recipe / followup['path']
        with tempfile.TemporaryDirectory(prefix='nvrm615diag-followup-once-') as temporary:
            root = Path(temporary)
            target = root / relative
            target.parent.mkdir(parents=True)
            target.write_bytes((self.output / KAPI).read_bytes())
            reverse = ['git', 'apply', '--reverse', '--directory=src/nvidia-modeset']
            subprocess.run([*reverse, '--check', str(patch)], cwd=root, check=True)
            subprocess.run([*reverse, str(patch)], cwd=root, check=True)
            self.assertEqual(BASE.sha(target), expected['before_sha256'])
            DIAG.strict_patch(root, patch, (relative,), 'src/nvidia-modeset')
            self.assertEqual(BASE.sha(target), expected['after_sha256'])
            before_second = target.read_bytes()
            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run(['git', 'apply', '--check', '--directory=src/nvidia-modeset',
                                str(patch)], cwd=root, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(target.read_bytes(), before_second)

    def test_followup_helper_checks_preimage_postimage_and_double_application(self):
        followup = self.proof['followup_patch']
        patch = self.diag_recipe / followup['path']
        for mutation in ('none', 'preimage', 'postimage'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                    prefix='nvrm615diag-followup-hashes-') as temporary:
                root = Path(temporary)
                target = root / KAPI
                target.parent.mkdir(parents=True)
                target.write_bytes((self.output / KAPI).read_bytes())
                reverse = ['git', 'apply', '--reverse', '--directory=src/nvidia-modeset']
                subprocess.run([*reverse, '--check', str(patch)], cwd=root / 'ogkm', check=True)
                subprocess.run([*reverse, str(patch)], cwd=root / 'ogkm', check=True)
                proof = copy.deepcopy(self.proof)
                if mutation == 'preimage':
                    target.write_bytes(target.read_bytes() + b'\n/* changed old diagnostic */\n')
                    before = target.read_bytes()
                    with self.assertRaises(RuntimeError):
                        DIAG.apply_followup(root, proof)
                    self.assertEqual(target.read_bytes(), before)
                elif mutation == 'postimage':
                    proof['followup_patch']['source_changes'][KAPI]['after_sha256'] = '0' * 64
                    with self.assertRaises(RuntimeError):
                        DIAG.apply_followup(root, proof)
                else:
                    DIAG.apply_followup(root, proof)
                    self.assertEqual(BASE.sha(target), followup['source_changes'][KAPI]['after_sha256'])
                    before_second = target.read_bytes()
                    with self.assertRaises(RuntimeError):
                        DIAG.apply_followup(root, proof)
                    self.assertEqual(target.read_bytes(), before_second)

    def test_all_five_baseline_patches_timer_and_fsp_are_retained(self):
        proof = json.loads((BASE.HERE / 'patches/patch-proof.json').read_text())
        self.assertEqual(len(self.manifest['patch_order']), 5)
        self.assertEqual(self.manifest['patch_order'], proof['patch_order'])
        self.assertEqual(self.manifest['patch_order'][-1]['normalized_path'],
                         '615-timer-callback-clear.patch')
        self.assertEqual(BASE.sha(self.output / 'ogkm' / TIMER),
                         'da908b3245f9be48f0787eb48fd4d70b721f6d48cd55e99bb99421bb370379f2')
        self.assertEqual((self.output / 'ogkm' / TIMER).read_bytes(),
                         (self.reference / 'ogkm' / TIMER).read_bytes())
        self.assertEqual(BASE.sha(self.output / 'ogkm' / FSP),
                         '84ba2a49c08aa490aad40fe737b42d135e3bf11969e76ca257a8e5f425c51e54')
        self.assertEqual((self.output / 'ogkm' / FSP).read_bytes(), (VENDOR / FSP).read_bytes())

    def test_prepared_sources_are_independent_regular_files(self):
        relative = 'ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c'
        pristine = VENDOR / relative[len('ogkm/') :]
        paths = (pristine, self.reference / relative, self.output / relative)
        self.assertTrue(all(path.is_file() and not path.is_symlink() for path in paths))
        self.assertEqual(len({(path.stat().st_dev, path.stat().st_ino) for path in paths}), 3)

    def test_changed_source_hook_or_additional_header_is_rejected(self):
        for relative in self.proof['source_changes']:
            path = self.output / relative
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'\n/* changed integration input */\n')
                with self.subTest(relative=relative), self.assertRaisesRegex(RuntimeError, 'Input changed'):
                    DIAG.load_inputs(str(self.output))
            finally:
                path.write_bytes(original)
        extra = self.output / 'nullmoth/kexts/NVRM/unexpected-header.h'
        try:
            extra.write_text('/* Added search-path input. */\n')
            with self.assertRaisesRegex(RuntimeError, 'inventory changed'):
                DIAG.load_inputs(str(self.output))
        finally:
            extra.unlink()

    def test_manifest_role_provenance_or_source_map_tampering_is_rejected(self):
        path = self.output / 'inputs.json'
        original = path.read_bytes()
        mutations = [dict(format=1), dict(diagnostic_enabled=False), dict(build_kind='reference'),
                     dict(diagnostic_patch_order=[]), dict(diagnostic_recipe_sha256={}),
                     dict(baseline_manifest_sha256='0' * 64)]
        modified = copy.deepcopy(self.manifest['vendor_sha256'])
        modified[next(iter(modified))] = '0' * 64
        mutations.append(dict(vendor_sha256=modified))
        try:
            for mutation in mutations:
                changed = copy.deepcopy(self.manifest)
                changed.update(mutation)
                BASE.save(path, changed)
                with self.subTest(mutation=list(mutation)), self.assertRaises(RuntimeError):
                    DIAG.load_inputs(str(self.output))
        finally:
            path.write_bytes(original)

    def test_preserved_baseline_manifest_or_recipe_change_is_rejected(self):
        baseline = self.output / 'baseline-inputs.json'
        original = baseline.read_bytes()
        try:
            baseline.write_bytes(original + b'\n')
            with self.assertRaisesRegex(RuntimeError, 'baseline input manifest changed'):
                DIAG.load_inputs(str(self.output))
        finally:
            baseline.write_bytes(original)
        recipe = self.diag_recipe / 'build.py'
        original = recipe.read_bytes()
        try:
            recipe.write_bytes(original + b'\n# changed recipe fixture\n')
            with self.assertRaisesRegex(RuntimeError, 'recipe or provenance changed'):
                DIAG.load_inputs(str(self.output))
        finally:
            recipe.write_bytes(original)

    def test_duplicate_diagnostic_or_hook_patch_application_is_rejected(self):
        cases = ((self.output / 'ogkm', REPO / self.proof['diagnostic_patch']['path'],
                  ('src/nvidia-modeset/kapi/src/nvkms-kapi.c',
                   'src/nvidia-modeset/src/nvkms-modepool.c'), 'src/nvidia-modeset'),
                 (self.output / 'nullmoth', self.diag_recipe / self.proof['xnu_hook']['path'],
                  ('kexts/NVRM/os-nvkms-xnu.cpp',), None))
        before = {path: BASE.sha(self.output / path) for path in self.proof['source_changes']}
        native_run = subprocess.run
        def quiet_run(*args, **kwargs):
            kwargs.setdefault('stdout', subprocess.PIPE)
            kwargs.setdefault('stderr', subprocess.PIPE)
            return native_run(*args, **kwargs)
        for cwd, patch, expected, directory in cases:
            with self.subTest(patch=patch), self.assertRaises(subprocess.CalledProcessError):
                with mock.patch.object(DIAG.subprocess, 'run', side_effect=quiet_run):
                    DIAG.strict_patch(cwd, patch, expected, directory)
        self.assertEqual(before, {path: BASE.sha(self.output / path) for path in before})

    def test_new_prepare_reproduces_source_hashes_and_patch_order(self):
        output = self.root / 'reproduced'
        with redirect_stdout(io.StringIO()):
            DIAG.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(output)))
        reproduced = json.loads((output / 'inputs.json').read_text())
        for key in ('format', 'build_kind', 'diagnostic_enabled', 'vendor_sha256', 'nullmoth_sha256',
                    'patch_order', 'diagnostic_patch_order', 'diagnostic_proof', 'source_date_epoch',
                    'recipe_sha256', 'diagnostic_recipe_sha256'):
            self.assertEqual(reproduced[key], self.manifest[key], key)
        self.assertNotEqual(reproduced['output'], self.manifest['output'])
        with self.assertRaises(RuntimeError):
            DIAG.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(output)))

    def test_existing_ab_harness_runs_against_the_actual_paired_preparation(self):
        result = subprocess.run([sys.executable, str(REPO / 'tools/test_yuv420_diagnostic.py'),
                                 '--prepared-build', str(self.output)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS claim:', result.stdout)
        for scenario in ('off', 'on', 'sw', 'other', 'end', 'normalerror', 'ioerror',
                         'bound', 'oom1', 'oom2', 'longinfo', 'only30', 'mixed',
                         'diagempty', 'bound30', 'unselected', 'off30',
                         'ioerrorfirst', 'normalretry'):
            self.assertIn('PASS ' + scenario + ':', result.stdout)
        # Running the shared harness must not change source hashes or role.
        _, manifest = DIAG.load_inputs(str(self.output))
        self.assertEqual(manifest['vendor_sha256'], self.manifest['vendor_sha256'])
        self.assertEqual(manifest['nullmoth_sha256'], self.manifest['nullmoth_sha256'])

    def test_complete_diagnostic_translation_units_pass_native_linux_syntax(self):
        if not sys.platform.startswith('linux'):
            self.skipTest('Explicitly a native Linux check, not Apple compilation')
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('A native C compiler is required')
        modeset = self.output / 'ogkm/src/nvidia-modeset'
        flags = []
        for line in (modeset / 'Makefile').read_text().splitlines():
            if not line.startswith('CFLAGS += '):
                continue
            value = line[len('CFLAGS += '):].replace('$(SRC_COMMON)', '../common')
            if value.startswith('-I '):
                parts = shlex.split(value)
                flags += ['-I', str((modeset / parts[1]).resolve())]
            elif value.startswith('-D'):
                flags += shlex.split(value)
        self.assertTrue(flags)
        syntax = ['-fsyntax-only', '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-parameter',
                  '-Wno-sign-compare', '-Wno-missing-field-initializers',
                  '-Werror=implicit-function-declaration', '-Werror=format',
                  '-include', str(self.output / 'ogkm/src/common/sdk/nvidia/inc/cpuopsys.h')]
        for relative in ('kapi/src/nvkms-kapi.c', 'src/nvkms-modepool.c'):
            result = subprocess.run([compiler, *syntax, *flags, str(modeset / relative)], cwd=modeset,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_make_inventory_and_compiler_flags_match_the_reference(self):
        compiler = shutil.which('clang') or shutil.which('cc')
        make = shutil.which('make')
        if not compiler or not make:
            self.skipTest('Native make and compiler needed for the pinned dry run')
        inventories, commands = [], []
        for root in (self.reference, self.output):
            cwd = root / 'ogkm/src/nvidia-modeset'
            argv = [make, '-f', 'Makefile', '-f', str(BASE.HERE / 'darwin.mk'), 'NV_VERBOSE=1',
                    'CC=' + compiler, 'CXX=' + compiler, 'TARGET_OS=Darwin', 'TARGET_ARCH=x86_64',
                    'NV_BUILD_USER=reference', 'NV_BUILD_HOST=isolated', 'SHADER_OBJS=',
                    'EXTRA_CFLAGS=-arch x86_64 -mmacosx-version-min=15.0 -mkernel -DNV_UNIX -DNV_BSD']
            raw = subprocess.check_output([*argv, '-n', '-B', 'reference-objs'], cwd=cwd,
                                          stderr=subprocess.PIPE, text=True)
            rows = BASE.compile_commands(raw)
            objects = subprocess.check_output([*argv, 'reference-print-objs'], cwd=cwd,
                                              stderr=subprocess.PIPE, text=True)
            inventories.append(BASE.validate_object_manifest(objects, rows, 'nvidia-modeset'))
            commands.append([command for _, command in rows])
        self.assertEqual(len(inventories[0]), 225)
        self.assertEqual(inventories[0], inventories[1])
        self.assertEqual(commands[0], commands[1])
        for name in ('nvkms-kapi.o', 'nvkms-modepool.o'):
            self.assertEqual(sum(Path(path).name == name for path in inventories[0]), 1)


def main():
    global VENDOR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', type=Path)
    args, remaining = parser.parse_known_args()
    if args.vendor:
        VENDOR = args.vendor.expanduser().resolve()
    # The integration class is intentionally skipped unless a real pin is supplied.
    PreparedDiagnosticTests.__unittest_skip__ = VENDOR is None
    PreparedDiagnosticTests.__unittest_skip_why__ = 'Use --vendor for real pinned-source integration checks'
    unittest.main(argv=[sys.argv[0], *remaining])


if __name__ == '__main__':
    main()
