#!/usr/bin/env python3
"""Prepare an opt-in YUV420 experiment and reuse the pinned NVRM reference build.

All compilation/signing affects a fresh candidate only. Existing builds,
installed drivers and the official backup are read-only. Never loads a kext.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE_DIR = REPO / 'build/nvrm615'
sys.path.insert(0, str(BASE_DIR))
SPEC = importlib.util.spec_from_file_location('nvrm615_reference_runner', BASE_DIR / 'build.py')
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
KIND = 'yuv420-ab-experiment'
BASE_COMMIT = '2e9f435637ff44164ba0355ae8904f15935c2f5a'
DIAG_COMMIT = 'ff37f3812fe170610885081f3a9cb223eb580938'
FOLLOWUP_PARENT = '61c581ed679ebec7be9685eecf0bad991671da82'
FOLLOWUP_SOURCE = 'ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c'


def recipe_hashes():
    return {p.relative_to(HERE).as_posix(): BASE.sha(p) for p in sorted(HERE.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def load_proof():
    proof = json.loads((HERE / 'proof.json').read_text())
    if (proof['format'], proof['experiment_base_commit'], proof['diagnostic_source_commit'],
            proof['nullmoth_commit'], proof['nvidia_commit']) != (
            2, BASE_COMMIT, DIAG_COMMIT, BASE.NULLMOTH, BASE.NVIDIA):
        BASE.fail('Unexpected diagnostic provenance pins')
    historical = json.loads(subprocess.check_output(['git', '-C', str(REPO), 'show',
        FOLLOWUP_PARENT + ':build/nvrm615diag/proof.json']))
    for key in ('diagnostic_patch', 'xnu_hook', 'expected_changed_objects',
                'allowed_extra_exports', 'reference_recipe_sha256', 'baseline_patch_proof_sha256'):
        if proof[key] != historical[key]:
            BASE.fail('Historical diagnostic provenance must remain unchanged: ' + key)
    if proof['historical_source_changes'] != historical['source_changes']:
        BASE.fail('Historical diagnostic source hashes must remain unchanged')
    followup = proof['followup_patch']
    if (followup['path'], followup['parent_commit'], followup['apply_directory']) != (
            'patches/615-yuv420-trigger-overview.patch', FOLLOWUP_PARENT, 'src/nvidia-modeset'):
        BASE.fail('Unexpected diagnostic follow-up provenance or scope')
    if set(followup['source_changes']) != {FOLLOWUP_SOURCE}:
        BASE.fail('The trigger follow-up must modify only the NVKMS KAPI source')
    transition = followup['source_changes'][FOLLOWUP_SOURCE]
    expected = copy.deepcopy(historical['source_changes'])
    if transition['before_sha256'] != expected[FOLLOWUP_SOURCE]['after_sha256']:
        BASE.fail('Follow-up preimage must match the historical diagnostic output')
    expected[FOLLOWUP_SOURCE]['after_sha256'] = transition['after_sha256']
    if (proof['source_changes'] != expected or proof['patch_order'] != historical['patch_order']
            + ['diagnostic:615-yuv420-trigger-overview.patch']):
        BASE.fail('Unexpected follow-up source transition or patch order')
    if BASE.sha(BASE_DIR / 'patches/patch-proof.json') != proof['baseline_patch_proof_sha256']:
        BASE.fail('The complete five-patch baseline must remain unchanged')
    patch, hook = proof['diagnostic_patch'], proof['xnu_hook']
    for path, digest in ((REPO / patch['path'], patch['sha256']),
                         (HERE / followup['path'], followup['sha256']),
                         (HERE / hook['path'], hook['sha256']),
                         (REPO / hook['source_path'], hook['source_sha256'])):
        if path.is_symlink() or BASE.sha(path) != digest:
            BASE.fail('Diagnostic input changed: ' + str(path))
    for path, digest in ((patch['path'], patch['sha256']),
                         (hook['source_path'], hook['source_sha256'])):
        original = subprocess.check_output(['git', '-C', str(REPO), 'show', DIAG_COMMIT + ':' + path])
        if hashlib.sha256(original).hexdigest() != digest:
            BASE.fail('Original diagnostic provenance mismatch: ' + path)
    if patch['apply_directory'] != 'src/nvidia-modeset':
        BASE.fail('Unexpected diagnostic patch scope')
    expected = {'ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c',
                'ogkm/src/nvidia-modeset/src/nvkms-modepool.c',
                'nullmoth/kexts/NVRM/os-nvkms-xnu.cpp'}
    if set(proof['source_changes']) != expected:
        BASE.fail('Diagnostic source scope must contain exactly the paired three files')
    if proof['source_changes']['nullmoth/' + hook['source_path']]['after_sha256'] != hook['source_sha256']:
        BASE.fail('The staged XNU hook must be byte-identical to the original paired hook')
    return proof


def new_output(value):
    out = BASE.output_path(value, fresh=True)
    for parent in out.parents:
        if (parent / 'inputs.json').is_file() or (parent / 'build-state.json').is_file():
            BASE.fail('Do not create diagnostic output inside an existing build: ' + str(parent))
    return out


def strict_patch(cwd, path, expected, directory=None):
    command = ['git', 'apply']
    if directory:
        command += ['--directory=' + directory]
    raw = subprocess.check_output(command + ['--numstat', '-z', str(path)], cwd=cwd)
    rows = [row.split(b'\t') for row in raw.split(b'\0') if row]
    if (any(len(row) != 3 for row in rows) or len(rows) != len(expected)
            or {row[2].decode('ascii') for row in rows} != set(expected)):
        BASE.fail('Diagnostic patch changes unexpected paths')
    subprocess.run(command + ['--check', str(path)], cwd=cwd, check=True)
    subprocess.run(command + [str(path)], cwd=cwd, check=True)


def expected_sources(baseline, proof):
    maps = {key: dict(baseline[key]) for key in ('vendor_sha256', 'nullmoth_sha256')}
    for relative, change in proof['source_changes'].items():
        folder, path = relative.split('/', 1)
        key = 'vendor_sha256' if folder == 'ogkm' else 'nullmoth_sha256'
        if maps[key].get(path) != change['before_sha256']:
            BASE.fail('Diagnostic preimage does not match the complete baseline: ' + relative)
        maps[key][path] = change['after_sha256']
    return maps


def apply_followup(out, proof):
    """Apply the separately recorded correction only to its exact preimage."""
    entry = proof['followup_patch']
    path = HERE / entry['path']
    if BASE.sha(path) != entry['sha256']:
        BASE.fail('Diagnostic follow-up patch hash changed')
    BASE.verify_hashes(out, {name: change['before_sha256']
                             for name, change in entry['source_changes'].items()})
    strict_patch(out / 'ogkm', path, ('src/nvidia-modeset/kapi/src/nvkms-kapi.c',),
                 entry['apply_directory'])
    BASE.verify_hashes(out, {name: change['after_sha256']
                             for name, change in entry['source_changes'].items()})


def prepare(args):
    out = new_output(args.output)
    proof = load_proof()
    print('Preparing the complete five-patch baseline before adding the diagnostic.')
    BASE.prepare(args)
    # Preserve the baseline evidence, then remove the reference manifest name.
    # A failed integration is never consumable by either build entry point.
    (out / 'inputs.json').rename(out / 'baseline-inputs.json')
    baseline = json.loads((out / 'baseline-inputs.json').read_text())
    maps = expected_sources(baseline, proof)
    BASE.verify_hashes(out, {path: change['before_sha256']
                             for path, change in proof['source_changes'].items()})
    strict_patch(out / 'ogkm', REPO / proof['diagnostic_patch']['path'],
                 ('src/nvidia-modeset/kapi/src/nvkms-kapi.c',
                  'src/nvidia-modeset/src/nvkms-modepool.c'), 'src/nvidia-modeset')
    strict_patch(out / 'nullmoth', HERE / proof['xnu_hook']['path'],
                 ('kexts/NVRM/os-nvkms-xnu.cpp',))
    BASE.verify_hashes(out, {path: change['after_sha256']
                             for path, change in proof['historical_source_changes'].items()})
    apply_followup(out, proof)
    BASE.verify_hashes(out, {path: change['after_sha256']
                             for path, change in proof['source_changes'].items()})
    for folder, key in (('ogkm', 'vendor_sha256'), ('nullmoth', 'nullmoth_sha256')):
        if BASE.tree_hashes(out / folder) != maps[key]:
            BASE.fail('Integration changed unexpected sources: ' + folder)
    BASE.verify_hashes(out / 'ogkm', baseline['preserved_sources_sha256'])
    manifest = copy.deepcopy(baseline)
    manifest.update(format=2, purpose='experimental diagnostic artifact; no installation or load approval',
                    build_kind=KIND, diagnostic_enabled=True, diagnostic_proof=proof,
                    diagnostic_patch_order=proof['patch_order'],
                    baseline_manifest_sha256=BASE.sha(out / 'baseline-inputs.json'),
                    diagnostic_recipe_sha256=recipe_hashes(), **maps)
    BASE.save(out / 'inputs.json', manifest)
    BASE.save(out / 'diagnostic-state.json', {'status': 'prepared', 'build_kind': KIND,
              'diagnostic_enabled': True, 'hardware_approved': False})
    print('Prepared paired YUV420 diagnostic sources: ' + str(out))
    print('Only the two existing NVKMS diagnostic sources and the existing XNU hook were added.')


def load_inputs(value):
    out = BASE.output_path(value)
    manifest = json.loads((out / 'inputs.json').read_text())
    if (manifest['format'], manifest['output'], manifest['build_kind'],
            manifest['nullmoth_commit'], manifest['nvidia_commit'], manifest['diagnostic_enabled']) != (
            2, str(out), KIND, BASE.NULLMOTH, BASE.NVIDIA, True):
        BASE.fail('Expected a separately prepared YUV420 experiment, not a reference build')
    proof = load_proof()
    if (manifest['diagnostic_proof'] != proof or manifest['diagnostic_patch_order'] != proof['patch_order']
            or manifest['recipe_sha256'] != BASE.recipe_hashes()
            or manifest['diagnostic_recipe_sha256'] != recipe_hashes()):
        BASE.fail('Diagnostic recipe or provenance changed; prepare a new output')
    if BASE.sha(out / 'baseline-inputs.json') != manifest['baseline_manifest_sha256']:
        BASE.fail('Preserved baseline input manifest changed')
    baseline = json.loads((out / 'baseline-inputs.json').read_text())
    if (baseline['format'], baseline['output'], baseline['diagnostic_enabled'],
            baseline['nullmoth_commit'], baseline['nvidia_commit']) != (
            1, str(out), False, BASE.NULLMOTH, BASE.NVIDIA):
        BASE.fail('Invalid preserved baseline manifest')
    current_proof = json.loads((BASE_DIR / 'patches/patch-proof.json').read_text())
    if manifest['patch_order'] != current_proof['patch_order'] or baseline['patch_order'] != manifest['patch_order']:
        BASE.fail('All baseline patches, including the timer correction, must be retained')
    maps = expected_sources(baseline, proof)
    for folder, key in (('nullmoth', 'nullmoth_sha256'), ('ogkm', 'vendor_sha256')):
        if manifest[key] != maps[key]:
            BASE.fail('Unexpected diagnostic source manifest delta')
        BASE.verify_hashes(out / folder, manifest[key])
        if BASE.tree_hashes(out / folder) != manifest[key]:
            BASE.fail('Diagnostic input inventory changed: ' + folder)
    BASE.verify_hashes(out / 'ogkm', current_proof['preserved_sources_sha256'])
    return out, manifest


def build(args):
    if platform.system() != 'Darwin':
        BASE.fail('Compilation/linking require macOS; Linux can prepare/test only')
    if os.geteuid() == 0:
        BASE.fail('Run as an ordinary user')
    out, _ = load_inputs(args.output)
    if (out / 'build-state.json').exists():
        BASE.fail('Single-attempt diagnostic output: prepare a new directory for another attempt')
    baseline = Path(args.baseline_build).expanduser().resolve(strict=True)
    if out == baseline or out in baseline.parents or baseline in out.parents:
        BASE.fail('Diagnostic and reference-005 builds must be separate')
    recorded = json.loads((baseline / 'inputs.json').read_text())
    proof = load_proof()
    if (recorded['format'], recorded['diagnostic_enabled'], recorded['nullmoth_commit'],
            recorded['nvidia_commit'], recorded['recipe_sha256']) != (
            1, False, BASE.NULLMOTH, BASE.NVIDIA, proof['reference_recipe_sha256']):
        BASE.fail('Expected the unchanged reference-005 built with the pinned reference recipe')
    before = json.loads((out / 'baseline-inputs.json').read_text())
    for key in ('vendor_sha256', 'nullmoth_sha256', 'patch_order', 'source_date_epoch'):
        if recorded[key] != before[key]:
            BASE.fail('Reference-005 and diagnostic source baselines differ: ' + key)
    artifact = baseline / 'NVRM.kext/Contents/MacOS/NVRM'
    baseline_sha = BASE.sha(artifact)
    # Check only the future report path here. The comparison creates it after
    # building; this is never an output within an existing build directory.
    report = new_output(args.comparison_output)
    if report == out or out in report.parents or report in out.parents:
        BASE.fail('Keep the static comparison outside the candidate build')
    state = {'status': 'building', 'build_kind': KIND, 'diagnostic_enabled': True,
             'baseline_build': str(baseline), 'baseline_binary_sha256': baseline_sha,
             'comparison_output': str(report), 'hardware_approved': False}
    BASE.save(out / 'diagnostic-state.json', state)
    try:
        BASE.build(args, input_loader=load_inputs)
        command = [sys.executable, str(HERE / 'compare.py'), '--baseline-build', str(baseline),
                   '--candidate-build', str(out), '--output', str(report)]
        if args.llvm_objdump:
            command += ['--llvm-objdump', args.llvm_objdump]
        result = subprocess.run(command)
        if result.returncode:
            BASE.fail('Diagnostic comparison rejected or failed; inspect ' + str(report))
        if BASE.sha(artifact) != baseline_sha:
            BASE.fail('Reference-005 binary changed during the experiment')
        state['status'] = 'built_static_review_required'
        print('Experimental diagnostic built; read the comparison reports. No hardware test is authorized.')
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        state['status'] = 'failed'
        raise
    finally:
        BASE.save(out / 'diagnostic-state.json', state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    prepare_parser = actions.add_parser('prepare', help='Materialize a fresh paired diagnostic source tree')
    prepare_parser.add_argument('--vendor', required=True)
    prepare_parser.add_argument('--output', required=True)
    build_parser = actions.add_parser('build', help='macOS: isolated full build and mandatory baseline comparison')
    build_parser.add_argument('--output', required=True)
    build_parser.add_argument('--reference', required=True, help='Official backed-up 2.5.4 NVRM.kext')
    build_parser.add_argument('--baseline-build', required=True, help='Existing completed reference-005; read-only')
    build_parser.add_argument('--comparison-output', required=True, help='New separate static report directory')
    build_parser.add_argument('--sdk')
    build_parser.add_argument('--jobs', type=int, default=4)
    build_parser.add_argument('--llvm-objdump', help='Optional existing LLVM disassembler path')
    args = parser.parse_args()
    try:
        (prepare if args.action == 'prepare' else build)(args)
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
