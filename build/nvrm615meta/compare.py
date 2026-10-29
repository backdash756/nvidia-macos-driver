#!/usr/bin/env python3
"""Compare only the explicit 2.5.4 metadata experiment against reference-005.

The ordinary three-object diagnostic comparator never selects this policy.
This entry point permits exactly its additional NVRM object and two plist
version values, while requiring the actual linked kmod_info version record.
"""
import copy
import json
from pathlib import Path
import plistlib
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# The shared reader is imported by a file-specific name, not the generic build
# or compare names used by the independent reference scripts.
import importlib.util


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


DIAG = module('nvrm615meta_shared_comparison', HERE.parent / 'nvrm615diag/compare.py')
KMOD = module('nvrm615meta_kmod_reader', HERE / 'kmod.py')
SOURCE = module('nvrm615meta_source_transform', HERE / 'build.py')
KIND = 'yuv420-ab-metadata-experiment'
VERSION = '2.5.4'
PARENT = 'e82c6637727af6e839ef9856fc9a4820b92d4fa4'
PLIST_SOURCE = 'nullmoth/kexts/NVRM/Info.plist'
CPP_SOURCE = 'nullmoth/kexts/NVRM/NVRM.cpp'
PLIST_BUNDLE = 'NVRM.kext/Contents/Info.plist'
VERSION_KEYS = ('CFBundleVersion', 'CFBundleShortVersionString')
EXPECTED_OBJECTS = (
    'ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-kapi.o',
    'ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-modepool.o',
    'glue/os-nvkms-xnu.o', 'glue/NVRM.o',
)


def historical_diagnostic_proof():
    return json.loads(subprocess.check_output(['git', '-C', str(REPO), 'show',
        PARENT + ':build/nvrm615diag/proof.json']))


def typed_equal(left, right):
    """Plist equality must not silently equate bool/int or list/tuple values."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(typed_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def compare_plists(baseline, candidate):
    """Require exactly the two 0.1 -> 2.5.4 value changes in the entire plist."""
    with Path(baseline).open('rb') as stream:
        old = plistlib.load(stream)
    with Path(candidate).open('rb') as stream:
        new = plistlib.load(stream)
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError('Bundle plists must be dictionaries')
    expected = copy.deepcopy(old)
    for key in VERSION_KEYS:
        if type(old.get(key)) is not str or old[key] != '0.1':
            raise ValueError('Reference source version must remain 0.1: ' + key)
        expected[key] = VERSION
    if not typed_equal(expected, new):
        raise ValueError('Full plist changes exceed the two expected version keys')
    return {'baseline_sha256': DIAG.sha(baseline), 'candidate_sha256': DIAG.sha(candidate),
            'changed_keys': list(VERSION_KEYS), 'before_version': '0.1', 'after_version': VERSION,
            'full_typed_dictionary_comparison': True}


class MetadataPolicy:
    """An explicit internal policy; no diagnostic CLI flag broadens old builds."""
    proof_key = 'metadata_proof'
    evidence_files = ('metadata-manifest.json', 'diagnostic-inputs.json')
    object_scope = 'four explicitly declared diagnostic/metadata objects'
    allowed_nonobjects = (PLIST_BUNDLE,)

    def validate_manifest(self, manifest, root, proof):
        expected = (3, str(root), DIAG.NULLMOTH, DIAG.NVIDIA, True, KIND, VERSION)
        actual = tuple(manifest.get(key) for key in ('format', 'output', 'nullmoth_commit',
            'nvidia_commit', 'diagnostic_enabled', 'build_kind', 'metadata_version'))
        if (actual != expected or any(manifest.get(key) is not True
                for key in ('experimental', 'metadata_enabled', 'diagnostic_enabled'))):
            raise ValueError('Wrong explicit experimental metadata build role')
        if (proof.get('format'), proof.get('metadata_parent_commit'), proof.get('version')) != (1, PARENT, VERSION):
            raise ValueError('Unexpected metadata provenance or version')
        diagnostic = proof['diagnostic_proof']
        if diagnostic != historical_diagnostic_proof():
            raise ValueError('The diagnostic proof is not the pinned unchanged e82c663 input')
        if manifest.get('diagnostic_proof') != diagnostic:
            raise ValueError('The original diagnostic proof must remain unchanged')
        if (proof.get('reference_recipe_sha256') != diagnostic.get('reference_recipe_sha256')
                or proof.get('allowed_extra_exports') != diagnostic.get('allowed_extra_exports')
                or proof.get('expected_changed_objects') != diagnostic.get('expected_changed_objects', []) + ['glue/NVRM.o']
                or proof.get('expected_changed_objects') != list(EXPECTED_OBJECTS)):
            raise ValueError('Metadata must add only NVRM.o to the original diagnostic policy')
        if proof.get('patch_order') != diagnostic['patch_order'] + ['metadata:254-metadata.patch']:
            raise ValueError('Metadata must follow the complete original diagnostic patch order')
        metadata_changes = proof['metadata_patch']['source_changes']
        if set(metadata_changes) != {PLIST_SOURCE, CPP_SOURCE}:
            raise ValueError('The metadata patch may change only Info.plist and NVRM.cpp')
        expected_sources = dict(diagnostic['source_changes'])
        expected_sources.update(metadata_changes)
        if proof.get('source_changes') != expected_sources:
            raise ValueError('Metadata source scope differs from the original diagnostic plus two version files')
        preserved = DIAG.read_json(DIAG.safe_file(root, 'diagnostic-inputs.json'))
        DIAG.manifest_role(preserved, root, True)
        if (manifest.get('diagnostic_manifest_sha256') != DIAG.sha(root / 'diagnostic-inputs.json')
                or preserved.get('diagnostic_proof') != diagnostic
                or manifest.get('diagnostic_patch_order') != diagnostic['patch_order']
                or preserved.get('diagnostic_patch_order') != diagnostic['patch_order']):
            raise ValueError('Preserved diagnostic manifest/provenance changed')
        if manifest.get('metadata_proof') != proof:
            raise ValueError('Candidate metadata provenance differs from the selected proof')
        expected_maps = {key: dict(preserved[key]) for key in ('nullmoth_sha256', 'vendor_sha256')}
        for relative, change in metadata_changes.items():
            name = relative[len('nullmoth/'):]
            if expected_maps['nullmoth_sha256'].get(name) != change['before_sha256']:
                raise ValueError('Metadata preimage differs from the preserved diagnostic source')
            expected_maps['nullmoth_sha256'][name] = change['after_sha256']
        if any(manifest.get(key) != value for key, value in expected_maps.items()):
            raise ValueError('Source delta exceeds the two exact metadata transitions')
        metadata_manifest = DIAG.read_json(DIAG.safe_file(root, 'metadata-manifest.json'))
        expected_metadata = {'format': 1, 'build_kind': KIND, 'experimental': True,
            'runtime_approved': False, 'hardware_approved': False,
            'version': VERSION, 'metadata_proof': proof,
            'diagnostic_manifest_sha256': DIAG.sha(root / 'diagnostic-inputs.json'),
            'baseline_manifest_sha256': DIAG.sha(root / 'baseline-inputs.json'),
            'metadata_patch_order': proof['patch_order']}
        if (manifest.get('metadata_manifest_sha256') != DIAG.sha(root / 'metadata-manifest.json')
                or not typed_equal(metadata_manifest, expected_metadata)
                or manifest.get('metadata_patch_order') != proof['patch_order']):
            raise ValueError('Own metadata manifest/status/provenance changed')
        if preserved.get('baseline_manifest_sha256') != manifest.get('baseline_manifest_sha256'):
            raise ValueError('Metadata and preserved diagnostic use different baseline evidence')

    def inspect_artifacts(self, baseline, candidate, manifests, proof):
        source_delta = {}
        for relative in (PLIST_SOURCE, CPP_SOURCE):
            old_path = DIAG.safe_file(baseline, relative)
            new_path = DIAG.safe_file(candidate, relative)
            expected = SOURCE.expected_metadata_bytes(relative, old_path.read_bytes())
            if new_path.read_bytes() != expected:
                raise ValueError('Metadata source bytes exceed the exact permitted version change: ' + relative)
            source_delta[relative] = {'before_sha256': DIAG.sha(old_path),
                                      'after_sha256': DIAG.sha(new_path)}
        for root in (baseline, candidate):
            if (DIAG.safe_file(root, PLIST_SOURCE).read_bytes()
                    != DIAG.safe_file(root, PLIST_BUNDLE).read_bytes()):
                raise ValueError('Bundle plist bytes differ from the exact staged source: ' + str(root))
        plist = compare_plists(baseline / PLIST_BUNDLE, candidate / PLIST_BUNDLE)
        if DIAG.read_json(candidate / 'artifacts.json').get('metadata_version') != VERSION:
            raise ValueError('Artifact metadata version does not identify the explicit metadata profile')
        old_kmod = KMOD.inspect_kmod(baseline / 'NVRM.kext/Contents/MacOS/NVRM', expected_version='0.1')
        new_kmod = KMOD.inspect_kmod(candidate / 'NVRM.kext/Contents/MacOS/NVRM', expected_version=VERSION)
        return {'profile': KIND, 'experimental': True, 'runtime_approved': False, 'hardware_approved': False,
                'source_delta_verified': True, 'metadata_source_delta': source_delta,
                'plist_comparison': plist, 'baseline_kmod': old_kmod, 'candidate_kmod': new_kmod}


def inspect_pair(baseline, candidate, proof):
    return DIAG.inspect_pair(baseline, candidate, proof, candidate_policy=MetadataPolicy())


def main():
    return DIAG.main(proof_path=HERE / 'proof.json', candidate_policy=MetadataPolicy())


if __name__ == '__main__':
    sys.exit(main())
