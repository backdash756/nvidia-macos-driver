#!/usr/bin/env python3
"""Read-only preflight for the NVIDIA 615 YUV420 production-fix experiment.

Does not fetch, build, sign, install, modify NVRAM, or write any files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

BRANCH = "experiment/nvrm-615-yuv420-fix"
NVIDIA_PIN = "1a82fbfda8f321b056eb3568850f9316c5e465c5"
NULLMOTH_PIN = "46779055f68208baa5ddfb9b2e9c77ed028e1d59"
ORIGINAL_SHA = "4591c5bd732838417168b366380c9a0eefdd4ad940d3eed3bfff22f6ade2e0f7"
DIAG003_SHA = "62305992f2cd6de9861d6de0fae26f9d4dba52c37adca442af51be7e71380587"
ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

def cmd(*args: str) -> str:
    result = subprocess.run(args, text=True, capture_output=True, check=True)
    return result.stdout.strip()

def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def binary(bundle: Path, expected: str, label: str) -> None:
    path = bundle / "Contents/MacOS/NVRM"
    require(path.is_file(), f"{label}: missing {path}")
    require(digest(path) == expected, f"{label}: binary hash differs from pinned reference")
    cmd("/usr/bin/codesign", "--verify", "--strict", str(bundle))
    print(f"OK: {label} SHA-256 and code signature")

def main() -> None:
    print("=== READ-ONLY YUV420-FIX PREFLIGHT ===")
    require(cmd("git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD") == BRANCH,
            f"checkout is not on {BRANCH}")
    require(not cmd("git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=normal"),
            "fix checkout contains local modifications")
    for rel in ("build/nvrm615diag/build.py",
                "build/nvrm615diag/proof.json",
                "docs/NVRM-615-YUV420-FIX-PLAN.md"):
        require((ROOT / rel).is_file(), f"missing repository file {rel}")
    print("OK: clean development checkout and baseline diagnostic recipe")

    vendor = HOME / "nvrm-inputs/open-gpu-615"
    require((vendor / ".git").exists(), f"NVIDIA checkout missing: {vendor}")
    require(cmd("git", "-C", str(vendor), "rev-parse", "HEAD") == NVIDIA_PIN,
            "NVIDIA source checkout is not the pinned 615 commit")
    require(not cmd("git", "-C", str(vendor), "status", "--porcelain", "--untracked-files=normal"),
            "NVIDIA checkout is not clean")
    print("OK: clean pinned NVIDIA 615.78.08 source")

    for name in ("reference-005", "yuv420-diag-002", "yuv420-diag-003"):
        path = HOME / "nvrm-builds" / name
        require(path.is_dir(), f"missing existing build: {path}")
        manifest = path / "inputs.json"
        require(manifest.is_file(), f"missing recorded build manifest: {manifest}")
        data = json.loads(manifest.read_text())
        require(data.get("nvidia_commit") == NVIDIA_PIN, f"{name}: vendor pin differs")
        require(data.get("nullmoth_commit") == NULLMOTH_PIN, f"{name}: NullMoth pin differs")
        print(f"OK: recorded build {name} and source pins")

    binary(HOME / "nvrm-reference/2.5.4/NVRM.kext",
           ORIGINAL_SHA, "official 2.5.4 backup")
    binary(HOME / "nvrm-builds/yuv420-diag-003/NVRM.kext",
           DIAG003_SHA, "known working diagnostic 003")
    print("Xcode developer directory:", cmd("/usr/bin/xcode-select", "-p"))
    print("Compiler:", cmd("/usr/bin/xcrun", "--find", "clang"))
    print("macOS build:", cmd("/usr/bin/sw_vers", "-buildVersion"))
    print("Existing production driver and boot configuration: NOT MODIFIED")
    print("PREFLIGHT PASSED — SOURCE IMPLEMENTATION AND TESTING STILL REQUIRED")

if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError,
            KeyError, RuntimeError) as exc:
        print("PREFLIGHT STOP:", exc, file=sys.stderr)
        sys.exit(2)
