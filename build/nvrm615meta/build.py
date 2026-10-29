#!/usr/bin/env python3
"""Build an explicitly experimental, isolated 2.5.4-metadata YUV420 artifact.

Only staged Info.plist version keys and KMOD_EXPLICIT_DECL are patched. The
source repository, reference builds, original diagnostic and installed drivers
are never modified. No firmware, kernel collection, installation or load step.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DIAG_DIR = HERE.parent / 'nvrm615diag'
SPEC = importlib.util.spec_from_file_location('nvrm615meta_diagnostic_runner', DIAG_DIR / 'build.py')
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)
BASE = DIAG.BASE
KIND = 'yuv420-ab-metadata-experiment'
PARENT = 'e82c6637727af6e839ef9856fc9a4820b92d4fa4'
VERSION = '2.5.4'
PLIST = 'nullmoth/kexts/NVRM/Info.plist'
CPP = 'nullmoth/kexts/NVRM/NVRM.cpp'
VERSION_KEYS = ('CFBundleVersion', 'CFBundleShortVersionString')


def recipe_hashes():
    return {p.relative_to(HERE).as_posix(): BASE.sha(p) for p in sorted(HERE.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def expected_metadata_bytes(relative, before):
    """Derive the only permitted textual changes from the pinned source bytes."""
    after = before
    if relative == PLIST:
        for key in VERSION_KEYS:
            old = ('<key>' + key + '</key>\n\t<string>0.1</string>').encode()
            if after.count(old) != 1:
                BASE.fail('Expected one original plist version field: ' + key)
            after = after.replace(old, old.replace(b'0.1', VERSION.encode()))
        expected = dict(plistlib.loads(before), **{key: VERSION for key in VERSION_KEYS})
        if plistlib.loads(after) != expected:
            BASE.fail('Plist transformation must change only the two version keys')
    elif relative == CPP:
        old = b'KMOD_EXPLICIT_DECL(com.nullmoth.NVRM, "0.1", _start, _stop)'
        if before.count(old) != 1:
            BASE.fail('Expected one original explicit KMOD version parameter')
        after = before.replace(old, old.replace(b'"0.1"', b'"2.5.4"'))
    else:
        BASE.fail('Unexpected metadata source path: ' + relative)
    return after


def load_proof():
    proof = json.loads((HERE / 'proof.json').read_text())
    if (proof['format'], proof['metadata_parent_commit'], proof['version'],
            proof['nullmoth_commit'], proof['nvidia_commit']) != (
            1, PARENT, VERSION, BASE.NULLMOTH, BASE.NVIDIA):
        BASE.fail('Unexpected metadata experiment provenance')
    original = json.loads(subprocess.check_output(['git', '-C', str(REPO), 'show',
        PARENT + ':build/nvrm615diag/proof.json']))
    if proof['diagnostic_proof'] != original or DIAG.load_proof() != original:
        BASE.fail('The complete diagnostic, trigger correction and three-object profile must stay unchanged')
    if (proof['expected_changed_objects'] != original['expected_changed_objects'] + ['glue/NVRM.o']
            or proof['allowed_extra_exports'] != original['allowed_extra_exports']
            or proof['reference_recipe_sha256'] != original['reference_recipe_sha256']
            or proof['patch_order'] != original['patch_order'] + ['metadata:254-metadata.patch']):
        BASE.fail('Metadata must use its own exact four-object profile and appended patch')
    entry = proof['metadata_patch']
    if (entry['path'], entry['apply_root'], set(entry['source_changes'])) != (
            'patches/254-metadata.patch', 'nullmoth', {PLIST, CPP}):
        BASE.fail('Metadata patch scope must be exactly Info.plist and NVRM.cpp')
    path = HERE / entry['path']
    if path.is_symlink() or BASE.sha(path) != entry['sha256']:
        BASE.fail('Metadata patch hash changed')
    for relative, transition in entry['source_changes'].items():
        original_bytes = subprocess.check_output(['git', '-C', str(REPO), 'show',
            BASE.NULLMOTH + ':' + relative[len('nullmoth/'):]])
        after = expected_metadata_bytes(relative, original_bytes)
        if (hashlib.sha256(original_bytes).hexdigest(), hashlib.sha256(after).hexdigest()) != (
                transition['before_sha256'], transition['after_sha256']):
            BASE.fail('Metadata source hashes do not describe the exact permitted version change')
    if proof['source_changes'] != {**original['source_changes'], **entry['source_changes']}:
        BASE.fail('Unexpected combined metadata/diagnostic source scope')
    expected_changes = {key: {'before': '0.1', 'after': VERSION} for key in VERSION_KEYS}
    if proof['expected_plist_changes'] != expected_changes:
        BASE.fail('Only the two explicit plist version changes are permitted')
    return proof


def patched_maps(diagnostic, proof):
    maps = {key: dict(diagnostic[key]) for key in ('vendor_sha256', 'nullmoth_sha256')}
    for relative, change in proof['metadata_patch']['source_changes'].items():
        path = relative[len('nullmoth/'):]
        if maps['nullmoth_sha256'].get(path) != change['before_sha256']:
            BASE.fail('Metadata preimage differs from the pinned diagnostic: ' + relative)
        maps['nullmoth_sha256'][path] = change['after_sha256']
    return maps


def apply_metadata(out, proof):
    entry = proof['metadata_patch']
    path = HERE / entry['path']
    if BASE.sha(path) != entry['sha256']:
        BASE.fail('Metadata patch hash changed')
    BASE.verify_hashes(out, {name: change['before_sha256'] for name, change in entry['source_changes'].items()})
    DIAG.strict_patch(out / 'nullmoth', path, ('kexts/NVRM/Info.plist', 'kexts/NVRM/NVRM.cpp'))
    BASE.verify_hashes(out, {name: change['after_sha256'] for name, change in entry['source_changes'].items()})


def prepare(args):
    out = DIAG.new_output(args.output)
    proof = load_proof()
    DIAG.prepare(args)
    (out / 'inputs.json').rename(out / 'diagnostic-inputs.json')
    (out / 'diagnostic-state.json').rename(out / 'diagnostic-pre-metadata-state.json')
    diagnostic = json.loads((out / 'diagnostic-inputs.json').read_text())
    maps = patched_maps(diagnostic, proof)
    apply_metadata(out, proof)
    for folder, key in (('ogkm', 'vendor_sha256'), ('nullmoth', 'nullmoth_sha256')):
        if BASE.tree_hashes(out / folder) != maps[key]:
            BASE.fail('Metadata preparation changed unexpected files: ' + folder)
    metadata = {'format': 1, 'build_kind': KIND, 'experimental': True, 'hardware_approved': False,
                'runtime_approved': False, 'version': VERSION, 'metadata_proof': proof,
                'diagnostic_manifest_sha256': BASE.sha(out / 'diagnostic-inputs.json'),
                'baseline_manifest_sha256': BASE.sha(out / 'baseline-inputs.json'),
                'metadata_patch_order': proof['patch_order']}
    BASE.save(out / 'metadata-manifest.json', metadata)
    manifest = copy.deepcopy(diagnostic)
    manifest.update(format=3, build_kind=KIND, diagnostic_enabled=True, experimental=True,
                    metadata_enabled=True, metadata_version=VERSION,
                    purpose='experimental metadata compatibility artifact; no runtime or hardware approval',
                    metadata_policy='Isolated two-key plist and explicit KMOD version patch to 2.5.4; still experimental, not an official release',
                    metadata_proof=proof, metadata_patch_order=proof['patch_order'],
                    metadata_recipe_sha256=recipe_hashes(),
                    diagnostic_manifest_sha256=metadata['diagnostic_manifest_sha256'],
                    metadata_manifest_sha256=BASE.sha(out / 'metadata-manifest.json'), **maps)
    BASE.save(out / 'inputs.json', manifest)
    BASE.save(out / 'metadata-state.json', {'status': 'prepared', 'build_kind': KIND,
        'experimental': True, 'metadata_version': VERSION, 'runtime_approved': False, 'hardware_approved': False})
    print('Prepared isolated experimental metadata sources: ' + str(out))


def load_inputs(value):
    out = BASE.output_path(value)
    manifest = json.loads((out / 'inputs.json').read_text())
    if (manifest['format'], manifest['output'], manifest['build_kind'], manifest['diagnostic_enabled'],
            manifest['experimental'], manifest['metadata_enabled'], manifest['metadata_version'],
            manifest['nullmoth_commit'], manifest['nvidia_commit']) != (
            3, str(out), KIND, True, True, True, VERSION, BASE.NULLMOTH, BASE.NVIDIA):
        BASE.fail('Expected an explicitly experimental metadata manifest, not an earlier diagnostic')
    proof = load_proof()
    if (manifest['metadata_proof'] != proof or manifest['diagnostic_proof'] != proof['diagnostic_proof']
            or manifest['metadata_patch_order'] != proof['patch_order']
            or manifest['diagnostic_patch_order'] != proof['diagnostic_proof']['patch_order']
            or manifest['metadata_recipe_sha256'] != recipe_hashes()
            or manifest['diagnostic_recipe_sha256'] != DIAG.recipe_hashes()
            or manifest['recipe_sha256'] != BASE.recipe_hashes()):
        BASE.fail('Metadata recipe or original diagnostic provenance changed; prepare a new output')
    for name, key in (('metadata-manifest.json', 'metadata_manifest_sha256'),
                      ('diagnostic-inputs.json', 'diagnostic_manifest_sha256'),
                      ('baseline-inputs.json', 'baseline_manifest_sha256')):
        if BASE.sha(out / name) != manifest[key]:
            BASE.fail('Preserved metadata evidence changed: ' + name)
    metadata = json.loads((out / 'metadata-manifest.json').read_text())
    expected_metadata = {'format': 1, 'build_kind': KIND, 'experimental': True, 'hardware_approved': False,
        'runtime_approved': False, 'version': VERSION, 'metadata_proof': proof,
        'diagnostic_manifest_sha256': manifest['diagnostic_manifest_sha256'],
        'baseline_manifest_sha256': manifest['baseline_manifest_sha256'], 'metadata_patch_order': proof['patch_order']}
    if metadata != expected_metadata:
        BASE.fail('Metadata identity or experiment status changed')
    diagnostic = json.loads((out / 'diagnostic-inputs.json').read_text())
    baseline = json.loads((out / 'baseline-inputs.json').read_text())
    if (diagnostic['format'], diagnostic['output'], diagnostic['build_kind'], diagnostic['diagnostic_enabled'],
            diagnostic['diagnostic_proof'], baseline['format'], baseline['output'], baseline['diagnostic_enabled']) != (
            2, str(out), DIAG.KIND, True, proof['diagnostic_proof'], 1, str(out), False):
        BASE.fail('Invalid preserved diagnostic/reference source manifests')
    before = DIAG.expected_sources(baseline, proof['diagnostic_proof'])
    for key in before:
        if diagnostic[key] != before[key]:
            BASE.fail('Preserved diagnostic source map changed')
    maps = patched_maps(diagnostic, proof)
    for folder, key in (('ogkm', 'vendor_sha256'), ('nullmoth', 'nullmoth_sha256')):
        if manifest[key] != maps[key]:
            BASE.fail('Unexpected metadata source manifest delta')
        BASE.verify_hashes(out / folder, manifest[key])
        if BASE.tree_hashes(out / folder) != manifest[key]:
            BASE.fail('Metadata source inventory changed: ' + folder)
    baseline_proof = json.loads((DIAG.BASE_DIR / 'patches/patch-proof.json').read_text())
    if any(item['patch_order'] != baseline_proof['patch_order'] for item in (manifest, diagnostic, baseline)):
        BASE.fail('All five baseline patches must remain unchanged')
    BASE.verify_hashes(out / 'ogkm', baseline_proof['preserved_sources_sha256'])
    return out, manifest


def build(args):
    if platform.system() != 'Darwin':
        BASE.fail('Compilation/linking require macOS; Linux can prepare and run fixtures only')
    if os.geteuid() == 0:
        BASE.fail('Run as an ordinary user')
    out, _ = load_inputs(args.output)
    if (out / 'build-state.json').exists():
        BASE.fail('Single-attempt metadata build; prepare a fresh output')
    baseline = Path(args.baseline_build).expanduser().resolve(strict=True)
    if baseline == out or baseline in out.parents or out in baseline.parents:
        BASE.fail('Keep the metadata candidate separate from reference-005')
    reference_manifest = json.loads((baseline / 'inputs.json').read_text())
    proof = load_proof()
    if (reference_manifest['format'], reference_manifest['diagnostic_enabled'], reference_manifest['recipe_sha256']) != (
            1, False, proof['reference_recipe_sha256']):
        BASE.fail('Expected the unchanged pinned reference-005 recipe')
    original = json.loads((out / 'baseline-inputs.json').read_text())
    for key in ('nullmoth_commit', 'nvidia_commit', 'vendor_sha256', 'nullmoth_sha256', 'patch_order', 'source_date_epoch'):
        if reference_manifest[key] != original[key]:
            BASE.fail('Reference-005 source basis differs: ' + key)
    baseline_binary = baseline / 'NVRM.kext/Contents/MacOS/NVRM'
    baseline_sha = BASE.sha(baseline_binary)
    report = DIAG.new_output(args.comparison_output)
    if out == report or out in report.parents or report in out.parents:
        BASE.fail('Keep comparison reports outside the metadata build')
    state = {'status': 'building', 'build_kind': KIND, 'experimental': True, 'metadata_version': VERSION,
             'baseline_build': str(baseline), 'baseline_binary_sha256': baseline_sha,
             'comparison_output': str(report), 'runtime_approved': False, 'hardware_approved': False}
    BASE.save(out / 'metadata-state.json', state)
    try:
        BASE.build(args, input_loader=load_inputs, artifact_metadata_version=VERSION)
        # Verify the actual linked data record, not a strings match. This is
        # after the ordinary link/sign/official structural checks and before
        # recording a completed metadata experiment.
        kmod_module = importlib.util.spec_from_file_location('nvrm615meta_linked_kmod', HERE / 'kmod.py')
        kmod = importlib.util.module_from_spec(kmod_module)
        kmod_module.loader.exec_module(kmod)
        bundle = out / 'NVRM.kext'
        kmod_report = kmod.inspect_kmod(bundle / 'Contents/MacOS/NVRM', expected_version=VERSION)
        candidate_plist = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
        official_plist = plistlib.loads((Path(args.reference).expanduser().resolve() / 'Contents/Info.plist').read_bytes())
        # Canonical plist encodings preserve value types (unlike Python's
        # equality between bool and int), while ignoring dictionary key order.
        if (plistlib.dumps(candidate_plist, fmt=plistlib.FMT_BINARY, sort_keys=True)
                != plistlib.dumps(official_plist, fmt=plistlib.FMT_BINARY, sort_keys=True)):
            BASE.fail('The complete candidate plist differs from the unchanged official 2.5.4 backup')
        BASE.save(out / 'metadata-check.json', {'status': 'verified_static_metadata', 'experimental': True,
            'runtime_approved': False, 'hardware_approved': False, 'kmod': kmod_report,
            'full_official_plist_equal': True})
        command = [sys.executable, str(HERE / 'compare.py'), '--baseline-build', str(baseline),
                   '--candidate-build', str(out), '--output', str(report)]
        if args.llvm_objdump:
            command += ['--llvm-objdump', args.llvm_objdump]
        if subprocess.run(command).returncode:
            BASE.fail('Metadata static comparison rejected or failed; inspect ' + str(report))
        if BASE.sha(baseline_binary) != baseline_sha:
            BASE.fail('Reference-005 changed during the experiment')
        state['status'] = 'built_static_review_required'
        print('Experimental metadata artifact built and statically checked; no runtime or hardware approval.')
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        state['status'] = 'failed'
        raise
    finally:
        BASE.save(out / 'metadata-state.json', state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    preparation = commands.add_parser('prepare', help='Fresh diagnostic sources plus isolated metadata patch')
    preparation.add_argument('--vendor', required=True)
    preparation.add_argument('--output', required=True)
    compilation = commands.add_parser('build', help='macOS full build, KMOD and strict four-object comparison')
    compilation.add_argument('--output', required=True)
    compilation.add_argument('--reference', required=True)
    compilation.add_argument('--baseline-build', required=True)
    compilation.add_argument('--comparison-output', required=True)
    compilation.add_argument('--sdk')
    compilation.add_argument('--jobs', type=int, default=4)
    compilation.add_argument('--llvm-objdump')
    args = parser.parse_args()
    try:
        (prepare if args.action == 'prepare' else build)(args)
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
