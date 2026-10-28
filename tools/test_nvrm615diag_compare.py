#!/usr/bin/env python3
"""Regress the read-only diagnostic comparison with small synthetic artifacts.

These model build manifests, Mach-O headers, archives and tool transcripts. They
are not a completed Darwin NVRM build or a comparison of the user's binaries.
"""
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'build/nvrm615diag/compare.py'
SPEC = importlib.util.spec_from_file_location('nvrm615diag_comparison_tested', SCRIPT)
COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n')


def macho(tag):
    return struct.pack('<8I', 0xFEEDFACF, 0x01000007, 3, 1, 0, 0, 0, 0) + tag.encode()


def archive(path, members, timestamp=0):
    result = bytearray(b'!<arch>\n')
    for name, payload in [('__.SYMDEF', b'fixture index'), *members]:
        raw_name = name + '/'
        if len(raw_name) > 16:
            raw_name = '#1/' + str(len(name))
            payload = name.encode() + payload
        header = ('%-16s%-12d%-6d%-6d%-8s%-10d`\n' %
                  (raw_name, timestamp, 0, 0, '100644', len(payload))).encode()
        assert len(header) == 60
        result.extend(header)
        result.extend(payload)
        if len(payload) & 1:
            result.extend(b'\n')
    path.write_bytes(result)


class Fixture:
    def __init__(self, root):
        self.baseline = root / 'reference-005'
        self.candidate = root / 'yuv420-diag-001'
        self.expected = ['ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-kapi.o',
                         'ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-modepool.o',
                         'glue/os-nvkms-xnu.o']
        source_changes = {
            'ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c': b'kapi',
            'ogkm/src/nvidia-modeset/src/nvkms-modepool.c': b'modepool',
            'nullmoth/kexts/NVRM/os-nvkms-xnu.cpp': b'hook',
        }
        self.proof = {'expected_changed_objects': self.expected,
                      'reference_recipe_sha256': {'build.py': 'a' * 64},
                      'allowed_extra_exports': ['_nvkms_yuv420_diag_claim'],
                      'source_changes': {name: {'before_sha256': digest(data),
                                               'after_sha256': digest(data + b' diagnostic')}
                                         for name, data in source_changes.items()}}
        self.manifests = {}
        for build, diagnostic in ((self.baseline, False), (self.candidate, True)):
            build.mkdir()
            manifest = {'format': 2 if diagnostic else 1, 'output': str(build),
                        'nullmoth_commit': COMPARE.NULLMOTH, 'nvidia_commit': COMPARE.NVIDIA,
                        'nvidia_tag': '615.78.08', 'source_date_epoch': 123,
                        'patch_proof_format': 2, 'patch_order': ['four NVKMS patches', 'timer patch'],
                        'preserved_sources_sha256': {'fsp.c': 'f' * 64},
                        'diagnostic_enabled': diagnostic,
                        'recipe_sha256': self.proof['reference_recipe_sha256'],
                        'nullmoth_sha256': {}, 'vendor_sha256': {}}
            if diagnostic:
                manifest['build_kind'] = 'yuv420-ab-experiment'
                before = copy.deepcopy(manifest)
                before.update(format=1, diagnostic_enabled=False)
                before.pop('build_kind')
            for name, data in {**source_changes, 'ogkm/unchanged.c': b'unchanged'}.items():
                path = build / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data + (b' diagnostic' if diagnostic and name in source_changes else b''))
                folder, relative = name.split('/', 1)
                key = 'nullmoth_sha256' if folder == 'nullmoth' else 'vendor_sha256'
                manifest[key][relative] = digest(path.read_bytes())
                if diagnostic:
                    before[key][relative] = digest(data)
            if diagnostic:
                save(build / 'baseline-inputs.json', before)
                manifest['diagnostic_proof'] = self.proof
                manifest['baseline_manifest_sha256'] = digest((build / 'baseline-inputs.json').read_bytes())
            save(build / 'inputs.json', manifest)
            self.manifests[build] = manifest
            commands = []
            for component, names in (('nvidia', ['rm.o']),
                                     ('nvidia-modeset', ['nvkms-kapi.o', 'nvkms-modepool.o', 'unrelated.o'])):
                out = build / 'ogkm/src' / component / '_out/Darwin_x86_64'
                out.mkdir(parents=True)
                objects = ['_out/Darwin_x86_64/' + name for name in names]
                save(build / (component + '-objects.json'), objects)
                rows = []
                for name in names:
                    rel = 'ogkm/src/' + component + '/_out/Darwin_x86_64/' + name
                    payload = macho(name + (' DIAG' if diagnostic and rel in self.expected else ' SAME'))
                    (out / name).write_bytes(payload)
                    rows.append('/usr/bin/clang -O2 -I' + str(build / 'ogkm/include') +
                                ' -ffile-prefix-map=' + str(build) + '=/nvrm-reference'
                                ' -c src/' + name[:-2] + '.c -o _out/Darwin_x86_64/' + name)
                (out / 'compile_cmds.sh').write_text('\n'.join(rows) + '\n')
                archive(out / COMPARE.COMPONENTS[component], [(name, (out / name).read_bytes()) for name in names])
            for stem in COMPARE.BUILD.LINK_OBJECTS:
                path = build / 'glue' / (stem + '.o')
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(macho(stem + (' DIAG' if diagnostic and stem == 'os-nvkms-xnu' else ' SAME')))
                commands.append({'log': 'glue-' + stem, 'cwd': str(build / 'nullmoth/kexts/NVRM'),
                                 'argv': ['/usr/bin/clang++', '-O2', '-c', str(build / 'nullmoth' / (stem + '.cpp')),
                                          '-o', str(path)]})
            commands.append({'log': 'link', 'cwd': str(build),
                             'argv': ['/usr/bin/clang++', '-kext', str(build / 'glue/NVRM.o'),
                                      '-o', str(build / 'NVRM.kext/Contents/MacOS/NVRM')]})
            (build / 'commands.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in commands))
            preflight = {'status': 'passed', 'tools': {'clang': {'path': '/usr/bin/clang', 'sha256': 'c' * 64}},
                         'sdk': {'path': '/SDK', 'kernel_headers': '/SDK/Kernel', 'version': '26.5',
                                 'key_header_sha256': {'header.h': 'd' * 64}}}
            save(build / 'preflight/preflight.json', preflight)
            bundle = build / 'NVRM.kext/Contents/MacOS/NVRM'
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(b'binary diagnostic' if diagnostic else b'binary baseline')
            (bundle.parent.parent / 'Info.plist').write_bytes(b'fixture unchanged plist')
            self.refresh_artifacts(build)

    def refresh_artifacts(self, build):
        mapping = {path.relative_to(build).as_posix(): digest(path.read_bytes())
                   for path in build.rglob('*') if path.is_file()
                   and (path.suffix in ('.o', '.a') or build / 'NVRM.kext' in path.parents)}
        save(build / 'artifacts.json', {'files': mapping, 'diagnostic_enabled': build == self.candidate})

    def inspect(self):
        return COMPARE.inspect_pair(self.baseline, self.candidate, self.proof)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nvrm615diag-compare-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)

    def rejected(self, text):
        report = self.fixture.inspect()
        self.assertEqual(report['status'], 'rejected', report)
        self.assertIn(text, '\n'.join(report['static_rejections']))
        return report

    def test_exact_three_object_fixture_changes_require_review_not_load_approval(self):
        before = {root: {path.relative_to(root).as_posix(): digest(path.read_bytes())
                         for path in root.rglob('*') if path.is_file()}
                  for root in (self.fixture.baseline, self.fixture.candidate)}
        report = self.fixture.inspect()
        self.assertEqual(report['status'], 'review_required', report['static_rejections'])
        self.assertEqual(set(report['object_changes']), set(self.fixture.expected))
        self.assertEqual(report['archive_changes']['nvidia']['changed_object_members'], [])
        self.assertEqual(report['archive_changes']['nvidia-modeset']['changed_object_members'],
                         ['nvkms-kapi.o', 'nvkms-modepool.o'])
        for root, mapping in before.items():
            self.assertEqual(mapping, {path.relative_to(root).as_posix(): digest(path.read_bytes())
                                     for path in root.rglob('*') if path.is_file()})

    def test_unexpected_unchanged_object_difference_is_a_hard_rejection(self):
        path = self.fixture.candidate / 'glue/NVRM.o'
        path.write_bytes(macho('unexpected'))
        self.fixture.refresh_artifacts(self.fixture.candidate)
        report = self.rejected('Object changes are not exactly')
        self.assertEqual(report['unexpected_object_changes'], ['glue/NVRM.o'])

    def test_missing_expected_change_is_not_silent_success(self):
        name = self.fixture.expected[-1]
        (self.fixture.candidate / name).write_bytes((self.fixture.baseline / name).read_bytes())
        self.fixture.refresh_artifacts(self.fixture.candidate)
        report = self.rejected('Object changes are not exactly')
        self.assertEqual(report['expected_but_unchanged_objects'], [name])

    def test_artifact_tampering_or_unrecorded_objects_are_rejected(self):
        path = self.fixture.candidate / 'glue/NVRM.o'
        path.write_bytes(macho('not recorded'))
        self.rejected('Recorded input/artifact hash differs')
        self.fixture.refresh_artifacts(self.fixture.candidate)
        (self.fixture.candidate / 'rogue.o').write_bytes(macho('rogue'))
        self.rejected('Actual object/archive/bundle inventory disagrees')

    def test_missing_objects_and_inventory_changes_are_rejected(self):
        path = self.fixture.candidate / 'glue/NVRM.o'
        path.unlink()
        self.fixture.refresh_artifacts(self.fixture.candidate)
        self.rejected('Object inventory differs')

    def test_stale_archive_member_is_rejected_even_if_archive_hash_is_updated(self):
        out = self.fixture.candidate / 'ogkm/src/nvidia-modeset/_out/Darwin_x86_64'
        names = ['nvkms-kapi.o', 'nvkms-modepool.o', 'unrelated.o']
        archive(out / 'libnvkms.a', [(name, macho('stale') if name == 'nvkms-kapi.o'
                                    else (out / name).read_bytes()) for name in names])
        self.fixture.refresh_artifacts(self.fixture.candidate)
        self.rejected('Archive member is stale/different')

    def test_rm_member_change_is_rejected(self):
        out = self.fixture.candidate / 'ogkm/src/nvidia/_out/Darwin_x86_64'
        (out / 'rm.o').write_bytes(macho('changed RM'))
        archive(out / 'libnvkernel.a', [('rm.o', (out / 'rm.o').read_bytes())])
        self.fixture.refresh_artifacts(self.fixture.candidate)
        self.rejected('Archive member changes exceed/miss')

    def test_archive_container_timestamp_difference_is_separate_from_member_bytes(self):
        out = self.fixture.candidate / 'ogkm/src/nvidia/_out/Darwin_x86_64'
        archive(out / 'libnvkernel.a', [('rm.o', (out / 'rm.o').read_bytes())], timestamp=987654)
        self.fixture.refresh_artifacts(self.fixture.candidate)
        self.assertEqual(self.fixture.inspect()['status'], 'review_required')

    def test_wrong_role_source_delta_or_historical_recipe_is_rejected(self):
        path = self.fixture.candidate / 'inputs.json'
        original = COMPARE.read_json(path)
        for field, value, message in (('diagnostic_enabled', False, 'Wrong pinned build role'),
                                      ('build_kind', 'reference', 'Wrong pinned build role')):
            changed = dict(original)
            changed[field] = value
            save(path, changed)
            self.rejected(message)
        save(path, original)
        self.fixture.proof['reference_recipe_sha256'] = {'build.py': 'e' * 64}
        original['diagnostic_proof'] = self.fixture.proof
        save(path, original)
        self.rejected('Baseline recipe is not')

    def test_source_hash_tampering_and_extra_headers_are_rejected(self):
        path = self.fixture.candidate / 'ogkm/unchanged.c'
        path.write_text('changed source')
        self.rejected('Recorded input/artifact hash differs')
        path.write_bytes(b'unchanged')
        (self.fixture.candidate / 'ogkm/extra.h').write_text('extra include input')
        self.rejected('Unexpected source/header inventory')

    def test_different_source_basis_is_not_accepted_as_the_same_pins(self):
        path = self.fixture.candidate / 'baseline-inputs.json'
        data = COMPARE.read_json(path)
        data['vendor_sha256']['unchanged.c'] = 'a' * 64
        save(path, data)
        inputs = COMPARE.read_json(self.fixture.candidate / 'inputs.json')
        inputs['baseline_manifest_sha256'] = digest(path.read_bytes())
        save(self.fixture.candidate / 'inputs.json', inputs)
        self.rejected('Candidate initial source basis differs')

    def test_toolchain_and_header_changes_are_rejected(self):
        path = self.fixture.candidate / 'preflight/preflight.json'
        data = COMPARE.read_json(path)
        data['tools']['clang']['sha256'] = 'e' * 64
        save(path, data)
        self.rejected('Toolchain/SDK/header evidence differs')

    def test_unexpected_compile_flags_are_rejected(self):
        path = self.fixture.candidate / 'ogkm/src/nvidia/_out/Darwin_x86_64/compile_cmds.sh'
        path.write_text(path.read_text().replace('-O2', '-O3'))
        self.rejected('Compile/link command inventory or flags differ')

    def test_only_owned_build_root_paths_are_normalized(self):
        root = self.fixture.baseline
        self.assertEqual(COMPARE.normalize_argument('-I' + str(root) + '/include', root), '-I@BUILD/include')
        self.assertEqual(COMPARE.normalize_argument('-ffile-prefix-map=' + str(root) + '=/nvrm-reference', root),
                         '-ffile-prefix-map=@BUILD=/nvrm-reference')
        for argument in (str(root) + '-evil/include', '-DVALUE=' + str(root), '/other' + str(root)):
            self.assertEqual(COMPARE.normalize_argument(argument, root), argument)

    def test_output_cannot_enter_or_overwrite_existing_builds(self):
        for path in (self.fixture.baseline / 'audit', self.fixture.candidate / 'audit',
                     self.fixture.baseline, Path('/Library/experiment'), self.root):
            with self.subTest(path=path), self.assertRaises(ValueError):
                COMPARE.new_output(str(path), (self.fixture.baseline, self.fixture.candidate))
        unrelated = self.root / 'other-build'
        unrelated.mkdir()
        (unrelated / 'inputs.json').write_text('{}')
        with self.assertRaises(ValueError):
            COMPARE.new_output(str(unrelated / 'audit'), (self.fixture.baseline, self.fixture.candidate))

    def run_simulated_mac_cli(self, child):
        output = self.root / 'report'
        argv = ['compare.py', '--baseline-build', str(self.fixture.baseline),
                '--candidate-build', str(self.fixture.candidate), '--output', str(output)]
        native_read = COMPARE.read_json
        def read(path):
            return self.fixture.proof if Path(path) == COMPARE.HERE / 'proof.json' else native_read(path)
        with mock.patch.object(COMPARE.sys, 'platform', 'darwin'), \
                mock.patch.object(COMPARE.os, 'geteuid', return_value=1000), \
                mock.patch.object(COMPARE.sys, 'argv', argv), \
                mock.patch.object(COMPARE, 'read_json', side_effect=read), \
                mock.patch.object(COMPARE, 'run_child', side_effect=child), redirect_stdout(io.StringIO()):
            result = COMPARE.main()
        return result, COMPARE.read_json(output / 'comparison.json')

    def test_simulated_mac_cli_invokes_both_audits_without_load_approval(self):
        calls = []
        def child(argv, output, label, report):
            calls.append((argv, label))
            report['commands'].append({'fixture': label})
            if label == 'structural':
                save(output / 'structural/comparison.json', {'comparison': {'static_rejections': [],
                     'new_imports': [], 'missing_exports': [], 'extra_exports': ['_nvkms_yuv420_diag_claim']}})
            if label == 'instructions':
                save(output / 'instructions/audit.json', {'status': 'complete', 'counts': {'examined': 1,
                     'identical': 0, 'different': 1, 'not_comparable': 0}})
            return 0
        result, report = self.run_simulated_mac_cli(child)
        self.assertEqual(result, 0)
        self.assertEqual(report['status'], 'review_required')
        self.assertEqual([label for _, label in calls], ['structural', 'instructions'])
        self.assertIn(str(self.fixture.baseline / 'NVRM.kext'), list(map(str, calls[0][0])))
        self.assertIn(str(self.fixture.candidate / 'NVRM.kext/Contents/MacOS/NVRM'), list(map(str, calls[1][0])))

    def test_simulated_child_failure_is_a_rejection(self):
        result, report = self.run_simulated_mac_cli(lambda *args: 2)
        self.assertEqual(result, 1)
        self.assertEqual(report['status'], 'rejected')
        self.assertIn('Final structural comparison rejected or failed', report['static_rejections'])
        self.assertIn('Final instruction audit failed operationally', report['static_rejections'])

    def test_unexpected_final_exports_are_rejected_despite_successful_tool_exit(self):
        def child(argv, output, label, report):
            if label == 'structural':
                save(output / 'structural/comparison.json', {'comparison': {'static_rejections': [],
                     'new_imports': [], 'missing_exports': [],
                     'extra_exports': ['_nvkms_yuv420_diag_claim', '_unexpected_extra']}})
            if label == 'instructions':
                save(output / 'instructions/audit.json', {'status': 'complete', 'counts': {'examined': 1,
                     'identical': 0, 'different': 1, 'not_comparable': 0}})
            return 0
        result, report = self.run_simulated_mac_cli(child)
        self.assertEqual(result, 1)
        self.assertIn('Final symbol/import/export changes exceed or miss the exact diagnostic scope',
                      report['static_rejections'])

    def test_mutation_during_simulated_tools_is_detected(self):
        def child(argv, output, label, report):
            if label == 'structural':
                save(output / 'structural/comparison.json', {'comparison': {'static_rejections': [],
                     'new_imports': [], 'missing_exports': [], 'extra_exports': ['_nvkms_yuv420_diag_claim']}})
            if label == 'instructions':
                (self.fixture.baseline / 'glue/NVRM.o').write_bytes(macho('changed during tools'))
            if label == 'instructions':
                save(output / 'instructions/audit.json', {'status': 'complete', 'counts': {'examined': 1,
                     'identical': 0, 'different': 1, 'not_comparable': 0}})
            return 0
        result, report = self.run_simulated_mac_cli(child)
        self.assertEqual(result, 1)
        self.assertIn('Input evidence changed or failed validation during the comparison', report['static_rejections'])

    def test_nonsemantic_metadata_mutation_during_children_is_detected(self):
        def child(argv, output, label, report):
            if label == 'structural':
                save(output / 'structural/comparison.json', {'comparison': {'static_rejections': [],
                     'new_imports': [], 'missing_exports': [], 'extra_exports': ['_nvkms_yuv420_diag_claim']}})
            if label == 'instructions':
                save(output / 'instructions/audit.json', {'status': 'complete', 'counts': {'examined': 1}})
                path = self.fixture.baseline / 'commands.jsonl'
                path.write_text(path.read_text() + '\n')
            return 0
        result, report = self.run_simulated_mac_cli(child)
        self.assertEqual(result, 1)
        self.assertIn('Input evidence changed or failed validation during the comparison', report['static_rejections'])

    def test_linux_cli_stops_before_creating_output(self):
        output = self.root / 'report'
        argv = ['compare.py', '--baseline-build', str(self.fixture.baseline),
                '--candidate-build', str(self.fixture.candidate), '--output', str(output)]
        with mock.patch.object(COMPARE.sys, 'platform', 'linux'), mock.patch.object(COMPARE.sys, 'argv', argv), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            COMPARE.main()
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
