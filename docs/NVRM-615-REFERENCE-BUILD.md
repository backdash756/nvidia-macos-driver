# Isolated NVRM 615 reference build for NullMoth 2.5.4

The public NvGfx Darwin harness closes the previously missing RM/NVKMS archive and final NVRM link recipe. A complete source-based reference build can now be attempted without obtaining unpublished prebuilt NVIDIA archives. This branch prepares that attempt; **the complete NVRM compilation/link, official 2.5.4 binary comparison and hardware validation remain untested here**. The local macOS capability-probe results reported below are separate from a complete NVRM build.

The build is deliberately reference-only. It uses the pinned NullMoth 2.5.4 sources, the existing display-completion/validation-log/viewport/backlight patches, and NVIDIA 615.78.08. It does not include our YUV420 diagnostic or NvGfx's laptop power/physical-control modifications. The source `0.1` KMOD and plist versions remain unchanged. The output is a development artifact, not an official 2.5.4 package or an installation-ready replacement.

## Audited inputs and evidence

| Input | Pinned revision |
|---|---|
| NullMoth 2.5.4 source | `46779055f68208baa5ddfb9b2e9c77ed028e1d59` |
| NVIDIA tag `615.78.08` | Commit `1a82fbfda8f321b056eb3568850f9316c5e465c5`; annotated tag object `4bfe30bb78e7364959e59c16cfefd88e8c14670e` |
| NvGfx `nvgfx-2.5.3` | [`0eff8317443f7af55dc4b4fd333173819859799c`](https://github.com/watchthelight-labs/NvGfx/tree/0eff8317443f7af55dc4b4fd333173819859799c) |
| Original diagnostic branch | `8617eb1e7caf5db3fedbfaf710a2149f4caa75ea`; this work is on the separate `build/nvrm-615-reference` branch |
| Official comparison input | A complete, backed-up, known-working official 2.5.4 `NVRM.kext`; its actual binary/plist hashes are recorded locally |

NvGfx's [NVRM-HARNESS.md](https://github.com/watchthelight-labs/NvGfx/blob/0eff8317443f7af55dc4b4fd333173819859799c/build/nvgfx/NVRM-HARNESS.md) describes the **older 610.57.04 / NullMoth 2.1.0** reconstruction. Its 1055/225 object counts, 29376/29431 normalized function comparison, CLT/SDK qualification and “not loaded” status must not be presented as 615 results.

The newer 615 evidence is separate:

- [`34d2677`](https://github.com/watchthelight-labs/NvGfx/commit/34d2677da3f78c51f6ddc80c3b18b0f72c8dee61): switch the harness to NVIDIA 615.78.08.
- [`cd9de5a`](https://github.com/watchthelight-labs/NvGfx/commit/cd9de5a2c7170fa3f858fb3aff9690f0faaf0b86): include the missing 615 viewport patch.
- [`95ba277`](https://github.com/watchthelight-labs/NvGfx/commit/95ba277ca6de1296fe78d8ccb1cba50bd8dc733b): remove the Linux return-thunk flag. The commit reports 30875 of 30884 normalized functions matching the shipped 2.5.3 image, with the remaining differences attributed to fork power code and two KAPI helpers.
- [CHANGELOG.md, 2.5.3-nvgfx.1](https://github.com/watchthelight-labs/NvGfx/blob/0eff8317443f7af55dc4b4fd333173819859799c/CHANGELOG.md#253-nvgfx1-fork-booted-2026-10-22): reports a source-built 615 NVRM boot on a Legion 5 / RTX 5070 Laptop / Tahoe 26.7.1, Metal 3, 165 Hz and 13-second GSP bring-up, with NvGfx's laptop power additions.

These are meaningful **author-reported** build/comparison/boot results. Complete 615 build manifests, binary hashes, comparison transcripts and boot logs are not included in that checkout. They have not been independently reproduced here and do not establish RTX 5080 or official 2.5.4 compatibility.

## What the harness supplies

The audited [build_nvrm.sh](https://github.com/watchthelight-labs/NvGfx/blob/0eff8317443f7af55dc4b4fd333173819859799c/build/nvgfx/build_nvrm.sh) and [darwin.mk](https://github.com/watchthelight-labs/NvGfx/blob/0eff8317443f7af55dc4b4fd333173819859799c/build/nvgfx/nvrm/darwin.mk) supply the previously missing pieces:

| Stage | Concrete recipe and local adaptation |
|---|---|
| NVIDIA objects | Reuse NVIDIA's source/object rules with `TARGET_OS=Darwin TARGET_ARCH=x86_64`; add `-arch x86_64 -mmacosx-version-min=15.0 -mkernel -DNV_UNIX -DNV_BSD`. Our runner additionally pins the selected SDK with `-isysroot` and records all commands. |
| Darwin archives | Replace NVIDIA's ELF final processing with Apple `libtool -static` to create `libnvkernel.a` and `libnvkms.a`. Do not run the GNU `ld`/`objcopy` final targets. |
| 615 return mitigation | Remove `-mfunction-return=thunk-extern`; XNU provides no corresponding Linux `__x86_return_thunk`, and the NvGfx 615 recipe removes it too. No display safety/bandwidth checks are bypassed. |
| Shader blobs | Set `SHADER_OBJS=`; the existing, unchanged `nvkms_shaders.cpp` supplies the binary shader symbols instead of the GNU binary-to-object rule. |
| Compile records | Save `make -n -B reference-objs` compile lines, requiring one unique output per object and exact agreement with the object manifest. |
| Display patches | Apply the strict, normalized baseline patch series once before compilation. The resulting `libnvkms.a` is already the paired display-completion archive; no second completion helper or second patch application is needed. |
| XNU glue | Build the fixed ten-file C++ list below using the Kernel.framework headers, RM/NVKMS include paths and RM defines. |
| Internal C bridges | Build `nvrm_pageoff.c` using RM's `osapi.c` command and `nvrm_kapi_fields.c` using NVKMS's `nvkms-kapi.c` command, replacing source/object/dependency output arguments. |
| Final link | `clang++ -arch x86_64 -mmacosx-version-min=15.0 -isysroot <SDK> -fapple-kext -nostdlib -Xlinker -kext <objects> libnvkernel.a libnvkms.a -lkmodc++ -lkmod -lcc_kext`. |
| Bundle | Copy the unchanged source plist, ad-hoc sign the staged bundle with SHA-256 and require successful signature verification. This does not approve it for loading. |

C++ source list: `NVRM`, `os-xnu`, `os-xnu2`, `nv_xnu_stubs`, `nv_xnu_power`, `os-nvkms-xnu`, `nvkms_shaders`, `nvrm_fbinfo`, `nvrm_gpuva`, `nvrm_surfshare` (all `.cpp`).

Final object order: `nv_xnu_stubs`, `nvkms_shaders`, `nvrm_fbinfo`, `nvrm_gpuva`, `nvrm_surfshare`, `NVRM`, `os-nvkms-xnu`, `os-xnu`, `os-xnu2`, `nv_xnu_power`, `nvrm_kapi_fields`, `nvrm_pageoff`, then the RM and NVKMS archives. C++ flags include `-fapple-kext -mkernel -nostdinc`, the kernel defines, C++17, `-fno-rtti -fno-exceptions -fno-builtin -fno-common -O2`. Full argv and working directories are retained in the output.

## Patch order, source boundaries and excluded changes

[patches/README.md](../build/nvrm615/patches/README.md) and [patch-proof.json](../build/nvrm615/patches/patch-proof.json) record exact original/normalized hashes and final source hashes.

1. `615-display-completion.patch` (existing `610-display-completion.patch`, context normalized for 615).
2. `615-setmode-reason.patch` (existing setmode-reason patch).
3. `615-viewport-scaling.patch`.
4. `615-backlight-register-log.patch` (separate `nvkms-rm.c` target).

The original third completion hunk needs GNU patch **fuzz 1** because its preceding function changed in 615. The normalized patches change only context/positions; their complete output files are byte-identical to NvGfx's original ordered sequence. `git apply --check` rejects a duplicate application. The runner verifies source pins, a clean vendor input, original patch provenance, normalized patch hashes and the exact final patched source hashes. Existing vendor or NullMoth working trees are never patched or overwritten.

NVIDIA 615 already includes the `getFlipPendingStatus` table member; completion initializes it and implements the notifier query/reset behavior. No new public KAPI field is added. The 610 FSP cleanup patch is not used. In 615, the FSP poll-event guard/NULL assignment is upstream, but the separate timer-handle-clearing hunk is not; the reference preserves NVIDIA's pinned RM rather than adding cleanup changes.

NvGfx's `nvrm_power.cpp`, `nvrm_physctl.c`, physical-control ABI and NVRM entry points, and GPS/NVPCF/TGP changes in `os-xnu.cpp`/`os-xnu2.cpp` are excluded. NullMoth's existing `nv_xnu_power.cpp` is retained. No fork files are automatically discovered or linked.

Between the local 2.5.4 commit and the 2.6.2 fork baseline, only NVAccel changes under `kexts/NVRM`; the NVRM core sources and relevant ABI headers are unchanged. The existing diagnostic branch additionally changes the XNU diagnostic hook. Extracting the pinned 2.5.4 Git objects, rather than building the current worktree, excludes that hook even though this branch retains the diagnostic files for later work.

## CLT, SDK and kernel-header prerequisites

Full Xcode is **not a demonstrated requirement**. NvGfx explicitly used Command Line Tools for its older reconstruction: Apple clang 17.0.0 (`clang-1700.0.13.5`), SDK 15.5 with `Kernel.framework`, GNU make 3.81 and Python 3. The newer 615 report does not independently pin its compiler/SDK. Do not assume that every newer CLT SDK supplies the same headers or that the old qualification proves a 615 build.

Our preflight checks the installed tools and actual selected SDK. Selection order is `--sdk`, then `SDKROOT`, then the SDK returned by `xcrun`. It does not require `xcodebuild`, switch the developer directory or download a KDK. `DEVELOPER_DIR`, if already set, is respected and recorded.

Required capabilities include Apple `clang`/`clang++`, Darwin `ld`, `ar` and `libtool`, `make`, Python 3.8 or newer, Git, and the Mach-O/signature inspection tools used by the helpers. The selected SDK must supply:

- `System/Library/Frameworks/Kernel.framework/Headers`, including the IOKit service/user-client/PCI/ACPI, libkern/OSAtomic/crypto, kernel thread/task/locks, Mach, BSD and pexpert headers used by the XNU glue. The complete checked list is `KEY_HEADERS` in [preflight.py](../build/nvrm615/preflight.py).
- An x86_64 kernel C++ compile environment with the kernel/private defines and C++17 flags.
- Kernel support libraries resolving `-lkmodc++ -lkmod -lcc_kext` for the selected SDK and deployment target 15.0.

Header existence alone is insufficient. The preflight syntax-checks and compiles a small kernel C++ source, links a kext-format Mach-O, and checks its x86_64 architecture and kext file type. It never executes or loads that output. Success qualifies those capabilities, not all NVIDIA sources or the running Tahoe kernel. If it fails, inspect its exact missing header/compiler/linker error before choosing another SDK or developer-tools installation. No general NVRM-only KDK requirement is established; the earlier 26.7.1 KDK mention concerns NVAccel vtable reconstruction.

Firmware, NVAccel, NVRMFB, NVRMAGDC, Mesa/NVK, Rust and the shader translator do not have to be rebuilt for this reference. Existing GSP 615 firmware remains a later runtime prerequisite, not a generated build input.

### Mach-O header correction and reported macOS checks

On Tahoe 26.7.1 / x86_64, the user confirmed successful SDK/header checks, kernel C++ syntax checking, probe-object compilation, kernel Mach-O linking and `lipo` architecture checking. An independent `nm -u` check also returned zero. The original preflight stopped solely because Apple's actual verbose header prints `KEXTBUNDLE`, while the check expected `KEXT_BUNDLE`:

```text
magic       cputype  cpusubtype  caps  filetype    ncmds
MH_MAGIC_64 X86_64   ALL         0x00  KEXTBUNDLE 7
```

[macho.py](../build/nvrm615/macho.py) now supplies the single header parser and kext-type predicate used by both preflight and comparison. They read the actual `filetype` column of exactly one valid header table; filenames and unrelated text are not type evidence. Accepted representations are `KEXTBUNDLE`, `KEXT_BUNDLE`, `MH_KEXT_BUNDLE`, and numeric `MH_KEXT_BUNDLE` value 11 / `0xb` (including equivalent hexadecimal formatting). Executables, dylibs, ordinary bundles and malformed/ambiguous headers remain rejected.

After recognizing the header, the preflight still requires a successful `nm -u` call before recording artifact hashes and `status: passed` in `preflight.json`. A symbol-tool failure is not ignored. The user subsequently confirmed a complete successful preflight on Tahoe 26.7.1 / x86_64 with SDK 26.5, including this symbol check. Linux regression tests also simulate the complete tool sequence and saved success/failure reports; they do not run Apple's compiler or tools. The user's local results are reported observations, not a newly executed macOS test of the current build fixes.

## First real Darwin RM build: fixes and evidence

Starting from `8901170588d721f5681852a5c38b2f52a6bdd41c`, the user successfully prepared `reference-002` and `reference-003`. The first attempt stopped after preflight because the path guard rejected Homebrew Python `/usr/local/opt/python@3.15/bin/python3.15`. With `/usr/bin/python3` (3.9.6), the second attempt built `libnvkernel.a` and then stopped at object-manifest comparison. No NVKMS archive or complete NVRM link was reached.

The reported RM archive is approximately 20 MB and has 1101 `ar -t` entries: 1100 object files and `__.SYMDEF`, Apple's archive symbol index. That is consistent with the expected RM object count, but does not establish member architecture, symbol-index correctness or linkability. The archive itself was not supplied to this Linux environment and has not been inspected here.

The backed-up official reference at `~/nvrm-reference/2.5.4/NVRM.kext` was reported as bundle ID `com.nullmoth.NVRM`, version `2.5.4`, strictly signature-verified and byte-identical to the installed binary. Its reported binary SHA-256 is `4591c5bd732838417168b366380c9a0eefdd4ad940d3eed3bfff22f6ade2e0f7`. The helpers only read that reference; they never replace, resign or alter it.

### Path validation at the actual expansion boundary

| Path role | Treatment |
|---|---|
| SDK and `clang`, `clang++`, `ld`, `ar`, `libtool` paths | Retain the strict existing Make/shell character restriction. These paths enter NVIDIA Make variables or shell-expanded compiler flags. |
| Python interpreter, `make` executable, `codesign`, direct preflight/comparison tools | Launched as subprocess argument lists. They are not subjected to a Make expansion restriction merely because their paths are recorded in the preflight report. Homebrew Python's `@` is valid here. |
| Staged source/object paths | Retain `output_path()` restrictions. Compilation reads the owned snapshot, not the original working tree. |
| Original vendor/repository inputs | Passed as Git arguments to materialize pinned sources. Existing inputs are not patched or overwritten. |

The fix narrows validation to paths actually expanded by Make; it does not broaden the Make-safe character whitelist. `$`, shell metacharacters and problematic whitespace remain rejected at that boundary. All direct launches still use argv lists with no shell.

### Why Apple's linker output became six extra objects

NVIDIA `utils.mk:604` immediately evaluates `LD_IS_LLD := $(if $(filter LLD,$(shell $(LD) -v)),1)` when either component's Makefile is read. GNU make captures the probe's stdout; Apple's `ld -v` writes its six-line version banner to stderr. The original recorder merged stderr into stdout and returned the result to the manifest parser. Thus RM's 1100 real paths became 1106 lines. The same path affects NVKMS: Linux reproduction with the real Makefiles and a simulated Apple linker yields 225 paths plus the same six stderr lines. This is not a successful macOS NVKMS build.

The recorder now writes stdout to `logs/<stage>.log` and stderr to `logs/<stage>.stderr.log`, records both paths in `commands.jsonl`, and returns only stdout for data parsing. Neither channel is discarded. Failed commands still abort and name both logs. No NVIDIA source change, linker-probe override or banner whitelist is needed; an override in the second-loaded overlay would be too late for that immediate probe.

The object validator requires exact multisets, one compiler output and manifest entry per object. It rejects missing, extra and duplicate objects, empty inventories, malformed paths and **all unknown nonempty stdout lines**, including the familiar Apple banner if it were actually emitted on stdout. `*-manifest-check.json` retains the complete differences; the terminal error includes counts and bounded examples. Compiler return failures remain fatal. The object count comes from the pinned Makefile inventory, not a hard-coded 1100/225 acceptance rule.

### Archive member validation

After each component's object-manifest check, [archives.py](../build/nvrm615/archives.py) validates the archive before the next build stage. It reads native Darwin ar containers, including BSD extended names, requires every object to be thin little-endian Mach-O64 / x86_64 / `MH_OBJECT`, checks header/load-command bounds and compares exact member basenames against the expected object inventory. Apple's `__.SYMDEF` / sorted / 64-bit index variants are reported separately and never counted as NVIDIA objects. Missing, extra or duplicate objects, unsupported formats and wrong architectures stop the build. `*-archive-check.json` records the result.

These are container/header/inventory checks. They do not validate the symbol-index contents, relocations, internal ABI or final linkability. Those require the real Darwin link and reference comparison, which remain open.

For a read-only inspection of the already created RM archive and its existing libtool file list, without resuming or modifying the old build:

```bash
python3 build/nvrm615/archives.py \
  --archive "$HOME/nvrm-builds/reference-003/ogkm/src/nvidia/_out/Darwin_x86_64/libnvkernel.a" \
  --objects "$HOME/nvrm-builds/reference-003/ogkm/src/nvidia/_out/Darwin_x86_64/libnvkernel.a.filelist"
```

The command prints JSON to stdout and writes no files. Preserve `reference-002`, `reference-003` and the official reference bundle unchanged. Do not delete outputs, alter their manifests/build status, or continue a started build after these script fixes.

## Commands: preparation and build only

Review the committed helpers before running them. Use an ordinary account without `sudo`. All output goes into a new dedicated directory outside either source checkout; existing outputs are not overwritten or recursively cleaned. Use simple absolute output paths, and SDK/Make-expanded tool paths without spaces or shell/make metacharacters. This restriction does not apply to metadata-only/direct-argv interpreter paths such as Homebrew Python with `@`. No helper installs, loads, creates Kernel Collections, or changes EFI/NVRAM/SIP.

To update an existing checkout manually, run these commands from its repository root. They do not rewrite divergent history; a non-fast-forward update stops instead:

```bash
git switch build/nvrm-615-reference
git pull --ff-only origin build/nvrm-615-reference
```

For an early CLT/SDK capability check on the Mac, before the larger build:

```bash
mkdir -p "$HOME/nvrm-builds"
python3 build/nvrm615/preflight.py \
  --output "$(mktemp -d "$HOME/nvrm-builds/clt-check.XXXXXX")"
```

For a specific already installed SDK, append `--sdk /absolute/path/to/MacOSX.sdk`. A new output directory is required for another attempt; the command above creates a unique empty directory, which the preflight accepts. Its printed path identifies the resulting `preflight.json`.

Obtain the NVIDIA source in a separate directory; a clone is a download, not an installation:

```bash
git clone --depth 1 --branch 615.78.08 https://github.com/NVIDIA/open-gpu-kernel-modules.git "$HOME/nvrm-inputs/open-gpu-615"
git -C "$HOME/nvrm-inputs/open-gpu-615" rev-parse HEAD
```

The resolved commit must match the NVIDIA pin above. The pinned NullMoth source commit must also already exist in local Git history. The user's earlier shallow-clone issue has been resolved locally; the helpers perform no automatic fetch, unshallow operation or history rewrite.

**After updating the build helpers, prepare a new reference output directory.** Preparations made with earlier helper versions intentionally fail the recipe-hash check. Do not edit old manifests or reuse partial builds; retain their logs and choose a fresh path such as `reference-004` for the example below. Existing `reference-002` and `reference-003` are evidence and must remain unchanged. The unchanged single-attempt build policy still applies.

From this branch's repository root (the example requires a directory that does not yet exist):

```bash
python3 build/nvrm615/build.py prepare \
  --vendor "$HOME/nvrm-inputs/open-gpu-615" \
  --output "$HOME/nvrm-builds/reference-004"
```

Preparation also works under Linux. It materializes independent Git archives, not shared clones or dirty working-tree files, and applies the baseline patches strictly. It records every staged input hash and the build-recipe hashes in `inputs.json`. It does not compile. To compile later, run preparation on the Mac rather than moving its path-bound output directory.

After providing a complete backed-up official 2.5.4 reference bundle outside the output tree:

```bash
python3 build/nvrm615/build.py build \
  --output "$HOME/nvrm-builds/reference-004" \
  --reference "$HOME/nvrm-reference/2.5.4/NVRM.kext" \
  --jobs 4
```

Append `--sdk /absolute/path/to/MacOSX.sdk` if needed. The build performs its own preflight, refuses changed inputs/recipe hashes, builds both archives and the fixed glue list, links and signs the staged candidate, then **requires** the static comparison; a missing reference is an error. A build directory permits one attempt only, avoiding stale SDK/flag/object reuse. For a failed attempt, retain its logs and prepare a new output directory.

The corrected macOS preflight and the full build/compare command paths have not been executed here. Do not interpret Linux preparation success as a completed Darwin build.

## Outputs, metadata and comparison

| Path within the output directory | Purpose |
|---|---|
| `inputs.json` | Full source pins, baseline patch order, recipe and source hashes; diagnostics disabled |
| `nullmoth/kexts/NVRM/` | Exact 2.5.4 source snapshot, including unchanged source `0.1` metadata |
| `ogkm/` | Independent NVIDIA 615 source snapshot with the baseline display patches |
| `ogkm/src/nvidia/_out/Darwin_x86_64/libnvkernel.a` | RM Mach-O archive |
| `ogkm/src/nvidia-modeset/_out/Darwin_x86_64/libnvkms.a` | Paired, already baseline-patched NVKMS Mach-O archive |
| Each component's `compile_cmds.sh` and top-level `*-objects.json` | Recorded compile templates and object manifests |
| `preflight/preflight.json` | Selected SDK/header/tool hashes and versions, probe commands/results |
| `glue/`, `logs/`, `commands.jsonl` | C/C++ objects; separate `<stage>.log` stdout and `<stage>.stderr.log` diagnostics; explicit commands, working directories and both log paths |
| `*-manifest-check.json`, `*-archive-check.json` | Strict object-list differences and archive member/architecture validation results |
| `NVRM.kext` | Ad-hoc-signed reference candidate; not installed or hardware-qualified |
| `artifacts.json`, `build-state.json` | Artifact hashes and attempt/comparison state |
| `comparison/` | Mandatory official-reference static report and raw tool output |

The old completion helper is intentionally not run. Its hard-coded 610.57.04 identification string would mislabel a 615 build and attempt the patches again. We retain NVIDIA's generated 615 identification metadata, using fixed build user/host and a source-derived date. Those ID strings, the original source `0.1` KMOD/plist metadata, signing/UUID, compiler, SDK and layout may differ from the release. All differences must be reviewed; no candidate is presented as byte-identical or silently relabeled as official 2.5.4. A separately verified release-metadata preparation is still needed before any later installation-ready artifact.

The comparison records full binary/plist hashes, architecture/file type, **detailed** load commands, dependencies, signatures, imports, exports, all symbol-name multiplicities, and uniquely named function address spans. It rejects critical discrepancies such as new kernel imports, missing reference exports, wrong ID/dependencies/architecture/type or invalid signatures. Version and layout differences are explicitly reported for human review. A function address span includes padding; it is not a decoded code-equivalence result. No optional MacPorts LLVM installation is required.

NvGfx's original report tools are useful evidence but not an automatic compatibility gate: their “identical load commands” checks only command types/counts, missing references can be skipped, and normalized disassembly comparison ignores missing/duplicate functions and some data-address differences. Our comparison likewise cannot prove internal KAPI layout, semantic equivalence or loadability. The consumer's KAPI table and other 2.5.4 dependencies require separate review before a boot test. Functional reproducibility is the immediate goal; bit-for-bit reproducibility across toolchains/signing metadata is not claimed.

## Validation and the next diagnostic step

Linux checks cover Python/CLI guards, actual pinned-source extraction, strict patch order/final hashes, duplicate/tamper rejection, diagnostic exclusion, and compile-command/template parsing. The existing diagnostic patch can be checked for later applicability without applying it to this reference. The corresponding test command is:

```bash
python3 tools/test_nvrm615_macho.py
python3 tools/test_nvrm615_archives.py
python3 tools/test_nvrm615_manifest.py --vendor /absolute/path/to/open-gpu-615
python3 tools/test_nvrm615_build.py --vendor /absolute/path/to/open-gpu-615
```

The original baseline Linux run passed 39 tests, including real Makefile dry runs covering 1100 RM and 225 NVKMS objects and native syntax/format checks of both complete baseline-patched C files. These Makefile checks use the Linux host compiler and do not qualify Apple Clang flags or Mach-O output. The command output is the authoritative test result. The current Linux validation passed 85 tests: 39 baseline, 14 Mach-O, 15 path/manifest/channel and 17 archive tests. The Mach-O regressions cover the real Apple header, accepted constant representations, negative file types, malformed headers, comparison decisions and the complete simulated preflight through symbol checking and JSON output. The corrected preflight has now been reported successful on the user's Mac. The current path/stream/archive fixes, NVKMS compilation, complete NVRM link/signing, official 2.5.4 comparison, boot and RTX 5080 display behavior remain untested on macOS here. An author-reported RTX 5070 laptop boot is not a substitute for these checks.

The new regression suites additionally test actual direct-argv execution through an `@` interpreter path, unsafe Make paths, the exact six-line Apple linker banner with both real pinned Makefiles, 1100/225 object inventories and all mismatch cases, subprocess failures, and synthetic archive format/architecture failures. Simulated Apple-linker and synthetic archive tests are not Darwin compilation results.

Only after the unchanged reference compiles and its official 2.5.4 comparison has been reviewed should a separate diagnostic output be prepared. The later change is small: use the same baseline/toolchain, add the existing vendor diagnostic patch, pair it with the existing `os-nvkms-xnu.cpp` hook, rebuild the affected NVKMS objects/archive and XNU hook, and relink NVRM. The present runner has no diagnostic-enable option and does not perform that integration automatically.

No deployment or boot commands are provided here. The separate System/Data test volume group, independently tested recovery boot, backed-up 2.5.4 set, selective NVRM deployment/rollback and global NVRAM cautions remain necessary as documented in [NVRM-DIAGNOSE-BUILD.md](NVRM-DIAGNOSE-BUILD.md). Existing installers are not selective NVRM/test-volume installers; the uninstaller does not restore 2.5.4. Removing diagnostic boot arguments alone does not restore an installed binary.

To undo this preparation, stop using the new build branch and keep or remove only its known, dedicated output directories after reviewing their paths. Nothing from this process needs uninstalling: installed kexts, other driver components, firmware, EFI, NVRAM, SIP and Kernel Collections have not been changed. The original diagnostic branch remains at its previous commit.
