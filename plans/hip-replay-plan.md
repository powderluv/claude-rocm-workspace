# HIP Record & Replay (hip-replay)

## Context

We need a system to record HIP GPU workloads (kernel launches, memory operations, buffer contents) and replay them deterministically on any compatible AMD GPU. Use cases:
- **Bug reproduction**: Capture a failing workload and replay on a different machine
- **Performance regression testing**: Record a reference workload, replay across ROCm versions
- **Kernel-level debugging**: Isolate individual kernels with their exact input state
- **Portable benchmarking**: Package a complete GPU workload for cross-machine comparison

No existing tool covers this. roctracer/rocprofiler trace timing but not buffer contents. hip-remote sends HIP calls over TCP but doesn't record them. GPU_DUMP_CODE_OBJECT dumps code objects but not kernel args or buffers.

**No APEX dependency.** APEX was only referenced as a design pattern for LD_PRELOAD interposer structure. HRR is completely standalone — no shared code, no linking, no runtime dependency on APEX.

---

## Two Recording Paths

HRR supports two complementary recording mechanisms:

### Path A: In-Tree (CLR Runtime Hooks) — Primary

Recording hooks built directly into the HIP runtime (CLR) in TheRock. Triggered by `HIP_RECORD=1` env var. This is the preferred path because:
- Full access to internal state (kernel args, code objects, buffer metadata)
- No need to reverse-engineer the public API surface or parse fat binaries externally
- The CLR already has the `hip_apex.h` pattern as a template — env-var gated hooks at malloc/free/launch insertion points
- Works on both Linux and Windows without separate interposer builds
- Can access COMGR metadata directly (already loaded by the runtime)

**Implementation**: Add `hip_hrr.h` / `hip_hrr.cpp` to CLR alongside existing `hip_apex.h`, using the same insertion points:

| Operation | CLR File | Function | Existing Pattern |
|-----------|----------|----------|------------------|
| Malloc | `hip_memory.cpp:770` | `hipMalloc()` | `apex::track_alloc()` at line 380 |
| Free | `hip_memory.cpp:786` | `hipFree()` | `apex::track_free()` at line 101 |
| Memcpy | `hip_memory.cpp:809` | `hipMemcpy_common()` | `HIP_INIT_API` macro |
| Kernel launch | `hip_module.cpp:363` | `ihipLaunchKernelCommand()` | `apex::pre_launch()` |
| Module load | `hip_module.cpp:57` | `hipModuleLoad()` | `HIP_INIT_API` macro |
| Fat binary | `hip_fatbin.cpp:42` | `FatBinaryInfo::loadCodeObject()` | `LOG_CODE` mask logging |
| Sync | `hip_device_runtime.cpp` | `hipDeviceSynchronize()` | `HIP_RETURN_DURATION` |

The CLR already has:
- `amd::activity_prof::report_activity` callback infrastructure for profiler hooks
- `HIP_CB_SPAWNER_OBJECT(cid)` macro at every API entry
- `HIP_RETURN_DURATION` with timestamp collection
- Correlation IDs (`amd::activity_prof::correlation_id`) for tracing causality
- Flags system in `rocclr/utils/flags.hpp` for env var registration
- Thread-local state (`hip::tls`) for per-thread tracking

### Path B: Out-of-Tree (LD_PRELOAD / Proxy DLL) — Portable

For use with pre-built ROCm installations where you can't rebuild the runtime.

| Platform | Mechanism | Reference |
|----------|-----------|-----------|
| Linux | LD_PRELOAD interposer (`libhrr_record.so`) | APEX interposer pattern (dlsym/RTLD_NEXT) |
| Windows | Proxy DLL (`amdhip64_7.dll`) | hip-remote proxy DLL pattern |

Both forward every call to the real HIP runtime, then log the event. Plain C for the interposer (avoids C++ ABI issues).

The out-of-tree path requires its own ELF/msgpack parser for kernel arg introspection (since it can't access CLR internals). The in-tree path gets this for free from COMGR.

### Shared Components

Both paths write the same trace format and use the same replay tool:

```
                    Path A: In-Tree              Path B: Out-of-Tree
                    ┌──────────────┐             ┌────────────────────┐
                    │ CLR Runtime  │             │ LD_PRELOAD / Proxy │
Application ──────►│ hip_hrr.cpp  │             │ hrr_interposer.c   │
                    │ (env gated)  │             │ (dlsym forward)    │
                    └──────┬───────┘             └────────┬───────────┘
                           │                              │
                           ▼                              ▼
                    ┌──────────────────────────────────────────────┐
                    │           Shared: HRR Trace Writer           │
                    │  - events.bin (binary event stream)           │
                    │  - blobs/ (content-addressed, XXH3-128)      │
                    │  - code_objects/ (captured .hsaco ELFs)       │
                    │  - manifest.json (device info, config)        │
                    └──────────────────────┬───────────────────────┘
                                           │
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │           Shared: hrr-replay tool             │
                    │  - Load code objects (hipModuleLoadData)      │
                    │  - Recreate allocations                       │
                    │  - Restore buffers from blob store            │
                    │  - Launch kernels with captured args          │
                    │  - Verify outputs (optional)                  │
                    └──────────────────────────────────────────────┘
```

---

## Phase 1: Kernel-Level Record & Replay

### APIs to Capture

| Category | APIs |
|----------|------|
| Memory | hipMalloc, hipFree, hipMallocManaged, hipMallocAsync, hipFreeAsync |
| Transfer | hipMemcpy, hipMemcpyAsync, hipMemset, hipMemsetAsync |
| Module | hipModuleLoad, hipModuleLoadData, hipModuleUnload, hipModuleGetFunction |
| Launch | hipLaunchKernel, hipModuleLaunchKernel, hipExtModuleLaunchKernel |
| Sync | hipDeviceSynchronize, hipStreamSynchronize, hipEventSynchronize |
| Stream | hipStreamCreate, hipStreamCreateWithFlags, hipStreamDestroy |
| Event | hipEventCreate, hipEventRecord, hipEventDestroy |
| Fat binary | __hipRegisterFatBinary (out-of-tree path only; in-tree hooks FatBinaryInfo directly) |

### Kernel Argument Introspection

Which kernel args are GPU pointers (need buffer snapshots) vs scalars (record raw bytes)?

**In-tree** (preferred): The CLR already parses COMGR metadata when loading code objects. `hip_hrr.cpp` accesses the kernel's `amd::KernelParameterDescriptor` directly — each parameter already has `type_` (pointer/value), `size_`, and `offset_` fields. No additional parsing needed.

**Out-of-tree**: Parse code object ELF `.note` section, extract msgpack `amdhsa.kernels[].args[]` metadata. Each arg has `value_kind`: `global_buffer` (pointer), `by_value` (scalar), `hidden_*` (runtime). Build lookup table `(code_obj_hash, kernel_name) -> [ArgDescriptor]`.

**Fallback** for assembly kernels without metadata: blind-scan all 8-byte-aligned positions in kernarg buffer, treat values in GPU address range as pointers (same approach hip-remote uses for Tensile).

### Trace Format

```
capture_YYYYMMDD_HHMMSS.hrr/
  manifest.json              # Device info, ROCm version, capture config
  events.bin                 # Binary event stream (32-byte headers)
  blobs/                     # Content-addressed buffer store (XXH3-128)
    ab/ab1234...xxh3.blob    # Optionally zstd-compressed
  code_objects/              # Captured code object ELFs
    <xxh3_hash>.hsaco
```

**events.bin format** (little-endian, per-event):

```
Header (32 bytes):
  magic:          u32  = 0x52524845 ("HRRE")
  version:        u16  = 1
  event_type:     u16  (enum: MALLOC=0x01, FREE=0x02, MEMCPY=0x03, ...)
  sequence_id:    u64  (monotonic)
  timestamp_ns:   u64  (CLOCK_MONOTONIC / QueryPerformanceCounter)
  stream_id:      u32
  device_id:      u16
  payload_length: u16
Payload: (variable, event-specific, references blobs by XXH3 hash)
```

**KERNEL_LAUNCH payload**: code_obj_hash, kernel_name, grid/block dims, shared_mem, arg descriptors with inline scalar data or blob references for buffer snapshots.

**Why binary over JSON**: Fixed 32-byte headers enable O(1) seeking and memory-mapped replay. Tens of thousands of events per second.

**Why content-addressed blobs**: Model weights (static across kernel launches) are stored once. A 7B model trace: ~14GB weights (deduplicated) + ~2GB activations = ~16GB. Without dedup: hundreds of GB.

### Recording Modes

| Mode | Overhead | Use case |
|------|----------|----------|
| `HIP_RECORD_MODE=timeline` | Minimal | Record API call sequence only (no buffer data) |
| `HIP_RECORD_MODE=inputs` | Moderate | Snapshot input buffers before each kernel (default) |
| `HIP_RECORD_MODE=full` | High | Snapshot inputs + outputs (sync after every kernel) |

Additional controls:
- `HIP_RECORD_KERNEL_FILTER=matmul_*` — record only matching kernels
- `HIP_RECORD_MAX_BLOB_MB=1024` — skip buffers above threshold
- `HIP_RECORD_PID_FILTER=main` — record only main process

(In-tree uses `HIP_RECORD*` env vars registered in CLR's flags system. Out-of-tree uses `HRR_*` prefix to avoid collisions.)

### Replay Tool

`hrr-replay capture.hrr [--verify] [--timing] [--kernel-filter NAME]`

1. Read manifest, validate GPU compatibility (gfx arch match or warn)
2. Load code objects with `hipModuleLoadData`
3. Process events sequentially:
   - MALLOC: allocate, build handle-to-pointer map (reuse hip-remote's vaddr translation pattern)
   - MEMCPY: restore buffer contents from blob store
   - KERNEL_LAUNCH: marshal args (translate handles to real pointers), launch
4. With `--verify`: compare output buffers against recorded snapshots (bitwise, ULP, L2 norm)
5. With `--timing`: report per-kernel and total wall time vs recorded

### Benchmark & Reproduce Tool

`hrr-bench` — extract and benchmark individual kernels or full application traces. This is the primary tool for performance analysis and issue reproduction.

#### Kernel-Level Benchmarking

```bash
# List all kernels in a capture with stats
hrr-bench list capture.hrr
#  ID  Kernel                        Grid        Block     Calls  Avg(us)
#  1   _ZN4gemm...                   [256,1,1]   [256,1,1]   47   1842.3
#  2   _ZN8softmax...                [128,1,1]   [128,1,1]   47    312.1
#  3   _ZN4relu...                   [512,1,1]   [64,1,1]    94     28.7

# Benchmark a single kernel (restore inputs, run N iterations, report stats)
hrr-bench kernel capture.hrr --id 1 --iterations 1000 --warmup 50
#  Kernel: _ZN4gemm...
#  Grid: [256,1,1]  Block: [256,1,1]  SharedMem: 32768
#  Iterations: 1000  Warmup: 50
#  ──────────────────────────────────────
#  Min:     1.801 ms
#  Median:  1.843 ms
#  Mean:    1.849 ms
#  P95:     1.892 ms
#  P99:     1.923 ms
#  Max:     2.104 ms
#  Throughput: 541.3 kernel/s
#  Recorded: 1.842 ms  Delta: +0.4%

# Benchmark with modified grid/block dims (for tuning)
hrr-bench kernel capture.hrr --id 1 --grid 512,1,1 --block 128,1,1

# Benchmark all kernels matching a pattern
hrr-bench kernel capture.hrr --filter "gemm*" --iterations 100

# Export kernel as standalone .hip test (for sharing/filing bugs)
hrr-bench export capture.hrr --id 1 --output gemm_repro/
#  Creates:
#    gemm_repro/
#      CMakeLists.txt     # Build with: cmake -B build && cmake --build build
#      repro.hip          # Standalone HIP program that runs this one kernel
#      kernel.hsaco       # Code object
#      input_0.bin        # Input buffer snapshots
#      input_1.bin
#      expected_output.bin

# Export with sanitized data (for sharing repros without leaking model weights)
hrr-bench export capture.hrr --id 1 --output gemm_repro/ --safe
#  Same structure, but buffer contents are randomized.
#  Zero values are preserved (keeps sparsity patterns intact).
#  Scalar kernel args (dims, strides) are preserved.
#  Code objects are preserved (kernel binary needed for repro).
#  Safe for external bug reports — no proprietary data leaked.
```

#### Application-Level Benchmarking

```bash
# Replay full trace with timing comparison
hrr-bench app capture.hrr --iterations 5
#  Run 1: 4.823s (recorded: 4.801s, +0.5%)
#  Run 2: 4.819s (-0.1%)
#  Run 3: 4.821s (+0.0%)
#  Run 4: 4.818s (-0.1%)
#  Run 5: 4.820s (+0.0%)
#  ──────────────────────────────────────
#  Mean: 4.820s  StdDev: 0.002s  vs Recorded: +0.4%

# Profile hottest kernels (sorted by total time)
hrr-bench app capture.hrr --profile
#  Kernel                     Calls  Total(ms)  Avg(ms)   % Time
#  _ZN4gemm...                  47    86,588     1,842     73.2%
#  _ZN8softmax...               47    14,669       312     12.4%
#  _ZN4relu...                  94     2,698        29      2.3%
#  [other 12 kernels]          ...     ...         ...     12.1%

# Compare two captures (e.g., before/after optimization, or two ROCm versions)
hrr-bench compare before.hrr after.hrr
#  Kernel                     Before(ms)  After(ms)  Delta
#  _ZN4gemm...                   1,842      1,623    -11.9% ✓
#  _ZN8softmax...                  312        315     +1.0%
#  _ZN4relu...                      29         28     -3.4%
#  Total                         4,801      4,512     -6.0% ✓
```

#### Issue Reproduction

```bash
# Reproduce a crash or hang (replay until failure)
hrr-bench repro capture.hrr
#  Replaying 15,234 events...
#  Event 8,421: KERNEL_LAUNCH _ZN4gemm... -> hipErrorLaunchFailure
#  Kernel args dumped to crash_dump/

# Reproduce with additional diagnostics
hrr-bench repro capture.hrr --check-nan --check-inf
#  Event 3,201: KERNEL_LAUNCH _ZN8softmax... output contains NaN
#  Input buffers saved to nan_dump/
#  Output buffer saved to nan_dump/output.bin

# Binary search for first divergent kernel (regression bisect)
hrr-bench bisect before.hrr after.hrr --tolerance 1e-6
#  Comparing kernel-by-kernel...
#  First divergence at event 4,892: _ZN4gemm...
#  Max abs diff: 0.00234  Max ULP diff: 47
#  Input buffers: identical
#  Likely cause: kernel code changed (code object hash differs)

# Stress test a single kernel (repeat with same inputs to find intermittent failures)
hrr-bench stress capture.hrr --id 1 --iterations 10000 --verify
#  Running kernel 10,000 times with verification...
#  Iteration 7,234: output mismatch (max ULP diff: 3, tolerance: 0)
#  Saved divergent output to stress_fail_7234.bin
```

#### Implementation

`hrr-bench` is built on top of the replay engine with these additions:
- **Kernel isolation**: Can set up just one kernel's state (allocations + input buffers) without replaying the full trace. Uses the event index to find the kernel's dependencies.
- **Iteration loop**: Restore input buffers from blob store before each iteration (ensures clean state).
- **HIP event timing**: Uses `hipEventRecord` / `hipEventElapsedTime` for GPU-side kernel timing (not wall clock).
- **Export mode**: Generates a standalone `.hip` file with `CMakeLists.txt` and buffer data. Build with `cmake -B build && cmake --build build && ./build/repro`. Self-contained — no dependency on HRR or the trace archive. Useful for filing bug reports against ROCm.
- **Safe mode** (`--safe`): Randomizes buffer contents while preserving zeros. This keeps sparsity patterns and data shapes intact but strips proprietary data (model weights, activations). Scalar kernel args (dimensions, strides) are preserved since they're structural. Safe for sharing repros externally.
- **NaN/Inf checker**: After each kernel, does a quick `hipMemcpy` + scan for NaN/Inf in output buffers.
- **Bisect mode**: Replays both captures kernel-by-kernel, comparing outputs after each launch to find first divergence.

### Implementation Milestones

| # | Milestone | Scope |
|---|-----------|-------|
| 1 | **In-tree skeleton** | Add `hip_hrr.h/cpp` to CLR. Register `HIP_RECORD` flag. Hook hipMalloc/Free/Memcpy. Trace writer + blob store. |
| 2 | **Kernel arg capture (in-tree)** | Hook `ihipLaunchKernelCommand()`. Access `KernelParameterDescriptor` for pointer/scalar identification. Buffer snapshots. |
| 3 | **Replay tool** | `hrr-replay` binary. Archive reader, handle translator, kernel replayer, output verification. |
| 4 | **Benchmark & reproduce tool** | `hrr-bench` with kernel isolation, iteration benchmarking, NaN/Inf detection, export-to-standalone-hip, bisect mode, compare mode. |
| 5 | **Out-of-tree interposer (Linux)** | `libhrr_record.so` with LD_PRELOAD. ELF/msgpack parser for kernel arg introspection. Same trace writer. |
| 6 | **Out-of-tree proxy DLL (Windows)** | `amdhip64_7.dll` proxy. Generated from HIP headers. MSVC build. |
| 7 | **Testing + hardening** | Real workloads (rocBLAS, PyTorch), edge cases, docs. |

### Key Risks

1. **Buffer snapshot overhead**: Copying multi-GB buffers to host per kernel launch can be 10-100x slower. Mitigate with `inputs` mode (skip outputs), kernel filtering, content-addressed dedup, lazy snapshot (skip if hash unchanged).

2. **Fat binary format brittleness** (out-of-tree only): HIPF/HIPK magic varies across ROCm versions. The in-tree path avoids this entirely since CLR already handles format evolution. Out-of-tree mitigates with `GPU_DUMP_CODE_OBJECT=1` fallback.

3. **Multi-process ML frameworks**: PyTorch/vLLM use fork/subprocess. Mitigate with `hrr_enabled()` guard, fork detection, PID filtering. The in-tree path can check `hip::tls` state directly.

4. **Storage size**: 70B parameter model = ~140GB weights in FP16. Content-addressed dedup + zstd compression is the primary defense. Weights are static across launches = stored once.

---

## Phase 2: Full Application Capture & Replay

### Research Summary

No existing tool handles cross-platform GPU application packaging:
- **CDE**: Dead (last updated ~2014), Linux-only, no GPU support
- **AppImage/Flatpak**: Linux-only, explicitly exclude GPU drivers/libs
- **Docker/OCI**: Strong for Linux, weak for Windows, requires Docker runtime
- **LD_PRELOAD file tracing / Detours**: Can discover deps but can't capture GPU state

### Approach: Custom Archive with Platform-Specific Capture

```
capture.hrr/                     (Phase 1 trace data)
  manifest.json
  events.bin
  blobs/
  code_objects/
  environment/                   (Phase 2 additions)
    env_vars.json                # Relevant env vars at capture time
    python/
      requirements.txt           # pip freeze output
      sys_path.json              # Python path
      entry_script.py            # Captured entry point
    libs/                        # Captured shared libraries
      linux/
        libamdhip64.so.6         # Exact HIP runtime used
        libhsa-runtime64.so.1
        librocsolver.so.0
        ...
      windows/
        amdhip64_7.dll
        ...
    loader.sh                    # Linux replay launcher
    loader.bat                   # Windows replay launcher
```

### Library Capture

| Platform | Discovery | Mechanism |
|----------|-----------|-----------|
| Linux | `/proc/self/maps` at init + `dlopen` hook | In-tree: CLR constructor. Out-of-tree: LD_PRELOAD |
| Windows | `EnumerateLoadedModules64` + `LoadLibraryW` hook | In-tree: DllMain. Out-of-tree: Detours/IAT patch |

The launcher scripts set `LD_LIBRARY_PATH` / `PATH` to point at captured libs before running replay.

### Phase 2 Milestones

| # | Milestone | Scope |
|---|-----------|-------|
| 8 | Library capture | Enumerate + copy loaded shared libs on both platforms |
| 9 | Python env capture | Detect Python, freeze packages, capture entry script |
| 10 | Portable packaging | Archive format, launcher scripts, optional Docker export |
| 11 | Cross-platform replay | Validate Linux trace replay on Windows and vice versa |

### Packaging Decision

**Default**: tar.zst archive with embedded launcher script (works everywhere, no runtime deps)
**Optional exports**: Docker image, AppImage (Linux only)

---

## Alternatives Considered

| Decision | Chosen | Rejected | Why |
|----------|--------|----------|-----|
| Recording path | Both in-tree + out-of-tree | Out-of-tree only | In-tree gives full access to CLR internals (kernel metadata, code objects) without parsing; out-of-tree provides portability with pre-built ROCm |
| In-tree pattern | `hip_apex.h`-style hooks in CLR | rocprofiler-register dispatch table, activity callbacks | Direct hooks are simpler, lower overhead, and can access internal state that callbacks can't |
| Trace format | Custom binary + JSON manifest | Protobuf, JSON events | Fixed headers for O(1) seek, no external deps, fast write path |
| Buffer capture | Full snapshot + dedup | GPU page fault tracking (XNACK) | Simpler, works on all hardware, dedup handles common case |
| Blob hashing | XXH3-128 | SHA-256 | 10-50x faster, not a security application, matches hip-remote |
| Phase 2 packaging | Custom archive + launcher | CDE, AppImage, Flatpak, Docker-only | Cross-platform requirement eliminates Linux-only options |
| Language | C++ (in-tree, matches CLR) + C (out-of-tree interposer) | Pure C, Rust | In-tree must be C++ to access CLR classes; out-of-tree C avoids ABI issues |
| APEX dependency | None | Shared interposer code | Different goals (prefetch vs record); no shared state needed |

---

## Project Layout

### In-Tree (CLR changes in TheRock)

```
rocm-systems/projects/clr/hipamd/src/
  hip_hrr.h                      # HRR API: hrr::enabled(), hrr::record_*()
  hip_hrr.cpp                    # Implementation: trace writer, blob store, env var init
  hip_hrr_writer.h               # Trace file writer (events.bin + blobs/)
  hip_hrr_writer.cpp             # Content-addressed blob store, XXH3 hashing
```

Changes to existing files (minimal, following `hip_apex.h` pattern):
- `hip_memory.cpp` — add `hrr::record_malloc/free/memcpy()` calls
- `hip_module.cpp` — add `hrr::record_module_load/launch()` calls
- `hip_fatbin.cpp` — add `hrr::record_code_object()` at extraction
- `rocclr/utils/flags.hpp` — register `HIP_RECORD*` env vars

### Out-of-Tree (standalone, for pre-built ROCm)

```
hip-replay/                      # Standalone repo
  CMakeLists.txt
  src/
    hrr_core.h                   # Shared types (event structs, blob hash)
    hrr_writer.c                 # Trace writer (shared with in-tree via header-only or lib)
    hrr_reader.c                 # Trace reader (memory-mapped)
    hrr_alloc_tracker.c          # Pointer-to-handle mapping
    hrr_code_object.c            # ELF/msgpack parser (out-of-tree only)
    hrr_interposer_linux.c       # LD_PRELOAD entry points
    hrr_proxy_win.c              # Windows proxy DLL
    hrr_replay.c                 # Replay engine (shared tool)
    hrr_bench.c                  # Benchmark & reproduce tool
    hrr_verify.c                 # Output buffer comparison
    hrr_export.c                 # Export kernel to standalone .hip repro
    hrr_info.c                   # Archive info CLI
  tools/
    gen_proxy_dll.py             # Generate proxy from HIP headers
  tests/
    test_record_replay.hip
```

### Environment Variables

**In-tree** (registered in CLR flags system):

| Variable | Description |
|----------|-------------|
| `HIP_RECORD=1` | Enable recording |
| `HIP_RECORD_OUTPUT=./capture.hrr` | Output directory |
| `HIP_RECORD_MODE=inputs\|full\|timeline` | Recording mode |
| `HIP_RECORD_KERNEL_FILTER=pattern` | Record only matching kernels |
| `HIP_RECORD_MAX_BLOB_MB=N` | Skip blobs above threshold |
| `HIP_RECORD_COMPRESS=1` | Zstd-compress blobs |

**Out-of-tree** (read directly via `getenv()`):

| Variable | Description |
|----------|-------------|
| `HRR_RECORD=1` | Enable recording |
| `HRR_OUTPUT=./capture.hrr` | Output directory |
| (same pattern with `HRR_` prefix) | |

---

## Verification Plan

1. **Unit tests**: blob store write/read, event serialization round-trip, ELF metadata parsing
2. **Integration test**: record `vectorAdd.hip` -> replay -> verify output matches
3. **rocBLAS test**: record SGEMM -> replay -> verify numerical accuracy (ULP comparison)
4. **PyTorch test**: record GPT-2 inference (single forward pass) -> replay -> verify output tensors
5. **Cross-platform test**: record on Linux -> replay on Linux (same machine, different machine)
6. **Windows test**: record with proxy DLL -> replay with `hrr-replay.exe`
7. **In-tree vs out-of-tree parity**: Record same workload with both paths, verify identical trace output

---

## Critical Files (In-Tree Path)

These are the CLR source files that need modifications:

| File | What to add |
|------|-------------|
| `rocm-systems/projects/clr/hipamd/src/hip_hrr.h` | **New file.** `hrr::enabled()`, `hrr::record_malloc()`, `hrr::record_launch()`, etc. |
| `rocm-systems/projects/clr/hipamd/src/hip_hrr.cpp` | **New file.** Init, trace writer, blob store, env var parsing. |
| `rocm-systems/projects/clr/hipamd/src/hip_memory.cpp` | Add `hrr::record_malloc/free/memcpy()` at existing APEX hook points (lines 101, 380, 809). |
| `rocm-systems/projects/clr/hipamd/src/hip_module.cpp` | Add `hrr::record_module_load()` at line 57, `hrr::record_launch()` at line 363. |
| `rocm-systems/projects/clr/hipamd/src/hip_fatbin.cpp` | Add `hrr::record_code_object()` at `loadCodeObject()` (line 42). |
| `rocm-systems/projects/clr/rocclr/utils/flags.hpp` | Register `HIP_RECORD`, `HIP_RECORD_MODE`, etc. using existing `DEFINE_FLAG` macro. |
| `rocm-systems/projects/clr/hipamd/CMakeLists.txt` | Add `hip_hrr.cpp` to sources, optional xxhash/zstd dependency. |
