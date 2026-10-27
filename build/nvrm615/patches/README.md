# Normalized NVIDIA 615 baseline patches

These are context-normalized copies of the existing NullMoth display patches, in the order used by the audited NvGfx 615 build. They introduce no additional behavior beyond that original patch series. No existing patch in `kexts/NVRM/patches/` is modified or replaced.

The reference inputs are pinned to:

- NullMoth 2.5.4 source: `46779055f68208baa5ddfb9b2e9c77ed028e1d59`.
- NvGfx `nvgfx-2.5.3`: `0eff8317443f7af55dc4b4fd333173819859799c`.
- NVIDIA `615.78.08`: `1a82fbfda8f321b056eb3568850f9316c5e465c5`.

All four original patches are byte-identical between the pinned NullMoth and NvGfx inputs. NvGfx additionally lists the viewport patch in its completion-archive preparation; the existing NullMoth helper does not.

## Order and scope

Apply the following sequence once to an owned, pristine staged copy of the pinned NVIDIA tree. Patch paths are relative to `src/nvidia-modeset/`. The runner must first verify the source commit and patch hashes, then use strict `git apply --check` and `git apply`. It must reject prepatched or modified inputs rather than guess which changes are present.

| Order | Normalized patch | Original NullMoth patch | Target |
|---|---|---|---|
| 1 | `615-display-completion.patch` | `610-display-completion.patch` | `kapi/src/nvkms-kapi.c` |
| 2 | `615-setmode-reason.patch` | `610-setmode-reason.patch` | `kapi/src/nvkms-kapi.c` |
| 3 | `615-viewport-scaling.patch` | `615-viewport-scaling.patch` | `kapi/src/nvkms-kapi.c` |
| 4 | `615-backlight-register-log.patch` | `610-backlight-register-log.patch` | `src/nvkms-rm.c` |

The original `610-display-completion.patch` applies to vanilla 615 only when GNU `patch` permits **fuzz 1** for its third hunk. Its old context ends the preceding function with `return !status || params.reply.complete;`. In 615, the function immediately before `FramebufferConsoleDisabled` is `nvKmsKapiProbe`. The normalized patch uses the actual 615 context. The other normalized patches record their exact positions after the preceding changes. No added or removed source statements differ from the original sequence.

The NVIDIA 615 KAPI header already contains the `getFlipPendingStatus` function-table member; the vanilla implementation leaves it unset. The completion patch adds the implementation and table assignment, plus notifier resets. It does not extend the public structure layout.

## Provenance and verification

`patch-proof.json` records every original and normalized SHA-256, the source pins, the order, and the final patched source hashes. The local `.gitattributes` disables end-of-line/EOF blank checks only for these patch files: unified-diff context lines intentionally contain a single-space prefix, which must be preserved. The original patch hashes are:

| Original patch | SHA-256 |
|---|---|
| `610-display-completion.patch` | `729216b44c0f1dc31223d971d5aadf2e5531a1f8cf69b12ede8e41da3a7971eb` |
| `610-setmode-reason.patch` | `622e99e70a52310ef4c8a9bae4b20f10dfe3c78b300a594267cb4dfcc6f50ffb` |
| `615-viewport-scaling.patch` | `c0c30b4085a43d60c0ff0427c12e3c44dd490d6cf6a501edb446b5d9cf6b501f` |
| `610-backlight-register-log.patch` | `97b7028ed45789895906615f1417103e728d8e5b86cde6fef3137c28d5a6a64d` |

Linux verification used separate temporary copies of the actual pinned NVIDIA sources:

1. Apply the original ordered sequence with GNU `patch` and its default fuzz allowance, matching NvGfx's helper.
2. Apply the normalized sequence with strict `git apply --check` and `git apply`.
3. Compare the complete resulting files byte for byte; both sequences produce identical files.
4. Check that attempting to apply each normalized patch twice fails.
5. Check that the existing YUV420 diagnostic patch can subsequently apply strictly, without applying it to the reference build.

Final SHA-256 values after the baseline sequence:

| Source relative to `src/nvidia-modeset/` | SHA-256 |
|---|---|
| `kapi/src/nvkms-kapi.c` | `ff0f957733b1576ad5ddd6e6c60c33f899c71f0010e52b1c078f90011203ae81` |
| `src/nvkms-rm.c` | `0118510d6ee1c72b8cef4be93338ad1ca0d681ef15df880a39a9157950bb01cc` |

These checks establish patch provenance and applicability. They do not establish Mach-O compilation, linking, binary compatibility, loadability, or hardware behavior.

## Deliberately excluded changes

- **YUV420 diagnostics:** `615-yuv420-diagnostic.patch` is not applied in the baseline. The 2.5.4 XNU source snapshot also excludes the later `nvkms_yuv420_diag_claim` hook. Integrating the existing paired diagnostic changes is a later step after the baseline build and official 2.5.4 binary comparison.
- **NvGfx power and physical controls:** do not import `nvrm_power.cpp`, `nvrm_physctl.c`, `nvrm_physctl_abi.h`, their NVRM entry points, or the NvGfx GPS/NVPCF/TGP ACPI changes. Preserve NullMoth's existing `nv_xnu_power.cpp`; it is a different, upstream component.
- **610 FSP cleanup patch:** do not apply `610-fsp-timer-double-destroy.patch` to this 615 baseline. NVIDIA 615 already guards and clears `pPollEvent` in `kfspCleanupBootState_IMPL`. Its timer implementation does not include the separate handle-clearing hunk from the 610 patch; this baseline deliberately preserves the pinned 615 RM rather than applying additional cleanup changes.
- **Existing completion-archive helper:** do not run the original helper on an already baseline-patched tree. It would attempt the same patches again and writes a hard-coded `610.57.04` identification string. The isolated 615 build must compile the prepared source once and retain consistent 615 metadata.
