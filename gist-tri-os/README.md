# gfx1201 (RDNA4) `lite::` GPU bring-up — macOS eGPU / Linux / Windows

Experimental effort to run **ROCm + PyTorch on a gfx1201 GPU** (AMD Radeon RX 9070 XT /
AI PRO R9700, RDNA4, PCI `0x1002:0x7551`) through a shared, firmware-light **`lite::`
ROCr backend** that programs the GPU's compute queue directly from userspace — on three
OSes that lack the usual ROCm/KFD kernel path:

- **macOS** (Apple Silicon) — AMD eGPU over Thunderbolt, via a DriverKit DEXT (no kernel driver).
- **Linux** (x86) — `amdgpu_lite` minimal kernel shim + a userspace bring-up.
- **Windows** — C++ ROCr `WindowsLiteDriver` over `wddm_lite`/`D3DKMTEscape` (production `amdgpu_wddm` KMD), linked into `amdhip64_7.dll`; **torch smoke 9/9 single-process and 9/9 per-test isolate / multi-process** on gfx1201 (matmul/GEMM, elementwise, transpose, dot, matrix-vector) with the **MES-backed compute queue HW-validated**, the gfx12 scratch wave resolved (#62), and per-process re-bring-up fixed + default-on (#66). Only the separate MCDM-KMD track remains blocked (`dxgmms2` VidMm init, §6).

End goal: PyTorch unit tests passing on gfx1201 via the `lite::` path on all three. This
gist is the *recreate-the-builds + ramp-up* pointer and is kept updated over time.

> ⚠️ **Research bring-up, not a product.** Nothing here is upstreamed or self-contained.
> It targets specific hardware and requires a per-power-cycle bring-up. Internal-infra
> references (an x86 validation host `shark-a`, an iBoot PDU, a BMC) are placeholders;
> credentials/IPs are redacted.

## Status at a glance (2026-07-21)

Branches (forks): `powderluv/rocm-systems` @ `a6de5ec13c` (`users/powderluv/macos-os-darwin`),
`powderluv/TheRock` @ `61159fb9a` (submodule → `a6de5ec13c`),
`powderluv/rocm-libraries` @ `01960882c8c` (`users/powderluv/macos-egpu` — MIOpen).

| OS | State |
|---|---|
| **macOS eGPU** | Full ROCm SDK + ROCr (`lite::`+macOS backend) + a PyTorch wheel build & link. **torch smoke 13/14 on gfx1201** — every GPU op passes: matmul/GEMM, elementwise, reductions, dot, matrix-vector, register-spilling **scratch**, and **conv** (single-process **and** 14-process isolate, **no wedges**). Blockers fixed this run: **scratch (#15)** — a per-dispatch `RESOURCE_LIMITS` PM4 block re-zeroed `COMPUTE_TMPRING_SIZE` (register-aliasing); fixed + default-on (`bcea652b27`). **multi-dispatch / queue reliability (#19)** — coherent-DART-DMA for tensors+queue memory + a `DestroyDirectQueue` doorbell-clear so multi-process needs no `SKIP_DESTROY` leak (`d9e8af0332`). **conv/MIOpen (#65)** — two `.so`-vs-`.dylib` macOS port bugs: HIPRTC `findIsa` `dlopen`'d `libamdhip64.so.7` (ELF) instead of `.dylib` → runtime compiles had no target arch ("Please provide architecture"); and MIOpen's `dynamic_library_postfix` was `.so` so the CK grouped-conv loader missed `libMIOpenCKGroupedConv_gfx1201.dylib`. Fixed in clr (`2b802b3a64`) and MIOpen (`01960882c8c`). **MES-backed path is now the default run-recipe path** (`run-torch-egpu.sh`; opt out with `ROCR_MACOS_DIRECT=1`), matching the Linux/Windows lite:: path. It matches the direct path exactly — **13/14 single-process and 9/9 per-test isolate**, and MES-vs-direct smoke time is equal (~60s vs ~58s, so the old ~500ms-scheduler-stall caveat no longer applies). The cross-process wedge it used to hit (2nd process hangs — the same MES scheduler-ring HQD `status=4096` as Windows #66) is fixed by porting the #66 scheduler-HQD teardown-at-exit to macOS (`525d9fa653` / TheRock `cbd2dfb70`; `#if defined(_WIN32) || defined(__APPLE__)`, opt out `ROCR_MACOS_MES_TEARDOWN_AT_EXIT=0`) → MES isolate 9/9 (was 0/9), 5/5 sequential procs. **Open:** only `test_openblas_is_selected_blas` (the macOS torch build isn't OpenBLAS — GPU-independent; LAPACK works). |
| **Linux (shark-a)** | `lite::` reaches **BOOTLOAD_COMPLETE → MES engine → direct-MEC real compute wave + 1025 sustained dispatches**; committed (`916f4935b`…`c7cc6565c`) and re-verified working (clean reboot → `insmod amdgpu_lite` → `BOOTLOAD_STATUS=0x8000003f` → DIRECT-MEC NOP+fence). |
| **Windows** | **torch matmul computes end-to-end + exits cleanly** via ROCr `WindowsLiteDriver` over `wddm_lite`/`D3DKMTEscape` (production `amdgpu_wddm` KMD), statically linked into `amdhip64_7.dll`. **MES-backed compute queue + scheduler ring SOLVED** (start MES on the driver path, skip the firmware HQD reclaim, activate the scheduler-ring HQD image-only before `MAP_SCHEDULER`; doorbell dead under WDDM → wptr MMIO-poked `ROCR_WINDOWS_MES_MMIO_WPTR=1`). **~14-dispatch ceiling (#62) FIXED** — it was a HQD `QUEUE_SIZE` (9=1024dw) vs 8192dw ring mismatch; deriving QUEUE_SIZE from the ring retired 150/150 register-spilling dispatches, so **the scratch GEMM now computes** (the old "#57 scratch stall" is resolved). **teardown hang (#63) FIXED** — at process exit torch's fatbin dtor spun forever in `SyncAllStreams→HostQueue::finish` because Windows kills the completion threads before onexit; skip the drain when `RtlDllShutdownInProgress` (`751e1ba8b5`, default-on). **torch smoke 9/9 single-process** — every core GPU op passes: matmul/GEMM, batch-mm, `@`, elementwise, transpose, dot, matrix-vector, matmul-variant. **#64 (the earlier "5 op failures") was a harness artifact, not op bugs** — the 5 ops produce correct values in one process; the failures only appeared in per-test *isolate* mode, where each test is a fresh subprocess. **Per-process re-bring-up (#66) FIXED** — the cross-process wedge was the MES **scheduler-ring HQD** (me=3/pipe=0) left active by the prior process: `hipMalloc` succeeds but the next `hsa_queue_create` fails (`hipErrorInvalidValue`) because the fresh `EnsureMesScheduler`'s dequeue-drain times out on the now-dead ring → scheduler `SET_HW_RESOURCES` never serviced (`status=4096`). Fix: a `std::atexit` hook deactivates this process's scheduler HQD while the MES is still healthy (its drain completes at once → `active=0`), so the next process starts clean — synchronous MMIO on the calling thread, no dead-thread dependency (`a3adf7c6a1`; **default-on on Windows** `a6de5ec13c`, opt out `ROCR_WINDOWS_MES_TEARDOWN_AT_EXIT=0`; macOS/Linux stay opt-in). HW-validated (no env set): **isolate smoke → 9/9 (was 4/9); 6/6 sequential processes; single-process 9/9 with clean exit** (no teardown-path regression) — 16 processes on one power cycle, baseline wedges at #2/#3. Separate MCDM-KMD track still blocked at `dxgmms2` VidMm init (§6), independent. |

**Open blockers (updated 2026-07-21):** on macOS the GPU-side blockers are cleared — gfx12 *scratch*
(#15), *multi-dispatch/queue reliability* (#19), and *conv/MIOpen* (#65) are all fixed + HW-validated;
torch smoke is **13/14** with every GPU op working, and the only remaining fail is the GPU-independent
`test_openblas_is_selected_blas` build-config check. The gfx12 scratch/register-spill blocker is also
resolved on Windows (HQD `QUEUE_SIZE`/ring fix, #62), and **Windows torch smoke is now 9/9 single-process and 9/9 per-test isolate / multi-process by
default** — the earlier "5 op failures" (#64) were a per-test-isolate harness artifact, not op bugs
(the ops compute correct values in one process); the cross-process wedge is fixed by the #66
`std::atexit` scheduler-HQD teardown (default-on on Windows), so 16 processes run on one power cycle. Remaining: on the
separate MCDM-KMD track, the `dxgmms2` VidMm-init crash just past adapter-start.

## Upstream code

- ROCr `lite::` + macOS backend: **`ROCm/rocm-systems`**, branch `users/powderluv/macos-os-darwin`
  (`projects/rocr-runtime/runtime/hsa-runtime/core/{driver/lite,driver/macos,runtime}`).
- Super-build / SDK: **`ROCm/TheRock`** (+ `powderluv/TheRock` fork), branch `users/powderluv/egpu-build`.
- Windows userspace bring-up backend (`userspace_driver/python/amd_gpu_driver/backends/windows/`): **`ROCm/TheRock`**, branch `users/powderluv/macos-os-darwin` (compute bring-up + shader dispatch committed `f2b12969c`, pushed to the `powderluv/TheRock` fork).
- PyTorch macOS port: `external-builds/pytorch/pytorch`, branch `users/powderluv/macos-egpu`.
- In-repo docs: `docs/development/macos_egpu_port.md`, `docs/development/build_system.md`.

---

## lite:: GPU backend — architecture & ramp-up

This section orients a new contributor to the **`lite::`** ROCr GPU backend: the shared, firmware-light direct-queue / MES dispatch layer used to drive **gfx1201 (RDNA4)** GPUs across macOS, Linux, and Windows, without the traditional KFD kernel driver. All file paths are absolute.

### 1. What "lite::" is, and why

`lite::` (`rocr::AMD::lite`) is a **shared, OS-agnostic ROCr backend** that programs an AMD GPU's compute queue (MEC HQD or MES-mapped queue) *directly from userspace*, instead of going through `libhsakmt` and the Linux `amdgpu`/KFD kernel driver. It exists because the macOS eGPU port has **no kernel-mode GPU driver** — the GPU is reached through a DriverKit DEXT for MMIO/BAR/DMA only — so ROCr must build the queue's MQD, ring, doorbell and (optionally) drive the MES scheduler itself. The same logic is reused on Linux (`amdgpu`-lite, talking DRM ioctls directly) and on Windows (the C++ `WindowsLiteDriver` over `wddm_lite`/`D3DKMTEscape`), so the three OSes converge on one queue implementation.

The core of the shared layer is one file plus its header:

- `/Users/anush/github/TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/driver/lite/amd_lite_direct_queue.cpp` (the implementation; ~100 KB)
- `/Users/anush/github/TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/inc/amd_lite_direct_queue.h` (the public API + key types)

**Key types** (all in `amd_lite_direct_queue.h`):
- `DirectQueueState` — the per-queue handle: `queue_id`, `queue_index`, `doorbell_index`, the ring/rptr/wptr GPU addresses and their CPU-mapped pointers (`ring_cpu`, `wptr_cpu`, `rptr_cpu`, `doorbell_cpu`), `framebuffer_base`, the MQD `DirectQueueLayout`, and a `bool mes_backed` flag that records whether the queue was mapped via MES or programmed as a raw HQD.
- `DirectQueuePlatform` — **the platform abstraction**: a pure-virtual interface of low-level primitives the shared code needs (`ReadMmio32`/`WriteMmio32` against a GC register base, `ZeroGpuMemory`/`WriteGpuMemory32`, `GpuMemoryCpuPointer`, `DoorbellCpuPointer`, `EnsureDoorbellAperture`, `FlushHdp`, `SleepUs`, optional `AllocateQueueMemory`/`FreeQueueMemory`). Each OS subclasses this; the shared `lite::` queue logic never calls OS APIs directly.
- `DirectQueueOptions` — the per-call switches, notably `use_mes_queue` (MES vs direct), `use_firmware_dequeue` (RESET_WAVES firmware dequeue on teardown), `force_reclaim`, `trace`, and settle/sleep timings.
- `DirectQueueLayout` / `DirectQueueMqd` / `DirectQueueMemory` — MQD/ring/EOP/rptr/wptr offset layout and the backing allocation descriptor.

The free functions `CreateDirectQueue`, `DestroyDirectQueue`, `SubmitDirectQueue`, `ReadDirectQueueRptr`, and `SetDirectQueueScratch` take a `const DirectQueuePlatform&` plus a `DirectQueueState&` and are what each OS driver calls.

### 2. Code map: shared layer vs per-OS drivers vs Python bring-up

**Shared `lite::` queue layer (rocm-systems / ROCr):**
- `.../core/driver/lite/amd_lite_direct_queue.cpp` + `.../core/inc/amd_lite_direct_queue.h` — the OS-agnostic direct-queue/MES code described above.
- `.../core/driver/lite/linux/` — the Linux amdgpu-lite transport: `amd_lite_linux_driver.cpp`, `amd_lite_linux_transport.cpp`, `amdgpu_lite_uapi.h` (raw DRM `ioctl` to `/dev/dri`, no libhsakmt).
- `.../core/runtime/amd_lite_aql_queue.cpp` + `.../core/inc/amd_lite_aql_queue.h` — the **Linux** AQL queue/agent that sits on top of the lite direct queue. (Note: this is *not* compiled on macOS — see §4.)

**Per-OS ROCr drivers (each implements `core::Driver` and `lite::DirectQueuePlatform`):**
- macOS: `/Users/anush/github/TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/driver/macos/amd_macos_driver.cpp` (+ header `.../core/inc/amd_macos_driver.h`). `MacOsDriver` is declared `final : public core::Driver, private lite::DirectQueuePlatform`. Its `CreateDirectComputeQueue`/`DestroyDirectComputeQueue`/`SubmitDirectCompute`/`ReadDirectComputeRptr`/`SetQueueScratch` simply delegate to `lite::CreateDirectQueue(*this, ...)` etc., passing `MacDirectQueueOptions()`. The `DirectQueuePlatform` virtuals are implemented privately over `libmacgpu` / the DEXT (BAR-mapped VRAM window, doorbell aperture, MMIO escapes).
- Linux: `LinuxAmdgpuLiteDriver` (`.../core/inc/amd_lite_linux_driver.h`, `.../core/driver/lite/linux/amd_lite_linux_driver.cpp`), `DriverType::LINUX_AMDGPU_LITE`.
- For contrast, the legacy KFD/XDNA/virtio backends live alongside under `.../core/driver/{kfd,xdna,virtio}/`.

**macOS AQL queue / agent (the HSA-facing layer on macOS):**
- `/Users/anush/github/TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/runtime/amd_macos_aql_queue.cpp` (+ `.../core/inc/amd_macos_aql_queue.h`) — `MacAqlQueue` is the `core::Queue` ROCr hands kernels to. It holds a `MacOsDriver::DirectComputeQueue direct_queue_` (i.e. a `lite::DirectQueueState`), translates AQL kernel-dispatch packets into PM4, stages kernargs into the BAR window, manages dispatch/GPU scratch, and submits via `driver_.SubmitDirectCompute(...)`. The companion agent is `.../core/runtime/amd_macos_agent.cpp`.

**Userspace Python bring-up (super-project, not ROCr):**
- `/Users/anush/github/TheRock/userspace_driver/python/amd_gpu_driver/` is the Python package that brings the GPU *up* (PSP/SOS → GFX → MEC → MES → clock-gating → scheduler) before the C++ runtime attaches. Shared structure:
  - `backends/` — `base.py` (the `DeviceBackend` ABC + `MemoryHandle`/`QueueHandle`/`SignalHandle` dataclasses), and per-OS backends `backends/kfd/`, `backends/windows/`, `backends/macos/`. (There is **no `amdgpu_lite` Python backend dir** — the Linux/amdgpu-lite path is the C++ `LinuxAmdgpuLiteDriver`; the Python `kfd` backend is the Linux/KFD bring-up.) `device.py` auto-selects: `windows` on win32, else `kfd`, with explicit `macos`.
  - `commands/` (`pm4.py`, `sdma.py`, `ring.py`), `ioctl/` (`drm.py`, `kfd.py`, `helpers.py`), plus shared `gpu/` (per-family register configs: `rdna4.py` etc.), `kernel/` (ELF/descriptor parsing), `memory/`, `sync/`.
  - The macOS backend (`backends/macos/`) is the richest: `psp_bootloader.py`, `psp_ring.py`, `psp_cmd.py`, `gfx_autoload.py`/`gfx_psp_autoload.py`, `gmc.py`, `smu.py`, `ih_init`-equivalents, `queue.py`, `iokit_client.py`, `ip_discovery.py` — it talks to the DEXT via IOKit.
  - The DEXT itself: `/Users/anush/github/TheRock/userspace_driver/macos_driver/` (DriverKit extension, service name `ROCmGPUDriver`, bundle `ai.rocm.gpu.driver`).

### 3. The ROCr pluggable-driver seam (where a new OS backend plugs in)

The abstract interface is `rocr::core::Driver` in:
`/Users/anush/github/TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/inc/driver.h`

It is a ~50-method pure-virtual class (`Init`, `Open`, `Close`, `GetSystemProperties`, `GetNodeProperties`, `AllocateMemory`/`FreeMemory`, `CreateQueue`/`DestroyQueue`, `ExportDMABuf`, etc.). To add a platform:

1. Add a value to the `DriverType` enum in `driver.h` (it already has `XDNA`, `KFD`, optional `KFD_VIRTIO`, plus compile-gated `LINUX_AMDGPU_LITE` (`#if defined(__linux__)`) and `MACOS_DEXT` (`#if defined(__APPLE__)`)).
2. Implement a `core::Driver` subclass with a static `DiscoverDriver(std::unique_ptr<core::Driver>&)` factory (returns `HSA_STATUS_SUCCESS` with a live driver, or error + null when no device).
3. Register that factory in the **compile-gated discovery array in** `/Users/anush/github/TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/runtime/amd_topology.cpp`. On `__APPLE__` only `MacOsDriver::DiscoverDriver` is compiled in; on `__linux__` the array is `KfdDriver`, `XdnaDriver`, (virtio), `LinuxAmdgpuLiteDriver`. `runtime.cpp` then branches on `agent->driver().kernel_driver_type_` for backend-specific behavior.

For a GPU backend specifically, you additionally implement `lite::DirectQueuePlatform` (privately, as `MacOsDriver` does) so the shared `lite::` queue code can drive your hardware, then delegate `CreateDirectComputeQueue`/`SubmitDirectCompute`/etc. to the `lite::` free functions.

### 4. MES vs direct-MEC dispatch paths

`lite::` supports two ways to get a compute queue running, selected per call via `DirectQueueOptions::use_mes_queue` (in `CreateDirectQueue`, `amd_lite_direct_queue.cpp`):

- **Direct MEC-HQD path (the ROCr driver default; the `run-torch-egpu.sh` recipe now opts into MES by default):** `lite::` builds the MQD and writes the `CP_HQD_*` registers itself (via `DirectQueuePlatform::WriteMmio32` against the GC base), activating a specific pipe/HQD (`DirectQueuePipe`/`DirectQueueHqd`/`DirectQueueDoorbell`). `SubmitDirectQueue` writes PM4 into the ring (with a TYPE-3 NOP ring-wrap guard so a packet never straddles the ring end → avoids the CP 0x1000 fault), bumps the VRAM wptr, flushes HDP, re-selects the HQD, optionally pokes `CP_HQD_PQ_WPTR_*` (gated by `ROCR_MACOS_DIRECT_QUEUE_MMIO_WPTR`), and rings the doorbell. Teardown uses a firmware dequeue (`use_firmware_dequeue` → `RESET_WAVES`) plus a `SPI_COMPUTE_QUEUE_RESET` pairing.
- **MES-backed path (`use_mes_queue=true`):** instead of programming the HQD directly, `CreateDirectQueue` calls `MapLegacyQueueWithMes(...)`, which submits MES API frames (`kMesOpcodeAddQueue`, `kMesOpcodeSetHwResources`, scheduler `SET_HW_RSRC`/status, with `kMesAddQueueMapLegacyKq`) so the **MES firmware owns HQD activation**; `mes_backed` is set true. On submit, the MES-backed branch just advances the wptr and writes the doorbell value (`*queue.doorbell_cpu = new_wptr`) — the scheduler does the rest. This was added to lift the ~13–15-dispatch ceiling seen on the raw direct path (selected on macOS via `ROCR_MACOS_USE_MES_QUEUE`).

Important nuance verified in source: on **macOS the HSA-facing AQL queue is `MacAqlQueue`** (`amd_macos_aql_queue.cpp`), which sits on top of the `lite::` *direct queue*. `LiteAqlQueue`/`amd_lite_aql_queue.cpp` is the **Linux** lite agent and is not compiled into the macOS dylib — a common point of confusion. So macOS = `MacAqlQueue` (AQL→PM4) + `lite::` direct/MES queue (driver); Linux = `LiteAqlQueue` + `LinuxAmdgpuLiteDriver` + same `lite::` direct/MES core.

### 5. Where to bring up the hardware before any of this runs

ROCr's `lite::` backend *attaches to an already-initialized GPU*. The one-time-per-power-cycle bring-up (PSP/SOS → GFX → MEC → MES → clock-gating → MES scheduler) is the Python phase-9 script:
`PYTHONPATH=userspace_driver/python python3 -u userspace_driver/python/try_phase9_doorbell.py` (see `docs/development/macos_egpu_port.md` §7 for the exact env). Bring-up is per power-cycle; a wedge (HSA 0x1000) needs a physical eGPU power-cycle, not just a re-run.


---

## Build & run on macOS (Apple Silicon, AMD eGPU, gfx1201)

> Status (honest, 2026-07-26): This is a research bring-up, not a supported path. The full ROCm SDK, ROCr (with the macOS backend), and a PyTorch wheel **build and link** on macOS arm64, and PyTorch runs a broad set of GPU ops on a Thunderbolt-attached RX 9070 XT (gfx1201): **`pytorch_smoke_test.py` = 13/14**, with **every GPU op passing** — matmul/GEMM, elementwise, reductions, dot, matrix-vector, register-spilling **scratch**, **conv**, and multi-dispatch/multi-process (single-process *and* 14-process isolate, no wedges). Scratch (#15), multi-dispatch/queue reliability (#19), and conv/MIOpen (#65) are all fixed (scratch default-on; conv unblocked by two `.so`→`.dylib` macOS port fixes in clr HIPRTC `findIsa` and MIOpen `dynamic_library_postfix`). **Open issue:** only the non-GPU `test_openblas_is_selected_blas` (the build isn't OpenBLAS; LAPACK works). Validation bar: the smoke suite (via `run-torch-egpu.sh`, single-process for a clean count or isolate for crash-tolerance), not just `torch_baseline.py`. The run recipe now defaults to the MES-backed path (opt out `ROCR_MACOS_DIRECT=1`), validated equal to direct (13/14 single-process, 9/9 isolate).

### 0. Hardware / platform context

- GPU: AMD Radeon RX 9070 XT (`0x1002:0x7551`, RDNA4, **gfx1201**) in a Razer Core X V2 eGPU enclosure over Thunderbolt.
- Host: Apple Silicon (arm64) MacBook Pro, macOS 26.x.
- Constraints that shape the run recipe: only a **256 MiB BAR0** VRAM window (ReBAR not negotiated by macOS), DART IOMMU, and a custom DriverKit extension (DEXT) instead of a kernel driver. All CPU↔VRAM transfer goes through SDMA / host-blit, not direct memcpy.
- The DEXT install + Apple entitlement flow is a separate prerequisite (`com.apple.developer.driverkit.transport.pci` / `userclient-access`) and is out of scope for this build section. This section assumes the DEXT is already activated (`systemextensionsctl list` shows the driver `[activated enabled]`) and the eGPU enumerates as `0x7551`.

### 1. Prerequisites

- **Xcode** with the macOS SDK. The reference tree configured against `MacOSX26.4.sdk` (`CMAKE_OSX_SYSROOT`), `CMAKE_OSX_ARCHITECTURES=arm64`, `CMAKE_OSX_DEPLOYMENT_TARGET=13.0`. (VERIFY your exact SDK; some torch patches — `-isysroot`, `-D_VSTD=std` — are sensitive to the SDK's libc++ version and may need revisiting per SDK roll.)
- **Homebrew** `llvm` (for `llvm-ar`) and `libomp` — used by the PyTorch build.
- **python3.11** (the wheel is `cp311`).
- **cmake `>=3.27,<4`** — cmake 4.x trips older third-party in the torch tree. Use the project venv's cmake.
- `xcrun --show-sdk-path` must resolve (used to pass `SDKROOT` to `hipcc`).

### 2. TheRock super-build: macOS ROCm SDK for gfx1201

The macOS SDK lives in a dedicated build tree at `/Users/anush/github/TheRock/build-macos-egpu`, with the installed SDK under `build-macos-egpu/dist/rocm`. The reference branch is `users/powderluv/egpu-build` in `/Users/anush/github/TheRock`.

Key points from the configured cache (`build-macos-egpu/CMakeCache.txt`):
- Targets are set via **`THEROCK_AMDGPU_TARGETS=gfx1201`** (the `*_TARGETS` form, not `THEROCK_AMDGPU_FAMILIES`, which is empty here).
- **`THEROCK_ENABLE_CORE_RUNTIME=OFF`** — ROCr is *not* built by the super-build; it is built separately (step 3) so the macOS backend can be iterated independently.
- Enabled for the torch math stack: `COMPILER`, `HIP_RUNTIME`, `BLAS`, `FFT`, `RAND`, `PRIM`, `SOLVER`, `SPARSE`, `MIOPEN`, `HIPBLASLTPROVIDER`, `COMPOSABLE_KERNEL`, `IREE_COMPILER/IREE_LIBS`, `FUSILLIPROVIDER`, `HIPKERNELPROVIDER`, `HIPDNN`.

Configure (adapt the toggles to the cache above; the exact configure line is project-specific — confirm the enable flags rather than copying a generic one):

```bash
cmake -B /Users/anush/github/TheRock/build-macos-egpu \
      -S /Users/anush/github/TheRock \
      -GNinja \
      -DTHEROCK_AMDGPU_TARGETS=gfx1201 \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_OSX_ARCHITECTURES=arm64
# (plus the THEROCK_ENABLE_* toggles matching build-macos-egpu/CMakeCache.txt)
```

Build the SDK (long):

```bash
ninja -C /Users/anush/github/TheRock/build-macos-egpu
```

Result: the macOS ROCm SDK at `build-macos-egpu/dist/rocm` — `bin/hipcc`, `bin/amdclang++`, `lib/llvm/bin/{clang++,llvm-ar}`, `lib/llvm/amdgcn`, plus the math/HIP dylibs and CMake config packages used by the torch build.

### 3. ROCr (lite:: + macOS backend) and staging `libhsa-runtime64`

ROCr is built out-of-tree at `build-macos-egpu/core/ROCR-Runtime/build`. The macOS backend source lives in `rocm-systems` (submodule, branch `users/powderluv/macos-os-darwin`):
- `rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/runtime/amd_macos_aql_queue.cpp`, `amd_macos_agent.cpp`, `amd_lite_*` (the shared `lite::` direct/MES path).
- `rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/driver/macos/amd_macos_driver.cpp`.

Incremental rebuild of just the runtime (use `SDKROOT` so the existing `build.ninja` compiles against the current SDK; do **not** re-`cmake -B` without `-S`):

```bash
SDKROOT=$(xcrun --show-sdk-path) \
  ninja -C /Users/anush/github/TheRock/build-macos-egpu/core/ROCR-Runtime/build
```

This produces `core/ROCR-Runtime/build/rocr/lib/libhsa-runtime64.1.21.0.dylib` (install name `@rpath/libhsa-runtime64.1.dylib`).

**Stage the fresh dylib over the SDK copy** so torch (which resolves ROCm dylibs by absolute SDK rpath) picks it up:

```bash
SDK=/Users/anush/github/TheRock/build-macos-egpu/dist/rocm
# back up first (a .pre-*fix.bak convention is already in use here)
cp "$SDK/lib/libhsa-runtime64.1.21.0.dylib" "$SDK/lib/libhsa-runtime64.1.21.0.dylib.bak"
cp /Users/anush/github/TheRock/build-macos-egpu/core/ROCR-Runtime/build/rocr/lib/libhsa-runtime64.1.21.0.dylib \
   "$SDK/lib/libhsa-runtime64.1.21.0.dylib"
```

The `libhsa-runtime64.1.dylib` / `libhsa-runtime64.dylib` symlinks in `$SDK/lib` already point at the versioned file. After staging, `import torch` should still load (ABI unchanged).

### 4. PyTorch wheel (USE_ROCM=1, gfx1201, BLAS=vecLib)

The torch source is checked out under `external-builds/pytorch/pytorch` on branch **`users/powderluv/macos-egpu`** (torch nightly `2.13.0a0`; current head `592bfedb57`). The macOS port is a stack of `__APPLE__`-guarded patches:
- `bf08e0250f` — enable USE_ROCM on Apple; `rocm_smi` optional; force HIP compiler-id / ROCm root; HIP flags `-x hip -isysroot $CMAKE_OSX_SYSROOT -D_VSTD=std -DNDEBUG`; `std::memcpy`→`__builtin_memcpy` in `Half.h` + `int4mm.{cu,hip}`; link roctx into `torch_hip`. (kernarg-translation fix #13 lives in rocm-systems, committed `60ec472f91`.)
- `592bfedb57` — make c10 `SetDevice` device-restore non-throwing on Apple (so a wedged-queue error raises a catchable `c10::AcceleratorError` instead of `std::terminate`).

> Note: the in-tree `external-builds/pytorch/build_prod_wheels.py` targets Linux/Windows and uses OpenBLAS; it is **not** the macOS recipe. The macOS wheel is built with a manual `setup.py bdist_wheel` and the explicit env below (from the build memory).

Build env (cp311 venv with cmake `>=3.27,<4`; `$SDK = build-macos-egpu/dist/rocm`):

```bash
export ROCM_PATH=$SDK HIP_PATH=$SDK
export PATH="$SDK/bin:$SDK/lib/llvm/bin:$(brew --prefix llvm)/bin:$PATH"
export USE_ROCM=1 USE_CUDA=0
export PYTORCH_ROCM_ARCH=gfx1201
export BLAS=vecLib
# disables (not supported / not needed on this path):
export USE_FLASH_ATTENTION=0 USE_MEM_EFF_ATTENTION=0 \
       USE_KINETO=0 USE_MAGMA=0 USE_MKLDNN=0 USE_FBGEMM=0 \
       USE_DISTRIBUTED=0   # + composable-kernel off
# Homebrew llvm-ar for thin archives:
export CMAKE_ARGS="-DCMAKE_AR=$(brew --prefix llvm)/bin/llvm-ar"
# libomp from Homebrew on the include/lib path as needed.

cd /Users/anush/github/TheRock/external-builds/pytorch/pytorch
python setup.py bdist_wheel
```

Output (cp311, macosx arm64): e.g. `external-builds/pytorch/pytorch/dist/torch-2.13.0a0+git592bfed-cp311-cp311-macosx_26_0_universal2.whl` (~121 MB). The wheel is **not self-contained** — ROCm dylibs resolve via the absolute SDK rpath baked into `libtorch_hip` (delocate later).

Install into the dedicated run venv:

```bash
/Users/anush/github/TheRock/external-builds/pytorch/.venv-torch/bin/pip install \
  /Users/anush/github/TheRock/external-builds/pytorch/pytorch/dist/torch-2.13.0a0+git592bfed-cp311-cp311-macosx_26_0_universal2.whl
```

### 5. eGPU bring-up + run recipe

Two things are required per power-cycle: (a) **phase-9 GFX bring-up** of the card, then (b) running torch with the validated ROCr direct-queue env.

**Bring-up** is `SKIP_NOP`-only. Do **not** add `PHASE9_MAP_SCHED` / `PHASE9_SEND_SET_HW_RSRC` — ROCr's `hsa_queue_create` maps the MES scheduler itself via KIQ `MAP_SCHEDULER`; pre-mapping double-maps it and wedges the scheduler ring (→ `SET_HW_RESOURCES` timeout → `0x1000` → `hsa_queue_create failed` → `hipErrorIllegalState` on the first op).

```bash
# Drain/reset the eGPU (power-cycle via iBoot G2 + wait for re-enumerate).
# Use egpu_drain.py for a soft drain; egpu-replug.sh / egpu-bringup.sh for a full
# power-cycle when the card wedges after ~8-9 soft cycles.
python3 /Users/anush/github/claude-rocm-workspace/egpu_drain.py

# Phase-9 GFX bring-up (run with the driver venv, NOT the torch venv):
PHASE9_SKIP_NOP=1 \
PYTHONPATH=/Users/anush/github/TheRock/userspace_driver/python \
  /Users/anush/github/TheRock/.venv/bin/python -u \
  /Users/anush/github/TheRock/userspace_driver/python/try_phase9_doorbell.py
# success == log line "GFX bring-up complete" (KIQ activation is flaky cold; retry the
# whole drain+bring-up on failure — see the loop in run-torch-egpu.sh).
```

**Run env** (matches `run-torch-egpu.sh` / `run-multi-dispatch-test.sh`; the older `ROTATE_BACKING/MAX_QUEUES/PQ_CONTROL/DEQUEUE_AFTER_SUBMIT/HOST_BLIT_ONLY` knobs are now defaults in the lite:: refactor and no longer needed):

```bash
SDK=/Users/anush/github/TheRock/build-macos-egpu/dist/rocm
export ROCM_PATH=$SDK HIP_PATH=$SDK
export DYLD_LIBRARY_PATH="$SDK/lib:$SDK/lib/llvm/lib"
export AMD_GPU_MACOS_FORCE_DIRECT_COMPUTE=1
export ROCR_MACOS_HOST_BLIT_ONLY=1
export ROCR_MACOS_AQL_SKIP_HOST_COPYBACK=1
# MES-backed path is the DEFAULT (the DestroyDirectQueue + #66/#67 teardown fixes
# removed the old SKIP_DESTROY per-process leak, so SKIP_DESTROY is no longer set).
# Opt into the legacy direct path with ROCR_MACOS_DIRECT=1.
export ROCR_MACOS_USE_MES_QUEUE=1
```

The whole drain → bring-up (with retry) → run is automated end-to-end by:

```bash
bash /Users/anush/github/claude-rocm-workspace/run-torch-egpu.sh
```

### 6. Verification

The validation bar is `torch_baseline.py` (`/Users/anush/github/claude-rocm-workspace/torch_baseline.py`), which exercises **12 non-scratch ops** with CPU-generated inputs moved to device: `ones+1`, `mul`, `sub`, `abs`, `le` (bool), `matmul128` (hipBLASLt vs CPU ref), `sum`/`mean`/`max` (native reductions — exercise the #13 kernarg-translation fix), `relu`, `neg`, `transpose`, `cat`, `reshape`, `slice`, `broadcast_add`. It prints `=== N/N passed ===` and exits non-zero on any FAIL.

```bash
bash /Users/anush/github/claude-rocm-workspace/run-torch-egpu.sh
# (or, after manual bring-up + env from §5:)
/Users/anush/github/TheRock/external-builds/pytorch/.venv-torch/bin/python \
  /Users/anush/github/claude-rocm-workspace/torch_baseline.py
```

Expected first line confirms the eGPU is seen, e.g. `torch 2.13.0a0+git592bfed hip=7.13.60980 device_count=1`. Sanity one-liner: `torch.ones(4, device='cuda') + 1 == [2,2,2,2]` runs on gfx1201.

**Other harnesses** (HIP-level, self-driving with the same bring-up/env):
- `run-multi-dispatch-test.sh [N]` — builds `multi_dispatch_test.cpp` with `hipcc --offload-arch=gfx1201`, launches a kernel N times (default 200) through the lite:: MES path. Used to confirm the queue path is healthy independent of torch.
- `run-scratch-test.sh` — builds `scratch_test.cpp` and exercises a register-spilling kernel over the lite:: path. Scratch (#15) is fixed + HW-validated (default-on, `bcea652b27`), and register-spilling scratch is part of the passing torch smoke suite (13/14); this harness is a HIP-level check of the scratch path independent of torch.

**Diagnostics:** set `TORCH_TRACE=1` (enables `AMD_LOG_LEVEL=4 ROCR_MACOS_TRACE_AQL=1 ROCR_MACOS_TRACE_DIRECT_QUEUE=1 HIP_LAUNCH_BLOCKING=1`) or `TORCH_BLOCKING=1` for `run-torch-egpu.sh`. Note `HIP_LAUNCH_BLOCKING` is **not** a reliable workaround for the intermittent wedge.

---

## Build & run on Linux (x86, gfx1201, `lite::` backend on shark-a)

> **Where this runs.** This path runs on **shark-a** (`ssh nod@shark-a`, in `~/.ssh/config`), an x86 box with the **same gfx1201** GPU as the macOS eGPU (`1002:7551` rev c0 @ `c3:00.0`, audio fn at `c3:00.1`), IOMMU on, KVM. `nod` has passwordless `sudo`. Stock `amdgpu` is **blacklisted from auto-load** (so the GPU is free at boot), but can still be loaded explicitly for a known-good ROCm reference.
>
> **Source-of-truth caveat (read first).** The Linux `lite::` recipe code (`LITE_MES_RECIPE`, the `amdgpu_lite` Python backend, the recipe additions to the shared `windows/` backend, and the `amdgpu_lite.ko` kernel module) lives **only on shark-a** on branch `users/powderluv/macos-os-darwin`, and much of milestone 4/5 is **staged/uncommitted** there (committed pieces: `916f4935b` autoload, `9ca118b3f`/`9ca118b3f` MES-start + S2A, `c7cc6565c` multi-dispatch, `ef9547077` direct-queue pivot, `5b7bb740a` kernel doorbell, `26aec9e2d` selfring, `c15cc7832` S2A entries). The local Mac checkout (`/Users/anush/github/TheRock`, branch `users/powderluv/egpu-build`) does **not** contain `amdgpu_lite/` (Python backend or `.ko`); its `userspace_driver/kernel_driver/` is the **Windows WDDM KMD** (`amdgpu_mcdm.inf/.sln/.vcxproj`), not the Linux module. So the file paths/line numbers below are as cited on shark-a and may have moved — verify on the box before relying on them.

### 1. The `amdgpu_lite` kernel module (NOT auto-loaded after reboot)

The Linux `lite::` userspace driver talks to a minimal kernel shim (`/dev/amdgpu_lite0`) via `AMDGPU_LITE_IOC_*` ioctls + `mmap` of the PCI BARs. It is built on shark-a at:

```
~/github/TheRock/userspace_driver/amdgpu_lite/      # source + Makefile -> amdgpu_lite.ko
```

Key facts:
- The module is **not** auto-loaded at boot and stock `amdgpu` is **blacklisted from auto-load**, so after a reboot the GPU is unbound and you must `insmod` the module by hand.
- **vermagic must match the running kernel** or `insmod` is rejected. The host kernel has moved across reboots (seen at `6.17.0-29`, `-35`); rebuild the `.ko` if it does not match `uname -r`.
- A **clean boot is required** for the module to bind cleanly. After stock `amdgpu` has touched and unbound the GPU, the device is left dirty (empty driver dir, no probe) — recover with a BMC reset (see below) rather than a soft reboot.

```bash
# On shark-a, after a clean reboot (stock amdgpu is blacklisted, GPU is free):
ssh nod@shark-a
uname -r                                  # note the kernel; .ko vermagic must match

# (re)build only if vermagic mismatches the running kernel:
make -C ~/github/TheRock/userspace_driver/amdgpu_lite

sudo insmod ~/github/TheRock/userspace_driver/amdgpu_lite/amdgpu_lite.ko
ls -l /dev/amdgpu_lite0                    # device node should now exist
dmesg | tail -20                           # expect "Minimal AMD GPU access..." + BAR map lines
```

The module classifies the doorbell BAR (middle BAR by size) and `pci_iomap`s the first 4 KB of it; it `pci_enable_device` + `pci_set_master` and maps BARs `pgprot_noncached` (uncached, equivalent to the macOS DEXT's cache-inhibit mapping). A kernel-side doorbell-ring ioctl (`AMDGPU_LITE_IOC_RING_DOORBELL`, `_IOW 'L' 0x50`) was added (commit `5b7bb740a`) for diagnostics.

### 2. Firmware staging (one-time per fresh OS / firmware bump)

gfx1201 firmware ships **`.zst`-compressed** on Linux. The bring-up reads raw `*.bin`, so decompress the needed blobs into `/lib/firmware/amdgpu/`:

```bash
# Decompress the PSP / GC / SDMA firmware the recipe needs (gfx1201 = GC 12.0.1):
sudo sh -c 'for f in /lib/firmware/amdgpu/{psp_14_0_3_*,gc_12_0_1_*,sdma_7_0_*}.bin.zst; do \
  zstd -d -f "$f" -o "${f%.zst}"; done'
ls /lib/firmware/amdgpu/gc_12_0_1_*.bin    # confirm the gc_*_uni_mes.bin etc. exist
```

(The recipe reads firmware with `--fw-dir /lib/firmware/amdgpu`.)

### 3. Drive the `lite::` bring-up to BOOTLOAD_COMPLETE + MES start

The Python `lite::` backend lives under:

```
~/github/TheRock/userspace_driver/python/amd_gpu_driver/backends/amdgpu_lite/    # Linux backend (bringup.py)
~/github/TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/        # SHARED psp_init.py / ring_init.py / smu_init.py imported by Linux
```

The entire flow is gated behind **`LITE_MES_RECIPE=1`** (Approach B: fix the shared `windows/` backend in place, faithfully transcribing the macOS `backends/macos/gfx_bringup.py::gfx_bring_up` order). The recipe entry points (on shark-a's branch):
- `backends/windows/psp_init.py`: `load_all_firmware_recipe()` + `_toc_from_sos()` (+ `_recipe_extract_*`), dispatched from `load_all_firmware` when `LITE_MES_RECIPE=1`.
- `backends/amdgpu_lite/bringup.py`: `_recipe_bringup()` + `_poll_bootload_complete()`; `_recipe_mes_start()` for milestone 4.
- `backends/windows/ring_init.py`: `init_gfx_for_compute`, `init_compute_queue`, `init_mes_for_compute`.

The 4 fixes vs the legacy windows path that get bootload to complete: (1) TOC parsed from the **SOS container** (`PSP_TOC`=4, 2304 B), not `gc_*_toc.bin`; (2) `config.use_cmd_buffer=True` (1024-byte cmd-buffer LOAD_IP_FW ABI); (3) RS64 ucode offset read from the **v2 gfx-header field at +40**, not the common-header `ucode_array_offset_bytes` at +24; (4) **RLC_G loaded last**, then AUTOLOAD_RLC, then the SMU mailbox (`SetDriverDramAddrHigh=0x0E`/`Low=0x0F` + `EnableAllSmuFeatures(0)` non-fatal), then poll bootload bit31; no DisallowGfxOff.

```bash
ssh nod@shark-a
cd ~/github/TheRock/userspace_driver/python

# (a) bring up to BOOTLOAD_COMPLETE only (stop early to inspect):
sudo env LITE_MES_RECIPE=1 LITE_STOP_AFTER=bootload LITE_PSP_VERBOSE=1 \
  python3 -u -m amd_gpu_driver.backends.amdgpu_lite.bringup \
  --device 0 --fw-dir /lib/firmware/amdgpu
# Expect post-conditions matching macOS:
#   RLC_RLCS_BOOTLOAD_STATUS = 0x8000003f   (bit31 set = BOOTLOAD_COMPLETE)
#   GFX_IMU_GFX_RESET_CTRL   = 0x7f
#   GFX_IMU_CORE_CTRL        = 0x8
#   RLC_CNTL                 = 0x1

# (b) MES-scheduled compute path (no LITE_DIRECT_QUEUE) -- milestone 4:
sudo env LITE_MES_RECIPE=1 LITE_PSP_VERBOSE=1 \
  python3 -u -m amd_gpu_driver.backends.amdgpu_lite.bringup --device 0 --fw-dir /lib/firmware/amdgpu
# Expect: "MES: KIQ and scheduler rings initialized" + "PASS: MILESTONE 4 -- MES-scheduled NOP+fence completed"

# (c) DIRECT MEC HQD path (proven path for a REAL compute wave):
sudo env LITE_MES_RECIPE=1 LITE_DIRECT_QUEUE=1 \
  python3 -u -m amd_gpu_driver.backends.amdgpu_lite.bringup --device 0 --fw-dir /lib/firmware/amdgpu
```

`LITE_STOP_AFTER` accepts `{toc,autoload,smu,bootload,mes,queue}`. Always use `python3 -u` (stdout is block-buffered through a pipe; buffered output is lost when `timeout` kills a hung run).

**Reboot-per-shot discipline (important).** The PSP **wedges after ~1 full recipe run** (`"PSP TOS not ready (C2PMSG_64 timeout)"` in `create_psp_ring` on the next run), so it is effectively **one clean run per reboot** — make each run count.

```bash
# Clean-shot loop:
ssh nod@shark-a sudo reboot                         # amdgpu blacklisted -> GPU free on reboot
# wait for it to come back, then:
ssh nod@shark-a sudo insmod ~/github/TheRock/userspace_driver/amdgpu_lite/amdgpu_lite.ko
# ... run one bring-up shot ...

# If the card hard-wedges (PSP timeout that survives a soft reboot), power-cycle via BMC:
#   BMC <BMC-IP>, ssh admin / password '<BMC-PASSWORD>'  (single-quote the $)
#   -> reset /system   (or 'reset /system' equivalent), then insmod again.
```

### 4. Stock-`amdgpu` + ROCm 7.2.0 known-good reference, and how to restore `lite::`

To get a **working reference** (driver register dumps, sustained dispatch, etc.) on the same card: the `amdgpu` blacklist only suppresses **auto-load**, so an explicit `modprobe amdgpu` binds the free GPU.

```bash
ssh nod@shark-a

# Switch FROM lite:: TO stock amdgpu (needs a reboot first to release the GPU cleanly):
sudo rmmod amdgpu_lite        # if loaded
sudo reboot                   # clean state; amdgpu won't auto-load
# after reboot:
sudo modprobe amdgpu          # binds the free gfx1201; rings come up = doorbells work
dmesg | grep -i amdgpu | tail # confirm gfx1201 init, rings gfx_0.0.0 / comp_1.x

# Use ROCm 7.2.0 at /opt/rocm with stock amdgpu for a known-good comparison:
/opt/rocm/bin/rocminfo | grep -i gfx1201

# Read live registers for diffing against the lite:: recipe (under stock amdgpu):
#   sudo dd if=/sys/kernel/debug/dri/1/amdgpu_regs bs=4 skip=<absDword> count=1 | od -An -tx4
#   (NBIO BASE_IDX=2 = 0xD20; GC BASE_IDX=0 = 0x1260, BASE_IDX=1 = 0xA000; abs dword = base+off)

# Restore lite:: (amdgpu won't auto-reload after a reboot):
sudo reboot
sudo insmod ~/github/TheRock/userspace_driver/amdgpu_lite/amdgpu_lite.ko
ls -l /dev/amdgpu_lite0
```

Note: do **not** rely on `rmmod amdgpu; insmod amdgpu_lite.ko` without a reboot — unbinding stock `amdgpu` leaves the GPU dirty and `amdgpu_lite` will not probe. Reboot, then `insmod`.

### 5. git-lfs requirement for the `rocm-systems` submodule

The `rocm-systems` submodule (`url = https://github.com/ROCm/rocm-systems.git`, confirmed in `.gitmodules`) **requires git-lfs** or its init fails with `git-lfs: command not found` / `fatal: the remote end hung up unexpectedly`.

```bash
# One-time on any fresh clone host:
sudo apt-get install -y git-lfs    # (brew install git-lfs on macOS)
git lfs install

cd ~/github/TheRock
# If a prior failed init left a partial dir, remove it first or the re-init is a no-op:
rm -rf rocm-systems
git submodule update --init rocm-systems
# For the lite:: changes, rocm-systems must be on ROCm/rocm-systems
# users/powderluv/macos-os-darwin (the shared lite:: AQL/direct-queue backend).
```

### 6. Verification

| Check | Expected |
|---|---|
| Kernel module bound | `ls /dev/amdgpu_lite0` exists; `dmesg` shows BAR map lines |
| `.ko` vermagic | `modinfo amdgpu_lite.ko \| grep vermagic` matches `uname -r` |
| BOOTLOAD_COMPLETE | `RLC_RLCS_BOOTLOAD_STATUS=0x8000003f`, `GFX_IMU_GFX_RESET_CTRL=0x7f`, `GFX_IMU_CORE_CTRL=0x8`, `RLC_CNTL=0x1` |
| MES engine running | `CP_MES_CNTL=0x0C000000`, `CP_MEC_RS64_CNTL=0x3C000000`, `CP_MES_HEADER_DUMP` ticks |
| MES KIQ serviced (after PSP CP_MES fix) | MES pipe-1 `CP_MES_INSTR_PNTR=0x6d04`, `CP_MES_GP3_LO=0x01026081`; "PASS: MILESTONE 4 -- MES-scheduled NOP+fence completed" |
| Real compute wave (direct path) | `LITE_MES_RECIPE=1 LITE_DIRECT_QUEUE=1` -> fill_kernel: "All 256 elements = 0xDEADBEEF", no VM fault |

### Honest status (where the Linux `lite::` bring-up currently reaches)

- **BOOTLOAD_COMPLETE: working** on gfx1201 via `LITE_MES_RECIPE=1` (resolved the original LOAD_TOC `0x11` -> AUTOLOAD timeout -> bootload=0 blocker).
- **MES engine start (milestone 4a): working** — MES boots and loops.
- **MES KIQ / `SET_HW_RESOURCES` (#17): RESOLVED.** Root cause was loading MES via the rejected `RS64_MES(76/77)` (PSP returns `0xFFFF0006`); fix loads `gc_*_uni_mes.bin` as `CP_MES(33)/MES_STACK(34)` + `CP_MES_KIQ(81)/MES_KIQ_STACK(82)` via PSP and lets `_enable_mes_from_ucode` start the PSP-loaded image. After the fix MES-scheduled NOP+fence passes and multi-dispatch sustains **1025 with no ceiling** (so the ~14-dispatch ceiling is macOS-specific, not inherent). **This 2-file fix (`psp_init.py` + `ring_init.py`) is git-add STAGED on shark-a, NOT committed** (user reviewing).
- **Real compute kernel: working on the DIRECT MEC HQD path** (`LITE_DIRECT_QUEUE=1`): a wave32 `fill_kernel` dispatched + completed + verified end-to-end (first real compute wave on Linux `lite::`). The **identical** kernel on the **MES-mapped** queue still hangs (wave-launch / MQD compute-state bug isolated to the MES-mapped queue) — **deferred**; the direct path sidesteps it.
- **Net:** `bootload -> MEC enable -> direct MEC HQD -> real verified compute wave + sustained 1025 dispatches` works. Several recipe edits (milestone 4/5 MES-start, multi-dispatch loop, the CP_MES PSP fix, NOP diagnostics) remain **uncommitted/staged on shark-a**.

---

## Build & run on Windows (gfx1201, lite:: backend, `win11-gpu` VM)

> **Status (2026-06-18): a userspace compute bring-up + shader dispatch works on gfx1201.** The `amd_gpu_driver` Windows backend cold-boots the GPU entirely from userspace over `D3DKMTEscape` — PSP firmware load → RLC/IMU autoload → MEC enable → a 4-level GPUVM page table → a compute `DISPATCH_DIRECT` — and an `s_endpgm` wave executes with its EOP fence signalling. Verified end-to-end from a cold boot in the `win11-gpu` VM: NOP+fence, a PM4 `WRITE_DATA` memory test, and the shader dispatch all PASS. **It runs against the production `amdgpu_wddm` KMD that owns the card (the stock ROCm Windows driver) — no custom kernel driver is installed or required** (`sc query amdgpu_mcdm` → not installed). Committed `f2b12969c` (branch `users/powderluv/macos-os-darwin`, pushed to `powderluv/TheRock`).
>
> This is the same `lite::`-style direct-queue approach used on macOS/Linux, ported to Windows' `D3DKMTEscape` transport. It is **separate** from the `amdgpu_mcdm` custom MCDM kernel-driver effort (§6), which passed dxgkrnl's adapter-start contract but is still blocked at `dxgmms2` VidMm init — the working userspace path does not depend on it. Remaining for Track C: real compiled kernels + kernargs + scratch on top of this, then PyTorch units (#20).
>
> **Update (2026-07-06 — the C++ ROCr path reaches PyTorch):** the Python bring-up above proved the transport; Track C then moved to the **shared C++ ROCr `lite::` backend** (a `WindowsLiteDriver` like the macOS/Linux drivers, over `wddm_lite`/`D3DKMTEscape`), statically linked into **`amdhip64_7.dll`**, so torch talks to real ROCm. Milestones since:
> - **A torch one-liner runs on gfx1201** through ROCr → `WindowsLiteDriver` → `wddm_lite` (foundation `be9b89171`: the shared lite:: direct-queue dispatches NOP+fence over `wddm_lite`).
> - **MES-backed compute queue + scheduler ring — SOLVED and HW-validated 2026-06-23** (blocked on all three OSes until then). The gfx1201 MES schedules + maps + dispatches a compute queue end-to-end on Windows. Three fixes: (1) start the MES engine on the ROCr **driver** path (`EnsureMesEngineStartedLocked` → `wddmStartMes`; previously only the standalone harness did, so the map returned `status=4096`); (2) skip the firmware HQD reclaim on the MES path (`MapLegacyQueueWithMes` reprograms the HQD anyway); (3) activate the scheduler-ring HQD **image-only** (no host activate) *before* `MAP_SCHEDULER`, mirroring the proven `direct_activate=False` recipe. The doorbell is dead under WDDM/passthrough, so the wptr is MMIO-poked on `CP_HQD_PQ_WPTR` (`ROCR_WINDOWS_MES_MMIO_WPTR=1`). The initial validation used a **fresh (power-cycled) GPU** because stale MES scheduler state masks the fixes; the per-process re-bring-up fix (#66, in the 2026-07-21 update below) later deactivates that scheduler state at process exit, so multiple torch processes run on one power-cycle.
> - **EOP `RELEASE_MEM` completion fence** (`ROCR_WINDOWS_AQL_EOP_FENCE`, mirroring the wddm_lite scratch recipe): non-scratch dispatches retire through it.
> - **Ring-wrap CP halt fixed:** `kDirectComputeRingSize` 0x1000 → 0x8000 (the 1024-dword ring wrapped at the first boundary-crossing dispatch and the MES CP halted).
> - Committed `b30a8d5c53` on `powderluv/rocm-systems` (branch `users/powderluv/macos-os-darwin`). All changes are **Windows-only / env-gated (default-off)**, so macOS and Linux are unaffected.
>
> **Update (2026-07-21 — scratch resolved; torch smoke 9/9 single- and multi-process):**
> - **~14-dispatch ceiling (#62) FIXED**, which also resolved the earlier "#57 scratch stall": it was a HQD `QUEUE_SIZE` (9 = 1024 dw) vs 8192-dw ring mismatch; deriving `QUEUE_SIZE` from the ring retired 150/150 register-spilling dispatches, so the register-spilling GEMM now computes.
> - **teardown hang (#63) FIXED** — at process exit torch's fatbin dtor spun forever in `SyncAllStreams→HostQueue::finish` because Windows kills the completion threads before onexit; skip the drain when `RtlDllShutdownInProgress` (`751e1ba8b5`, default-on).
> - **torch smoke 9/9 single-process** — matmul/GEMM, batch-mm, `@`, elementwise, transpose, dot, matrix-vector, matmul-variant. **#64 (the earlier "5 op failures") was a per-test-*isolate* harness artifact, not op bugs** — the 5 ops produce correct values in one process; the failures only appeared in per-test isolate mode, where each test is a fresh subprocess.
> - **Per-process re-bring-up (#66) FIXED + default-on.** The cross-process wedge was the MES **scheduler-ring HQD** (me=3/pipe=0) left active by the prior process: `hipMalloc` still succeeds, but the next `hsa_queue_create` fails because the fresh scheduler `SET_HW_RESOURCES` is never serviced (`status=4096`). Fix: a `std::atexit` hook deactivates this process's scheduler HQD while the MES is still healthy (its dequeue-drain completes at once → `active=0`), so the next process starts clean — synchronous MMIO on the calling thread, no dead-thread dependency (unlike #63). `a3adf7c6a1` (opt-in) then `a6de5ec13c` (**default-on on Windows** under `#ifdef _WIN32`; opt out `ROCR_WINDOWS_MES_TEARDOWN_AT_EXIT=0`; macOS/Linux stay opt-in). HW-validated with no env set: **isolate smoke 9/9 (was 4/9), 6/6 sequential processes, single-process 9/9 with a clean exit** — 16 processes on one power-cycle; the `env=0` baseline wedges at #2/#3. So Windows torch runs single-process **and** per-test isolate / multi-process **by default**, and the earlier "MES bring-up is one-shot per power cycle" no longer holds.
> - **Only remaining Windows blocker:** the separate `amdgpu_mcdm` MCDM-KMD track (`dxgmms2` VidMm init, §6), independent of the working `wddm_lite` path.

### 1. Host + VM setup (x86 box with a passthrough gfx1201)

GPU validation cannot run on the Mac (eGPU PCIe/Thunderbolt passthrough into a VM is architecturally impossible on Apple Silicon — no IOMMU/DMA passthrough in Virtualization.framework). Linux/Windows VM validation runs on an x86 box (`shark-a` in these notes) with the **same gfx1201 silicon** as the Mac eGPU.

- **Host:** x86 Linux, IOMMU on, KVM + libvirt. The gfx1201 is PCI `1002:7551` (+ its HDMI-audio function); the host's own display is a separate card, so the AMD card is free for passthrough. The user has passwordless `sudo` and is in `libvirt`/`kvm`.
- **VM (build it once):** a standard Windows-11 libvirt guest with the gfx1201 **and its HDMI-audio function** attached as `<hostdev>` (PCI passthrough), host IOMMU enabled, and **the stock AMD ROCm Windows driver installed in the guest** — that driver registers as `amdgpu_wddm`, owns the card, and is what services the `D3DKMTEscape` calls the userspace bring-up makes. You do **not** install the custom `amdgpu_mcdm` MCDM driver for this path (that's the separate §6 track). Creating the passthrough domain (hostdev XML, the virtiofs share below) is standard libvirt GPU-passthrough and not re-documented here.
- **GPU lease:** a Linux VM (Track B) and the Windows VM (Track C) share one physical GPU; only one VM can hold the `hostdev` at a time. Stop one before starting the other.
- **The VFIO-without-reset trick (`start-gpu-vm.sh`, included):** the gfx1201 compute bring-up depends on the VBIOS POST state (PSP SOS, SMU, GC power). A normal VFIO FLR on assignment wipes it. So the flow is: **cold power-cycle the host** (fresh POST) → run `start-gpu-vm.sh`, which binds `vfio-pci`, **clears the device `reset_method`** (skips the FLR so the POST state survives), and starts the VM. See the script for the redacted env vars (`GPU` BDF, `VM`, `VM_IP`, `VM_SSH_PASS`).
- **Per-shot freshness:** the PSP accepts the firmware-load recipe once per POST, so each bring-up shot starts from a cold host power-cycle, and a guest reboot alone does not re-arm the PSP. **No BMC required** — physically power the host off and cold-boot before each shot. The out-of-band BMC/PDU (IP + credentials redacted to a gitignored env file) is only there to automate that power-cycle in a loop.
- **Guest share:** the `userspace_driver/` tree and the firmware dir are exposed to the guest over a virtiofs share (here drive `Z:`), so guest-side edits aren't needed — push on the host, run in the guest. **Without virtiofs**, just copy `userspace_driver\python` + the firmware dir into the guest and point `run_bringup.bat`'s `DRIVER_ROOT`/`FW_DIR` at the local paths.

### 2. Reproduce the compute bring-up + shader dispatch

Prerequisites in §4 (Python 3.12 + the firmware set; **no kernel driver**). Get the code — the Windows commit is **public on the `powderluv/TheRock` fork** (unlike the Linux pieces flagged in §Linux 0, which live only on the validation host):
```
git clone https://github.com/powderluv/TheRock && cd TheRock && git checkout f2b12969c
```
Then, in the guest with `userspace_driver/` + a gfx1201 firmware dir visible (here over the `Z:` virtiofs share):

1. **Stage the firmware** into `--fw-dir`. The PSP loads the gfx1201 (**GC 12.0.1 / PSP 14.0 / Navi48**) blobs: `psp_14_0_3_*`, `gc_12_0_1_*` (PFP/ME/MEC/RLC + `_uni_mes`), `sdma_7_0_*`, and the SMU `.bin`. Source them from the `amdgpu/` tree of [linux-firmware](https://gitlab.com/kernel-firmware/linux-firmware) (or `/lib/firmware/amdgpu` on a recent Linux box) — the same blobs as the Linux §2 staging; decompress the `.zst` and drop the resulting plain `.bin` files into the dir (Windows loads `.bin`, not `.zst`).
2. **Run the launcher (`run_bringup.bat`, included).** It sets `LITE_MES_RECIPE=1` (selects the gfx1201 RLC/IMU autoload firmware-load recipe — TOC-from-SOS + cmd-buffer `LOAD_IP_FW` + RS64 + RLC_G-last) and runs the backend as a script:
   ```bat
   set LITE_MES_RECIPE=1
   set PYTHONPATH=Z:\userspace_driver\python
   "C:\Program Files\Python312\python.exe" -u ^
       amd_gpu_driver\backends\windows\compute_dispatch.py --device 0 --fw-dir Z:\winfw
   ```
3. **`full_gpu_bringup` then runs the cold-boot sequence:** open device (`D3DKMTEscape`) → IP discovery → NBIO → GMC → PSP + firmware (autoload) → SMU `EnableAllSmuFeatures` + poll `RLC_RLCS_BOOTLOAD_STATUS` for bit31 → MEC enable + doorbell range → compute queue (direct MEC HQD, VMID 0) → self-tests.
4. **The shader self-test** stages an `s_endpgm` wave in VRAM, builds a **4-level GFXHUB page table** (PDB2→PDB1→PDB0→PTB; entries are **0-based VRAM offsets**, matching the gfx12 walker — not MC addresses), enables `GCVM_CONTEXT0` (depth-3, fault-enabled) **after** the autoload, then issues a wave32 `DISPATCH_DIRECT`. GPUVM is enabled only on the shader path; the NOP/WRITE_DATA control tests stay on the physical FB-aperture path under VMID 0.

**Expected output (cold boot):**
```
BOOTLOAD_STATUS=0x8000003f   ... PASS: BOOTLOAD_COMPLETE - RLC/IMU autoload succeeded
GFX: MEC enabled (CP_MEC_RS64_CNTL=0x3C000000)
[8/8] Running NOP + fence self-test...   PASS: NOP + RELEASE_MEM fence completed
--- WRITE_DATA memory test ---           PASS: 16 DWORDs written and verified
--- Noop shader dispatch test ---        PASS: Noop shader dispatched and completed
BRINGUP_EXIT=0
```
Code: committed `f2b12969c` (7 files: `compute_dispatch.py` bring-up stages + shader test, `gmc_init.py` `build_compute_gpuvm`, `ring_init.py` doorbell/wptr, `registers.py` gfx12 `RSRC3`, plus `device.py`/`discovery.py`/`driver_interface.py` transport + VRAM allocator).

### 3. The Windows userspace backend (what it does now)

Path: `userspace_driver/python/amd_gpu_driver/backends/windows/` (branch `users/powderluv/macos-os-darwin`).

It drives the GPU from userspace over `D3DKMTEscape`, serviced by whichever KMD owns the adapter — in the validated setup that is the **production `amdgpu_wddm`** driver:
`Python → gdi32.D3DKMTEscape → dxgkrnl.sys → DxgkDdiEscape → amdgpu_wddm.sys`. The escapes it uses (`GET_INFO`, `MAP_BAR`/`MAP_VRAM`, `READ_REG32`/`WRITE_REG32`, `ALLOC_DMA`) are answered by that driver; the gfx1201 register-programming is identical to the macOS/Linux `lite::` direct-queue path, which is why the init modules are shared.

Module map:
- `__init__.py` — guards the import: `device` + `compute_dispatch` only import on `sys.platform == "win32"`. The register-programming init modules (`nbio_init`, `gmc_init`, `psp_init`, `ih_init`, `ring_init`, `ip_discovery`) are **OS-agnostic and imported directly by the Linux and macOS backends** — that's why this dir is called "shared" even though it's named `windows`.
- `compute_dispatch.py` — the bring-up orchestrator. **`full_gpu_bringup(...)`** runs the cold-boot sequence (open device → IP discovery → NBIO → GMC → PSP+firmware autoload → SMU `EnableAllSmuFeatures` + `BOOTLOAD_STATUS` bit31 poll → MEC enable + doorbell range → compute ring → NOP+fence self-test) and returns a `GPUContext`. Plus `test_write_data()`, `test_noop_dispatch()` (the `s_endpgm` GPUVM shader path), `dispatch_elf_kernel()`, `shutdown()`. The `__main__` block takes `--device`/`--fw-dir`/`--kernel`.
- `device.py` — `WindowsDevice(DeviceBackend)`. `read_reg32/write_reg32` via escape (SMN indirect through NBIO index/data `0x60`/`0x64`); **`alloc_memory(... VRAM)` + `read_vram()` are implemented** (over `MAP_VRAM`). The generic `DeviceBackend` queue/submit/signal ABC methods (`create_compute_queue`, `submit_packets`, `create_signal`/`wait_signal`, SDMA) **still raise `NotImplementedError`** — the bring-up does **not** use them; it drives `ring_init`'s compute-queue / submit / fence helpers directly. Wiring those ABC methods is the follow-on for the normal HSA-agent / `lite::`-C++ flow. `gfx_target_version` maps `0x7551`/`0x7550` → `120001`.
- `discovery.py` — `discover_devices()` / `open_device()` via `D3DKMTEnumAdapters3` + `ESCAPE_GET_INFO`, matching AMD vendor `0x1002` and device IDs `0x7551`/`0x7550`.
- `driver_interface.py` — ctypes bindings for `gdi32` D3DKMT structs (`D3DKMT_ESCAPE`, enum/open/create-device) + the `AMDGPU_ESCAPE_*` opcode payloads. Imports `raise ImportError` unless `sys.platform == "win32"`.
- `ip_discovery.py` — `parse_ip_discovery()` + `read_discovery_table_via_mmio(read_fn, vram_size)`; reads the discovery table from the **top of VRAM** (`base_addr = vram_size - read_size`). This top-of-VRAM read is the region out of reach on the Mac's 256 MB BAR — on a VM with full ReBAR it is reachable.
- `psp_init.py` — PSP v14.0 (Navi48). `init_psp(...)`, `load_all_firmware(...)`, and the **`LITE_MES_RECIPE=1` autoload recipe** (`load_all_firmware_recipe()` / `_toc_from_sos()`): TOC parsed from the SOS container, cmd-buffer `LOAD_IP_FW`, RS64 ucode, RLC_G loaded last. Mailbox uses `C2PMSG_35` (bootloader), `C2PMSG_81` (SOS sign-of-life).
- `nbio_init.py`, `gmc_init.py` (incl. `build_compute_gpuvm`), `ih_init.py`, `ring_init.py`, `registers.py` — the OS-agnostic IP-init + dispatch building blocks consumed by `full_gpu_bringup` and shared with the macOS/Linux backends.

**Env knobs (read by the recipe path):** `LITE_MES_RECIPE=1` selects the gfx1201 autoload firmware-load recipe (without it, the legacy load path uses the wrong TOC and the autoload never completes); `LITE_PSP_VERBOSE=1` logs each PSP mailbox step; assorted `AMDGPU_LITE_PSP_*` flags toggle cmd-buffer / TMR / autoload variants. The same recipe is what the Linux backend uses on shark-a.

### 4. Build prerequisites (the working userspace path)

**No kernel driver needed for this path.** It runs against the production `amdgpu_wddm` that already owns the card; you do not build or install anything in kernel mode. (The separate custom `amdgpu_mcdm` KMD and its WDK build live in §6.)

**Python userspace** — `userspace_driver/python` (package `amd_gpu_driver`, `pyproject.toml`), Python 3.12 in the guest. The bring-up path imports only the standard library (`ctypes`/`struct`/`pathlib`), so running straight from the checkout works with no install: `run_bringup.bat` sets `PYTHONPATH` to `userspace_driver\python` and runs the module. If your tree pulls extra deps, `pip install -e userspace_driver/` first (editable, so the `windows` backend resolves).
- **Packaging gotcha:** `pyproject.toml` `tool.setuptools.packages` does **not** list `amd_gpu_driver.backends.windows` (or `.macos`) — so the backend works in an *editable* install but would be **missing from a built wheel**. Fix before any wheel distribution (or just run from the checkout). (Source: `memory/macos-egpu-rocm-effort.md`.)
- **Source ASCII gotcha:** keep these backend files pure ASCII — non-ASCII in a comment/docstring (em-dash, arrow) parses on some hosts but the guest's Python 3.12 rejected it (`SyntaxError: invalid character`).

**Firmware:** `--fw-dir` expects the gfx1201 (GC 12.0.1) firmware `.bin` set the PSP loads (SOS/RLC/MEC/SDMA/MES/SMU), from the AMD/ROCm firmware package. On Linux these ship `.zst` and are decompressed (§Linux 2); on Windows place the plain `.bin` files in the dir.

**ROCm / PyTorch units (the Track C goal, #20):** real compiled kernels (kernargs + scratch) plus a lite-enabled ROCr / PyTorch — now realized on the C++ ROCr `WindowsLiteDriver` path (linked into `amdhip64_7.dll`, § status update above), which runs the torch smoke 9/9 on gfx1201 single- and multi-process; the Python userspace bring-up here proved the transport.

### 5. Status: implemented vs planned (userspace path)

| Item | Status |
|---|---|
| Host VFIO-without-reset + VM start (`start-gpu-vm.sh`) | **Working** — preserves VBIOS POST state into the guest |
| Device open / IP discovery / MMIO reg r/w via `D3DKMTEscape` | **Working** against production `amdgpu_wddm` (no custom KMD) |
| VRAM allocation (`alloc_memory`/`read_vram` over `MAP_VRAM`) | **Working** |
| PSP firmware load → RLC/IMU autoload (`LITE_MES_RECIPE=1`) → `BOOTLOAD_STATUS=0x8000003f` | **Working** |
| SMU `EnableAllSmuFeatures` + MEC enable + doorbell range | **Working** |
| NOP+fence + PM4 `WRITE_DATA` self-tests | **PASS** |
| 4-level GFXHUB GPUVM page table + compute `s_endpgm` shader dispatch | **PASS** (committed `f2b12969c`) |
| Generic `WindowsDevice` queue/submit/signal ABC methods | **Stubbed** — the bring-up drives `ring_init` directly instead |
| Real compiled kernels + kernargs (non-scratch) | **Working** — moved to the C++ ROCr `WindowsLiteDriver` path (§ status update above); torch smoke 9/9 runs via `amdhip64_7.dll` |
| MES-backed compute queue + scheduler ring | **Working** — HW-validated 2026-06-23; retires via EOP `RELEASE_MEM` fence (committed `b30a8d5c53`) |
| Register-spilling kernels + scratch (torch matmul) | **Working** (#62) — deriving HQD `QUEUE_SIZE` from the ring retired 150/150 register-spilling dispatches, so the GEMM computes (the old "#57 scratch stall" is resolved) |
| PyTorch units via the `lite::` path on Windows | **torch smoke 9/9** (#20) — single-process, and per-test isolate / multi-process by default after #66 |
| (Separate track) `amdgpu_mcdm` custom MCDM KMD | adapter-start contract passed; blocked at `dxgmms2` VidMm init (§6) |

**Bottom line:** two Windows results stand. (1) The Python `amd_gpu_driver` userspace path dispatches a compute shader on gfx1201 from cold boot against stock `amdgpu_wddm` (no custom KMD) — reproducible via `start-gpu-vm.sh` (host) + `run_bringup.bat` (guest). (2) The C++ ROCr `WindowsLiteDriver` (in `amdhip64_7.dll`) runs the **torch smoke 9/9** on gfx1201 — single-process and per-test isolate / multi-process by default (#66) — with a **MES-backed compute queue solved and validated on hardware** (committed `b30a8d5c53`) and the gfx12 register-spill **scratch** wave retirement resolved (#62). The only remaining Windows wall is the separate `amdgpu_mcdm` MCDM-KMD track (`dxgmms2` VidMm init, §6).

### 6. (Separate track) the `amdgpu_mcdm` custom MCDM kernel driver

This is an **independent** effort from the working userspace path above: a from-scratch **compute-only MCDM kernel miniport** (`amdgpu_mcdm.sys`) for gfx1201, so the GPU could be driven without the production display driver. The working bring-up (§2–§5) does **not** use it. It reached dxgkrnl's adapter-start contract but is currently blocked one layer deeper, at `dxgmms2` VidMm init.

**Build/sign/install (WDK, in the guest):** source at `userspace_driver/kernel_driver/` (`amdgpu_mcdm.vcxproj/.sln/.inf`, `ddi_*.c`, `driver_entry.c`); no prebuilt `.sys` checked in. Build with the EWDK/WDK (`msbuild amdgpu_mcdm.sln /p:Configuration=Release /p:Platform=x64`, link with `/OPT:NOREF /OPT:NOICF` so `DriverEntry` + the DDI table aren't dead-stripped). Then `sign_and_install.ps1` (makecert → inf2cat → signtool → pnputil); needs testsigning on (`bcdedit /set testsigning on`). The INF must have a `CopyFiles` section or the `.sys` never lands in the DriverStore (Code 39).

The adapter-start contract fixes (committed `52d93387`, in `userspace_driver/kernel_driver/`):

1. **`DXGK_DRIVERCAPS.SchedulingCaps.MultiEngineAware = 1`** — required of any WDDMv2 driver; its absence is the silent `STATUS_INVALID_PARAMETER` at `StartAdapter_AddAdapterFailed`.
2. **Pin `DXGKDDI_INTERFACE_VERSION` to `WDDM2_6`** before the WDK headers — the 26100 headers default to `WDDM3_2`, so `DXGK_*` structs are larger / differently laid out than the 2.6-sized buffers dxgkrnl passes a 2.6 driver → `BUFFER_TOO_SMALL` and bad-offset reads. Also accept dxgkrnl's buffer size instead of demanding `sizeof()`.
3. **A consistent IoMmu memory model** — `DRIVERCAPS` (`VirtualAddressingSupported + IoMmuSupported`) and `PHYSICALADAPTERCAPS.Flags.IoMmuSupported` must agree.
4. **Answer the required caps queries**: `WDDMDEVICECAPS`, `PHYSICALADAPTERCAPS` (version-tolerant, one execution node), `GPUVERSION`/`ADAPTERPERFDATA` (zeroed-SUCCESS — returning `NotSupported` leaves dxgmms2 walking an uninitialized string).
5. **Register the required DDIs**: `SetStablePowerState`, `SetVirtualMachineData`, `BeginExclusiveAccess`, `EndExclusiveAccess` (absence → `REVISION_MISMATCH` / "Failed to create ADAPTER_RENDER").
6. **StartDevice**: clamp an implausible VRAM size (gfx12 `mmRCC_CONFIG_MEMSIZE` misreads) so VidMm gets a sane segment.
7. **Build/install prereqs**: `/OPT:NOREF /OPT:NOICF` (stop the linker dead-stripping `DriverEntry` + the DDI table into a ~16 KB stub that won't load); INF `CopyFiles` (else the `.sys` never lands in the DriverStore → Code 39).
8. **Diagnostic instrumentation** (WIP, strippable): registry breadcrumbs under `HKLM\SOFTWARE\AmdMcdmDiag` + `QueryAdapterInfo` type bitmasks.

**The diagnostic loop that found these (no live kernel debugger / 2nd VM needed):**
- **DxgKrnl ETW `id=494`** — capture the dxgkrnl provider to a file (`logman create trace dxgcap -p {802ec45a-1e99-4b83-9920-87c98277ba9d} 0xFFFFFFFFFFFFFFFF 0xff -o C:\dxg.etl -ets`), bind the device, stop, then read each event's raw `.Properties` (WPP format strings live in the payload). `id=494` prints human-readable contract complaints ("SchedulingCaps.MultiEngineAware is not set by WDDMv2 driver", "DxgkDdiX is required").
- **`Microsoft-Windows-DxgKrnl-Admin` `id=549`** — the per-bind `NTSTATUS` ("Adapter start failed … reason StartAdapter_AddAdapterFailed"); watching it advance (`BUFFER_TOO_SMALL → REVISION_MISMATCH → INVALID_PARAMETER → cleared`) confirms each fix.
- **Kernel-dump deep-kd** — set `CrashControl\CrashDumpEnabled=2`, reproduce, analyze `MEMORY.DMP` with the EWDK `kd.exe` (`!analyze -v`, `uf`, `du`). This root-caused the next blocker — `dxgmms2!VIDMM_GLOBAL::ReadPhysicalAdapterConfiguration` faulting on an uninitialized `UNICODE_STRING` returned by `DpiGetPnpRegistryKeyName`.

**Gotcha — don't crash-loop a WDDM driver on a no-reset VFIO GPU.** Iterating a *crashing* bind many times on the passthrough GPU (with `start-gpu-vm.sh` deliberately disabling PCIe reset) can wedge the GPU/VM baseline so *every* WDDM driver — including the stock one — fails. Recovery is a **host reboot / cold power-cycle** (the gfx1201 itself is fine after a clean POST — re-verified via the Linux bring-up), and if the Windows install is corrupted, revert the VM to a clean snapshot. Stop at the first VidMm bugcheck and change approach instead of cycling.

---

## Reference paths & docs

- **Running the ROCm macOS eGPU Port (gfx1201) — branches, build, bring-up, what changed** — `TheRock/docs/development/macos_egpu_port.md`
- **TheRock build system (super-project vs sub-projects; core/ holds ROCR-Runtime)** — `TheRock/docs/development/build_system.md`
- **ROCr pluggable driver interface — core::Driver + DriverType enum (MACOS_DEXT, LINUX_AMDGPU_LITE)** — `TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/inc/driver.h`
- **Shared lite:: direct-queue API + key types (DirectQueueState, DirectQueuePlatform, DirectQueueOptions)** — `TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/inc/amd_lite_direct_queue.h`
- **Shared lite:: direct-queue + MES implementation (Create/Submit/Destroy, MES vs direct branches)** — `TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/driver/lite/amd_lite_direct_queue.cpp`
- **macOS driver — MacOsDriver : core::Driver, private lite::DirectQueuePlatform** — `TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/driver/macos/amd_macos_driver.cpp`
- **macOS AQL queue — MacAqlQueue (AQL→PM4, kernarg staging, scratch), submits via lite::** — `TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/runtime/amd_macos_aql_queue.cpp`
- **Driver discovery seam — DiscoverDrivers() per-OS factory array** — `TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/runtime/amd_topology.cpp`
- **Linux amdgpu-lite driver + DRM transport (no libhsakmt)** — `TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/driver/lite/linux/amd_lite_linux_driver.cpp`
- **Userspace Python bring-up package (backends/{kfd,windows,macos}, commands/, ioctl/)** — `TheRock/userspace_driver/python/amd_gpu_driver/`
- **Userspace DeviceBackend ABC (shared backend interface)** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/base.py`
- **macOS DriverKit DEXT (ROCmGPUDriver / ai.rocm.gpu.driver)** — `TheRock/userspace_driver/macos_driver/`
- **ROCm public documentation** — `https://rocm.docs.amd.com/`
- **TheRock repository** — `https://github.com/ROCm/TheRock`
- **Reconciled torch eGPU run recipe + env** — `claude-rocm-workspace/run-torch-egpu.sh`
- **12-op non-scratch torch validation baseline** — `claude-rocm-workspace/torch_baseline.py`
- **HIP multi-dispatch harness (queue-health check)** — `claude-rocm-workspace/run-multi-dispatch-test.sh`
- **Scratch-op harness (HIP-level scratch-path check; #15 fixed, in the 13/14 smoke suite)** — `claude-rocm-workspace/run-scratch-test.sh`
- **eGPU power-cycle / re-enumerate (replug replacement)** — `claude-rocm-workspace/egpu-replug.sh`
- **eGPU bring-up wrapper (replug + phase-9 retry)** — `claude-rocm-workspace/egpu-bringup.sh`
- **eGPU soft drain** — `claude-rocm-workspace/egpu_drain.py`
- **Phase-9 GFX bring-up entrypoint** — `TheRock/userspace_driver/python/try_phase9_doorbell.py`
- **macOS ROCm SDK (dist) layout** — `TheRock/build-macos-egpu/dist/rocm`
- **ROCR-Runtime macOS build tree** — `TheRock/build-macos-egpu/core/ROCR-Runtime/build`
- **macOS ROCr backend source (amd_macos_aql_queue.cpp / amd_macos_driver.cpp)** — `TheRock/rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/runtime/amd_macos_aql_queue.cpp`
- **PyTorch macOS port branch (users/powderluv/macos-egpu)** — `TheRock/external-builds/pytorch/pytorch`
- **Full build recipe + status (memory)** — `memory: pytorch-macos-rocm-build.md`
- **TheRock CMake conventions (CLAUDE.md)** — `claude-rocm-workspace/CLAUDE.md`
- **linux-lite-autoload-working memory (the LITE_MES_RECIPE recipe, 4 fixes, BOOTLOAD_COMPLETE, #17 resolution, direct-MEC real wave)** — `memory: linux-lite-autoload-working.md`
- **shark-a-tri-os-validation memory (shark-a host setup, GPU 1002:7551, amdgpu_lite.ko build, BMC power-cycle, lite:: SET_HW_RESOURCES bases)** — `memory: shark-a-tri-os-validation.md`
- **rocm-systems-lite-backend-refactor memory (shared lite:: backend, Linux scaffold, MES-backed queues)** — `memory: rocm-systems-lite-backend-refactor.md`
- **rocm-systems-git-lfs memory (git-lfs required for rocm-systems submodule init)** — `memory: rocm-systems-git-lfs.md`
- **Shared windows/ backend (psp_init.py `load_all_firmware` + `load_all_firmware_recipe`, ring_init.py compute-queue/dispatch, gmc_init.py `build_compute_gpuvm`) — reused by Linux/macOS; recipe present on `users/powderluv/macos-os-darwin`** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/`
- **directory-map.md (shark-a / sharkmi300x remote setup, repo aliases)** — `claude-rocm-workspace/directory-map.md`
- **Windows MCDM backend package init (import guards; lists shared OS-agnostic init modules)** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/__init__.py`
- **WindowsDevice — D3DKMTEscape device; `alloc_memory`(VRAM)/`read_vram` implemented, generic queue/submit/signal ABC still stubbed** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/device.py`
- **Host VFIO-without-reset + VM start (redacted)** — `claude-rocm-workspace/gist-tri-os/start-gpu-vm.sh`
- **Windows guest compute bring-up launcher (`LITE_MES_RECIPE=1`)** — `claude-rocm-workspace/gist-tri-os/run_bringup.bat`
- **compute_dispatch.py — full_gpu_bringup() / run_demo() entry points + __main__ (--device/--fw-dir/--kernel)** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/compute_dispatch.py`
- **driver_interface.py — gdi32 D3DKMT ctypes bindings; ImportError unless win32** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/driver_interface.py`
- **discovery.py — D3DKMTEnumAdapters3 + ESCAPE_GET_INFO; matches 0x1002/0x7551/0x7550** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/discovery.py`
- **psp_init.py — PSP v14.0 init_psp/load_all_firmware (no LITE_MES_RECIPE on this branch)** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/psp_init.py`
- **ip_discovery.py — read_discovery_table_via_mmio reads top-of-VRAM (vram_size - read_size)** — `TheRock/userspace_driver/python/amd_gpu_driver/backends/windows/ip_discovery.py`
- **kernel_driver/ — amdgpu_mcdm.sys source (.vcxproj/.sln/.inf/ddi_*.c), no prebuilt binary** — `TheRock/userspace_driver/kernel_driver/`
- **sign_and_install.ps1 — WDK 10.0.26100.0 paths; makecert/inf2cat/signtool/pnputil install** — `TheRock/userspace_driver/kernel_driver/sign_and_install.ps1`
- **Memory: Tri-OS validation — win11-gpu VM, VFIO/BMC on shark-a, Windows in-scope follow-on** — `memory: shark-a-tri-os-validation.md`
- **Memory: pyproject only lists Linux packages — windows/macos backends missing from built wheel** — `memory: macos-egpu-rocm-effort.md`
- **directory-map.md — Windows repo aliases (therock=D:\R, build D:\R\therock-build, gfx1201)** — `claude-rocm-workspace/directory-map.md`

---

## Maintaining this gist

Update with the **positional** source-file form (`gh gist edit <ID> -f README.md` *alone*
is interactive/no-op — it does not push file content):
```
gh gist edit <ID> --filename README.md README.md      # repeat per changed file
```
Paths are from the reference machine — replace the checkout root with yours. The helper
scripts (`run-*.sh`, `torch_baseline.py`, `egpu-*.{sh,py}`, `start-gpu-vm.sh`,
`run_bringup.bat`) are included alongside this README. **Always re-run a secret scan
before pushing edits** — the iBoot PDU IP, the BMC IP/credentials, and the VM SSH
password are redacted/externalized to gitignored env files (`egpu-powercycle.py` reads
`$IBOOT_PASSWORD` or `egpu-iboot.env`; `start-gpu-vm.sh` reads `$VM_SSH_PASS`).
