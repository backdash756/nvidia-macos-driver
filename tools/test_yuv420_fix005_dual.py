#!/usr/bin/env python3
"""Read-only fix-005 overlay and compiled gate truth-table test.

Verifies the exact preimage of the already built fix-004 source. Applies the
minimal dual-display overlay only inside a disposable temporary directory.
Compiles and tests the extracted *real patched function*, not an imitation.
Does not build/install a kext or touch EFI, NVRAM or kernel collections.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
PATCH = REPO / "build/nvrm615fix005/patches/xnu-yuv420-dual-optin.patch"
RELATIVE = Path("kexts/NVRM/os-nvkms-xnu.cpp")
DEFAULT_SOURCE = (Path.home() / "nvrm-builds/yuv420-fix-004-build-003" /
                  "nullmoth" / RELATIVE)
FIX004_SOURCE_SHA256 = "2b1c434507a91c8dffa60f101caf30a326d7711a1f5cc05c4404d5ddbf24ca7b"

OLD_FUNCTION = """// Separate opt-in for the production-fix experiment. Exactly one display
// handle per boot; disabled if the argument is absent or carries multiple bits.
// Stage 1 is NOT a complete modeset implementation and must not be deployed yet.
NvBool nvkms_yuv420_fix_enabled(NvU32 display)
{
    uint32_t target = 0;
    return PE_parse_boot_argn("nvyuv420fix", &target, sizeof target) &&
           target != 0 && !(target & (target - 1)) && target == display;
}
"""

NEW_FUNCTION = """// Opt-in for the two hardware-tested displays of one GPU.
// Only 0x400 and 0x4000 are accepted; display handles are boot-local.
// Preserve the strict opt-in and leave every other display untouched.
NvBool nvkms_yuv420_fix_enabled(NvU32 display)
{
    const uint32_t allowed = 0x400U | 0x4000U;
    uint32_t target = 0;
    return PE_parse_boot_argn("nvyuv420fix", &target, sizeof target) &&
           target != 0 && (target & ~allowed) == 0 &&
           (display == 0x400U || display == 0x4000U) &&
           (target & display) != 0;
}
"""

def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)

def run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="Read-only fix-004 build-003 XNU source")
    args = parser.parse_args()
    source = args.source.expanduser().resolve(strict=True)
    original = source.read_bytes()
    require(hashlib.sha256(original).hexdigest() == FIX004_SOURCE_SHA256,
            "fix-004 source does not match pinned SHA-256; abort")
    text = original.decode("utf-8")
    require(text.count(OLD_FUNCTION) == 1, "Expected fix-004 gate is absent or duplicated")
    require(PATCH.is_file(), "fix-005 overlay patch missing")
    expected = text.replace(OLD_FUNCTION, NEW_FUNCTION)

    with tempfile.TemporaryDirectory(prefix="fix005-gate-test-") as temp:
        root = Path(temp)
        dest = root / RELATIVE
        dest.parent.mkdir(parents=True)
        shutil.copy2(source, dest)
        run(["git", "apply", "--check", str(PATCH)], cwd=root)
        run(["git", "apply", str(PATCH)], cwd=root)
        result = dest.read_text()
        require(result == expected, "Patch changed more than the pinned gate")
        m = re.search(
            r"(?m)^NvBool nvkms_yuv420_fix_enabled\(NvU32 display\)\s*\{.*?^\}",
            result, flags=re.S | re.M)
        require(m is not None, "Patched function not found")

        test_c = root / "gate_test.c"
        executable = root / "gate_test"
        test_c.write_text(r'''#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stddef.h>
typedef uint32_t NvU32;
typedef int NvBool;
static uint32_t requested;
static int arg_present;
static int PE_parse_boot_argn(const char *name, void *out, size_t length) {
    if (!arg_present || strcmp(name, "nvyuv420fix") ||
        length != sizeof(requested)) return 0;
    memcpy(out, &requested, length);
    return 1;
}
''' + m.group(0) + r'''
int main(void) {
    const uint32_t requests[] = {
        0U, 0x400U, 0x4000U, 0x4400U,
        0x800U, 0x4800U, 0x4401U, 0xffffffffU
    };
    const uint32_t displays[] = {
        0U, 0x400U, 0x4000U, 0x4400U, 0x800U, 0x200U
    };
    unsigned cases = 0;
    for (unsigned a = 0; a < 2; ++a)
      for (unsigned i = 0; i < sizeof(requests)/sizeof(requests[0]); ++i)
        for (unsigned j = 0; j < sizeof(displays)/sizeof(displays[0]); ++j) {
            arg_present = (int)a;
            requested = requests[i];
            uint32_t display = displays[j];
            int expected =
                arg_present && (requested == 0x400U ||
                requested == 0x4000U || requested == 0x4400U) &&
                (display == 0x400U || display == 0x4000U) &&
                ((requested & display) != 0U);
            int got = nvkms_yuv420_fix_enabled(display) != 0;
            if (got != expected) {
                fprintf(stderr, "FAIL arg=%d request=0x%x display=0x%x got=%d want=%d\n",
                        arg_present, requested, display, got, expected);
                return 1;
            }
            ++cases;
        }
    printf("FIX-005 GATE PASS: %u C cases\n", cases);
    return 0;
}
''')
        clang = shutil.which("clang")
        require(clang is not None, "clang not found")
        run([clang, "-std=c11", "-Wall", "-Wextra", "-Werror",
             str(test_c), "-o", str(executable)])
        run([str(executable)])
        print("FIX-005 PATCH APPLIES CLEANLY TO PINNED FIX-004 SOURCE")
        print("FIX-005 PATCH CHANGES EXACTLY ONE GATE")
        print("PATCHED XNU SOURCE SHA-256:", hashlib.sha256(dest.read_bytes()).hexdigest())
    print("Original fix-004 source and installed driver were NOT modified.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print("FIX-005 STOP:", exc, file=sys.stderr)
        sys.exit(2)
