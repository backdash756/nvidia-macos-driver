#!/usr/bin/env python3
"""Replay the opt-in Y420 diagnostic against controlled NVKMS responses.

Usage: python3 tools/test_yuv420_diagnostic.py --vendor /path/to/open-gpu-kernel-modules
   or: python3 tools/test_yuv420_diagnostic.py --prepared-build /path/to/new/diagnostic

The supplied NVIDIA 615.78.08 tree is read-only. The original and follow-up
patches are applied and reversed in a temporary directory. --historical checks
only the original patch and its original twelve scenarios. The C harness compiles the actual patched
KAPI functions and the actual macOS opt-in hook; it models ioctl responses,
allocation failures and boot arguments, rather than a display engine. This
checks diagnostic behavior, not real EDID parsing, link training or a Mac boot.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'kexts/NVRM/patches/615-yuv420-diagnostic.patch'
FOLLOWUP = ROOT / 'build/nvrm615diag/patches/615-yuv420-trigger-overview.patch'


def cut(source, start, end):
    a = source.index(start)
    return source[a:source.index(end, a)]


PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <pthread.h>
typedef uint8_t NvU8;
typedef uint16_t NvU16;
typedef uint32_t NvU32;
typedef uint64_t NvU64;
typedef uint32_t UInt32;
typedef int32_t SInt32;
typedef unsigned char NvBool;
typedef NvU32 NvKmsKapiDisplay;
#define NV_ALIGN_BYTES(n)
#define ct_assert(condition) _Static_assert(condition, #condition)
#define NV_TRUE 1
#define NV_FALSE 0
#define NVKMS_LOG_LEVEL_INFO 1
#define NVKMS_IOCTL_VALIDATE_MODE_INDEX 19
#define NVKMS_MODE_VALIDATION_NO_RRX1K_CHECK 0x800
#define NVKMS_KAPI_MODE_NAME_LEN 32
#define NVKMS_MODE_VALIDATION_MAX_INFO_STRING_LENGTH 2048
#define FOLLOWUP_ACTIVE @FOLLOWUP_ACTIVE@
'''

DOUBLES = r'''
struct NvKmsMode { NvModeTimings timings; char name[NVKMS_KAPI_MODE_NAME_LEN]; };
struct NvKmsKapiDevice { NvU32 hKmsDevice, hKmsDisp; void *pKmsOpen; };
/* The request fields not read by extracted code are canaries checked below. */
struct NvKmsModeValidationParams {
    NvBool verboseModeValidation, moreVerboseModeValidation, preferYUV420;
    NvU32 overrides, stereoMode, dscMode;
};
struct NvKmsValidateModeIndexParams {
    struct {
        NvU32 deviceHandle, dispHandle, dpyId;
        struct NvKmsModeValidationParams modeValidation;
        NvU32 modeIndex;
        NvU64 pInfoString;
        NvU32 infoStringSize;
        unsigned char dpyOutputColor[16];
    } request;
    struct {
        NvBool end, valid, preferredMode;
        struct NvKmsMode mode;
        NvU32 source, infoStringLenWritten;
    } reply;
};
static const char *scenario;
static NvBool opt_in;
static uint32_t target_mask = 0x400;
static int normal_calls, diag_calls[2], alloc_calls, live_allocations, fail_allocation;
static int cas_calls, injected_cas_failures;
static char logs[131072];
static size_t log_length;
static NvU32 next_index[2];
static int last_pass = -1;
static NvBool normal_error, normal_end, normal_other, normal_30;

static bool PE_parse_boot_argn(const char *name, void *value, int length) {
    assert(strcmp(name, "nvyuv420diag") == 0 && length == sizeof(target_mask));
    memcpy(value, &target_mask, sizeof(target_mask));
    return opt_in;
}
static SInt32 OSAddAtomic(SInt32 amount, volatile SInt32 *address) {
    return __atomic_fetch_add(address, amount, __ATOMIC_SEQ_CST) + amount;
}
static bool OSCompareAndSwap(UInt32 old, UInt32 next, volatile UInt32 *address) {
    __atomic_fetch_add(&cas_calls, 1, __ATOMIC_SEQ_CST);
    if (injected_cas_failures) { injected_cas_failures--; return false; }
    return __atomic_compare_exchange_n(address, &old, next, false,
                                       __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
static void *nvKmsKapiCalloc(size_t count, size_t size) {
    alloc_calls++;
    if (alloc_calls == fail_allocation) return NULL;
    void *p = calloc(count, size);
    assert(p); live_allocations++; return p;
}
static void nvKmsKapiFree(void *p) { if (p) { live_allocations--; free(p); } }
#define nvkms_memset memset
#define nvkms_memcpy memcpy
#define nvkms_snprintf snprintf
#define nvNvU32ToDpyId(x) (x)
#define nvKmsPointerToNvU64(x) ((NvU64)(uintptr_t)(x))
static void nvkms_log(int level, const char *prefix, const char *message) {
    assert(level == NVKMS_LOG_LEVEL_INFO && !prefix);
    int n = snprintf(logs + log_length, sizeof(logs) - log_length, "%s\n", message);
    assert(n >= 0 && (size_t)n < sizeof(logs) - log_length);
    log_length += (size_t)n;
}
static void nvKmsKapiLogDeviceDebug(struct NvKmsKapiDevice *device,
                                    const char *format, ...) { (void)device; (void)format; }
static struct NvKmsMode mode420(enum NvYuv420Mode yuv) {
    struct NvKmsMode m = {0};
    m.timings = (NvModeTimings) {
        .pixelClockHz=594000000, .RRx1k=60000,
        .hVisible=3840, .hSyncStart=4016, .hSyncEnd=4104, .hTotal=4400,
        .vVisible=2160, .vSyncStart=2168, .vSyncEnd=2178, .vTotal=2250,
        .hSyncPos=1, .vSyncPos=1, .sizeMM={600,340}, .yuv420Mode=yuv,
    };
    strcpy(m.name, "3840x2160"); return m;
}
static struct NvKmsMode mode30(void) {
    struct NvKmsMode m = mode420(NV_YUV420_MODE_NONE);
    m.timings.pixelClockHz = 297000000;
    m.timings.RRx1k = 30000;
    return m;
}
static struct NvKmsMode normal_mode(void) {
    struct NvKmsMode m = normal_30 ? mode30() : mode420(NV_YUV420_MODE_NONE);
    if (normal_other) {
        m.timings.pixelClockHz = 74250000; m.timings.RRx1k = 30000;
        m.timings.hVisible = 1920; m.timings.vVisible = 1080;
        m.timings.hSyncStart = 2008; m.timings.hSyncEnd = 2052; m.timings.hTotal = 2200;
        m.timings.vSyncStart = 1084; m.timings.vSyncEnd = 1089; m.timings.vTotal = 1125;
    }
    return m;
}
static NvBool nvkms_ioctl_from_kapi(void *open, NvU32 cmd, void *params, size_t bytes) {
    struct NvKmsValidateModeIndexParams *p = params;
    assert(open == (void *)(uintptr_t)0x123 && cmd == NVKMS_IOCTL_VALIDATE_MODE_INDEX);
    assert(bytes == sizeof(*p));
    assert(p->request.deviceHandle == 7 && p->request.dispHandle == 9 && p->request.dpyId == 0x400);
    assert(p->request.modeValidation.overrides == NVKMS_MODE_VALIDATION_NO_RRX1K_CHECK);
    assert(!p->request.modeValidation.verboseModeValidation);
    assert(!p->request.modeValidation.moreVerboseModeValidation);
    assert(!p->request.modeValidation.stereoMode && !p->request.modeValidation.dscMode);
    const unsigned char zero[16] = {0};
    assert(memcmp(p->request.dpyOutputColor, zero, sizeof(zero)) == 0);
    if (!p->request.pInfoString) {
        normal_calls++;
        assert(!p->request.modeValidation.preferYUV420 && !p->request.infoStringSize);
        if (normal_error) return NV_FALSE;
        p->reply.end = normal_end;
        p->reply.mode = normal_mode();
        p->reply.valid = NV_FALSE;
        p->reply.preferredMode = NV_TRUE;
        return NV_TRUE;
    }
    unsigned pass = p->request.modeValidation.preferYUV420 ? 1 : 0;
    assert(pass >= (unsigned)(last_pass < 0 ? 0 : last_pass));
    last_pass = (int)pass;
    assert(p->request.modeIndex == next_index[pass]++);
    diag_calls[pass]++;
    assert(p->request.modeIndex < 512);
    const unsigned char fresh_reply[sizeof(p->reply)] = {0};
    assert(memcmp(&p->reply, fresh_reply, sizeof(fresh_reply)) == 0);
    assert(p->request.infoStringSize == NVKMS_MODE_VALIDATION_MAX_INFO_STRING_LENGTH);
    char *info = (char *)(uintptr_t)p->request.pInfoString;
    for (unsigned i = 0; i < p->request.infoStringSize; i++) assert(info[i] == 0);
    if ((strcmp(scenario, "ioerror") == 0 && pass == 0 && p->request.modeIndex == 1) ||
        strcmp(scenario, "ioerrorfirst") == 0)
        return NV_FALSE;
    if (strcmp(scenario, "diagempty") == 0) {
        p->reply.end = NV_TRUE; return NV_TRUE;
    }
    if (strcmp(scenario, "only30") == 0 || strcmp(scenario, "bound30") == 0) {
        if (strcmp(scenario, "only30") == 0 && p->request.modeIndex == 2) {
            p->reply.end = NV_TRUE; return NV_TRUE;
        }
        p->reply.mode = mode30(); p->reply.source = 1; p->reply.valid = NV_TRUE;
        return NV_TRUE;
    }
    if (strcmp(scenario, "bound") == 0) {
        p->reply.mode.timings.hVisible = 1920; return NV_TRUE;
    }
    unsigned match_index = pass ? 3 : 1;
    p->reply.end = p->request.modeIndex > match_index;
    if (p->reply.end) return NV_TRUE;
    if (strcmp(scenario, "mixed") == 0 && p->request.modeIndex == 0) {
        p->reply.mode = mode30(); p->reply.source = 1; p->reply.valid = NV_TRUE;
        return NV_TRUE;
    }
    p->reply.mode = mode420(pass ?
        (strcmp(scenario, "sw") == 0 ? NV_YUV420_MODE_SW : NV_YUV420_MODE_HW)
        : NV_YUV420_MODE_NONE);
    if (p->request.modeIndex != match_index) {
        p->reply.mode.timings.hVisible = 1920; return NV_TRUE;
    }
    p->reply.source = 1;
    p->reply.valid = pass;
    if (strcmp(scenario, "longinfo") == 0) {
        memset(info, 'x', p->request.infoStringSize);
        p->reply.infoStringLenWritten = p->request.infoStringSize;
    } else {
        strcpy(info, pass ? "Y420 valid\nsecond line\n" : "Mode is rejected: TMDS limit.\n");
        p->reply.infoStringLenWritten = strlen(info) + 1;
    }
    return NV_TRUE;
}
'''

CHECKS = r'''
static void expect(const char *message) { assert(strstr(logs, message)); }
static unsigned occurrences(const char *message) {
    unsigned count = 0;
    for (const char *p = logs; (p = strstr(p, message)) != NULL; p += strlen(message)) count++;
    return count;
}
static void *claim_thread(void *result) {
    *(NvBool *)result = nvkms_yuv420_diag_claim(8); return NULL;
}
static void check_claim(void) {
    assert(!nvkms_yuv420_diag_claim(0x400));
    opt_in=1; target_mask=0x400 | 0x4000 | 8 | 0x80000000u;
    assert(!nvkms_yuv420_diag_claim(0));
    assert(!nvkms_yuv420_diag_claim(3));
    assert(!nvkms_yuv420_diag_claim(0x400 | 0x4000));
    assert(!nvkms_yuv420_diag_claim(2));
    injected_cas_failures=1;
    assert(nvkms_yuv420_diag_claim(0x400));
    assert(cas_calls == 2);
    assert(!nvkms_yuv420_diag_claim(0x400));
    assert(nvkms_yuv420_diag_claim(0x4000));
    assert(nvkms_yuv420_diag_claim(0x80000000u));
    pthread_t threads[16]; NvBool results[16] = {0}; unsigned claims=0;
    for (unsigned i=0;i<16;i++) assert(!pthread_create(&threads[i],NULL,claim_thread,&results[i]));
    for (unsigned i=0;i<16;i++) { assert(!pthread_join(threads[i],NULL)); claims+=results[i]; }
    assert(claims == 1);
}
static void check_conversion(void) {
    for (unsigned mode=0;mode<3;mode++) {
        struct NvKmsMode original=mode420((enum NvYuv420Mode)mode), back;
        struct NvKmsKapiDisplayMode wire;
        NvKmsModeToKapi(&original,&wire);
        NvKmsKapiDisplayModeToKapi(&wire,&back);
        assert(wire.timings.pixelClockHz==594000000 && back.timings.pixelClockHz==594000000);
        assert(back.timings.hVisible==3840 && back.timings.hTotal==4400);
        assert(back.timings.vVisible==2160 && back.timings.vTotal==2250);
        assert(back.timings.RRx1k==60000 && back.timings.sizeMM.w==600);
        assert(!strcmp(back.name,original.name));
        assert(back.timings.yuv420Mode==NV_YUV420_MODE_NONE);
    }
}
int main(int argc, char **argv) {
    assert(argc==2); scenario=argv[1];
    if (!strcmp(scenario,"claim")) { check_claim(); puts("PASS claim: opt-in, targets, one-bit IDs, CAS retry, concurrent once"); return 0; }
    check_conversion();
    opt_in=strcmp(scenario,"off")!=0 && strcmp(scenario,"off30")!=0;
    normal_error=!strcmp(scenario,"normalerror") || !strcmp(scenario,"normalretry");
    normal_end=!strcmp(scenario,"end");
    normal_other=!strcmp(scenario,"other");
    normal_30=!strcmp(scenario,"only30") || !strcmp(scenario,"mixed") ||
              !strcmp(scenario,"diagempty") || !strcmp(scenario,"bound30") ||
              !strcmp(scenario,"off30");
    if (!strcmp(scenario,"unselected")) target_mask=0x4000;
    if (!strcmp(scenario,"oom1")) fail_allocation=1;
    if (!strcmp(scenario,"oom2")) fail_allocation=2;
    struct NvKmsKapiDevice device={7,9,(void *)(uintptr_t)0x123};
    struct NvKmsKapiDisplayMode output, expected;
    NvBool valid=99, preferred=99;
    memset(&output,0xa5,sizeof(output));
    unsigned char untouched[sizeof(output)];
    memcpy(untouched, &output, sizeof(output));
    assert(GetDisplayMode(NULL,0x400,3,&output,&valid,&preferred)==-1);
    assert(!normal_calls && !alloc_calls);
    struct NvKmsMode m=normal_mode();
    NvKmsModeToKapi(&m,&expected);
    int rc=GetDisplayMode(&device,0x400,3,&output,&valid,&preferred);
    if (!strcmp(scenario,"normalretry")) {
        assert(rc==-1 && valid==99 && preferred==99 && !alloc_calls);
        assert(!memcmp(&output, untouched, sizeof(output)));
        normal_error=NV_FALSE;
        rc=GetDisplayMode(&device,0x400,3,&output,&valid,&preferred);
    }
    if (normal_error) {
        assert(rc==-1 && valid==99 && preferred==99 && !alloc_calls);
        assert(!memcmp(&output, untouched, sizeof(output)));
        assert(!diag_calls[0] && !diag_calls[1] && !log_length);
        assert(GetDisplayMode(&device,0x400,3,NULL,NULL,NULL)==-1);
        assert(!alloc_calls && !diag_calls[0] && !diag_calls[1]);
    } else {
        assert(rc==(normal_end?0:1));
        assert(valid==NV_FALSE && preferred==NV_TRUE);
        assert(!memcmp(&output,&expected,sizeof(output)));
        if (!opt_in || normal_end || !strcmp(scenario,"unselected") ||
            (!FOLLOWUP_ACTIVE && normal_other)) {
            assert(!alloc_calls && !diag_calls[0] && !diag_calls[1] && !log_length);
        } else if (fail_allocation) {
            assert(alloc_calls==2 && !diag_calls[0] && !diag_calls[1]);
            expect("ABORT allocation failed");
        } else if (!strcmp(scenario,"bound") || !strcmp(scenario,"bound30")) {
            assert(diag_calls[0]==512 && diag_calls[1]==512);
            expect("pass=A INCOMPLETE queried=512"); expect("pass=B INCOMPLETE queried=512");
            assert(!strstr(logs," COMPLETE"));
            if (!strcmp(scenario,"bound30")) {
                expect("pass=A INCOMPLETE queried=512 matches=0 matches30=512 logged30=8");
                expect("pass=B INCOMPLETE queried=512 matches=0 matches30=512 logged30=8");
                assert(occurrences("overview=4K30") == 16);
                assert(occurrences("pass=A index=") == 8 && occurrences("pass=B index=") == 8);
                assert(!strstr(logs,"index=8 overview=4K30"));
            }
        } else if (!strcmp(scenario,"only30")) {
            assert(diag_calls[0]==3 && diag_calls[1]==3);
            expect("pass=A COMPLETE queried=3 matches=0 matches30=2 logged30=2");
            expect("pass=B COMPLETE queried=3 matches=0 matches30=2 logged30=2");
            expect("overview=4K30 source=1 valid=1 pclkHz=297000000 rrMilliHz=30000 yuv420=NONE");
            assert(occurrences("overview=4K30") == 4);
            assert(!strstr(logs,"pclkHz=594000000") && !strstr(logs,"roundtrip="));
        } else if (!strcmp(scenario,"diagempty")) {
            assert(diag_calls[0]==1 && diag_calls[1]==1);
            expect("pass=A COMPLETE queried=1 matches=0 matches30=0 logged30=0");
            expect("pass=B COMPLETE queried=1 matches=0 matches30=0 logged30=0");
            assert(!strstr(logs,"overview=4K30"));
        } else if (!strcmp(scenario,"ioerrorfirst")) {
            assert(diag_calls[0]==1 && diag_calls[1]==1);
            expect("pass=A ABORT ioctl index=0"); expect("pass=B ABORT ioctl index=0");
            expect("pass=A INCOMPLETE queried=1"); expect("pass=B INCOMPLETE queried=1");
            assert(!strstr(logs," COMPLETE"));
        } else if (!strcmp(scenario,"ioerror")) {
            assert(diag_calls[0]==2 && diag_calls[1]==5);
            expect("pass=A ABORT ioctl index=1"); expect("pass=A INCOMPLETE queried=2");
            expect("pass=B COMPLETE queried=5");
        } else {
            assert(diag_calls[0]==3 && diag_calls[1]==5);
            expect("pass=A COMPLETE queried=3 matches=1"); expect("pass=B COMPLETE queried=5 matches=1");
            expect("pass=A index=1 source=1 valid=0 pclkHz=594000000");
            expect("pass=B index=3 source=1 valid=1 pclkHz=594000000");
            expect("yuv420=NONE roundtrip=NONE lost=0");
            expect(!strcmp(scenario,"sw") ? "yuv420=SW roundtrip=NONE lost=1" : "yuv420=HW roundtrip=NONE lost=1");
            if (!strcmp(scenario,"longinfo")) {
                expect("INFO_MAY_BE_TRUNCATED"); expect("info: xxxxxxxxxx");
            } else {
                expect("Mode is rejected: TMDS limit."); expect("info: second line");
                assert(!strstr(logs,"ABORT"));
            }
            if (!strcmp(scenario,"mixed")) {
                expect("pass=A COMPLETE queried=3 matches=1 matches30=1 logged30=1");
                expect("pass=B COMPLETE queried=5 matches=1 matches30=1 logged30=1");
                assert(occurrences("overview=4K30") == 2);
            }
        }
        int count=diag_calls[0]+diag_calls[1], alloc=alloc_calls;
        assert(GetDisplayMode(&device,0x400,3,NULL,NULL,NULL)==(normal_end?0:1));
        assert(diag_calls[0]+diag_calls[1]==count && alloc_calls==alloc);
    }
    assert(live_allocations==0);
    printf("PASS %s: original reply preserved, diagnostic outcome and allocation accounting checked\n",scenario);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--vendor', type=Path,
                        help='Unmodified NVIDIA open-gpu-kernel-modules 615.78.08 root')
    inputs.add_argument('--prepared-build', type=Path,
                        help='Prepared paired experiment; test its actual staged sources read-only')
    parser.add_argument('--historical', action='store_true',
                        help='Test the preserved original patch and its original twelve scenarios only')
    args = parser.parse_args()
    patches = (PATCH,) if args.historical else (PATCH, FOLLOWUP)
    hook_path = ROOT / 'kexts/NVRM/os-nvkms-xnu.cpp'
    if args.prepared_build:
        prepared = args.prepared_build.resolve(strict=True)
        manifest = json.loads((prepared / 'inputs.json').read_text())
        assert (manifest['format'], manifest['build_kind'], manifest['diagnostic_enabled']) in (
            (2, 'yuv420-ab-experiment', True),
            (3, 'yuv420-ab-metadata-experiment', True)), 'Expected a paired experimental source manifest'
        if manifest['format'] == 3:
            assert (manifest.get('metadata_enabled') is True and manifest.get('experimental') is True
                    and manifest.get('metadata_version') == '2.5.4'), 'Expected isolated experimental metadata'
        vendor = prepared / 'ogkm'
        hook_path = prepared / 'nullmoth/kexts/NVRM/os-nvkms-xnu.cpp'
        for folder, key, paths in (
            ('ogkm', 'vendor_sha256', ('src/nvidia-modeset/kapi/src/nvkms-kapi.c',
                                     'src/nvidia-modeset/src/nvkms-modepool.c')),
            ('nullmoth', 'nullmoth_sha256', ('kexts/NVRM/os-nvkms-xnu.cpp',)),
        ):
            for relative in paths:
                actual = hashlib.sha256((prepared / folder / relative).read_bytes()).hexdigest()
                assert actual == manifest[key][relative], 'Changed prepared source: ' + relative
    else:
        vendor = args.vendor.resolve()
    modeset = vendor / 'src/nvidia-modeset'
    paths = ('kapi/src/nvkms-kapi.c', 'src/nvkms-modepool.c')
    originals = {p: (modeset / p).read_bytes() for p in paths}
    header = (modeset / 'kapi/interface/nvkms-kapi.h').read_text()
    timing_header = (vendor / 'src/common/unix/common/inc/nv_mode_timings.h').read_text()
    kapi_structs = cut(header, 'struct NvKmsKapiDisplayModeTimings {', '#define NVKMS_KAPI_LAYER_MAX')
    internal_structs = cut(timing_header, 'enum NvYuv420Mode {', 'static inline NvBool NvModeTimingsMatch')
    original_hook = hook_path.read_bytes()
    hook = cut(original_hook.decode(),
               'NvBool nvkms_yuv420_diag_claim(', 'static bool gKmsTraceRm')
    with tempfile.TemporaryDirectory(prefix='nullmoth-y420-test-') as tmp:
        work = Path(tmp)
        for p, content in originals.items():
            target = work / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if not args.prepared_build:
            for patch in patches:
                subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)], cwd=work, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        patched = (work / paths[0]).read_text()
        functions = cut(patched, 'static void NvKmsModeToKapi', 'static NvBool ValidateDisplayMode')
        assert 'Yuv420DiagEnumerate' in functions
        assert ('Y420_DIAG_4K30_LOG_LIMIT' in functions) is not args.historical, 'Wrong diagnostic revision for test mode'
        preamble = PREAMBLE.replace('@FOLLOWUP_ACTIVE@', '0' if args.historical else '1')
        harness = preamble + internal_structs + kapi_structs + DOUBLES + hook + functions + CHECKS
        source = work / 'diagnostic.c'
        source.write_text(harness)
        exe = work / 'diagnostic'
        compiler = os.environ.get('CC', 'cc')
        subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-pthread', str(source), '-o', str(exe)], check=True)
        # Only the VALIDATE_MODE_INDEX ioctl is provided: adding a mode-setting
        # call to the extracted diagnostic fails compilation or the ioctl guard.
        scenarios = ('off', 'on', 'sw', 'other', 'end', 'normalerror', 'ioerror',
                     'bound', 'oom1', 'oom2', 'longinfo', 'claim')
        if not args.historical:
            scenarios += ('only30', 'mixed', 'diagempty', 'bound30', 'unselected',
                          'off30', 'ioerrorfirst', 'normalretry')
        for scenario in scenarios:
            subprocess.run([str(exe), scenario], check=True)
        # Compile both complete patched translation units with the real vendor
        # headers. The extracted harness intentionally does not replace this
        # independent check of the actual NVKMS interfaces and format strings.
        vendor_flags = []
        for line in (modeset / 'Makefile').read_text().splitlines():
            if not line.startswith('CFLAGS += '):
                continue
            value = line[len('CFLAGS += '):].replace('$(SRC_COMMON)', '../common')
            if value.startswith('-I '):
                parts = shlex.split(value)
                assert len(parts) == 2, f'Unexpected vendor include: {line}'
                vendor_flags += ['-I', str((modeset / parts[1]).resolve())]
            elif value.startswith('-D'):
                vendor_flags += shlex.split(value)
        assert vendor_flags, 'No vendor includes or defines found'
        syntax_flags = ['-fsyntax-only', '-std=gnu11', '-Wall', '-Wextra',
                        '-Wno-unused-parameter', '-Wno-sign-compare',
                        '-Wno-missing-field-initializers',
                        '-Werror=implicit-function-declaration', '-Werror=format',
                        '-include', str(vendor / 'src/common/sdk/nvidia/inc/cpuopsys.h')]
        for path in paths:
            subprocess.run([compiler] + syntax_flags + vendor_flags + [str(work / path)],
                           cwd=modeset, check=True)
            print(f'PASS: full vendor syntax and format check: {path}', flush=True)
        for patch in reversed(patches):
            subprocess.run(['patch', '--batch', '--fuzz=0', '--reverse', '-p1', '-i', str(patch)],
                           cwd=work, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if args.prepared_build:
            # Restore only the temporary test copy, never the supplied build.
            for patch in patches:
                subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                               cwd=work, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for p, content in originals.items():
            assert (work / p).read_bytes() == content, f'Patch reverse changed {p}'
            assert (modeset / p).read_bytes() == content, f'Original vendor tree changed: {p}'
        assert hook_path.read_bytes() == original_hook, 'Supplied XNU hook changed'
    print('PASS: patch applies/reverses without fuzz; supplied NVIDIA files unchanged')


if __name__ == '__main__':
    main()
