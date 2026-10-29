#!/usr/bin/env python3
"""Regress the explicit metadata comparison policy using synthetic artifacts.

No fixture is a built Darwin NVRM or evidence of runtime/ABI compatibility.
The existing object/archive fixture and real KMOD reader are reused together.
"""
from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


COMPARE = module('nvrm615meta_compare_tested', ROOT / 'build/nvrm615meta/compare.py')
DIAG_FIX = module('nvrm615meta_old_comparison_fixtures', ROOT / 'tools/test_nvrm615diag_compare.py')
KMOD_FIX = module('nvrm615meta_record_fixtures', ROOT / 'tools/test_nvrm615meta_kmod.py')
save, digest = DIAG_FIX.save, DIAG_FIX.digest


class Fixture(DIAG_FIX.Fixture):
    def __init__(self, root):
        super().__init__(root)
        self.diagnostic_proof = copy.deepcopy(self.proof)
        self.diagnostic_proof['patch_order'] = ['baseline', 'diagnostic', 'trigger']
        self.proof = {'format': 1, 'metadata_parent_commit': COMPARE.PARENT,
            'version': COMPARE.VERSION, 'diagnostic_proof': self.diagnostic_proof,
            'expected_changed_objects': list(COMPARE.EXPECTED_OBJECTS),
            'allowed_extra_exports': self.diagnostic_proof['allowed_extra_exports'],
            'reference_recipe_sha256': self.diagnostic_proof['reference_recipe_sha256'],
            'patch_order': self.diagnostic_proof['patch_order'] + ['metadata:254-metadata.patch'],
            'source_changes': copy.deepcopy(self.diagnostic_proof['source_changes'])}
        before = COMPARE.DIAG.read_json(self.candidate / 'baseline-inputs.json')
        diagnostic = copy.deepcopy(self.manifests[self.candidate])
        metadata_changes = {}
        for root_path, manifest in ((self.baseline, self.manifests[self.baseline]),
                                    (self.candidate, self.manifests[self.candidate])):
            version = '0.1' if root_path == self.baseline else COMPARE.VERSION
            values = {'CFBundleIdentifier': 'com.nullmoth.NVRM', 'CFBundleVersion': version,
                'CFBundleShortVersionString': version, 'OSBundleLibraries': {'com.apple.kpi.iokit': '8.0'},
                'IOKitPersonalities': {'NVRM': {'Feature': True}}}
            plist = root_path / COMPARE.PLIST_SOURCE
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_bytes(plistlib.dumps(values, sort_keys=False))
            (root_path / COMPARE.CPP_SOURCE).write_text(
                'KMOD_EXPLICIT_DECL(com.nullmoth.NVRM, "' + version + '", _start, _stop)\n')
            for relative in (COMPARE.PLIST_SOURCE, COMPARE.CPP_SOURCE):
                path = root_path / relative
                key = relative[len('nullmoth/'):]
                manifest['nullmoth_sha256'][key] = digest(path.read_bytes())
            (root_path / COMPARE.PLIST_BUNDLE).write_bytes(plist.read_bytes())
            (root_path / 'NVRM.kext/Contents/MacOS/NVRM').write_bytes(KMOD_FIX.fixture(version=version))
        for relative in (COMPARE.PLIST_SOURCE, COMPARE.CPP_SOURCE):
            key = relative[len('nullmoth/'):]
            old = self.manifests[self.baseline]['nullmoth_sha256'][key]
            new = self.manifests[self.candidate]['nullmoth_sha256'][key]
            metadata_changes[relative] = {'before_sha256': old, 'after_sha256': new}
            before['nullmoth_sha256'][key] = old
            diagnostic['nullmoth_sha256'][key] = old
        save(self.baseline / 'inputs.json', self.manifests[self.baseline])
        save(self.candidate / 'baseline-inputs.json', before)
        diagnostic.update(diagnostic_proof=self.diagnostic_proof,
            diagnostic_patch_order=self.diagnostic_proof['patch_order'],
            baseline_manifest_sha256=digest((self.candidate / 'baseline-inputs.json').read_bytes()))
        save(self.candidate / 'diagnostic-inputs.json', diagnostic)
        self.proof['metadata_patch'] = {'path': 'patches/254-metadata.patch',
            'sha256': 'b' * 64, 'source_changes': metadata_changes}
        self.proof['source_changes'].update(metadata_changes)
        manifest = self.manifests[self.candidate]
        manifest.update(format=3, build_kind=COMPARE.KIND, experimental=True, metadata_enabled=True,
            metadata_version=COMPARE.VERSION, metadata_proof=self.proof,
            diagnostic_proof=self.diagnostic_proof,
            diagnostic_patch_order=self.diagnostic_proof['patch_order'],
            baseline_manifest_sha256=digest((self.candidate / 'baseline-inputs.json').read_bytes()),
            diagnostic_manifest_sha256=digest((self.candidate / 'diagnostic-inputs.json').read_bytes()),
            metadata_patch_order=self.proof['patch_order'])
        metadata_manifest = {'format': 1, 'build_kind': COMPARE.KIND, 'experimental': True,
            'runtime_approved': False, 'hardware_approved': False,
            'version': COMPARE.VERSION, 'metadata_proof': self.proof,
            'diagnostic_manifest_sha256': manifest['diagnostic_manifest_sha256'],
            'baseline_manifest_sha256': manifest['baseline_manifest_sha256'],
            'metadata_patch_order': self.proof['patch_order']}
        save(self.candidate / 'metadata-manifest.json', metadata_manifest)
        manifest['metadata_manifest_sha256'] = digest((self.candidate / 'metadata-manifest.json').read_bytes())
        save(self.candidate / 'inputs.json', manifest)
        (self.candidate / 'glue/NVRM.o').write_bytes(DIAG_FIX.macho('NVRM metadata'))
        self.refresh_artifacts(self.baseline)
        self.refresh_artifacts(self.candidate)

    def refresh_artifacts(self, build):
        super().refresh_artifacts(build)
        report = COMPARE.DIAG.read_json(build / 'artifacts.json')
        report['metadata_version'] = COMPARE.VERSION if build == self.candidate else '0.1'
        save(build / 'artifacts.json', report)

    def record_changed_metadata_source(self, relative):
        """Model self-consistent hashes that must not authorize extra source edits."""
        after = digest((self.candidate / relative).read_bytes())
        self.proof['metadata_patch']['source_changes'][relative]['after_sha256'] = after
        self.proof['source_changes'][relative]['after_sha256'] = after
        primary = self.candidate / 'inputs.json'
        manifest = COMPARE.DIAG.read_json(primary)
        manifest['nullmoth_sha256'][relative[len('nullmoth/'):]] = after
        manifest['metadata_proof'] = self.proof
        metadata_path = self.candidate / 'metadata-manifest.json'
        metadata = COMPARE.DIAG.read_json(metadata_path)
        metadata['metadata_proof'] = self.proof
        save(metadata_path, metadata)
        manifest['metadata_manifest_sha256'] = digest(metadata_path.read_bytes())
        save(primary, manifest)
        self.refresh_artifacts(self.candidate)

    def inspect(self):
        with mock.patch.object(COMPARE, 'historical_diagnostic_proof', return_value=self.diagnostic_proof):
            return COMPARE.inspect_pair(self.baseline, self.candidate, self.proof)


class MetadataComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nvrm615meta-compare-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)

    def rejected(self, reason):
        report = self.fixture.inspect()
        self.assertEqual(report['status'], 'rejected', report)
        self.assertIn(reason, '\n'.join(report['static_rejections']))
        return report

    def test_exact_four_object_policy_checks_real_kmod_and_full_plist(self):
        original = {root: {path.relative_to(root).as_posix(): digest(path.read_bytes())
                           for path in root.rglob('*') if path.is_file()}
                    for root in (self.fixture.baseline, self.fixture.candidate)}
        report = self.fixture.inspect()
        self.assertEqual(report['status'], 'review_required', report['static_rejections'])
        self.assertEqual(report['object_changes'], sorted(COMPARE.EXPECTED_OBJECTS))
        self.assertEqual(report['object_counts']['changed'], 4)
        extra = report['candidate_policy_evidence']
        self.assertEqual(extra['candidate_kmod']['version'], '2.5.4')
        self.assertEqual(extra['baseline_kmod']['version'], '0.1')
        self.assertEqual(extra['plist_comparison']['changed_keys'], list(COMPARE.VERSION_KEYS))
        self.assertFalse(extra['hardware_approved'])
        self.assertFalse(extra['runtime_approved'])
        self.assertTrue(extra['source_delta_verified'])
        self.assertEqual(set(extra['metadata_source_delta']), {COMPARE.PLIST_SOURCE, COMPARE.CPP_SOURCE})
        for root, values in original.items():
            self.assertEqual(values, {path.relative_to(root).as_posix(): digest(path.read_bytes())
                                      for path in root.rglob('*') if path.is_file()})

    def test_a_fifth_changed_object_is_rejected(self):
        (self.fixture.candidate / 'glue/os-xnu.o').write_bytes(DIAG_FIX.macho('unexpected fifth'))
        self.fixture.refresh_artifacts(self.fixture.candidate)
        report = self.rejected('Object changes are not exactly')
        self.assertEqual(report['unexpected_object_changes'], ['glue/os-xnu.o'])

    def test_unchanged_nvrm_object_is_not_a_verified_metadata_rebuild(self):
        name = 'glue/NVRM.o'
        (self.fixture.candidate / name).write_bytes((self.fixture.baseline / name).read_bytes())
        self.fixture.refresh_artifacts(self.fixture.candidate)
        report = self.rejected('Object changes are not exactly')
        self.assertEqual(report['expected_but_unchanged_objects'], [name])

    def test_old_diagnostic_entry_point_rejects_metadata_role_without_opt_in(self):
        report = COMPARE.DIAG.inspect_pair(self.fixture.baseline, self.fixture.candidate, self.fixture.proof)
        self.assertEqual(report['status'], 'rejected')
        self.assertIn('Wrong pinned build role', '\n'.join(report['static_rejections']))

    def test_old_diagnostic_policy_still_rejects_nvrm_or_plist_changes(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615meta-old-scope-') as temporary:
            fixture = DIAG_FIX.Fixture(Path(temporary))
            (fixture.candidate / 'glue/NVRM.o').write_bytes(DIAG_FIX.macho('not allowed'))
            (fixture.candidate / 'NVRM.kext/Contents/Info.plist').write_bytes(b'unexpected metadata')
            fixture.refresh_artifacts(fixture.candidate)
            report = fixture.inspect()
            self.assertEqual(report['status'], 'rejected')
            self.assertIn('Object changes are not exactly', '\n'.join(report['static_rejections']))
            self.assertIn('Unexpected nonobject/bundle metadata artifact changes', '\n'.join(report['static_rejections']))

    def test_wrong_embedded_kmod_version_is_rejected_despite_decoy_strings(self):
        binary = self.fixture.candidate / 'NVRM.kext/Contents/MacOS/NVRM'
        raw = KMOD_FIX.fixture(version='0.1')
        self.assertIn(b'2.5.4\0', raw)
        binary.write_bytes(raw)
        self.fixture.refresh_artifacts(self.fixture.candidate)
        self.rejected('Embedded KMOD version mismatch')

    def test_wrong_kmod_name_or_missing_record_is_rejected(self):
        binary = self.fixture.candidate / 'NVRM.kext/Contents/MacOS/NVRM'
        for values, reason in (({'name': 'com.other.Driver'}, 'name mismatch'),
                               ({'symbol_name': '_decoy'}, 'unique defined external')):
            binary.write_bytes(KMOD_FIX.fixture(**values))
            self.fixture.refresh_artifacts(self.fixture.candidate)
            with self.subTest(values=values):
                self.rejected(reason)

    def test_reformatted_or_unreviewed_bundle_plist_bytes_are_rejected(self):
        path = self.fixture.candidate / COMPARE.PLIST_BUNDLE
        values = plistlib.loads(path.read_bytes())
        path.write_bytes(plistlib.dumps(values, sort_keys=True))
        self.fixture.refresh_artifacts(self.fixture.candidate)
        self.rejected('Bundle plist bytes differ from the exact staged source')

    def test_extra_cpp_code_is_rejected_even_with_consistent_source_hashes(self):
        source = self.fixture.candidate / COMPARE.CPP_SOURCE
        source.write_bytes(source.read_bytes() + b'void unexpected_extra_code(void) {}\n')
        self.fixture.record_changed_metadata_source(COMPARE.CPP_SOURCE)
        self.rejected('Metadata source bytes exceed the exact permitted version change: ' + COMPARE.CPP_SOURCE)

    def test_plist_source_reformatting_is_rejected_even_with_consistent_bundle_and_hashes(self):
        source = self.fixture.candidate / COMPARE.PLIST_SOURCE
        source.write_bytes(source.read_bytes() + b'\n')
        (self.fixture.candidate / COMPARE.PLIST_BUNDLE).write_bytes(source.read_bytes())
        self.fixture.record_changed_metadata_source(COMPARE.PLIST_SOURCE)
        self.rejected('Metadata source bytes exceed the exact permitted version change: ' + COMPARE.PLIST_SOURCE)

    def test_only_two_typed_plist_keys_may_change(self):
        baseline = self.fixture.baseline / COMPARE.PLIST_BUNDLE
        candidate = self.fixture.candidate / COMPARE.PLIST_BUNDLE
        expected = plistlib.loads(candidate.read_bytes())
        for mutate in ('identifier', 'extra-key', 'type', 'wrong-version', 'missing'):
            values = copy.deepcopy(expected)
            if mutate == 'identifier':
                values['CFBundleIdentifier'] = 'com.other.Driver'
            elif mutate == 'extra-key':
                values['Extra'] = True
            elif mutate == 'type':
                values['IOKitPersonalities']['NVRM']['Feature'] = 1
            elif mutate == 'wrong-version':
                values['CFBundleVersion'] = '2.6.3'
            else:
                del values['CFBundleShortVersionString']
            candidate.write_bytes(plistlib.dumps(values))
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, 'two expected version keys'):
                COMPARE.compare_plists(baseline, candidate)

    def test_source_metadata_or_own_manifest_tampering_is_rejected(self):
        path = self.fixture.candidate / 'metadata-manifest.json'
        data = COMPARE.DIAG.read_json(path)
        data['hardware_approved'] = True
        save(path, data)
        primary = self.fixture.candidate / 'inputs.json'
        manifest = COMPARE.DIAG.read_json(primary)
        manifest['metadata_manifest_sha256'] = digest(path.read_bytes())
        save(primary, manifest)
        self.rejected('Own metadata manifest/status/provenance changed')

    def test_primary_experiment_flags_must_be_explicit_true_booleans(self):
        path = self.fixture.candidate / 'inputs.json'
        original = COMPARE.DIAG.read_json(path)
        for key in ('metadata_enabled', 'experimental', 'diagnostic_enabled'):
            for value in (None, False, 1):
                data = copy.deepcopy(original)
                if value is None:
                    del data[key]
                else:
                    data[key] = value
                save(path, data)
                with self.subTest(key=key, value=value):
                    self.rejected('Wrong explicit experimental metadata build role')

    def test_own_manifest_requires_both_explicit_false_approval_flags(self):
        path = self.fixture.candidate / 'metadata-manifest.json'
        original = COMPARE.DIAG.read_json(path)
        primary = self.fixture.candidate / 'inputs.json'
        manifest = COMPARE.DIAG.read_json(primary)
        self.assertIs(original['runtime_approved'], False)
        self.assertIs(original['hardware_approved'], False)
        for key in ('runtime_approved', 'hardware_approved'):
            for value in (None, True, 0):
                data = copy.deepcopy(original)
                if value is None:
                    del data[key]
                else:
                    data[key] = value
                save(path, data)
                manifest['metadata_manifest_sha256'] = digest(path.read_bytes())
                save(primary, manifest)
                with self.subTest(key=key, value=value):
                    self.rejected('Own metadata manifest/status/provenance changed')

    def test_preserved_diagnostic_manifest_is_bound_by_hash(self):
        path = self.fixture.candidate / 'diagnostic-inputs.json'
        path.write_text(path.read_text() + '\n')
        self.rejected('Preserved diagnostic manifest/provenance changed')

    def test_proof_cannot_expand_four_objects_even_with_edited_diagnostic_subset(self):
        self.fixture.proof['diagnostic_proof'] = copy.deepcopy(self.fixture.diagnostic_proof)
        self.fixture.proof['diagnostic_proof']['expected_changed_objects'].append('glue/os-xnu.o')
        manifest = COMPARE.DIAG.read_json(self.fixture.candidate / 'inputs.json')
        manifest['diagnostic_proof'] = self.fixture.proof['diagnostic_proof']
        save(self.fixture.candidate / 'inputs.json', manifest)
        self.rejected('not the pinned unchanged e82c663 input')

    def test_metadata_order_cannot_omit_the_original_diagnostic_sequence(self):
        self.fixture.proof['patch_order'] = ['metadata:254-metadata.patch']
        self.rejected('Metadata must follow the complete original diagnostic patch order')

    def test_preserved_diagnostic_order_is_checked_against_the_original_proof(self):
        path = self.fixture.candidate / 'diagnostic-inputs.json'
        data = COMPARE.DIAG.read_json(path)
        data['diagnostic_patch_order'] = ['omitted original patches']
        save(path, data)
        primary = self.fixture.candidate / 'inputs.json'
        manifest = COMPARE.DIAG.read_json(primary)
        manifest['diagnostic_patch_order'] = data['diagnostic_patch_order']
        manifest['diagnostic_manifest_sha256'] = digest(path.read_bytes())
        save(primary, manifest)
        self.rejected('Preserved diagnostic manifest/provenance changed')

    def run_mac_fixture(self, child):
        output = self.root / 'report'
        argv = ['metadata-compare.py', '--baseline-build', str(self.fixture.baseline),
            '--candidate-build', str(self.fixture.candidate), '--output', str(output)]
        native_read = COMPARE.DIAG.read_json
        def read(path):
            return self.fixture.proof if Path(path) == COMPARE.HERE / 'proof.json' else native_read(path)
        with mock.patch.object(COMPARE.DIAG.sys, 'platform', 'darwin'), \
                mock.patch.object(COMPARE.DIAG.os, 'geteuid', return_value=1000), \
                mock.patch.object(COMPARE.DIAG.sys, 'argv', argv), \
                mock.patch.object(COMPARE.DIAG, 'read_json', side_effect=read), \
                mock.patch.object(COMPARE, 'historical_diagnostic_proof', return_value=self.fixture.diagnostic_proof), \
                mock.patch.object(COMPARE.DIAG, 'run_child', side_effect=child), redirect_stdout(io.StringIO()):
            result = COMPARE.main()
        return result, COMPARE.DIAG.read_json(output / 'comparison.json')

    @staticmethod
    def child_reports(argv, output, label, report):
        report['commands'].append({'fixture': label})
        if label == 'structural':
            save(output / 'structural/comparison.json', {'comparison': {'static_rejections': [],
                'new_imports': [], 'missing_exports': [], 'extra_exports': ['_nvkms_yuv420_diag_claim']}})
        else:
            save(output / 'instructions/audit.json', {'status': 'complete',
                'counts': {'examined': 1, 'identical': 0, 'different': 1, 'not_comparable': 0}})
        return 0

    def test_explicit_metadata_mac_cli_reuses_both_audits_and_rechecks_evidence(self):
        result, report = self.run_mac_fixture(self.child_reports)
        self.assertEqual(result, 0, report)
        self.assertEqual(report['status'], 'review_required')
        self.assertEqual(report['candidate_policy_evidence']['profile'], COMPARE.KIND)
        self.assertEqual([row['fixture'] for row in report['commands']], ['structural', 'instructions'])

    def test_post_tool_metadata_manifest_mutation_is_not_silent_success(self):
        def child(argv, output, label, report):
            result = self.child_reports(argv, output, label, report)
            if label == 'instructions':
                path = self.fixture.candidate / 'metadata-manifest.json'
                path.write_text(path.read_text() + '\n')
            return result
        result, report = self.run_mac_fixture(child)
        self.assertEqual(result, 1)
        self.assertIn('Input evidence changed or failed validation', '\n'.join(report['static_rejections']))

    def test_linux_metadata_cli_never_creates_a_report(self):
        output = self.root / 'not-created'
        args = ['metadata-compare.py', '--baseline-build', str(self.fixture.baseline),
                '--candidate-build', str(self.fixture.candidate), '--output', str(output)]
        with mock.patch.object(COMPARE.DIAG.sys, 'platform', 'linux'), \
                mock.patch.object(COMPARE.DIAG.sys, 'argv', args), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            COMPARE.main()
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
