#!/usr/bin/env python3
"""Read-only comparison of a YUV420 experiment against its reference-005 build.

Object bytes are strict: no address, timestamp or instruction normalization is
used for standalone objects. Archive container/index metadata is separate from
member payload identity. Final Mach-O/layout/data evidence still needs review.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE = HERE.parent / 'nvrm615'
NULLMOTH = '46779055f68208baa5ddfb9b2e9c77ed028e1d59'
NVIDIA = '1a82fbfda8f321b056eb3568850f9316c5e465c5'
COMPONENTS = {'nvidia': 'libnvkernel.a', 'nvidia-modeset': 'libnvkms.a'}
LIMITS = [
    'Identical object bytes and compiler records constrain the change scope; equal symbols alone do not prove ABI compatibility.',
    'Final image layout, data references, binding, KAPI structures, signatures, kernel loadability and runtime side effects remain separate concerns.',
    'Archive member payloads are compared strictly; container dates, ownership and symbol-index bytes are reported separately.',
    'The instruction audit is conservative; unresolved data references or function extents remain not_comparable, never proven identical.',
    'A review_required result is not permission to install, load, alter boot arguments or begin a hardware test.',
]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ARCHIVES = module('nvrm615diag_archive_reader', BASE / 'archives.py')
BUILD = module('nvrm615diag_reference_helpers', BASE / 'build.py')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    with Path(path).open() as stream:
        return json.load(stream)


def safe_file(root, relative):
    value = Path(relative)
    if not relative or value.is_absolute() or '..' in value.parts or str(value) != relative:
        raise ValueError('Invalid manifest relative path: ' + repr(relative))
    path = root / value
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Input paths must not use symlinks: ' + str(path))
    if not path.is_file():
        raise ValueError('Missing input file: ' + str(path))
    return path


def verify_mapping(root, mapping):
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError('Missing or empty file-hash mapping: ' + str(root))
    for relative, expected in mapping.items():
        if not re.fullmatch(r'[0-9a-f]{64}', expected) or sha(safe_file(root, relative)) != expected:
            raise ValueError('Recorded input/artifact hash differs: ' + str(root / relative))


def source_inventory(root, manifest):
    for folder, key in (('nullmoth', 'nullmoth_sha256'), ('ogkm', 'vendor_sha256')):
        mapping = manifest.get(key)
        verify_mapping(root / folder, mapping)
        # NVIDIA emits generated build files under _out. No additional source
        # or header outside those dedicated object trees is silently accepted.
        actual = {path.relative_to(root / folder).as_posix()
                  for path in (root / folder).rglob('*') if path.is_file()
                  and '_out' not in path.relative_to(root / folder).parts}
        if actual != set(mapping):
            raise ValueError('Unexpected source/header inventory outside _out: ' + folder)


def manifest_role(manifest, root, diagnostic):
    expected = (2 if diagnostic else 1, str(root), NULLMOTH, NVIDIA, diagnostic)
    actual = tuple(manifest.get(key) for key in (
        'format', 'output', 'nullmoth_commit', 'nvidia_commit', 'diagnostic_enabled'))
    if actual != expected or (diagnostic and manifest.get('build_kind') != 'yuv420-ab-experiment'):
        raise ValueError('Wrong pinned build role: ' + str(root))


def normalize_argument(argument, root):
    """Replace only this build's path at an explicit, bounded path position."""
    root = str(root)
    prefixes = ('', '-I', '-L', '-F', '--sysroot=', '-isysroot=',
                '-ffile-prefix-map=', '-fdebug-prefix-map=', '-fmacro-prefix-map=')
    for prefix in prefixes:
        if not argument.startswith(prefix):
            continue
        rest = argument[len(prefix):]
        if rest == root or rest.startswith(root + '/') or (prefix.endswith('prefix-map=') and rest.startswith(root + '=')):
            return prefix + '@BUILD' + rest[len(root):]
    return argument


def normalized_argv(argv, root):
    if not isinstance(argv, list) or any(not isinstance(arg, str) or '\0' in arg for arg in argv):
        raise ValueError('Compile records must contain string argv lists')
    return [normalize_argument(arg, root) for arg in argv]


def compile_inventory(root):
    records = {}
    for component in COMPONENTS:
        path = root / 'ogkm/src' / component / '_out/Darwin_x86_64/compile_cmds.sh'
        rows = BUILD.compile_commands(safe_file(root, path.relative_to(root).as_posix()).read_text())
        if not rows:
            raise ValueError('Missing compile command rows: ' + component)
        component_records = set()
        for _, argv in rows:
            output = argv[argv.index('-o') + 1]
            if not re.fullmatch(r'_out/Darwin_x86_64/[A-Za-z0-9_.+-]+\.o', output):
                raise ValueError('Unexpected compile output: ' + output)
            key = 'ogkm/src/' + component + '/' + output
            if key in records:
                raise ValueError('Duplicate compile output: ' + key)
            records[key] = {'argv': normalized_argv(argv, root), 'cwd': '@BUILD/ogkm/src/' + component}
            component_records.add(key)
        objects = read_json(safe_file(root, component + '-objects.json'))
        expected = {'ogkm/src/' + component + '/' + name for name in objects}
        if len(objects) != len(set(objects)) or component_records != expected:
            raise ValueError('Compile command outputs disagree with the exact object manifest: ' + component)
    commands = [json.loads(line) for line in safe_file(root, 'commands.jsonl').read_text().splitlines() if line.strip()]
    for row in commands:
        label = row.get('log', '')
        if label.startswith('glue-'):
            argv = row['argv']
            if argv.count('-o') != 1:
                raise ValueError('Invalid glue output command')
            path = Path(argv[argv.index('-o') + 1])
            relative = path.relative_to(root).as_posix()
            if relative in records:
                raise ValueError('Duplicate glue command: ' + relative)
            records[relative] = {'argv': normalized_argv(argv, root),
                                 'cwd': normalize_argument(row['cwd'], root)}
    wanted_glue = {'glue/' + stem + '.o' for stem in BUILD.LINK_OBJECTS}
    if set(key for key in records if key.startswith('glue/')) != wanted_glue:
        raise ValueError('Glue compile inventory does not match the fixed link objects')
    links = [row for row in commands if row.get('log') == 'link']
    if len(links) != 1:
        raise ValueError('Expected one final link command')
    records['@link'] = {'argv': normalized_argv(links[0]['argv'], root),
                        'cwd': normalize_argument(links[0]['cwd'], root)}
    return records


def toolchain_signature(preflight):
    if preflight.get('status') != 'passed':
        raise ValueError('Build preflight must have passed')
    tools = preflight.get('tools')
    sdk = preflight.get('sdk')
    if not isinstance(tools, dict) or not isinstance(sdk, dict) or not tools or not sdk.get('key_header_sha256'):
        raise ValueError('Missing toolchain/SDK evidence')
    hashes = {name: item.get('sha256') for name, item in tools.items()}
    if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in hashes.values()):
        raise ValueError('Missing tool executable hash')
    # Paths/command transcripts are retained in the original preflight files.
    # SDK version, metadata/header hashes and every tool binary must match.
    sdk_signature = {key: value for key, value in sdk.items() if key not in ('path', 'kernel_headers')}
    return {'tools_sha256': hashes, 'sdk': sdk_signature}


def artifact_inventory(root, manifest):
    report = read_json(safe_file(root, 'artifacts.json'))
    if report.get('diagnostic_enabled') is not manifest['diagnostic_enabled']:
        raise ValueError('Artifact diagnostic role disagrees with inputs')
    mapping = report.get('files')
    verify_mapping(root, mapping)
    actual = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()
              and (path.suffix in ('.o', '.a') or root / 'NVRM.kext' in path.parents)}
    if actual != set(mapping):
        raise ValueError('Actual object/archive/bundle inventory disagrees with artifacts.json')
    return mapping


def archive_inventory(root, component, artifact_hashes):
    objects = read_json(safe_file(root, component + '-objects.json'))
    if (not isinstance(objects, list) or not objects
            or len(objects) != len(set(objects)) or any(not isinstance(obj, str)
            or not re.fullmatch(r'_out/Darwin_x86_64/[A-Za-z0-9_.+-]+\.o', obj) for obj in objects)):
        raise ValueError('Invalid exact object manifest: ' + component)
    for obj in objects:
        if 'ogkm/src/' + component + '/' + obj not in artifact_hashes:
            raise ValueError('Manifest object absent from artifacts: ' + obj)
    relative = 'ogkm/src/' + component + '/_out/Darwin_x86_64/' + COMPONENTS[component]
    report = ARCHIVES.validate_archive(safe_file(root, relative), objects)
    members = {row['name']: row['sha256'] for row in report['members'] if row['kind'] == 'object'}
    for obj in objects:
        relative_obj = 'ogkm/src/' + component + '/' + obj
        if members[Path(obj).name] != artifact_hashes[relative_obj]:
            raise ValueError('Archive member is stale/different from standalone object: ' + relative_obj)
    return {'objects': objects, 'report': report, 'members': members}


def inspect_pair(baseline, candidate, proof, candidate_policy=None):
    """Validate actual recorded files; fixtures can exercise this core on Linux."""
    baseline, candidate = Path(baseline).resolve(), Path(candidate).resolve()
    if baseline == candidate or baseline in candidate.parents or candidate in baseline.parents:
        raise ValueError('Builds must be separate nonoverlapping trees')
    report = {'schema': 1, 'status': 'running', 'baseline_build': str(baseline),
              'candidate_build': str(candidate), 'limitations': LIMITS,
              'static_rejections': [], 'review_required': [], 'object_changes': [],
              'archive_changes': {}, 'commands': []}
    reject = report['static_rejections'].append
    try:
        manifests = [read_json(safe_file(root, 'inputs.json')) for root in (baseline, candidate)]
        before = read_json(safe_file(candidate, 'baseline-inputs.json'))
        manifest_role(manifests[0], baseline, False)
        if candidate_policy is None:
            manifest_role(manifests[1], candidate, True)
        else:
            # Separate experiment entry points opt in explicitly. The ordinary
            # diagnostic CLI retains its original role and object scope.
            candidate_policy.validate_manifest(manifests[1], candidate, proof)
        manifest_role(before, candidate, False)
        proof_key = 'diagnostic_proof' if candidate_policy is None else candidate_policy.proof_key
        if manifests[1].get(proof_key) != proof:
            raise ValueError('Candidate diagnostic provenance differs from the selected proof')
        if manifests[1].get('baseline_manifest_sha256') != sha(candidate / 'baseline-inputs.json'):
            raise ValueError('Preserved baseline manifest hash differs')
        expected_recipe = proof.get('reference_recipe_sha256')
        if not expected_recipe or manifests[0].get('recipe_sha256') != expected_recipe:
            raise ValueError('Baseline recipe is not the pinned reference-005 recipe')
        for key in ('nullmoth_commit', 'nvidia_commit', 'nvidia_tag', 'source_date_epoch',
                    'patch_proof_format', 'patch_order', 'preserved_sources_sha256'):
            if manifests[0].get(key) != before.get(key) or before.get(key) != manifests[1].get(key):
                raise ValueError('Baseline source/patch metadata differs: ' + key)
        for key in ('nullmoth_sha256', 'vendor_sha256'):
            if manifests[0].get(key) != before.get(key):
                raise ValueError('Candidate initial source basis differs from reference-005: ' + key)
        changes = {}
        for folder, key in (('nullmoth', 'nullmoth_sha256'), ('ogkm', 'vendor_sha256')):
            old, new = before[key], manifests[1][key]
            if set(old) != set(new):
                raise ValueError('Diagnostic must not add/remove staged source files: ' + folder)
            changes.update({folder + '/' + name: {'before_sha256': old[name], 'after_sha256': new[name]}
                            for name in old if old[name] != new[name]})
        report['source_changes'] = changes
        if changes != proof['source_changes']:
            raise ValueError('Source delta differs from the exact paired diagnostic changes')
        for root, manifest in zip((baseline, candidate), manifests):
            source_inventory(root, manifest)
        report['input_manifests_sha256'] = {label: sha(root / 'inputs.json')
                                           for label, root in (('baseline', baseline), ('candidate', candidate))}
        metadata_files = ['inputs.json', 'artifacts.json', 'preflight/preflight.json', 'commands.jsonl']
        metadata_files += [component + '-objects.json' for component in COMPONENTS]
        metadata_files += ['ogkm/src/' + component + '/_out/Darwin_x86_64/compile_cmds.sh' for component in COMPONENTS]
        report['evidence_metadata_sha256'] = {label: {name: sha(safe_file(root, name))
            for name in metadata_files + (['baseline-inputs.json'] if label == 'candidate' else [])}
            for label, root in (('baseline', baseline), ('candidate', candidate))}
        if candidate_policy is not None:
            report['evidence_metadata_sha256']['candidate'].update({
                name: sha(safe_file(candidate, name)) for name in candidate_policy.evidence_files})
        chains = [toolchain_signature(read_json(safe_file(root, 'preflight/preflight.json')))
                  for root in (baseline, candidate)]
        report['toolchains'] = chains
        if chains[0] != chains[1]:
            reject('Toolchain/SDK/header evidence differs; use the same qualified tools or create a fresh paired reference')
        records = [compile_inventory(root) for root in (baseline, candidate)]
        report['compile_record_changes'] = {key: {'baseline': records[0].get(key), 'candidate': records[1].get(key)}
            for key in sorted(set(records[0]) | set(records[1])) if records[0].get(key) != records[1].get(key)}
        if report['compile_record_changes']:
            reject('Compile/link command inventory or flags differ beyond exact owned build-root paths')
        artifacts = [artifact_inventory(root, manifest) for root, manifest in zip((baseline, candidate), manifests)]
        report['artifacts'] = {'baseline': artifacts[0], 'candidate': artifacts[1]}
        old_objects = {name: value for name, value in artifacts[0].items() if name.endswith('.o')}
        new_objects = {name: value for name, value in artifacts[1].items() if name.endswith('.o')}
        expected_objects = set(proof['expected_changed_objects'])
        if set(old_objects) != set(new_objects):
            reject('Object inventory differs (missing/extra objects)')
        object_changes = {name for name in set(old_objects) & set(new_objects) if old_objects[name] != new_objects[name]}
        report['object_changes'] = sorted(object_changes)
        report['unexpected_object_changes'] = sorted(object_changes - expected_objects)
        report['expected_but_unchanged_objects'] = sorted(expected_objects - object_changes)
        report['object_counts'] = {'baseline': len(old_objects), 'candidate': len(new_objects),
                                   'identical': sum(old_objects.get(name) == value for name, value in new_objects.items()),
                                   'changed': len(object_changes)}
        if object_changes != expected_objects:
            scope = 'three paired diagnostic objects' if candidate_policy is None else candidate_policy.object_scope
            reject('Object changes are not exactly the ' + scope)
        if set(artifacts[0]) != set(artifacts[1]):
            reject('Object/archive/bundle artifact inventory differs')
        allowed_nonobjects = {'NVRM.kext/Contents/MacOS/NVRM', 'NVRM.kext/Contents/_CodeSignature/CodeResources'}
        allowed_nonobjects.update('ogkm/src/' + component + '/_out/Darwin_x86_64/' + archive
                                 for component, archive in COMPONENTS.items())
        if candidate_policy is not None:
            allowed_nonobjects.update(candidate_policy.allowed_nonobjects)
        unexpected = [name for name in set(artifacts[0]) & set(artifacts[1])
                      if not name.endswith('.o') and artifacts[0][name] != artifacts[1][name]
                      and name not in allowed_nonobjects]
        report['unexpected_nonobject_artifact_changes'] = sorted(unexpected)
        if unexpected:
            reject('Unexpected nonobject/bundle metadata artifact changes')
        for component in COMPONENTS:
            inventories = [archive_inventory(root, component, mapping) for root, mapping in zip((baseline, candidate), artifacts)]
            if inventories[0]['objects'] != inventories[1]['objects']:
                reject('Archive object order/inventory differs: ' + component)
            changed = {name for name in set(inventories[0]['members']) | set(inventories[1]['members'])
                       if inventories[0]['members'].get(name) != inventories[1]['members'].get(name)}
            allowed = {Path(name).name for name in expected_objects if name.startswith('ogkm/src/' + component + '/')}
            report['archive_changes'][component] = {'changed_object_members': sorted(changed),
                'baseline': inventories[0]['report'], 'candidate': inventories[1]['report']}
            if changed != allowed:
                reject('Archive member changes exceed/miss the diagnostic scope: ' + component)
        if candidate_policy is not None:
            report['candidate_policy_evidence'] = candidate_policy.inspect_artifacts(baseline, candidate, manifests, proof)
        report['review_required'].extend([
            'Inspect final structural and instruction reports, including layout/relocation/data-reference uncertainty.',
            'Final code generation, imported bindings and KAPI/other-component compatibility need human review before any hardware decision.'])
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        reject('Evidence validation failed: ' + str(error))
    report['status'] = 'rejected' if report['static_rejections'] else 'review_required'
    return report


def new_output(value, builds):
    raw = Path(value).expanduser()
    if not raw.is_absolute() or '..' in raw.parts or raw.is_symlink():
        raise ValueError('Output must be a new absolute nonsymlink directory')
    path = raw.resolve()
    if path.exists():
        raise ValueError('Output already exists; reports are never overwritten')
    folded = tuple(part.casefold() for part in path.parts)
    if (len(folded) < 3 or any(part in ('efi', 'system', 'library') or part.endswith('.kext') for part in folded)
            or folded[1] in ('usr', 'bin', 'sbin', 'etc', 'dev', 'proc', 'sys', 'applications')
            or folded[1:3] == ('private', 'etc')):
        raise ValueError('Output must be outside system and kext paths')
    for root in builds:
        if path == root or root in path.parents or path in root.parents:
            raise ValueError('Output overlaps an input build')
    for parent in path.parents:
        if (parent / 'inputs.json').is_file() or (parent / 'build-state.json').is_file():
            raise ValueError('Output must not enter any existing build')
    path.mkdir(parents=True)
    return path


def run_child(argv, output, label, report):
    stdout, stderr = output / (label + '.stdout.txt'), output / (label + '.stderr.txt')
    with stdout.open('w') as out, stderr.open('w') as err:
        result = subprocess.run(list(map(str, argv)), stdout=out, stderr=err)
    report['commands'].append({'argv': list(map(str, argv)), 'returncode': result.returncode,
                               'stdout': stdout.name, 'stderr': stderr.name})
    return result.returncode


def summary(report):
    lines = ['NVRM YUV420 isolated experiment comparison', 'Status: ' + report['status'], '']
    if 'object_counts' in report:
        lines.append('Objects: ' + json.dumps(report['object_counts'], sort_keys=True))
    lines += ['Changed: ' + name for name in report['object_changes']]
    if 'instruction_result' in report:
        lines.append('Instruction coverage: ' + json.dumps(report['instruction_result']['counts'], sort_keys=True))
    lines += ['', 'Static rejections:'] + (['- ' + value for value in report['static_rejections']] or ['- None'])
    lines += ['', 'Limits:'] + ['- ' + value for value in LIMITS]
    lines += ['', 'Full evidence and uncertainty: comparison.json and child reports.']
    return '\n'.join(lines) + '\n'


def main(proof_path=None, candidate_policy=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-build', required=True, type=Path)
    parser.add_argument('--candidate-build', required=True, type=Path)
    parser.add_argument('--output', required=True)
    parser.add_argument('--llvm-objdump')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('The complete binary comparison requires macOS; Linux runs regression fixtures only')
    if os.geteuid() == 0:
        parser.error('Run as an ordinary user')
    output = None
    try:
        baseline = args.baseline_build.expanduser().resolve(strict=True)
        candidate = args.candidate_build.expanduser().resolve(strict=True)
        output = new_output(args.output, (baseline, candidate))
        proof = read_json(proof_path or HERE / 'proof.json')
        report = inspect_pair(baseline, candidate, proof, candidate_policy=candidate_policy)
        if not report['static_rejections']:
            structural = [sys.executable, BASE / 'compare.py', '--reference', baseline / 'NVRM.kext',
                          '--candidate', candidate / 'NVRM.kext', '--output', output / 'structural']
            if run_child(structural, output, 'structural', report):
                report['static_rejections'].append('Final structural comparison rejected or failed')
            else:
                details = read_json(output / 'structural/comparison.json')['comparison']
                report['structural_result'] = details
                if (details.get('static_rejections') or details.get('new_imports')
                        or details.get('missing_exports')
                        or set(details.get('extra_exports', [])) != set(proof['allowed_extra_exports'])):
                    report['static_rejections'].append('Final symbol/import/export changes exceed or miss the exact diagnostic scope')
            instruction = [sys.executable, REPO / 'tools/nvrm_code_audit.py',
                           '--reference', baseline / 'NVRM.kext/Contents/MacOS/NVRM',
                           '--candidate', candidate / 'NVRM.kext/Contents/MacOS/NVRM',
                           '--output', output / 'instructions']
            if args.llvm_objdump:
                instruction += ['--llvm-objdump', args.llvm_objdump]
            if run_child(instruction, output, 'instructions', report):
                report['static_rejections'].append('Final instruction audit failed operationally')
            else:
                audit = read_json(output / 'instructions/audit.json')
                if audit.get('status') != 'complete' or not audit.get('counts', {}).get('examined'):
                    report['static_rejections'].append('Final instruction audit did not produce a complete nonempty report')
                report['instruction_result'] = {key: audit.get(key) for key in
                    ('status', 'counts', 'address_dependent_summary', 'metadata_comparison', 'limitations')}
            # Recheck actual source/object hashes after the child tools. Neither
            # child is allowed to modify inputs or this captured evidence.
            final = inspect_pair(baseline, candidate, proof, candidate_policy=candidate_policy)
            keys = ('source_changes', 'input_manifests_sha256', 'evidence_metadata_sha256', 'toolchains', 'compile_record_changes',
                    'artifacts', 'object_changes', 'archive_changes', 'candidate_policy_evidence')
            if any(final.get(key) != report.get(key) for key in keys) or final['static_rejections']:
                report['static_rejections'].append('Input evidence changed or failed validation during the comparison')
        report['status'] = 'rejected' if report['static_rejections'] else 'review_required'
        (output / 'comparison.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        (output / 'summary.txt').write_text(summary(report))
        print(summary(report), end='')
        return 1 if report['static_rejections'] else 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        if output is not None:
            (output / 'failure.json').write_text(json.dumps({'status': 'failed', 'error': str(error)}, indent=2) + '\n')
        print('STOP: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
