# Plan: Windows Compute-Only AMDGPU/KFD Driver

## Executive Summary

This plan outlines the strategy for building an open-source Windows kernel driver based on Linux's amdgpu, enabling ROCm (built from TheRock) to run natively on Windows without AMD's proprietary PAL layer or Adrenaline driver. The approach uses Microsoft's **MCDM (Microsoft Compute Driver Model)** — a compute-only subset of WDDM 2.6+ — combined with a custom KFD device interface for ROCm's HSA runtime.

**Scope:** Compute-only, single GPU, no display. The stock Adrenaline driver handles display on the primary GPU; this driver targets a secondary GPU dedicated to compute.

**Estimated effort:** 12-17 months, 3-4 people.

---

## Background & Motivation

### The Problem

ROCm on Linux talks to GPUs through KFD (Kernel Fusion Driver), which is part of the amdgpu kernel driver. The stack is:

```
ROCm userspace (HSA runtime, HIP, math libs)
    → /dev/kfd (KFD ioctl interface)
    → amdgpu kernel driver (DRM/TTM/scheduler)
    → hardware
```

On Windows, AMD uses a completely different, proprietary stack:

```
DirectX / Vulkan / OpenCL / HIP SDK
    → PAL (Platform Abstraction Layer)
    → Adrenaline WDDM driver (closed-source)
    → hardware
```

There is no KFD on Windows. ROCm built from TheRock cannot run on Windows because the entire kernel interface is missing.

### Why Not Just Use AMD's Windows HIP SDK?

AMD's HIP SDK for Windows uses their proprietary driver stack, not the open-source ROCm/KFD path. It is:
- Closed-source and not buildable from TheRock
- Behind Linux ROCm in features and GPU support
- Not extensible or debuggable by the community

### The Goal

Build an open-source MCDM kernel driver that:
1. Ports Linux amdgpu's compute path to Windows
2. Exposes a KFD-compatible device interface (`\\.\AMDKFD`)
3. Enables ROCm userspace from TheRock to run unmodified (with a ported thunk layer)

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   ROCm Userspace (TheRock)                │
│           HSA Runtime ← HIP ← rocBLAS ← etc.            │
├──────────────────────────────────────────────────────────┤
│  libhsakmt (thunk) - Windows port                        │
│  open("/dev/kfd") → CreateFile("\\\\.\\AMDKFD")          │
│  ioctl()           → DeviceIoControl()                   │
│  mmap()            → MapViewOfFile() / custom mapping    │
│  sysfs topology    → DeviceIoControl() queries           │
├──────────────────────────────────────────────────────────┤
│              amdgpu-win.sys (MCDM Miniport)              │
│  ┌──────────────────────────────────────────────────┐    │
│  │ MCDM DDI Layer (~35 callbacks)                   │    │
│  │  DxgkDdiStartDevice, CreateAllocation,           │    │
│  │  SubmitCommandVirtual, BuildPagingBuffer, ...    │    │
│  ├──────────────────────────────────────────────────┤    │
│  │ KFD Device Interface (\\.\AMDKFD)                │    │
│  │  16 Tier-0 ioctls → DeviceIoControl handlers     │    │
│  │  GET_VERSION, ALLOC_MEMORY, CREATE_QUEUE,        │    │
│  │  CREATE_EVENT, WAIT_EVENTS, ...                  │    │
│  ├──────────────────────────────────────────────────┤    │
│  │ amdgpu core (ported from Linux)                  │    │
│  │  Device init, IP blocks (GMC,IH,PSP,SMU,         │    │
│  │  SDMA,GC,NBIO,DF,MMHUB,ATHUB)                   │    │
│  │  Rings, fences, jobs, GPUVM, firmware loading    │    │
│  │  amdgpu_amdkfd backend                           │    │
│  ├──────────────────────────────────────────────────┤    │
│  │ DRM/TTM/Scheduler shim                           │    │
│  │  TTM memory manager (adapted)                    │    │
│  │  drm_sched (ported as library)                   │    │
│  │  dma_fence (reimplemented)                       │    │
│  ├──────────────────────────────────────────────────┤    │
│  │ WinLinuxKPI (~15-25K lines)                      │    │
│  │  spinlock→KSPIN_LOCK, mutex→KMUTEX,              │    │
│  │  workqueue→IoWorkItem, DMA API→WDF DMA,          │    │
│  │  PCI→WDF PCI, firmware→driver store load         │    │
│  └──────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────┤
│       Windows Kernel (WDDM 2.6+ / MCDM)                 │
│       ComputeAccelerator device class                    │
└──────────────────────────────────────────────────────────┘
```

### Key Architecture Decision: MCDM

MCDM (Microsoft Compute Driver Model) was introduced in Windows 10 1903 (WDDM 2.6). It is a scaled-down WDDM subset designed for compute-only devices (NPUs, ML accelerators, GPUs in compute mode).

Why MCDM over alternatives:
- **~35 required DDIs** vs hundreds for full WDDM display driver
- **ComputeAccelerator device class** — purpose-built, no display DDIs needed
- **GPU virtual addressing supported** — required for GPUVM
- **Can expose custom device interfaces** alongside WDDM path (proven by NVIDIA for CUDA)
- **OS-managed GPU scheduling, TDR, memory residency** included
- **Microsoft provides a [compute-only sample driver](https://github.com/microsoft/graphics-driver-samples)** as starting point

Why not alternatives:
- **Full WDDM display driver:** Orders of magnitude more work. Requires WDDM display DDIs, VidPn, present, overlay, compositor integration.
- **Plain WDM/KMDF driver (skip WDDM entirely):** Loses OS GPU scheduling, virtual memory management, TDR, multi-process isolation. Must rebuild all of that. No precedent — even NVIDIA uses WDDM for compute.
- **Filter driver on top of Adrenaline:** Fragile, depends on undocumented proprietary internals, breaks with every driver update.

---

## Research Findings

### KFD Ioctl Surface Audit

Total mainline ioctls: 39 (plus 5 out-of-tree in ROCK driver).

#### Tier 0: Required for Hello-World Kernel Launch (16 ioctls)

These are called in sequence from `open("/dev/kfd")` to dispatching an AQL packet:

| # | Ioctl | Purpose |
|---|-------|---------|
| 0x01 | `GET_VERSION` | Version handshake (major must be 1) |
| 0x14 | `GET_PROCESS_APERTURES_NEW` | Discover per-GPU GPUVM/LDS/scratch ranges |
| 0x15 | `ACQUIRE_VM` | Bind DRM render fd to KFD for memory ops |
| 0x04 | `SET_MEMORY_POLICY` | Set cache coherency policy |
| 0x16 | `ALLOC_MEMORY_OF_GPU` | Allocate ring buffer, EOP, ctx save, code/data |
| 0x18 | `MAP_MEMORY_TO_GPU` | Map allocations into GPU page tables |
| 0x11 | `SET_SCRATCH_BACKING_VA` | Program hidden scratch base address |
| 0x13 | `SET_TRAP_HANDLER` | Register trap handler address |
| 0x08 | `CREATE_EVENT` | Create signal event for completion notification |
| 0x0A | `SET_EVENT` | Signal event from userspace |
| 0x02 | `CREATE_QUEUE` | Create AQL hardware queue, returns doorbell offset |
| 0x0C | `WAIT_EVENTS` | Block until GPU signals completion |
| 0x03 | `DESTROY_QUEUE` | Teardown |
| 0x09 | `DESTROY_EVENT` | Teardown |
| 0x19 | `UNMAP_MEMORY_FROM_GPU` | Teardown |
| 0x17 | `FREE_MEMORY_OF_GPU` | Teardown |

**Initialization sequence for a minimal kernel launch:**
```
1. open("/dev/kfd")
2. GET_VERSION
3. GET_PROCESS_APERTURES_NEW
4. ACQUIRE_VM
5. SET_MEMORY_POLICY
6. ALLOC_MEMORY_OF_GPU (×N: ring, EOP, ctx save, code, data)
7. MAP_MEMORY_TO_GPU (×N)
8. SET_SCRATCH_BACKING_VA
9. SET_TRAP_HANDLER
10. CREATE_EVENT
11. CREATE_QUEUE → get doorbell offset
12. [Write AQL dispatch packet to ring, ring doorbell]
13. WAIT_EVENTS
14. [Teardown: DESTROY_QUEUE, DESTROY_EVENT, UNMAP, FREE]
```

#### Tier 1: Required for Real Workloads (14 ioctls)

| Ioctl | Use Case |
|-------|----------|
| `UPDATE_QUEUE` | Dynamic queue reconfiguration |
| `RESET_EVENT` | Reusable events in dispatch loops |
| `SVM` | Shared Virtual Memory (`hipMallocManaged`) |
| `SET_XNACK_MODE` | Page-fault retry for SVM |
| `IMPORT_DMABUF` / `EXPORT_DMABUF` / `GET_DMABUF_INFO` | IPC, peer-to-peer |
| `AVAILABLE_MEMORY` | Memory pressure queries |
| `GET_CLOCK_COUNTERS` | Timestamps, profiling |
| `SET_CU_MASK` | CU partitioning |
| `ALLOC_QUEUE_GWS` | Global wave sync / cooperative groups |
| `GET_TILE_CONFIG` | Memory layout optimization (older GFX) |
| `SMI_EVENTS` | System management monitoring |
| `RUNTIME_ENABLE` | Debug mode for HSA runtime |

#### Tier 2: Deferred (14 ioctls)

Debug (`DBG_TRAP`, legacy `DBG_*`), CRIU, profiling (`RLC_SPM`, `PC_SAMPLE`), experimental (`CREATE_PROCESS`).

### FreeBSD LinuxKPI — Prior Art

FreeBSD has the closest existing port of amdgpu to a non-Linux OS.

**What works:** Display/graphics via KMS (HD 7000 through RDNA3). ~20K lines of compat code enables ~2M lines of Linux DRM/GPU driver code.

**What doesn't:** amdkfd is NOT ported (Makefile only, no source files compiled). When developers attempted it, "there were many linuxkpi parts missing" — specifically mm_struct, mmu_notifier, HMM, and process lifecycle hooks.

**Key lessons:**
- GFP flag semantics are the most dangerous subtle bug. FreeBSD's `__GFP_NORETRY` mishandling caused system freezes that took months to diagnose.
- dma-fence/sync/scheduler can be carried from Linux source if underlying primitives are correctly shimmed.
- Each upstream Linux kernel version bump requires reviewing and upstreaming new compat patches. Budget for ongoing maintenance.
- ~20K lines of shim code is a reasonable estimate for the compat layer.

### amdgpu Linux Kernel Dependencies

Total `drivers/gpu/drm/amd/`: ~6M lines, but:
- ~4.4M are auto-generated register headers (only target ASIC headers needed)
- ~400-500K are display code (DCN/DM, not needed)
- Compute-critical code for a single ASIC: ~200-300K lines

#### Dependency Difficulty Ranking

| Subsystem | Difficulty | Notes |
|-----------|-----------|-------|
| HMM / mmu_notifier / migrate_vma | **Extreme** | No Windows equivalent. Required for SVM. Defer. |
| DRM core / GEM | **High** | Tightly coupled to Linux VFS and char device model |
| TTM | **High** | Assumes Linux page allocator, VMA mapping, DMA API |
| dma_fence / dma_resv | **Medium-High** | Concept maps to Windows fences, semantics differ |
| DRM scheduler | **Medium** | Self-contained ~3-4K lines, portable |
| PCI subsystem | **Medium** | Mappable to WDF PCI interfaces |
| DMA API | **Medium** | Windows DMA APIs exist, different shape |
| Firmware loader | **Low-Medium** | Simple file loading from driver store |
| Interrupt handling | **Medium** | Windows MSI-X support exists |
| Workqueue / kthread | **Low-Medium** | IoQueueWorkItem / PsCreateSystemThread |
| Synchronization primitives | **Low** | Direct mappings exist |
| Data structures (IDR, rbtree, etc.) | **Low** | Straightforward reimplementation |

### WDDM Compute-Only Driver Model

**MCDM facts:**
- Introduced Windows 10 1903 (WDDM 2.6)
- Device class: `ComputeAccelerator` (GUID `{F01A9D53-3FF6-48D2-9F97-C8A7004BE10C}`)
- Requires GPU MMU for multi-process isolation
- ~35 required DDI callbacks (device lifecycle, memory, scheduling, interrupts, query, power)
- Virtual addressing is required; physical addressing not supported for MCDM
- `DxgkDdiEscape` available as private ioctl channel (how NVIDIA routes CUDA)
- Custom named device objects allowed (NVIDIA creates `\\.\NvAdminDevice`, `\\.\UVMLiteController`)
- Microsoft provides [compute-only sample driver](https://github.com/microsoft/graphics-driver-samples/wiki/Compute-Only-Sample) as reference

**Driver signing:**
- Test-signing: `bcdedit -set testsigning on` (requires Secure Boot off)
- Preproduction WHQL: Possible with Secure Boot via `EnableUefiSbTest.exe` provisioning
- Production: Requires full HLK certification through Hardware Dev Center

---

## Sizing Estimates

| Component | Lines of Code | Notes |
|-----------|--------------|-------|
| WinLinuxKPI shim layer | ~15-25K | Based on FreeBSD LinuxKPI |
| MCDM DDI implementation | ~5-10K | ~35 callbacks + plumbing |
| KFD device interface | ~5-8K | 16 Tier-0 ioctl handlers + dispatch |
| DRM core subset (compute) | ~10-15K | Device, GEM, PRIME basics |
| TTM (adapted) | ~6-8K | Buffer object + VRAM/GTT managers |
| DRM scheduler (ported) | ~3-4K | Self-contained library |
| dma_fence / dma_resv | ~3-5K | Core fence implementation |
| amdgpu core (single ASIC) | ~100-150K | Device init, rings, VM, IRQ, PSP, GFX, SDMA |
| amdkfd (no SVM) | ~20-30K | Process mgmt, queues, events, memory |
| PM/SMU (single ASIC) | ~20-30K | Power management for target GPU |
| Register headers (single ASIC) | ~50-200K | Copy from Linux, no porting needed |
| libhsakmt Windows port | ~5-10K | Thunk layer: ioctl→DeviceIoControl translation |
| **Total new/ported code** | **~190-290K** | Excluding register headers |

---

## Phase Plan

### Phase 1: MCDM Skeleton + WinLinuxKPI Foundation (3-4 months)

**Goal:** A driver that loads on Windows, claims a GPU via PCI, and passes MCDM validation.

#### 1a: MCDM Miniport Shell (4-6 weeks)

Start from Microsoft's compute-only sample driver (`coskmd.sys`).

Implement:
- `DriverEntry` → `DxgkInitialize`
- INF file with `Class=ComputeAccelerator`
- Device lifecycle DDIs: `AddDevice`, `StartDevice`, `StopDevice`, `RemoveDevice`
- `QueryAdapterInfo` returning `ComputeOnly = TRUE`, `WDDMVersion >= WDDM 2.6`
- PCI BAR mapping through `DxgkDdiStartDevice` resource list
- MSI-X interrupt setup: `DxgkDdiInterruptRoutine` + `DxgkDdiDpcRoutine`
- Test-signing workflow established

**Deliverable:** Driver installs, shows in Device Manager under ComputeAccelerator, starts/stops cleanly.

#### 1b: WinLinuxKPI Core (6-8 weeks, parallel with 1a)

Linux kernel API compatibility layer for Windows. Use FreeBSD's LinuxKPI as reference.

**P0 — Synchronization & threading:**

| Linux API | Windows Implementation |
|-----------|----------------------|
| `spinlock_t` | `KSPIN_LOCK` |
| `mutex` | `KMUTEX` / `KGUARDED_MUTEX` |
| `kref` / `refcount_t` | `InterlockedIncrement` / `InterlockedDecrement` |
| `completion` | `KEVENT` (notification type) |
| `wait_queue_head_t` | `KEVENT` + linked list |
| `workqueue` | `IoQueueWorkItem` / custom thread pool |
| `kthread` | `PsCreateSystemThread` |

**P0 — Memory:**

| Linux API | Windows Implementation |
|-----------|----------------------|
| `kmalloc` / `kfree` | `ExAllocatePool2` |
| `vmalloc` / `vfree` | `ExAllocatePool2` (non-contiguous) |
| `alloc_pages` (GFP flags) | `MmAllocatePagesForMdl` — **handle GFP flags carefully** |
| `ioremap` / `iounmap` | `MmMapIoSpace` / `MmUnmapIoSpace` |
| `dma_alloc_coherent` | `WdfCommonBufferCreate` or `MmAllocateContiguousMemory` |
| `dma_map_sg` | WDF DMA scatter-gather |

**P1 — Device & I/O:**

| Linux API | Windows Implementation |
|-----------|----------------------|
| `pci_*` resource APIs | WDF PCI resource descriptor parsing |
| `request_firmware` | Load from `%SystemRoot%\System32\drivers\` |
| `request_irq` | Handled via MCDM DDIs |
| `timer_list` / `hrtimer` | `KeSetTimerEx` / `KeSetCoalescableTimer` |

**P2 — Data structures & misc:**

| Linux API | Windows Implementation |
|-----------|----------------------|
| `IDR` / `xarray` | Custom implementation |
| `rcu_read_lock` | SRCU shim or reader-writer lock |
| `debugfs` | Stub → ETW tracing |
| `seq_file` | Stub |

**Deliverable:** Shim library compiles, unit tests pass for each primitive.

### Phase 2: amdgpu Core Bring-up (4-6 months)

**Goal:** GPU initializes, firmware loads, rings are operational, NOP packet submission works.

**Target ASIC:** gfx1100 (RDNA3 / Navi 31) or gfx1201 (RDNA4). Pick one to avoid combinatorial explosion of per-ASIC code.

#### 2a: Device Init + Firmware (6-8 weeks)

Port the `amdgpu_device_init()` path:

1. **`amdgpu_discovery`** — reads IP discovery table from GPU ROM. Hardware-format parsing, minimal kernel deps.
2. **NBIO init** — North Bridge I/O, doorbell aperture setup.
3. **GMC init** — VRAM size detection, GART setup, page table base.
4. **PSP init** — load PSP firmware, establish ring communication, authenticate remaining IP firmwares.
5. **SMU init** — basic power management (clocks to known-good state).

Firmware deployment: Copy amdgpu firmware blobs to driver store. The `request_firmware()` shim loads them by name.

Expect significant WinDbg time debugging BSODs during IP block initialization.

#### 2b: Memory Management (6-8 weeks)

**Port TTM** (recommended over rewriting against WDDM memory model):
- Buffer object lifecycle: init, validate, move, destroy
- VRAM range manager (straightforward)
- GTT manager backed by Windows system memory pages
- Wire TTM to MCDM's `DxgkDdiCreateAllocation` / `DestroyAllocation` / `BuildPagingBuffer`
- Page allocator shim (`alloc_pages` → `MmAllocatePagesForMdl`) — trickiest part

GPU page tables (`amdgpu_vm`):
- Port GPUVM page table management
- SDMA-based page table updates
- VMID allocation pool

#### 2c: Ring Buffers + Scheduler (4-6 weeks)

- Port `amdgpu_ring` — GFX ring and SDMA ring for target ASIC
- Port DRM scheduler as standalone library (~3-4K lines)
- Port `dma_fence` — core signaling primitive (~3-5K lines)
- Wire interrupt handler: IH ring → fence signal → scheduler feedback
- Doorbell write mechanism

**Milestone test:** Submit a NOP packet to the GFX ring, get a fence signal back. This proves the entire init → submit → interrupt → signal path works.

### Phase 3: KFD + Thunk (3-4 months)

**Goal:** `hipMemcpy` + simple kernel launch working through the ROCm stack.

#### 3a: KFD Device Interface (4-6 weeks)

Create `\\.\AMDKFD` device object from within the MCDM miniport:

```c
// In DxgkDdiStartDevice:
IoCreateDevice(..., L"\\Device\\AMDKFD", ...);
IoCreateSymbolicLink(L"\\DosDevices\\AMDKFD", L"\\Device\\AMDKFD");
// Register IRP_MJ_DEVICE_CONTROL handler
```

Implement 16 Tier-0 ioctls as `DeviceIoControl` codes:

| Linux Ioctl | Implementation Notes |
|-------------|---------------------|
| `GET_VERSION` | Return `{1, 22}` |
| `GET_PROCESS_APERTURES_NEW` | Return GPUVM/LDS/scratch ranges |
| `ACQUIRE_VM` | Bind process to GPU VM context (no DRM fd on Windows) |
| `SET_MEMORY_POLICY` | Configure cache policy via amdgpu_amdkfd |
| `ALLOC_MEMORY_OF_GPU` | Allocate via amdgpu_amdkfd → TTM |
| `MAP_MEMORY_TO_GPU` | Update GPU page tables |
| `UNMAP_MEMORY_FROM_GPU` | Remove GPU page table entries |
| `FREE_MEMORY_OF_GPU` | Free via TTM |
| `SET_SCRATCH_BACKING_VA` | Program SH_HIDDEN_PRIVATE_BASE |
| `SET_TRAP_HANDLER` | Register TBA/TMA addresses |
| `CREATE_QUEUE` | Create AQL HW queue + doorbell |
| `DESTROY_QUEUE` | Teardown HW queue |
| `CREATE_EVENT` | Allocate signal event |
| `DESTROY_EVENT` | Free signal event |
| `SET_EVENT` | Signal from userspace |
| `WAIT_EVENTS` | Block on completion (with timeout) |

**`ACQUIRE_VM` adaptation:** On Linux, this binds a DRM render fd. On Windows, have it implicitly bind to the MCDM device context or accept a WDDM device handle.

**Doorbell mmap:** On Linux, doorbells are mmap'd to userspace. On Windows, use `ZwMapViewOfSection` or MDL-based mapping from kernel. Test early — this is a potential blocker.

#### 3b: libhsakmt Windows Port (4-6 weeks)

The thunk library (~15-20 source files). Mostly mechanical translation:

| Linux API | Windows Replacement |
|-----------|-------------------|
| `open("/dev/kfd")` | `CreateFileW(L"\\\\.\\AMDKFD", ...)` |
| `ioctl(fd, cmd, &arg)` | `DeviceIoControl(handle, cmd, &arg, ...)` |
| `mmap(PROT_*, MAP_SHARED, fd, offset)` | `MapViewOfFile()` or custom mapping via ioctl |
| `close(fd)` | `CloseHandle()` |
| sysfs topology | New `IOCTL_KFD_GET_TOPOLOGY` or registry |
| `pthread_*` | `CreateThread` / `SRWLock` / `ConditionVariable` |
| `eventfd` | Windows `Event` objects |
| `numa_*` | `GetNumaProcessorNode` etc. |

**Topology:** On Linux, libhsakmt reads `/sys/class/kfd/kfd/topology/`. On Windows, add a `IOCTL_KFD_GET_TOPOLOGY` ioctl that returns equivalent data (node count, GPU properties, memory banks, IO links).

#### 3c: Integration Testing (2-4 weeks)

Walk through the full ROCm initialization:
1. HSA runtime init → thunk → KFD → GPU queues created
2. Code object loading → kernel binary placed in VRAM
3. `hipMemcpy` H2D → SDMA copy
4. Kernel dispatch → AQL packet → doorbell → GFX execution
5. `hipMemcpy` D2H → SDMA copy back
6. Completion signal → event → thunk → HSA runtime

**Deliverable:** `hipMemcpy` + simple kernel launch working end-to-end.

### Phase 4: Stabilization + Tier 1 Ioctls (2-3 months)

**Goal:** Real workloads running (rocBLAS, PyTorch inference).

Add Tier 1 ioctls:
- `UPDATE_QUEUE`, `RESET_EVENT` — queue/event lifecycle
- `AVAILABLE_MEMORY`, `GET_CLOCK_COUNTERS` — diagnostics
- `IMPORT_DMABUF` / `EXPORT_DMABUF` → Windows shared handle equivalent
- `SET_CU_MASK`, `ALLOC_QUEUE_GWS` — advanced queue config
- `SMI_EVENTS` — system management monitoring

Stability work:
- GPU reset recovery (TDR integration)
- Multi-process isolation testing
- Memory pressure / eviction testing
- Long-running workload stability

---

## Critical Design Issues

### WDDM Scheduler vs KFD Queue Conflict

This is the most important architectural tension in the design.

**MCDM/WDDM expects:** OS submits DMA buffers → driver's `DxgkDdiSubmitCommandVirtual` → hardware.

**KFD expects:** Userspace writes AQL packets directly to ring buffer → rings doorbell → hardware.

These are fundamentally different submission models. KFD bypasses the OS scheduler entirely — that's the point (low-latency dispatch).

**Options:**

| Option | Approach | Risk |
|--------|---------|------|
| **A: Minimal MCDM + real work through KFD** | Implement MCDM DDIs enough to keep Windows happy. Route all compute through KFD device interface. MCDM engine reports idle. | WDDM TDR watchdog may interfere if GPU is busy with KFD work while MCDM thinks it's idle. |
| **B: Bridge KFD through WDDM HW scheduling** | Map KFD AQL queues as WDDM 2.7+ HW queues via `DxgkDdiSubmitCommandToHwQueue`. | More correct, much more work. AMD's MES (Micro Engine Scheduler) on RDNA3+ aligns well. |
| **C: Custom device objects (NVIDIA model)** | Route compute through `\\.\AMDKFD` + `DxgkDdiEscape`, not WDDM DMA buffers. | Proven by NVIDIA for CUDA in production. |

**Recommendation:** Option A for MVP. NVIDIA proves Option C works at scale. Graduate to Option B if TDR becomes a problem.

### SVM (Shared Virtual Memory)

SVM on Linux uses HMM (`hmm_range_fault`), `mmu_notifier`, and `migrate_vma` — deep Linux mm subsystem integration with no Windows equivalent.

**Decision:** Defer SVM to a future phase. Most ML workloads use explicit `hipMalloc` / `hipMemcpy` and do not require SVM. When SVM is needed, potential Windows approaches:
- Hook into Windows' own GPU virtual memory / WDDM residency model
- Use `VirtualAlloc` + custom page fault handling via vectored exception handlers
- Implement a simplified migration scheme without full HMM semantics

### Coexistence with Adrenaline Driver

Two drivers cannot claim the same GPU. Options:
1. **Dedicated second GPU** — Install Adrenaline on GPU 0 (display), this driver on GPU 1 (compute). Cleanest approach.
2. **Disable Adrenaline on target GPU** via Device Manager, install this driver. Works for single-GPU testing.
3. **Different PCI device IDs** — Not viable since it's the same hardware.

**Recommendation:** Dedicated second GPU for development and deployment. This mirrors how HPC/ML setups work (display on integrated/cheap GPU, compute on discrete).

### Driver Signing & Deployment

| Mode | Secure Boot | Use Case |
|------|------------|----------|
| Test-signing (`bcdedit -set testsigning on`) | Must be OFF | Development |
| Preproduction WHQL (via `EnableUefiSbTest.exe`) | ON (with test keys) | Pre-release testing |
| Production WHQL (HLK certification) | ON | End-user deployment |

Development will use test-signing. Production deployment requires eventual HLK certification through Microsoft's Hardware Dev Center.

---

## Explicit Scope Exclusions

| Feature | Why Excluded |
|---------|-------------|
| Display / KMS / DCN | Compute-only. Adrenaline handles display. |
| VCN / JPEG | Video encode/decode not needed for ROCm compute. |
| SVM (initial) | Requires HMM/mmu_notifier port. Extreme difficulty. |
| Multi-GPU | Single GPU first. |
| CRIU | Container checkpoint/restore — Linux-specific. |
| Debug API (`DBG_TRAP`) | rocgdb support deferred. |
| Profiling (`RLC_SPM`, `PC_SAMPLE`) | Out-of-tree ioctls, deferred. |
| D3D12 UMD | MCDM includes D3D12 path; we only need KFD initially. |
| VGA switcheroo | Laptop dual-GPU switching — not relevant for compute. |

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| WDDM TDR vs KFD queue conflict | High | Start with Option A; disable TDR on compute engine if possible; graduate to HW scheduling |
| Doorbell mmap to userspace on Windows | High | Test MDL-based mapping early in Phase 2; fallback to kernel-mediated doorbell writes |
| PSP firmware handshake | Medium | Must follow exact Linux amdgpu sequence; use WinDbg + serial console for debugging |
| TTM page allocator on Windows | Medium | Budget extra time; FreeBSD's biggest pain point was GFP flag semantics |
| GPU coexistence with Adrenaline | Medium | Require dedicated second GPU; document clearly |
| Upstream amdgpu divergence | Medium | Pin to specific kernel version; rebase periodically |
| Performance parity with Linux | Medium | Defer optimization; correctness first |
| Microsoft signing for production | Medium | Use test-signing for development; plan HLK path for production |

---

## Timeline Summary

| Phase | Duration | Team | Deliverable |
|-------|----------|------|------------|
| 1: MCDM + WinLinuxKPI | 3-4 months | 2-3 | Driver loads, GPU claimed |
| 2: amdgpu bring-up | 4-6 months | 3-4 | GPU initialized, rings running, NOP submission |
| 3: KFD + Thunk | 3-4 months | 2-3 | hipMemcpy + kernel launch |
| 4: Stabilization | 2-3 months | 2-3 | Real workloads (rocBLAS, PyTorch) |
| **Total MVP** | **12-17 months** | **3-4 people** | **ROCm compute on Windows** |

The critical-path person is someone who knows both Windows kernel driver development (WDDM/MCDM, WDF, WinDbg) AND Linux amdgpu internals.

---

## References

- [MCDM Overview](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/mcdm)
- [MCDM Architecture](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/mcdm-architecture)
- [MCDM Implementation Guidelines](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/mcdm-implementation-guidelines)
- [Microsoft Compute-Only Sample Driver](https://github.com/microsoft/graphics-driver-samples/wiki/Compute-Only-Sample)
- [WDDM 2.0 GPU Virtual Memory](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-virtual-memory-in-wddm-2-0)
- [Driver Signing Policy](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/kernel-mode-code-signing-policy--windows-vista-and-later-)
- [FreeBSD LinuxKPI](https://github.com/freebsd/freebsd-src/tree/master/sys/compat/linuxkpi)
- [FreeBSD drm-kmod](https://github.com/freebsd/drm-kmod)
- [Linux kernel kfd_ioctl.h](https://github.com/torvalds/linux/blob/master/include/uapi/linux/kfd_ioctl.h)
- [Linux amdgpu documentation](https://docs.kernel.org/gpu/amdgpu/index.html)
- [ROCm/TheRock](https://github.com/ROCm/TheRock)
- [NVIDIA TCC mode](https://archive.docs.nvidia.com/gameworks/content/developertools/desktop/tesla_compute_cluster.htm)
- [Project Zero: Attacking Windows NVIDIA Driver](https://projectzero.google/2017/02/attacking-windows-nvidia-driver.html)

## Alternatives Considered

1. **WSL2 GPU passthrough** — Runs ROCm in a Linux VM. Not a native Windows driver. Adds latency and complexity. Doesn't achieve the goal of replacing PAL/Adrenaline.

2. **Modify AMD's open-source PAL** — PAL is open source but designed around AMD's proprietary kernel driver. Still need the kernel-mode component. PAL is a Vulkan/DX abstraction, not a KFD replacement.

3. **KFD filter driver on top of Adrenaline** — Inject a filter driver exposing KFD semantics using Adrenaline's internal interfaces for hardware access. Fragile, depends on undocumented internals, breaks with every driver update.

4. **Lobby AMD to ship official Windows ROCm/KFD** — The political path. Less engineering, more uncertainty, no community control.

5. **Port only the userspace (thunk + runtime) and use WDDM for GPU access** — Rewrite the thunk to use WDDM/D3D12 APIs instead of KFD. Fundamentally different submission model (WDDM DMA buffers vs AQL packets). Would require rewriting significant portions of the HSA runtime. Performance characteristics would change.
