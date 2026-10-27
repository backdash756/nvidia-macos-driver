#!/usr/bin/env python3
"""Compare two NVRM bundles without loading, installing, or modifying either one."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

from macho import is_kext_bundle, parse_mach_header


LIMITATION = (
    "These static checks do not establish KAPI structure layout, semantic equivalence, "
    "kernel loadability, hardware behavior, or compatibility with the other installed "
    "2.5.4 components. Exit 0 is not approval to load the candidate or integrate diagnostics. "
    "No normalized disassembly or code-parity claim is made."
)
REQUIRED_TOOLS = ("file", "lipo", "otool", "nm", "codesign")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_symbols(output):
    """Parse BSD nm's normal rows; preserve duplicate defined symbol names."""
    rows = []
    for line in output.splitlines():
        match = re.fullmatch(r"\s*(?:([0-9a-fA-F]+)\s+)?([A-Za-z?])\s+(.+?)\s*", line)
        if match:
            address, kind, name = match.groups()
            rows.append({"address": int(address, 16) if address else None,
                         "kind": kind, "name": name})
    return rows


def parse_load_commands(output):
    blocks = re.split(r"(?m)^Load command \d+\s*$", output)[1:]
    commands = []
    for block in blocks:
        match = re.search(r"(?m)^\s*cmd (LC_\w+)\s*$", block)
        if match:
            commands.append({"cmd": match.group(1),
                             "details": [line.strip() for line in block.strip().splitlines()]})
    if not commands:
        raise ValueError("Cannot parse any Mach-O load commands")
    return commands


def text_section_end(output):
    for section in re.split(r"(?m)^\s*Section\s*$", output)[1:]:
        fields = {}
        for line in section.splitlines():
            pair = line.split(None, 1)
            if len(pair) == 2:
                fields.setdefault(pair[0], pair[1].strip())
        if fields.get("sectname") == "__text" and fields.get("segname") == "__TEXT":
            return int(fields["addr"], 16) + int(fields["size"], 16)
    raise ValueError("Cannot find __TEXT,__text bounds")


def unique_function_spans(rows, text_end):
    """Report symbol-to-next-address spans, including padding, not decoded code sizes."""
    text = [row for row in rows if row["kind"] in ("t", "T") and row["address"] is not None]
    counts = Counter(row["name"] for row in text)
    addresses = sorted({row["address"] for row in text} | {text_end})
    next_address = dict(zip(addresses, addresses[1:]))
    return {row["name"]: next_address[row["address"]] - row["address"]
            for row in text if counts[row["name"]] == 1 and row["address"] in next_address
            and row["address"] < text_end}


def prepare_output(path, bundles):
    if path.is_symlink():
        raise ValueError("Output directory must not be a symlink")
    resolved = path.expanduser().resolve()
    if any(part.casefold() in ("efi", "system", "library") or part.casefold().endswith(".kext")
           for part in resolved.parts):
        raise ValueError("Output must not be inside EFI, System, Library, or a kext, including mounted volumes")
    for bundle in bundles:
        if resolved == bundle or bundle in resolved.parents:
            raise ValueError("Output must be outside both input bundles")
    protected = ("/System", "/Library", "/Applications", "/usr", "/bin", "/sbin", "/etc", "/dev")
    for name in protected:
        protected_path = Path(name).resolve()
        if resolved == protected_path or protected_path in resolved.parents:
            raise ValueError("Output must not be a system directory")
    if resolved.exists():
        if not resolved.is_dir() or resolved.stat().st_uid != os.getuid():
            raise ValueError("Existing output must be a directory owned by the current user")
        if any(resolved.iterdir()):
            raise ValueError("Output directory must be new or empty; previous reports are never overwritten")
    else:
        if not resolved.parent.is_dir() or resolved.parent.stat().st_uid != os.getuid():
            raise ValueError("Create an owned output parent directory first")
        resolved.mkdir()
    return resolved


def checked_bundle(path):
    bundle = path.resolve(strict=True)
    binary = bundle / "Contents/MacOS/NVRM"
    info = bundle / "Contents/Info.plist"
    if not bundle.is_dir() or not binary.is_file() or not info.is_file():
        raise ValueError("Input must be a complete NVRM.kext bundle: " + str(bundle))
    with info.open("rb") as stream:
        plist = plistlib.load(stream)
    if not isinstance(plist, dict):
        raise ValueError("Info.plist is not a dictionary: " + str(info))
    return bundle, binary, info, plist


def run_tool(tools, output, records, label, name, *args, allow_failure=False):
    command = [tools[name], *map(str, args)]
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, errors="replace", check=False)
    stdout_file = label + ".stdout.txt"
    stderr_file = label + ".stderr.txt"
    (output / stdout_file).write_text(process.stdout)
    (output / stderr_file).write_text(process.stderr)
    records.append({"label": label, "command": command, "returncode": process.returncode,
                    "stdout": stdout_file, "stderr": stderr_file})
    if process.returncode and not allow_failure:
        raise RuntimeError("Tool failed: " + label + "; see " + stderr_file)
    return process


def inspect(label, bundle_data, tools, output, commands):
    bundle, binary, info, plist = bundle_data
    raw = {}
    for suffix, name, arguments in (
        ("file", "file", (binary,)),
        ("architectures", "lipo", ("-archs", binary)),
        ("header", "otool", ("-hv", binary)),
        ("load_commands", "otool", ("-l", binary)),
        ("all_symbols", "nm", ("-a", binary)),
        ("imports", "nm", ("-u", binary)),
        ("exports", "nm", ("-gU", binary)),
    ):
        raw[suffix] = run_tool(tools, output, commands, label + "." + suffix,
                               name, *arguments).stdout
    verify = run_tool(tools, output, commands, label + ".codesign_verify", "codesign",
                      "--verify", "--strict", "--verbose=2", bundle, allow_failure=True)
    run_tool(tools, output, commands, label + ".codesign_details", "codesign",
             "--display", "--verbose=4", bundle, allow_failure=True)
    rows = parse_symbols(raw["all_symbols"])
    exported_rows = parse_symbols(raw["exports"])
    if not rows or not exported_rows:
        raise ValueError("No parseable symbol inventory or global exports in " + label)
    imports = sorted({row["name"] for row in rows if row["kind"] == "U"})
    exports = sorted({row["name"] for row in exported_rows if row["kind"] != "U"})
    if not imports:
        raise ValueError("Unexpected empty kernel import inventory in " + label)
    defined = Counter(row["name"] for row in rows if row["kind"] != "U")
    return {
        "bundle": str(bundle), "binary": str(binary),
        "binary_sha256": sha256(binary), "binary_size": binary.stat().st_size,
        "info_plist_sha256": sha256(info),
        "architectures": raw["architectures"].split(),
        "mach_header": parse_mach_header(raw["header"]),
        "file_description": raw["file"].strip(),
        "load_commands": parse_load_commands(raw["load_commands"]),
        "imports": imports, "global_exports": exports,
        "all_defined_symbols_with_multiplicity": dict(sorted(defined.items())),
        "unique_function_spans": unique_function_spans(rows, text_section_end(raw["load_commands"])),
        "metadata": {key: plist.get(key) for key in (
            "CFBundleIdentifier", "CFBundleExecutable", "CFBundleVersion", "CFBundleShortVersionString",
            "OSBundleLibraries", "OSBundleRequired", "IOKitPersonalities")},
        "signature_verified": verify.returncode == 0,
    }


def compare(reference, candidate):
    rejected, review = [], []
    if candidate["architectures"] != reference["architectures"] or candidate["architectures"] != ["x86_64"]:
        rejected.append("Architecture mismatch; both inputs must be thin x86_64 NVRM images")
    if (not is_kext_bundle(reference["mach_header"])
            or not is_kext_bundle(candidate["mach_header"])):
        rejected.append("Both Mach-O inputs must have the kext bundle file type")
    for key, expected in (("CFBundleIdentifier", "com.nullmoth.NVRM"), ("CFBundleExecutable", "NVRM")):
        if reference["metadata"].get(key) != expected or candidate["metadata"].get(key) != expected:
            rejected.append("Unexpected or mismatched " + key)
    if candidate["metadata"].get("OSBundleLibraries") != reference["metadata"].get("OSBundleLibraries"):
        rejected.append("OSBundleLibraries dependency mismatch")
    if not isinstance(reference["metadata"].get("OSBundleLibraries"), dict):
        rejected.append("Reference has no usable OSBundleLibraries dictionary")
    new_imports = sorted(set(candidate["imports"]) - set(reference["imports"]))
    removed_imports = sorted(set(reference["imports"]) - set(candidate["imports"]))
    missing_exports = sorted(set(reference["global_exports"]) - set(candidate["global_exports"]))
    extra_exports = sorted(set(candidate["global_exports"]) - set(reference["global_exports"]))
    if new_imports:
        rejected.append("Candidate introduces kernel imports absent from the reference")
    if missing_exports:
        rejected.append("Candidate lacks global exports present in the reference")
    for label, image in (("Reference", reference), ("Candidate", candidate)):
        if not image["signature_verified"]:
            rejected.append(label + " signature verification failed")
    if removed_imports:
        review.append("Some reference kernel imports are no longer present")
    if extra_exports:
        review.append("Candidate has additional global exports")
    for key in ("CFBundleVersion", "CFBundleShortVersionString", "OSBundleRequired", "IOKitPersonalities"):
        if reference["metadata"].get(key) != candidate["metadata"].get(key):
            review.append("Metadata differs: " + key)
    if reference["load_commands"] != candidate["load_commands"]:
        review.append("Detailed load commands differ; inspect full raw reports, including UUID/signature/layout changes")
    old_symbols = Counter(reference["all_defined_symbols_with_multiplicity"])
    new_symbols = Counter(candidate["all_defined_symbols_with_multiplicity"])
    if old_symbols != new_symbols:
        review.append("Defined symbol inventory or multiplicity differs")
    old_spans, new_spans = reference["unique_function_spans"], candidate["unique_function_spans"]
    common = sorted(set(old_spans) & set(new_spans))
    changed_spans = {name: {"reference": old_spans[name], "candidate": new_spans[name]}
                     for name in common if old_spans[name] != new_spans[name]}
    only_old, only_new = sorted(set(old_spans) - set(new_spans)), sorted(set(new_spans) - set(old_spans))
    if changed_spans or only_old or only_new:
        review.append("Unique function symbol spans differ; these spans include padding and are not code-equivalence checks")
    if reference["binary_sha256"] != candidate["binary_sha256"]:
        review.append("Binary hashes differ; signing/compiler/layout differences require interpretation")
    return {
        "status": "rejected" if rejected else "review_required" if review else "static_checks_passed",
        "static_rejections": rejected, "differences_requiring_review": review,
        "new_imports": new_imports, "removed_imports": removed_imports,
        "missing_exports": missing_exports, "extra_exports": extra_exports,
        "defined_symbols_only_reference": dict(old_symbols - new_symbols),
        "defined_symbols_only_candidate": dict(new_symbols - old_symbols),
        "function_span_comparison": {"common_unique_names": len(common),
                                     "same_span_count": len(common) - len(changed_spans),
                                     "changed_spans": changed_spans,
                                     "only_reference": only_old, "only_candidate": only_new},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="Backed-up official NVRM.kext bundle")
    parser.add_argument("--candidate", type=Path, required=True, help="Staged candidate NVRM.kext bundle")
    parser.add_argument("--output", type=Path, required=True, help="New or empty owned comparison directory")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Binary inspection requires macOS tools; Linux can test the pure parsing/comparison functions only")
    if os.geteuid() == 0:
        parser.error("Run as an ordinary user; no administrator privileges are needed")
    try:
        reference = checked_bundle(args.reference)
        candidate = checked_bundle(args.candidate)
        if reference[0] == candidate[0] or reference[1].resolve() == candidate[1].resolve():
            raise ValueError("Reference and candidate must be separate bundles and binaries")
        tools = {name: shutil.which(name) for name in REQUIRED_TOOLS}
        if any(path is None for path in tools.values()):
            raise ValueError("Missing tools: " + ", ".join(name for name, path in tools.items() if path is None))
        output = prepare_output(args.output, [reference[0], candidate[0]])
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        parser.error(str(error))
    report = {"schema": 1, "limitation": LIMITATION, "tools": tools, "commands": []}
    try:
        report["reference"] = inspect("reference", reference, tools, output, report["commands"])
        report["candidate"] = inspect("candidate", candidate, tools, output, report["commands"])
        report["comparison"] = compare(report["reference"], report["candidate"])
        result = report["comparison"]
        text = ["NVRM static comparison", "Status: " + result["status"], "", LIMITATION, ""]
        for label in ("reference", "candidate"):
            image = report[label]
            text.extend([label + ": " + image["bundle"], "  SHA-256: " + image["binary_sha256"],
                         "  imports: " + str(len(image["imports"])) + "; global exports: " + str(len(image["global_exports"]))])
        for heading, key in (("Static rejections", "static_rejections"),
                             ("Differences requiring human review", "differences_requiring_review")):
            text.extend(["", heading + ":"] + ["- " + value for value in result[key]])
            if not result[key]:
                text.append("- None detected by these checks")
        text.extend(["", "Full inventories, metadata, detailed commands, and function spans are in comparison.json.",
                     "Raw stdout/stderr are retained separately for every tool invocation."])
        exit_code = 1 if result["static_rejections"] else 0
    except (OSError, ValueError, RuntimeError) as error:
        report["operational_error"] = str(error)
        text = ["NVRM static comparison failed: " + str(error), "", LIMITATION]
        exit_code = 2
    (output / "comparison.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    summary = "\n".join(text) + "\n"
    (output / "comparison.txt").write_text(summary)
    print(summary, end="")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
