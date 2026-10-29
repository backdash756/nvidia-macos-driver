#!/usr/bin/env python3
"""Read-only strict git-apply checks for YUV420 fix stage-1 patches.

Requires existing yuv420-diag-003 build tree. This script NEVER applies a patch,
modifies a source tree or the live driver, or enables a boot argument.
"""
from pathlib import Path
import hashlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
STAGED = Path.home() / "nvrm-builds/yuv420-diag-003"
KAPI = STAGED / "ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c"
XNU = STAGED / "nullmoth/kexts/NVRM/os-nvkms-xnu.cpp"
PATCH_KAPI = ROOT / "build/nvrm615fix/patches/615-yuv420-fix-discovery.patch"
PATCH_XNU = ROOT / "build/nvrm615fix/patches/xnu-yuv420-fix-optin.patch"
EXPECTED_KAPI = "d25439b4c3ea42a2e739227b34c1ce12d94c4c6441680bbd7a0774c9c07e1481"

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    return h.hexdigest()

def check_patch(cwd, args):
    result = subprocess.run(["git", "apply", "--check", *args],
                            cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git apply --check failed")

try:
    for file in (KAPI, XNU, PATCH_KAPI, PATCH_XNU):
        if not file.is_file() or file.is_symlink():
            raise RuntimeError("missing or symlinked input: " + str(file))
    if sha(KAPI) != EXPECTED_KAPI:
        raise RuntimeError("diagnostic KAPI SHA mismatch; do not force/fuzz apply")
    before = {str(f): sha(f) for f in (KAPI, XNU)}
    check_patch(STAGED / "ogkm",
                ["--directory=src/nvidia-modeset", str(PATCH_KAPI)])
    print("OK: KAPI fix stage-1 patch applies without fuzz")
    check_patch(STAGED / "nullmoth", [str(PATCH_XNU)])
    print("OK: XNU single-display opt-in patch applies without fuzz")
    for f in (KAPI, XNU):
        if sha(f) != before[str(f)]:
            raise RuntimeError("input changed unexpectedly: " + str(f))
    print("OK: source SHA-256 unchanged")
    print("STAGE-1 PATCH PREFLIGHT PASSED; no production modeset fix yet.")
except (RuntimeError, OSError) as e:
    print("STOP:", e, file=sys.stderr)
    sys.exit(2)
