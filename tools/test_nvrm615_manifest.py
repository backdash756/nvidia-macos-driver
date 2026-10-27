#!/usr/bin/env python3
"""Regress path roles and strict RM/NVKMS object manifests without a Darwin build.

Run pure/recorder regressions:
    python3 tools/test_nvrm615_manifest.py
Also reproduce Apple's linker stderr through the real pinned NVIDIA Makefiles:
    python3 tools/test_nvrm615_manifest.py --vendor /path/to/open-gpu-615

All generated files are in temporary directories. The optional vendor checkout
is copied and remains unchanged. Native compiler capability probes can run in
that copy; no Darwin compilation, driver installation, or loading is performed.
"""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nvrm615_manifest_build', REPO / 'build/nvrm615/build.py')
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)
VENDOR = None

# Exact six-line Apple linker version banner from the real RM build logs.
APPLE_LD_BANNER = (
    '@(#)PROGRAM:ld PROJECT:ld-1267\n'
    'BUILD 16:37:34 Jun  8 2026\n'
    'configured to support archs: armv6 armv7 armv7s arm64 arm64e arm64_32 i386 x86_64 x86_64h armv6m armv7k armv7m armv7em armv8m.main armv8.1m.main\n'
    'will use ld-classic for: armv6 armv7 armv7s i386 armv6m armv7k armv7m armv7em\n'
    'LTO support using: LLVM version 21.0.0 (static support for 30, runtime is 30)\n'
    'TAPI support using: Apple TAPI version 21.0.0 (tapi-2100.0.2.6)\n'
)


def object_fixture(count):
    outputs = ['_out/Darwin_x86_64/g_fixture_%04d.o' % index for index in range(count)]
    lines = ['/usr/bin/clang -O2 -c src/fixture_%04d.c -o %s' % (index, output)
             for index, output in enumerate(outputs)]
    return outputs, BUILD.compile_commands('\n'.join(lines))


def manifest_text(objects):
    return '\n'.join(objects) + '\n'


def tool_fixture():
    return {
        'clang': '/usr/bin/clang', 'clang++': '/usr/bin/clang++',
        'ld': '/usr/bin/ld', 'ar': '/usr/bin/ar', 'libtool': '/usr/bin/libtool',
        'make': '/usr/bin/make', 'codesign': '/usr/bin/codesign',
        'python3': '/usr/local/opt/python@3.15/bin/python3.15',
        'nm': '/usr/bin/nm', 'otool': '/usr/bin/otool', 'lipo': '/usr/bin/lipo',
    }


class ToolPathRoleTests(unittest.TestCase):
    def test_observed_homebrew_python_path_is_allowed(self):
        BUILD.validate_make_paths('/Applications/Xcode.app/Contents/Developer/SDKs/MacOSX26.5.sdk',
                                  tool_fixture())

    def test_direct_argument_tools_are_not_subject_to_make_path_restrictions(self):
        tools = tool_fixture()
        for name in ('python3', 'make', 'codesign', 'nm', 'otool', 'lipo'):
            tools[name] = '/tmp/tools@version/' + name
        BUILD.validate_make_paths('/tmp/MacOSX26.5.sdk', tools)

    def test_sdk_and_make_expanded_tools_keep_strict_metacharacter_rejection(self):
        for character in (' ', '$', '#', '`', ';', '&', '|', '<', '>', '(', ')', '\n', "'", '"', '\\'):
            with self.subTest(field='SDK', character=character), self.assertRaises(RuntimeError):
                BUILD.validate_make_paths('/tmp/MacOSX' + character + '.sdk', tool_fixture())
            for name in ('clang', 'clang++', 'ld', 'ar', 'libtool'):
                tools = tool_fixture()
                tools[name] = '/tmp/unsafe' + character + 'tool'
                with self.subTest(field=name, character=character), self.assertRaises(RuntimeError):
                    BUILD.validate_make_paths('/tmp/MacOSX.sdk', tools)


class ManifestValidationTests(unittest.TestCase):
    def test_correct_rm_and_nvkms_manifests_have_one_entry_per_compile_output(self):
        for component, count in (('nvidia', 1100), ('nvidia-modeset', 225)):
            objects, rows = object_fixture(count)
            with self.subTest(component=component):
                self.assertEqual(BUILD.validate_object_manifest(manifest_text(objects), rows, component),
                                 objects)

    def test_missing_object_is_reported_with_an_example(self):
        objects, rows = object_fixture(1100)
        with self.assertRaises(RuntimeError) as caught:
            BUILD.validate_object_manifest(manifest_text(objects[:-1]), rows, 'nvidia')
        message = str(caught.exception)
        self.assertIn('missing', message.lower())
        self.assertIn(objects[-1], message)
        self.assertIn('nvidia', message)

    def test_extra_object_is_reported_with_an_example(self):
        objects, rows = object_fixture(1100)
        unexpected = '_out/Darwin_x86_64/not_in_compiler_commands.o'
        with self.assertRaises(RuntimeError) as caught:
            BUILD.validate_object_manifest(manifest_text(objects + [unexpected]), rows, 'nvidia')
        message = str(caught.exception)
        self.assertIn('unexpected', message.lower())
        self.assertIn(unexpected, message)

    def test_duplicate_object_is_reported(self):
        objects, rows = object_fixture(1100)
        with self.assertRaises(RuntimeError) as caught:
            BUILD.validate_object_manifest(manifest_text(objects + [objects[0]]), rows, 'nvidia')
        self.assertIn('duplicate', str(caught.exception).lower())
        self.assertIn(objects[0], str(caught.exception))

    def test_compensating_missing_and_duplicate_entries_still_fail(self):
        objects, rows = object_fixture(225)
        bad = objects[:-1] + [objects[0]]
        self.assertEqual(len(bad), len(objects))
        with self.assertRaises(RuntimeError) as caught:
            BUILD.validate_object_manifest(manifest_text(bad), rows, 'nvidia-modeset')
        message = str(caught.exception).lower()
        self.assertIn('missing', message)
        self.assertIn('duplicate', message)

    def test_unknown_stdout_is_rejected_instead_of_filtered_out(self):
        objects, rows = object_fixture(225)
        foreign = 'unrecognized stdout diagnostic from make'
        with self.assertRaises(RuntimeError) as caught:
            BUILD.validate_object_manifest(foreign + '\n' + manifest_text(objects), rows, 'nvidia-modeset')
        self.assertIn(foreign, str(caught.exception))

    def test_even_the_known_linker_banner_is_rejected_if_it_appears_on_stdout(self):
        objects, rows = object_fixture(1100)
        with self.assertRaises(RuntimeError) as caught:
            BUILD.validate_object_manifest(APPLE_LD_BANNER + manifest_text(objects), rows, 'nvidia')
        self.assertIn('@(#)PROGRAM:ld', str(caught.exception))

    def test_manifest_failure_writes_details_and_bounds_terminal_examples(self):
        objects, rows = object_fixture(1100)
        with tempfile.TemporaryDirectory(prefix='nvrm615-manifest-report-') as temporary:
            report = Path(temporary) / 'manifest.json'
            with self.assertRaises(RuntimeError) as caught:
                BUILD.validate_object_manifest(manifest_text(objects[100:]), rows, 'nvidia', report)
            message = str(caught.exception)
            self.assertLess(len(message), 5000)
            self.assertIn('100', message)
            self.assertIn(objects[0], message)
            self.assertNotIn(objects[99], message)
            details = json.loads(report.read_text())
            serialized = json.dumps(details)
            self.assertIn(objects[0], serialized)
            self.assertIn('nvidia', serialized)


class RecorderChannelTests(unittest.TestCase):
    def test_apple_linker_stderr_does_not_become_a_manifest_entry(self):
        objects, rows = object_fixture(1100)
        with tempfile.TemporaryDirectory(prefix='nvrm615-recorder-') as temporary:
            root = Path(temporary)
            (root / 'logs').mkdir()
            recorder = BUILD.Recorder(root, os.environ.copy())
            code = 'import sys; sys.stdout.write(%r); sys.stderr.write(%r)' % (
                manifest_text(objects), APPLE_LD_BANNER)
            with redirect_stdout(io.StringIO()):
                stdout = recorder.run([sys.executable, '-c', code], root, 'nvidia-objects')
            self.assertEqual(stdout, manifest_text(objects))
            self.assertEqual((root / 'logs/nvidia-objects.log').read_text(), stdout)
            self.assertEqual((root / 'logs/nvidia-objects.stderr.log').read_text(), APPLE_LD_BANNER)
            self.assertEqual(BUILD.validate_object_manifest(stdout, rows, 'nvidia'), objects)

    def test_direct_python_with_at_in_its_path_executes_without_a_shell(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-python-path-') as temporary:
            root = Path(temporary)
            interpreter = root / 'python@3.15/bin/python3.15'
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            (root / 'logs').mkdir()
            recorder = BUILD.Recorder(root, os.environ.copy())
            with redirect_stdout(io.StringIO()):
                output = recorder.run([interpreter, '-c', 'print("direct argv interpreter works")'],
                                      root, 'python-direct')
            self.assertEqual(output, 'direct argv interpreter works\n')
            recorded = json.loads((root / 'commands.jsonl').read_text().splitlines()[0])
            self.assertEqual(recorded['argv'][0], str(interpreter))

    def test_nonzero_tool_still_fails_and_preserves_both_logs(self):
        with tempfile.TemporaryDirectory(prefix='nvrm615-recorder-failure-') as temporary:
            root = Path(temporary)
            (root / 'logs').mkdir()
            recorder = BUILD.Recorder(root, os.environ.copy())
            code = 'import sys; print("partial stdout"); sys.stderr.write("fatal stderr\\n"); sys.exit(7)'
            with redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError) as caught:
                recorder.run([sys.executable, '-c', code], root, 'failed-tool')
            self.assertEqual((root / 'logs/failed-tool.log').read_text(), 'partial stdout\n')
            self.assertEqual((root / 'logs/failed-tool.stderr.log').read_text(), 'fatal stderr\n')
            self.assertIn('failed-tool.log', str(caught.exception))
            self.assertIn('failed-tool.stderr.log', str(caught.exception))


@unittest.skipUnless(VENDOR, 'Use --vendor to exercise the real NVIDIA Makefiles')
class RealMakeChannelTests(unittest.TestCase):
    def test_both_real_make_manifests_keep_apple_ld_banner_on_stderr(self):
        make = shutil.which('make')
        compiler = shutil.which('cc')
        cxx = shutil.which('c++')
        if not make or not compiler or not cxx:
            self.skipTest('Native make and compiler tools are required for capability probes')
        with tempfile.TemporaryDirectory(prefix='nvrm615-real-make-channels-') as temporary:
            root = Path(temporary)
            vendor_copy = root / 'ogkm'
            shutil.copytree(VENDOR, vendor_copy, ignore=shutil.ignore_patterns('.git', '_out'))
            fake_ld = root / 'apple-ld'
            fake_ld.write_text('#!%s\nimport sys\nif sys.argv[1:] == ["-v"]:\n'
                               '    sys.stderr.write(%r)\n    sys.exit(0)\nsys.exit(1)\n'
                               % (sys.executable, APPLE_LD_BANNER))
            fake_ld.chmod(0o700)
            (root / 'logs').mkdir()
            recorder = BUILD.Recorder(root, os.environ.copy())
            for component, count in (('nvidia', 1100), ('nvidia-modeset', 225)):
                args = [make, '-f', 'Makefile', '-f', str(REPO / 'build/nvrm615/darwin.mk'),
                        'TARGET_OS=Darwin', 'TARGET_ARCH=x86_64', 'NV_VERBOSE=1',
                        'CC=' + compiler, 'CXX=' + cxx, 'LD=' + str(fake_ld),
                        'NV_BUILD_USER=reference', 'NV_BUILD_HOST=isolated']
                if component == 'nvidia-modeset':
                    args += ['SHADER_OBJS=']
                cwd = vendor_copy / 'src' / component
                with self.subTest(component=component), redirect_stdout(io.StringIO()):
                    stdout = recorder.run(args + ['reference-print-objs'], cwd, component + '-objects')
                    commands = recorder.run(args + ['-n', '-B', 'reference-objs'], cwd, component + '-commands')
                    rows = BUILD.compile_commands(commands)
                    objects = BUILD.validate_object_manifest(stdout, rows, component)
                    self.assertEqual(len(objects), count)
                    self.assertEqual((root / 'logs' / (component + '-objects.stderr.log')).read_text(), APPLE_LD_BANNER)
                    self.assertEqual(len(stdout.splitlines()), count)


def main():
    global VENDOR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', type=Path)
    args, rest = parser.parse_known_args()
    if args.vendor:
        VENDOR = args.vendor.expanduser().resolve()
        RealMakeChannelTests.__unittest_skip__ = False
    unittest.main(argv=[sys.argv[0], *rest])


if __name__ == '__main__':
    main()
