# Normalized NVIDIA 615 baseline patches

The first four patches are context-normalized copies of the existing NullMoth display patches, in the order used by the audited NvGfx 615 build. The fifth carries the missing timer callback safety assignment from the pinned NullMoth 2.5.4 patch. It intentionally omits the FSP hunk already present in NVIDIA 615. No existing patch in `kexts/NVRM/patches/` is modified or replaced.

The reference inputs are pinned to:

- NullMoth 2.5.4 source: `46779055f68208baa5ddfb9b2e9c77ed028e1d59`.
- NvGfx `nvgfx-2.5.3`: `0eff8317443f7af55dc4b4fd333173819859799c`.
- NVIDIA `615.78.08`: `1a82fbfda8f321b056eb3568850f9316c5e465c5`.

The four original NVKMS patches are byte-identical between the pinned NullMoth and NvGfx inputs. NvGfx additionally lists the viewport patch in its completion-archive preparation; the existing NullMoth helper does not.

## Order and scope

Apply the following sequence once to an owned, pristine staged copy of the pinned NVIDIA tree. Each entry has an explicit `apply_directory`: patches 1–4 are relative to `src/nvidia-modeset/`; patch 5 is relative to `src/nvidia/`. The runner verifies source pins, original/normalized patch hashes, the exact before/after target hashes and a single declared target in `git apply --numstat -z`, then uses strict `git apply --check` and `git apply`. There is no fuzz or whitespace relaxation. It must reject prepatched or modified inputs rather than guess which changes are present.

| Order | Normalized patch | Original NullMoth patch | Target |
|---|---|---|---|
| 1 | `615-display-completion.patch` | `610-display-completion.patch` | `kapi/src/nvkms-kapi.c` |
| 2 | `615-setmode-reason.patch` | `610-setmode-reason.patch` | `kapi/src/nvkms-kapi.c` |
| 3 | `615-viewport-scaling.patch` | `615-viewport-scaling.patch` | `kapi/src/nvkms-kapi.c` |
| 4 | `615-backlight-register-log.patch` | `610-backlight-register-log.patch` | `src/nvkms-rm.c` |
| 5 | `615-timer-callback-clear.patch` | Timer assignment only from `610-fsp-timer-double-destroy.patch` | `src/kernel/gpu/timer/timer_ostimer.c` (RM scope) |

The original `610-display-completion.patch` applies to vanilla 615 only when GNU `patch` permits **fuzz 1** for its third hunk. Its old context ends the preceding function with `return !status || params.reply.complete;`. In 615, the function immediately before `FramebufferConsoleDisabled` is `nvKmsKapiProbe`. The normalized patch uses the actual 615 context. The other normalized NVKMS patches record their exact positions after the preceding changes. Their added and removed statements are unchanged; the timer patch is explicitly a subset of its original two-file patch.

The NVIDIA 615 KAPI header already contains the `getFlipPendingStatus` function-table member; the vanilla implementation leaves it unset. The completion patch adds the implementation and table assignment, plus notifier resets. It does not extend the public structure layout.

## Provenance and verification

`patch-proof.json` format 2 records every original and normalized SHA-256, source pins, patch order, application scope, exact per-patch before/after hashes, pristine/final source hashes and preserved FSP hash. Source-hash map keys are relative to the staged NVIDIA repository root, not implicitly to NVKMS. `inputs.json` retains the proof format, complete patch entries, preserved-source hashes and complete staged source/recipe hashes. The local `.gitattributes` disables end-of-line/EOF blank checks only for these patch files: unified-diff context lines intentionally contain a single-space prefix, which must be preserved. The original patch hashes are:

| Original patch | SHA-256 |
|---|---|
| `610-display-completion.patch` | `729216b44c0f1dc31223d971d5aadf2e5531a1f8cf69b12ede8e41da3a7971eb` |
| `610-setmode-reason.patch` | `622e99e70a52310ef4c8a9bae4b20f10dfe3c78b300a594267cb4dfcc6f50ffb` |
| `615-viewport-scaling.patch` | `c0c30b4085a43d60c0ff0427c12e3c44dd490d6cf6a501edb446b5d9cf6b501f` |
| `610-backlight-register-log.patch` | `97b7028ed45789895906615f1417103e728d8e5b86cde6fef3137c28d5a6a64d` |
| `610-fsp-timer-double-destroy.patch` | `2998eb54ae95310724b12d24f99e92b34cd4f6812bf9236cd0ba8249832784f0` |

Linux verification used separate temporary copies of the actual pinned NVIDIA sources:

1. For the original four NVKMS patches, apply the ordered sequence with GNU `patch` and its default fuzz allowance, matching NvGfx's helper. The timer patch is a separately verified one-statement subset, not an attempt to apply the full 610 patch to 615.
2. Apply the normalized sequence with strict `git apply --check` and `git apply`.
3. Compare the complete resulting NVKMS files byte for byte; both sequences produce identical files. Independently verify that the RM delta is exactly the callback NULL assignment and that FSP remains byte-identical to the pinned source.
4. Check that attempting to apply each normalized patch twice fails.
5. Check that the existing YUV420 diagnostic patch can subsequently apply strictly, without applying it to the reference build.

Final SHA-256 values after the five-patch baseline sequence:

| Source relative to the NVIDIA repository root | SHA-256 |
|---|---|
| `src/nvidia-modeset/kapi/src/nvkms-kapi.c` | `ff0f957733b1576ad5ddd6e6c60c33f899c71f0010e52b1c078f90011203ae81` |
| `src/nvidia-modeset/src/nvkms-rm.c` | `0118510d6ee1c72b8cef4be93338ad1ca0d681ef15df880a39a9157950bb01cc` |
| `src/nvidia/src/kernel/gpu/timer/timer_ostimer.c` | `da908b3245f9be48f0787eb48fd4d70b721f6d48cd55e99bb99421bb370379f2` |

The pristine timer hash is `9710b89d0725d433b993ba803983bde1262096e7f62b81d39d0bc8f816ff5bb3`. The FSP file remains `84ba2a49c08aa490aad40fe737b42d135e3bf11969e76ca257a8e5f425c51e54` before and after preparation. The full staged source inventory differs from the pinned repository in exactly these three patched files.

These checks establish patch provenance and applicability. They do not establish Mach-O compilation, linking, binary compatibility, loadability, or hardware behavior.

## Deliberately excluded changes

- **YUV420 diagnostics:** `615-yuv420-diagnostic.patch` is not applied in the baseline. The 2.5.4 XNU source snapshot also excludes the later `nvkms_yuv420_diag_claim` hook. Integrating the existing paired diagnostic changes is a later step after the baseline build and official 2.5.4 binary comparison.
- **NvGfx power and physical controls:** do not import `nvrm_power.cpp`, `nvrm_physctl.c`, `nvrm_physctl_abi.h`, their NVRM entry points, or the NvGfx GPS/NVPCF/TGP ACPI changes. Preserve NullMoth's existing `nv_xnu_power.cpp`; it is a different, upstream component.
- **Redundant FSP hunk:** do not apply the complete `610-fsp-timer-double-destroy.patch` to 615. NVIDIA already guards and clears `pPollEvent` in `kfspCleanupBootState_IMPL`. Only its missing timer callback assignment is carried as patch 5; no FSP code or other RM source is changed.
- **Existing completion-archive helper:** do not run the original helper on an already baseline-patched tree. It would attempt the same patches again and writes a hard-coded `610.57.04` identification string. The isolated 615 build must compile the prepared source once and retain consistent 615 metadata.

## Timer safety correction and historical builds

The user reports that the official 2.5.4 `_tmrEventDestroyOSTimer_OSTIMER` clears the callback field after `osDestroyNanoTimer`, while `reference-004` omits that operation. The pinned source independently confirms the missing statement. Earlier reference recipes excluded the entire 610 patch because its FSP hunk was upstream; this also excluded the still-required timer half.

`615-timer-callback-clear.patch` adds exactly `pTmrEvent->super.pOSTmrCBdata = NULL;` immediately after the destroy call, before clearing the queued flag. It preserves the original NullMoth unconditional placement after the call, without a new status check. NVIDIA's `osDestroyNanoTimer` calls the void destructor and returns `NV_OK`; the XNU destructor cancels/frees the timer. Clearing the stored pointer prevents a later call on the same live event from reusing that stale callback. It does not establish arbitrary concurrent double-destroy safety or permit accessing a freed event object.

Preserve all earlier outputs, especially `reference-004`. The changed recipe deliberately requires a new preparation, such as `reference-005`; do not edit old manifests or patch old staged sources. No complete macOS build, final machine-code comparison or hardware test of this correction has been run in this Linux workspace. Baseline compiler options, source commits, release metadata, other components and the excluded YUV420 diagnostic are unchanged.

The Linux timer regression can be repeated with:

```bash
python3 tools/test_nvrm615_build.py --vendor /absolute/path/to/open-gpu-615
python3 tools/test_nvrm615_timer.py --vendor /absolute/path/to/open-gpu-615
```

The latter compiles the real extracted function bodies with model RM types and an OS destructor stub. Its sequential second-destroy and NULL-guard checks validate the transplanted statement's behavior, not production ABI or concurrent event lifetime. No source checkout or existing build is modified.
