#!/usr/bin/env python3
"""Strict static delta audit of built fix-005 against the working fix-004.

Never installs, signs, loads, modifies an EFI or alters kernel collections.
Structural/instruction checks supplement the strict one-object scope but
do not imply runtime safety or hardware approval.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location("fix005_build_runner", HERE / "build.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
BASE = RUNNER.BASE

def load_module(label, path):
    spec = importlib.util.spec_from_file_location(label, path)
    item = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item

COMPARE = load_module("fix005_diagnostic_compare",
                      REPO / "build/nvrm615diag/compare.py")
KMOD = load_module("fix005_metadata_reader",
                   REPO / "build/nvrm615meta/kmod.py")
EXPECTED_OBJECT = "glue/os-nvkms-xnu.o"
MAY_CHANGE = {
    "NVRM.kext/Contents/MacOS/NVRM",
    "NVRM.kext/Contents/_CodeSignature/CodeResources",
}

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def cmd(command):
    subprocess.run([str(item) for item in command], check=True)

def audit(args):
    old, original = RUNNER.pinned_parent(args.parent)
    new, manifest = RUNNER.load_inputs(args.candidate)
    require(manifest["fix005_parent"] == str(old), "Wrong fix-004 parent binding")
    require((new / "fix005-build-state.json").is_file(),
            "Candidate was not built through isolated runner")
    state = json.loads((new / "fix005-build-state.json").read_text())
    require(state.get("status") == "built_static_comparison_required",
            "Candidate build did not report a completed build")
    candidate = new / "NVRM.kext"
    baseline = old / "NVRM.kext"
    require(BASE.sha(candidate / "Contents/MacOS/NVRM") == state["candidate_sha256"],
            "Candidate binary changed after build")
    require(BASE.sha(baseline / "Contents/MacOS/NVRM") == RUNNER.PARENT_NVRM_SHA,
            "Reference fix-004 binary changed")
    for bundle in (baseline, candidate):
        cmd(["codesign", "--verify", "--strict", bundle])
    require(plistlib.loads((candidate / "Contents/Info.plist").read_bytes()) ==
            plistlib.loads((baseline / "Contents/Info.plist").read_bytes()),
            "Plist differs from proven fix-004")
    for bundle in (baseline, candidate):
        checked = KMOD.inspect_kmod(bundle / "Contents/MacOS/NVRM",
                                    expected_version="2.5.4")
        require(checked is not None, "KMOD metadata missing")

    preflights = [COMPARE.toolchain_signature(COMPARE.read_json(
        root / "preflight/preflight.json")) for root in (old, new)]
    require(preflights[0] == preflights[1], "Compiler/SDK/header provenance changed")
    compiles = [COMPARE.compile_inventory(root) for root in (old, new)]
    require(compiles[0] == compiles[1], "Compile/link commands or flags changed")
    sources = [original, manifest]
    require(sources[0]["vendor_sha256"] == sources[1]["vendor_sha256"],
            "NVIDIA source manifest changed")
    require({k: v for k, v in sources[0]["nullmoth_sha256"].items()
             if k != RUNNER.XNU} ==
            {k: v for k, v in sources[1]["nullmoth_sha256"].items()
             if k != RUNNER.XNU}, "Other NullMoth source changed")
    for root, data in ((old, original), (new, manifest)):
        COMPARE.source_inventory(root, data)

    inventories = [COMPARE.artifact_inventory(root, source)
                   for root, source in zip((old, new), sources)]
    a, b = inventories
    require(set(a) == set(b), "Artifact inventory differs")
    changed_objects = {k for k in a if k.endswith(".o") and a[k] != b[k]}
    require(changed_objects == {EXPECTED_OBJECT},
            "Unexpected changed object(s): " + repr(sorted(changed_objects)))
    require(len([k for k in a if k.endswith(".o")]) == 1338,
            "NVRM object inventory count differs from fix-004 reference")
    other = {k for k in a if a[k] != b[k] and not k.endswith(".o")}
    require(other <= MAY_CHANGE,
            "Unexpected changed archives or bundle files: " + repr(sorted(other)))
    for component in ("nvidia", "nvidia-modeset"):
        old_archive = COMPARE.archive_inventory(old, component, a)
        new_archive = COMPARE.archive_inventory(new, component, b)
        require(old_archive["members"] == new_archive["members"],
                "NVIDIA archive contents changed: " + component)

    out = BASE.output_path(args.output, fresh=True)
    require(not out.exists(), "Audit output exists")
    require(all(out not in root.parents and root not in out.parents and out != root
                for root in (old, new)), "Audit output overlaps build")
    out.mkdir(parents=True)
    structural = out / "structural"
    cmd([sys.executable, REPO / "build/nvrm615/compare.py",
         "--reference", baseline, "--candidate", candidate,
         "--output", structural])
    details = COMPARE.read_json(structural / "comparison.json")["comparison"]
    require(not details.get("static_rejections") and
            not details.get("new_imports") and
            not details.get("missing_exports") and
            not details.get("extra_exports"),
            "New link/import/export errors relative to fix-004")

    instr = out / "instructions"
    instr_cmd = [sys.executable, REPO / "tools/nvrm_code_audit.py",
                 "--reference", baseline / "Contents/MacOS/NVRM",
                 "--candidate", candidate / "Contents/MacOS/NVRM",
                 "--output", instr]
    if args.llvm_objdump:
        instr_cmd.extend(["--llvm-objdump", args.llvm_objdump])
    cmd(instr_cmd)
    code = COMPARE.read_json(instr / "audit.json")
    require(code.get("status") == "complete" and
            code.get("counts", {}).get("examined"),
            "Instruction audit incomplete")
    require(BASE.sha(candidate / "Contents/MacOS/NVRM") == state["candidate_sha256"],
            "Candidate changed during comparison")
    require(BASE.sha(baseline / "Contents/MacOS/NVRM") == RUNNER.PARENT_NVRM_SHA,
            "Reference changed during comparison")
    report = {
        "status": "static_review_required",
        "parent_binary_sha256": RUNNER.PARENT_NVRM_SHA,
        "candidate_binary_sha256": state["candidate_sha256"],
        "only_changed_object": EXPECTED_OBJECT,
        "identical_object_count": 1337,
        "compiler_records_equal": True,
        "toolchain_equal": True,
        "nvidia_archives_equal": True,
        "plist_equal": True,
        "new_imports": details.get("new_imports"),
        "missing_exports": details.get("missing_exports"),
        "extra_exports": details.get("extra_exports"),
        "instruction_counts": code.get("counts"),
        "runtime_approved": False,
        "hardware_approved": False,
    }
    BASE.save(out / "fix005-audit.json", report)
    print("FIX-005 VS FIX-004: STATIC DELTA AUDIT PASSED")
    print("1337/1338 object files identical; only glue/os-nvkms-xnu.o changed")
    print("NVIDIA 615 archives, compiler records, metadata and exports unchanged")
    print("Instruction audit retained for human review at:", out)
    print("NO hardware or installation approval.")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parent", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--llvm-objdump")
    args = p.parse_args()
    try:
        audit(args)
    except (OSError, ValueError, KeyError, RuntimeError,
            subprocess.CalledProcessError, TypeError) as e:
        print("FIX-005 AUDIT STOP:", e, file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
