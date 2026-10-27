<a id="nvrm-diagnosebuild-für-tahoe-2671--x86_64"></a>

# NVRM diagnostic build for Tahoe 26.7.1 / x86_64

<a id="ergebnis-und-status"></a>

## Findings and status

> **Updated build investigation:** The audited NvGfx 615 Darwin harness now supplies the archive and link recipe missing from the original investigation below. See [NVRM-615-REFERENCE-BUILD.md](NVRM-615-REFERENCE-BUILD.md) for the isolated 2.5.4 reference workflow, strict patch manifest, CLT/SDK preflight and remaining macOS validation. This file retains the original audit and the compatibility/recovery cautions. Statements below about missing public recipes describe the checkout before this new harness; no macOS build or hardware qualification has yet been performed.

At the time of the original investigation, the public fork and unmodified NVIDIA sources did **not provide a reproducible, complete NVRM build with demonstrated compatibility with the shipped 2.5.4 release**. The XNU porting sources were present, but the complete Darwin build/link recipe and the exact preparation of the shipped NVIDIA backend were missing from that checkout. An invented `make TARGET_OS=Darwin` command would not have closed that gap. The new reconstructed harness now provides the concrete archive/link recipe; its compatibility still needs a real reference build and comparison.

The smallest useful diagnostic build can nevertheless be defined clearly: reuse an already working Darwin build matching 2.5.4, including the unchanged individual NVRM object files and the final link recipe; rebuild two NVKMS objects and the XNU hook; then relink exactly **NVRM.kext**. The other working 2.5.4 components remain unchanged. This is a conditional build plan, not yet a verified procedure that produces a finished kext.

This investigation installed nothing, generated no Kernel Collection, and changed no system files, EFI/NVRAM settings, or security configurations. No macOS build was run. The subsequent test steps described below have not already been performed.

<a id="quellen-und-reproduzierbare-ausgangsdaten"></a>

## Sources and reproducible starting inputs

| Component | Established state or state that must be established |
|---|---|
| Installed system | According to the terminal output: Tahoe 26.7.1, build `25G241`, x86_64; verify again on the test system. |
| Reference driver | The complete, actually working 2.5.4 package and its installed files, including signatures/metadata. |
| Local 2.5.4 source comparison | Commit `4677905`; this source revision alone does not establish the complete provenance of the release binary. |
| Fork baseline | `6e375469` (changelog 2.6.2). |
| Diagnostic change | Commit `ff37f3812fe170610885081f3a9cb223eb580938`; see [YUV420-DIAGNOSE.md](YUV420-DIAGNOSE.md). |
| NVIDIA | Tag `615.78.08`; the public Git query returned tag object `4bfe30bb78e7364959e59c16cfefd88e8c14670e` and resolved commit `1a82fbfda8f321b056eb3568850f9316c5e465c5`. Record the local tree and every additional patch. |
| Firmware | Retain the existing GSP firmware 615.78.08 that belongs to the driver; no firmware rebuild for this diagnostic. |

Reproducibility requires a manifest containing all source SHAs, patch order and patch hashes, compiler/SDK/linker versions, complete commands with working directories, object/archive manifests, and SHA-256 values for inputs and output. The initial criterion is a functionally compatible build. Byte-for-byte identity with the release is not guaranteed without its complete provenance and deterministic metadata/signing rules.

<a id="vorhandene-und-fehlende-buildteile"></a>

## Available and missing build components

| Finding | Specific evidence and consequence |
|---|---|
| The XNU port is present | `kexts/NVRM/NVRM.cpp`, `os-xnu.cpp`, `os-xnu2.cpp`, `os-nvkms-xnu.cpp`, power/GPUVA/surfshare modules, RM-internal C bridges, and `nvkms_shaders.cpp`. The entire macOS port is not simply absent. |
| The NVRM bootstrap is missing | `kexts/NVRM/rmcc.py:6–7` mentions `build-nvrm.sh`, which was not found in the checkout or the available Git history that was examined. The helper already expects `src/nvidia/_out/Darwin_x86_64/compile_cmds.sh` to exist. |
| The modeset archive helper is not a 615 bootstrap | `kexts/NVRM/build-completion-archive.py:14–15` expects existing Darwin compile commands and `libnvkms.a`; line 33 explicitly writes a 610.57.04 identifier. Do not use it unchanged for this diagnostic. |
| The public NVIDIA Makefiles do not provide a finished kext build | The 615 targets produce `nv-kernel.o` and `nv-modeset-kernel.o`. Their rules include GNU linker scripts, `objcopy`, and ELF-oriented processing. They do not produce the complete Darwin Mach-O/kext build needed here. Apple platform macros in `cpuopsys.h` do not replace that recipe. |
| The other available scripts do not solve the NVRM build | `build/accel_build.sh` and `accel_build_all.sh` build NVAccel; `build_agdc.sh` builds NVRMAGDC. The translator/plugin/Mesa scripts build user-space components. |
| The complete 615 patch manifest is missing | In particular, the shipped completion/KAPI extensions and viewport adjustments must be preserved. `610-display-completion.patch` cannot be applied completely to unmodified 615. Do not replace the release backend with vanilla 615. |
| The release metadata recipe is missing | `NVRM/Info.plist` and `KMOD_EXPLICIT_DECL` in `NVRM.cpp:45` contain version `0.1` in the source tree; the loaded version was 2.5.4. The process that consistently prepares the release metadata must be known. Do not simply invent a plist version or present the build as an unchanged original. |

The following specific items are missing for a standalone complete build:

1. The Darwin adaptations and complete object manifest for RM and NVKMS, including generated files, configuration defines, shader/binary embedding, and symbol handling.
2. The complete, ordered 615 patch series used by the working release tree.
3. The generation rules for Mach-O `libnvkernel.a` and `libnvkms.a`, or a working, verifiable baseline build of those archives.
4. The commands for all NullMoth XNU objects and RM-internal additional objects, using type definitions that match the archives.
5. The final NVRM link recipe: objects, archives and their order, libraries, visibility/export rules, kext entry point, and deployment target.
6. Consistent generation of bundle/kmod metadata, signature, and package manifest, along with toolchain/SDK pins.

An installed `NVRM.kext` cannot reliably be converted back into those original archives, individual objects, and complete compiler commands. A complete 2.5.4 package is an important reference and recovery asset, but it is not a substitute for the build inputs.

<a id="toolchain-und-abhängigkeiten"></a>

## Toolchain and dependencies

The README specifies **Xcode 16**, but no exact minor version, Apple Clang version, SDK version, or 615 NVRM deployment setting. It therefore does not establish that any particular 16.x version has been qualified. For the compatible minimal build, the toolchain of the working original build is the initial reference; it must then be pinned explicitly.

Required tools and inputs include Apple Clang/Clang++, the Darwin linker and archive tools, matching macOS kernel/IOKit/libkern headers, Python 3, and Mach-O, plist, and signature inspection tools. The existing NVAccel helper uses `xcrun --show-sdk-path` and `Kernel.framework/Headers`; its flags are clues, not a complete NVRM recipe. Likewise, the deployment target 15.0 used by the 610 archive helper is not evidence of the correct setting for a newly reconstructed 615 build.

The reference to the 26.7.1 KDK in `gen_tahoe_fwd.py` concerns reconstruction of **NVAccel** vtables. It does not establish a requirement to compile an NVRM-only diagnostic build against that exact KDK. Whether additional private kernel headers or a KDK are needed must be determined from the actual original NVRM recipe. Kernel Collection generation and C/C++ compilation are separate steps.

Rust, Meson/Ninja, Mesa/NVK, and the shader translator do not need to be rebuilt for this NVRM-only experiment. The existing working user-space components remain at the reference revision. The diagnostic patch does not generate the firmware images; `os-xnu2.cpp:29` expects them under `/Library/NullMoth/nvfw/nvidia/615.78.08/`.

The following inventory commands can be run later on the build Mac. They are read-only and require no `sudo`:

```bash
sw_vers
uname -m
xcodebuild -version
xcrun --sdk macosx --show-sdk-path
xcrun --sdk macosx --show-sdk-version
xcrun clang --version
xcrun --find clang++
xcrun --find ld
xcrun --find ar
```

<a id="kleinster-bedingter-buildablauf"></a>

## Smallest conditional build procedure

**Technical prerequisite before building:** A working 2.5.4/615 Darwin baseline build and the missing recipes listed above must be available. Until then, continue with source inspection and documentation. Do not produce a complete build script using guessed flags.

Once that prerequisite is met:

1. Create a separate staging tree outside `/Library`, `/System`, EFI, and production build outputs. Only copy the baseline archives and unchanged individual objects; retain the originals and their hashes. Use the actual patched 615 tree.
2. Check the diagnostic patch first with `git apply --check`; apply the patch documented in [YUV420-DIAGNOSE.md](YUV420-DIAGNOSE.md) only to the staging source tree. Preserve the existing patch series. Do not force individual 610 patches to apply.
3. Recompile `kapi/src/nvkms-kapi.c` and `src/nvkms-modepool.c` with their original Darwin compile commands. Preserve includes, defines, architecture, and ABI-relevant options; redirect only the source, object, and dependency outputs to the staging tree.
4. In a copy of the original `libnvkms.a`, replace exactly the corresponding archive members and update its index with the appropriate Darwin tool. Check the original member names/object formats; do not insert ELF objects from Linux.
5. Rebuild `os-nvkms-xnu.cpp` from the diagnostic commit with the original NullMoth command. Otherwise, the new `nvkms_yuv420_diag_claim` hook remains unresolved at the final link.
6. Reuse the unchanged matching `libnvkernel.a` and all other original NVRM objects. Relink with the original final NVRM link command, generate metadata consistently, and sign and verify the complete kext bundle in the staging tree.
7. Archive all input/output hashes and commands. **No installation step is part of this build procedure.**

Later functional validation also requires a baseline build **without** the diagnostic patch from the same inputs. If that build already fails to work with the known 2.5.4 components, a diagnostic A/B display test is not meaningful.

<a id="binärkompatibilität-254-gegenüber-262"></a>

## Binary compatibility: 2.5.4 versus 2.6.2

Git comparison: `4677905` versus `6e375469`, before the diagnostic commit.

| Component | Change | Consequence for this experiment |
|---|---|---|
| NVRM core/XNU port, NVRMFB, NVRMAGDC | Byte-identical sources; the NVRM subdirectory `accel` is considered separately. | No identified source ABI change requires rebuilding them together. |
| VRAM/GPUVA/flip ABI and plist templates | Unchanged; versions VRAM 2, GPUVA 1, flip 2. | The diagnostic patch does not change these interfaces. |
| NVAccel | Throttling of log frequency for rejected VRAM allocations. | No reason to replace the working Tahoe NVAccel 2.5.4 for the diagnostic. |
| User-space plugin | Video/capture/ANGLE changes, among others. | Not needed for the NVRM diagnostic; do not update it incidentally. |
| Installer | KextPolicy code-hash handling and restart instructions, among others. | Do not equate installer changes with binary compatibility or use them for the diagnostic without checking them. |

Identical sources do not establish a complete binary contract. NVRM publishes the `nvkms-kapi` function table as a pointer; NVRMFB consumes it directly (`NVRM.cpp:68`, `NVRMFB/fb/nvrm-fb.cpp:2448`). The existing function-pointer checks are not a complete layout fingerprint. Matching NVIDIA version labels or visible bundle versions are therefore insufficient.

**NVRM-only is the goal**, provided the original patched KAPI layout and all consumed exports are preserved. NVRMFB, NVAccel for macOS 26, NVRMAGDC, plugins, NVK, firmware, and unaffected caches remain in the working 2.5.4 state. The diagnostic adds no public KAPI struct. It does require both new vendor objects and the XNU hook to be included in the same newly linked NVRM.

<a id="artefaktprüfungen-vor-jedem-hardwareversuch"></a>

## Artifact checks before any hardware experiment

Inspect files in the staging/reference directory, not at production system paths:

```bash
reference_kext=/pfad/zur/gesicherten-2.5.4/NVRM.kext
candidate_kext=/pfad/zum/staging/NVRM.kext
file "$candidate_kext/Contents/MacOS/NVRM"
lipo -archs "$candidate_kext/Contents/MacOS/NVRM"
plutil -lint "$candidate_kext/Contents/Info.plist"
otool -hv "$candidate_kext/Contents/MacOS/NVRM"
otool -l "$candidate_kext/Contents/MacOS/NVRM"
nm -gU "$reference_kext/Contents/MacOS/NVRM"
nm -gU "$candidate_kext/Contents/MacOS/NVRM"
nm -u "$candidate_kext/Contents/MacOS/NVRM"
codesign --verify --strict "$candidate_kext"
dwarfdump --uuid "$candidate_kext/Contents/MacOS/NVRM"
shasum -a 256 "$candidate_kext/Contents/MacOS/NVRM"
```

Compare the x86_64 architecture, kext Mach-O type, deployment/SDK metadata, unchanged bundle ID/dependencies, and the public symbols that are still required. Do not create a second competing NVRM instance by renaming the bundle or its ID. The new hook function must be resolved internally in the finished NVRM. A kext has expected external kernel/IOKit symbols; `nm -u` does not have to be empty. New unexplained imports and missing previously consumed exports must, however, be investigated. Successful signature verification alone proves neither kernel compatibility nor approval to load.

<a id="separates-testsystem-voraussetzungen-noch-keine-installation"></a>

## Separate test system: prerequisites, no installation yet

A test volume reduces the risk to production files, but it does not make a kernel experiment risk-free. The i9-14900KF has no usable integrated graphics for an alternative display output. A black screen, kernel panic, boot loop, and loss of unsaved test data remain possible.

- Use a separate **System/Data volume group**, preferably on a separate drive, with the exact matching Tahoe build. Another data volume or a shared Data volume is insufficient.
- First qualify the complete unchanged 2.5.4 state on this test system. Demonstrate that an independent boot into the known production installation or a recovery boot works before the diagnostic experiment.
- Leave the production EFI unchanged. An independent, known-working OpenCore recovery drive is preferable to assuming that switching volumes alone guarantees recovery. Retain the already working security settings; do not bypass additional checks.
- EFI/OpenCore and NVRAM are not automatically isolated by volume. NullMoth uses global `nullmoth-remove`, `nullmoth-nv-starts`, and `nullmoth-nv-ok` values; diagnostic boot arguments may also persist. A test boot can therefore affect the next production boot.
- Do not change cables or matrix/switch inputs during A/B. The diagnostic patch does not guarantee an identical connection generation between passes.

The subsequent deployment procedure must be checked separately in advance: identify the test volume group unambiguously, replace NVRM selectively only there, preserve its third-party kexts and 2.5.4 components, build the Auxiliary KC against its exact Boot/System KCs, and inspect it before publishing it. Final `_PrelinkBundlePath` values must point to permanent paths in the test system, not to a temporary preflight directory.

**The existing standard installer is not a selective tool for this purpose:** `package/install.sh:6–9` changes only the source with `--payload`; lines 11–18 and 449 address `/Library`, `/System`, and `--volume-root /`. The installer replaces all four kexts and additional components. Do not run it on the production system expecting it to update a different volume or only NVRM. Running the script from a different working directory does not change its target either. Do not additionally inject the driver from `EFI/OC/Kexts`.

No unchecked `sudo cp`/`kmutil create`/offline installation command is deliberately provided here. Such a step is useful only once a verifiable artifact and a qualified test system are available.

<a id="rückweg-zum-funktionierenden-254"></a>

## Returning to working 2.5.4

Before the subsequent experiment, save the following on separate, accessible storage: the complete original 2.5.4 package with unchanged checksums, original NVRM, other kexts and their metadata, GPU bundles/NVK, firmware, accelerator caches, version record, matching Auxiliary KC, and the known EFI configuration. File backups must preserve signatures, extended attributes, permissions, and links. Package changes must be identifiable as separate diagnostic artifacts with their own manifest; do not rewrite original checksums.

The preferred recovery path is the untouched production system through its known boot path. Before booting it, account for the fact that boot arguments and NullMoth NVRAM states are global. A `nullmoth-remove` switch must not be presented as a recovery feature that affects only the test volume.

To reset the test system, restore its complete known 2.5.4 state. Reuse an old KC only if the macOS build and the other kexts match; otherwise regenerate it from the restored permanent files and verify it. A selective NVRM change on the test system requires a recovery path that has been checked to be equally selective. Removing `nvyuv420diag` stops the additional queries but does not replace the diagnostic binary.

**Important limitation of the existing uninstaller:** `package/uninstall.sh <Backup>` does not restore a previous version in the examined revision. Lines 49–50 only check the argument; the script then removes the components and firmware. The installer output `Undo: ... uninstall.sh $BK` must therefore not be treated as a guarantee that it will restore 2.5.4. The automatic Lastgood logic is a different mechanism and does not replace a recovery path that has been checked in advance.

<a id="bereits-geprüft-und-weiterhin-offen"></a>

## Checks completed and work still outstanding

- Checked: Git differences between 2.5.4 and the fork baseline, available build scripts and their required inputs, public NVIDIA build targets and resolution of the 615 tag, installation/recovery paths in the source code.
- Already checked during diagnostic development: twelve Linux scenarios, syntax/format checks of both complete modified NVIDIA C files with real 615 headers, diagnostic patch application/reversal, and application after the 615 viewport patch. This is not a Mach-O/Darwin build.
- Outstanding: a complete Darwin baseline build matching the release, toolchain/SDK pins, final kext linking and metadata generation, binary comparison, loadability, and boot on the RTX 5080.
- Outstanding: a separate test volume group that has been qualified in practice and its selective deployment/recovery process. None of that has been set up or installed here.

The original next requirement was a **complete 615 Darwin build tree or its reproducible recipe and release patch manifest**. The audited NvGfx harness now supplies a reconstructed public recipe, and the isolated reference workflow supplies its pinned baseline patch manifest. The next concrete steps are the CLT/SDK preflight, a complete unchanged reference build, and comparison against the known-working official 2.5.4 bundle. Exact release metadata, binary compatibility and hardware qualification remain open; see [NVRM-615-REFERENCE-BUILD.md](NVRM-615-REFERENCE-BUILD.md).
