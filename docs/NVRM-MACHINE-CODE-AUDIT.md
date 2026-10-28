# NVRM 615 machine-code audit and KAPI compiler control

This is a static, read-only follow-up to the [isolated reference build](NVRM-615-REFERENCE-BUILD.md). It compares existing Mach-O inputs and writes reports to a new output directory. The optional compiler control creates two standalone test objects in its own new directory. Neither tool installs, loads, signs, relinks or replaces NVRM, changes firmware/EFI/NVRAM/SIP/Kernel Collections, applies display patches, or changes baseline compiler flags.

**The actual official-versus-reference-004 NVRM machine-code comparison has not been executed in this Linux workspace.** Synthetic and cross-toolchain test results do not establish code identity of the user's macOS binaries. Preserve `reference-004`, all earlier reference builds and the official backup unchanged.

## Reported baseline evidence

The user reports a successful full reference build on an ASUS ROG Maximus Z690 Extreme / i9-14900KF / Tahoe 26.7.1 / x86_64, using NVIDIA 615.78.08 and the pinned NullMoth 2.5.4 sources. This is stronger evidence than the earlier Linux Makefile checks, but it is not a newly executed hardware test in this workspace.

| Reported measurement | Result |
|---|---|
| RM archive | 1100 validated x86_64 Mach-O objects |
| NVKMS archive | 225 validated x86_64 Mach-O objects |
| XNU glue, final NVRM link, ad-hoc signing | Successful |
| Existing static comparison | `review_required`, no static rejections |
| Kernel imports | 477, identical inventories |
| Global exports | 18669, identical inventories |
| All recorded symbol names and multiplicities | Identical |
| Common uniquely named text-symbol intervals | 30884 |
| Equal interval lengths | 30882 |
| Different interval lengths | `_nvrm_kapi_mem_handle`, `_nvrm_kapi_dev_handles` |

A text-symbol interval is not a guaranteed function size: the distance to the next distinct text symbol can include alignment padding, an unnamed fragment or other code/data. Equal lengths, symbol names and imports/exports do not establish equal instructions, function prototypes, structure layouts or overall ABI compatibility.

The backed-up official binary was previously reported as SHA-256 `4591c5bd732838417168b366380c9a0eefdd4ad940d3eed3bfff22f6ade2e0f7`. Use the hashes recorded by each new audit to identify its actual inputs; do not replace the backup or alter either binary to improve the result.

## Run the complete code audit on macOS

From the updated repository root:

```bash
python3 tools/nvrm_code_audit.py \
  --reference "$HOME/nvrm-reference/2.5.4/NVRM.kext/Contents/MacOS/NVRM" \
  --candidate "$HOME/nvrm-builds/reference-004/NVRM.kext/Contents/MacOS/NVRM" \
  --output "$HOME/nvrm-audits/code-001"
```

The output directory must be new and separate from both inputs and the existing build tree. Choose a different unused name for another run. The tool discovers `llvm-objdump` and `nm` through the existing Apple developer tools and executable search path. If necessary, select already installed compatible tools explicitly:

```bash
python3 tools/nvrm_code_audit.py \
  --reference "$HOME/nvrm-reference/2.5.4/NVRM.kext/Contents/MacOS/NVRM" \
  --candidate "$HOME/nvrm-builds/reference-004/NVRM.kext/Contents/MacOS/NVRM" \
  --output "$HOME/nvrm-audits/code-002" \
  --llvm-objdump /absolute/path/to/llvm-objdump \
  --nm /absolute/path/to/nm
```

No tool installation or developer-directory change is automatic. All tools are invoked through argument lists; input binaries are never executed. An operational failure must be resolved before interpreting a report as a completed audit.

The output contains a compact `summary.txt`, full machine-readable `audit.json`, and raw tool evidence. Exit 0 means that a report was completed, not that all functions matched; exit 2 is an operational failure. `counts` separates examined symbols, `identical`, `different` and `not_comparable` entries. `functions` contains every selected/union identity, evidence hashes, reference mappings and bounded disassembly examples. `address_dependent_summary` separates normalized references and padding/layout effects. `metadata_comparison` records UUID, signature-blob hashes, symbol-binding inventories, segment/section changes and available bundle metadata. Signature blobs are not re-signed or approved by this tool. Retain tool paths/versions, input hashes, command arguments, stderr and the full report together. The complete report, rather than the displayed examples alone, is the record of coverage and unresolved cases.

## Comparison rules and interpretation

The tool uses the Mach-O symbol/section information and decoded instructions together. It attempts all identifiable text functions, and reports missing, duplicate, aliased or otherwise uncertain identities explicitly rather than silently limiting the denominator to successful matches. Where available, function-start metadata supplies additional boundary evidence. Symbol intervals remain an explicitly documented fallback, not a promise that all intervals are complete source-language functions.

The conservative classification distinguishes:

- Instruction sequences judged identical under the supported, validated address mappings.
- Actual instruction differences, including register selection, opcode/instruction kind, memory access, structure displacement or relevant constant changes.
- Address-dependent differences that can be shown to be equivalent references.
- Cases that cannot be compared safely because identity, boundaries, decoding, relocation or reference semantics are unresolved.
- Separately identified trailing padding/layout differences.

Counts must be read with their coverage and reasons. Missing symbols, ambiguous aliases, undecodable bytes, unsupported fixups and unresolved address references are not identical functions. Unnamed or unidentifiable executable code remains outside a claim about named-function coverage. A report with no unexplained differences but incomplete coverage is not proof of complete NVRM code identity.

The following are not valid shortcuts:

- Dropping all immediates, memory displacements or RIP-relative operands.
- Treating equal function names or address-span lengths as instruction equality.
- Mapping an anonymous data address to the nearest symbol without proof.
- Ignoring all trailing bytes after a return regardless of branch targets.
- Removing `endbr64`, frame-pointer prologues/epilogues, `add` instructions or zero-filled bytes as generic padding.

Direct branch targets and RIP-relative displacements are normalized only when the tool can validate equivalent destinations and preserve all other instruction/operand bytes. Relevant symbol addends and structure offsets remain strict. Symbol definition/type, visibility, weak-reference/definition and description flags are retained; an unresolved name is not silently equated with an image-defined symbol. Data-reference normalization requires adequate target-region identity; an equal symbol name is not permission to ignore changed initializers or unresolved fixups. Unsupported cases remain uncertain. A reference/data-evidence difference can be reported even when the raw instruction bytes match; its category is separate from `instruction_or_encoding_difference`. This is not a blanket claim that different code-generation or binding representations imply different program behavior.

Trailing architectural NOPs can be reported separately only under the tool's terminal/control-flow checks. Known direct branch, RIP/LEA and data-relocation targets are checked against the candidate suffix; unresolved in-image address-bearing metadata can disable trimming. `hlt` and `int3` are resumable and do not establish an unreachable suffix. Padding trimming does not establish that symbol-to-symbol spans are exact compiler function extents. Ambiguous tails and references into apparent padding must not become safe identity claims.

### Classic Mach-O relocation coordinates

Section relocations use section-relative locations. Dynamic relocation tables in `LC_DYSYMTAB` need separate handling; ignoring them can misclassify identical placeholder bytes with different targets.

Apple's published dyld code explicitly treats classic `MH_KEXT_BUNDLE` relocation locations as relative to the text load address: [localRelocBaseAddress and externalRelocBaseAddress](https://github.com/apple-oss-distributions/dyld/blob/fd8d0c4d52320ebf64db34f3cb280310d905c5ae/common/MachOAnalyzer.cpp#L1125). The generic x86_64 first-writable-segment rule must not be substituted for this kext-specific case. The implementation supports only mappings it can validate against the parsed image; unsupported layouts, negative/ambiguous coordinates, paired relocations or dynamic fixup encodings remain explicit uncertainty where not implemented. This is a source-based format interpretation, not a newly performed macOS loader test.

## Isolated KAPI compiler reproduction

The user reports that recompiling only `nvrm_kapi_fields.c` with the following two controlled changes produced the same visible instructions as the official 2.5.4 helpers. The recorded command contains an inherited `-fno-omit-frame-pointer` followed by `-fomit-frame-pointer`; the latter is effective. The probe accepts exactly that ordering or the earlier single `-fomit-frame-pointer` form. It rejects reversed/duplicated frame-pointer options, leaf-frame-pointer overrides and additional CET options. The inherited earlier option is retained unchanged in both test commands:

| Recorded NVKMS-template option | Isolated variant |
|---|---|
| `-fomit-frame-pointer` | `-fno-omit-frame-pointer` |
| `-fcf-protection=branch` | `-fcf-protection=none` |

The successful `reference-004` build was not modified. This supports a compiler-option explanation for the two helper differences. It does not establish the exact compiler/toolchain used for every official object. Frame-pointer and CET instruction changes are actual code differences in the original baseline and must remain visible in its audit.

To reproduce the experiment automatically without rebuilding NVRM:

```bash
python3 tools/nvrm_kapi_compiler_probe.py \
  --build "$HOME/nvrm-builds/reference-004" \
  --reference "$HOME/nvrm-reference/2.5.4/NVRM.kext/Contents/MacOS/NVRM" \
  --output "$HOME/nvrm-audits/kapi-flags-001"
```

An already installed `llvm-objdump` can be selected with `--llvm-objdump /absolute/path/to/llvm-objdump`. The probe requires a new output directory outside the existing build and reference. It reads the unique `glue-nvrm_kapi_fields` command from `reference-004/commands.jsonl`, checks the recorded inputs, and recompiles using that original working directory and compiler. Only output/dependency paths are redirected for the control object. The variant changes those same output paths plus exactly the two flags above. No shell command from a log is executed.

The probe creates `control.o`, `variant.o`, separate compile stdout/stderr files, its own `commands.jsonl`, `probe.json`, and before/after input-integrity manifests. It audits the two helpers in:

1. The control object versus the recorded original glue object.
2. The control object versus the official NVRM binary.
3. The variant object versus the official NVRM binary.

Each comparison has its own `audit.json` and `summary.txt`. A verified reproduction requires both control helpers to match the recorded object, both baseline helpers to differ from the official instructions, and both variant helpers to match the official instructions under the audit's supported rules. Uncertain comparisons do not establish reproduction. A changed compiler or input must be investigated rather than silently accepted.

This reproduces a compiler cause; it does not change baseline options, link a new NVRM, sign anything or authorize deployment. The actual macOS reproduction with these new helpers remains to be run. Linux fake-compiler and simulated-audit regressions demonstrate control flow and isolation only.

## Timer omission identified after reference-004

The user subsequently reports an actual machine-code difference in `_tmrEventDestroyOSTimer_OSTIMER`: official 2.5.4 clears the stored callback after `osDestroyNanoTimer`, while `reference-004` does not. The pinned 615 source confirms that it lacks `pTmrEvent->super.pOSTmrCBdata = NULL;`. The original NullMoth `610-fsp-timer-double-destroy.patch` contains that correction as well as an FSP hunk already upstream in 615. The updated [reference build](NVRM-615-REFERENCE-BUILD.md) carries only the missing one-line timer statement, preserves FSP, and requires a fresh `reference-005`. It does not change any old build, baseline compiler flags or diagnostic patch.

This new source evidence supersedes any assumption that the two helper-size differences were the only code differences. The complete corrected NVRM must still be built and compared on macOS. Source-level and fixture checks cannot establish its final machine instructions or runtime behavior.

## KAPI and component compatibility: evidence and remaining limits

The source helper [nvrm_kapi_fields.c](https://github.com/backdash756/nvidia-macos-driver/blob/46779055f68208baa5ddfb9b2e9c77ed028e1d59/kexts/NVRM/nvrm_kapi_fields.c#L9) reads four fields, with NULL checks. The reported disassemblies of the official and rebuilt helpers show:

| Field | Observed offset |
|---|---|
| `NvKmsKapiMemory.hRmHandle` | `0x00` |
| `NvKmsKapiDevice.hRmClient` | `0x10` |
| `NvKmsKapiDevice.hRmDevice` | `0x14` |
| `NvKmsKapiDevice.hRmSubDevice` | `0x18` |

These observations are consistent with the pinned [615 internal definitions](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/1a82fbfda8f321b056eb3568850f9316c5e465c5/src/nvidia-modeset/kapi/include/nvkms-kapi-internal.h#L81): the device begins with `gpuId`, an aligned semaphore pointer, and the three handles; memory starts with `hRmHandle`. This confirms only those observed accesses, not every field, total size, alignment, nested layout or function signature.

The two helpers matter beyond display enumeration: [nvrm_gpuva.cpp](https://github.com/backdash756/nvidia-macos-driver/blob/46779055f68208baa5ddfb9b2e9c77ed028e1d59/kexts/NVRM/nvrm_gpuva.cpp#L40) resolves RM client/device handles, and [nvrm_surfshare.cpp](https://github.com/backdash756/nvidia-macos-driver/blob/46779055f68208baa5ddfb9b2e9c77ed028e1d59/kexts/NVRM/nvrm_surfshare.cpp#L17) uses both helpers for sharing/page-off operations.

The wider KAPI boundary remains significant:

- [os-nvkms-xnu.cpp](https://github.com/backdash756/nvidia-macos-driver/blob/46779055f68208baa5ddfb9b2e9c77ed028e1d59/kexts/NVRM/os-nvkms-xnu.cpp#L217) fills a static `NvKmsKapiFunctionsTable` and returns its pointer. [NVRM.cpp](https://github.com/backdash756/nvidia-macos-driver/blob/46779055f68208baa5ddfb9b2e9c77ed028e1d59/kexts/NVRM/NVRM.cpp#L78) publishes it as `nvkms-kapi`.
- [NVRMFB](https://github.com/backdash756/nvidia-macos-driver/blob/46779055f68208baa5ddfb9b2e9c77ed028e1d59/kexts/NVRM/fb/nvrm-fb.cpp#L1040) casts that property directly and calls table members with shared request/reply structures. The [public table](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/1a82fbfda8f321b056eb3568850f9316c5e465c5/src/nvidia-modeset/kapi/interface/nvkms-kapi.h#L647) contains a version string but no complete size/layout fingerprint negotiated by this path.
- NVAccel also accesses this table for CRC queries and uses the VRAM/GPUVA/flip/surfshare interfaces. Their explicit version values help reject known interface mismatches but do not validate all C structure padding or function prototypes.
- Identical kernel imports and global exports establish name availability, not parameter types, calling conventions, table-slot offsets or request/reply layouts. The unchanged 2.5.4 consumer components must remain part of the reference compatibility assessment.

Other artifact differences require separate interpretation:

| Difference | Interpretation and check |
|---|---|
| Segment/section addresses, sizes, alignment or protection | Can reflect layout/padding or data changes. Inspect the existing detailed Mach-O comparison; instruction equality does not validate every data section, vtable, initializer, relocation or protection. |
| UUID | Identifies a linked artifact; a different UUID alone does not prove a code change. Preserve it in reports rather than rewriting it. |
| Ad-hoc signature | Verification establishes integrity of the signed artifact, not loader approval, release provenance or hardware compatibility. The code audit does not resign inputs. |
| Source KMOD/plist `0.1` versus official `2.5.4` | A real metadata distinction. Do not relabel or alter the reference artifacts to hide it; a future installation-ready package needs separately reviewed consistent metadata. |
| Other NullMoth 2.5.4 components | Keep their known-working binaries/headers/ABI versions fixed. This NVRM-only audit does not independently verify their loaded state or all producer/consumer layouts. |

## Regression tests and execution status

The initial audit Linux validation passed **153 tests**: 85 existing build/preflight/archive regressions and 68 new audit/probe checks (16 Mach-O reader, 10 engine/output guards, 31 real LLVM fixture comparisons, 11 compiler-probe fixtures).

```bash
python3 tools/test_nvrm_audit_macho.py
python3 tools/test_nvrm_code_audit.py
python3 tools/test_nvrm_kapi_compiler_probe.py
python3 tools/test_nvrm_audit_llvm.py \
  --clang /absolute/path/to/clang \
  --llvm-objdump /absolute/path/to/llvm-objdump \
  --nm /absolute/path/to/llvm-nm
```

The subsequent timer/KAPI update passed **172 tests** across these suites and the build regressions, including 15 compiler-probe fixtures and 8 new timer C-harness checks. The unchanged YUV420 diagnostic harness passed separately in temporary trees. See the [reference build validation](NVRM-615-REFERENCE-BUILD.md#validation-and-the-next-diagnostic-step) for the current breakdown and its limits.

The LLVM tests use small cross-generated x86_64 Mach-O objects and actual LLVM disassembly. They detect changed registers, structure displacements, immediates, frame-pointer/CET instructions, relocation targets/weak bindings, aliases and references into apparent padding. The fixtures are never executed or loaded. Reader/engine tests use synthetic metadata, and compiler-probe control-flow tests use a fake compiler and simulated audit reports. None of these results establishes code equality of the actual NVRM binaries. The real macOS full audit and compiler reproduction remain open.

## Recommended next checks

Before integrating the existing YUV420 diagnostic into a new isolated build:

1. Run the full code audit on the actual two NVRM binaries. Review every actual difference and uncertainty, including coverage gaps; retain the hashes and full reports.
2. Run the isolated compiler control. Verify that it reproduces the two-helper explanation without changing `reference-004` or its compiler records. Keep the baseline flags unchanged.
3. Retain the existing artifact comparison and inspect section/data/relocation differences alongside code results. Confirm that KAPI headers and the intended 2.5.4 consumers remain the pinned set; use additional read-only `sizeof`/`alignof`/`offsetof` or consumer-disassembly evidence if an unexplained boundary difference remains.
4. Create a fresh diagnostic output from the same established baseline and toolchain only after reviewing those results. Pair the existing NVIDIA diagnostic patch with its matching XNU hook, and compare the resulting artifact against the unchanged baseline. No such integration is performed by these audit tools.

Perfect binary identity is not asserted or required to explain a compiler-generated helper difference. Conversely, an unexplained code or ABI difference must not be dismissed because symbol counts match. Building a diagnostic artifact and deciding to load it are separate steps. Consistent release metadata, a qualified separate System/Data test volume, selective deployment/rollback and the previously documented global NVRAM cautions belong to any later hardware test; no installation or boot commands are provided here.
