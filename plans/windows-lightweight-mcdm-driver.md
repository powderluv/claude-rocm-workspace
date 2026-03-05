# Plan: Windows Lightweight MCDM Driver — Python GPU Evaluation

## Context & Relationship to Prior Work

The [full MCDM plan](windows-userspace-driver.md) proposed porting Linux amdgpu to a Windows MCDM miniport — 190-290K lines, 12-17 months, 3-4 people. That plan produced valuable research (KFD ioctl audit, FreeBSD lessons, dependency analysis, WDDM scheduler conflict analysis) which directly informs this approach.

This plan takes a different strategy: **keep GPU logic in Python** with a minimal MCDM kernel driver that satisfies Windows' WDDM requirements while routing real GPU work through `DxgkDdiEscape`. The kernel does the minimum needed to be a valid MCDM miniport (DDI callbacks, memory segment reporting, TDR handling) plus hardware access that userspace cannot do (PCI BAR mapping, DMA allocation, interrupt handling). All IP discovery, firmware loading, ring setup, and command submission logic stays in Python.

This serves as an evaluation phase — proving hardware feasibility before committing to the full MCDM port.

### Architecture

```
Python userspace driver
  → D3DKMTEscape / DeviceIoControl
    → dxgkrnl.sys
      → DxgkDdiEscape → amdgpu_mcdm.sys
          ├── MCDM DDI layer (37 callbacks, mostly stubs)
          │   ├── QueryAdapterInfo (ComputeOnly=TRUE)
          │   ├── Memory segments (VRAM + system, minimal)
          │   ├── BuildPagingBuffer (stub)
          │   ├── SubmitCommandVirtual (stub/no-op)
          │   └── TDR handlers (GPU reset via register writes)
          ├── Escape handler (routes Python ↔ hardware commands)
          │   ├── READ_REG32 / WRITE_REG32
          │   ├── MAP_BAR / UNMAP_BAR
          │   ├── ALLOC_DMA / FREE_DMA
          │   ├── MAP_VRAM
          │   ├── REGISTER_EVENT / ENABLE_MSI
          │   └── GET_INFO / GET_IOMMU_INFO
          ├── PCI resource management
          ├── MSI-X interrupt handling
          └── WDF DMA framework
```

### How the Three Plans Relate

| Aspect | Full MCDM Plan | Original Lightweight | This Plan (Lightweight MCDM) |
|--------|---------------|---------------------|------------------------------|
| **Goal** | Production ROCm on Windows | Evaluate feasibility | Evaluate feasibility + production path |
| **Driver model** | MCDM (full implementation) | Plain KMDF (Class=System) | MCDM (fake/minimal) |
| **Kernel code** | 190-290K lines ported C | ~2-4K lines new C | ~8-15K lines new C |
| **Total code** | 190-290K | ~7.5-12.5K | ~13.5-24K |
| **Timeline** | 12-17 months, 3-4 people | 15-28 weeks, 1-2 people | 22-40 weeks, 1-2 people |
| **GPU logic** | All in kernel C | All in Python | All in Python |
| **Debug experience** | WinDbg (BSODs) | Python debugger | Python debugger (mostly) + WinDbg for DDIs |
| **WDDM involvement** | Full | None | Minimal (stubs) |
| **Production signing** | Yes (HLK) | No (attestation only) | Yes (HLK path exists) |
| **TDR recovery** | Yes | No (manual reboot on hang) | Yes (kernel-side GPU reset) |
| **Multi-process safety** | Yes (dxgkrnl) | No | Yes (dxgkrnl) |
| **Upgrade path** | N/A (is the target) | Throw away kernel code | DDI skeletons reusable |
| **SVM support** | Deferred (extreme) | Deferred | Deferred |
| **ROCm stack compat** | Full (libhsakmt ported) | Driver-level only | Driver-level only |

### Why MCDM Instead of Plain KMDF

The original lightweight plan used `Class=System` to avoid WDDM entirely. Switching to MCDM costs ~6-12 weeks of additional DDI boilerplate but gains:

1. **Production signing path** — HLK certification is possible. Plain KMDF under Class=System has no GPU-specific HLK path.
2. **TDR recovery** — GPU hangs during bring-up (likely) get OS-managed timeout and reset instead of manual reboot.
3. **Multi-process safety** — dxgkrnl provides process isolation; plain KMDF has race conditions if two processes open the device.
4. **Stepping stone** — The 37 DDI skeletons transfer directly to the full MCDM plan. With plain KMDF, the kernel code is throwaway.
5. **Ecosystem coexistence** — MCDM drivers sit in the WDDM ecosystem properly; plain KMDF under Class=System is outside it.

The key architectural bet: real GPU work routes through `DxgkDdiEscape` as a private channel (the "fake MCDM" pattern), while MCDM scheduling DDIs are satisfied with stubs. This is conceptually similar to how NVIDIA routes CUDA through WDDM.

---

## Existing Codebase

The `users/powderluv/userspace-driver` branch in D:\R has a working Python userspace GPU driver for Linux (~9700 lines new code across 5 commits), now importable on Windows (Phase 0 complete). Key architecture:

```
Public API (AMDDevice, Buffer, Program, MultiGPUContext)
  → Backend Abstraction (DeviceBackend in backends/base.py)
    → KFD Backend (backends/kfd/) — Linux-only
      → Ioctl Layer (ioctl/helpers.py, ioctl/kfd.py)
        → Linux Kernel KFD (/dev/kfd, /dev/dri/renderD*)
```

Key capabilities demonstrated on Linux:
- Device discovery via sysfs topology
- VRAM and GTT memory allocation (KFD ioctls → TTM)
- Compute queue creation and PM4 packet submission
- SDMA copy with automatic chunking (>4MB)
- Compute kernel dispatch (ELF parser, kernel descriptor, SET_SH_REG + DISPATCH_DIRECT)
- Multi-GPU P2P memory access and XGMI SDMA copies
- Timeline semaphores (RELEASE_MEM + WAIT_REG_MEM)
- Compute-communication overlap

GPU family configs exist for: CDNA2 (gfx90a), CDNA3 (gfx942), RDNA2, RDNA3, **RDNA4 (gfx1200/1201)**.

**Target hardware:** AMD RX 9070 XT (PCI DEV_7551, RDNA 4 / GFX1201 / Navi 48) — currently unclaimed on this Windows 11 machine (no driver loaded). AMD Ryzen 9 7950X CPU.

**Phase 0 status:** Complete. All 109 unit tests pass on Windows, 121 integration tests properly skip. Platform-guarded libc loading, cross-platform `ctypes.memset`, lazy KFD backend import.

---

## Phase 1: MCDM Kernel Driver (8-12 weeks)

An MCDM miniport driver (~8-15K lines C) that satisfies the 37 required DDIs with minimal implementations while providing hardware access to Python through the escape channel.

### 1.1: INF and Device Class

Use `Class=ComputeAccelerator` (GUID `{F01A9D53-3FF6-48D2-9F97-C8A7004BE10C}`). Match `PCI\VEN_1002&DEV_7551`. Test-signing with `bcdedit -set testsigning on` (Secure Boot already off on this machine).

Entry point: `DriverEntry` → `DxgkInitialize` (not standard WDF `DriverEntry` → `WdfDriverCreate`).

### 1.2: DDI Implementation Strategy

The 37 required DDIs fall into categories by implementation depth:

**Real implementations (hardware interaction):**

| DDI | Purpose | Lines |
|-----|---------|-------|
| `DxgkDdiStartDevice` | Parse PCI resources, map BARs, set up DMA | ~200 |
| `DxgkDdiStopDevice` | Unmap BARs, release resources | ~50 |
| `DxgkDdiInterruptRoutine` | MSI-X ISR — check interrupt source, schedule DPC | ~80 |
| `DxgkDdiDpcRoutine` | Signal Windows Events from interrupt context | ~60 |
| `DxgkDdiResetFromTimeout` | Write GPU soft reset registers (GRBM, etc.) | ~150 |
| `DxgkDdiResetEngine` | Per-engine reset via register writes | ~100 |
| `DxgkDdiEscape` | Route Python commands to/from hardware (the core) | ~500 |
| `DxgkDdiQueryAdapterInfo` | Report capabilities, memory segments | ~300 |
| `DxgkDdiSetPowerState` | Basic power state handling | ~80 |

**Minimal implementations (satisfy WDDM contract):**

| DDI | Implementation | Lines |
|-----|---------------|-------|
| `DxgkDdiAddDevice` / `RemoveDevice` | Allocate/free device context | ~40 each |
| `DxgkDdiCreateDevice` / `DestroyDevice` | Allocate/free per-device-handle context | ~30 each |
| `DxgkDdiCreateContext` / `DestroyContext` | Allocate/free per-context struct | ~30 each |
| `DxgkDdiCreateAllocation` / `DestroyAllocation` | Track allocation metadata (no real GPU alloc) | ~100 each |
| `DxgkDdiOpenAllocation` / `CloseAllocation` | Ref counting on allocation handles | ~30 each |
| `DxgkDdiBuildPagingBuffer` | Return success (Python manages GPU page tables) | ~50 |
| `DxgkDdiSubmitCommandVirtual` | No-op — real work goes through Escape | ~30 |
| `DxgkDdiPreemptCommand` | Signal completion immediately | ~40 |
| `DxgkDdiRestartFromTimeout` | Re-init after reset | ~60 |
| `DxgkDdiCreateProcess` / `DestroyProcess` | Per-process tracking | ~40 each |
| `DxgkDdiGetRootPageTableSize` | Return a fixed size | ~10 |
| `DxgkDdiSetRootPageTable` | Store page table base (Python manages content) | ~20 |
| `DxgkDdiDescribeAllocation` | Return allocation properties | ~30 |
| `DxgkDdiGetStandardAllocationDriverData` | Standard alloc metadata | ~30 |

**Pure stubs (return STATUS_SUCCESS or STATUS_NOT_SUPPORTED):**

| DDI | Notes |
|-----|-------|
| `DxgkDdiSetStablePowerState` | Power hint, not required |
| `DxgkDdiSetVirtualMachineData` | VM passthrough, not needed |
| `DxgkDdiCalibrateGpuClock` | Return CPU clock |
| `DxgkDdiCollectDbgInfo` | Return empty |
| `DxgkDdiFormatHistoryBuffer` | Return empty |
| `DxgkDdiGetNodeMetadata` | Report one compute node |
| `DxgkDdiQueryDependentEngineGroup` | Single engine group |
| `DxgkDdiQueryDeviceDescriptor` | Return NOT_SUPPORTED |
| `DxgkDdiQueryEngineStatus` | Report engine active |
| `DxgkDdiQueryChildStatus` / `Relations` / `ConnectionChange` | No children (compute-only) |
| `DxgkDdiUnload` | Cleanup |

### 1.3: Escape Command Interface

The `DxgkDdiEscape` handler is the heart of the driver — it routes commands between Python and hardware. The escape buffer carries a command header followed by command-specific data.

| Escape Command | Purpose | Maps to Linux |
|----------------|---------|---------------|
| `ESCAPE_GET_INFO` | PCI IDs, BAR addresses/sizes, VRAM size | PCI resource enumeration |
| `ESCAPE_MAP_BAR` / `UNMAP_BAR` | Map PCI BAR region to caller's VA | `mmap()` on DRM render fd |
| `ESCAPE_READ_REG32` / `WRITE_REG32` | MMIO register read/write (fallback) | `RREG32()`/`WREG32()` in amdgpu |
| `ESCAPE_ALLOC_DMA` / `FREE_DMA` | Contiguous DMA memory (VA + bus addr) | `dma_alloc_coherent()` |
| `ESCAPE_MAP_VRAM` | Map VRAM range (via BAR2) to caller | DRM mmap of VRAM BOs |
| `ESCAPE_REGISTER_EVENT` / `ENABLE_MSI` | MSI-X interrupt → Windows Event | `request_irq()` + KFD events |
| `ESCAPE_GET_IOMMU_INFO` | Query IOMMU status | N/A (transparent on Linux) |

**User-mode access:** Python calls `D3DKMTEscape` (from `gdi32.dll`) which goes through `dxgkrnl.sys` to our `DxgkDdiEscape`. Alternatively, if `D3DKMTEscape` proves too cumbersome, we can use `DxgkDdiDispatchIoRequest` for a standard `DeviceIoControl` path.

**BAR mapping detail:** `DxgkDdiEscape` runs at PASSIVE_LEVEL. For BAR mapping, allocate an MDL for the BAR physical range, call `MmMapLockedPagesSpecifyCache` to map into the calling process's address space. Return the mapped VA through the escape buffer.

### 1.4: Memory Segment Reporting

MCDM requires reporting memory segments via `DXGKQAITYPE_QUERYSEGMENT4`. We report minimally:

- **Segment 1 (VRAM):** Size from BAR2 or CONFIG_MEMSIZE register. `CpuVisible = TRUE` if ReBAR enabled. `PopulatedFromSystemMemory = FALSE`.
- **Segment 2 (System Memory):** Aperture segment for GTT-like access. `PopulatedFromSystemMemory = TRUE`.

The OS uses this for residency tracking but since `BuildPagingBuffer` is a stub (returns success), actual memory management stays in Python.

### 1.5: TDR Handling

TDR is the main new concern vs. plain KMDF. During GPU bring-up, long-running Python operations (firmware loading, ring init) could trigger TDR's 2-second default timeout.

**Development mitigation:** Set `TdrLevel=0` (disable TDR) in registry during bring-up:
```
HKLM\System\CurrentControlSet\Control\GraphicsDrivers\TdrLevel = 0
```

**Production handling:** Implement `DxgkDdiResetFromTimeout` with real GPU reset:
1. Write GRBM soft reset register
2. Wait for reset completion
3. Re-initialize minimal state

`DxgkDdiResetEngine` for per-engine reset (GFX compute engine, SDMA engine).

`DxgkDdiPreemptCommand` returns immediately (since SubmitCommandVirtual is a no-op, there's nothing to preempt).

### 1.6: IOMMU Handling

Use WDF DMA framework throughout (even within MCDM miniport, WDF DMA APIs are available). Bus addresses from `WdfCommonBufferGetAlignedLogicalAddress` are IOMMU-translated.

### 1.7: Incremental Prototyping

Start from Microsoft's `coskmd` sample as a skeleton:

1. **v0.1** (~2K lines): DDI stubs + DriverEntry + INF. Driver installs and shows in Device Manager.
2. **v0.2** (+1.5K lines): Real `StartDevice` — PCI BAR mapping. `Escape` handler for READ_REG32/WRITE_REG32.
3. **v0.3** (+1.5K lines): `MAP_BAR` for full BAR0 userspace mapping. `ALLOC_DMA`/`FREE_DMA`.
4. **v0.4** (+1K lines): MSI-X interrupt handling. `REGISTER_EVENT`.
5. **v0.5** (+1K lines): `MAP_VRAM` for BAR2. TDR handlers with real GPU reset.
6. **v0.6** (+1K lines): QueryAdapterInfo with real segment info. Harden escape handler.

### 1.8: Code Structure

```
kernel_driver/
    driver_entry.c          -- DriverEntry → DxgkInitialize
    ddi_device.c            -- AddDevice, StartDevice, StopDevice, RemoveDevice
    ddi_memory.c            -- CreateAllocation, DestroyAllocation, BuildPagingBuffer, segments
    ddi_scheduling.c        -- SubmitCommandVirtual, PreemptCommand (stubs)
    ddi_interrupt.c         -- InterruptRoutine, DpcRoutine
    ddi_query.c             -- QueryAdapterInfo, GetNodeMetadata, engine queries
    ddi_tdr.c               -- ResetFromTimeout, RestartFromTimeout, ResetEngine
    ddi_escape.c            -- Escape handler (routes Python commands)
    ddi_stubs.c             -- Pure stubs (SetPowerState, CalibrateGpuClock, etc.)
    bar_mapping.c           -- PCI BAR enum + MDL-based userspace mapping
    dma_alloc.c             -- Contiguous DMA memory via WDF DMA framework
    amdgpu_mcdm.h           -- Escape command codes, shared structures
    amdgpu_mcdm.inf         -- ComputeAccelerator PCI device matching
```

### 1.9: Driver Signing

Same as the full MCDM plan:

| Mode | Secure Boot | Use Case |
|------|------------|----------|
| Test-signing (`bcdedit -set testsigning on`) | Must be OFF | Development |
| Preproduction WHQL (`EnableUefiSbTest.exe`) | ON (test keys) | Pre-release |
| Production WHQL (HLK certification) | ON | End users |

---

## Phase 2: Windows Python Backend (4-8 weeks)

### 2.1: New Directory Structure

```
backends/windows/
    __init__.py           -- exports WindowsDevice
    device.py             -- WindowsDevice(DeviceBackend)
    driver_interface.py   -- D3DKMTEscape wrapper / ctypes bindings
    memory.py             -- WindowsMemoryManager
    queue.py              -- WindowsQueueManager
    events.py             -- WindowsEventManager
    discovery.py          -- IP discovery table parser via register reads
```

### 2.2: D3DKMTEscape Wrapper

The Python-to-kernel communication goes through `D3DKMTEscape` in `gdi32.dll`:

```python
import ctypes
from ctypes import wintypes

gdi32 = ctypes.WinDLL("gdi32")

class D3DKMT_ESCAPE(ctypes.Structure):
    _fields_ = [
        ("hAdapter", wintypes.HANDLE),
        ("hDevice", wintypes.HANDLE),
        ("Type", ctypes.c_uint),           # D3DKMT_ESCAPE_DRIVERPRIVATE
        ("Flags", ctypes.c_uint),
        ("pPrivateDriverData", ctypes.c_void_p),
        ("PrivateDriverDataSize", ctypes.c_uint),
        ("hContext", wintypes.HANDLE),
    ]

def escape(adapter_handle, command_buffer, size):
    """Send an escape command to the MCDM driver."""
    args = D3DKMT_ESCAPE()
    args.hAdapter = adapter_handle
    args.Type = 0  # D3DKMT_ESCAPE_DRIVERPRIVATE
    args.pPrivateDriverData = ctypes.addressof(command_buffer)
    args.PrivateDriverDataSize = size
    status = gdi32.D3DKMTEscape(ctypes.byref(args))
    if status != 0:
        raise RuntimeError(f"D3DKMTEscape failed: NTSTATUS 0x{status:08x}")
```

Adapter discovery uses `D3DKMTEnumAdapters3` to find our MCDM device by PCI VEN/DEV.

### 2.3: Key Mappings from KFD → Windows MCDM

| KFD (Linux) | Windows MCDM | Notes |
|-------------|-------------|-------|
| `open("/dev/kfd")` | `D3DKMTEnumAdapters3` + `D3DKMTOpenAdapterFromLuid` | Find our ComputeAccelerator |
| `ioctl(fd, KFD_CMD, ...)` | `D3DKMTEscape(ESCAPE_CMD, ...)` | Through dxgkrnl |
| `mmap(fd, BAR_offset)` | `ESCAPE_MAP_BAR` → mapped VA | MDL mapping from kernel |
| sysfs topology | IP discovery table via BAR0 registers | No sysfs on Windows |
| `ACQUIRE_VM` | Implicit (we own the GPU) | No DRM fd |
| KFD `ALLOC_MEMORY_OF_GPU` | VRAM: free-list + `ESCAPE_MAP_VRAM`; GTT: `ESCAPE_ALLOC_DMA` | |
| KFD `MAP_MEMORY_TO_GPU` | Python writes GPU page table entries via MMIO | |
| KFD `CREATE_QUEUE` | Python writes MQD + activates via MES | |
| KFD events | Windows Events via `ESCAPE_REGISTER_EVENT`, or spin-poll | |

### 2.4: Memory Management

From the full MCDM plan's research, KFD memory operations follow a 4-step pattern:
1. Reserve VA space (Windows: `VirtualAlloc(MEM_RESERVE)`)
2. Allocate backing pages (Windows: `ESCAPE_ALLOC_DMA` or VRAM allocator)
3. CPU mapping (Windows: kernel provides mapped VA directly via escape)
4. GPU page table update (Windows: Python writes PTEs via MMIO)

**VRAM:** Python-managed free-list allocator. Map via `ESCAPE_MAP_VRAM` (BAR2). Program GPU page tables via MMIO.

**GTT (system memory):** `ESCAPE_ALLOC_DMA` returns usermode VA + bus address. Bus address goes into GPU page table entries.

**Initial simplification:** Use flat identity mapping for a contiguous VRAM region. Avoid full multi-level page table management until basic dispatch works.

### 2.5: Queue Management

Since real GPU work goes through the escape channel (not WDDM scheduling), there's no scheduler conflict. Python manages queues directly, same as on Linux.

RDNA4 uses MES (Micro Engine Scheduler) for queue activation:
1. MES firmware loaded via PSP
2. MQD (Memory Queue Descriptor) written
3. MES ring command to activate the queue

---

## Phase 3: GPU Bring-up (6-12 weeks)

### 3.1: Register Access (Week 1) — FIRST MILESTONE

- Install kernel driver v0.2
- From Python: `escape_read_reg32(0x0)` on BAR0
- Most registers use SMN indirect access: write target address to NBIO index reg, read result from NBIO data reg
- Read `mmRCC_CONFIG_MEMSIZE` to confirm VRAM size
- **Milestone: "Hello GPU" — confirm register reads work through MCDM escape**

### 3.2: IP Discovery (Week 2)

- IP discovery table lives at `VRAM_SIZE - 64KB` (binary format in `drivers/gpu/drm/amd/include/discovery.h`)
- Read via BAR2 (if ReBAR enabled) or MMIO readback
- Parse in Python: identify all IP blocks, versions, base addresses
- Expected for GFX1201: GC v12.0, SDMA v7.0, NBIO v7.11, IH v7.0, PSP v14.0, SMU v14+, GMC v12.0, MMHUB v4.x, ATHUB v4.x
- **Milestone: Print full IP block table for the RX 9070 XT**

### 3.3: NBIO + Doorbell Init (Week 3)

- Program doorbell aperture size and base
- Enable BAR access
- Map doorbell BAR to userspace (via ESCAPE_MAP_BAR)
- Reference: `nbio_v7_11_funcs` in Linux amdgpu at `C:\Users\nod\github\amdgpu`

### 3.4: GMC (Memory Controller) Init (Weeks 4-5)

- VRAM size and configuration from MC registers
- GART setup (system memory access from GPU)
- GPU VM page table base programming
- Memory hub routing (MMHUB, ATHUB)
- From the full MCDM plan: GMC is "High" difficulty due to page allocator semantics

### 3.5: PSP Init + Firmware Loading (Weeks 5-7) — HIGHEST RISK

- PSP mailbox handshake via register writes
- Load SOS firmware, then IP-specific firmware (GC, SDMA, MES, SMU)
- Firmware files from linux-firmware package (placed in a local directory)
- **Risk: PSP may reject firmware loading from a non-official driver context. Test the mailbox handshake ASAP to determine if this is a hard blocker.**
- **Fallback: VBIOS should have already initialized PSP during POST. Check if existing firmware state is sufficient for compute.**
- **Note:** Being an MCDM driver (vs plain KMDF) may help here — the GPU is claimed by a "real" WDDM driver, which might satisfy any driver-context checks in PSP. This is speculative and must be tested.
- Reference: `psp_v14_0.c` in Linux amdgpu

### 3.6: Ring Bring-up (Weeks 7-9)

- GFX compute ring: allocate ring buffer, configure MQD, activate via MES
- SDMA ring: allocate ring buffer, configure SDMA MQD, activate
- Submit NOP packet, check fence completion (RELEASE_MEM writes value → CPU reads)
- Reuse existing `PM4PacketBuilder` from `commands/pm4.py` and `SDMAPacketBuilder` from `commands/sdma.py`
- **Milestone: NOP submission + fence signal works**

### 3.7: Compute Dispatch (Weeks 9-12)

- Allocate code memory in VRAM, map to GPU page tables
- Upload compiled kernel (fill_kernel for gfx1201)
- SET_SH_REG + DISPATCH_DIRECT + RELEASE_MEM (reuse existing `program.py` PM4 builder)
- Wait for completion, read back results
- **Milestone: First compute kernel dispatch on Windows**

---

## Risks

Incorporates findings from both the full MCDM plan and the MCDM-specific concerns:

| Risk | Severity | Mitigation |
|------|----------|------------|
| PSP refuses firmware from stub driver | **HIGH** | Test mailbox handshake early (Phase 3.5). Fallback: reuse VBIOS-initialized PSP state. Being MCDM (vs plain KMDF) may help — it's a "real" WDDM driver. |
| TDR interferes with GPU bring-up | **MEDIUM-HIGH** | Set `TdrLevel=0` during development. Implement real GPU reset in `DxgkDdiResetFromTimeout` for production. |
| DDI validation failures during install | **MEDIUM** | Start from Microsoft's `coskmd` sample. Test on Windows Insider builds (better MCDM support). |
| IOMMU blocks GPU DMA | **MEDIUM** | Use WDF DMA framework. Bus addresses are auto-translated. |
| D3DKMTEscape overhead or limitations | **MEDIUM** | If escape path is too slow for register-heavy operations, batch reads/writes. Fallback: use `DxgkDdiDispatchIoRequest` for a direct DeviceIoControl path. |
| BAR mapping via MDL in escape context | **MEDIUM** | Test early — `MmMapLockedPagesSpecifyCache` from `DxgkDdiEscape` context may have restrictions. Fallback: map during StartDevice, return pre-mapped VA. |
| MES queue activation complexity | **MEDIUM** | Study `mes_v12_0.c`. Check if legacy direct queue programming works as fallback on RDNA4. |
| GPU page table bugs → system crash | **MEDIUM** | Start with identity mapping. Read-back verification. |
| BAR2 too small for full VRAM access | **MEDIUM** | Check if ReBAR is enabled. Desktop RDNA4 cards typically support ReBAR. |
| Upstream amdgpu divergence | LOW | Pin to a specific kernel version for firmware and register headers. |

---

## Sizing

| Component | Lines | Duration |
|-----------|-------|----------|
| Phase 0: Platform fixes | ~100 Python | **Done** |
| Phase 1: MCDM kernel driver | ~8-15K C | 8-12 weeks |
| Phase 2: Windows backend | ~2.5-4K Python | 4-8 weeks |
| Phase 3: GPU bring-up | ~3-5K Python | 6-12 weeks |
| **Total** | **~13.5-24K** | **18-32 weeks, 1-2 people** |

vs. full MCDM plan: 190-290K lines, 12-17 months, 3-4 people.

---

## Scope Exclusions

| Feature | Reason |
|---------|--------|
| Display / KMS / DCN | Compute-only. Adrenaline handles display on another GPU. |
| VCN / JPEG | Video not needed for compute. |
| SVM | Requires HMM/mmu_notifier. Extreme difficulty. |
| Multi-GPU | Single GPU first. |
| WDDM scheduling (real) | SubmitCommandVirtual is a stub. Real work through Escape. |
| ROCm stack compatibility | No libhsakmt port. Driver-level evaluation only. |
| D3D12 UMD | Not needed — direct hardware access through Escape. |
| Full production HLK | Test-signing only initially. HLK path exists but deferred. |

---

## Alternatives Considered

1. **Full MCDM kernel driver** (the original plan) — 190-290K lines, 12-17 months. Deferred, not rejected. This lightweight evaluation de-risks the hardest parts (PSP, GPU init, IOMMU) before committing to the full effort.

2. **Plain KMDF driver (Class=System)** — The original lightweight plan. ~2-4K lines kernel, simpler, but no production signing path, no TDR recovery, no multi-process safety, no WDDM ecosystem integration, and the kernel code is throwaway if we graduate to MCDM. The ~6-12 week overhead for MCDM DDI stubs is worth it for the stepping-stone value.

3. **Use AMD HIP SDK as backend** — Wrap AMD's proprietary `amdhip64.dll` from Python. Would work immediately but defeats the goal of open-source ROCm on Windows.

4. **WSL2 GPU passthrough** — Run the existing Linux driver inside WSL2. Doesn't work for unclaimed devices (requires host WDDM driver). Not a native Windows solution.

5. **Generic PCI driver (WinIO/InpOut)** — No DMA allocation or IOMMU configuration. Insufficient for GPU communication beyond register reads.

6. **D3DKMT/DxCore APIs without custom driver** — Use Windows' WDDM infrastructure from Python (undocumented D3DKMT APIs). Requires AMD's Adrenaline driver installed. Not what we're building.

---

## Immediate Next Steps

1. ~~Check out `users/powderluv/userspace-driver` branch~~ **Done**
2. ~~Fix `ioctl/helpers.py` and `buffer.py` for Windows importability~~ **Done**
3. ~~Run unit tests on Windows~~ **Done** (109 passed, 121 skipped)
4. Set up Windows Driver Kit (WDK) development environment
5. Clone/examine Microsoft's `coskmd` sample as a skeleton
6. Create `kernel_driver/` directory structure with INF file
7. Implement v0.1: DDI stubs + DriverEntry → driver installs
