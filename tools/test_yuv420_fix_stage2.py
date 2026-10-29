#!/usr/bin/env python3
"""Strict isolated source-only check of NVIDIA 615 YUV420 fix stages 1 + 2.

No build, signing, kext loading, boot-arg change, or writes into existing
source/checkouts. Applies the proposed patches only to a temporary copy.
"""
from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BASE = Path.home() / "nvrm-builds/yuv420-diag-003"
BASE_KAPI = BASE / "ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c"
BASE_XNU = BASE / "nullmoth/kexts/NVRM/os-nvkms-xnu.cpp"
PIN_KAPI = "d25439b4c3ea42a2e739227b34c1ce12d94c4c6441680bbd7a0774c9c07e1481"
PIN_XNU = "ec27b73112ea5e029a2da46ce10953ef72a522dd1e2de319bd678334570ad37d"
P1 = ROOT / "build/nvrm615fix/patches/615-yuv420-fix-discovery.patch"
P2 = ROOT / "build/nvrm615fix/patches/615-yuv420-fix-validate-setmode.patch"
PX = ROOT / "build/nvrm615fix/patches/xnu-yuv420-fix-optin.patch"

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()

def run(cwd, *args):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(" ".join(args) + "\n" + result.stderr.strip())

def patch(cwd, path, directory=None):
    common = ["git", "apply"]
    if directory:
        common.append("--directory=" + directory)
    # No unsafe/fuzzy apply; a failed context check aborts before modification.
    run(cwd, *common, "--check", str(path))
    run(cwd, *common, str(path))

def main():
    for f, expected in ((BASE_KAPI, PIN_KAPI), (BASE_XNU, PIN_XNU)):
        if not f.is_file() or f.is_symlink() or sha(f) != expected:
            raise RuntimeError("staged diag-003 source is absent or hash differs: " + str(f))
    for p in (P1, P2, PX):
        if not p.is_file() or p.is_symlink():
            raise RuntimeError("missing or symlinked patch: " + str(p))
    print("OK: unchanged diagnostic-003 inputs and patch files")
    with tempfile.TemporaryDirectory(prefix="y420-fix-004-source-") as folder:
        stage = Path(folder)
        ogkm = stage / "ogkm"
        xnu = stage / "nullmoth"
        staged_kapi = ogkm / "src/nvidia-modeset/kapi/src/nvkms-kapi.c"
        staged_xnu = xnu / "kexts/NVRM/os-nvkms-xnu.cpp"
        staged_kapi.parent.mkdir(parents=True)
        staged_xnu.parent.mkdir(parents=True)
        shutil.copyfile(BASE_KAPI, staged_kapi)
        shutil.copyfile(BASE_XNU, staged_xnu)

        patch(ogkm, P1, "src/nvidia-modeset")
        print("OK: stage 1 patch applied to isolated copy")
        patch(ogkm, P2, "src/nvidia-modeset")
        print("OK: stage 2 validation/modeset patch applied to isolated copy")
        patch(xnu, PX)
        print("OK: single-display opt-in hook applied to isolated copy")

        source = staged_kapi.read_text()
        required = (
            "Y420FixCandidate(", "Y420FixSameTiming(",
            "Y420FixRestoreValidatedMode(",
            "Y420_FIX_MAX_MODE_INDEX 128",
            "p->reply.mode.timings.yuv420Mode != NV_YUV420_MODE_HW",
            "Y420FixRestoreValidatedMode(device, display, mode,",
            "headModeSetConfig->numDisplays == 1",
        )
        if not all(part in source for part in required):
            raise RuntimeError("missing expected validation and safety gates")
        if "nvyuv420fix" not in staged_xnu.read_text():
            raise RuntimeError("opt-in hook missing after patching")
        print("KAPI SHA-256 after stage 1+2:", sha(staged_kapi))
        print("XNU  SHA-256 after opt-in:", sha(staged_xnu))

    if sha(BASE_KAPI) != PIN_KAPI or sha(BASE_XNU) != PIN_XNU:
        raise RuntimeError("existing diagnostic input changed")
    print("OK: temporary files discarded; original diag-003 sources unchanged")
    print("STAGE-2 SOURCE PREFLIGHT PASSED — NOT A BUILD OR HARDWARE APPROVAL")

if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, UnicodeError) as error:
        print("STAGE-2 STOP:", error, file=sys.stderr)
        sys.exit(2)
