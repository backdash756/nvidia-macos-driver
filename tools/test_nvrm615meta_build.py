#!/usr/bin/env python3
"""Regress the isolated metadata build without compiling or loading Darwin NVRM.

    python3 tools/test_nvrm615meta_build.py --vendor /path/to/open-gpu-615

All source preparations, fixture mutations and synthetic linked records are in
new temporary trees. The source repository and vendor checkout stay read-only.
Native Linux diagnostic harnesses and simulated build routing are not macOS
compilation, ABI proof, installation, or hardware approval.
"""
import argparse
from contextlib import redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


META = module('nvrm615meta_build_tested', REPO / 'build/nvrm615meta/build.py')
DIAG, BASE = META.DIAG, META.BASE
KMOD_FIX = module('nvrm615meta_build_record_fixtures', REPO / 'tools/test_nvrm615meta_kmod.py')
VENDOR = None
TIMER = 'src/nvidia/src/kernel/gpu/timer/timer_ostimer.c'
FSP = 'src/nvidia/src/kernel/gpu/fsp/kern_fsp.c'


def git(*args, cwd=REPO):
    return subprocess.check_output(['git', *map(str, args)], cwd=cwd, text=True).strip()


def pinned(path):
    return subprocess.check_output(['git', 'show', BASE.NULLMOTH + ':' + path], cwd=REPO)


class MetadataGuardTests(unittest.TestCase):
    def test_proof_is_a_separate_profile_with_one_additional_object(self):
        proof = META.load_proof()
        original = json.loads(subprocess.check_output(['git', 'show',
            META.PARENT + ':build/nvrm615diag/proof.json'], cwd=REPO))
        self.assertEqual(proof['diagnostic_proof'], original)
        self.assertEqual(proof['expected_changed_objects'], original['expected_changed_objects'] + ['glue/NVRM.o'])
        self.assertEqual(len(original['expected_changed_objects']), 3)
        self.assertEqual(len(proof['expected_changed_objects']), 4)
        self.assertEqual(proof['patch_order'], original['patch_order'] + ['metadata:254-metadata.patch'])
        self.assertEqual(set(proof['metadata_patch']['source_changes']), {META.PLIST, META.CPP})
        self.assertEqual(set(proof['source_changes']), set(original['source_changes']) | {META.PLIST, META.CPP})

    def test_global_versions_remain_the_original_pinned_sources(self):
        for relative in ('kexts/NVRM/Info.plist', 'kexts/NVRM/NVRM.cpp'):
            self.assertEqual((REPO / relative).read_bytes(), pinned(relative))
        values = plistlib.loads((REPO / 'kexts/NVRM/Info.plist').read_bytes())
        self.assertTrue(all(values[key] == '0.1' for key in META.VERSION_KEYS))
        self.assertIn(b'KMOD_EXPLICIT_DECL(com.nullmoth.NVRM, "0.1", _start, _stop)',
                      (REPO / 'kexts/NVRM/NVRM.cpp').read_bytes())

    def test_output_reuse_and_existing_build_parent_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615meta-guard-') as temporary:
            root = Path(temporary)
            (root / 'keep.txt').write_text('preserve existing output\n')
            with self.assertRaises(RuntimeError):
                DIAG.new_output(str(root))
            (root / 'inputs.json').write_text('{}\n')
            output = root / 'nested/metadata'
            with self.assertRaisesRegex(RuntimeError, 'inside an existing build'):
                DIAG.new_output(str(output))
            self.assertFalse(output.exists())
            self.assertEqual((root / 'keep.txt').read_text(), 'preserve existing output\n')

    def test_linux_build_stops_before_accessing_inputs(self):
        with mock.patch.object(META.platform, 'system', return_value='Linux'), \
                mock.patch.object(META, 'load_inputs') as load, \
                mock.patch.object(BASE, 'build') as build:
            with self.assertRaisesRegex(RuntimeError, 'macOS'):
                META.build(SimpleNamespace(output='/unused/new-output'))
        load.assert_not_called()
        build.assert_not_called()

    def test_artifact_version_override_requires_the_explicit_metadata_profile(self):
        # Stop at jobs validation before any compiler or reference-bundle I/O.
        # An unrelated manifest field must not change the ordinary 0.1 default.
        with tempfile.TemporaryDirectory(prefix='nvrm615meta-version-guard-') as temporary:
            output = Path(temporary) / 'not-created'
            args = SimpleNamespace(output=str(output), jobs=0)
            reference = {'format': 1, 'diagnostic_enabled': False, 'metadata_version': '2.5.4'}
            metadata = {'format': 3, 'build_kind': META.KIND, 'diagnostic_enabled': True,
                        'metadata_enabled': True, 'experimental': True, 'metadata_version': '2.5.4'}
            old_loader = mock.Mock(return_value=(output, reference))
            metadata_loader = mock.Mock(return_value=(output, metadata))
            with mock.patch.object(BASE.platform, 'system', return_value='Darwin'), \
                    mock.patch.object(BASE.os, 'geteuid', return_value=1000):
                with self.assertRaisesRegex(RuntimeError, 'Jobs must be'):
                    BASE.build(args, input_loader=old_loader)
                with self.assertRaisesRegex(RuntimeError, 'explicit isolated metadata profile'):
                    BASE.build(args, input_loader=old_loader, artifact_metadata_version='2.5.4')
                with self.assertRaisesRegex(RuntimeError, 'Jobs must be'):
                    BASE.build(args, input_loader=metadata_loader, artifact_metadata_version='2.5.4')
                for changed in ({'experimental': False}, {'metadata_enabled': False}, {'format': 2}):
                    invalid = {**metadata, **changed}
                    with self.subTest(changed=changed), self.assertRaisesRegex(
                            RuntimeError, 'explicit isolated metadata profile'):
                        BASE.build(args, input_loader=lambda value: (output, invalid),
                                   artifact_metadata_version='2.5.4')
            self.assertFalse(output.exists())

    def test_metadata_scope_and_provenance_tampering_are_rejected(self):
        for mutation in ('patch', 'before', 'after', 'scope', 'extra-object', 'parent', 'diagnostic'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                    prefix='nvrm615meta-proof-') as temporary:
                recipe = Path(temporary) / 'recipe'
                shutil.copytree(META.HERE, recipe, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                proof_path = recipe / 'proof.json'
                proof = json.loads(proof_path.read_text())
                if mutation == 'patch':
                    path = recipe / proof['metadata_patch']['path']
                    path.write_bytes(path.read_bytes() + b'\n')
                elif mutation in ('before', 'after'):
                    proof['metadata_patch']['source_changes'][META.PLIST][mutation + '_sha256'] = '0' * 64
                elif mutation == 'scope':
                    proof['metadata_patch']['source_changes']['nullmoth/kexts/NVRM/extra.cpp'] = {
                        'before_sha256': '0' * 64, 'after_sha256': '1' * 64}
                elif mutation == 'extra-object':
                    proof['expected_changed_objects'].append('glue/os-xnu.o')
                elif mutation == 'parent':
                    proof['metadata_parent_commit'] = '0' * 40
                else:
                    proof['diagnostic_proof']['expected_changed_objects'].append('glue/NVRM.o')
                BASE.save(proof_path, proof)
                with mock.patch.object(META, 'HERE', recipe), self.assertRaises(RuntimeError):
                    META.load_proof()

    def test_only_the_two_known_text_transformations_are_supported(self):
        with self.assertRaises(RuntimeError):
            META.expected_metadata_bytes('nullmoth/kexts/NVRM/other.cpp', b'0.1')
        with self.assertRaises(RuntimeError):
            META.expected_metadata_bytes(META.CPP, b'no explicit declaration')
        with self.assertRaises(RuntimeError):
            META.expected_metadata_bytes(META.PLIST, b'<plist><dict/></plist>')


@unittest.skipUnless(VENDOR, 'Use --vendor for real pinned-source integration checks')
class PreparedMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='nvrm615meta-integration-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.vendor_head = git('rev-parse', 'HEAD', cwd=VENDOR)
        cls.vendor_status = git('status', '--porcelain', '--untracked-files=normal', cwd=VENDOR)
        cls.vendor_hashes = {relative: BASE.sha(VENDOR / relative)
                             for relative in git('ls-files', cwd=VENDOR).splitlines()}
        cls.global_hashes = {relative: BASE.sha(REPO / relative)
                             for relative in ('kexts/NVRM/Info.plist', 'kexts/NVRM/NVRM.cpp')}
        # Freeze all three recipes so independent review edits cannot invalidate
        # the test's own prepare/load hash agreement part-way through a run.
        for owner, label in ((META, 'metadata'), (DIAG, 'diagnostic'), (BASE, 'reference')):
            path = cls.root / (label + '-recipe')
            shutil.copytree(owner.HERE, path, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            patch = mock.patch.object(owner, 'HERE', path)
            patch.start()
            cls.addClassCleanup(patch.stop)
        cls.reference, cls.diagnostic, cls.output = (cls.root / name for name in ('reference', 'diagnostic', 'metadata'))
        with redirect_stdout(io.StringIO()):
            BASE.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(cls.reference)))
            DIAG.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(cls.diagnostic)))
            META.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(cls.output)))
        cls.proof = META.load_proof()
        cls.manifest = json.loads((cls.output / 'inputs.json').read_text())
        cls.diag_manifest = json.loads((cls.diagnostic / 'inputs.json').read_text())
        cls.reference_manifest = json.loads((cls.reference / 'inputs.json').read_text())

    @classmethod
    def tearDownClass(cls):
        if git('rev-parse', 'HEAD', cwd=VENDOR) != cls.vendor_head:
            raise AssertionError('Vendor HEAD changed')
        if git('status', '--porcelain', '--untracked-files=normal', cwd=VENDOR) != cls.vendor_status:
            raise AssertionError('Vendor status changed')
        for relative, digest in cls.vendor_hashes.items():
            if BASE.sha(VENDOR / relative) != digest:
                raise AssertionError('Vendor source changed: ' + relative)
        for relative, digest in cls.global_hashes.items():
            if BASE.sha(REPO / relative) != digest:
                raise AssertionError('Global source version changed: ' + relative)

    def test_new_manifest_has_its_own_experimental_identity_and_hashes(self):
        output, manifest = META.load_inputs(str(self.output))
        self.assertEqual(output, self.output)
        self.assertEqual((manifest['format'], manifest['build_kind'], manifest['metadata_version']),
                         (3, META.KIND, '2.5.4'))
        self.assertTrue(manifest['metadata_enabled'] and manifest['diagnostic_enabled'] and manifest['experimental'])
        metadata = json.loads((self.output / 'metadata-manifest.json').read_text())
        state = json.loads((self.output / 'metadata-state.json').read_text())
        for value in (metadata, state):
            self.assertTrue(value['experimental'])
            self.assertFalse(value['runtime_approved'])
            self.assertFalse(value['hardware_approved'])
        self.assertEqual(state['status'], 'prepared')
        self.assertEqual(metadata['metadata_proof'], self.proof)
        for filename, key in (('metadata-manifest.json', 'metadata_manifest_sha256'),
                              ('diagnostic-inputs.json', 'diagnostic_manifest_sha256'),
                              ('baseline-inputs.json', 'baseline_manifest_sha256')):
            self.assertEqual(BASE.sha(self.output / filename), manifest[key])
        self.assertFalse((self.output / 'diagnostic-state.json').exists())
        self.assertTrue((self.output / 'diagnostic-pre-metadata-state.json').is_file())

    def test_real_preparation_matches_the_metadata_comparison_policy_contract(self):
        # This uses the actual builder output rather than another hand-written
        # comparator fixture, catching cross-module manifest schema mismatches.
        compare = module('nvrm615meta_prepared_policy_contract', REPO / 'build/nvrm615meta/compare.py')
        compare.MetadataPolicy().validate_manifest(self.manifest, self.output, self.proof)

    def test_reference_and_original_diagnostic_versions_and_profiles_remain_unchanged(self):
        self.assertEqual(self.reference_manifest['format'], 1)
        self.assertFalse(self.reference_manifest['diagnostic_enabled'])
        self.assertEqual(self.diag_manifest['format'], 2)
        self.assertEqual(len(self.diag_manifest['diagnostic_proof']['expected_changed_objects']), 3)
        for root in (self.reference, self.diagnostic):
            plist = plistlib.loads((root / META.PLIST).read_bytes())
            self.assertTrue(all(plist[key] == '0.1' for key in META.VERSION_KEYS))
            self.assertIn(b'KMOD_EXPLICIT_DECL(com.nullmoth.NVRM, "0.1", _start, _stop)',
                          (root / META.CPP).read_bytes())

    def test_metadata_and_earlier_loaders_reject_each_others_manifest_roles(self):
        for loader, root in ((BASE.load_inputs, self.output), (DIAG.load_inputs, self.output),
                             (META.load_inputs, self.reference), (META.load_inputs, self.diagnostic)):
            with self.subTest(loader=loader.__name__, root=root), self.assertRaises((RuntimeError, KeyError)):
                loader(str(root))

    def test_exactly_two_sources_change_beyond_the_original_diagnostic(self):
        self.assertEqual(self.manifest['vendor_sha256'], self.diag_manifest['vendor_sha256'])
        changes = {name for name in self.manifest['nullmoth_sha256']
                   if self.manifest['nullmoth_sha256'][name] != self.diag_manifest['nullmoth_sha256'][name]}
        self.assertEqual(changes, {'kexts/NVRM/Info.plist', 'kexts/NVRM/NVRM.cpp'})
        for folder, key in (('ogkm', 'vendor_sha256'), ('nullmoth', 'nullmoth_sha256')):
            actual = BASE.tree_hashes(self.output / folder)
            self.assertEqual(actual, self.manifest[key])
        before = json.loads((self.output / 'baseline-inputs.json').read_text())
        combined = {folder + '/' + name for folder, key in (('ogkm', 'vendor_sha256'), ('nullmoth', 'nullmoth_sha256'))
                    for name, digest in before[key].items() if self.manifest[key][name] != digest}
        self.assertEqual(combined, set(self.proof['source_changes']))
        self.assertEqual(len(combined), 5)

    def test_full_plist_changes_only_the_two_named_version_values(self):
        before = plistlib.loads((self.diagnostic / META.PLIST).read_bytes())
        actual = plistlib.loads((self.output / META.PLIST).read_bytes())
        expected = copy.deepcopy(before)
        for key in META.VERSION_KEYS:
            self.assertEqual(before[key], '0.1')
            expected[key] = '2.5.4'
        self.assertEqual(actual, expected)
        self.assertEqual({key for key in before if before[key] != actual[key]}, set(META.VERSION_KEYS))

    def test_nvrm_cpp_changes_only_the_explicit_kmod_version_argument(self):
        before = (self.diagnostic / META.CPP).read_bytes()
        after = (self.output / META.CPP).read_bytes()
        old = b'KMOD_EXPLICIT_DECL(com.nullmoth.NVRM, "0.1", _start, _stop)'
        new = b'KMOD_EXPLICIT_DECL(com.nullmoth.NVRM, "2.5.4", _start, _stop)'
        self.assertEqual(before.count(old), 1)
        self.assertEqual(after, before.replace(old, new))
        self.assertEqual(before, pinned('kexts/NVRM/NVRM.cpp'))

    def test_baseline_timer_fsp_and_every_diagnostic_source_are_identical(self):
        self.assertEqual(len(self.manifest['patch_order']), 5)
        self.assertEqual(self.manifest['patch_order'], self.reference_manifest['patch_order'])
        self.assertEqual(BASE.sha(self.output / 'ogkm' / TIMER),
                         'da908b3245f9be48f0787eb48fd4d70b721f6d48cd55e99bb99421bb370379f2')
        self.assertEqual(BASE.sha(self.output / 'ogkm' / FSP),
                         '84ba2a49c08aa490aad40fe737b42d135e3bf11969e76ca257a8e5f425c51e54')
        for relative, transition in self.proof['diagnostic_proof']['source_changes'].items():
            self.assertEqual((self.output / relative).read_bytes(), (self.diagnostic / relative).read_bytes())
            self.assertEqual(BASE.sha(self.output / relative), transition['after_sha256'])
        self.assertEqual(self.manifest['diagnostic_proof'], self.diag_manifest['diagnostic_proof'])
        self.assertEqual(self.manifest['diagnostic_patch_order'], self.diag_manifest['diagnostic_patch_order'])

    def test_source_preimage_and_postimage_hashes_match_the_proof(self):
        for relative, transition in self.proof['metadata_patch']['source_changes'].items():
            self.assertEqual(BASE.sha(self.diagnostic / relative), transition['before_sha256'])
            self.assertEqual(BASE.sha(self.output / relative), transition['after_sha256'])

    def test_metadata_patch_applies_once_and_second_application_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615meta-once-') as temporary:
            root = Path(temporary)
            for relative in (META.PLIST, META.CPP):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((self.diagnostic / relative).read_bytes())
            META.apply_metadata(root, self.proof)
            after = {relative: (root / relative).read_bytes() for relative in (META.PLIST, META.CPP)}
            with self.assertRaisesRegex(RuntimeError, 'Input changed'):
                META.apply_metadata(root, self.proof)
            self.assertEqual(after, {relative: (root / relative).read_bytes() for relative in after})

    def test_metadata_patch_helper_checks_preimage_and_postimage(self):
        for mutation in ('preimage', 'postimage'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                    prefix='nvrm615meta-hash-') as temporary:
                root = Path(temporary)
                for relative in (META.PLIST, META.CPP):
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes((self.diagnostic / relative).read_bytes())
                proof = copy.deepcopy(self.proof)
                if mutation == 'preimage':
                    target = root / META.PLIST
                    target.write_bytes(target.read_bytes() + b'\n')
                    before = target.read_bytes()
                else:
                    proof['metadata_patch']['source_changes'][META.PLIST]['after_sha256'] = '0' * 64
                with self.assertRaisesRegex(RuntimeError, 'Input changed'):
                    META.apply_metadata(root, proof)
                if mutation == 'preimage':
                    self.assertEqual((root / META.PLIST).read_bytes(), before)

    def test_source_changes_and_extra_headers_invalidate_preparation(self):
        for relative in (META.PLIST, META.CPP):
            path = self.output / relative
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'\n/* changed fixture */\n')
                with self.subTest(relative=relative), self.assertRaises(RuntimeError):
                    META.load_inputs(str(self.output))
            finally:
                path.write_bytes(original)
        extra = self.output / 'nullmoth/kexts/NVRM/extra-metadata-header.h'
        try:
            extra.write_text('/* A new input is not an allowed metadata change. */\n')
            with self.assertRaisesRegex(RuntimeError, 'inventory changed'):
                META.load_inputs(str(self.output))
        finally:
            extra.unlink()

    def test_role_status_scope_and_hash_manifest_changes_are_rejected(self):
        path = self.output / 'inputs.json'
        original = path.read_bytes()
        mutations = [dict(format=2), dict(build_kind=DIAG.KIND), dict(experimental=False),
                     dict(metadata_enabled=False), dict(metadata_version='0.1'),
                     dict(metadata_patch_order=[]), dict(metadata_recipe_sha256={}),
                     dict(metadata_manifest_sha256='0' * 64), dict(diagnostic_manifest_sha256='0' * 64)]
        try:
            for mutation in mutations:
                value = copy.deepcopy(self.manifest)
                value.update(mutation)
                BASE.save(path, value)
                with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                    META.load_inputs(str(self.output))
        finally:
            path.write_bytes(original)

    def test_preserved_evidence_and_recipe_tampering_are_rejected(self):
        for name in ('metadata-manifest.json', 'diagnostic-inputs.json', 'baseline-inputs.json'):
            path = self.output / name
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'\n')
                with self.subTest(name=name), self.assertRaises(RuntimeError):
                    META.load_inputs(str(self.output))
            finally:
                path.write_bytes(original)
        path = META.HERE / 'build.py'
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'\n# changed isolated fixture recipe\n')
            with self.assertRaisesRegex(RuntimeError, 'recipe or original diagnostic provenance changed'):
                META.load_inputs(str(self.output))
        finally:
            path.write_bytes(original)

    def test_hardware_or_runtime_approval_cannot_be_added_to_the_immutable_metadata_identity(self):
        path = self.output / 'metadata-manifest.json'
        old = path.read_bytes()
        input_path = self.output / 'inputs.json'
        old_input = input_path.read_bytes()
        try:
            for key in ('hardware_approved', 'runtime_approved'):
                value = json.loads(old)
                value[key] = True
                BASE.save(path, value)
                changed = copy.deepcopy(self.manifest)
                changed['metadata_manifest_sha256'] = BASE.sha(path)
                BASE.save(input_path, changed)
                with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'experiment status changed'):
                    META.load_inputs(str(self.output))
        finally:
            path.write_bytes(old)
            input_path.write_bytes(old_input)

    def test_second_fresh_prepare_reproduces_sources_and_metadata_policy(self):
        output = self.root / 'metadata-reproduced'
        with redirect_stdout(io.StringIO()):
            META.prepare(SimpleNamespace(vendor=str(VENDOR), output=str(output)))
        second = json.loads((output / 'inputs.json').read_text())
        for key in ('format', 'build_kind', 'experimental', 'metadata_version', 'metadata_proof',
                    'metadata_patch_order', 'diagnostic_proof', 'diagnostic_patch_order',
                    'source_date_epoch', 'patch_order', 'nullmoth_sha256', 'vendor_sha256',
                    'recipe_sha256', 'diagnostic_recipe_sha256', 'metadata_recipe_sha256'):
            self.assertEqual(second[key], self.manifest[key], key)
        self.assertEqual(second['metadata_manifest_sha256'], BASE.sha(output / 'metadata-manifest.json'))
        self.assertNotEqual(second['diagnostic_manifest_sha256'], self.manifest['diagnostic_manifest_sha256'])

    def test_existing_diagnostic_harness_runs_all_twenty_scenarios_on_metadata_sources(self):
        if not sys.platform.startswith('linux'):
            self.skipTest('Native model regression is explicitly a Linux check')
        result = subprocess.run([sys.executable, str(REPO / 'tools/test_yuv420_diagnostic.py'),
                                 '--prepared-build', str(self.output)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for scenario in ('off', 'on', 'sw', 'other', 'end', 'normalerror', 'ioerror', 'bound',
                         'oom1', 'oom2', 'longinfo', 'claim', 'only30', 'mixed', 'diagempty',
                         'bound30', 'unselected', 'off30', 'ioerrorfirst', 'normalretry'):
            self.assertIn('PASS ' + scenario + ':', result.stdout)
        self.assertIn('full vendor syntax and format check: kapi/src/nvkms-kapi.c', result.stdout)
        self.assertIn('full vendor syntax and format check: src/nvkms-modepool.c', result.stdout)


def main():
    global VENDOR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', type=Path)
    args, remaining = parser.parse_known_args()
    if args.vendor:
        VENDOR = args.vendor.expanduser().resolve()
    PreparedMetadataTests.__unittest_skip__ = VENDOR is None
    PreparedMetadataTests.__unittest_skip_why__ = 'Use --vendor for real pinned-source integration checks'
    unittest.main(argv=[sys.argv[0], *remaining])


if __name__ == '__main__':
    main()
