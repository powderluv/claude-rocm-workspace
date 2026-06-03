# Plan: Local ROCm on macOS via eGPU (DEXT-backed)

**Date:** 2026-04-16
**Status:** Draft
**Supersedes (for local-GPU path):** Parts of `plans/macos-port-plan.md` — that plan stays valid for the *remote execution* path; this plan covers the orthogonal *local GPU* path using a Thunderbolt eGPU.

## 0. Scope and Goal

Run real ROCm workloads (HIP, rocBLAS, PyTorch) on an Apple Silicon Mac with an AMD eGPU connected over Thunderbolt 4 — no Linux in the loop. Correctness first, performance later. TB4 bandwidth (~40 Gbps, ~5 GB/s effective) is a hard ceiling, but is sufficient for functional bring-up and most small-to-medium workloads.

## 1. What Exists Today

### 1.1 Already built
- **`ROCmGPUDriver.dext`** — DriverKit extension built, arm64. 13 escape commands: GetInfo, Reset, CfgRead/Write, MMIORead/Write32, MapBAR, AllocDMA/FreeDMA/MapDMA, EnableMSI, WaitInterrupt. Provides raw PCIe HAL.
- **`ROCmGPUApp.app`** — installer/manager for the DEXT.
- **Python `amd_gpu_driver`** — userspace driver (~unit tests pass, 109/109). Backends: `kfd/` (Linux), `macos/` (IOKit→DEXT), `windows/` (WDDM).
- **`DeviceBackend` abstraction** (`backends/base.py`) — clean interface: alloc/free/map memory, create queues, submit packets, signals. MacOS and KFD backends both implement it.
- **PM4/SDMA packet builders** — OS-agnostic, validated with 109 unit tests.
- **Existing macOS remote-execution stack** — `hip-remote-client` (macOS) ↔ Linux worker. Reusable only for testing; *not* on the local-GPU path.

### 1.2 Not yet built (on current branch)
- Hardware bring-up sequence (IP discovery, NBIO, GMC, PSP firmware load, IH, ring init) — framework exists in `macos/bringup.py` but phases are stubs pending hardware.
- Any C/C++ interface to the Python driver — Python is the only client today.
- Any ROCR/HIP integration.

### 1.3 Relevant ROCR architecture (from source survey)
- **`core/driver/driver.h`** (401 lines) — `core::Driver` is a pure-virtual interface with ~50 methods (GetSystemProperties, AllocateMemory, CreateQueue, CreateEvent, WaitOnEvent, ExportDMABuf, …). This is the **pluggable seam**.
- Existing backends: `core/driver/kfd/amd_kfd_driver.cpp` (780 LOC), `core/driver/xdna/amd_xdna_driver.cpp` (1130 LOC), `core/driver/virtio/*`.
- **Adding a macOS backend here is the intended extension point.** `DriverType::MACOS_DEXT` slots into the existing enum.
- `hsaKmt*` calls: 18+ sites in `amd_kfd_driver.cpp`, a handful in `amd_aql_queue.cpp`, `amd_gpu_agent.cpp`, `signal.cpp`, `interrupt_signal.cpp`, `amd_blit_sdma.cpp`. These are the integration surfaces that must be backend-routed or macOS-conditional.
- ROCR's own Linux-specific surface is small: `<sys/eventfd.h>` in 1 file (`amd_hsa_loader.cpp`), two `<linux/...>` headers. Most of ROCR is portable POSIX.
- **`core/util/os.h`** has only `_WIN32` / `__linux__` branches (lines 73–78) — hard compile failure on macOS. Needs `os_darwin.cpp` (parallel to `os_linux.cpp` at ~32 KB).

### 1.4 Relevant CLR/HIP architecture (from source survey)
- 219 `_WIN32` vs 43 `__linux__` vs 8 `!_WIN32` guards — clean POSIX/Windows split, Linux falls through the POSIX branch.
- `rocclr/os/os_posix.cpp` Linux-isms: `prctl(PR_SET_NAME)`, `<sys/sysinfo.h>`, direct NUMA/affinity syscalls, `<link.h>`. Each has a Darwin equivalent (`pthread_setname_np`, `sysctl`, thread-affinity APIs, `<mach-o/dyld.h>`).
- HSA boundary is clean — 7 files include `hsa/hsa.h`, zero direct `hsaKmt` usage in CLR.
- comgr is loaded via the abstracted `Os::loadLibrary()` — dylib loading works out of the box.

## 2. Architecture Target

```
┌───────────────────────────────────────────────────────────────┐
│  Application (PyTorch, HIP sample)                            │
├───────────────────────────────────────────────────────────────┤
│  HIP runtime (libamdhip64.dylib)    ←  CLR port               │
├───────────────────────────────────────────────────────────────┤
│  ROCR (libhsa-runtime64.dylib)      ←  port + new driver bkd  │
│    core::Driver (abstract)                                    │
│         │                                                     │
│         ├─ KfdDriver (Linux only — unchanged)                 │
│         ├─ XdnaDriver (Linux only — unchanged)                │
│         └─ MacOsDriver  ←  NEW, backend for this port         │
├───────────────────────────────────────────────────────────────┤
│  libmacgpu (C/C++ shim)   ← NEW: IOKit client, replaces hsaKmt│
├───────────────────────────────────────────────────────────────┤
│  ROCmGPU.dext   (DriverKit, arm64)  ← built                   │
├───────────────────────────────────────────────────────────────┤
│  PCIDriverKit → Thunderbolt → AMD eGPU                        │
└───────────────────────────────────────────────────────────────┘
```

**Key architectural decision:** Port at `core::Driver`, not at `libhsakmt`.

- libhsakmt is 203 public functions, 2.6k-line topology.c, 146k fmm.c — mostly things `core::Driver::KfdDriver` already wraps. Re-implementing that surface against IOKit is wasted work.
- `core::Driver` is ~50 methods, a clean HAL. One `MacOsDriver` implementation subsumes libhsakmt's role for our platform. Same pattern the XDNA NPU backend already follows.
- Python driver stays as the *prototyping* / *bring-up* tool. `libmacgpu` is a fresh C++ shim that exercises the same DEXT escapes from ROCR.

## 3. Staged Plan

Order is strict — later stages depend on earlier ones validating on hardware.

### Stage 0 — DEXT bring-up on real hardware (PREREQUISITE)
Currently the macOS bringup sequence is stubbed. No ROCm porting is meaningful until compute actually runs.

**Milestones (all require actual eGPU hardware):**

1. **PCIe + BAR access** — load DEXT, enumerate AMD eGPU, map BARs, read/write MMIO. Validate: read `mmRCC_DEV0_EPF0_VF0_STRAP0` or equivalent, match expected device ID.
2. **IP discovery** — parse IP discovery binary from VRAM. Validate: enumerated block list matches expected gfx11/gfx12 layout.
3. **NBIO init** — PCIe config, interrupt routing, memory aperture setup.
4. **GMC init** — page tables, VRAM aperture, GTT window. Validate: CPU-write-then-GPU-read of a test buffer via SDMA.
5. **PSP firmware load** — load SOS/ASD/MEC/SDMA firmware (sourced from linux-firmware repo — licensing note: GPL-compatible binary redistribution).
6. **IH ring init** — interrupt handler ring buffer, MSI-X vector delivery back through DEXT.
7. **Compute ring** — MEC (micro-engine) queue creation, doorbell mapping, submit a no-op PM4 packet, observe completion fence.
8. **First dispatch** — load a precompiled `.co` (code object) via SDMA copy, issue `DISPATCH_DIRECT`, observe results in VRAM, copy back via SDMA.

**Exit criterion:** `tests/integration/test_dispatch.py` passes on real hardware — a HIP kernel compiled on macOS runs on the eGPU and produces correct output.

**Blocking risks:**
- **PCI transport entitlement** — Apple must approve `com.apple.developer.driverkit.transport.pci` for distribution. SIP-off development works, distribution doesn't without this. TinyGPU got it in March 2026 (noted in README), so precedent exists.
- **Thunderbolt hot-plug semantics** — DEXT lifecycle (start/stop) tied to TB connect/disconnect. Need clean teardown.
- **IOMMU (DART) quirks** — Apple Silicon's IOMMU is stricter than typical x86 IOMMUs. DMA mappings must go through `IODMACommand::PrepareForDMA` (DEXT already does this). Resizable BAR over TB4: unknown — may need to fall back to smaller VRAM aperture.

### Stage 1 — C/C++ shim: `libmacgpu`
Factor the Python macOS backend's IOKit plumbing into a small C++ library that ROCR can link against.

- Mirrors Python backend 1:1: device discovery, IOKit user client, the 13 DEXT escapes, DMA buffer management, signal polling.
- Exposes a C API close to the function subset ROCR's kfd backend uses (AllocateMemory, CreateQueue, SubmitPackets, WaitOnSignal, etc.) — but *not* libhsakmt-shaped. It's whatever `MacOsDriver` needs.
- Build: CMake target, static or shared, uses `IOKit.framework`. Can be built and unit-tested without hardware using the existing `test_ioctl_structs`, `test_pm4_packets`-style harnesses.
- ~1500–2000 LOC estimate.

**Exit criterion:** `libmacgpu` C++ client can do everything the Python macOS backend does, validated by replicating `test_dispatch.py` in C++.

### Stage 2 — ROCR port: `os_darwin.cpp` + `MacOsDriver`

Two parallel sub-tracks:

**Track A: Platform layer (`os_darwin.cpp`)**
- Add `__APPLE__` branch to `core/util/os.h` (line 78).
- New `core/util/darwin/os_darwin.cpp` (parallel to `os_linux.cpp`, ~32 KB / ~1200 LOC).
- Map Linux APIs: `pthread_setname_np` (Darwin variant), `sysctl` for `nproc`/memory, Mach `host_statistics` for RAM, `<mach-o/dyld.h>` for exec-path, Mach semaphores or dispatch sources instead of eventfd.
- `amd_hsa_loader.cpp` — replace the single `<sys/eventfd.h>` use with a Darwin alternative (Mach port / `pipe2`-style fallback).

**Track B: Driver backend (`core/driver/macos/amd_macos_driver.cpp`)**
- Implement `class MacOsDriver : public core::Driver`.
- Add `DriverType::MACOS_DEXT` to the enum in `driver.h`.
- Wire into `runtime.cpp` driver instantiation (alongside KFD/XDNA/virtio).
- Link against `libmacgpu` from Stage 1.
- Handle the callers of `hsaKmt*` outside `amd_kfd_driver.cpp` (`amd_aql_queue.cpp`, `signal.cpp`, `interrupt_signal.cpp`, `amd_blit_sdma.cpp`): either route those through `core::Driver` virtuals (may require promoting a few helpers) or keep KFD-path under `__linux__` guards. Prefer the former — cleaner and matches how XDNA/virtio coexist.

**Exit criterion:** ROCR builds on macOS and `rocminfo` prints the eGPU correctly (topology, agent count, agent info). A minimal HSA program using `hsa_queue_create` + `hsa_signal_create` + `hsa_agent_iterate_regions` works end-to-end.

### Stage 3 — HIP/CLR port
Relatively cheap given ROCR works.

- Add `__APPLE__` to the `_WIN32`/`__linux__` guards in `rocclr/os/os_posix.cpp`:
  - Replace `prctl(PR_SET_NAME, …)` with `pthread_setname_np(name)` (Darwin takes 1 arg, Linux takes 2).
  - Replace `<sys/sysinfo.h>` with `sysctl(HW_MEMSIZE)`.
  - Replace the `__NR_getcpu`/`__NR_get_mempolicy`/`__NR_sched_setaffinity` syscalls with no-op stubs or Darwin's `thread_policy_set`. Most of this is NUMA — single-GPU eGPU has no NUMA, stub is fine.
  - `<link.h>` → `<mach-o/dyld.h>` for introspection.
- `comgrctx.cpp` — add dylib-name variant `"libamd_comgr.${MAJOR}.${MINOR}.dylib"`. The load path is already abstracted.
- Build system: `hipamd/CMakeLists.txt` — enable Darwin branch (currently treated as generic UNIX, mostly fine).

**Exit criterion:** `hipcc` on macOS + linking `libamdhip64.dylib` (not the remote-client variant) runs `hipDeviceGetCount() == 1`, `hipMalloc`/`hipMemcpy`/`hipLaunchKernel` work end-to-end for a simple vector-add kernel.

### Stage 4 — Math/ML libraries
Should mostly just work once HIP does.

- `rocBLAS`, `rocFFT`, `hipBLASLt`, `MIOpen` — compile-only concerns (source changes typically minimal; the GPU kernels are code objects). Validate build on macOS; run functional tests.
- `RCCL` — depends on network fabric; not essential for single-GPU eGPU. Park.
- `rocm_smi_lib` — requires libdrm on Linux; on macOS, a thin stub over DEXT MMIO (temp, power readings) or skip.

### Stage 5 — TheRock integration
- Add `THEROCK_CONDITION_IS_MACOS_EGPU` or similar feature flag to distinguish *local eGPU* from *remote client* on macOS.
- `base/CMakeLists.txt` / `core/CMakeLists.txt`: enable ROCR-Runtime, CLR, math-libs for macOS-with-eGPU.
- Sysdep flags fixed in prior macOS work (`macos-build-status.md`) still needed; don't rework.
- CI: GitHub macOS-arm64 runners can do compile-only validation. Hardware CI requires a physical Mac + eGPU (self-hosted runner).

### Stage 6 — Distribution
- Apple Developer entitlement for `com.apple.developer.driverkit.transport.pci` (required for non-SIP-off installs).
- Notarization of `ROCmGPUApp.app` (ships the DEXT embedded).
- Wheel-distributable HIP: `pip install rocm-hip` on macOS pulls down `libamdhip64.dylib`, `libhsa-runtime64.dylib`, `libmacgpu.dylib`, bundled firmware, and a copy of `ROCmGPUApp.app` with install instructions.

## 4. Answering the Question: "Do we need a libhsakmt backend?"

**No — do not port libhsakmt.** The right port point is one layer up, at `core::Driver`. Rationale:

- libhsakmt has 203 functions across topology/memory/queue/event/SVM/perfctr/debug. Most of those are KFD-specific ioctl wrappers that ROCR itself already re-wraps in `amd_kfd_driver.cpp`. Re-implementing *both* layers against DEXT is redundant.
- ROCR already has the abstract `core::Driver` with working multi-backend (KFD, XDNA, virtio). Adding `MacOsDriver` is the intended, linear extension.
- Our `libmacgpu` C++ shim is ~1500 LOC vs. a hypothetical libhsakmt-macos port at ~10k LOC.
- The 4–5 non-driver ROCR files that still call `hsaKmt*` directly (e.g., `amd_aql_queue.cpp`) are the real coupling pain. Better to push those through `core::Driver` virtuals (matching what XDNA already does) than reimplement libhsakmt on macOS.

## 5. Effort Estimate

Rough sizing, one engineer full-time, assumes eGPU hardware in hand:

| Stage | Description | Effort |
|-------|-------------|--------|
| 0     | DEXT hardware bring-up (riskiest; depends on hardware time & firmware cooperation) | 6–12 weeks |
| 1     | `libmacgpu` C++ shim | 2–3 weeks |
| 2A    | `os_darwin.cpp` | 1–2 weeks |
| 2B    | `MacOsDriver` in ROCR + rerouting non-driver hsaKmt sites | 3–5 weeks |
| 3     | CLR Darwin pass | 1–2 weeks |
| 4     | Math libs compile / smoke-test | 2–3 weeks |
| 5     | TheRock integration + CI | 1–2 weeks |
| 6     | Distribution / entitlement / notarization | Calendar-bound on Apple review |

**Total to first PyTorch HIP workload on macOS eGPU: 4–6 months of focused effort, gated on Stage 0.** Stage 0 is the make-or-break — if firmware cooperation or Thunderbolt DMA semantics block it, nothing downstream matters.

## 6. Open Questions

1. **Firmware.** Is `linux-firmware` redistribution compatible with macOS app-bundle distribution? (GPL headers say yes, AMD EULA on firmware blobs needs verification.)
2. **Apple PCI transport entitlement.** TinyGPU has it — what's the expected lead time for a second applicant in this space?
3. **TB4 ↔ Resizable BAR.** Does PCIDriverKit expose enough to negotiate resizable BAR for >256 MB VRAM aperture? If not, we lose the whole-VRAM CPU mapping path, have to rely on BAR-windowed access via SDMA. This is a significant perf (not correctness) hit.
4. **Multi-process.** The DEXT escape model (`com.apple.developer.driverkit.allow-any-userclient-access`) supports multiple client processes. ROCR assumes a queue-owner is a single process today — validate nothing breaks when a second HIP process attaches.
5. **Kernel preemption / timeout-detection-and-recovery (TDR).** No GPU watchdog on DEXT side today. First infinite loop kernel could require manual DEXT reload. Need at least a software timeout in the ring-submit path.

## 7. Alternatives Considered

- **Port libhsakmt directly.** Rejected — much larger surface (203 fns vs ~50), redundant with what `core::Driver` already abstracts.
- **Skip ROCR, extend HIP to call DEXT directly.** Rejected — HIP depends on HSA concepts (agents, signals, queues) throughout; re-inventing them below HIP creates a permanent macOS fork.
- **Use the existing `hip-remote-client` as a trap-to-local-DEXT layer.** Rejected — semantics differ (TCP RPC vs direct memory), latencies differ by orders of magnitude, and the RPC layer serializes what should be parallel queue submits. Test suite from `hip-remote-client` is still useful for validation.
- **Write a macOS KFD emulator (shim `/dev/kfd`-like interface via a user-mode FUSE-style driver, then run real libhsakmt unchanged).** Rejected — macOS FUSE is third-party and unstable, and we'd still need all the logic of `MacOsDriver` anyway; adds a layer.
- **Build on Asahi Linux on Apple Silicon + eGPU.** Out of scope — this plan is explicitly macOS. Asahi is a separate, also-valid path.
- **Metal-backed HIP.** Rejected — different programming model, different ISA, would be months of ISA translation work, and performance would be capped at Apple's Metal driver rather than at the hardware.

## 8. Required Changes to Current Branch

Before starting Stage 1, fix-ups to current `users/powderluv/macos-egpu-driver`:

- Current DEXT build requires a generated `ROCmGPU.xcodeproj` (I added one locally during validation). Commit this so the README's `./scripts/build.sh` works out of the box.
- `build.sh` references `ROCmGPU.xcodeproj` but the project wasn't in-tree. Either commit the `.xcodeproj` or rewrite `build.sh` to generate it (CMake+`xcodeproject` or a script).
- Python `userspace_driver/pyproject.toml` declares only Linux packages under `tool.setuptools.packages`. Missing `amd_gpu_driver.backends.macos` and `amd_gpu_driver.backends.windows`. Install works in editable mode but non-editable wouldn't ship the macOS backend.
