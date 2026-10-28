#!/usr/bin/env python3
"""Read-only x86_64 NVRM instruction audit; never installs or executes an image."""

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys

from nvrm_audit.macho import load_macho, MH_OBJECT
from nvrm_audit.engine import Context, compare_contexts, parse_disassembly, parse_nm


LIMITATIONS = [
    'Identical means strict decoded instructions/encodings with the explicitly proven address references, not whole-program semantic equivalence.',
    'Symbol/LC_FUNCTION_STARTS intervals are bounded evidence, not authoritative function sizes; ambiguous entries and unsupported references remain not_comparable.',
    'Only unreachable trailing decoded NOP instructions after a return/direct jump/UD2 are separated as padding; ADD/zero fill, resumable HLT/INT3 traps, CET and prologues are never stripped.',
    'Anonymous data, unsupported/local/scattered relocations, indirect jumps, data fixups and dyld/chained bindings may prevent comparison.',
    'This does not prove all data/vtable initializers, KAPI ABI layouts, unwind behavior, runtime bindings, signatures, loadability or other-component compatibility.',
    'A complete report (exit 0) does not mean that all functions matched. Read counts and every not_comparable/different record.',
    'No image was installed, loaded, approved, signed, or added to a Kernel Collection.',
]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def new_output(value, inputs):
    raw = Path(value).expanduser()
    if not raw.is_absolute() or raw.is_symlink() or '..' in raw.parts:
        raise ValueError('Output must be a new absolute directory without symlinks or ..')
    path = raw.resolve()
    if path.exists():
        raise ValueError('Output already exists; previous reports/builds are never overwritten')
    forbidden = ('system', 'library', 'efi', 'applications')
    if (len(path.parts) < 3 or any(part.casefold() in forbidden or part.casefold().endswith('.kext') for part in path.parts)
            or path.parts[1].casefold() in ('usr', 'bin', 'sbin', 'etc', 'dev', 'proc', 'sys')
            or tuple(part.casefold() for part in path.parts[1:3]) == ('private', 'etc')):
        raise ValueError('Output must be a dedicated non-system audit directory')
    for parent in path.parents:
        if (parent / 'inputs.json').is_file() or (parent / 'build-state.json').is_file():
            raise ValueError('Output must not modify any existing reference build: ' + str(parent))
    for image in inputs:
        if path == image:
            raise ValueError('Output overlaps an input file')
        for parent in image.parents:
            if (parent / 'inputs.json').is_file() or (parent / 'build-state.json').is_file():
                if path == parent or parent in path.parents:
                    raise ValueError('Output must be outside the existing reference build: ' + str(parent))
    path.mkdir(parents=True)
    return path


def find_tool(name, explicit=None):
    if explicit:
        candidate = shutil.which(explicit) or str(Path(explicit).expanduser().absolute())
    else:
        candidate = None
        if sys.platform == 'darwin' and shutil.which('xcrun'):
            result = subprocess.run(['xcrun', '--find', name], capture_output=True, text=True)
            if result.returncode == 0:
                candidate = result.stdout.strip()
        candidate = candidate or shutil.which(name)
    if not candidate or not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
        raise ValueError('Required tool unavailable: ' + name + '; specify its absolute path (no automatic installation)')
    return str(Path(candidate).absolute())


def run_tool(output, records, label, argv, env, check=True):
    stdout, stderr = output / (label + '.stdout.txt'), output / (label + '.stderr.txt')
    with stdout.open('w') as out, stderr.open('w') as err:
        process = subprocess.run([str(arg) for arg in argv], stdout=out, stderr=err, env=env)
    records.append({'argv': [str(arg) for arg in argv], 'returncode': process.returncode,
                    'stdout': stdout.name, 'stderr': stderr.name})
    if check and process.returncode:
        raise ValueError('Tool failed; inspect ' + str(stderr))
    return stdout


def image_report(image):
    return {'path': image.path, 'sha256': image.sha256, 'size_bytes': len(image.data),
            'filetype': image.filetype, 'flags': image.flags, 'uuid': image.uuid,
            'segments': [asdict(segment) for segment in image.segments],
            'sections': [asdict(section) for section in image.sections],
            'load_commands': image.load_commands,
            'function_start_count': len(image.function_starts),
            'function_starts': image.function_starts,
            'data_in_code': image.data_in_code,
            'relocations': [asdict(rel) for rel in image.relocations],
            'relocation_count': len(image.relocations),
            'unmapped_relocation_count': sum(rel.address is None for rel in image.relocations),
            'unsupported_dynamic_fixups': image.dynamic_fixups,
            'symbol_inventory': dict(Counter(symbol.name for symbol in image.symbols)),
            'symbol_bindings': [{'name': symbol.name, 'type': symbol.type,
                                 'description': symbol.description}
                                for symbol in image.symbols]}


def metadata_comparison(images):
    result = {'uuid_equal': images[0].uuid == images[1].uuid, 'bundle_metadata': [],
              'code_signatures': [], 'segment_changes': [], 'section_changes': []}
    for image in images:
        path = Path(image.path)
        plist = path.parent.parent / 'Info.plist'
        if path.parent.name == 'MacOS' and path.parent.parent.name == 'Contents' and plist.is_file():
            with plist.open('rb') as stream:
                values = plistlib.load(stream)
            result['bundle_metadata'].append({key: values.get(key) for key in (
                'CFBundleIdentifier', 'CFBundleVersion', 'CFBundleShortVersionString', 'OSBundleLibraries')})
        else:
            result['bundle_metadata'].append(None)
        signatures = [command for command in image.load_commands if command['cmd'] == 0x1D]
        result['code_signatures'].append([{'size': command['data_size'],
            'sha256': hashlib.sha256(image.data[command['data_offset']:command['data_offset'] + command['data_size']]).hexdigest()}
            for command in signatures])
    for label, accessor in (('segment_changes', 'segments'), ('section_changes', 'sections')):
        left, right = (list(getattr(image, accessor)) for image in images)
        for index in range(max(len(left), len(right))):
            before = asdict(left[index]) if index < len(left) else None
            after = asdict(right[index]) if index < len(right) else None
            if before != after:
                result[label].append({'index': index, 'reference': before, 'candidate': after})
    result['symbol_bindings_equal'] = Counter((s.name, s.type, s.description) for s in images[0].symbols) == Counter((s.name, s.type, s.description) for s in images[1].symbols)
    result['signature_note'] = 'Signature blobs are hashed only; this tool does not approve or verify their loadability.'
    return result


def summary(report):
    counts = report['counts']
    text = ['NVRM machine-code audit', '',
            'Symbols examined: {examined}; identical: {identical}; different: {different}; not safely comparable: {not_comparable}'.format(**counts),
            'Symbolically identical: ' + str(report['address_dependent_summary']['symbolically_identical_functions']),
            '', 'Different functions:']
    different = [entry for entry in report['functions'] if entry['status'] == 'different']
    text += ['  ' + entry['symbol'] for entry in different[:25]] or ['  None detected in safely compared functions.']
    if len(different) > 25:
        text.append('  ... see audit.json for all symbols.')
    categories = Counter()
    for entry in different:
        for category in entry.get('difference_categories', []):
            categories[category] += 1
    for category in sorted(categories):
        text.extend(['', 'Examples: ' + category + ' (' + str(categories[category]) + ' functions)'])
        for entry in [item for item in different if category in item.get('difference_categories', [])][:3]:
            text.append('  ' + entry['symbol'])
            for example in entry.get('examples', [])[:3]:
                text += ['    reference ' + example['reference'][:240],
                         '    candidate ' + example['candidate'][:240]]
    text.extend(['', 'Metadata (separate from instruction identity):',
                 '  UUID equal: ' + str(report['metadata_comparison']['uuid_equal']),
                 '  Symbol binding inventory equal: ' + str(report['metadata_comparison']['symbol_bindings_equal']),
                 '  Segment records changed: ' + str(len(report['metadata_comparison']['segment_changes'])),
                 '  Section records changed: ' + str(len(report['metadata_comparison']['section_changes']))])
    text.extend(['', 'Not-comparable reasons:'])
    text += ['  %d: %s' % (count, reason) for reason, count in sorted(report['address_dependent_summary']['unknown_reasons'].items())]
    text.extend(['', 'Limits:'] + ['- ' + item for item in LIMITATIONS])
    return '\n'.join(text) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path, help='Existing official NVRM Mach-O binary, read only')
    parser.add_argument('--candidate', required=True, type=Path, help='Existing reference-004 Mach-O binary, read only')
    parser.add_argument('--output', required=True, help='New separate absolute report directory')
    parser.add_argument('--llvm-objdump', help='Explicit LLVM disassembler path')
    parser.add_argument('--nm', help='Explicit Apple nm or llvm-nm path')
    parser.add_argument('--symbols', nargs='+', help='Optional exact symbol names; default examines the full union')
    parser.add_argument('--allow-object', action='store_true', help='Allow MH_OBJECT inputs for isolated compiler probes')
    args = parser.parse_args()
    output, report = None, {'schema': 1, 'status': 'running', 'limitations': LIMITATIONS, 'commands': []}
    try:
        reference = args.reference.expanduser().resolve(strict=True)
        candidate = args.candidate.expanduser().resolve(strict=True)
        if reference == candidate or not reference.is_file() or not candidate.is_file():
            raise ValueError('Two distinct existing Mach-O files are required')
        images = [load_macho(reference), load_macho(candidate)]
        if not args.allow_object and any(image.filetype == MH_OBJECT for image in images):
            raise ValueError('MH_OBJECT requires explicit --allow-object for compiler probes')
        tools = {'llvm-objdump': find_tool('llvm-objdump', args.llvm_objdump),
                 'nm': find_tool('nm', args.nm)}
        output = new_output(args.output, [reference, candidate])
        env = os.environ.copy()
        env.update(LANG='C', LC_ALL='C')
        report['tools'] = {name: {'path': path, 'sha256': sha(path)} for name, path in tools.items()}
        for name, path in tools.items():
            run_tool(output, report['commands'], name + '-version', [path, '--version'], env, check=False)
        contexts = []
        for label, image in zip(('reference', 'candidate'), images):
            disassembly = run_tool(output, report['commands'], label + '-disassembly',
                                   [tools['llvm-objdump'], '--disassemble', '--disassemble-zeroes',
                                    '--x86-asm-syntax=att', image.path], env)
            symbols = run_tool(output, report['commands'], label + '-symbols', [tools['nm'], '-n', image.path], env)
            with disassembly.open() as stream:
                decoded = parse_disassembly(stream)
            with symbols.open() as stream:
                names = parse_nm(stream)
            if not decoded or not names:
                raise ValueError('Empty/unparseable instruction or nm inventory: ' + label)
            contexts.append(Context(image, decoded, names))
            report[label] = image_report(image)
        report.update(compare_contexts(*contexts, selected=args.symbols))
        report['metadata_comparison'] = metadata_comparison(images)
        if not report['counts']['examined']:
            raise ValueError('No function symbols were examined')
        report['status'] = 'complete'
        report['scope'] = {'selected_symbols': args.symbols, 'unit': 'one symbol name, with missing/duplicate/aliased identities explicitly uncertain'}
        for path, image in zip((reference, candidate), images):
            if sha(path) != image.sha256:
                raise ValueError('Input changed during audit: ' + str(path))
        (output / 'audit.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        text = summary(report)
        (output / 'summary.txt').write_text(text)
        print(text, end='')
        print('Full report: ' + str(output / 'audit.json'))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report.update(status='failed', error=str(error))
        if output is not None:
            (output / 'audit.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        print('STOP: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
