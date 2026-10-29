# RTX 5080 / NVIDIA 615 YUV420 production-fix investigation

Status: **stage-1 source patch written; full fix and build integration NOT complete**. This branch must **not** be installed, or used to alter EFI. Discovery-only source changes are still insufficient for a productive modeset. Parent: `experiment/nvrm-615-yuv420-ab`. Preserve the working diagnostic and official NullMoth 2.5.4 rollback.

## Implemented stage 1 (source-only, not deployable)

Two new patches on this branch:

- `build/nvrm615fix/patches/615-yuv420-fix-discovery.patch`: guarded `GetDisplayMode` retry for invalid 3840x2160 at 59.9–60.1 Hz. Strict same timings/clock check, hardware YUV420 mode required. Normal responses are unchanged when absent/disabled or when retry fails. It does **not** fix `ValidateDisplayMode` or `KmsSetMode`.
- `build/nvrm615fix/patches/xnu-yuv420-fix-optin.patch`: single-display `nvyuv420fix` gate, distinct from `nvyuv420diag`, disabled by default.
- `tools/test_yuv420_fix_stage1.py`: read-only `git apply --check` against existing `yuv420-diag-003` staged inputs, verifying KAPI source SHA and no mutations.

Static strict hunk simulation verified both new patches against the pinned original NVIDIA KAPI source after the existing diagnostic and trigger-follow-up patches, and against the existing XNU hook. **This is not a compiler, static link, runtime or hardware test**. The installed NVRM remains `diag-003`. Do not enable `nvyuv420fix` yet. The original diagnostic build runner's pinned source hashes remain intentionally unchanged.

Next: build a dedicated pinned fix runner with additional source-hash transitions and object allowlist; implement matched-mode YUV420 reconstruction for both `ValidateDisplayMode` and `KmsSetMode`, test preflight/commit semantics, and only then compile and compare an isolated new binary.

## Real hardware evidence — 2026-10-29

Target: PNY RTX 5080 GB203, Tahoe 26.7.1, four heads; 2 affected active DP-to-HDMI signal paths. The `yuv420-diag-003` NVRM kernel image was confirmed loaded with UUID `27B4E5F2-06D5-3B3C-87B0-277EA7D7BFF7`.

| Display | Physical path | Normal A (4K60 / 59.94) | Prefer420 B | B local KAPI round trip |
|---|---|---|---|---|
| 0x400 | LG UltraFine, DP-to-HDMI | invalid, 594/593.41 MHz exceeds 300 MHz | valid, YUV420 HW, 8 bpc | HW -> NONE, lost=1 |
| 0x4000 | DP-to-HDMI then HDMI matrix | same | same | same |

Both EDID-derived mode masks report RGB/Y420 `0xe`, VIC 97. B internally succeeds with format 3 (YCbCr 4:2:0); A fails with format 1 (4:2:2). A/B has no productive modeset: this evidence **does not** prove 4K60 signal output. Matrix EDID additionally claims 30–68 kHz horizontal sync even though its 4K60 mode requires ~135 kHz; NVKMS explicitly ignores that contradictory sync-range check.

## Source call sites to modify only after proof

Pinned vendor source: NVIDIA `open-gpu-kernel-modules` 615.78.08, file `src/nvidia-modeset/kapi/src/nvkms-kapi.c`. Existing diagnostic is in `kexts/NVRM/patches/615-yuv420-diagnostic.patch` plus separate trigger/overview follow-up. Baseline five patches and proof/hashes are immutable; new fix must be an **additional** patch applied after the fully verified diagnostic sources in a new output.

1. `GetDisplayMode`: `NVKMS_IOCTL_VALIDATE_MODE_INDEX` obtains a mode valid flag. Only for a single-bit, opted-in display ID and exact 3840x2160 59.94/60Hz candidate, allow a *second* bounded validation with `preferYUV420=TRUE` if normal validity is false. Preserve the normal reply for all other candidates/displays.
2. `ValidateDisplayMode`: KAPI-mode conversion drops `NvModeTimings.yuv420Mode`. A `preferYUV420` bit alone is **not** sufficient. Only for the opted-in candidate, safely recover the selected internal `NV_YUV420_MODE_HW/SW` from fresh, matching NVKMS/EDID validation; do not blindly force HW or change struct ABI.
3. `NvKmsKapiRequestedModeSetConfigToKms` / `KmsSetMode`: ensure the same target+timing gate, internal 420 mode and preference survive the actual set-mode path. Verify with prepare/commit instrumentation before considering productive commit; no global changes to RGB modes or other heads.
4. XNU hook in `kexts/NVRM/os-nvkms-xnu.cpp`: independent opt-in bootarg, suggested `nvyuv420fix=0x400` for the first test. Parse once; require one GPU, power-of-two display handle, and exact mask match. No use of the diagnostic claim bitmap for activation. Avoid changing published KAPI ABI.

## Safety and acceptance criteria

- No unsigned/unchecked file moves into `/Library/Extensions`; keep official 2.5.4 and diagnostic snapshots, signed KEXT hashes, AuxiliaryKernelExtensions.kc and KextPolicy history backups.
- No production display changes during the source-preparation and static-test phases. Distinct candidate root, output paths, comparison manifests and source-hash transitions. Do not reuse `yuv420-diag-003` build directory.
- Test default-off and target mask semantics (0x400, 0x4000 and invalid/missing masks), independent revalidation, EDID 420 capability, 59.94/60 Hz exact timing match, failed hardware selection, mode-set preflight/commit, four-head isolation, and fallback to 4K30.
- All unchanged objects must be byte-identical to the known reference; the changed-object allowlist must be justified and exact. Review final Mach-O diff and code signature. Successful compilation is not runtime proof.
- First runtime attempt only after verifying signed bundle, KextPolicy CDHash and live-path kernel collection, with recovery entry verified. Initially enable only 0x400. If 4K60 isn't listed or modeset fails, capture NVRM/NVRMFB logs; do not automatically try unbounded configurations.
- Once verified, test 0x4000 separately. Only then offer a minimal, redacted evidence summary for nullmoth issue #383.

## Immediate next gate

Verify the existing Mac clone against `origin/experiment/nvrm-615-yuv420-ab`, and inventory the complete NVIDIA 615 source, build scripts, local Xcode/CLT/SDK, signed reference-005 and diagnostic-003. Do not modify the running driver. Next deliverable: separately hashed and regression-tested production-fix patch + isolated builder extension, **not** an installation script.
