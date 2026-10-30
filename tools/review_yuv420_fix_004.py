#!/usr/bin/env python3
"""Read-only focused review of the already built fix-004 NVRM.

Reads pinned sources and existing full audit evidence; does not recompile,
rewrite build manifests, change EFI/NVRAM, sign, load or install anything.
Exit 0 means evidence was read successfully, NOT runtime approval.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

HOME = Path.home()
ROOT = HOME / "nvrm-audits/yuv420-fix-004-build-003-vs-reference-005"
BUILD = HOME / "nvrm-builds/yuv420-fix-004-build-003"
NVRM = BUILD / "NVRM.kext/Contents/MacOS/NVRM"
EXPECTED_SHA = "c73a8b5065e1474a07e6ba7dd66c151266e11b5c9e053f113a22d90a0cf18160"
EXPECTED_EXPORTS = {"_nvkms_yuv420_diag_claim", "_nvkms_yuv420_fix_enabled"}
EXPECTED_OBJECTS = {
    "glue/NVRM.o", "glue/os-nvkms-xnu.o",
    "ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-kapi.o",
    "ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-modepool.o",
}
FOCUS = ("_ApplyModeSetConfig", "_GetDisplayMode", "_ValidateDisplayMode",
         "_Y420FixRestoreValidatedMode", "_nvkms_yuv420_fix_enabled")

def load(path):
    return json.loads(path.read_text())

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for piece in iter(lambda: fp.read(1024*1024), b""):
            h.update(piece)
    return h.hexdigest()

def need(ok, message):
    if not ok:
        raise ValueError(message)

def main():
    need(sha256(NVRM) == EXPECTED_SHA, "Candidate NVRM hash differs: stop")
    result = load(ROOT / "comparison.json")
    structural = load(ROOT / "structural/comparison.json")["comparison"]
    audit = load(ROOT / "instructions/audit.json")
    build_state = load(BUILD / "fix-build-state.json")
    metadata = load(BUILD / "fix-metadata-check.json")
    need(build_state.get("status") == "built_static_review_required",
         "Wrong build-state; stop")
    need(metadata.get("full_official_plist_equal") is True,
         "2.5.4 plist mismatch; stop")
    need(metadata.get("status") == "static_verified", "KMOD not statically verified")
    need(result.get("status") == "review_required" and
         not result.get("static_rejections"), "Comparison rejected; stop")
    need(not structural.get("static_rejections") and
         not structural.get("new_imports") and
         not structural.get("missing_exports"), "Structural rejection; stop")
    need(set(structural.get("extra_exports", [])) == EXPECTED_EXPORTS,
         "Additional exports differ; stop")
    need(set(result.get("object_changes", [])) == EXPECTED_OBJECTS and
         not result.get("unexpected_object_changes") and
         not result.get("compile_record_changes"), "Unplanned object/compile changes")
    need(audit.get("status") == "complete", "Instruction audit not complete")

    counts = audit["counts"]
    changed = [r for r in audit["functions"] if r["status"] == "different"]
    instruction_changed = {
        r["symbol"] for r in changed
        if "instruction_or_encoding_difference" in r.get("difference_categories", [])
    }
    need(instruction_changed == set(FOCUS[:3]),
         "More or fewer code-different functions than the reviewed three: " +
         repr(sorted(instruction_changed)))

    print("=== IDENTITAET / BINAERBEGRENZUNG ===")
    print("NVRM SHA-256:", EXPECTED_SHA)
    print("Version/KMOD:", metadata.get("status"))
    print("Neue Exports:", ", ".join(sorted(EXPECTED_EXPORTS)))
    print("1338 Objekte, davon unveraendert:", result.get("object_counts", {}).get("identical"))
    print("Keine neuen Kernel-Imports/fehlenden Exports/unerwarteten Objekte: JA")
    print("\n=== MASCHINENCODE ===")
    print("Anzahl:", counts)
    kinds = Counter(k for r in changed for k in r.get("difference_categories", []))
    print("Unterschiedskategorien:", dict(kinds))
    entries = {r["symbol"]: r for r in audit["functions"]}
    for name in FOCUS:
        entry = entries.get(name)
        print("\n---", name, "---")
        if entry is None:
            print("KEIN SYMBOL mit exakt diesem Namen im Audit")
            continue
        print("Status:", entry["status"])
        print("Grund:", entry.get("reason", "-"))
        print("Kategorien:", entry.get("difference_categories", []))
        for diff in entry.get("examples", [])[:5]:
            print("  Instruction", diff.get("index"))
            print("    alt:", str(diff.get("reference", ""))[:240])
            print("    neu:", str(diff.get("candidate", ""))[:240])
        if entry["status"] == "not_comparable":
            print("  Nicht vergleichbar ist KEIN Nachweis der Korrektheit.")

    print("\n=== OFFENE PUNKTE ===")
    print("Ungepruefte Symbole:", counts.get("not_comparable"))
    print("EDID-Indexgrenze des Fixes: 128; benoetigt Abgleich mit realem 4K60-Modusindex")
    print("Fehlermodi, Umschalten, Hotplug und echte 4K60-Ausgabe: NICHT NACHGEWIESEN")
    print("STATUS: AUDIT-AUSZUG FERTIG; KEINE FREIGABE ZUR INSTALLATION")

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("AUDIT STOP:", error, file=sys.stderr)
        sys.exit(2)
