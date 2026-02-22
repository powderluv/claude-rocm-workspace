# Building amd-llvm on macOS (Apple Silicon)

## Status: ✅ SUCCESS

Build completed: 2026-02-11
Platform: macOS ARM64 (Apple Silicon)

### Key Achievement
Successfully compiled HIP kernels on macOS targeting AMDGPU (gfx942/MI300X).

```bash
# This works!
./bin/clang++ -x hip --offload-arch=gfx942 \
  --rocm-path=$PWD/rocm-mock \
  --cuda-device-only \
  -c kernel.hip -o kernel.o
```

Produced valid AMDGPU code (verified via `llvm-objdump`).

## Configuration

```bash
cd /Users/setupuser/github/TheRock/build-llvm-macos

cmake ../compiler/amd-llvm/llvm \
  -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLVM_TARGETS_TO_BUILD="AMDGPU;AArch64" \
  -DLLVM_ENABLE_PROJECTS="clang;lld" \
  -DLLVM_ENABLE_RUNTIMES="" \
  -DLLVM_BUILD_LLVM_DYLIB=ON \
  -DLLVM_LINK_LLVM_DYLIB=ON \
  -DLLVM_ENABLE_ZLIB=ON \
  -DLLVM_ENABLE_Z3_SOLVER=OFF \
  -DLLVM_ENABLE_LIBXML2=OFF \
  -DCLANG_DEFAULT_LINKER=lld \
  -DCLANG_ENABLE_AMDCLANG=ON \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLVM_INCLUDE_TESTS=OFF \
  -DLLVM_INCLUDE_BENCHMARKS=OFF \
  -DLLVM_EXTERNAL_ROCM_DEVICE_LIBS_SOURCE_DIR="/Users/setupuser/github/TheRock/compiler/amd-llvm/amd/device-libs" \
  -DLLVM_EXTERNAL_PROJECTS="rocm-device-libs"
```

## Key Configuration Choices

### Targets
- **AMDGPU**: Required for compiling HIP kernels
- **AArch64**: Required for host code on Apple Silicon

### Projects Enabled
- **clang**: C/C++/HIP compiler frontend
- **lld**: Linker (used for device code)
- **rocm-device-libs**: AMDGPU device library bitcode

### Projects Disabled (for initial build)
- **compiler-rt**: Would need cross-compilation setup
- **libcxx/libcxxabi**: Not needed for device code
- **openmp/offload**: Requires ROCR-Runtime (Linux only)
- **flang**: Fortran not needed initially

### Build Options
- **LLVM_BUILD_LLVM_DYLIB=ON**: Build shared libLLVM
- **LLVM_LINK_LLVM_DYLIB=ON**: Link tools against shared lib
- **CLANG_ENABLE_AMDCLANG=ON**: Enable AMD-specific clang features

## Build Progress

Total targets: 5198

## Expected Outputs

After build completes:
- `bin/clang` - Clang compiler
- `bin/clang++` - C++ compiler
- `bin/lld` - Linker
- `bin/llvm-*` - LLVM tools
- `lib/libLLVM.dylib` - Shared LLVM library
- `lib/clang/*/amdgcn/` - AMDGPU device libraries

## Next Steps After Build

1. Test compiling a simple HIP kernel:
   ```bash
   ./bin/clang++ -x hip --offload-arch=gfx942 -c test.hip -o test.o
   ```

2. Add hipcc wrapper script for macOS

3. Integrate into TheRock build system

## Differences from Linux Build

| Setting | Linux | macOS |
|---------|-------|-------|
| LLVM_TARGETS_TO_BUILD | AMDGPU;X86 | AMDGPU;AArch64 |
| LLVM_ENABLE_RUNTIMES | compiler-rt;libunwind;libcxx;libcxxabi;openmp;offload | (none initially) |
| CMAKE_INSTALL_RPATH | $ORIGIN/... | @loader_path/... |
| Linker | GNU ld / lld | Apple ld / lld |

## Known Issues

1. **No compiler-rt**: Device-side address sanitizer won't work without compiler-rt
2. **No OpenMP offload**: Offload runtime requires HSA/ROCR which is Linux-only
3. **No llvm-mt**: Requires libxml2 (optional)
4. **HIP headers need C++ stdlib**: Using `#include <hip/hip_runtime.h>` requires additional setup for C++ standard library headers in device compilation mode

## Workarounds

### For HIP kernels without standard library dependencies:
Use AMDGPU builtins directly:
```cpp
__attribute__((global)) void kernel(float* a, float* b, float* c, int n) {
    unsigned int idx = __builtin_amdgcn_workgroup_id_x() * 256 + __builtin_amdgcn_workitem_id_x();
    if (idx < n) {
        c[idx] = a[idx] + b[idx];
    }
}
```

### For full HIP support:
Need to set up libcxx or use system headers with proper include paths.

## Build Artifacts

After build completes, key binaries in `bin/`:
- `clang`, `clang++`, `clang-22` - Main compiler
- `amdclang`, `amdclang++` - AMD-specific frontend
- `lld`, `ld.lld` - Linker
- `clang-offload-bundler` - Bundle/unbundle fat binaries
- `llvm-objdump` - Disassembler (supports AMDGPU)

Device libraries in `tools/rocm-device-libs/amdgcn/bitcode/`:
- `ocml.bc` - OpenCL math library
- `ockl.bc` - OpenCL kernel library
- `oclc_isa_version_*.bc` - ISA-specific options
- `hip.bc` - HIP-specific library

## Phase 1.2: hipcc macOS Port - COMPLETE

Successfully ported hipcc to macOS with the following changes:

### Changes Made

1. **`src/utils.cpp`** - Added macOS `_NSGetExecutablePath()` support
2. **`src/hipBin_base.h`** - Added `macos` to `OsType` enum and detection
3. **`src/hipBin_util.h`** - Added macOS headers
4. **`src/hipBin_amd.h`** - Skip `rocm_agent_enumerator` on macOS, fix linker flags
5. **`CMakeLists.txt`** - Don't link libstdc++fs on macOS (libc++ has built-in filesystem)

### Build Commands

```bash
# Configure hipcc for macOS
cd /Users/setupuser/github/TheRock/build-hipcc-macos
cmake ../compiler/amd-llvm/amd/hipcc -G Ninja -DCMAKE_BUILD_TYPE=Release

# Build
ninja
```

### Usage on macOS

```bash
export HIP_CLANG_PATH=/path/to/build-llvm-macos/bin
export ROCM_PATH=/path/to/build-llvm-macos
export HIP_PATH=/path/to/build-llvm-macos

# For device-only compilation without C++ stdlib requirements:
hipcc --rocm-path=/path/to/rocm-mock \
      --offload-arch=gfx942 \
      -nogpuinc \
      --cuda-device-only \
      -c kernel.hip -o kernel.o
```

### Known Limitations

1. **No local GPU detection** - Must specify `--offload-arch=gfxNNN` explicitly
2. **C++ stdlib not available for device code** - Use `-nogpuinc` for kernels with no stdlib deps
3. **No host linking** - Can only do device-only compilation (for remote execution)

## Next Steps

1. Set up proper HIP headers with C++ stdlib support for full HIP compilation
2. Create macOS-specific hipcc wrapper script for convenience
3. Integrate with TheRock build system
4. Test code object compatibility with remote Linux GPU worker (already verified in Phase 1.1)
