# Isolated YUV420 diag-003 metadata compatibility build

This is an **experimental static compatibility artifact**, not an official NullMoth release and not runtime or hardware approval. All existing source checkouts, `reference-005`, `yuv420-diag-001`, `yuv420-diag-002`, backups and installed drivers remain unchanged. No installer, driver loading, EFI/OpenCore/SIP/NVRAM operation or Kernel Collection command is included.

The user reports that the actual macOS `yuv420-diag-002` build succeeded: 1338 objects, 1335 byte-identical to `reference-005`, exactly the three expected diagnostic changes and no static rejection. A subsequent full comparison with the official 2.5.4 plist reportedly found only `CFBundleVersion` and `CFBundleShortVersionString` at source version `0.1`. These are user-reported macOS results. The actual binaries are not present here; the new diag-003 Darwin build and its real binary checks have not been executed in this Linux workspace.

## Version and provenance boundaries

| Input | Pinned value |
|---|---|
| Experiment branch | `experiment/nvrm-615-yuv420-ab` |
| Metadata parent | `e82c6637727af6e839ef9856fc9a4820b92d4fa4` |
| NullMoth source | 2.5.4, `46779055f68208baa5ddfb9b2e9c77ed028e1d59` |
| NVIDIA source | 615.78.08, `1a82fbfda8f321b056eb3568850f9316c5e465c5` |
| Source metadata retained globally | `0.1` |
| Isolated diag-003 technical metadata | `2.5.4` |

The [metadata proof](../build/nvrm615meta/proof.json) embeds the complete unchanged [diagnostic proof](../build/nvrm615diag/proof.json) and verifies it against the parent Git object. All five baseline patches, the original YUV420 A/B implementation, XNU hook, 4K30 trigger/overview follow-up, headers and ABI definitions are retained. The metadata patch is appended after those eight patch steps.

The new [254-metadata.patch](../build/nvrm615meta/patches/254-metadata.patch) has SHA-256 `89a2318a62e541a1892e0eaca9f7cb58f3451061a702857107491be60a77f07d`. It applies only to a fresh, staged NullMoth snapshot:

| Staged file | Exact change | Before SHA-256 | After SHA-256 |
|---|---|---|---|
| `nullmoth/kexts/NVRM/Info.plist` | Only the two bundle-version strings, `0.1` to `2.5.4` | `934914c01b15ad266fa4ff2f62e03671e2bc6849660564710bbebfc429ac91b6` | `a1aab09b29d273a491b63458e0f1871eca99ad99d5671e74b7c0e2df23b0fe87` |
| `nullmoth/kexts/NVRM/NVRM.cpp` | Only the version argument in `KMOD_EXPLICIT_DECL(com.nullmoth.NVRM, ...)` | `bb16898582cb3cca2cffad4b85f6f8a75a1eb2bb5230ab5d9def1695ed31f9ff` | `fa7dd5bb17a7b8997f736ac6fb151a627bf80dc8a8894917969ea5fd81980ab1` |

Preparation derives these exact permitted output bytes from the pinned Git inputs, verifies patch/source hashes, checks the declared two-file scope and uses strict `git apply --check` followed by `git apply`. No fuzz, whitespace relaxation or idempotent skip is used. A second application or changed preimage is rejected. Plist formatting and all other source bytes are preserved.

## Separate entry points and manifests

The ordinary [reference builder](NVRM-615-REFERENCE-BUILD.md) and [diag-001/002 workflow](NVRM-615-YUV420-EXPERIMENT.md) remain separate. Their metadata stays `0.1`; their existing comparison profile is not expanded. The common compiler/link recipe records `2.5.4` only when the metadata runner explicitly requests that version with its strict format-3 loader. Adding a `metadata_version` field to an ordinary reference manifest cannot change its default artifact-version report.

The metadata runner is `build/nvrm615meta/build.py`. It first prepares the complete unchanged diagnostic in a new directory, preserves its manifest, then applies the metadata patch. It writes:

| File | Purpose |
|---|---|
| `baseline-inputs.json` | Original format-1 reference-source basis before diagnostics |
| `diagnostic-inputs.json` | Original format-2 diagnostic source manifest before metadata changes |
| `diagnostic-pre-metadata-state.json` | Preserved diagnostic-preparation state |
| `inputs.json` | New format 3, `build_kind=yuv420-ab-metadata-experiment`, `diagnostic_enabled=true`, `metadata_enabled=true`, `experimental=true`, metadata version `2.5.4` |
| `metadata-manifest.json` | Immutable metadata identity, complete proof/patch order and manifest hashes; `runtime_approved=false`, `hardware_approved=false` |
| `metadata-state.json` | Preparation/build outcome; always experimental and without runtime/hardware approval |
| `metadata-check.json` | Post-link structured KMOD result and complete official-plist comparison |
| `artifacts.json`, `build-state.json` | Ordinary build evidence, with the explicit diagnostic/version values |

The primary manifest binds the preserved reference/diagnostic manifests and the new metadata manifest by hash, plus all three recipe/source inventories. A failed metadata preparation leaves no consumable reference or diagnostic manifest at `inputs.json`. Earlier loaders reject format 3. A build output remains single-attempt; preserve failed attempts and prepare a new directory rather than modifying an old build.

## macOS commands for a fresh diag-003

Run from the updated experiment branch's repository root as an ordinary user. Keep the clean pinned NVIDIA checkout and the same CLT/SDK/compiler used for `reference-005`. No complete Xcode installation, tool download or source-history fetch is performed by the helpers.

```bash
git switch experiment/nvrm-615-yuv420-ab
git pull --ff-only origin experiment/nvrm-615-yuv420-ab

python3 build/nvrm615meta/build.py prepare \
  --vendor "$HOME/nvrm-inputs/open-gpu-615" \
  --output "$HOME/nvrm-builds/yuv420-diag-003"

python3 build/nvrm615meta/build.py build \
  --output "$HOME/nvrm-builds/yuv420-diag-003" \
  --reference "$HOME/nvrm-reference/2.5.4/NVRM.kext" \
  --baseline-build "$HOME/nvrm-builds/reference-005" \
  --comparison-output "$HOME/nvrm-audits/yuv420-diag-003-vs-reference-005" \
  --jobs 4
```

Both output paths must be new. Append `--sdk /absolute/path/to/MacOSX.sdk` only to select the same existing SDK explicitly. An installed LLVM disassembler can be selected with `--llvm-objdump /absolute/path/to/llvm-objdump`; no installation occurs automatically.

The builder compiles only its new staged tree, links and ad-hoc signs the new candidate, runs the existing official structural comparison, then verifies the actual linked KMOD record and the complete typed plist against the official backup. It finally requires the separate metadata object/archive/binary comparison against `reference-005`. Completion records `built_static_review_required`, never runtime or hardware approval.

For another full static comparison without rebuilding, choose another new report directory:

```bash
python3 build/nvrm615meta/compare.py \
  --baseline-build "$HOME/nvrm-builds/reference-005" \
  --candidate-build "$HOME/nvrm-builds/yuv420-diag-003" \
  --output "$HOME/nvrm-audits/yuv420-diag-003-vs-reference-005-repeat-001"
```

## Strict four-object and plist comparison

Only the explicit metadata comparator selects the four-object policy:

1. `ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-kapi.o`
2. `ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-modepool.o`
3. `glue/os-nvkms-xnu.o`
4. `glue/NVRM.o`

All four must differ from `reference-005`; every other object must remain byte-identical. Missing/extra objects, any fifth change, changed compiler flags or mismatched SDK/tool evidence are errors. RM archive object payloads remain identical; only the original two NVKMS archive members may differ. Archive members are additionally bound to the standalone object bytes. Container/index/signature/UUID/layout differences are reported separately.

`NVRM.kext/Contents/Info.plist` is the only additional expected bundle-metadata change. The actual bundle plist must equal the staged source bytes. A full recursive, type-strict plist comparison permits only the two named version values changing from `0.1` to `2.5.4`; dependencies, personalities, identifiers, booleans, arrays, added keys and all other values remain strict. The source CPP and plist bytes must also equal the exact minimal transformations, so extra C++ statements or plist reformatting cannot be hidden by updated hashes.

The shared comparator's default remains the existing three-object format-2 profile. Only the new metadata entry point supplies its explicit format-3 policy. There is no implicit auto-detection or global fourth-object exception. Metadata and old diagnostic manifests are not interchangeable.

## Actual embedded KMOD version verification

[kmod.py](../build/nvrm615meta/kmod.py) uses the existing strict thin x86_64 Mach-O parser. It finds exactly one defined external `_kmod_info` symbol, maps its virtual address through the declared section to stored file bytes, and requires the complete supported record to fit in one readable, non-executable data section.

The checked definition is Apple's version-1 `kmod_info_64_v1_t` in [XNU kmod.h](https://github.com/apple-oss-distributions/xnu/blob/8d741a5de7ff4191bf97d57b9f54c2f6d4a15585/osfmk/mach/kmod.h), header SHA-256 `a8fcc1de6620c9484ff96f699d20d37e2b1504615f2acfc547e8f0d14198a2f7`. Its **pack(4)** layout is 196 bytes: `info_version` at offset 8, name at 16 and version at 80, with 64-byte name/version arrays. Host-native struct alignment must not be substituted.

The reader requires `info_version=1`, name `com.nullmoth.NVRM` and the expected exact version, with NUL termination and zero padding. Ambiguous/missing symbols, truncated records, executable/zero-fill storage, overlapping symbol extents, unsupported/unmapped fixups or relocations into protected version/name fields are errors. Encoded pointer fields and supported pointer relocations are recorded without claiming their runtime correctness. The input hash is checked again after reading.

The comparison requires the actual baseline record to remain `0.1` and the candidate record to be `2.5.4`. A matching string elsewhere in the binary cannot satisfy the check. The candidate can also be inspected read-only on its own:

```bash
python3 build/nvrm615meta/kmod.py \
  --binary "$HOME/nvrm-builds/yuv420-diag-003/NVRM.kext/Contents/MacOS/NVRM" \
  --expected-version 2.5.4
```

## Validation and remaining limits

The final Linux run passed **281 unit/integration tests**: all 216 existing reference/diagnostic/audit/probe checks, 24 metadata build/integration checks, 21 metadata comparison checks and 20 KMOD record-reader checks. The 20 current diagnostic scenarios passed on the prepared ordinary and metadata source trees as well as the standalone vendor test path; the 12 historical original-only scenarios also passed. Python 3.8 grammar, local documentation links and Git whitespace checks passed.

The test suites cover exact metadata source deltas, full plist keys/types, source and recipe hashes, duplicate/tamper rejection, manifest-role separation, unchanged diagnostic behavior, explicit object profiles and the actual KMOD record reader with negative decoy-string cases. The source integration runs against the pinned NVIDIA tree in temporary directories. The existing 20-scenario diagnostic harness is reused on the prepared metadata sources.

```bash
python3 tools/test_nvrm615meta_build.py --vendor /absolute/path/to/open-gpu-615
python3 tools/test_nvrm615meta_compare.py
python3 tools/test_nvrm615meta_kmod.py
```

Synthetic Mach-O/archive fixtures and simulated macOS tool reports are not a real NVRM link or binary comparison. The actual diag-003 macOS compilation, signing, four-object result, official plist equality and KMOD version result remain to be obtained from the commands above. Preserve `comparison.json`, `summary.txt`, child structural/instruction reports, `metadata-check.json` and build logs together.

Technical version agreement does not prove KAPI structure layouts, ABI compatibility with other components, correctness of all linked data references, runtime bindings, loader acceptance, display behavior or release provenance. The unchanged YUV420 diagnostic still has its documented query/logging/claim/cache/PM limits. Its source, trigger, validation parameters and modeset behavior are not altered by this metadata patch.

Do not install or load this artifact based on a static pass. To stop using it, retain its evidence and return to the earlier workflow; there is nothing to uninstall. Existing builds must not be edited or relabeled to emulate diag-003.
