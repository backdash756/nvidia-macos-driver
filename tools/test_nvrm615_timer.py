#!/usr/bin/env python3
"""Run the real pinned timer function in a small native Linux C harness.

    python3 tools/test_nvrm615_timer.py --vendor /path/to/open-gpu-615

The vendor checkout is read-only; two source files are copied to a temporary
tree and the timer patch is applied there. Only the extracted function is
compiled, with model RM types/macros and a model OS timer destructor. This is
neither a Darwin build nor evidence of NVIDIA structure/ABI compatibility.
Without --vendor, the integration checks are skipped; nothing is downloaded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
PATCHES = REPO / 'build/nvrm615/patches'
VENDOR = None
NVIDIA = '1a82fbfda8f321b056eb3568850f9316c5e465c5'
TIMER = 'src/nvidia/src/kernel/gpu/timer/timer_ostimer.c'
FSP = 'src/nvidia/src/kernel/gpu/fsp/kern_fsp.c'
PRISTINE_SHA = '9710b89d0725d433b993ba803983bde1262096e7f62b81d39d0bc8f816ff5bb3'
PATCHED_SHA = 'da908b3245f9be48f0787eb48fd4d70b721f6d48cd55e99bb99421bb370379f2'
FSP_SHA = '84ba2a49c08aa490aad40fe737b42d135e3bf11969e76ca257a8e5f425c51e54'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args, cwd):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def extract_function(source, replacement_name):
    """Retain the exact function body; replace only its identifier for testing."""
    identifier = 'tmrEventDestroyOSTimer_OSTIMER'
    start = source.index('NV_STATUS ' + identifier + '\n')
    opening = source.index('{', start)
    # Ignore braces in comments and literals when finding the closing brace.
    tokens = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', re.S)
    depth = 0
    for match in tokens.finditer(source, opening):
        if match.group() == '{':
            depth += 1
        elif match.group() == '}':
            depth -= 1
            if depth == 0:
                function = source[start:match.end()]
                return function.replace(identifier, replacement_name, 1)
    raise ValueError('The pinned timer function has no matching closing brace')


HARNESS = r'''
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

/* Field names model the body dependencies; no production layout is claimed. */
typedef int NV_STATUS;
enum { NV_OK = 0, NV_ERR_INVALID_REQUEST = 7, MODEL_OS_ERROR = 13 };
enum { TMR_FLAG_OS_TIMER_QUEUED = 4, OTHER_FLAG = 128 };
typedef struct { void *pOSTmrCBdata; unsigned flags; } TMR_EVENT;
typedef struct { TMR_EVENT super; } TMR_EVENT_PVT;
typedef struct { void *pOsGpuInfo; } OBJGPU;
typedef struct { OBJGPU *gpu; } OBJTMR;
#define ENG_GET_GPU(timer) ((timer)->gpu)
#define NV_PRINTF(...) ((void)0)

static int token, calls;
static NV_STATUS os_status;
static TMR_EVENT_PVT *active_event;

static NV_STATUS osDestroyNanoTimer(void *os_info, void *callback)
{
    (void)os_info;
    assert(active_event != NULL);
    assert(callback == &token);
    /* The pointer must still be supplied to the destructor before clearing. */
    assert(active_event->super.pOSTmrCBdata == &token);
    ++calls;
    return os_status;
}

/* The exact baseline and patched function bodies are inserted below. */
@BASELINE@
@PATCHED@

int main(int argc, char **argv)
{
    OBJGPU gpu = { NULL };
    OBJTMR timer = { &gpu };
    static TMR_EVENT_PVT event = { { &token, TMR_FLAG_OS_TIMER_QUEUED | OTHER_FLAG } };
    TMR_EVENT *public_event = (TMR_EVENT *)&event;
    active_event = &event;
    assert(argc == 2);

    if (strcmp(argv[1], "baseline") == 0)
    {
        assert(baseline_destroy(&timer, public_event) == NV_OK);
        assert(calls == 1);
        assert(event.super.pOSTmrCBdata == &token);
        assert(event.super.flags == OTHER_FLAG);
    }
    else if (strcmp(argv[1], "success") == 0)
    {
        assert(patched_destroy(&timer, public_event) == NV_OK);
        assert(calls == 1);
        assert(event.super.pOSTmrCBdata == NULL);
        assert(event.super.flags == OTHER_FLAG);
    }
    else if (strcmp(argv[1], "second-destroy") == 0)
    {
        assert(patched_destroy(&timer, public_event) == NV_OK);
        assert(patched_destroy(&timer, public_event) == NV_ERR_INVALID_REQUEST);
        assert(calls == 1);
        assert(event.super.pOSTmrCBdata == NULL);
        assert(event.super.flags == OTHER_FLAG);
    }
    else if (strcmp(argv[1], "null-event") == 0)
    {
        assert(patched_destroy(&timer, NULL) == NV_ERR_INVALID_REQUEST);
        assert(calls == 0);
        assert(event.super.pOSTmrCBdata == &token);
        assert(event.super.flags == (TMR_FLAG_OS_TIMER_QUEUED | OTHER_FLAG));
    }
    else if (strcmp(argv[1], "null-callback") == 0)
    {
        event.super.pOSTmrCBdata = NULL;
        assert(patched_destroy(&timer, public_event) == NV_ERR_INVALID_REQUEST);
        assert(calls == 0);
        assert(event.super.pOSTmrCBdata == NULL);
        assert(event.super.flags == (TMR_FLAG_OS_TIMER_QUEUED | OTHER_FLAG));
    }
    else if (strcmp(argv[1], "os-error") == 0)
    {
        /* Real 615 osDestroyNanoTimer returns NV_OK. This models the original
         * patch's unconditional placement, not an observed production error. */
        os_status = MODEL_OS_ERROR;
        assert(patched_destroy(&timer, public_event) == MODEL_OS_ERROR);
        assert(calls == 1);
        assert(event.super.pOSTmrCBdata == NULL);
        assert(event.super.flags == OTHER_FLAG);
    }
    else
    {
        return 2;
    }
    puts("model timer assertions passed; not a Darwin or ABI test");
    return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith('linux'), 'Native harness is explicitly Linux-only')
class TimerBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if VENDOR is None:
            raise unittest.SkipTest('Use --vendor for the real pinned-source timer checks')
        compiler = shutil.which('cc')
        if compiler is None:
            raise unittest.SkipTest('A native C compiler is required')
        cls.vendor_head = git('rev-parse', 'HEAD', cwd=VENDOR)
        cls.vendor_status = git('status', '--porcelain', '--untracked-files=normal', cwd=VENDOR)
        if cls.vendor_head != NVIDIA or cls.vendor_status:
            raise ValueError('Vendor must be the clean pinned NVIDIA 615.78.08 checkout')
        cls.vendor_files = {name: (VENDOR / name).read_bytes() for name in (TIMER, FSP)}
        if sha(cls.vendor_files[TIMER]) != PRISTINE_SHA or sha(cls.vendor_files[FSP]) != FSP_SHA:
            raise ValueError('Pinned timer/FSP source hash mismatch')
        cls.temporary = tempfile.TemporaryDirectory(prefix='nvrm615-timer-model-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.staged = cls.root / 'ogkm'
        for name, data in cls.vendor_files.items():
            target = cls.staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        proof = json.loads((PATCHES / 'patch-proof.json').read_text())
        cls.patch_entry = next(entry for entry in proof['patch_order']
                               if entry['normalized_path'] == '615-timer-callback-clear.patch')
        patch = PATCHES / cls.patch_entry['normalized_path']
        if sha(patch.read_bytes()) != cls.patch_entry['normalized_sha256']:
            raise ValueError('Timer patch hash mismatch')
        cls.apply_command = ['git', 'apply', '--directory=src/nvidia']
        subprocess.run(cls.apply_command + ['--check', str(patch)], cwd=cls.staged, check=True)
        subprocess.run(cls.apply_command + [str(patch)], cwd=cls.staged, check=True)
        patched_source = (cls.staged / TIMER).read_bytes()
        if sha(patched_source) != PATCHED_SHA:
            raise ValueError('Patched timer source hash mismatch')
        baseline = extract_function(cls.vendor_files[TIMER].decode(), 'baseline_destroy')
        patched = extract_function(patched_source.decode(), 'patched_destroy')
        source = HARNESS.replace('@BASELINE@', baseline).replace('@PATCHED@', patched)
        cls.executable = cls.root / 'timer-model'
        source_path = cls.root / 'timer-model.c'
        source_path.write_text(source)
        compiled = subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                                   str(source_path), '-o', str(cls.executable)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if compiled.returncode:
            raise RuntimeError('Native model compilation failed:\n' + compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        if git('rev-parse', 'HEAD', cwd=VENDOR) != cls.vendor_head:
            raise AssertionError('Vendor HEAD changed')
        if git('status', '--porcelain', '--untracked-files=normal', cwd=VENDOR) != cls.vendor_status:
            raise AssertionError('Vendor status changed')
        for name, original in cls.vendor_files.items():
            if (VENDOR / name).read_bytes() != original:
                raise AssertionError('Vendor source changed: ' + name)

    def run_case(self, name):
        result = subprocess.run([str(self.executable), name], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertIn('model timer assertions passed', result.stdout)

    def test_pristine_baseline_retains_the_stale_callback(self):
        self.run_case('baseline')

    def test_patch_clears_callback_after_call_and_only_queued_flag(self):
        self.run_case('success')

    def test_second_destroy_does_not_reach_the_os_destructor(self):
        self.run_case('second-destroy')

    def test_null_public_event_guard_is_preserved(self):
        self.run_case('null-event')

    def test_null_callback_guard_is_preserved(self):
        self.run_case('null-callback')

    def test_null_assignment_is_unconditional_like_the_original_patch(self):
        self.run_case('os-error')

    def test_exact_source_hashes_and_existing_fsp_guard_are_preserved(self):
        self.assertEqual(sha(self.vendor_files[TIMER]), PRISTINE_SHA)
        self.assertEqual(sha((self.staged / TIMER).read_bytes()), PATCHED_SHA)
        self.assertEqual(sha((self.staged / FSP).read_bytes()), FSP_SHA)
        self.assertEqual((self.staged / FSP).read_bytes(), self.vendor_files[FSP])
        self.assertEqual(self.patch_entry['target_before_sha256'], PRISTINE_SHA)
        self.assertEqual(self.patch_entry['target_after_sha256'], PATCHED_SHA)

    def test_second_patch_application_is_rejected(self):
        patch = PATCHES / self.patch_entry['normalized_path']
        result = subprocess.run(self.apply_command + ['--check', str(patch)], cwd=self.staged,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sha((self.staged / TIMER).read_bytes()), PATCHED_SHA)


def main():
    global VENDOR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', type=Path)
    args, remaining = parser.parse_known_args()
    if args.vendor:
        VENDOR = args.vendor.expanduser().resolve()
    unittest.main(argv=[sys.argv[0], *remaining])


if __name__ == '__main__':
    main()
