#!/usr/bin/env python3
"""Prepare and build an isolated, source-pinned NVRM 615 reference; never install.

Darwin flags, include paths, object/link order and archive strategy adapted from
watchthelight-labs/NvGfx build/nvgfx/build_nvrm.sh and nvrm/darwin.mk,
commit 0eff8317443f7af55dc4b4fd333173819859799c, by watchthelight.
License: PolyForm-Noncommercial-1.0.0; see the repository LICENSE.
"""
import argparse
from collections import Counter
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
NULLMOTH = '46779055f68208baa5ddfb9b2e9c77ed028e1d59'
NVIDIA = '1a82fbfda8f321b056eb3568850f9316c5e465c5'
NVGFX = '0eff8317443f7af55dc4b4fd333173819859799c'
TAG = '615.78.08'
CXX_OBJECTS = (
    'NVRM', 'os-xnu', 'os-xnu2', 'nv_xnu_stubs', 'nv_xnu_power',
    'os-nvkms-xnu', 'nvkms_shaders', 'nvrm_fbinfo', 'nvrm_gpuva', 'nvrm_surfshare',
)
LINK_OBJECTS = (
    'nv_xnu_stubs', 'nvkms_shaders', 'nvrm_fbinfo', 'nvrm_gpuva',
    'nvrm_surfshare', 'NVRM', 'os-nvkms-xnu', 'os-xnu', 'os-xnu2',
    'nv_xnu_power', 'nvrm_kapi_fields', 'nvrm_pageoff',
)


def fail(message):
    raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def capture(args, cwd=None):
    return subprocess.check_output([str(a) for a in args], cwd=cwd, text=True).strip()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def output_path(value, fresh=False):
    raw = Path(value).expanduser()
    if not raw.is_absolute() or raw.is_symlink() or '..' in raw.parts:
        fail('Output must be an absolute path, not a symlink')
    path = raw.resolve()
    if re.search(r'[^A-Za-z0-9_./-]', str(path)):
        fail('Use an output path without spaces or shell/make metacharacters')
    forbidden = (Path('/System'), Path('/Library'), Path('/usr'), Path('/bin'),
                 Path('/sbin'), Path('/etc'), Path('/private/etc'), Path('/Applications'),
                 Path('/dev'), Path('/proc'), Path('/sys'))
    if len(path.parts) < 3 or any(path == p or p in path.parents for p in forbidden):
        fail('Output must be a dedicated build directory outside system paths')
    if any(part.lower() in ('efi', 'system', 'library') or part.lower().endswith('.kext') for part in path.parts):
        fail('Output cannot be an EFI, System, Library or kext path, including on mounted volumes')
    if path == REPO or path in REPO.parents or REPO in path.parents:
        fail('Output must be outside the source repository')
    if fresh and path.exists():
        fail('Prepare requires a new output directory; nothing is overwritten')
    return path


def extract_commit(repo, commit, destination, members=()):
    """Materialize Git objects, not working-tree changes, links or alternates."""
    archive = destination.parent / (destination.name + '-input.tar')
    subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar',
                    '--output=' + str(archive), commit] + list(members), check=True)
    destination.mkdir()
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            target = destination / member.name
            if (Path(member.name).is_absolute() or '..' in Path(member.name).parts
                    or not (member.isdir() or member.isfile())):
                fail('Unexpected archive member: ' + member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
                target.chmod(member.mode & 0o777)
    archive.unlink()


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): sha(p)
            for p in sorted(root.rglob('*')) if p.is_file()}


def verify_hashes(root, hashes):
    for rel, digest in hashes.items():
        path = root / rel
        if path.is_symlink() or not path.is_file() or sha(path) != digest:
            fail('Input changed: ' + str(path))


def recipe_hashes():
    return {p.relative_to(HERE).as_posix(): sha(p) for p in sorted(HERE.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def patch_target(patch):
    """Keep patch scopes explicit; never infer an RM scope from an NVKMS path."""
    directory, target = patch['apply_directory'], patch['target_path']
    if (directory not in ('src/nvidia-modeset', 'src/nvidia')
            or not re.fullmatch(r'[A-Za-z0-9_./-]+', target)
            or Path(target).is_absolute() or '..' in Path(target).parts):
        fail('Invalid baseline patch scope')
    return directory + '/' + target


def apply_baseline_patch(ogkm, patch):
    """Require one exact before/after transition; git apply never uses fuzz."""
    relative = patch_target(patch)
    path = HERE / 'patches' / patch['normalized_path']
    if sha(path) != patch['normalized_sha256']:
        fail('Normalized patch hash mismatch: ' + patch['normalized_path'])
    verify_hashes(ogkm, {relative: patch['target_before_sha256']})
    common = ['git', 'apply', '--directory=' + patch['apply_directory']]
    changes = subprocess.check_output(common + ['--numstat', '-z', str(path)], cwd=ogkm)
    rows = changes.split(b'\0')
    if (len(rows) != 2 or rows[-1] != b'' or len(rows[0].split(b'\t')) != 3
            or rows[0].split(b'\t')[2] != relative.encode('ascii')):
        fail('Baseline patch must modify exactly its declared target: ' + relative)
    subprocess.run(common + ['--check', str(path)], cwd=ogkm, check=True)
    subprocess.run(common + [str(path)], cwd=ogkm, check=True)
    verify_hashes(ogkm, {relative: patch['target_after_sha256']})


def prepare(args):
    out = output_path(args.output, fresh=True)
    vendor = Path(args.vendor).expanduser().resolve()
    if out == vendor or out in vendor.parents or vendor in out.parents:
        fail('Output and vendor input must be separate trees')
    if capture(['git', '-C', vendor, 'rev-parse', 'HEAD']) != NVIDIA:
        fail('Vendor HEAD must be the pinned NVIDIA 615.78.08 commit')
    if capture(['git', '-C', vendor, 'status', '--porcelain', '--untracked-files=normal']):
        fail('Vendor checkout must be clean; preserve existing patches in their own tree')
    if capture(['git', '-C', REPO, 'rev-parse', NULLMOTH + '^{commit}']) != NULLMOTH:
        fail('Pinned NullMoth 2.5.4 source is unavailable in local Git history')
    proof = json.loads((HERE / 'patches/patch-proof.json').read_text())
    if (proof['format'], proof['nullmoth_source_commit'], proof['nvidia_commit'], proof['nvgfx_reference_commit']) != (2, NULLMOTH, NVIDIA, NVGFX):
        fail('Patch manifest source pins disagree with the runner')
    for patch in proof['patch_order']:
        patch_target(patch)
        if sha(HERE / 'patches' / patch['normalized_path']) != patch['normalized_sha256']:
            fail('Normalized patch hash mismatch: ' + patch['normalized_path'])
        original = subprocess.check_output(['git', '-C', str(REPO), 'show',
                                            NULLMOTH + ':' + patch['original_path']])
        if hashlib.sha256(original).hexdigest() != patch['original_sha256']:
            fail('Original patch provenance mismatch')
    out.mkdir(parents=True)
    extract_commit(REPO, NULLMOTH, out / 'nullmoth', ('kexts/NVRM',))
    extract_commit(vendor, NVIDIA, out / 'ogkm')
    verify_hashes(out / 'ogkm', proof['pristine_sources_sha256'])
    verify_hashes(out / 'ogkm', proof['preserved_sources_sha256'])
    # No old helper is invoked: strict patches are applied exactly once, before
    # building the paired archive. Source input repositories remain untouched.
    for patch in proof['patch_order']:
        apply_baseline_patch(out / 'ogkm', patch)
    verify_hashes(out / 'ogkm', proof['patched_sources_sha256'])
    verify_hashes(out / 'ogkm', proof['preserved_sources_sha256'])
    hook_source = out / 'nullmoth/kexts/NVRM/os-nvkms-xnu.cpp'
    if 'nvkms_yuv420_diag_claim' in hook_source.read_text():
        fail('Diagnostic hook must be absent from the reference snapshot')
    epoch = int(capture(['git', '-C', REPO, 'show', '-s', '--format=%ct', NULLMOTH]))
    manifest = {
        'format': 1, 'purpose': 'reference-only; not installed or hardware-qualified',
        'output': str(out), 'nullmoth_commit': NULLMOTH, 'nvidia_commit': NVIDIA,
        'nvidia_tag': TAG, 'nvgfx_recipe_commit': NVGFX, 'source_date_epoch': epoch,
        'diagnostic_enabled': False, 'patch_order': proof['patch_order'],
        'patch_proof_format': proof['format'],
        'preserved_sources_sha256': proof['preserved_sources_sha256'],
        'recipe_sha256': recipe_hashes(),
        'nullmoth_sha256': tree_hashes(out / 'nullmoth'),
        'vendor_sha256': tree_hashes(out / 'ogkm'),
        'metadata_policy': 'Unchanged source KMOD/plist 0.1; generated NVIDIA ID is 615, not the old helper\'s 610 ID',
    }
    save(out / 'inputs.json', manifest)
    print('Prepared reference inputs at ' + str(out))
    print('No diagnostic patch or NvGfx power modifications included; no compilation or installation performed.')


def load_inputs(value):
    out = output_path(value)
    if not (out / 'inputs.json').is_file():
        fail('Run prepare first')
    manifest = json.loads((out / 'inputs.json').read_text())
    if (manifest['format'], manifest['output'], manifest['nullmoth_commit'], manifest['nvidia_commit'], manifest['diagnostic_enabled']) != (1, str(out), NULLMOTH, NVIDIA, False):
        fail('Unexpected input manifest')
    if manifest['recipe_sha256'] != recipe_hashes():
        fail('Build recipe changed after prepare; prepare a new directory')
    verify_hashes(out / 'nullmoth', manifest['nullmoth_sha256'])
    verify_hashes(out / 'ogkm', manifest['vendor_sha256'])
    for folder, key in (('nullmoth', 'nullmoth_sha256'), ('ogkm', 'vendor_sha256')):
        if tree_hashes(out / folder) != manifest[key]:
            fail('Input file inventory changed: ' + folder)
    return out, manifest


def validate_make_paths(sdk, tools):
    """Restrict paths expanded by Make/shell rules, not direct argv metadata.

    Python, make itself and codesign are launched through subprocess argv;
    a Homebrew interpreter's '@' is not a shell or Make expansion there.
    Staged source/object paths remain restricted by output_path().
    """
    expanded = [('SDK', sdk)] + [(name, tools[name]) for name in
                                  ('clang', 'clang++', 'ld', 'ar', 'libtool')]
    for label, path in expanded:
        if re.search(r'[^A-Za-z0-9_./+:-]', path):
            fail('SDK/Make-expanded tool path must not contain spaces or make/shell '
                 'metacharacters (' + label + '): ' + path)


def _count_examples(counts, limit=5):
    return ', '.join(repr(path) + ' (x' + str(count) + ')'
                     for path, count in sorted(counts.items())[:limit])


def validate_object_manifest(text, rows, component, report_path=None):
    """Compare exact object multisets; reject all non-object stdout lines.

    stderr is recorded separately by Recorder. No banner whitelist or arbitrary
    output filtering belongs here. Full differences are retained in JSON when
    called by the build; terminal examples are deliberately bounded.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    pattern = r'_out/Darwin_x86_64/[A-Za-z0-9_.+-]+\.o'
    objects = [line for line in lines if re.fullmatch(pattern, line)]
    invalid = [line for line in lines if not re.fullmatch(pattern, line)]
    outputs = [argv[argv.index('-o') + 1] for _, argv in rows]
    expected, actual = Counter(outputs), Counter(objects)
    missing, unexpected = expected - actual, actual - expected
    duplicate_expected = {path: count for path, count in expected.items() if count != 1}
    duplicate_actual = {path: count for path, count in actual.items() if count != 1}
    invalid_outputs = [path for path in outputs if not re.fullmatch(pattern, path)]
    report = {
        'component': component, 'compiler_objects': len(outputs),
        'manifest_lines': len(lines), 'manifest_objects': len(objects),
        'missing': dict(missing), 'unexpected': dict(unexpected),
        'duplicate_compile_objects': duplicate_expected,
        'duplicate_manifest_objects': duplicate_actual,
        'invalid_manifest_lines': invalid, 'invalid_compile_outputs': invalid_outputs,
    }
    valid = bool(outputs) and not any((missing, unexpected, duplicate_expected,
                                      duplicate_actual, invalid, invalid_outputs))
    report['status'] = 'passed' if valid else 'failed'
    if report_path is not None:
        save(report_path, report)
    if not valid:
        fail('Compile command/object manifest mismatch for ' + component
             + ': compiler=' + str(len(outputs)) + ', manifest=' + str(len(lines))
             + ', valid_objects=' + str(len(objects))
             + '; missing=' + str(sum(missing.values())) + ' [' + _count_examples(missing) + ']'
             + '; unexpected=' + str(sum(unexpected.values())) + ' [' + _count_examples(unexpected) + ']'
             + '; duplicate_manifest=' + str(len(duplicate_actual)) + ' [' + _count_examples(duplicate_actual) + ']'
             + '; duplicate_compile=' + str(len(duplicate_expected)) + ' [' + _count_examples(duplicate_expected) + ']'
             + '; invalid_stdout=' + str(len(invalid)) + ' ' + repr([line[:200] for line in invalid[:5]])
             + '; invalid_compile_outputs=' + str(len(invalid_outputs)) + ' ' + repr(invalid_outputs[:5]))
    return objects


def compile_commands(text):
    """Extract one argv per real C/C++ object rule, excluding make chatter."""
    rows = []
    for line in text.splitlines():
        if ' -c ' not in line or ' -o ' not in line:
            continue
        argv = shlex.split(line.split(' && ', 1)[0])
        if '-c' not in argv or '-o' not in argv:
            continue
        if '-mfunction-return=thunk-extern' in argv:
            fail('Linux return thunk survived the Darwin overlay')
        rows.append((line, argv))
    outputs = [argv[argv.index('-o') + 1] for _, argv in rows]
    duplicates = {path: count for path, count in Counter(outputs).items() if count > 1}
    if duplicates:
        fail('Duplicate object in compile command recording: ' + str(len(duplicates))
             + ' [' + _count_examples(duplicates) + ']')
    return rows


def template_command(rows, source, replacement, output):
    matches = [argv for _, argv in rows if argv[argv.index('-c') + 1] == source]
    if len(matches) != 1:
        fail('Expected one compile template for ' + source)
    argv = list(matches[0])
    for flag, value in (('-c', replacement), ('-o', output), ('-MF', str(output) + '.d'), ('-MT', output)):
        if flag in argv:
            argv[argv.index(flag) + 1] = str(value)
        elif flag in ('-c', '-o'):
            fail('Missing required template argument ' + flag)
    return argv


class Recorder:
    def __init__(self, out, env):
        self.out, self.env = out, env
        self.log = out / 'commands.jsonl'

    def run(self, argv, cwd, name):
        argv = [str(a) for a in argv]
        path = self.out / 'logs' / (name + '.log')
        stderr_path = self.out / 'logs' / (name + '.stderr.log')
        with self.log.open('a') as stream:
            stream.write(json.dumps({'argv': argv, 'cwd': str(cwd), 'log': name,
                                     'stdout_log': str(path), 'stderr_log': str(stderr_path)}) + '\n')
        print(name, flush=True)
        # NVIDIA utils.mk probes LD_IS_LLD with $(shell $(LD) -v). Apple ld
        # writes its version to stderr while make's target emits stdout data.
        # Keep both streams, but never return diagnostics as object-list data.
        with path.open('w') as stdout, stderr_path.open('w') as stderr:
            result = subprocess.run(argv, cwd=cwd, env=self.env, stdout=stdout, stderr=stderr)
        if result.returncode:
            fail('Command failed (exit ' + str(result.returncode) + '); inspect '
                 + str(path) + ' and ' + str(stderr_path))
        return path.read_text()


def build(args, input_loader=None, artifact_metadata_version='0.1'):
    if platform.system() != 'Darwin':
        fail('Compilation/linking require macOS; prepare and Linux tests are available here')
    if os.geteuid() == 0:
        fail('Run as an ordinary user; no administrator privileges are required')
    from archives import validate_archive

    # The separate experiment runner supplies its strict diagnostic loader.
    # The reference CLI retains this default loader and rejects diagnostics.
    out, manifest = (input_loader or load_inputs)(args.output)
    diagnostic_enabled = manifest['diagnostic_enabled']
    if artifact_metadata_version != '0.1' and (
            artifact_metadata_version != '2.5.4' or input_loader is None
            or manifest.get('format') != 3
            or manifest.get('build_kind') != 'yuv420-ab-metadata-experiment'
            or manifest.get('diagnostic_enabled') is not True
            or manifest.get('metadata_enabled') is not True
            or manifest.get('experimental') is not True
            or manifest.get('metadata_version') != artifact_metadata_version):
        fail('A patched artifact version requires the explicit isolated metadata profile')
    if (out / 'build-state.json').exists():
        fail('This is a single-attempt build directory; prepare a fresh output for retries')
    if not 1 <= args.jobs <= 32:
        fail('Jobs must be between 1 and 32')
    reference = Path(args.reference).expanduser().resolve()
    if not (reference / 'Contents/MacOS/NVRM').is_file():
        fail('A complete, backed-up official 2.5.4 NVRM.kext reference is required')
    import plistlib
    ref_plist = plistlib.loads((reference / 'Contents/Info.plist').read_bytes())
    if ref_plist.get('CFBundleIdentifier') != 'com.nullmoth.NVRM' or ref_plist.get('CFBundleVersion') != '2.5.4':
        fail('Reference must identify the official com.nullmoth.NVRM version 2.5.4')
    if reference == out or out in reference.parents or reference in out.parents:
        fail('Keep the reference bundle outside the build output')
    for folder in ('logs', 'tmp', 'glue'):
        (out / folder).mkdir()
    save(out / 'build-state.json', {'state': 'started', 'reference': str(reference),
                                    'diagnostic_enabled': diagnostic_enabled,
                                    'reference_binary_sha256': sha(reference / 'Contents/MacOS/NVRM')})
    preflight = [sys.executable, str(HERE / 'preflight.py'), '--output', str(out / 'preflight')]
    if args.sdk:
        preflight += ['--sdk', args.sdk]
    subprocess.run(preflight, check=True)
    checks = json.loads((out / 'preflight/preflight.json').read_text())
    if checks['status'] != 'passed':
        fail('Preflight did not pass')
    sdk = checks['sdk']['path']
    tools = {name: item['path'] for name, item in checks['tools'].items()}
    validate_make_paths(sdk, tools)
    env = {key: os.environ[key] for key in ('HOME', 'DEVELOPER_DIR') if key in os.environ}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', SDKROOT=sdk,
               TMPDIR=str(out / 'tmp'), LANG='C', LC_ALL='C', TZ='UTC',
               SOURCE_DATE_EPOCH=str(manifest['source_date_epoch']))
    record = Recorder(out, env)
    flags = ['-arch', 'x86_64', '-mmacosx-version-min=15.0', '-mkernel',
             '-DNV_UNIX', '-DNV_BSD', '-isysroot', sdk,
             '-ffile-prefix-map=' + str(out) + '=/nvrm-reference']
    stamp = datetime.datetime.fromtimestamp(manifest['source_date_epoch'], datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    common = ['-f', 'Makefile', '-f', str(HERE / 'darwin.mk'), 'NV_VERBOSE=1',
              'CC=' + shlex.quote(tools['clang']), 'CXX=' + shlex.quote(tools['clang++']),
              'LD=' + shlex.quote(tools['ld']), 'AR=' + shlex.quote(tools['ar']),
              'NVRM615_LIBTOOL=' + shlex.quote(tools['libtool']),
              'TARGET_OS=Darwin', 'TARGET_ARCH=x86_64', 'NV_BUILD_USER=reference',
              'NV_BUILD_HOST=isolated', 'DATE=printf ' + stamp,
              'EXTRA_CFLAGS=' + shlex.join(flags)]
    ogkm = out / 'ogkm'
    commands = {}
    for component, archive in (('nvidia', 'libnvkernel.a'), ('nvidia-modeset', 'libnvkms.a')):
        cwd = ogkm / 'src' / component
        specific = ['NVRM615_LIB=' + archive]
        if component == 'nvidia-modeset':
            specific += ['SHADER_OBJS=']
        make = [tools['make']] + common + specific
        record.run(make + ['-j' + str(args.jobs), 'reference-archive'], cwd, component + '-build')
        raw = record.run(make + ['-n', '-B', 'reference-objs'], cwd, component + '-commands')
        rows = compile_commands(raw)
        objects_text = record.run(make + ['reference-print-objs'], cwd, component + '-objects')
        objects = validate_object_manifest(objects_text, rows, component,
                                           out / (component + '-manifest-check.json'))
        archive_path = cwd / '_out/Darwin_x86_64' / archive
        archive_report_path = out / (component + '-archive-check.json')
        try:
            archive_report = validate_archive(archive_path, objects)
        except ValueError as error:
            save(archive_report_path, {'status': 'failed', 'archive': str(archive_path),
                                       'error': str(error)})
            fail('Archive validation failed for ' + component + ': ' + str(error))
        archive_report['status'] = 'passed'
        save(archive_report_path, archive_report)
        (cwd / '_out/Darwin_x86_64/compile_cmds.sh').write_text('\n'.join(line for line, _ in rows) + '\n')
        save(out / (component + '-objects.json'), objects)
        commands[component] = rows

    kext = out / 'nullmoth/kexts/NVRM'
    nv, kms, c = ogkm / 'src/nvidia', ogkm / 'src/nvidia-modeset', ogkm / 'src/common'
    nv_paths = ('arch/nvalloc/unix/include', 'arch/nvalloc/common/inc', 'arch/nvalloc/common/inc/gsp',
                'inc', 'inc/os', 'inc/kernel', 'kernel/inc', 'interface', 'inc/libraries',
                'src/libraries', 'generated', 'src/mm/uvm/interface')
    c_paths = ('sdk/nvidia/inc', 'sdk/nvidia/inc/hw', 'inc', 'shared/inc', 'nvlink/interface',
               'inc/swref', 'inc/swref/published', 'uproc/os/libos-v2.0.0/include',
               'uproc/os/common/include', 'inc/displayport', 'nvlink/inband/interface',
               'unix/nvidia-push/interface', 'unix/common/inc', 'modeset', 'unix/common/utils/interface')
    kms_paths = ('os-interface/include', 'kapi/interface', 'kapi/include', 'interface', 'include')
    includes = [checks['sdk']['kernel_headers'], str(kext)]
    includes += [str(nv / p) for p in nv_paths] + [str(c / p) for p in c_paths] + [str(kms / p) for p in kms_paths]
    rm_defines = [token for token in commands['nvidia'][0][1]
                  if token.startswith('-D') and token not in ('-DNVRM', '-D_LANGUAGE_C')]
    cpp_flags = flags + ['-fapple-kext', '-nostdinc'] + ['-I' + p for p in includes]
    cpp_flags += ['-DKERNEL', '-DKERNEL_PRIVATE', '-DDRIVER_PRIVATE', '-DAPPLE', '-DNeXT'] + rm_defines
    cpp_flags += ['-std=c++17', '-fno-rtti', '-fno-exceptions', '-fno-builtin', '-fno-common',
                  '-Wall', '-Wno-unused-parameter', '-Wno-inconsistent-missing-override', '-Wno-unused-function', '-O2']
    glue = out / 'glue'
    for stem in CXX_OBJECTS:
        record.run([tools['clang++']] + cpp_flags + ['-c', kext / (stem + '.cpp'), '-o', glue / (stem + '.o')],
                   kext, 'glue-' + stem)
    for component, template, stem in (('nvidia', 'arch/nvalloc/unix/src/osapi.c', 'nvrm_pageoff'),
                                       ('nvidia-modeset', 'kapi/src/nvkms-kapi.c', 'nvrm_kapi_fields')):
        argv = template_command(commands[component], template, kext / (stem + '.c'), glue / (stem + '.o'))
        record.run(argv, ogkm / 'src' / component, 'glue-' + stem)

    candidate = out / 'NVRM.kext'
    (candidate / 'Contents/MacOS').mkdir(parents=True)
    link = [tools['clang++'], '-arch', 'x86_64', '-mmacosx-version-min=15.0', '-isysroot', sdk,
            '-fapple-kext', '-nostdlib', '-Xlinker', '-kext']
    link += [glue / (stem + '.o') for stem in LINK_OBJECTS]
    link += [nv / '_out/Darwin_x86_64/libnvkernel.a', kms / '_out/Darwin_x86_64/libnvkms.a',
             '-lkmodc++', '-lkmod', '-lcc_kext', '-o', candidate / 'Contents/MacOS/NVRM']
    record.run(link, out, 'link')
    shutil.copyfile(kext / 'Info.plist', candidate / 'Contents/Info.plist')
    record.run([tools['codesign'], '-f', '-s', '-', '--digest-algorithm=sha256', candidate], out, 'codesign')
    record.run([tools['codesign'], '--verify', '--strict', candidate], out, 'codesign-verify')
    # The default reference/diagnostic metadata stays at the pinned source 0.1.
    # A separate, strict metadata loader may record its isolated patched version.
    # These artifacts are not an official package or ready to install.
    verify_hashes(out / 'nullmoth', manifest['nullmoth_sha256'])
    verify_hashes(out / 'ogkm', manifest['vendor_sha256'])
    save(out / 'artifacts.json', {
        'files': {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob('*'))
                  if p.is_file() and (p.suffix in ('.o', '.a') or candidate in p.parents)},
        'reference_sha256': sha(reference / 'Contents/MacOS/NVRM'),
        'diagnostic_enabled': diagnostic_enabled, 'metadata_version': artifact_metadata_version,
    })
    compare = [sys.executable, str(HERE / 'compare.py'), '--reference', str(reference),
               '--candidate', str(candidate), '--output', str(out / 'comparison')]
    result = subprocess.run(compare, env=env)
    save(out / 'build-state.json', {'state': 'built; static review and macOS hardware validation still required',
                                    'comparison_returncode': result.returncode, 'diagnostic_enabled': diagnostic_enabled})
    if result.returncode:
        fail('Reference comparison rejected or failed; inspect comparison output; do not integrate diagnostics')
    print('Reference artifact built and compared. Review all differences; not installed or hardware-qualified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    prepare_parser = actions.add_parser('prepare', help='Linux/macOS: materialize pinned inputs and baseline patches')
    prepare_parser.add_argument('--vendor', required=True, help='Clean local NVIDIA 615.78.08 Git checkout')
    prepare_parser.add_argument('--output', required=True, help='New absolute directory outside either source checkout')
    build_parser = actions.add_parser('build', help='macOS: preflight, compile, link, sign and statically compare only')
    build_parser.add_argument('--output', required=True)
    build_parser.add_argument('--reference', required=True, help='Backed-up complete official 2.5.4 NVRM.kext; read only')
    build_parser.add_argument('--sdk', help='Explicit macOS SDK path; otherwise SDKROOT/selected SDK')
    build_parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    try:
        (prepare if args.action == 'prepare' else build)(args)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError, KeyError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
