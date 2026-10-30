#!/usr/bin/env python3
"""Explicit four-object + 2.5.4 metadata static comparator for YUV420 fix-004.

Uses the proven diagnostic archive/instruction/structural comparison but with
a separate strictly validated FIX profile, full saved metadata provenance,
and a two-source code-change boundary. Never installs or loads a kext.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

def module(label, path):
    spec = importlib.util.spec_from_file_location(label, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

BUILD = module("fix004_candidate_build", HERE / "build.py")
SHARED = module("fix004_shared_diagnostic_compare",
                HERE.parent / "nvrm615diag/compare.py")
META_COMPARE = module("fix004_original_metadata_compare",
                      HERE.parent / "nvrm615meta/compare.py")
PROFILE = "yuv420-fix-004"


class FixPolicy(META_COMPARE.MetadataPolicy):
    proof_key = "fix_combined_proof"
    evidence_files = (
        "metadata-manifest.json", "diagnostic-inputs.json",
        "metadata-inputs.json", "metadata-pre-fix-state.json",
        "fix-state.json", "fix-build-gate.json", "fix-build-state.json",
        "fix-metadata-check.json")
    object_scope = "four explicitly pinned diagnostic/metadata/fix objects"
    allowed_nonobjects = (META_COMPARE.PLIST_BUNDLE,)

    def validate_manifest(self, manifest, root, proof):
        out, loaded = BUILD.load_inputs(root)
        if loaded != manifest or loaded.get(self.proof_key) != proof:
            raise ValueError("Fix-004 comparator identity or proof mismatch")
        if (loaded.get("fix_profile") != PROFILE
                or loaded.get("fix_proof") != BUILD.PREP.read_proof()):
            raise ValueError("Wrong independent fix-004 provenance")
        # Preserve the complete unchanged metadata identity, too.
        metadata = SHARED.read_json(SHARED.safe_file(root, "metadata-inputs.json"))
        if (metadata.get("metadata_proof") != BUILD.META.load_proof()
                or metadata.get("metadata_version") != BUILD.VERSION):
            raise ValueError("Preserved metadata experiment does not match 2.5.4")
        if loaded.get("fix_combined_proof") != BUILD.combined_proof(
                BUILD.PREP.read_proof(), BUILD.META.load_proof()):
            raise ValueError("Mode-fix source/exports policy differs")
        state = SHARED.read_json(SHARED.safe_file(root, "fix-build-state.json"))
        if (state.get("build_kind") != PROFILE
                or state.get("source_manifest_sha256") != SHARED.sha(root / "inputs.json")
                or state.get("runtime_approved") is not False
                or state.get("hardware_approved") is not False):
            raise ValueError("Fix-004 build-state lacks matching evidence")

    def inspect_artifacts(self, baseline, candidate, manifests, proof):
        # Preserve the complete original 2.5.4 KMOD and full plist comparison.
        report = super().inspect_artifacts(baseline, candidate, manifests, proof)
        check = SHARED.read_json(SHARED.safe_file(candidate, "fix-metadata-check.json"))
        if (check.get("status") != "static_verified"
                or check.get("full_official_plist_equal") is not True
                or check.get("runtime_approved") is not False
                or check.get("hardware_approved") is not False):
            raise ValueError("Incomplete fix-004 official-metadata check")
        report["fix_profile"] = PROFILE
        report["fix_metadata_check_sha256"] = SHARED.sha(
            candidate / "fix-metadata-check.json")
        report["runtime_approved"] = False
        report["hardware_approved"] = False
        return report


def main():
    # The generic comparator expects a proof file. Generate its exact merged
    # historical+fix proof in a disposable file, never changing source manifests.
    proof = BUILD.combined_proof(BUILD.PREP.read_proof(), BUILD.META.load_proof())
    with tempfile.TemporaryDirectory(prefix="y420-fix-004-policy-") as directory:
        path = Path(directory) / "fix-comparison-proof.json"
        path.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
        return SHARED.main(proof_path=path, candidate_policy=FixPolicy())


if __name__ == "__main__":
    sys.exit(main())
