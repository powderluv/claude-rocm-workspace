# Plan: Lightweight GPU Kernel + Userspace ROCm Stack

**Goal:** Get HIP tests passing on both Windows and Linux using lightweight kernel drivers that only handle privileged operations, with all GPU complexity moved to userspace.

**Philosophy:** Keep the kernel thin, make userspace fat. The kernel handles what requires privilege (PCI BAR mapping, GPU page tables, interrupt forwarding). Everything else — IP discovery, queue management, MQD setup, packet construction, doorbell writes — lives in userspace. This is the same architecture on both platforms.

**Key Insight:** The ROCm stack's thunk layer (`libhsakmt`) already has platform-specific backends: Linux uses KFD ioctls (`src/*.c`), Windows uses D3DKMT + wkmi.lib (`src/dxg/`). We add new backends for both platforms that talk to our lightweight kernel modules instead. On Windows, we also support AMD's official driver via the existing wkmi path.

## Architecture

```
                    HIP Application (hipMalloc, hipLaunchKernel, hipMemcpy)
                            │
                    HIP Runtime (hipamd/src/*.cpp)
                            │
                    CLR / rocclr (amd::Device, amd::CommandQueue)
                            │
                    ROCR-Runtime (hsa_* API, 75+ functions)
                            │
                    libhsakmt (hsaKmt* API, 101 functions)
                            │
           ┌────────────────┼────────────────┐
           │                │                │
    ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐
    │ Linux:      │  │ Windows:    │  │ Windows:    │
    │ LightKfd    │  │ OurEscape   │  │ Wkmi        │
    │ Backend     │  │ Backend     │  │ Backend     │
    │ (new)       │  │ (new)       │  │ (existing)  │
    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
           │                │                │
    ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐
    │ amdgpu_lite │  │ amdgpu_wddm│  │ AMD official│
    │ .ko         │  │ .sys        │  │ driver      │
    │ (new)       │  │ (existing)  │  │             │
    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
           │                │                │
        GPU HW           GPU HW           GPU HW
```

### What Lives Where

| Responsibility | Traditional KFD | Our Lightweight Approach |
|---|---|---|
| IP discovery table parsing | Kernel (amdgpu) | **Userspace** |
| GPU topology enumeration | Kernel (sysfs) | **Userspace** (direct BAR read) |
| MQD construction | Kernel (KFD) | **Userspace** |
| Queue ring buffer management | Userspace (already) | Userspace (same) |
| PM4/SDMA packet construction | Userspace (already) | Userspace (same) |
| Doorbell writes | Userspace (mmap, already) | Userspace (mmap, same) |
| GPU page table programming | Kernel (amdgpu) | **Kernel** (must stay) |
| VRAM allocation | Kernel (TTM/amdgpu) | **Kernel** (must stay) |
| PCI BAR mapping to userspace | Kernel (amdgpu) | **Kernel** (must stay) |
| Interrupt forwarding | Kernel (amdgpu) | **Kernel** (must stay) |
| IOMMU setup | Kernel (amdgpu) | **Kernel** (must stay) |
| Firmware loading (via PSP ring) | Kernel (amdgpu) | **Userspace** (register writes + DMA buffer) |
| Power management | Kernel (amdgpu) | Kernel (can stub initially) |
| Debug/profiling | Kernel (KFD) | Defer to Phase 3 |

## The wkmi Interface (What We Abstract)

The existing thunk calls `Wkmi::` namespace functions at well-defined points. All calls are in 3 files: `device.cpp`, `gpu_memory.cpp`, `queue.cpp`.

### Calling Pattern

Every driver-specific operation follows this pattern:
```cpp
// 1. Get size of private data buffer
int priv_size = Wkmi::GetXxxPrivDataSize();
// 2. Fill the buffer with driver-specific data
Wkmi::FillinXxxPrivData(priv_data, ...params...);
// 3. Pass to kernel via D3DKMT
D3DKMT_XXX args = { .pPrivateDriverData = priv_data, .PrivateDriverDataSize = priv_size, ... };
D3DKMTXxx(&args);
```

### Full Function Inventory (~30 functions)

| Category | wkmi Function | Used In | Pattern |
|---|---|---|---|
| **Device Init** | `ParseAdapterInfo(adapter, &info)` | device.cpp constructor | Populates DeviceInfo struct once |
| **Device Query** | `QueryAdapterSupported(device_id)` | device.cpp WDDMCreateDevices | Returns bool |
| | `EngineOrdinal(engine, &info)` | device.cpp CreateContext | Maps scheduler → engine |
| | `GetHwsEnabled(engine, &info)` | device.cpp CreateContext/Queue | Returns bool |
| | `ShouldDisableGpuTimeout(engine, &info)` | device.cpp CreateContext/Queue | Returns bool |
| **Context** | `GetContextPrivDataSize()` | device.cpp CreateContext | GetSize+Fill |
| | `FillinContextPrivData(priv, fw_state, sched_id)` | device.cpp CreateContext | |
| **HW Queue** | `GetHwQueuePrivDataSize()` | device.cpp CreateHwQueue | GetSize+Fill |
| | `FillinHwQueuePrivData(priv, fw, prio, aql, ...)` | device.cpp CreateHwQueue | |
| **SW Submit** | `GetSubmitPrivDataSize()` | device.cpp Submit* | GetSize+Fill |
| | `FillinSubmitPrivData(priv, queue, addr, size, hw)` | device.cpp Submit* | |
| **AQL Submit** | `GetAqlSubmitPrivDataSize()` | device.cpp SubmitToAqlQueue | GetSize+Fill (Windows only) |
| | `FillinAqlSubmitPrivData(priv, doorbell_val)` | device.cpp SubmitToAqlQueue | |
| **Memory** | `GetAllocPrivDataSize(&drv_sz, &alloc_sz)` | gpu_memory.cpp | GetSize+Fill |
| | `FillinAllocPrivDrvData(priv, alloc_sz)` | gpu_memory.cpp | |
| | `SetAllocationInfo(priv, size, domain, addr, ...)` | gpu_memory.cpp | Per-allocation |
| | `QueueEngine2EngineFlag(engine)` | gpu_memory.cpp | Enum conversion |
| | `GetMemoryAllocationSize(priv)` | gpu_memory.cpp | Query from priv data |
| | `GetProxyResourceInfoSize()` | gpu_memory.cpp | Size query |
| **Events** | `GetRegisterEventPrivDataSize()` | device.cpp RegisterEvent | GetSize+Fill |
| | `FillinRegisterEventPrivData(priv, handle, id)` | device.cpp RegisterEvent | |
| | `GetRegisterEventMailbox(priv)` | device.cpp RegisterEvent | Extract VA post-escape |
| | `GetUnregisterEventPrivDataSize()` | device.cpp UnregisterEvent | GetSize+Fill |
| | `FillinUnregisterEventPrivData(priv, handle)` | device.cpp UnregisterEvent | |
| **Power** | `GetPowerOptPrivDataSize()` | device.cpp SetPowerOpt | GetSize+Fill |
| | `FillinPowerOptPrivData(priv, restore)` | device.cpp SetPowerOpt | |
| **CU Mask** | `GetCuMaskPrivDataSize()` | device.cpp | GetSize+Fill |
| | `FillinCuMaskPrivData(priv, doorbell, count, mask)` | device.cpp | |

### DeviceInfo Struct (populated by ParseAdapterInfo)

Key members used throughout the thunk:
- `major`, `minor`, `stepping` — GFX IP version
- `is_dgpu`, `device_id`, `family` — Device identification
- `compute_schedid`, `sdma_schedid` — Scheduler IDs
- `hwsInfo` — Hardware scheduling capability
- `local_visible_heap_size`, `local_invisible_heap_size`, `non_local_heap_size` — Memory sizes
- `state_shadowing_by_cpfw` — Firmware state shadowing
- `kmd_version` — Kernel mode driver version

## IDriverBackend Interface Design

```cpp
// driver_backend.h — Abstract interface replacing Wkmi:: namespace
class IDriverBackend {
public:
    virtual ~IDriverBackend() = default;

    // Device initialization
    virtual NTSTATUS ParseAdapterInfo(D3DKMT_HANDLE adapter, DeviceInfo* info) = 0;
    virtual bool QueryAdapterSupported(unsigned int device_id) = 0;
    virtual int EngineOrdinal(int engine, const DeviceInfo* info) = 0;
    virtual bool GetHwsEnabled(int engine, const DeviceInfo* info) = 0;
    virtual bool ShouldDisableGpuTimeout(int engine, const DeviceInfo* info) = 0;

    // Context creation
    virtual int GetContextPrivDataSize() = 0;
    virtual void FillinContextPrivData(void* priv, bool fw_state, uint32_t sched_id) = 0;

    // HW Queue
    virtual int GetHwQueuePrivDataSize() = 0;
    virtual void FillinHwQueuePrivData(void* priv, bool fw_state, SchedLevel prio,
                                       bool aql, uint64_t cmd_addr, uint32_t cmd_size,
                                       uint64_t wptr, uint64_t rptr, D3DKMT_HANDLE resource) = 0;

    // Submission
    virtual int GetSubmitPrivDataSize() = 0;
    virtual void FillinSubmitPrivData(void* priv, D3DKMT_HANDLE queue,
                                      uint64_t addr, uint64_t size, bool is_hw) = 0;
    virtual int GetAqlSubmitPrivDataSize() = 0;
    virtual void FillinAqlSubmitPrivData(void* priv, uint64_t doorbell_value) = 0;

    // Memory
    virtual void GetAllocPrivDataSize(int* drv_size, int* alloc_size) = 0;
    virtual void FillinAllocPrivDrvData(void* priv, int alloc_size) = 0;
    virtual void SetAllocationInfo(void* priv, uint64_t size, AllocDomain domain,
                                    uint64_t addr, uint32_t mem_flags,
                                    uint32_t engine_flag, const DeviceInfo& info) = 0;
    virtual uint32_t QueueEngine2EngineFlag(uint32_t engine) = 0;
    virtual uint64_t GetMemoryAllocationSize(const void* priv) = 0;
    virtual int GetProxyResourceInfoSize() = 0;

    // Events
    virtual int GetRegisterEventPrivDataSize() = 0;
    virtual void FillinRegisterEventPrivData(void* priv, uint64_t handle, uint32_t id) = 0;
    virtual uint64_t GetRegisterEventMailbox(void* priv) = 0;
    virtual int GetUnregisterEventPrivDataSize() = 0;
    virtual void FillinUnregisterEventPrivData(void* priv, uint64_t handle) = 0;

    // Power/CU mask (stubs OK for our backend)
    virtual int GetPowerOptPrivDataSize() = 0;
    virtual void FillinPowerOptPrivData(void* priv, bool restore) = 0;
    virtual int GetCuMaskPrivDataSize() = 0;
    virtual void FillinCuMaskPrivData(void* priv, uint32_t doorbell,
                                       uint32_t count, const uint32_t* mask) = 0;
};
```

### Backend Implementations

**WkmiBackend** — Thin wrapper around existing `wkmi.lib`:
```cpp
class WkmiBackend : public IDriverBackend {
    NTSTATUS ParseAdapterInfo(D3DKMT_HANDLE adapter, DeviceInfo* info) override {
        return Wkmi::ParseAdapterInfo(adapter, info);
    }
    // ... each method delegates to Wkmi:: namespace
};
```

**OurEscapeBackend** — Uses our D3DKMT escape protocol:
```cpp
class OurEscapeBackend : public IDriverBackend {
    D3DKMT_HANDLE adapter_;
    D3DKMT_HANDLE device_;

    NTSTATUS ParseAdapterInfo(D3DKMT_HANDLE adapter, DeviceInfo* info) override {
        // Send ESCAPE_GET_GPU_INFO, populate DeviceInfo from response
        AMDGPU_ESCAPE_GPU_INFO_DATA gpu_info = {};
        gpu_info.Header.Command = AMDGPU_ESCAPE_GET_GPU_INFO;
        EscapeCall(adapter, &gpu_info, sizeof(gpu_info));
        info->major = gpu_info.GfxIpMajor;
        info->minor = gpu_info.GfxIpMinor;
        // ... map all fields
    }

    int GetContextPrivDataSize() override {
        return sizeof(AMDGPU_ESCAPE_CREATE_CONTEXT);
    }

    void FillinContextPrivData(void* priv, bool fw, uint32_t sched_id) override {
        auto* ctx = static_cast<AMDGPU_ESCAPE_CREATE_CONTEXT*>(priv);
        ctx->Header.Command = AMDGPU_ESCAPE_CREATE_CONTEXT;
        ctx->FwManagedState = fw;
        ctx->SchedId = sched_id;
    }
    // ... each method uses our escape structs
};
```

### Runtime Selection

```cpp
// In WDDMDevice constructor or factory
std::unique_ptr<IDriverBackend> CreateBackend(D3DKMT_HANDLE adapter) {
    // Try our escape — if it succeeds, we're on our driver
    AMDGPU_ESCAPE_HEADER probe = { .Command = AMDGPU_ESCAPE_GET_INFO };
    if (TryEscape(adapter, &probe, sizeof(probe))) {
        return std::make_unique<OurEscapeBackend>(adapter);
    }
    // Fall back to AMD's wkmi.lib (must be present at link time or loaded dynamically)
    return std::make_unique<WkmiBackend>();
}
```

## Phase 1: Minimum Viable — Simple HIP Kernel Dispatch

**Target:** `hipMalloc` → `hipMemcpy(H2D)` → `hipLaunchKernel(vectorAdd)` → `hipMemcpy(D2H)` → verify result.

### 1.1 Kernel Driver: New Escape Commands

Add to `amdgpu_wddm.h`:

```c
AMDGPU_ESCAPE_GET_GPU_INFO     = 0x0070,  // IP discovery + GPU capabilities
AMDGPU_ESCAPE_CREATE_CONTEXT   = 0x0080,  // Create compute context
AMDGPU_ESCAPE_DESTROY_CONTEXT  = 0x0081,
AMDGPU_ESCAPE_CREATE_QUEUE     = 0x0090,  // Create compute/SDMA queue (MQD + doorbell)
AMDGPU_ESCAPE_DESTROY_QUEUE    = 0x0091,
AMDGPU_ESCAPE_MAP_DOORBELL     = 0x0092,  // Map doorbell page to userspace
AMDGPU_ESCAPE_GET_CLOCK        = 0x00A0,  // GPU clock counter
```

**GET_GPU_INFO response** (the big one):
- GFX IP version (major.minor.stepping)
- Shader engines, arrays/engine, CUs/array, SIMDs/CU
- Wavefront size (32 or 64)
- LDS size, VGPR/SGPR count
- Max engine/memory clocks
- SDMA engine count
- GPU counter frequency
- VRAM size, visible VRAM size
- Private/shared aperture bases
- Firmware versions (MEC, SDMA, RLC)

**CREATE_QUEUE request/response:**
- Input: queue type (compute/SDMA), ring buffer GPU VA, ring size, read/write ptr GPU VA, EOP buffer GPU VA
- Kernel allocates MQD, programs CP firmware, assigns doorbell offset
- Output: doorbell offset, MQD GPU VA, queue ID

**Implementation note:** The kernel driver must parse the IP discovery table (currently only done by our Python driver). Port the table parsing from `ip_discovery.py` to C in `ddi_device.c`.

### 1.2 Kernel Driver: Fix Memory Segment Reporting

The D3DKMT memory path requires correct segment reporting. Current `ddi_query.c` reports a single 256MB aperture segment. This must be:

```c
// Segment 1: VRAM (local, not CPU-visible by default)
pSeg[0].Flags.CpuVisible = 0;
pSeg[0].Size = pAdapter->VramSize;

// Segment 2: Visible VRAM (CPU-visible portion via BAR)
pSeg[1].Flags.CpuVisible = 1;
pSeg[1].Size = pAdapter->VisibleVramSize;
pSeg[1].CpuTranslatedAddress = pAdapter->Bars[pAdapter->VramBarIndex].PhysicalAddress;

// Segment 3: System memory (GTT-like)
pSeg[2].Flags.Aperture = 1;
pSeg[2].Flags.CpuVisible = 1;
pSeg[2].Size = 256ULL * 1024 * 1024;
```

Must also implement `DxgkDdiBuildPagingBuffer` properly for page table operations.

### 1.3 Modify the Thunk for Dual-Backend Support

**Approach:** Modify the existing thunk in-place (not fork) to use `IDriverBackend` instead of direct `Wkmi::` calls. This keeps us closer to upstream and makes merging easier.

**Changes to existing files (mechanical — replace `Wkmi::Xxx(...)` with `backend_->Xxx(...)`):**
- `device.cpp` — ~15 call sites, all in WDDMDevice methods
- `gpu_memory.cpp` — ~6 call sites, memory allocation private data
- `queue.cpp` — ~2 call sites, queue engine constants

The WDDMDevice class already holds a `Wkmi::DeviceInfo device_info_` member and all Wkmi calls go through it. Adding an `IDriverBackend*` member and replacing `Wkmi::Xxx` with `backend_->Xxx` is a straightforward refactor.

**New files:**
- `driver_backend.h` — `IDriverBackend` abstract interface
- `wkmi_backend.h/cpp` — Wrapper that delegates to `Wkmi::` namespace (links wkmi.lib)
- `our_escape_backend.h/cpp` — Implementation using our D3DKMT escape protocol
- `backend_factory.cpp` — Runtime detection and instantiation

### 1.4 hsaKmt Functions: Phase 1 Classification

**Real implementations needed (21 functions):**

| Function | Notes |
|---|---|
| `hsaKmtOpenKFD` | D3DKMT adapter enumeration + our GET_GPU_INFO |
| `hsaKmtCloseKFD` | Cleanup |
| `hsaKmtGetVersion` | Return hardcoded version |
| `hsaKmtAcquireSystemProperties` | Build from GET_GPU_INFO |
| `hsaKmtReleaseSystemProperties` | Free topology data |
| `hsaKmtGetNodeProperties` | Populate HsaNodeProperties from GET_GPU_INFO |
| `hsaKmtGetNodeMemoryProperties` | VRAM + system memory heaps |
| `hsaKmtSetMemoryPolicy` | No-op (coarse grain default) |
| `hsaKmtAllocMemory` | D3DKMT path (existing code) |
| `hsaKmtFreeMemory` | D3DKMT path (existing code) |
| `hsaKmtMapMemoryToGPU` | D3DKMT path (existing code) |
| `hsaKmtUnmapMemoryToGPU` | D3DKMT path (existing code) |
| `hsaKmtRegisterMemory` | D3DKMT path for userptr |
| `hsaKmtDeregisterMemory` | D3DKMT path |
| `hsaKmtCreateQueue` | Our CREATE_QUEUE escape |
| `hsaKmtDestroyQueue` | Our DESTROY_QUEUE escape |
| `hsaKmtCreateEvent` | Windows event + our REGISTER_EVENT |
| `hsaKmtDestroyEvent` | Cleanup |
| `hsaKmtWaitOnEvent` | WaitForSingleObject |
| `hsaKmtGetClockCounters` | QPC for CPU, register read for GPU |
| `hsaKmtQueueRingDoorbell` | Write to mapped doorbell MMIO |

**Stubs (return HSAKMT_STATUS_SUCCESS, ~80 functions):**
All debug, perf counter, SVM, IPC, and advanced features.

### 1.5 Build System

**Modify existing libhsakmt CMake build** (preferred now that we're not forking):
- Add CMake option: `-DHSAKMT_DRIVER_BACKEND=auto|wkmi|our_escape`
  - `auto` (default): Compile both backends, select at runtime via escape probe
  - `wkmi`: Link wkmi.lib only (original behavior)
  - `our_escape`: Our backend only (no wkmi.lib dependency)
- When `auto` or `wkmi`: link against `wkmi.lib` (existing dependency)
- When `auto` or `our_escape`: compile our escape backend (adds our escape header)
- Always link: `gdi32.lib` (D3DKMT APIs)
- Output: `libhsakmt.dll` (drop-in replacement, works with either driver)

**Build configurations:**
- Native Windows build with VS 2022 (primary development)
- Cross-compilation from Linux with clang-cl + Windows SDK headers (CI)

**Linking strategy for `auto` mode:**
- `wkmi.lib` can be delay-loaded (`/DELAYLOAD:wkmi.dll`) so the DLL works even without it
- Or: `WkmiBackend` loaded via `LoadLibrary` + `GetProcAddress` at runtime
- Simplest: just link wkmi.lib statically (it's a static lib anyway)

### 1.6 Implementation Order

```
Week 1:    IDriverBackend interface + WkmiBackend wrapper
           Refactor existing thunk: Wkmi::Xxx → backend_->Xxx (~23 call sites)
           Verify: thunk still works identically with AMD's driver via WkmiBackend
Week 2:    OurEscapeBackend skeleton (stubs returning reasonable defaults)
           Backend factory with runtime driver detection (escape probe)
           Verify: thunk compiles and links with both backends
Week 3-4:  Kernel driver: IP discovery table parsing in C
           Kernel driver: GET_GPU_INFO escape command
           OurEscapeBackend::ParseAdapterInfo implementation
Week 5:    Kernel driver: Fix memory segment reporting
           Kernel driver: BuildPagingBuffer implementation
           OurEscapeBackend: memory allocation private data
Week 6:    Thunk: topology functions (shared, backend-agnostic)
           Test: alloc GPU memory from C++, read/write via CPU mapping
Week 7-8:  Kernel driver: CREATE_QUEUE escape (MQD setup, doorbell)
           Kernel driver: MAP_DOORBELL escape
           OurEscapeBackend: context + queue + submission
Week 9:    Thunk: event system (both backends)
           ROCR-Runtime: build for Windows, link against dual-backend thunk
           Test: hsa_init(), hsa_iterate_agents() on both drivers
Week 10:   CLR/HIP: build for Windows
           Test: hipGetDeviceCount(), hipGetDeviceProperties()
Week 11-12: Integration: hipMalloc → hipMemcpy → hipLaunchKernel
           First HIP kernel running on our driver!
           Verify same test passes on AMD's driver via WkmiBackend
```

## Phase 1L: Linux — Lightweight Kernel Module + KfdBypassBackend

**Target:** Same HIP test passing on Linux, but using our lightweight `amdgpu_lite.ko` instead of the full amdgpu/KFD stack.

### 1L.1 Lightweight Kernel Module: `amdgpu_lite.ko`

A minimal Linux kernel module (~2000 lines vs amdgpu's ~500K lines). Uses the standard Linux PCI driver framework.

**What it does (privileged operations only):**

```c
// ~8 ioctls, compared to KFD's 32+
#define AMDGPU_LITE_IOC_GET_INFO        _IOR('L', 1, ...)   // PCI IDs, BAR info
#define AMDGPU_LITE_IOC_MAP_BAR         _IOWR('L', 2, ...)  // mmap BAR to userspace
#define AMDGPU_LITE_IOC_ALLOC_VRAM      _IOWR('L', 3, ...)  // Allocate VRAM pages
#define AMDGPU_LITE_IOC_FREE_VRAM       _IOW('L', 4, ...)   // Free VRAM pages
#define AMDGPU_LITE_IOC_MAP_GPU         _IOW('L', 5, ...)   // Install GPU page table entries
#define AMDGPU_LITE_IOC_UNMAP_GPU       _IOW('L', 6, ...)   // Remove GPU page table entries
#define AMDGPU_LITE_IOC_ALLOC_GTT       _IOWR('L', 7, ...)  // Allocate GTT (system mem visible to GPU)
#define AMDGPU_LITE_IOC_SETUP_IRQ       _IOW('L', 8, ...)   // Register eventfd for GPU interrupts
```

**What it does NOT do (moved to userspace):**
- IP discovery table parsing
- Firmware loading (done via PSP ring from userspace — see below)
- MQD construction
- Queue scheduling / MES interaction
- Topology enumeration
- PM4 packet construction
- Any GFX-version-specific logic

**Kernel module structure:**

```
amdgpu_lite/
├── amdgpu_lite.h          # Shared ioctl definitions (userspace + kernel)
├── main.c                 # PCI probe/remove, char device registration
├── pci_setup.c            # BAR mapping, IOMMU configuration
├── memory.c               # VRAM/GTT allocation, GPU page table programming
├── irq.c                  # MSI-X interrupt setup, eventfd forwarding
└── Makefile / Kbuild
```

**Key design decisions:**

1. **GPU page tables are programmed by the kernel** — This is the hard requirement. The GPU's VMID page directory base is a privileged register, and page table entries control DMA access. Must be kernel-resident for security.

2. **BAR mapping via mmap** — The module exposes BARs via `mmap()` on the char device fd. Userspace gets direct MMIO access for registers and doorbells, no ioctl per read/write.

3. **Interrupts via eventfd** — Userspace registers an eventfd per interrupt source (GPU completion, fault). The kernel ISR writes to the eventfd; userspace polls/blocks with standard `epoll`/`poll`.

4. **Firmware loading via ioctl** — Userspace reads firmware files from `/lib/firmware/amdgpu/`, passes blobs to kernel. Kernel writes them to GPU SRAM at the right addresses. This keeps firmware-version-specific logic in userspace.

5. **No AMDGPU/DRM dependency** — This is a standalone PCI driver, not a DRM subdriver. It registers its own char device (`/dev/amdgpu_lite0`). It can coexist with amdgpu if bound to different PCI devices.

6. **Firmware loading is userspace** — The Python driver already loads firmware (MEC, SDMA, RLC, MES) entirely through register writes and DMA buffers, communicating with the GPU's PSP (Platform Security Processor) via a ring buffer in system memory. The kernel module just needs to provide BAR mmap and DMA allocation. This is identical to how it works on Windows — our WDDM driver provides `READ_REG32`/`WRITE_REG32`/`ALLOC_DMA` escapes, and the Python driver does all firmware loading through them.

### 1L.2 GPU Initialization Sequence

The kernel module brings the GPU to a state where userspace can take over. Then userspace handles all GPU-specific initialization including firmware loading.

**Kernel module init (privileged):**
```
1. PCI enable + BAR mapping
2. Set up IOMMU for DMA
3. Enable MSI-X interrupts
4. Program VMID 0 page directory base
5. Expose BARs and DMA allocation via char device
```

**Userspace init (via mmap'd BARs + DMA buffers):**
```
1. IP discovery — read from mmap'd BAR, parse GPU capabilities
2. NBIO init — doorbell aperture, framebuffer access
3. GMC init — memory controller, system aperture, GART page table
4. PSP init + firmware loading:
   a. Check PSP SOS is alive (already running from VBIOS POST)
   b. Allocate DMA buffer for PSP ring (via kernel ioctl)
   c. Create PSP ring, submit firmware blobs (MEC, SDMA, RLC, MES)
   d. All firmware loading happens via register writes to mmap'd MMIO BAR
5. IH init — interrupt handler ring
6. Compute ring init — MQD, HQD registers, doorbell
7. Self-test — NOP + fence verification
```

This is exactly what the Python driver already does on Windows (`compute_dispatch.py`, `psp_init.py`). The C++ port reuses the same sequence. The kernel module only needs to provide BAR access and DMA-capable memory allocation — it never touches firmware itself.

**Same on both platforms:** The Windows WDDM driver also doesn't load firmware. It provides register access and DMA allocation escapes. The Python driver (and future C++ thunk) does all firmware loading through those primitives.

### 1L.3 KfdBypassBackend for libhsakmt

A new backend for the Linux thunk that talks to `amdgpu_lite.ko` instead of KFD:

```cpp
// New files in libhsakmt/src/
class KfdBypassBackend {
    int fd_;  // /dev/amdgpu_lite0 file descriptor
    void* mmio_bar_;  // mmap'd MMIO BAR
    void* doorbell_bar_;  // mmap'd doorbell BAR
    void* vram_bar_;  // mmap'd VRAM BAR (if large BAR)

public:
    // Device init — read IP discovery from mmap'd BAR, parse in userspace
    HSAKMT_STATUS OpenDevice();

    // Memory — thin wrappers around amdgpu_lite ioctls
    HSAKMT_STATUS AllocMemory(HSAuint32 node, HSAuint64 size,
                              HsaMemFlags flags, void** addr);
    HSAKMT_STATUS FreeMemory(void* addr);
    HSAKMT_STATUS MapMemoryToGPU(void* addr, HSAuint64 size, HSAuint64* gpu_va);

    // Queues — MQD built in userspace, doorbell write via mmap
    HSAKMT_STATUS CreateQueue(HSAuint32 node, HSA_QUEUE_TYPE type,
                              HSAuint32 ring_size, ...);

    // Events — eventfd from kernel, polling from userspace
    HSAKMT_STATUS CreateEvent(HsaEventDescriptor* desc, ...);
    HSAKMT_STATUS WaitOnEvent(HSAuint64 event, HSAuint32 timeout_ms);
};
```

**How it maps to hsaKmt functions:**

| hsaKmt Function | KFD path (current) | KfdBypass path (new) |
|---|---|---|
| `hsaKmtOpenKFD` | open `/dev/kfd` | open `/dev/amdgpu_lite0`, mmap BARs |
| `hsaKmtAcquireSystemProperties` | read sysfs topology | parse IP discovery from mmap'd BAR |
| `hsaKmtGetNodeProperties` | read sysfs | built from IP discovery data |
| `hsaKmtAllocMemory` | `AMDKFD_IOC_ALLOC_MEMORY_OF_GPU` | `AMDGPU_LITE_IOC_ALLOC_VRAM/GTT` |
| `hsaKmtMapMemoryToGPU` | `AMDKFD_IOC_MAP_MEMORY_TO_GPU` | `AMDGPU_LITE_IOC_MAP_GPU` |
| `hsaKmtCreateQueue` | `AMDKFD_IOC_CREATE_QUEUE` | Build MQD in userspace, write to mmap'd BAR |
| `hsaKmtCreateEvent` | `AMDKFD_IOC_CREATE_EVENT` | `AMDGPU_LITE_IOC_SETUP_IRQ` + eventfd |
| `hsaKmtWaitOnEvent` | `AMDKFD_IOC_WAIT_EVENTS` | `poll()` on eventfd |
| `hsaKmtGetClockCounters` | `AMDKFD_IOC_GET_CLOCK_COUNTERS` | Direct MMIO register read |
| `hsaKmtQueueRingDoorbell` | mmap'd doorbell write | mmap'd doorbell write (identical) |

### 1L.4 Build System

```cmake
# In libhsakmt/CMakeLists.txt
option(HSAKMT_USE_LITE_BACKEND "Use lightweight amdgpu_lite backend instead of KFD" OFF)

if(NOT WIN32)
    if(HSAKMT_USE_LITE_BACKEND)
        # Compile KfdBypassBackend sources
        list(APPEND SRCS src/lite/kfd_bypass_backend.cpp
                         src/lite/ip_discovery.cpp
                         src/lite/mqd_builder.cpp
                         src/lite/topology_builder.cpp)
    else()
        # Original KFD ioctl sources (default)
        list(APPEND SRCS src/libhsakmt.c src/openclose.c ...)
    endif()
endif()
```

**Kernel module build:**
```
cd userspace_driver/amdgpu_lite
make -C /lib/modules/$(uname -r)/build M=$(pwd) modules
sudo insmod amdgpu_lite.ko
```

### 1L.5 Shared Userspace Code (Linux ↔ Windows)

Much of the "heavy lifting" code is platform-independent and can be shared:

| Component | Shared? | Notes |
|---|---|---|
| IP discovery table parser | **Yes** | Same binary format on all AMD GPUs |
| PSP ring + firmware loader | **Yes** | Same PSP protocol on all platforms |
| GMC / NBIO init | **Yes** | Register writes via mmap'd BAR |
| MQD builder (per GFX version) | **Yes** | Same register layouts |
| PM4 packet construction | **Yes** | Already shared in Python driver |
| SDMA packet construction | **Yes** | Already shared in Python driver |
| Topology builder | **Yes** | Builds HSA topology from IP discovery data |
| Queue ring buffer logic | **Yes** | Wrap-around, doorbell encoding |
| Memory layout / VA manager | Partial | VA ranges differ per platform |

This shared code lives in a common library linked by both the `KfdBypassBackend` (Linux) and `OurEscapeBackend` (Windows):

```
libhsakmt/src/
├── common/                 # Shared userspace GPU logic (both platforms)
│   ├── ip_discovery.cpp    # Parse IP discovery table from raw bytes
│   ├── psp_firmware.cpp    # PSP ring creation + firmware blob loading
│   ├── gpu_init.cpp        # NBIO, GMC, IH initialization sequences
│   ├── mqd_builder.cpp     # Build MQD for GFX9/10/11/12
│   ├── topology_builder.cpp # Build HSA topology from parsed IP data
│   └── packet_builder.cpp  # PM4 + SDMA packet formats
├── lite/                   # Linux lightweight backend
│   ├── kfd_bypass_backend.cpp
│   └── lite_ioctl.h
├── dxg/                    # Windows backends (existing + our new one)
│   ├── wddm/
│   ├── our_escape_backend.cpp
│   └── driver_backend.h
└── *.c                     # Original KFD backend (untouched)
```

### 1L.6 Linux Implementation Order

```
Week 1-2:  Kernel module skeleton: PCI probe, BAR mapping, char device
           Test: load module, open /dev/amdgpu_lite0, mmap MMIO BAR
           Test: read GPU registers from userspace via mmap
Week 3:    Shared IP discovery parser (C++ port from Python)
           Test: parse IP discovery table from mmap'd BAR on real hardware
Week 4:    Shared PSP/firmware loader (C++ port from psp_init.py)
           Shared GPU init (NBIO, GMC — C++ port from compute_dispatch.py)
           Test: full GPU bring-up from userspace on real hardware
Week 5:    Kernel module: VRAM/GTT allocation + GPU page table programming
           Kernel module: interrupt setup (eventfd)
           Test: alloc VRAM, map to GPU VA, write via CPU, read back
Week 6:    Shared MQD builder (C++ port from Python)
           KfdBypassBackend skeleton: OpenDevice, topology, memory
           Test: hsaKmtOpenKFD → hsaKmtAllocMemory via amdgpu_lite
Week 7-8:  KfdBypassBackend: queue creation (MQD in userspace)
           Test: create queue, submit PM4 NOP packet, verify completion
Week 9:    KfdBypassBackend: events (eventfd-based)
           ROCR-Runtime: link against lite-backend thunk
           Test: hsa_init(), hsa_iterate_agents()
Week 10:   CLR/HIP: build with lite-backend thunk
           Test: hipGetDeviceCount(), hipMalloc, hipMemcpy
Week 11-12: Integration: hipLaunchKernel(vectorAdd)
           First HIP kernel on amdgpu_lite!
```

### 1L.7 amdgpu_lite vs VFIO

Could we use VFIO instead of writing a custom kernel module?

**VFIO provides:** PCI BAR mmap, DMA mapping (IOMMU), interrupt forwarding via eventfd, PCI config space access. This covers ~80% of what `amdgpu_lite.ko` needs.

**VFIO does NOT provide:** GPU-specific page table programming. VFIO handles IOMMU (host→device DMA translation) but AMD GPUs have their own MMU (GPUVM) with separate page tables. We'd still need custom code to program GPUVM page tables, which means either:
- A userspace library that programs GPU page tables via mmap'd MMIO registers (possible but tricky — page table walks via MMIO are slow)
- A helper kernel module on top of VFIO

**Decision:** Write `amdgpu_lite.ko` directly. It's simpler than layering on VFIO, and we get exactly the ioctl interface we want. VFIO is better suited for passthrough scenarios where the full driver runs inside a VM.

## Phase 2: HIP Test Suite Coverage

### Additional Functions to Implement

| Function | Why Needed |
|---|---|
| `hsaKmtCreateQueue` (SDMA type) | hipMemcpyAsync uses SDMA engine |
| `hsaKmtGetNodeCacheProperties` | Some HIP tests query cache topology |
| `hsaKmtQueryPointerInfo` | CLR memory management needs pointer metadata |
| `hsaKmtAvailableMemory` | Memory allocation tests |
| `hsaKmtSetQueueCUMask` | CU masking tests |
| `hsaKmtWaitOnMultipleEvents` | Complex sync patterns |
| `hsaKmtShareMemory` | IPC tests (if needed) |

### Additional Escape Commands

```c
AMDGPU_ESCAPE_CREATE_SDMA_QUEUE = 0x0093,  // SDMA engine queue
AMDGPU_ESCAPE_QUERY_FIRMWARE    = 0x00C0,  // Firmware version queries
AMDGPU_ESCAPE_SET_CU_MASK       = 0x00D0,  // CU masking per queue
```

### Proper WDDM Submission Path (Track B)

Replace direct HW access with standard WDDM path:
- Implement `DxgkDdiSubmitCommand` properly
- Implement `DxgkDdiCreateContext` with our own private data
- Define our own submit private data structures
- Benefits: proper GPU scheduling, preemption, TDR support

## Phase 3: Performance Optimization

- HW scheduling (HWS) queues for lower latency
- AQL doorbell passthrough (avoid kernel round-trip)
- Large BAR / ReBAR support
- SDMA queue optimization for overlapped compute + transfer
- GPU page fault handling for SVM

## Alternatives Considered

### Windows
1. **Fork the thunk and hardcode our escapes** — Rejected in favor of dual-backend. Forking creates a maintenance burden and prevents working with AMD's official driver.
2. **Reverse-engineer wkmi.lib** — Rejected. Proprietary, version-dependent, legal risk. We use wkmi.lib as-is via the `WkmiBackend` wrapper.
3. **Compile-time backend selection only** — Rejected. Runtime selection lets one DLL work with both drivers, simplifying deployment and testing.

### Linux
4. **Use VFIO instead of custom kernel module** — Rejected. VFIO handles IOMMU but not GPU-specific page tables (GPUVM). We'd need a helper module anyway, so writing `amdgpu_lite.ko` directly is simpler.
5. **Keep using KFD as-is** — Rejected for our purposes. KFD is tightly coupled to the full amdgpu driver (~500K lines). Our goal is a minimal kernel that's easy to understand, modify, and port.
6. **Pure userspace via /dev/mem** — Rejected for production. Works for prototyping (our Python driver uses it) but requires root, bypasses IOMMU, and can't safely program GPU page tables from userspace.

### Shared
7. **Rewrite thunk from scratch** — Rejected. The existing code works; adding backends is less effort than rewriting.
8. **Skip thunk, implement at ROCR level** — Rejected. ROCR has hundreds of hsaKmt callers; replacing them all is too invasive.
9. **Direct PM4 injection without MQD/doorbell** — Rejected. GPU firmware expects proper queue setup.

## Risk Assessment

### Windows Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Memory segment reporting wrong | All D3DKMT memory ops fail | Test early (Week 3), iterate with debugger |
| wkmi.lib DeviceInfo layout changes | WkmiBackend breaks on newer AMD driver | Pin to known wkmi.lib version, update wrapper when needed |
| D3DKMT API incompatibility | Standard APIs behave differently than expected | Test each API in isolation before integrating |

### Linux Risks
| Risk | Impact | Mitigation |
|---|---|---|
| GPU page table format wrong | GPU faults, system hang | Reference amdgpu source for PTE format, test with single-page mappings first |
| PSP firmware loading from userspace fails | GPU doesn't initialize | Port exact sequence from Python psp_init.py (already works on real hardware) |
| amdgpu_lite conflicts with amdgpu | Can't bind to device | Use different PCI IDs or unbind amdgpu first; document mutual exclusion |
| GPUVM setup differs per GFX version | Memory mapping fails on some GPUs | Start with GFX1201 (known hardware), add versions incrementally |

### Shared Risks
| Risk | Impact | Mitigation |
|---|---|---|
| MQD/queue setup wrong | GPU hangs | Reference Python driver (already works), use known-good register values |
| ROCR-Runtime build broken on platform | Can't proceed to HIP | Check AMD's build support first, fix build issues incrementally |
| GPU firmware interaction issues | Queue creation fails | Start with GFX1201 (known hardware), use same PM4 as Python driver |
| IP discovery parser bugs | Wrong GPU capabilities reported | Validate against Python parser output on same hardware |

## Key File Locations

### Our Code
| Component | Path |
|---|---|
| Windows WDDM kernel driver | `userspace_driver/wddm_driver/` |
| Windows escape header (shared) | `userspace_driver/wddm_driver/amdgpu_wddm.h` |
| Linux lightweight kernel module | `userspace_driver/amdgpu_lite/` (new) |
| Linux lite ioctl header (shared) | `userspace_driver/amdgpu_lite/amdgpu_lite.h` (new) |
| Python driver (reference impl) | `userspace_driver/python/amd_gpu_driver/` |
| Python IP discovery | `userspace_driver/python/amd_gpu_driver/backends/windows/ip_discovery.py` |
| Python KFD backend | `userspace_driver/python/amd_gpu_driver/backends/kfd/` |

### ROCm Upstream (to modify)
| Component | Path |
|---|---|
| libhsakmt build system | `rocm-systems/projects/rocr-runtime/libhsakmt/CMakeLists.txt` |
| Linux KFD thunk (reference) | `rocm-systems/projects/rocr-runtime/libhsakmt/src/*.c` |
| Windows WDDM thunk (reference) | `rocm-systems/projects/rocr-runtime/libhsakmt/src/dxg/wddm/` |
| wkmi.h (Windows interface) | `rocm-systems/projects/rocr-runtime/libhsakmt/src/dxg/wkmi/wkmi.h` |
| wkmi.lib (prebuilt) | `rocm-systems/projects/rocr-runtime/libhsakmt/src/dxg/wkmi/win/rel/wkmi.lib` |
| WDDMDevice header | `rocm-systems/projects/rocr-runtime/libhsakmt/include/impl/wddm/device.h` |
| hsaKmt API header | `rocm-systems/projects/rocr-runtime/libhsakmt/include/hsakmt/hsakmt.h` |
| hsaKmt types | `rocm-systems/projects/rocr-runtime/libhsakmt/include/hsakmt/hsakmttypes.h` |
| KFD ioctl definitions | `rocm-systems/projects/rocr-runtime/libhsakmt/include/hsakmt/linux/kfd_ioctl.h` |
| ROCR thunk loader | `rocm-systems/projects/rocr-runtime/runtime/hsa-runtime/core/runtime/thunk_loader.cpp` |
| HIP tests | `rocm-systems/projects/hip-tests/catch/unit/` |
| D3DKMT headers | `rocm-systems/projects/rocdbgapi/third_party/libdxg/include/` |
