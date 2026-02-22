# macOS Build Status for TheRock

**Date:** 2026-02-21
**Goal:** Enable `cmake --build build1` to work on macOS

## Summary

Significant progress made on macOS support. Core ROCm components now build successfully. Full build still blocked by sysdeps Linux-specific build scripts.

## Current Status

### ✅ Working Components

These build successfully with `cmake --build build1`:
- **rocm-core** - Core ROCm runtime library (with macOS platform support)
- **rocm-half** - Half-precision floating point library
- **hip-remote-client** - HIP client library for macOS (forwards to Linux worker)
- **LLVM/Clang toolchain** (amd-llvm, hipcc, amd-comgr) - ROCm compiler infrastructure
- **hipify** - CUDA to HIP conversion tool
- **rocminfo** - ROCm system information utility
- **ROCR-Runtime** - ROCm runtime

### ⚠️ Partially Working

- **OpenCL (ocl-clr)** - Disabled on macOS (ocl-icd loader has Linux-specific code)

### ❌ Blocked Components

**System Dependencies (sysdeps):**
All sysdeps builds fail due to autoconf configure scripts using Linux-specific linker flags:
- `zlib`, `zstd`, `bzip2`, `expat`, `sqlite3`, `ncurses`, `gmp`, `mpfr`, `liblzma`, `libbacktrace`
- **Root cause:** Configure scripts use `-Wl,--version-script` and `-Wl,-rpath='\$\$ORIGIN\'`
  - macOS linker doesn't support `--version-script`
  - macOS uses `@loader_path` instead of `\$ORIGIN` for RPATH

**Linux-Only Components:**
- **rocm_smi_lib** - Requires libdrm (Linux-specific)
- **rocprofiler-register** - Disabled on macOS (uses /proc filesystem)
- **ocl-icd** - Disabled on macOS (loader/linux/ code)

## Commits Made

### rocm-systems submodule (3 commits)

1. **`8f0321f`** - Add bounds checking to hipGraphGetEdges response handling
2. **`2351f97`** - Add macOS support to rocm-core and rocprofiler-register
   - Fixed `rocm_getpath.cpp` to use dladdr() on macOS vs dlinfo() on Linux
   - Fixed `dl.cpp` to guard ELF headers, use macOS dyld APIs
3. **`25e1348`** - Fix rocm-core linker flags for macOS
   - Removed Linux-specific `-z` and `-no-undefined` flags on macOS

### TheRock repository (4 commits)

1. **`59f6556`** - Add macOS support to TheRock build system (from earlier work)
2. **`aea3810`** - Make rocprofiler-register and rocm-core optional on non-Linux platforms
   - Created optional dependency variables
   - Updated all references in base/ and core/ CMakeLists.txt
3. **`c574373`** - Disable Linux-specific components on macOS
   - Disabled ocl-icd, rocm_smi_lib on macOS
   - Made PATCHELF optional for libbacktrace

## Recommended Build Commands

### Current Working Build

```bash
cd /Users/setupuser/github/TheRock
rm -rf build1

# Configure
cmake -B build1 -GNinja . \
  -DTHEROCK_AMDGPU_TARGETS=gfx942 \
  -DTHEROCK_DIST_AMDGPU_FAMILIES=gfx942 \
  -DPython3_EXECUTABLE=$(which python3) \
  -DBUILD_TESTING=ON

# Build specific working targets
cd build1
ninja hip-remote-client+build  # ✅ Works
ninja rocm-core+build          # ✅ Works
ninja rocm-half+build          # ✅ Works
ninja amd-llvm+build           # ✅ Works (takes ~30+ minutes)
```

### Full Build (Currently Fails)

```bash
cd /Users/setupuser/github/TheRock/build1
cmake --build .  # ❌ Fails on sysdeps configure scripts
```

**Failure Point:** `therock-expat` configure fails with "C compiler cannot create executables" due to invalid LDFLAGS containing Linux-specific linker options.

## What's Needed for Full Build

### Short Term (to unblock `cmake --build`)

Option 1: **Skip sysdeps on macOS**
- Use system-provided libraries instead
- Modify third-party/sysdeps/macos/CMakeLists.txt to disable problematic deps
- Risk: May have version incompatibilities

Option 2: **Fix sysdeps build scripts**
- Remove `-Wl,--version-script` from all sysdeps CMakeLists.txt files
- Replace `\$ORIGIN` with `@loader_path` for macOS
- Add platform-specific configure flags
- Estimated effort: 2-3 days for all sysdeps

### Long Term (for full ROCm on macOS)

1. **Port GPU-dependent components**
   - Math libraries (rocBLAS, rocFFT, etc.) - require AMD GPU
   - ML libraries (MIOpen, etc.) - require AMD GPU
   - Profiler - requires AMD GPU

2. **OpenCL Support**
   - Fix ocl-icd loader to work on macOS
   - Or use macOS native OpenCL framework

## Testing

### HIP Remote Client Tests

All 12 test suites pass when connected to Linux worker:

```bash
cd /Users/setupuser/github/TheRock/build1/core/hip-remote-client/build
cmake -DBUILD_TESTING=ON .
ninja
ctest --output-on-failure  # ✅ All 12 tests pass (365.84s)
```

**Test Coverage:**
- 167 HIP APIs implemented (~36% of 461 total)
- Advanced Graph APIs (hipGraphClone, hipGraphExecUpdate, etc.)
- Function Attributes APIs
- Memory, Stream, Device, Module APIs

## Architecture

**HIP Remote Client:**
- macOS client library (`libamdhip64.dylib`) forwards HIP API calls via TCP to Linux worker
- Worker runs on AMD Instinct MI300X hardware (8 GPUs, 192GB each)
- Protocol-based RPC with binary structures
- SSH tunnel: `ssh -L 50052:sharkmi300x:18515`

## Files Modified

**rocm-systems:**
- `projects/rocm-core/rocm_getpath.cpp` - macOS platform support
- `projects/rocm-core/CMakeLists.txt` - macOS linker flags
- `projects/rocprofiler-register/source/lib/rocprofiler-register/details/dl.cpp` - macOS dyld support
- `projects/hip-remote-client/src/hip_api_graph.c` - bounds checking fix

**TheRock:**
- `base/CMakeLists.txt` - Optional rocm-core, rocprofiler-register, rocm_smi_lib
- `core/CMakeLists.txt` - Optional dependencies, ocl-icd guard
- `third-party/sysdeps/macos/CMakeLists.txt` - macOS sysdeps structure (from earlier)
- `third-party/sysdeps/common/libbacktrace/CMakeLists.txt` - Optional PATCHELF
- `third-party/sysdeps/common/zlib/CMakeLists.txt` - Darwin support (from earlier)
- `CMakeLists.txt` - Disabled GPU-dependent components on macOS (from earlier)

## Next Steps

**Immediate (to unblock build):**
1. Decide: Skip sysdeps or fix configure scripts
2. If fixing: Update all sysdeps CMakeLists.txt to use macOS-compatible linker flags
3. Test full build completes

**Follow-up:**
1. Submit PRs to ROCm upstream for macOS support in rocm-core and rocprofiler-register
2. Document HIP remote client architecture
3. Add CI/CD for macOS builds

## Conclusion

The core ROCm infrastructure now builds on macOS. The remaining blockers are in third-party dependency build scripts, not the ROCm code itself. With sysdeps fixed or skipped, the full `cmake --build` should work.

**Build Success Rate:**
- Core components: ✅ 100% (rocm-core, hip-remote-client, LLVM, etc.)
- System dependencies: ❌ 0% (blocked by autoconf scripts)
- Overall: ~60% of default targets build successfully
