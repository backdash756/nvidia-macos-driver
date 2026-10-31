#!/usr/bin/env python3
"""Strict source preparation and isolated build for two-display YUV420 fix-005.

Takes verified, built fix-004 source as an immutable parent. Only one XNU
function is changed; all NVIDIA source and all other NullMoth files are
byte-identical. Never installs, loads or changes any kext/EFI/NVRAM/KC.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OLD_RUNNER = HERE.parent / "nvrm615fix/build.py"
OVERLAY = HERE / "patches/xnu-yuv420-dual-optin.patch"
TEST = REPO / "tools/test_yuv420_fix005_dual.py"
XNU = "kexts/NVRM/os-nvkms-xnu.cpp"
KEY = "nullmoth/" + XNU
PROFILE = "yuv420-fix-005-dual"
PARENT_NVRM_SHA = "c73a8b5065e1474a07e6ba7dd66c151266e11b5c9e053f113a22d90a0cf18160"
PARENT_XNU_SHA = "2b1c434507a91c8dffa60f101caf30a326d7711a1f5cc05c4404d5ddbf24ca7b"
CHANGED_XNU_SHA = "4fb65aed709d993328d76ec23bc07ffc16b2c94f65b6c6e735ab2d08a5868e22"
OVERLAY_GIT_BLOB = "e85108d4a6933d5e6d8339b8eb06ddd87b9a7087"

spec = importlib.util.spec_from_file_location("fix005_pinned_fix004_runner", OLD_RUNNER)
FIX004 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FIX004)
BASE = FIX004.BASE


def stop(message):
    raise ValueError(message)


def require(ok, message):
    if not ok:
        stop(message)


def sha(path):
    return BASE.sha(path)


def recipe():
    return {p.relative_to(REPO).as_posix(): sha(p)
            for p in (Path(__file__), OVERLAY, TEST)}


def check_patch():
    require(OVERLAY.is_file() and not OVERLAY.is_symlink(),
            "Missing exact fix-005 source overlay")
    blob = subprocess.check_output(
        ["git", "-C", str(REPO), "hash-object", str(OVERLAY)],
        text=True).strip()
    require(blob == OVERLAY_GIT_BLOB, "fix-005 overlay blob differs")
    require(TEST.is_file(), "Missing gate regression test")


def pinned_parent(value):
    root = BASE.output_path(value)
    root = root.resolve(strict=True)
    _, manifest = FIX004.load_inputs(root)
    require(sha(root / "NVRM.kext/Contents/MacOS/NVRM") == PARENT_NVRM_SHA,
            "Proven fix-004 NVRM binary hash changed")
    require(sha(root / KEY) == PARENT_XNU_SHA,
            "Proven fix-004 source preimage changed")
    require((root / "fix-build-state.json").is_file() and
            json.loads((root / "fix-build-state.json").read_text())
            .get("status") == "built_static_review_required",
            "Proven fix-004 build status differs")
    require(manifest["nullmoth_sha256"][XNU] == PARENT_XNU_SHA,
            "Proven fix-004 source manifest differs")
    return root, manifest


def expected_manifest(parent, source_dir, output):
    old, original = pinned_parent(parent)
    result = copy.deepcopy(original)
    result["output"] = str(output)
    result["purpose"] = "fix-005 dual-display isolated source: NO installation approval"
    result["fix005_profile"] = PROFILE
    result["fix005_parent"] = str(old)
    result["fix005_parent_binary_sha256"] = PARENT_NVRM_SHA
    result["fix005_parent_manifest_sha256"] = sha(old / "inputs.json")
    result["fix005_recipe_sha256"] = recipe()
    result["fix005_overlay_blob"] = OVERLAY_GIT_BLOB
    result["fix005_source_before_sha256"] = PARENT_XNU_SHA
    result["fix005_source_after_sha256"] = CHANGED_XNU_SHA
    result["nullmoth_sha256"][XNU] = CHANGED_XNU_SHA
    result["runtime_approved"] = False
    result["hardware_approved"] = False
    return result


def copy_source(src, dest):
    def ignored(path, names):
        return {"_out"} & set(names)
    shutil.copytree(src, dest, ignore=ignored, symlinks=False)


def prepare(args):
    check_patch()
    parent, old_manifest = pinned_parent(args.parent)
    output = BASE.output_path(args.output, fresh=True)
    require(not output.exists(), "Output exists, refuse to overwrite")
    require(not (output == parent or output in parent.parents or parent in output.parents),
            "New build and fix-004 parent must be separate trees")
    output.mkdir(parents=True)
    try:
        copy_source(parent / "ogkm", output / "ogkm")
        copy_source(parent / "nullmoth", output / "nullmoth")
        staged = output / KEY
        require(sha(staged) == PARENT_XNU_SHA, "Staged source hash wrong")
        subprocess.run(["git", "apply", "--check", str(OVERLAY)],
                       cwd=output / "nullmoth", check=True)
        subprocess.run(["git", "apply", str(OVERLAY)],
                       cwd=output / "nullmoth", check=True)
        require(sha(staged) == CHANGED_XNU_SHA, "Patched fix-005 source hash wrong")
        require(FIX004.source_tree_hashes(output / "ogkm") ==
                old_manifest["vendor_sha256"],
                "Unexpected change to any NVIDIA source")
        desired = dict(old_manifest["nullmoth_sha256"])
        desired[XNU] = CHANGED_XNU_SHA
        require(FIX004.source_tree_hashes(output / "nullmoth") == desired,
                "Unexpected change to any other NullMoth source")
        new = expected_manifest(parent, output / "nullmoth", output)
        BASE.save(output / "inputs.json", new)
        BASE.save(output / "fix005-state.json", {
            "status": "prepared_source_only",
            "parent_build": str(parent),
            "parent_binary_sha256": PARENT_NVRM_SHA,
            "only_changed_source": KEY,
            "source_sha256": CHANGED_XNU_SHA,
            "runtime_approved": False, "hardware_approved": False})
        load_inputs(str(output))
    except Exception:
        print("WARNING: stopped preparation left its isolated output for inspection.", file=sys.stderr)
        raise
    print("FIX-005 SOURCE PREPARATION PASSED")
    print("Changed source file:", KEY)
    print("NVIDIA source tree byte-identical to fix-004: YES")
    print("Source-only build directory:", output)
    print("NO compilation, installation, EFI or boot-argument changes.")


def load_inputs(value):
    output = BASE.output_path(value)
    check_patch()
    p = output / "inputs.json"
    require(p.is_file(), "Missing fix-005 inputs")
    manifest = json.loads(p.read_text())
    parent = manifest.get("fix005_parent")
    require(isinstance(parent, str), "Missing pinned parent path")
    expected = expected_manifest(parent, output / "nullmoth", output)
    require(manifest == expected, "Fix-005 input proof/identity differs")
    require((output / "fix005-state.json").is_file(),
            "Missing independent fix-005 state")
    state = json.loads((output / "fix005-state.json").read_text())
    require(state.get("status") == "prepared_source_only" and
            state.get("parent_binary_sha256") == PARENT_NVRM_SHA and
            state.get("only_changed_source") == KEY and
            state.get("source_sha256") == CHANGED_XNU_SHA,
            "Wrong fix-005 stage state")
    for subdir, key in (("ogkm", "vendor_sha256"),
                        ("nullmoth", "nullmoth_sha256")):
        require(FIX004.source_tree_hashes(output / subdir) == manifest[key],
                "Unapproved source change in " + subdir)
    return output, manifest


def build(args):
    require(platform.system() == "Darwin" and os.geteuid() != 0,
            "Build only on macOS as normal user, without sudo")
    output, manifest = load_inputs(args.output)
    require(not (output / "build-state.json").exists(), "Build already attempted")
    parent, old_manifest = pinned_parent(manifest["fix005_parent"])
    reference = Path(args.reference).expanduser().resolve(strict=True)
    expected_ref = Path.home() / "nvrm-reference/2.5.4/NVRM.kext"
    require(reference == expected_ref,
            "Use the separately saved official 2.5.4 reference NVRM")
    require(plistlib.loads((reference / "Contents/Info.plist").read_bytes()) ==
            plistlib.loads((parent / "NVRM.kext/Contents/Info.plist").read_bytes()),
            "Reference metadata differs from working fix-004")
    BASE.build(args, input_loader=load_inputs, artifact_metadata_version="2.5.4")
    require((output / "NVRM.kext/Contents/MacOS/NVRM").is_file(),
            "Expected fix-005 bundle missing")
    require(plistlib.loads((output / "NVRM.kext/Contents/Info.plist").read_bytes()) ==
            plistlib.loads((parent / "NVRM.kext/Contents/Info.plist").read_bytes()),
            "Candidate plist changed")
    BASE.save(output / "fix005-build-state.json", {
        "status": "built_static_comparison_required",
        "parent_sha256": PARENT_NVRM_SHA,
        "candidate_sha256": sha(output / "NVRM.kext/Contents/MacOS/NVRM"),
        "runtime_approved": False,
        "hardware_approved": False})
    print("FIX-005 COMPILED AND SIGNED. Full fix004-vs-fix005 audit is still REQUIRED.")
    print("Candidate NVRM SHA-256:", sha(output / "NVRM.kext/Contents/MacOS/NVRM"))
    print("No installed kext or boot parameters changed.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest="action", required=True)
    prep = subs.add_parser("prepare")
    prep.add_argument("--parent", required=True, help="Existing built fix-004-build-003 root")
    prep.add_argument("--output", required=True, help="Fresh, isolated fix-005 build root")
    b = subs.add_parser("build")
    b.add_argument("--output", required=True)
    b.add_argument("--reference", required=True)
    b.add_argument("--jobs", type=int, default=4)
    b.add_argument("--sdk")
    try:
        args = p.parse_args()
        if args.action == "prepare":
            prepare(args)
        else:
            build(args)
        return 0
    except (OSError, ValueError, KeyError, RuntimeError,
            subprocess.CalledProcessError, TypeError) as exc:
        print("FIX-005 STOP:", exc, file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
