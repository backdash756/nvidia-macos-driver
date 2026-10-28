<a id="diagnose-von-4k60-über-dp-zu-hdmi"></a>

# Diagnosing 4K/60 over DP to HDMI

> **Current isolated build workflow:** See [NVRM-615-YUV420-EXPERIMENT.md](NVRM-615-YUV420-EXPERIMENT.md) for the paired NVIDIA 615 build based on the pinned NullMoth 2.5.4 sources and reported successful `reference-005`. That workflow builds and statically compares only; it does not authorize installation or a hardware test. The historical implementation and capture notes below are not deployment instructions for this build task.

This diagnostic compares two separate NVKMS mode-validation passes. It is intended to establish whether a 3840×2160 mode at approximately 60 Hz fails with the existing parameters but passes with `preferYUV420=true`. It is not an output fix and does not demonstrate a successful display modeset.

The branch is based on the existing fork revision `6e375469` (changelog 2.6.2). At that revision, the examined display sources, including `os-nvkms-xnu.cpp`, are unchanged from the local 2.5.4 revision `4677905`. No driver package has been built, and no binary version has been relabeled as 2.5.4.

<a id="umfang-und-dateien"></a>

## Scope and files

- `kexts/NVRM/patches/615-yuv420-diagnostic.patch` adds the diagnostic to the external NVIDIA sources `kapi/src/nvkms-kapi.c` and `src/nvkms-modepool.c` at tag **615.78.08**.
- `kexts/NVRM/os-nvkms-xnu.cpp` adds the opt-in hook `nvkms_yuv420_diag_claim()`. It restricts the diagnostic to selected display IDs and claims each ID at most once per boot.
- `tools/test_yuv420_diagnostic.py` tests the diagnostic path on Linux. Consult the execution output for the actual test results.

The additional A/B queries do not run without the `nvyuv420diag` boot argument. The default parameters of the normal mode query remain unchanged. Even when the argument is enabled, the diagnostic replaces neither that query's response nor the normal mode list.

<a id="ablauf"></a>

## Execution flow

The diagnostic starts when `GetDisplayMode()` first returns a candidate with 3840×2160 and 59–61 Hz for a selected ID:

1. Pass **A** starts at mode index 0 with the existing default parameters.
2. Once that pass ends, pass **B** starts again at mode index 0. Only `preferYUV420` is additionally enabled.
3. The selection parameters remain constant within each pass. Both passes use their own freshly initialized requests.
4. For matching 4K candidates, the internal mode and a local round trip through the KAPI conversions are logged. The converted copies are not passed to a modeset.

Only `VALIDATE_MODE_INDEX` queries are added. There is no additional modeset or commit call, and no safety or bandwidth checks are disabled. NVKMS validation can nevertheless use internal caches and hardware queries; this diagnostic does not establish that all hardware accesses are free of side effects. The additional queries and logging can increase boot time.

Each pass is limited to 512 queries. Only an end-of-list response from NVKMS produces `COMPLETE`. Reaching the limit or encountering a query failure produces `INCOMPLETE`; allocation and IOCTL failures are reported as `ABORT`. An incomplete pass must not be treated as a complete comparison.

The candidate list can depend on the validation parameters. **Match A and B by source, complete raster/sync timings, pixel clock, and VIC where available, rather than by an identical mode index alone.** Do not change cables or inputs during capture. The patch does not enforce an unchanged EDID/hotplug generation across the two passes; such a change requires the comparison to be reassessed.

<a id="anwenden-auf-den-nvidia-quellbaum"></a>

## Applying the patch to the NVIDIA source tree

An external NVIDIA **615.78.08** tree matching the existing NullMoth build is required. Set the following variables to the actual local directories:

```bash
driver_repo=/pfad/zum/nvidia-macos-driver
ogkm_root=/pfad/zum/open-gpu-kernel-modules
diag_patch="$driver_repo/kexts/NVRM/patches/615-yuv420-diagnostic.patch"

git -C "$ogkm_root" apply --check --directory=src/nvidia-modeset "$diag_patch"
git -C "$ogkm_root" apply --directory=src/nvidia-modeset "$diag_patch"
```

These commands resolve paths relative to the root of the full NVIDIA repository. Preserve existing local changes and the existing NullMoth patch sequence. If `--check` fails, investigate the difference; do not proceed by forcing application or removing existing patches.

The diagnostic has been checked against unmodified 615.78.08 sources and after applying `615-viewport-scaling.patch`. This does not validate the complete historical patch sequence: the existing contexts in `610-display-completion.patch` do not all apply to unmodified 615. The already working 615 build tree used for the existing driver is therefore the reference; do not blindly reapply the old 610 patches here.

<a id="bauen-und-installieren-unter-macos"></a>

## Building and installing on macOS

The detailed investigation of the public build path, binary compatibility with 2.5.4, and rollback on a separate test system is in [NVRM-DIAGNOSE-BUILD.md](NVRM-DIAGNOSE-BUILD.md). The separate [NVRM-615-REFERENCE-BUILD.md](NVRM-615-REFERENCE-BUILD.md) now documents an isolated reference harness adapted from NvGfx, closing the previously missing public archive/link recipe. That reference must be built and compared with official 2.5.4 before diagnostic integration; its macOS validation remains open.

The diagnostic patch and the new XNU hook must be built together. Using the existing working Darwin/Xcode build setup, rebuild at least:

- the NVIDIA object for `kapi/src/nvkms-kapi.c`;
- the NVIDIA object for `src/nvkms-modepool.c` and the corresponding modeset archive;
- NullMoth's `os-nvkms-xnu.cpp` and the `NVRM.kext` linked against it.

Deploy the matching components through the existing build and installation process. A vendor archive alone does not contain the new XNU hook. Check for unresolved symbols when linking, and validate the resulting kext bundle using the usual build and signature checks.

**`kexts/NVRM/build-completion-archive.py` is unsuitable for this purpose.** The existing script targets NVIDIA 610.57.04, specific existing Darwin objects, and a fixed 610 version identifier. It is not a complete 615 builder. `build/accel_build.sh` builds NVAccel and does not replace an NVRM build either.

The original checkout lacked a complete, independently executable 615 NVRM build. The new separate reference harness prepares that build using a checked CLT/SDK environment; it does not yet integrate this diagnostic or establish compatibility. For the diagnostic build described here, a working, matching Darwin build remains a prerequisite. Do not bypass kernel collection, approval, or signature checks; use the existing installation process for the matching kext set. Have the known working package and its recovery procedure available beforehand.

<a id="aktivieren-und-logs-erfassen"></a>

## Enabling the diagnostic and capturing logs

The diagnostic currently supports **one GPU**. Target IDs are the `display 0x...` values from that GPU's current NVRMFB logs. They are not permanently stable port numbers. Only single-bit IDs are supported; dynamic or differently encoded IDs are not generally covered.

In the previous capture, the affected paths corresponded to IDs `0x400` and `0x4000`; their combined mask is `0x4400`. Only if that mapping still holds is the additional boot argument:

```text
nvyuv420diag=0x4400
```

Add it alongside the existing arguments through the existing OpenCore configuration procedure. Leave other boot arguments unchanged. After boot, verify the mapping again against the enumeration. An ID claim applies for the entire boot and is not tracked separately per GPU; therefore, do not use the mask on multi-GPU systems without checking. A new boot is required to repeat the diagnostic, including after an aborted run.

Capture logs through the existing diagnostic procedure or with this read-only terminal command:

```bash
log show --last 30m --style compact \
  --predicate 'eventMessage CONTAINS "Y420-AB"'
```

Also retain the NVRMFB enumeration so that `dpy=0x...` can be mapped to the physical signal path. Without `BEGIN` lines, there is no evidence that the diagnostic ran. Possible reasons include a missing argument, a mismatched build, an incorrect target ID, or no queried candidate within the filter range.

<a id="ausgabe-interpretieren"></a>

## Interpreting the output

Each output line carries `Y420-AB`, the display ID, and, for the passes, `pass=A` or `pass=B`.

| Field | Meaning |
|---|---|
| `source`, `index`, raster/sync values | Candidate identity; do not assume that identical indices in A and B identify the same candidate. |
| `pclkHz` | Pixel clock of the complete mode in Hz. For typical 4K/60 timings, it remains 594 MHz even with 4:2:0. |
| `rrMilliHz` | Refresh rate in thousandths of a hertz, for example 60000 for 60 Hz. |
| `masks rgb`, `y420` | EDID-derived BPC bitmasks for the specific timing, not a directly readable number of bits per component. |
| `vic` | CTA video code, where associated with the candidate. |
| `selected` | Selected enum: `0=NONE`, `1=SW`, `2=HW`. |
| `hdmi`, `dp` | HDMI/DP-library classification used by NVKMS; not a complete identification of the physical adapter. |
| `maxPclkKHz`, `singleLinkKHz` | Effective pixel-clock/single-link limits used by NVKMS, in kHz. Their interpretation depends on the protocol and format; these are not electrical TMDS measurements. Raw RM dongle or active-PCON DPCD capabilities are not queried separately. |
| `final format` | Output format: `0=RGB`, `1=YCbCr422`, `2=YCbCr444`, `3=YCbCr420`. |
| `bpc` | Actual bits per color component: `6`, `8`, `10`, `12`; `0` means unknown. |
| `range` | NVKMS enum for the quantization range: `0=FULL`, `1=LIMITED`. |
| `valid` | Result of the corresponding internal validation. |
| `yuv420`, `roundtrip`, `lost` | Internal enum before KAPI conversion, enum after the local round trip, and an indicator of a difference. |
| `info:` | Existing NVKMS validation text with the additional diagnostic values. |

The info buffer is limited to `NVKMS_MODE_VALIDATION_MAX_INFO_STRING_LENGTH`, which is 2048 bytes in the examined revision. Output is split into short lines. `INFO_MAY_BE_TRUNCATED` means that text at the end of the buffer may be missing; a missing rejection reason must not then be interpreted as success. If validation fails before output color selection, no `final format`/BPC line is expected. That also does not establish any particular color depth.

For typical CTA timings, 4K/60 with RGB and 8 BPC requires a 594 MHz TMDS character rate; with 4:2:0 and 8 BPC, it requires 297 MHz. **Do not confuse the complete 594 MHz pixel clock with the TMDS character rate or simply halve it in this comparison.**

| Comparison | Interpretation |
|---|---|
| A: `NONE`, `valid=0`; B: `SW`/`HW`, `valid=1` | The early 4:2:0 selection is decisive for internal validation. A working display modeset has not yet been demonstrated. |
| B remains `NONE` even though `y420` indicates support | Check the connector path and additional 4:2:0 eligibility conditions. |
| The parsed `y420` mask indicates no support | Investigate EDID import and the mapping of the specific timing. |
| B selects 4:2:0 but remains invalid | The `infoString` must explain the remaining check. |
| `yuv420=SW`/`HW`, `roundtrip=NONE`, `lost=1` | The local KAPI round trip loses the flag. This is not an actual modeset. |

<a id="linux-prüfungen-und-offene-validierung"></a>

## Linux checks and outstanding validation

The intended test command, using the full external NVIDIA source tree, is:

```bash
cd "$driver_repo"
python3 tools/test_yuv420_diagnostic.py --vendor "$ogkm_root"
```

Twelve tests passed on Linux with a simulated NVKMS endpoint: separate A/B passes with different candidate indices, unchanged normal responses, filter/end/error cases, both allocation failures, query and buffer limits, HW/SW KAPI flag loss, and the concurrent once-per-boot hook. Patch application and reversal were checked without fuzz; the supplied original NVIDIA files remained unchanged.

The same test command also checks both complete patched NVIDIA C files using `-fsyntax-only`, actual 615 headers, and include/define options from the vendor Makefile. Both syntax and format checks passed on Linux with GCC. This is not a Darwin compilation, a link test, or a test of the EDID parser or hardware validation; the test endpoint simulates their results.

Link and kext compatibility can only be checked with the matching macOS/Xcode build. Boot behavior, A/B results, adapter limits, log completeness, and unchanged display output can only be checked on real hardware. These items remain unverified until those checks are performed. The diagnostic does not switch the output to 4K/60 and does not fix the problem yet.

<a id="rücknahme"></a>

## Reverting the changes

1. Remove the additional boot argument for future boots. This prevents new A/B queries but does not replace the installed diagnostic binary with the previous version.
2. Reverse the vendor patch only if the check completes without conflicts:

   ```bash
   git -C "$ogkm_root" apply --reverse --check --directory=src/nvidia-modeset "$diag_patch"
   git -C "$ogkm_root" apply --reverse --directory=src/nvidia-modeset "$diag_patch"
   ```

3. After reviewing `git diff`, selectively revert the changes in the NullMoth branch. While they are uncommitted, restore only changes or remove new files belonging to this diagnostic; do not indiscriminately overwrite existing personal changes. After a later commit, a targeted `git revert <Diagnose-Commit>` is a traceable alternative.
4. On the Mac, restore the known working driver package through the existing installation and kernel collection process, then reboot. Switching branches or reversing a source patch alone does not change already installed kexts.

The diagnostic branch does not have to be merged or published. A pull request to the original NullMoth project is not required for this local investigation.
