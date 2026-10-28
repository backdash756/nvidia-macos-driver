#!/usr/bin/env python3
"""Reproduce the two KAPI compiler-flag differences in a new, isolated directory.

The existing reference build, its inputs, and the official binary are read only.
Only nvrm_kapi_fields.c is compiled, using the recorded command. This never links,
installs, loads, signs, or replaces NVRM, and does not modify baseline build flags.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


NULLMOTH = '46779055f68208baa5ddfb9b2e9c77ed028e1d59'
NVIDIA = '1a82fbfda8f321b056eb3568850f9316c5e465c5'
SYMBOLS = ('_nvrm_kapi_mem_handle', '_nvrm_kapi_dev_handles')
FLAG_REPLACEMENTS = {'-fomit-frame-pointer': '-fno-omit-frame-pointer',
                     '-fcf-protection=branch': '-fcf-protection=none'}
FRAME_POINTER_BASELINES = (('-fomit-frame-pointer',),
                          ('-fno-omit-frame-pointer', '-fomit-frame-pointer'))
OUTPUT_FLAGS = ('-o', '-MF', '-MT')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for data in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def save(path, report):
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')


def is_inside(path, root):
    return path == root or root in path.parents


def reference_binary(value):
    path = Path(value).expanduser().resolve(strict=True)
    if path.is_dir():
        path = (path / 'Contents/MacOS/NVRM').resolve(strict=True)
    if not path.is_file():
        raise ValueError('Reference must be an existing NVRM binary or complete NVRM.kext')
    return path


def prepare_output(value, protected):
    raw = Path(value).expanduser()
    if not raw.is_absolute() or '..' in raw.parts or raw.is_symlink():
        raise ValueError('Output must be a new absolute directory without symlinks or ..')
    path = raw.resolve()
    for parent in path.parents:
        if (parent / 'inputs.json').is_file() or (parent / 'build-state.json').is_file():
            raise ValueError('Output must not modify an existing reference build: ' + str(parent))
    for root in protected:
        root = Path(root).resolve()
        if is_inside(path, root) or is_inside(root, path):
            raise ValueError('Output must be separate from every input: ' + str(root))
    components = tuple(part.casefold() for part in path.parts)
    if (len(components) < 3 or any(part in ('efi', 'system', 'library')
                                 or part.endswith('.kext') for part in components)
            or components[1] in ('usr', 'bin', 'sbin', 'etc', 'dev', 'proc', 'sys', 'applications')
            or components[1:3] == ('private', 'etc')):
        raise ValueError('Output must be a dedicated report directory outside system paths')
    if path.exists():
        raise ValueError('Output already exists; choose a new directory')
    path.mkdir(parents=True)
    return path


def snapshot_tree(root):
    """Record all pre-existing build files, not just the source manifest."""
    root_stat = root.stat()
    result = {'.': {'directory': True, 'mtime_ns': root_stat.st_mtime_ns, 'mode': root_stat.st_mode}}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = {'symlink': os.readlink(path)}
        elif path.is_dir():
            stat = path.stat()
            result[relative] = {'directory': True, 'mtime_ns': stat.st_mtime_ns, 'mode': stat.st_mode}
        elif path.is_file():
            stat = path.stat()
            result[relative] = {'sha256': sha256(path), 'size': stat.st_size,
                                'mtime_ns': stat.st_mtime_ns, 'mode': stat.st_mode}
    return result


def verify_sources(build, manifest):
    for directory, key in (('nullmoth', 'nullmoth_sha256'), ('ogkm', 'vendor_sha256')):
        mapping = manifest.get(key)
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError('Missing source hash manifest: ' + key)
        root = build / directory
        for relative, expected in mapping.items():
            rel = Path(relative)
            if rel.is_absolute() or '..' in rel.parts:
                raise ValueError('Invalid source manifest path')
            path = root / rel
            if path.is_symlink() or not path.is_file() or sha256(path) != expected:
                raise ValueError('Recorded source changed: ' + str(path))


def load_template(build):
    manifest = json.loads((build / 'inputs.json').read_text())
    if (manifest.get('format'), manifest.get('output'), manifest.get('nullmoth_commit'),
            manifest.get('nvidia_commit'), manifest.get('diagnostic_enabled')) != (
            1, str(build), NULLMOTH, NVIDIA, False):
        raise ValueError('Build must be the pinned, non-diagnostic reference source snapshot')
    if 'kexts/NVRM/nvrm_kapi_fields.c' not in manifest.get('nullmoth_sha256', {}):
        raise ValueError('The KAPI source is missing from the source hash manifest')
    verify_sources(build, manifest)
    preflight = json.loads((build / 'preflight/preflight.json').read_text())
    if preflight.get('status') != 'passed':
        raise ValueError('The recorded reference-build preflight did not pass')
    commands = [json.loads(line) for line in (build / 'commands.jsonl').read_text().splitlines() if line.strip()]
    matches = [entry for entry in commands if entry.get('log') == 'glue-nvrm_kapi_fields']
    if len(matches) != 1:
        raise ValueError('Expected exactly one recorded glue-nvrm_kapi_fields command')
    entry = matches[0]
    argv = entry.get('argv')
    if (not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or '\0' in arg for arg in argv)):
        raise ValueError('Recorded compiler command must be a string argv list')
    if not Path(argv[0]).is_absolute():
        raise ValueError('Recorded compiler must use an absolute executable path')
    compiler = preflight['tools']['clang']
    if Path(argv[0]).resolve() != Path(compiler['path']).resolve():
        raise ValueError('Recorded compiler does not match the successful preflight')
    if sha256(argv[0]) != compiler['sha256']:
        raise ValueError('Compiler binary changed since the reference build')
    cwd = Path(entry['cwd']).resolve(strict=True)
    if cwd != (build / 'ogkm/src/nvidia-modeset').resolve(strict=True):
        raise ValueError('Unexpected compiler working directory')
    for flag in ('-c', *OUTPUT_FLAGS):
        if argv.count(flag) != 1 or argv.index(flag) + 1 >= len(argv):
            raise ValueError('Expected exactly one explicit ' + flag + ' argument')
    source = Path(argv[argv.index('-c') + 1]).resolve(strict=True)
    if source != (build / 'nullmoth/kexts/NVRM/nvrm_kapi_fields.c').resolve(strict=True):
        raise ValueError('Recorded source is not the pinned nvrm_kapi_fields.c')
    original_object = Path(argv[argv.index('-o') + 1]).resolve(strict=True)
    if original_object != (build / 'glue/nvrm_kapi_fields.o').resolve(strict=True):
        raise ValueError('Unexpected original KAPI object path')
    for arg in argv[1:]:
        if arg.startswith('@') or arg in ('&&', '|', ';', '--'):
            raise ValueError('Response files and shell command text are not replayed')
        if (arg.startswith(('-save-temps', '--save-temps', '-ftime-trace', '-fmodules',
                            '-fmodule-', '-MJ', '-serialize-diagnostics', '--serialize-diagnostics',
                            '-dependency-file', '--dependency-file', '-Xclang', '-Xplugin', '-fplugin',
                            '-fprofile', '-ftest-coverage', '--coverage', '-fstack-usage',
                            '-foptimization-record', '-fsave-optimization-record', '-Wa,', '-Wp,',
                            '--analyze', '-analyze'))):
            raise ValueError('Unreviewed side-output/plugin option: ' + arg)
        if any(arg.startswith(flag) and arg != flag for flag in OUTPUT_FLAGS):
            raise ValueError('Only explicit separate output/dependency arguments are supported: ' + arg)
    # Clang uses the last frame-pointer option. The recorded NVKMS template
    # can inherit no-omit before adding omit; keep both original arguments in
    # the control and replace only the final omit in the isolated variant.
    # Reject extra/leaf-frame-pointer or CET overrides rather than guessing
    # their effect or silently broadening the two-flag experiment.
    frames = tuple(arg for arg in argv if arg.startswith(('-f', '-m'))
                   and 'frame-pointer' in arg)
    cet = [arg for arg in argv if arg.startswith(('-fcf-protection', '-fno-cf-protection',
        '-mshstk', '-mno-shstk', '-mmanual-endbr', '-mno-manual-endbr', '-mcet', '-mno-cet'))]
    if frames not in FRAME_POINTER_BASELINES or cet != ['-fcf-protection=branch']:
        raise ValueError('The recorded command does not have an observed ordered frame-pointer/CET baseline')
    sdk = Path(preflight['sdk']['path']).resolve(strict=True)
    if argv.count('-isysroot') != 1 or Path(argv[argv.index('-isysroot') + 1]).resolve() != sdk:
        raise ValueError('Recorded SDK selection differs from the successful preflight')
    headers = Path(preflight['sdk']['kernel_headers'])
    for relative, expected in preflight['sdk'].get('key_header_sha256', {}).items():
        if sha256(headers / relative) != expected:
            raise ValueError('Recorded SDK header changed: ' + relative)
    return {'manifest': manifest, 'preflight': preflight, 'entry': entry, 'argv': argv,
            'cwd': cwd, 'source': source, 'original_object': original_object, 'sdk': sdk}


def planned_command(template, output, variant=False):
    argv = list(template['argv'])
    obj = output / ('variant.o' if variant else 'control.o')
    for flag, value in (('-o', obj), ('-MF', obj.with_suffix('.d')), ('-MT', obj)):
        argv[argv.index(flag) + 1] = str(value)
    if variant:
        argv = [FLAG_REPLACEMENTS.get(arg, arg) for arg in argv]
    return argv, obj


def run_command(argv, cwd, output, label, env):
    stdout, stderr = output / (label + '.stdout.txt'), output / (label + '.stderr.txt')
    with stdout.open('w') as out, stderr.open('w') as err:
        result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdout=out, stderr=err)
    record = {'label': label, 'argv': list(map(str, argv)), 'cwd': str(cwd),
              'returncode': result.returncode, 'stdout': stdout.name, 'stderr': stderr.name}
    with (output / 'commands.jsonl').open('a') as stream:
        stream.write(json.dumps(record) + '\n')
    if result.returncode:
        raise RuntimeError('Command failed: ' + label + '; inspect ' + str(stderr))
    return record


def run_audit(reference, candidate, output, env, llvm_objdump=None):
    audit = Path(__file__).with_name('nvrm_code_audit.py')
    argv = [sys.executable, str(audit), '--reference', str(reference), '--candidate', str(candidate),
            '--output', str(output), '--allow-object', '--symbols', *SYMBOLS]
    if llvm_objdump:
        argv += ['--llvm-objdump', str(llvm_objdump)]
    result = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Audit report classification is evaluated separately. A code difference is
    # not the same as a failed disassembler or missing report.
    return {'argv': argv, 'returncode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr, 'output': str(output)}


def audit_statuses(record):
    """Require exactly the requested symbols in the completed audit report."""
    report_path = Path(record['output']) / 'audit.json'
    if record['returncode'] != 0 or not report_path.is_file():
        return None
    report = json.loads(report_path.read_text())
    functions = report.get('functions', [])
    wanted = [entry for entry in functions if entry.get('symbol') in SYMBOLS]
    if len(wanted) != len(SYMBOLS) or {entry.get('symbol') for entry in wanted} != set(SYMBOLS):
        return None
    return {entry['symbol']: entry.get('status') for entry in wanted}


def audit_verified(record):
    statuses = audit_statuses(record)
    return statuses is not None and all(status == 'identical' for status in statuses.values())


def perform_probe(args):
    build = Path(args.build).expanduser().resolve(strict=True)
    reference = reference_binary(args.reference)
    template = load_template(build)
    protected_reference = next((parent for parent in reference.parents if parent.suffix == '.kext'), reference)
    output = prepare_output(args.output, (build, protected_reference))
    (output / 'tmp').mkdir()
    initial_build = snapshot_tree(build)
    initial_reference = sha256(reference)
    save(output / 'input-before.json', {'build': initial_build, 'reference_sha256': initial_reference})
    env = {key: os.environ[key] for key in ('HOME', 'DEVELOPER_DIR') if key in os.environ}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', SDKROOT=str(template['sdk']),
               TMPDIR=str(output / 'tmp'), LANG='C', LC_ALL='C', TZ='UTC',
               SOURCE_DATE_EPOCH=str(template['manifest']['source_date_epoch']))
    report = {'purpose': 'isolated compiler-cause reproduction, not a driver build or load approval',
              'build': str(build), 'reference': str(reference), 'source_sha256': sha256(template['source']),
              'original_object_sha256': sha256(template['original_object']),
              'flag_changes': FLAG_REPLACEMENTS, 'symbols': list(SYMBOLS), 'status': 'running',
              'toolchain': template['preflight']['tools']['clang'], 'audits': {}}
    result = 2
    try:
        for variant in (False, True):
            label = 'variant' if variant else 'control'
            argv, obj = planned_command(template, output, variant)
            run_command(argv, template['cwd'], output, label + '-compile', env)
            report[label] = {'object': str(obj), 'sha256': sha256(obj), 'argv': argv}
        audits = (
            ('control-vs-recorded-object', template['original_object'], Path(report['control']['object'])),
            ('control-vs-official', reference, Path(report['control']['object'])),
            ('variant-vs-official', reference, Path(report['variant']['object'])),
        )
        for name, first, second in audits:
            record = run_audit(first, second, output / name, env, getattr(args, 'llvm_objdump', None))
            report['audits'][name] = record
            if record['returncode']:
                raise RuntimeError('Machine-code audit failed: ' + name + '; ' + record['stderr'])
        control_ok = audit_verified(report['audits']['control-vs-recorded-object'])
        variant_ok = audit_verified(report['audits']['variant-vs-official'])
        baseline_statuses = audit_statuses(report['audits']['control-vs-official'])
        baseline_differs = baseline_statuses is not None and all(
            status == 'different' for status in baseline_statuses.values())
        report['baseline_differs_from_official'] = baseline_differs
        report['control_reproduces_recorded_object'] = control_ok
        report['flag_variant_matches_official'] = variant_ok
        reproduced = control_ok and variant_ok and baseline_differs
        report['status'] = 'reproduced' if reproduced else 'not_verified'
        result = 0 if reproduced else 1
    except (OSError, ValueError, RuntimeError) as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        print(str(error), file=sys.stderr)
    finally:
        final_build = snapshot_tree(build)
        final_reference = sha256(reference)
        unchanged = final_build == initial_build and final_reference == initial_reference
        integrity = {'unchanged': unchanged, 'build_before': initial_build, 'build_after': final_build,
                     'reference_before_sha256': initial_reference, 'reference_after_sha256': final_reference}
        save(output / 'input-integrity.json', integrity)
        report['inputs_unchanged'] = unchanged
        if not unchanged:
            report['status'] = 'failed'
            report['error'] = 'An existing input changed during the probe; do not treat the result as verified'
            result = 2
        save(output / 'probe.json', report)
        print(str(output / 'probe.json'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, help='Existing completed reference-004 directory; read only')
    parser.add_argument('--reference', required=True, help='Official NVRM binary or NVRM.kext; read only')
    parser.add_argument('--output', required=True, help='New separate report/object directory')
    parser.add_argument('--llvm-objdump', type=Path, help='Optional explicit llvm-objdump executable')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('Compilation requires macOS; Linux can run only the regression fixtures')
    if os.geteuid() == 0:
        parser.error('Run as an ordinary user; no administrator privileges are required')
    try:
        return perform_probe(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    sys.exit(main())
