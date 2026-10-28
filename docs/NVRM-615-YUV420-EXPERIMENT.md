# Isolated NVIDIA 615 YUV420 A/B experiment

This branch prepares an **experimental diagnostic artifact only**. It neither installs nor loads a driver and changes no EFI, OpenCore, NVRAM, SIP or Kernel Collection state. A hardware test requires a separate decision. Preserve `reference-005`, `yuv420-diag-001`, all earlier builds and the official 2.5.4 backup unchanged.

The user reports a successful local `reference-005` build. Its actual binaries and a Darwin build environment are not available in this Linux workspace. Source preparation and regression fixtures here are not a completed macOS build, actual NVRM object comparison or hardware result.

## Pinned inputs and reused implementation

| Input | Revision |
|---|---|
| Experiment branch | `experiment/nvrm-615-yuv420-ab` |
| Branch starting point / historical reference recipe | `2e9f435637ff44164ba0355ae8904f15935c2f5a` |
| NullMoth 2.5.4 source | `46779055f68208baa5ddfb9b2e9c77ed028e1d59` |
| NVIDIA 615.78.08 | `1a82fbfda8f321b056eb3568850f9316c5e465c5` |
| Existing paired diagnostic origin | `ff37f3812fe170610885081f3a9cb223eb580938` |
| Trigger follow-up parent | `61c581ed679ebec7be9685eecf0bad991671da82` |

The branch starts from the reference recipe, not the old diagnostic branch. It retains the existing [diagnostic patch and test](YUV420-DIAGNOSE.md). A separately recorded follow-up broadens only the diagnostic trigger and adds a bounded 4K30 overview; it does not overwrite or relabel the original patch. The baseline NullMoth snapshot is still extracted from the 2.5.4 Git objects. Only the original XNU hook is transplanted into that snapshot; its resulting entire file must equal the paired original.

[proof.json](../build/nvrm615diag/proof.json) format 2 records the full provenance, SHA-256 values, exact source transitions, historical reference recipe hashes and permitted object changes. The original NVIDIA diagnostic patch remains:

- Path: `kexts/NVRM/patches/615-yuv420-diagnostic.patch`.
- SHA-256: `ed9fd700dd6a83b01d552b9148a2dc983e28eb17a8a2379c9b062c0ba13af0c0`.
- The separately extracted `patches/xnu-yuv420-hook.patch` has SHA-256 `03932ec7af95ba53901d8dca6240bf6af0bc0bebe34caac53b5c038ea6a8def5`.

## Separate trigger correction for yuv420-diag-002

The original trigger required a normal 3840×2160 / 59–61 Hz candidate. An affected output exposing only 4K30 could therefore never start the additional diagnostic. The follow-up `patches/615-yuv420-trigger-overview.patch` removes those trigger timing checks and retains the existing successful-IOCTL path, `!reply.end` and unchanged selected-display claim. A normal reply with `valid=false` is still an enumerated candidate; it does not have to pass mode validation before starting the diagnostic. Normal query failures, end-of-list responses, missing opt-in and unselected IDs do not claim or start it.

The follow-up changes only `kapi/src/nvkms-kapi.c` after the original pair. It has SHA-256 `9470404eaa6fcc338c32efb82606010af14fe79aa2e92c7c0826232bccf95aa0`, with exact KAPI transition:

- Original diagnostic output: `5fda13a23642a936566ac63dc6e4f32f46481124b8c7e138e0efa31d5872a864`.
- After this follow-up: `d25439b4c3ea42a2e739227b34c1ce12d94c4c6441680bbd7a0774c9c07e1481`.

The proof preserves `historical_source_changes` verbatim from commit `61c581e`, verifies the historical original patch/hook/allowlist/recipe fields against that Git object, and records the additional patch and its source transition separately. Only the final KAPI hash in the baseline-to-experiment delta advances; the modepool and XNU hashes stay unchanged. A second application or a wrong preimage is rejected. Existing `yuv420-diag-001` manifests and sources are never upgraded in place; prepare `yuv420-diag-002` instead.

For 3840×2160 candidates in the 29–31 Hz range, each pass logs at most **eight** one-line `overview=4K30` records, including source, validity, pixel clock, refresh and the internal YUV420 enum. The overview does not add validation queries, info-buffer expansion or KAPI conversions for 4K30. The existing detailed 4K60 logging remains unchanged. Pass summaries retain `matches` for 4K60 and append `matches30` (all observed 4K30 candidates) and `logged30` (at most eight). For example, `matches=0 matches30=2 logged30=2` is useful evidence even without a 4K60 candidate. `matches30=512 logged30=8` still accompanies `INCOMPLETE` when the query bound is reached; it is not a complete enumeration.

## Preparation order and scope

Every preparation materializes a fresh, independent source tree. No objects or sources from an existing build are copied into it.

1. Apply all five [baseline patches](../build/nvrm615/patches/README.md) in their established order: display completion, setmode reason, viewport scaling, backlight logging and timer callback clearing.
2. Verify all baseline source hashes and the unchanged FSP hash.
3. Apply the existing NVIDIA diagnostic patch strictly, after the baseline, to NVKMS only.
4. Apply the matching XNU hook strictly to the staged NullMoth 2.5.4 file.
5. Verify the original pair against its historical hashes, then apply the trigger/overview follow-up strictly to its exact KAPI preimage.
6. Verify every exact before/after hash and the complete source inventory: the diagnostic adds changes to exactly three files relative to the full baseline.

No fuzz, relaxed whitespace application, double application or patch-helper fallback is used. The timer result remains SHA-256 `da908b3245f9be48f0787eb48fd4d70b721f6d48cd55e99bb99421bb370379f2`, and FSP remains `84ba2a49c08aa490aad40fe737b42d135e3bf11969e76ca257a8e5f425c51e54`.

| Additional source change relative to reference-005 | Only permitted changed standalone object |
|---|---|
| `ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c` | `ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-kapi.o` |
| `ogkm/src/nvidia-modeset/src/nvkms-modepool.c` | `ogkm/src/nvidia-modeset/_out/Darwin_x86_64/nvkms-modepool.o` |
| `nullmoth/kexts/NVRM/os-nvkms-xnu.cpp` | `glue/os-nvkms-xnu.o` |

Final source SHA-256 values (the full before/after pairs are retained in `proof.json`):

| Staged source | SHA-256 after integration |
|---|---|
| `nullmoth/kexts/NVRM/os-nvkms-xnu.cpp` | `ec27b73112ea5e029a2da46ce10953ef72a522dd1e2de319bd678334570ad37d` |
| `ogkm/src/nvidia-modeset/kapi/src/nvkms-kapi.c` | `d25439b4c3ea42a2e739227b34c1ce12d94c4c6441680bbd7a0774c9c07e1481` |
| `ogkm/src/nvidia-modeset/src/nvkms-modepool.c` | `736c8d3e2ad51c23f1dc0bff1be93d28c4263991a08272a90c8b59936b48cecb` |

All other headers, ABI definitions, NVIDIA RM sources, compiler flags, link order and source `0.1` version metadata remain unchanged. No NvGfx power modifications or NullMoth 2.6.3 update are included.

## Separate manifests and unchanged reference entry point

`build/nvrm615/build.py prepare/build` remains the reference entry point. Its default loader still accepts only reference format 1 with `diagnostic_enabled=false`. It rejects the experiment's manifest. The shared build function now permits the separate diagnostic runner to supply its strict input loader; the ordinary CLI does not expose a diagnostic-enable switch.

The experiment uses `build/nvrm615diag/build.py` and writes:

| File | Meaning |
|---|---|
| `baseline-inputs.json` | Preserved baseline manifest before applying the diagnostic pair |
| `inputs.json` | Format 2, `build_kind=yuv420-ab-experiment`, `diagnostic_enabled=true`; embedded provenance format 2 with historical pair and separate follow-up, full source/recipe hashes |
| `diagnostic-state.json` | `prepared`, `building`, `failed` or `built_static_review_required`; always `hardware_approved=false` |
| `build-state.json`, `artifacts.json` | The reused build's attempt/artifact records, explicitly marked diagnostic |
| `comparison/` within the candidate | Mandatory structural comparison against the unchanged official 2.5.4 bundle |
| Separate `--comparison-output` directory | Mandatory object/archive/toolchain and final binary comparison against reference-005 |

A failed integration does not leave a consumable reference manifest: the baseline manifest is renamed before applying the diagnostic, and format 2 is written only after verification. Changed recipes, changed inputs and reused build outputs are rejected. A compilation attempt is single-use; retain failures and choose a fresh output for retries.

## macOS commands

Run from this experiment branch's repository root as an ordinary user. Use the already available clean pinned NVIDIA checkout. Output paths must be new, separate from both checkouts and outside every existing build tree. No helper fetches source history or installs developer tools.

```bash
python3 build/nvrm615diag/build.py prepare \
  --vendor "$HOME/nvrm-inputs/open-gpu-615" \
  --output "$HOME/nvrm-builds/yuv420-diag-002"

python3 build/nvrm615diag/build.py build \
  --output "$HOME/nvrm-builds/yuv420-diag-002" \
  --reference "$HOME/nvrm-reference/2.5.4/NVRM.kext" \
  --baseline-build "$HOME/nvrm-builds/reference-005" \
  --comparison-output "$HOME/nvrm-audits/yuv420-diag-002-vs-reference-005" \
  --jobs 4
```

Use the same selected CLT/SDK/compiler as reference-005. Append `--sdk /absolute/path/to/MacOSX.sdk` only when selecting that existing SDK explicitly. An installed disassembler can be selected with `--llvm-objdump /absolute/path/to/llvm-objdump`; no installation is automatic.

The build reconstructs both NVIDIA archives and all glue objects from the fresh sources, links and ad-hoc signs only the new candidate, performs the official structural comparison, then runs the mandatory reference-005 comparison. It never resumes reference-005 or alters its manifests. Success records a review-required experimental artifact, not load approval.

To repeat only the static comparison, use another new report directory:

```bash
python3 build/nvrm615diag/compare.py \
  --baseline-build "$HOME/nvrm-builds/reference-005" \
  --candidate-build "$HOME/nvrm-builds/yuv420-diag-002" \
  --output "$HOME/nvrm-audits/yuv420-diag-002-vs-reference-005-repeat-001"
```

## Static comparison policy

The historical reference recipe is checked against the starting commit's recorded recipe hashes. The candidate's preserved baseline source manifests must match reference-005. Final source manifests may differ only in the three exact recorded transitions. Both builds' recorded source/artifact hashes and object inventories are verified read-only.

Compiler/SDK/header/tool evidence must match. Compiler and link arguments remain strict after replacing only the build-owned root paths in their known path positions. No global removal of flags, immediates, relocation bytes or memory offsets is allowed. A toolchain difference requires investigation and, when necessary, a new qualified reference; it is not a reason to broaden the object allowlist.

Every unaffected standalone object must be byte-identical. Missing/extra objects or changes outside the three declared objects are errors. The RM archive's object payloads must remain identical; NVKMS archive payload changes must be exactly the two declared objects. Archive member bytes are checked against standalone objects. Container timestamps/index/layout differences are recorded separately and never treated as permission to change object payloads.

On macOS, the comparator also reuses the structural bundle comparison and the full [instruction audit](NVRM-MACHINE-CODE-AUDIT.md). The paired hook is the only permitted additional global export. Structural rejection or an operational audit failure is an error. A completed instruction audit can still contain `different` and `not_comparable` functions; those remain explicit review items. UUID, ad-hoc signatures and linked layout naturally require separate interpretation. An object-scope pass is not proof that every final function or data reference is semantically equivalent.

## Existing diagnostic safety review

The existing A/B validation, production query and protection mechanisms remain unchanged:

- The normal `GetDisplayMode` request and reply copies are preserved. The extra diagnostic runs afterward and does not replace the production mode list or perform a modeset.
- A starts from the existing default validation parameters; B changes only `preferYUV420=true`. Each pass starts at index 0, and requests/replies/info buffers are zeroed anew for every query.
- Each claimed display ID permits at most 512 queries per pass, or 1024 additional `VALIDATE_MODE_INDEX` calls. Only a real end-of-list produces `COMPLETE`; the bound and errors produce `INCOMPLETE`/`ABORT`.
- Both heap allocations are released, including partial allocation failure. `nvKmsKapiFree(NULL)` is supported by the underlying allocator.
- The 2048-byte info buffer is forcibly NUL-terminated and emitted in chunks of at most 160 characters. The formatting helper has its own 512-byte bounded buffer. `diagColorReady` prevents reading an uninitialized output-color selection.
- The KAPI round trip uses local copies only; no productive conversion is repaired by the experiment.

Important limits and possible side effects:

- The trigger now requires only the first successful, non-end normal query of an explicitly selected display, regardless of timing. A failed normal query or an empty normal mode list does not start the diagnostic. A/B both still enumerate from index 0; absence of 4K60 yields zero 4K60 matches rather than preventing the run.
- The display claim is consumed before allocation and remains consumed after an aborted diagnostic. Hotplug does not reset it. The bitmap is global per loaded image, not keyed separately by GPU, connector generation or EDID.
- The extra validation is opt-in, but the new modepool info-string fields are gated by an info buffer plus the timing filter, not directly by the boot argument. Other verbose validation clients can therefore receive these extra fields even without an A/B run.
- A/B enumerations are not an atomic EDID/hardware snapshot. Hotplug and power-management activity can intervene. Compare source, full raster/sync timings and pixel clock, not mode index alone.
- A query cap is not a wall-clock timeout; there is no separate log-rate or log-volume budget. NVKMS validation can wait for PM and use preallocation, RM/DP queries or caches. Logging reaches existing kernel log paths. Absolute cache or hardware side-effect freedom is not established.
- Logged BPC/format/limits describe validation state, not an electrical measurement or successful output. A 594 MHz pixel clock remains 594 MHz when 4:2:0 reduces the required TMDS character rate.

No boot-argument, firmware or deployment changes are part of this preparation. The original [diagnostic interpretation guide](YUV420-DIAGNOSE.md#ausgabe-interpretieren) remains useful for a separately authorized later capture.

## Data, ABI and runtime limits

The three-object restriction confines the source/compiler delta, but it is not a complete ABI proof. The existing KAPI conversion loses information only in the diagnostic's reported local round trip; the experiment does not alter structure definitions, public table layouts or production output selection. Equal symbols do not prove prototypes, calling conventions, table slots, alignment, vtables, initializer bytes or runtime bindings.

The final instruction audit normalizes only supported proven address references. Anonymous data, relocations, aliases, indirect control flow and uncertain boundaries can remain not comparable. Keep those findings; do not count them as code identity. The existing four observed handle-field offsets cover only those accesses. Source `0.1` metadata, signature/loadability and compatibility with unchanged NVRMFB/NVAccel/other 2.5.4 components remain separate review items.

## Linux validation and rollback

At `61c581e`, the initial experiment Linux run passed **211 unit/integration tests**: 172 existing reference/audit/probe checks, 18 new paired-build integration checks and 21 new comparison checks. The existing twelve diagnostic harness scenarios passed against both the pristine-vendor test path and the fully prepared baseline-plus-diagnostic sources. The latter run also syntax-checked both complete NVKMS translation units. The comparison suite uses synthetic Mach-O/archive artifacts and simulated macOS child reports; it does not compare the actual NVRM binaries. Python 3.8 grammar and local documentation links were checked.

The trigger-corrected Linux run passed **216 unit/integration tests**: 172 existing reference/audit/probe checks, 23 paired-build integration checks and 21 comparison checks. All twenty current diagnostic harness scenarios passed against both pristine vendor inputs and the fully prepared baseline-plus-pair-plus-follow-up sources. The historical original-only mode also passed all twelve original scenarios. The new cases cover 4K30 without 4K60, mixed lists, 1080p/30 triggering, empty normal and diagnostic lists, missing opt-in, unselected IDs, normal/diagnostic IOCTL failures, repeated calls, allocation failure and the eight-line overview cap with 512 queries per pass. Full native NVKMS translation-unit syntax/format checks, Python 3.8 grammar, source/recipe hash checks and documentation links passed. These remain Linux model/static results, not a native macOS build or hardware test.

Run the existing regressions and the new integration/comparison suites. The diagnostic harness also accepts `--prepared-build` and runs twenty scenarios, including 4K30-only and bounded-overview cases, against the actual staged baseline-plus-diagnostic sources and XNU hook, without repatching the supplied build. Its reverse/reapply check affects only a temporary copy.

```bash
python3 tools/test_nvrm615_build.py --vendor /absolute/path/to/open-gpu-615
python3 tools/test_nvrm615_timer.py --vendor /absolute/path/to/open-gpu-615
python3 tools/test_nvrm615diag_build.py --vendor /absolute/path/to/open-gpu-615
python3 tools/test_nvrm615diag_compare.py
python3 tools/test_yuv420_diagnostic.py --vendor /absolute/path/to/open-gpu-615
python3 tools/test_yuv420_diagnostic.py --vendor /absolute/path/to/open-gpu-615 --historical
python3 tools/test_yuv420_diagnostic.py --prepared-build /absolute/path/to/new/prepared-diagnostic
```

Linux fixtures do not demonstrate a Darwin NVRM build or actual comparison against reference-005. Preserve the complete macOS build logs and all JSON/raw comparison evidence when those operations are run.

To stop using this experiment, retain its evidence and return to the reference branch. There is nothing to uninstall because no deployment is performed. Old outputs and the production driver remain intact. Do not attempt to roll back by editing old manifests or overwriting reference-005.
