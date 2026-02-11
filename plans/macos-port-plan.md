# Plan: Porting TheRock to macOS

## Executive Summary

This plan outlines the strategy for enabling macOS (Apple Silicon) as a first-class development platform for ROCm/HIP workloads. Since macOS lacks AMD GPU drivers, we use a **remote execution model** where:
- **macOS** handles development, compilation, and orchestration
- **Linux GPU servers** execute GPU workloads via the hip-remote infrastructure

## Current State

### Already Implemented
| Component | Status | Notes |
|-----------|--------|-------|
| Platform detection | ✅ Done | `THEROCK_CONDITION_IS_MACOS` |
| Darwin sysdeps | ✅ Done | install_name_tool, bundled deps |
| AppleClang config | ✅ Done | Compiler verification |
| hip-remote-client | ✅ Done | libamdhip64.dylib for macOS |
| hip-remote-worker | ✅ Done | Linux service for GPU execution |
| smi-remote-client | ✅ Done | Remote GPU metrics |

### Remaining Work
The rest of TheRock needs adaptation for macOS development workflows.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        macOS (Apple Silicon)                     │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ HIP Source  │  │  amd-llvm    │  │   libamdhip64.dylib     │ │
│  │   (.hip)    │──│  (hipcc)     │──│   (hip-remote-client)   │ │
│  └─────────────┘  └──────────────┘  └───────────┬─────────────┘ │
│                                                  │ TCP/SSH       │
└──────────────────────────────────────────────────┼───────────────┘
                                                   │
┌──────────────────────────────────────────────────┼───────────────┐
│                     Linux GPU Server             │               │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                    hip-remote-worker                         │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐ │ │
│  │  │ HIP Runtime  │  │   rocBLAS    │  │  AMD GPU (MI300X)  │ │ │
│  │  │ (ROCR)       │  │   MIOpen     │  │                    │ │ │
│  │  └──────────────┘  └──────────────┘  └────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## Phased Implementation Plan

### Phase 1: Compiler Toolchain (High Priority)
**Goal:** Compile HIP code on macOS targeting AMD GPUs

#### 1.1 Build amd-llvm on macOS
- [ ] Add macOS build support to `compiler/CMakeLists.txt`
- [ ] Handle Apple Silicon (ARM64) host with AMDGPU target
- [ ] Build clang, lld, llvm tools
- [ ] Skip GPU-specific tools (amd-llvm-runtime for device)

**Key challenges:**
- Cross-compilation: ARM64 macOS host → AMDGPU device code
- Need to build AMDGPU backend without requiring GPU drivers

#### 1.2 hipcc/HIP compiler driver
- [ ] Port hipcc to work on macOS
- [ ] Generate device code (.hsaco) that can run on remote Linux GPUs
- [ ] Handle code object bundling (clang-offload-bundler)

#### 1.3 Device libraries
- [ ] Build device bitcode libraries on macOS
- [ ] Package for use with hipcc

**Deliverable:** Ability to compile HIP C++ to code objects on macOS

---

### Phase 2: Extend Remote HIP Client (Medium Priority)
**Goal:** Full HIP API coverage for remote execution

#### 2.1 Additional HIP APIs
Current coverage: ~30% of HIP API. Need to add:

| Category | APIs to Add |
|----------|-------------|
| Memory | hipMallocAsync, hipMemPool*, hipMemcpy2D/3D |
| Streams | hipStreamWaitEvent, hipStreamAddCallback |
| Graphs | hipGraph*, hipGraphExec* |
| Textures | hipCreateTextureObject, hipTexRef* |
| Surfaces | hipCreateSurfaceObject |
| Occupancy | hipOccupancyMaxPotentialBlockSize |
| Peer Access | hipDeviceCanAccessPeer, hipMemcpyPeer |

#### 2.2 Math library wrappers
- [ ] Remote rocBLAS calls (GEMM, etc.)
- [ ] Remote rocFFT calls
- [ ] Remote rocRAND calls

**Approach:** Extend protocol with BLAS/FFT operation codes, serialize matrix descriptors

#### 2.3 Performance optimizations
- [ ] Connection pooling
- [ ] Async operation batching
- [ ] Bulk data transfer optimization (zero-copy where possible)

---

### Phase 3: Build System Integration (Medium Priority)
**Goal:** Full CMake configure on macOS

#### 3.1 Core build infrastructure
- [ ] rocm-cmake: Should work as-is (pure CMake)
- [ ] rocm-core: Version utilities, should work

#### 3.2 Conditional component enablement
Add macOS-aware conditionals:

```cmake
# In therock_features.cmake
if(THEROCK_CONDITION_IS_MACOS)
  # Disable components requiring local GPU drivers
  set(THEROCK_ENABLE_CORE_RUNTIME OFF)  # ROCR-Runtime
  set(THEROCK_ENABLE_DEBUG_TOOLS OFF)   # rocgdb, etc.
  set(THEROCK_ENABLE_PROFILER OFF)      # rocprofiler-sdk
  set(THEROCK_ENABLE_COMM_LIBS OFF)     # RCCL
  set(THEROCK_ENABLE_DC_TOOLS OFF)      # RDC

  # Enable remote variants
  set(THEROCK_ENABLE_REMOTE_HIP ON)
  set(THEROCK_ENABLE_REMOTE_SMI ON)
endif()
```

#### 3.3 Stub libraries for linking
For libraries that applications link against but execute remotely:
- [ ] librocblas.dylib stub → forwards to remote
- [ ] libmiopen.dylib stub → forwards to remote

---

### Phase 4: Developer Experience (Lower Priority)
**Goal:** Seamless macOS development workflow

#### 4.1 hipconfig and environment
- [ ] `hipconfig` tool working on macOS
- [ ] ROCm path detection
- [ ] GPU target discovery from remote worker

#### 4.2 IDE integration
- [ ] VSCode HIP extension compatibility
- [ ] Xcode project generation (optional)
- [ ] Code completion for HIP APIs

#### 4.3 Remote profiling
- [ ] Stream profiling data back to macOS
- [ ] Integration with rocprof output formats
- [ ] Flame graph generation from remote traces

#### 4.4 hipify tool
- [ ] Build hipify-clang on macOS
- [ ] CUDA → HIP source conversion
- [ ] Pure source transformation, no GPU needed

---

### Phase 5: Library Stubs (Future)
**Goal:** Link-compatible libraries that forward to remote execution

#### 5.1 rocBLAS remote wrapper
```c
// librocblas.dylib on macOS
rocblas_status rocblas_sgemm(...) {
    return hip_remote_rocblas_sgemm(handle, ...);
}
```

#### 5.2 MIOpen remote wrapper
- Forward convolution calls to remote
- Handle workspace allocation remotely

#### 5.3 Performance considerations
- Latency-sensitive operations may need batching
- Large data transfers should use streaming

---

## Component Compatibility Matrix

| Component | Linux | Windows | macOS (Remote) | Notes |
|-----------|-------|---------|----------------|-------|
| **Compiler** |
| amd-llvm | ✅ | ✅ | 🔧 Phase 1 | Cross-compile to AMDGPU |
| hipcc | ✅ | ✅ | 🔧 Phase 1 | Compiler driver |
| hipify | ✅ | ✅ | 🔧 Phase 4 | Source transformation |
| **Core** |
| ROCR-Runtime | ✅ | ❌ | ❌ | Requires GPU driver |
| CLR/HIP | ✅ | ✅ | 🔧 Phase 2 | Via hip-remote |
| rocminfo | ✅ | ❌ | 🔧 Phase 2 | Via remote query |
| **Math Libs** |
| rocBLAS | ✅ | ✅ | 🔧 Phase 5 | Via remote wrapper |
| rocFFT | ✅ | ✅ | 🔧 Phase 5 | Via remote wrapper |
| rocRAND | ✅ | ✅ | 🔧 Phase 5 | Via remote wrapper |
| rocSOLVER | ✅ | ✅ | 🔧 Phase 5 | Via remote wrapper |
| **ML Libs** |
| MIOpen | ✅ | ❌ | 🔧 Phase 5 | Via remote wrapper |
| hipBLASLt | ✅ | ❌ | 🔧 Phase 5 | Via remote wrapper |
| **Tools** |
| rocgdb | ✅ | ❌ | ❌ | Linux kernel APIs |
| rocprof | ✅ | ❌ | 🔧 Phase 4 | Remote trace collection |
| AMD SMI | ✅ | ❌ | ✅ Done | smi-remote-client |
| **Comm** |
| RCCL | ✅ | ❌ | ❌ | Multi-GPU networking |

Legend: ✅ Supported | 🔧 Planned | ❌ Not applicable

---

## Implementation Order (Recommended)

```
Phase 1.1: amd-llvm macOS build
    ↓
Phase 1.2: hipcc on macOS
    ↓
Phase 2.1: Extended HIP API coverage
    ↓
Phase 3.2: CMake integration
    ↓
Phase 4.4: hipify
    ↓
Phase 4.1-4.3: Developer experience
    ↓
Phase 5: Library stubs (as needed)
```

---

## Technical Challenges

### 1. Cross-compilation complexity
- macOS ARM64 compiling for AMDGPU
- Device code needs to match Linux GPU runtime expectations
- Code object format compatibility

### 2. Network latency
- Remote execution adds ~10-200ms per operation
- Batching and async operations critical for performance
- Not suitable for latency-critical interactive apps

### 3. Binary compatibility
- Code objects compiled on macOS must run on Linux
- ELF format, AMDGPU ISA must match
- ROCm version alignment between client and worker

### 4. State synchronization
- Device memory allocation tracking
- Stream and event ordering across network
- Error propagation and handling

---

## Testing Strategy

### Unit Tests
- Local tests for client-side code
- Mock server for protocol testing

### Integration Tests
- macOS → Linux GPU execution
- Verify code object compatibility
- Performance regression testing

### CI/CD
- macOS GitHub Actions runners for client builds
- Linux GPU runners (self-hosted) for full execution tests

---

## Success Criteria

1. **Phase 1 Complete:** Can compile simple HIP kernel on macOS, run on remote Linux GPU
2. **Phase 2 Complete:** PyTorch HIP tests pass via remote execution
3. **Phase 3 Complete:** `cmake -DTHEROCK_AMDGPU_FAMILIES=gfx942` succeeds on macOS
4. **Phase 4 Complete:** Developer can use VSCode on macOS for HIP development
5. **Phase 5 Complete:** Standard HIP applications link and run without source changes

---

## Alternatives Considered

### 1. Full emulation on macOS
- Use software GPU emulation (e.g., SwiftShader for Vulkan)
- **Rejected:** Too slow, incomplete HIP support

### 2. Wait for Apple GPU compute
- Apple has Metal, could theoretically port HIP to Metal
- **Rejected:** Massive effort, different programming model

### 3. Linux VM on macOS
- Run Linux in VM with GPU passthrough
- **Rejected:** No GPU passthrough on Apple Silicon

### 4. Docker with remote GPU
- Container-based development
- **Partially adopted:** Worker runs in container, but client is native macOS

---

## Resource Requirements

### Development
- 1-2 engineers for Phase 1-2 (compiler + HIP client)
- 1 engineer for Phase 3-4 (build system + DX)
- Ongoing maintenance for Phase 5

### Infrastructure
- macOS CI runners (GitHub Actions has these)
- Linux GPU servers for testing (existing)
- Network connectivity between dev machines and GPU servers

---

## Timeline Estimate

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase 1 | 4-6 weeks | None |
| Phase 2 | 4-6 weeks | Phase 1 |
| Phase 3 | 2-3 weeks | Phase 1 |
| Phase 4 | 3-4 weeks | Phase 2, 3 |
| Phase 5 | Ongoing | Phase 2 |

**Total to MVP (Phases 1-3):** ~10-15 weeks
**Full developer experience (Phase 4):** +3-4 weeks

---

## Next Steps

1. **Immediate:** Validate amd-llvm builds on macOS ARM64
2. **This week:** Prototype hipcc compilation on macOS
3. **Next sprint:** Extend hip-remote-client API coverage
4. **Ongoing:** Document macOS development workflow
