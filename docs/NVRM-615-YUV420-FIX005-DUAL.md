# fix-005 — simultaneous YUV420 4K60 opt-in for two RTX 5080 displays

Status: **isolated source-only gate patch and executable truth-table test**.
There is **no fix-005 NVRM binary**, no macOS kernel collection and no
approval to change the installed driver or OpenCore yet.

## Confirmed runtime evidence (31 Oct 2026)

Hardware: one PNY RTX 5080 GB203, macOS Tahoe 26.7.1, NVIDIA 615.78.08
inside NullMoth 2.5.4. The independently built fix-004 NVRM has binary
SHA-256 `c73a8b5065e1474a07e6ba7dd66c151266e11b5c9e053f113a22d90a0cf18160`.

- `nvyuv420fix=0x400`: LG UltraFine via active DP-to-HDMI and HDMI
  switch offers and displays **3840x2160 at 60Hz**, user verified.
- `nvyuv420fix=0x4000`: LG UltraFine via active DP-to-HDMI and HDMI
  matrix offers and displays **3840x2160 at 60Hz**; user independently
  confirmed the actual 3840x2160/60 incoming signal in the monitor OSD
  after enabling *Show All Resolutions* in macOS. Both Retina 1920x1080
  and unscaled 3840x2160 desktop modes were observed. No more OSD
  'Out of Range' at those modes.
- Concurrent 4K60 was **not yet tested**. Fix-004 explicitly rejects
  multi-bit `nvyuv420fix` masks. Reintroducing the mask without new
  validation would be ineffective.

A different, historically misleading default logical mode
2048x1080 and inconsistent matrix EDID HorizSync (30–68 kHz while 4K60
requires ~135 kHz) are **not** subjects of this patch. Do not modify
EDID, timing selection, 4K60 mode validation or the two working
hardware YUV420 reconstruction call sites for this experiment.

## Scope

New overlay patch:
`build/nvrm615fix005/patches/xnu-yuv420-dual-optin.patch`

Apply **only after the exact fix-004 patches**, to the proven
fix-004 *staged source*, not to the original NullMoth XNU source file.
Only `nvkms_yuv420_fix_enabled()` is changed. The mask is allowed
only when composed entirely of `0x400` and/or `0x4000`. Only a display
equal to one of these two observed handles can be enabled. No
unrecognized handles are admitted.

| requested boot argument | display 0x400 | display 0x4000 | other display |
| --- | --- | --- | --- |
| absent | off | off | off |
| 0x400 | on | off | off |
| 0x4000 | off | on | off |
| 0x4400 | on | on | off |
| 0x0, 0x800, 0x4800, 0x4401, 0xffffffff | off | off | off |

The handles are **boot-local**, not persistent port identifiers.
The tested host has one NVIDIA GPU; do not assume this mask is
appropriate for another GPU or after hotplug/mapping changes.

## Safe initial test on the user's Mac

Use a new detached Git worktree to preserve the previous fix-004
checkout:

```bash
WORK="$HOME/nvrm-work/nvidia-macos-driver-fix"
NEXT="$HOME/nvrm-work/nvidia-macos-driver-fix005"
git -C "$WORK" fetch origin experiment/nvrm-615-yuv420-fix005-dual
git -C "$WORK" worktree add --detach "$NEXT" FETCH_HEAD
/usr/bin/python3 "$NEXT/tools/test_yuv420_fix005_dual.py"
```

The regression script requires the **exact fix-004 build-003 XNU source
SHA-256** `2b1c434507a91c8dffa60f101caf30a326d7711a1f5cc05c4404d5ddbf24ca7b`.
It applies the overlay **only in a temporary folder**, verifies that
the entire source change is exactly the intended gate, then uses clang
to compile and execute 96 opt-in truth-table cases. It does not write
to fix-004 build-003, any installed KEXT, boot parameters or kernel
collections.

## Remaining work before hardware test

1. Implement a **distinct fix-005 pinned source-preparation and build
   runner** derived from the proven fix-004 preparation (but with
   independent source SHA-256 and Git blob identities). Do not modify
   any existing fix-004 proof, build output or reference baseline.
2. Rebuild `NVRM.kext` with only the XNU gate change; compare source
   inventory, compiler commands, Mach-O imports/exports, metadata,
   and object-level and instruction-level changes to fix-004.
3. Verify a separately staged Auxiliary Kernel Collection, code
   signature and KextPolicy when installing.
4. Keep the proven fix-004 driver, its active KC and matching OpenCore
   config as the rollback. Change boot argument to `0x4400` **only
   after** fix-005 build and live-path collection approval. Do not
   assume 1401's 'boot once without NVIDIA driver' restores fix-004.
5. Confirm both monitor OSDs independently at 3840x2160/60, and
   check remaining displays, warm boot and cold boot. If either
   fails, restore the proven fix-004 and its corresponding opt-in.

This document is a source/test plan, **not** an installation
instruction.
