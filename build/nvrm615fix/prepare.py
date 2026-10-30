#!/usr/bin/env python3
"""Prepare fix-004 using the pinned 2.5.4 metadata diagnostic recipe.

Source preparation ONLY. No binary build, codesigning, kernel loading,
EFI writes, KextPolicy changes, or modifications to existing build outputs.
The complete build and comparator are intentionally a separate approval gate.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
META_DIR = HERE.parent / "nvrm615meta"
META_SPEC = importlib.util.spec_from_file_location("fix004_metadata_runner", META_DIR / "build.py")
META = importlib.util.module_from_spec(META_SPEC)
META_SPEC.loader.exec_module(META)
DIAG = META.DIAG
BASE = META.BASE
PROOF_PATH = HERE / "proof.json"
KAPI = "ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c"
XNU = "nullmoth/kexts/NVRM/os-nvkms-xnu.cpp"
PROFILE = "yuv420-fix-004"


def fail(message):
    raise RuntimeError(message)


def recipe_hashes():
    return {p.relative_to(HERE).as_posix(): BASE.sha(p)
            for p in sorted(HERE.rglob("*")) if p.is_file()
            and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def read_proof():
    proof = json.loads(PROOF_PATH.read_text())
    if (proof.get("schema"), proof.get("kind"), proof.get("metadata_version"),
            proof.get("nvidia_commit"), proof.get("nullmoth_commit"),
            proof.get("runtime_approved"), proof.get("hardware_approved")) != (
            1, PROFILE, META.VERSION, BASE.NVIDIA, BASE.NULLMOTH, False, False):
        fail("Unexpected fix-004 proof identity")
    if (set(proof["after_metadata_sources"]) != {KAPI, XNU}
            or set(proof["after_fix_sources"]) != {KAPI, XNU}
            or set(proof["changed_source_keys"]) != {KAPI, XNU}):
        fail("Unexpected changed-source scope")
    if proof["expected_object_allowlist"] != [
            "ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-kapi.o",
            "ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-modepool.o",
            "glue/os-nvkms-xnu.o", "glue/NVRM.o"]:
        fail("Expected object scope changed")
    if proof["allowed_extra_exports"] != [
            "_nvkms_yuv420_diag_claim", "_nvkms_yuv420_fix_enabled"]:
        fail("Unexpected export scope")
    paths = [
        "build/nvrm615fix/patches/615-yuv420-fix-discovery.patch",
        "build/nvrm615fix/patches/615-yuv420-fix-validate-setmode.patch",
        "build/nvrm615fix/patches/xnu-yuv420-fix-optin.patch"
    ]
    if [p["file"] for p in proof["patches"]] != paths:
        fail("Patch order changed")
    for n, entry in enumerate(proof["patches"]):
        expected_root = "ogkm" if n < 2 else "nullmoth"
        expected_prefix = "src/nvidia-modeset" if n < 2 else None
        expected_file = ["src/nvidia-modeset/kapi/src/nvkms-kapi.c"] if n < 2 else [
            "kexts/NVRM/os-nvkms-xnu.cpp"]
        if (entry["directory"], entry["apply_directory"], entry["files"]) != (
                expected_root, expected_prefix, expected_file):
            fail("Unexpected patch target")
        path = REPO / entry["file"]
        if not path.is_file() or path.is_symlink():
            fail("Missing/symlinked pinned fix patch: " + str(path))
        blob = subprocess.check_output(["git", "-C", str(REPO), "hash-object", str(path)],
                                       text=True).strip()
        if blob != entry["git_blob_sha"]:
            fail("Fix patch blob differs: " + entry["file"])
    return proof


def source_maps(parent, proof):
    maps = {key: dict(parent[key]) for key in ("vendor_sha256", "nullmoth_sha256")}
    for key, after in proof["after_fix_sources"].items():
        folder, relative = key.split("/", 1)
        bucket = "vendor_sha256" if folder == "ogkm" else "nullmoth_sha256"
        if maps[bucket].get(relative) != proof["after_metadata_sources"][key]:
            fail("Staged metadata source preimage mismatch: " + key)
        maps[bucket][relative] = after
    return maps


def prepare(args):
    proof = read_proof()
    out = BASE.output_path(args.output, fresh=True)
    if out.exists():
        fail("Build output already exists; never reuse fix-004 output")
    # Rebuild the exact baseline, paired diagnostics, and metadata in an
    # independent fresh tree using the proven original runners.
    META.prepare(args)
    META.load_inputs(str(out))
    parent = json.loads((out / "inputs.json").read_text())
    BASE.verify_hashes(out, proof["after_metadata_sources"])
    maps = source_maps(parent, proof)

    # Preserve every original identity record, then apply only 3 fix patches.
    (out / "inputs.json").rename(out / "metadata-inputs.json")
    (out / "metadata-state.json").rename(out / "metadata-pre-fix-state.json")
    for entry in proof["patches"]:
        root = out / entry["directory"]
        path = REPO / entry["file"]
        DIAG.strict_patch(root, path, tuple(entry["files"]), entry["apply_directory"])

    BASE.verify_hashes(out, proof["after_fix_sources"])
    for folder, bucket in (("ogkm", "vendor_sha256"), ("nullmoth", "nullmoth_sha256")):
        if BASE.tree_hashes(out / folder) != maps[bucket]:
            fail("Fix changed files outside the two pinned sources: " + folder)

    # Keep format=3 and the explicit 2.5.4 metadata role because the
    # immutable reference compiler strictly recognizes that role. The
    # distinct fix_profile/proof fields are mandatory for the FIX runner and
    # will be rejected by the unchanged META loader's source map check.
    manifest = copy.deepcopy(parent)
    manifest.update(fix_profile=PROFILE,
                    fix_proof=proof,
                    fix_recipe_sha256=recipe_hashes(),
                    fix_patch_order=[p["file"] for p in proof["patches"]],
                    metadata_input_sha256=BASE.sha(out / "metadata-inputs.json"),
                    purpose="fix-004 source candidate: no runtime or hardware approval",
                    runtime_approved=False, hardware_approved=False,
                    **maps)
    BASE.save(out / "inputs.json", manifest)
    BASE.save(out / "fix-state.json", {
        "status": "prepared_source_only", "profile": PROFILE,
        "metadata_version": META.VERSION,
        "runtime_approved": False, "hardware_approved": False,
        "source_changes": proof["after_fix_sources"]})
    print("FIX-004 SOURCE PREPARATION PASSED")
    print("Output:", out)
    print("Pinned metadata: 2.5.4; previous diagnostic and metadata records preserved.")
    print("Changed source files:", ", ".join(sorted(proof["after_fix_sources"])))
    print("NO BINARY BUILT; NO INSTALLATION OR HARDWARE APPROVAL.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", required=True, help="Clean pinned NVIDIA 615.78.08 source checkout")
    parser.add_argument("--output", required=True, help="New, separate absolute output directory")
    args = parser.parse_args()
    try:
        prepare(args)
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("FIX-004 PREPARE STOP:", exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
