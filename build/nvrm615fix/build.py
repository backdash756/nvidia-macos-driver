#!/usr/bin/env python3
"""Strict, isolated source preparation and static macOS build for YUV420 fix-004.

Never installs or loads a kext and never changes EFI, NVRAM, KextPolicy or
KernelCollections. The successful prior source-only fix-004 directory is read-only.
Use a distinct, new output for the build candidate.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fix004_source_prepare", HERE / "prepare.py")
PREP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREP)
META, BASE, DIAG = PREP.META, PREP.BASE, PREP.DIAG
KIND = "yuv420-fix-004"
VERSION = "2.5.4"
REQUIRED_SAVED = ("metadata-inputs.json", "baseline-inputs.json",
                  "diagnostic-inputs.json", "metadata-manifest.json",
                  "metadata-pre-fix-state.json", "fix-state.json")


def fail(reason):
    raise RuntimeError(reason)


def recipe_hashes():
    return {p.relative_to(HERE).as_posix(): BASE.sha(p)
            for p in sorted(HERE.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def validate_reference_recipe_pair(reference, recorded):
    """Allow only the historically pinned metadata-only builder extension.

    A reference-005 recipe was captured before build.py gained the 2.5.4
    metadata flag. No make flags, build commands, SDK choices or linker options
    changed. The complete historical/new build.py Git objects are pinned;
    all *other* reference recipe SHA-256 entries must remain identical.
    The separate binary comparator still strictly checks normalized compiler
    commands, toolchains, 1,338 objects, archives, imports, and exports.
    """
    bridge = PREP.read_proof()["reference_recipe_bridge"]
    expected = META.load_proof()["reference_recipe_sha256"]
    current = BASE.recipe_hashes()
    if (reference.get("recipe_sha256") != expected or
            recorded.get("recipe_sha256") != current):
        fail("reference-005 and new baseline must use exactly pinned historical/current recipes")
    if (set(current) != set(expected) or
            any(current[name] != digest for name, digest in expected.items()
                if name != bridge["only_permitted_recipe_change"])):
        fail("A reference build recipe other than build.py differs from historical reference-005")
    historical_file = bridge["historical_commit"] + ":build/nvrm615/build.py"
    historical_blob = subprocess.check_output([
        "git", "-C", str(PREP.REPO), "rev-parse", historical_file],
        text=True).strip()
    current_blob = subprocess.check_output([
        "git", "-C", str(PREP.REPO), "hash-object", str(BASE.HERE / "build.py")],
        text=True).strip()
    if (historical_blob != bridge["original_build_py_git_blob"] or
            current_blob != bridge["metadata_aware_build_py_git_blob"]):
        fail("Reference builder sources do not match exact reviewed historic/current Git blobs")
    print("OK: old reference-005 and new metadata-aware builder match the pinned recipe bridge")


def source_tree_hashes(root):
    """Verify exact tracked-source inventory, excluding compiler's _out only.

    The reference builder writes hundreds of generated artifacts beneath _out
    while compiling; these are *not* additional staged source files. All other
    paths remain subject to exact filename + SHA-256 inventory checking.
    """
    return {path.relative_to(root).as_posix(): BASE.sha(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and "_out" not in path.relative_to(root).parts}


def combined_proof(proof, metadata_proof):
    if (metadata_proof != META.load_proof() or
            proof != PREP.read_proof()):
        fail("Historical metadata/fix proof differs")
    expected = copy.deepcopy(metadata_proof)
    for source, after in proof["after_fix_sources"].items():
        if source not in expected["source_changes"] or (
                expected["source_changes"][source]["after_sha256"]
                != proof["after_metadata_sources"][source]):
            fail("Fix source transition is not based on pinned metadata")
        expected["source_changes"][source]["after_sha256"] = after
    expected["expected_changed_objects"] = proof["expected_object_allowlist"]
    expected["allowed_extra_exports"] = proof["allowed_extra_exports"]
    return expected


def prepare(args):
    out = BASE.output_path(args.output, fresh=True)
    PREP.prepare(args)
    if not out.exists():
        fail("Source preparation did not create output")
    original = json.loads((out / "inputs.json").read_text())
    proof = PREP.read_proof()
    combined = combined_proof(proof, META.load_proof())
    result = copy.deepcopy(original)
    result.update(fix_build_schema=1,
                  fix_combined_proof=combined,
                  fix_build_recipe_sha256=recipe_hashes())
    # Inputs are finalized once, after all source snapshots have been verified.
    BASE.save(out / "inputs.json", result)
    BASE.save(out / "fix-build-gate.json", {
        "schema": 1, "kind": KIND, "status": "prepared_not_built",
        "manifest_sha256": BASE.sha(out / "inputs.json"),
        "runtime_approved": False, "hardware_approved": False})
    load_inputs(out)
    print("FIX-004 BUILD PREPARATION PASSED")
    print("No compilation, installation or boot configuration changes.")


def load_inputs(value):
    out = BASE.output_path(value)
    manifest_file = out / "inputs.json"
    if not manifest_file.is_file():
        fail("Fix-004 final build manifest missing")
    manifest = json.loads(manifest_file.read_text())
    proof = PREP.read_proof()
    metadata = META.load_proof()
    combined = combined_proof(proof, metadata)
    if (manifest.get("format"), manifest.get("build_kind"),
            manifest.get("metadata_version"), manifest.get("diagnostic_enabled"),
            manifest.get("metadata_enabled"), manifest.get("experimental"),
            manifest.get("fix_profile"), manifest.get("fix_build_schema"),
            manifest.get("runtime_approved"), manifest.get("hardware_approved"),
            manifest.get("nullmoth_commit"), manifest.get("nvidia_commit"),
            manifest.get("output")) != (
            3, META.KIND, VERSION, True, True, True,
            KIND, 1, False, False, BASE.NULLMOTH, BASE.NVIDIA, str(out)):
        fail("Invalid fix-004 build role")
    if (manifest.get("fix_proof") != proof
            or manifest.get("fix_combined_proof") != combined
            or manifest.get("metadata_proof") != metadata
            or manifest.get("fix_build_recipe_sha256") != recipe_hashes()
            or manifest.get("fix_recipe_sha256") != PREP.recipe_hashes()
            or manifest.get("metadata_recipe_sha256") != META.recipe_hashes()
            or manifest.get("diagnostic_recipe_sha256") != DIAG.recipe_hashes()
            or manifest.get("recipe_sha256") != BASE.recipe_hashes()):
        fail("Pinned fix/base recipe or complete proof changed")
    for name in REQUIRED_SAVED:
        if not (out / name).is_file():
            fail("Missing preserved evidence: " + name)
    prior = json.loads((out / "metadata-inputs.json").read_text())
    original = json.loads((out / "diagnostic-inputs.json").read_text())
    baseline = json.loads((out / "baseline-inputs.json").read_text())
    if (prior.get("metadata_proof") != metadata
            or prior.get("metadata_version") != VERSION
            or prior.get("build_kind") != META.KIND
            or original.get("diagnostic_proof") != metadata["diagnostic_proof"]
            or baseline.get("format") != 1
            or baseline.get("diagnostic_enabled") is not False):
        fail("Original baseline/diagnostic/metadata records mismatch")
    if (manifest.get("metadata_input_sha256")
            != BASE.sha(out / "metadata-inputs.json")
            or manifest.get("diagnostic_manifest_sha256")
            != BASE.sha(out / "diagnostic-inputs.json")
            or manifest.get("baseline_manifest_sha256")
            != BASE.sha(out / "baseline-inputs.json")
            or manifest.get("metadata_manifest_sha256")
            != BASE.sha(out / "metadata-manifest.json")):
        fail("Preserved manifest hash mismatch")
    # The metadata and diagnostic source-map entries are preserved identically
    # except for precisely the two intended new source files.
    maps = PREP.source_maps(prior, proof)
    for key in ("vendor_sha256", "nullmoth_sha256"):
        if manifest.get(key) != maps[key]:
            fail("Changed source scope exceeds the fix proof: " + key)
    for folder, key in (("ogkm", "vendor_sha256"), ("nullmoth", "nullmoth_sha256")):
        BASE.verify_hashes(out / folder, maps[key])
        if source_tree_hashes(out / folder) != maps[key]:
            fail("Unexpected extra source or header outside generated _out in " + folder)
    BASE.verify_hashes(out / "ogkm",
                       json.loads((PREP.REPO / "build/nvrm615/patches/patch-proof.json")
                                  .read_text())["preserved_sources_sha256"])
    if manifest.get("fix_patch_order") != [p["file"] for p in proof["patches"]]:
        fail("Fix patch order changed")
    gate = json.loads((out / "fix-build-gate.json").read_text())
    if (gate.get("schema"), gate.get("kind"), gate.get("manifest_sha256"),
            gate.get("runtime_approved"), gate.get("hardware_approved")) != (
            1, KIND, BASE.sha(manifest_file), False, False):
        fail("Wrong fix-004 build gate")
    return out, manifest


def build(args):
    if platform.system() != "Darwin" or os.geteuid() == 0:
        fail("Build requires macOS and an ordinary (non-root) user")
    out, manifest = load_inputs(args.output)
    if (out / "build-state.json").exists():
        fail("Single-attempt fix-004 build already started")
    base = Path(args.baseline_build).expanduser().resolve(strict=True)
    if base == out or base in out.parents or out in base.parents:
        fail("Fix and baseline trees overlap")
    old = json.loads((base / "inputs.json").read_text())
    recorded = json.loads((out / "baseline-inputs.json").read_text())
    for key in ("format", "nullmoth_commit", "nvidia_commit",
                "vendor_sha256", "nullmoth_sha256", "patch_order",
                "source_date_epoch"):
        if old.get(key) != recorded.get(key):
            fail("reference-005 pinned source identity differs: " + key)
    validate_reference_recipe_pair(old, recorded)
    original = Path(args.reference).expanduser().resolve(strict=True)
    official_sha = BASE.sha(original / "Contents/MacOS/NVRM")
    before_sha = BASE.sha(base / "NVRM.kext/Contents/MacOS/NVRM")
    report = BASE.output_path(args.comparison_output, fresh=True)
    for root in (out, base):
        if report == root or root in report.parents or report in root.parents:
            fail("Comparison output overlaps input build")
    state = {
        "status": "building", "build_kind": KIND,
        "source_manifest_sha256": BASE.sha(out / "inputs.json"),
        "reference_binary_sha256": before_sha,
        "official_binary_sha256": official_sha,
        "metadata_version": VERSION,
        "runtime_approved": False, "hardware_approved": False}
    BASE.save(out / "fix-build-state.json", state)
    try:
        BASE.build(args, input_loader=load_inputs, artifact_metadata_version=VERSION)
        bundle = out / "NVRM.kext"
        km = META.HERE / "kmod.py"
        spec = importlib.util.spec_from_file_location("fix004_kmod_inspector", km)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        kmod = module.inspect_kmod(bundle / "Contents/MacOS/NVRM",
                                   expected_version=VERSION)
        candidate_plist = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
        expected_plist = plistlib.loads((original / "Contents/Info.plist").read_bytes())
        if plistlib.dumps(candidate_plist, fmt=plistlib.FMT_BINARY, sort_keys=True) != (
                plistlib.dumps(expected_plist, fmt=plistlib.FMT_BINARY, sort_keys=True)):
            fail("Candidate bundle plist differs from official 2.5.4")
        BASE.save(out / "fix-metadata-check.json", {
            "status": "static_verified", "kmod": kmod,
            "full_official_plist_equal": True,
            "runtime_approved": False, "hardware_approved": False})
        compare_cmd = [
            sys.executable, str(HERE / "compare.py"),
            "--baseline-build", str(base),
            "--candidate-build", str(out),
            "--output", str(report)]
        if args.llvm_objdump:
            compare_cmd.extend(("--llvm-objdump", args.llvm_objdump))
        if subprocess.run(compare_cmd).returncode:
            fail("Fix-004 static binary comparison rejected; inspect " + str(report))
        if BASE.sha(base / "NVRM.kext/Contents/MacOS/NVRM") != before_sha or (
                BASE.sha(original / "Contents/MacOS/NVRM") != official_sha):
            fail("Unchanged baseline or official backup was modified")
        state["status"] = "built_static_review_required"
        print("FIX-004 BUILD COMPLETED; STATIC REVIEW STILL REQUIRED.")
        print("No runtime or hardware approval. Do not install this kext.")
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        state["status"] = "failed"
        raise
    finally:
        BASE.save(out / "fix-build-state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prep = commands.add_parser("prepare", help="Fresh verified fix+metadata source build inputs")
    prep.add_argument("--vendor", required=True)
    prep.add_argument("--output", required=True)
    run = commands.add_parser("build", help="Compile, sign and strictly compare, NEVER install")
    run.add_argument("--output", required=True)
    run.add_argument("--reference", required=True)
    run.add_argument("--baseline-build", required=True)
    run.add_argument("--comparison-output", required=True)
    run.add_argument("--jobs", type=int, default=4)
    run.add_argument("--sdk")
    run.add_argument("--llvm-objdump")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare(args)
        else:
            build(args)
    except (RuntimeError, OSError, ValueError, KeyError,
            subprocess.SubprocessError) as e:
        print("FIX-004 STOP:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
