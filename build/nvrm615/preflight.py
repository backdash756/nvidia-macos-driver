#!/usr/bin/env python3
"""Check the selected Darwin CLT/SDK with an isolated x86_64 kext build probe.

This does not install, load, approve, or modify any driver. All generated files
stay in a newly created or empty --output directory. Full Xcode is not required:
the selected developer directory must provide the tools, headers, and libraries
actually used by the probe. A successful probe is not a complete NVRM build or
proof that a resulting driver can be loaded on a particular macOS installation.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys


MINIMUM_MACOS = "15.0"
KEY_HEADERS = (
    "IOKit/IOLib.h",
    "IOKit/IOLocks.h",
    "IOKit/IOService.h",
    "IOKit/IOUserClient.h",
    "IOKit/IOBufferMemoryDescriptor.h",
    "IOKit/IOSubMemoryDescriptor.h",
    "IOKit/IOPlatformExpert.h",
    "IOKit/IOWorkLoop.h",
    "IOKit/IOFilterInterruptEventSource.h",
    "IOKit/pci/IOPCIDevice.h",
    "IOKit/acpi/IOACPIPlatformDevice.h",
    "libkern/libkern.h",
    "libkern/OSAtomic.h",
    "libkern/OSDebug.h",
    "libkern/c++/OSObject.h",
    "libkern/crypto/sha1.h",
    "mach/kmod.h",
    "mach/mach_types.h",
    "mach/mach_time.h",
    "mach/semaphore.h",
    "mach/vm_param.h",
    "kern/thread.h",
    "kern/thread_call.h",
    "kern/task.h",
    "kern/clock.h",
    "kern/locks.h",
    "kern/sched_prim.h",
    "sys/proc.h",
    "sys/kauth.h",
    "sys/sysctl.h",
    "sys/systm.h",
    "sys/random.h",
    "sys/vnode.h",
    "sys/fcntl.h",
    "sys/uio.h",
    "pexpert/pexpert.h",
)

PROBE_SOURCE = r"""// Build-only capability probe. This module is never installed or loaded.
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/pci/IOPCIDevice.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOSubMemoryDescriptor.h>
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IOWorkLoop.h>
#include <IOKit/IOFilterInterruptEventSource.h>
#include <IOKit/acpi/IOACPIPlatformDevice.h>
#include <libkern/OSAtomic.h>
#include <libkern/OSDebug.h>
#include <libkern/crypto/sha1.h>
#include <kern/thread.h>
#include <kern/thread_call.h>
#include <kern/task.h>
#include <kern/clock.h>
#include <kern/locks.h>
#include <kern/sched_prim.h>
#include <pexpert/pexpert.h>

extern "C" {
#include <mach/mach_types.h>
#include <mach/kmod.h>
#include <mach/mach_time.h>
#include <mach/semaphore.h>
#include <mach/vm_param.h>
#include <sys/proc.h>
#include <sys/kauth.h>
#include <sys/sysctl.h>
#include <sys/systm.h>
#include <sys/random.h>
#include <sys/vnode.h>
#include <sys/fcntl.h>
#include <sys/uio.h>

kern_return_t _start(kmod_info_t *, void *) { return KERN_SUCCESS; }
kern_return_t _stop(kmod_info_t *, void *) { return KERN_SUCCESS; }
KMOD_EXPLICIT_DECL(com.nullmoth.NVRMPreflight, "0.0.0", _start, _stop)
__private_extern__ kmod_start_func_t *_realmain = NULL;
__private_extern__ kmod_stop_func_t *_antimain = NULL;
__private_extern__ int _kext_apple_cc = __APPLE_CC__;
}

class NVRMPreflightService : public IOService {
    OSDeclareDefaultStructors(NVRMPreflightService)
};
OSDefineMetaClassAndStructors(NVRMPreflightService, IOService)
"""


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def staging_directory(value):
    raw = Path(value).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise RuntimeError("--output must be absolute and must not contain '..' components")
    path = raw.absolute()
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise RuntimeError("--output must not use symlinks: " + str(candidate))
    forbidden = (
        "/Library", "/System", "/Applications", "/bin", "/sbin", "/usr",
        "/etc", "/private/etc", "/dev", "/proc", "/sys", "/boot", "/EFI",
    )
    if len(path.parts) < 3 or path == Path.home().absolute():
        raise RuntimeError("--output must be a dedicated staging subdirectory")
    if any(path == Path(root) or Path(root) in path.parents for root in forbidden):
        raise RuntimeError("--output must not be a system path: " + str(path))
    folded_parts = tuple(part.casefold() for part in path.parts)
    if any(part in ("efi", "system", "library") or part.endswith(".kext") for part in folded_parts):
        raise RuntimeError("--output must not enter EFI, System, Library, or a kext, including on mounted volumes")
    if (folded_parts[1] in ("applications", "bin", "sbin", "usr", "etc", "dev", "proc", "sys", "boot")
            or folded_parts[1:3] == ("private", "etc")):
        raise RuntimeError("--output must not be a system path: " + str(path))
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise RuntimeError("--output must be absent or an empty directory")
        if path.stat().st_uid != os.getuid():
            raise RuntimeError("--output must be owned by the current user")
    else:
        path.mkdir(parents=True)
    return path


def command(report, argv, *, cwd, env, check=True, timeout=120):
    argv = [str(arg) for arg in argv]
    result = subprocess.run(
        argv, cwd=str(cwd), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
    )
    report["commands"].append({
        "argv": argv, "cwd": str(cwd), "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    })
    if check and result.returncode:
        raise RuntimeError(
            "Command failed: " + " ".join(shlex.quote(arg) for arg in argv)
            + "\n" + result.stderr + result.stdout
        )
    return result


def perform_checks(report, output, sdk_argument):
    # Match the main build's system-tool PATH and exclude user compiler/linker
    # injection variables. Keep developer selection local to this subprocess.
    env = {key: os.environ[key] for key in ("HOME", "DEVELOPER_DIR", "SDKROOT") if key in os.environ}
    temporary = output / "tmp"
    temporary.mkdir()
    env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", LANG="C", LC_ALL="C", TZ="UTC", TMPDIR=str(temporary))
    xcrun = shutil.which("xcrun", path=env["PATH"])
    if not xcrun:
        raise RuntimeError("xcrun is unavailable in the selected developer tools")
    xcrun = str(Path(xcrun).resolve())
    report["developer_dir_override"] = env.get("DEVELOPER_DIR")
    report["sdkroot_override"] = env.get("SDKROOT")
    if sdk_argument:
        sdk = Path(sdk_argument).expanduser().resolve(strict=True)
        report["sdk_selection"] = "--sdk"
    elif env.get("SDKROOT"):
        sdk = Path(env["SDKROOT"]).expanduser().resolve(strict=True)
        report["sdk_selection"] = "SDKROOT"
    else:
        sdk = Path(command(
            report, [xcrun, "--sdk", "macosx", "--show-sdk-path"],
            cwd=output, env=env,
        ).stdout.strip()).resolve(strict=True)
        report["sdk_selection"] = "xcrun default macosx SDK"
    if not sdk.is_dir():
        raise RuntimeError("The selected SDK is not a directory: " + str(sdk))
    env["SDKROOT"] = str(sdk)
    report["sdk"] = {
        "path": str(sdk),
        "version": command(
            report, [xcrun, "--sdk", str(sdk), "--show-sdk-version"],
            cwd=output, env=env,
        ).stdout.strip(),
        "metadata": {},
    }
    for name in ("SDKSettings.json", "SDKSettings.plist", "System/Library/CoreServices/SystemVersion.plist"):
        path = sdk / name
        if path.is_file():
            report["sdk"]["metadata"][name] = {"sha256": sha256(path)}
            if name.endswith(".json"):
                report["sdk"]["metadata"][name]["contents"] = json.loads(path.read_text())
    kernel_headers = sdk / "System/Library/Frameworks/Kernel.framework/Headers"
    report["sdk"]["kernel_headers"] = str(kernel_headers)
    report["sdk"]["key_header_sha256"] = {}
    missing = []
    for name in KEY_HEADERS:
        path = kernel_headers / name
        if path.is_file():
            report["sdk"]["key_header_sha256"][name] = sha256(path)
        else:
            missing.append(name)
    if missing:
        raise RuntimeError("Selected SDK lacks required Kernel.framework headers: " + ", ".join(missing))

    version_flags = {
        "clang": ["--version"], "clang++": ["--version"], "ld": ["-v"],
        "ar": ["-V"], "libtool": ["-V"], "ranlib": ["-V"],
        "nm": ["--version"], "otool": ["--version"], "lipo": ["-version"],
        "dwarfdump": ["--version"], "codesign": ["--version"],
        "plutil": ["-help"], "git": ["--version"], "make": ["--version"],
        "python3": ["--version"], "shasum": ["--version"], "file": ["--version"],
    }
    optional_tools = {"ranlib", "dwarfdump", "plutil", "shasum"}
    report["optional_tools_unavailable"] = []
    tools = report["tools"] = {}
    for name, flags in version_flags.items():
        if name == "python3":
            location = sys.executable
        elif name in ("git", "make", "shasum", "codesign", "plutil", "file"):
            location = shutil.which(name, path=env["PATH"])
        else:
            lookup = command(
                report, [xcrun, "--sdk", str(sdk), "--find", name],
                cwd=output, env=env, check=name not in optional_tools,
            )
            location = lookup.stdout.strip() if lookup.returncode == 0 else None
        if not location:
            if name in optional_tools:
                report["optional_tools_unavailable"].append(name)
                continue
            raise RuntimeError("Required tool is unavailable: " + name)
        # Keep the invocation basename: clang++ can be a symlink to clang, but
        # its driver mode depends on argv[0]. Hashing still follows the symlink.
        path = Path(location).absolute()
        if not os.access(path, os.X_OK):
            if name in optional_tools:
                report["optional_tools_unavailable"].append(name)
                continue
            raise RuntimeError("Required tool is not executable: " + str(path))
        # Apple tools have inconsistent version switches. Preserve the complete
        # response and exit code rather than treating a usage response as a build failure.
        tools[name] = {
            "path": str(path), "resolved_path": str(path.resolve(strict=True)),
            "sha256": sha256(path),
            "version_argv": flags, "optional": name in optional_tools,
        }
        try:
            version = command(report, [str(path), *flags], cwd=output, env=env, check=False, timeout=15)
            tools[name].update(version_exit_code=version.returncode,
                               version_output=(version.stdout + version.stderr).strip())
        except subprocess.TimeoutExpired:
            tools[name]["version_error"] = "Version query timed out; actual build capabilities are checked separately"
    report["tools"]["xcrun"] = {"path": xcrun, "sha256": sha256(xcrun)}
    source = output / "kernel-probe.cpp"
    obj = output / "kernel-probe.o"
    binary = output / "kernel-probe.kext-macho"
    source.write_text(PROBE_SOURCE)
    common = ["-arch", "x86_64", "-isysroot", str(sdk), "-mmacosx-version-min=" + MINIMUM_MACOS]
    compile_flags = [
        *common, "-fapple-kext", "-mkernel", "-nostdinc", "-I", str(kernel_headers),
        "-DKERNEL", "-DKERNEL_PRIVATE", "-DDRIVER_PRIVATE", "-DAPPLE", "-DNeXT",
        "-std=c++17", "-fno-rtti", "-fno-exceptions", "-fno-builtin", "-fno-common", "-O2",
    ]
    report["compile_flags"] = compile_flags
    report["link_libraries"] = ["kmodc++", "kmod", "cc_kext"]
    cxx = tools["clang++"]["path"]
    command(report, [cxx, *compile_flags, "-fsyntax-only", source], cwd=output, env=env)
    command(report, [cxx, *compile_flags, "-c", source, "-o", obj], cwd=output, env=env)
    command(report, [
        cxx, *common, "-fapple-kext", "-nostdlib", "-Xlinker", "-kext", obj,
        "-lkmodc++", "-lkmod", "-lcc_kext", "-o", binary,
    ], cwd=output, env=env)
    archs = command(report, [tools["lipo"]["path"], "-archs", binary], cwd=output, env=env).stdout.split()
    if archs != ["x86_64"]:
        raise RuntimeError("Probe is not exclusively x86_64: " + " ".join(archs))
    header = command(report, [tools["otool"]["path"], "-hv", binary], cwd=output, env=env).stdout
    if "KEXT_BUNDLE" not in header:
        raise RuntimeError("Probe Mach-O file type is not KEXT_BUNDLE")
    command(report, [tools["nm"]["path"], "-u", binary], cwd=output, env=env)
    report["artifacts"] = {
        path.name: {"sha256": sha256(path), "size_bytes": path.stat().st_size}
        for path in (source, obj, binary)
    }
    report["status"] = "passed"
    report["limitations"] = [
        "This probes the selected SDK and tools, not NVIDIA 615 or the complete NVRM sources.",
        "External kernel/IOKit symbols in a kext are expected and are recorded, not resolved against a running kernel.",
        "Nothing was installed, loaded, approved, or added to a Kernel Collection.",
        "NVRM link compatibility, release comparison, loading, and hardware tests remain separate requirements.",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Absent or empty, owned staging subdirectory; no symlinks")
    parser.add_argument("--sdk", help="SDK directory (default: SDKROOT, then xcrun's macosx SDK)")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.exit(2, "Darwin-only preflight: no output was created and no build was attempted.\n")
    try:
        output = staging_directory(args.output)
    except (OSError, RuntimeError) as error:
        parser.exit(2, str(error) + "\n")
    report = {
        "schema": 1, "status": "running", "architecture": "x86_64",
        "minimum_macos": MINIMUM_MACOS,
        "host": {"platform": platform.platform(), "machine": platform.machine()},
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "output": str(output), "commands": [],
    }
    result = 0
    try:
        perform_checks(report, output, args.sdk)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
        print(str(error), file=sys.stderr)
        result = 1
    report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report_path = output / "preflight.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(str(report_path))
    return result


if __name__ == "__main__":
    sys.exit(main())
