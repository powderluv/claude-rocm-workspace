# WDDM 3D Graphics Support Plan

## Status: Research / Design Phase

## Overview

This plan covers adding 3D graphics rendering support to our WDDM display miniport
driver for AMD GPUs on Windows. Today the driver provides POST framebuffer display
and a compute escape channel. The goal is to evolve it into a driver that can
actually render 3D graphics through the D3D API stack.

## What We Have Today

### Current Driver Architecture (WDDM 1.3 Display Miniport)

**Source:** `/home/nod/github/TheRock/userspace_driver/wddm_driver/`

The driver registers as `DXGKDDI_INTERFACE_VERSION_WDDM1_3` and implements:

| Component | File | Status |
|-----------|------|--------|
| PnP lifecycle | `ddi_device.c` | Working: AddDevice, StartDevice, StopDevice, RemoveDevice |
| PCI BAR enumeration | `ddi_device.c` | Working: MMIO, VRAM, Doorbell BAR classification |
| POST display acquisition | `ddi_device.c` | Working: DxgkCbAcquirePostDisplayOwnership |
| VidPn (display paths) | `ddi_vidpn.c` | Working: single source/target, POST framebuffer mode only |
| Child devices | `ddi_display.c` | Working: 1 always-connected output (or disconnected for headless) |
| Memory segments | `ddi_query.c` | Minimal: single 256MB aperture segment, CPU-visible only |
| Allocations | `ddi_memory.c` | Working: standard allocs (shared primary, shadow, staging) for DWM |
| Present | `ddi_present.c` | Software only: CPU blit to POST framebuffer |
| Scheduling | `ddi_scheduling.c` | Fake: immediate completion via DxgkCbNotifyInterrupt |
| Patch | `ddi_scheduling.c` | Minimal: resolves physical addresses for CPU blits |
| Escape | `ddi_escape.c` | Working: register R/W, BAR map, DMA alloc, VRAM map, MSI, IH ring |
| Render | `ddi_stubs.c` | **Stub: returns STATUS_NOT_SUPPORTED** |
| Context | `ddi_device.c` | Minimal: allocates context struct, DMA buffer = 4KB system memory |
| Interrupts | `ddi_interrupt.c` | Working: IH ring processing for compute |
| TDR | `ddi_tdr.c` | Stub: no-ops |

**Key limitations for 3D:**

1. **No GPU command submission.** SubmitCommand does CPU blits, never talks to the GPU.
   DMA buffers contain software blit descriptors, not PM4/GFX ring commands.
2. **No real memory segments.** Single 256MB aperture, no VRAM segment, no GPU VA.
3. **Render DDI is a stub.** The UMD path (DxgkDdiRender) returns STATUS_NOT_SUPPORTED.
4. **No User Mode Driver (UMD).** DWM uses WARP (Microsoft's software rasterizer) as the
   UMD. There is no hardware-accelerated UMD DLL.
5. **No GPU page tables.** SetRootPageTable, BuildPagingBuffer are no-ops.
6. **No real context/engine management.** One node, type ENGINE_TYPE_3D, but no actual
   GPU engine initialization.
7. **Single VidPn mode.** Only the POST framebuffer resolution is advertised.

### What Works Today

- Driver loads, DWM gets a desktop via WARP + software Present (CPU blit to POST FB)
- Compute via escape channel (Python userspace driver programs GPU directly)
- BSOD display (SystemDisplayWrite)
- Basic display at UEFI-selected resolution

## WDDM 3D Rendering Architecture

### The WDDM Graphics Stack

```
Application
    |
D3D11/D3D12 Runtime (d3d11.dll / d3d12.dll)  -- Microsoft-provided
    |
User Mode Driver (UMD)                        -- vendor-provided DLL
    |  (D3DKMTSubmitCommand, D3DKMTRender, D3DKMTPresent, etc.)
    v
dxgkrnl.sys (DirectX Graphics Kernel)         -- Microsoft-provided
    |
Kernel Mode Driver (KMD) = Display Miniport   -- our driver
    |
GPU Hardware
```

### Two Distinct Rendering Models in WDDM

**WDDM 1.x (D3D9/D3D11 "Render" model):**
- UMD builds command buffers in user mode
- Calls D3DKMTRender to submit
- dxgkrnl calls KMD's DxgkDdiRender to translate/validate
- KMD's DxgkDdiPatch resolves GPU addresses
- KMD's DxgkDdiSubmitCommand sends to GPU

**WDDM 2.0+ (D3D12 "Hardware Queue" model):**
- UMD builds GPU-native command buffers directly
- Calls D3DKMTSubmitCommandToHwQueue
- dxgkrnl calls KMD's DxgkDdiSubmitCommandVirtual
- No Render/Patch step -- GPU command buffers are submitted as-is
- GPU uses virtual addresses, managed by GPU page tables

### KMD DDIs Needed for 3D (by category)

**GPU Engine Management:**
- `DxgkDdiCreateContext` -- need real GPU context (not just a struct)
- `DxgkDdiDestroyContext`
- `DxgkDdiGetNodeMetadata` -- already reports ENGINE_TYPE_3D

**Command Submission (WDDM 1.x path):**
- `DxgkDdiRender` -- translate UMD command buffer to GPU commands
- `DxgkDdiPatch` -- resolve GPU addresses in DMA buffer
- `DxgkDdiSubmitCommand` -- submit DMA buffer to GPU ring
- `DxgkDdiPreemptCommand` -- preempt running work
- `DxgkDdiQueryCurrentFence` -- report actual GPU fence value

**Command Submission (WDDM 2.0+ / D3D12 path):**
- `DxgkDdiSubmitCommandVirtual` -- submit GPU-VA command buffer
- `DxgkDdiCreateHwQueue` / `DxgkDdiDestroyHwQueue` (WDDM 2.5+)

**Memory Management:**
- `DxgkDdiCreateAllocation` -- proper VRAM/GTT allocations with tiling
- `DxgkDdiBuildPagingBuffer` -- FILL, TRANSFER, MAP_APERTURE_SEGMENT
- `DxgkDdiSetRootPageTable` -- program GPUVM page tables
- QUERYSEGMENT -- expose real VRAM + system memory segments

**Present/Display:**
- `DxgkDdiPresent` -- hardware flip / BLT via GPU
- `DxgkDdiSetVidPnSourceAddress` -- flip to new primary address
- Mode enumeration (multi-resolution support via real display controller init)

**Interrupt/Fence:**
- `DxgkDdiInterruptRoutine` -- handle GPU completion interrupts
- `DxgkDdiDpcRoutine` -- process fence completions
- Real fence tracking (not returning 0xFFFFFFFF)

## User Mode Driver (UMD) Strategy

This is the most critical architectural decision. The UMD is the component that
translates D3D API calls into GPU command buffers.

### Option A: Port Mesa radeonsi as a WDDM UMD

Mesa's `radeonsi` gallium driver is the full OpenGL implementation for AMD GCN/RDNA
GPUs on Linux. It generates PM4 command streams, manages shader compilation via
`amd/compiler` (ACO) or `amd/llvm`, and handles all state management.

**What exists in our Mesa fork:**
- `/home/nod/github/TheRock/third-party/sysdeps/linux/amd-mesa/mesa-fork/src/gallium/drivers/radeonsi/` -- full radeonsi driver
- `/home/nod/github/TheRock/third-party/sysdeps/linux/amd-mesa/mesa-fork/src/amd/compiler/` -- ACO shader compiler
- `/home/nod/github/TheRock/third-party/sysdeps/linux/amd-mesa/mesa-fork/src/amd/common/` -- shared AMD GPU utilities
- `/home/nod/github/TheRock/third-party/sysdeps/linux/amd-mesa/mesa-fork/src/gallium/frontends/wgl/` -- Windows OpenGL frontend already exists
- `/home/nod/github/TheRock/third-party/sysdeps/linux/amd-mesa/mesa-fork/src/gallium/winsys/amdgpu/` -- Linux winsys (needs WDDM equivalent)

**Porting path:**
1. Write a WDDM winsys backend (replaces `winsys/amdgpu/drm/` which uses Linux DRM)
2. The winsys would call D3DKMT APIs to allocate memory, submit commands
3. Use the existing WGL frontend for OpenGL
4. Build as a DLL that dxgkrnl loads as the UMD

**Pros:** Mature, feature-complete, handles all AMD GPU generations
**Cons:** Massive porting effort; Linux DRM assumptions throughout; WDDM UMD is a
D3D-specific interface, not OpenGL -- so this only gets us OpenGL, not D3D.

### Option B: Use Mesa's D3D12 Gallium Driver (Reverse Direction)

Mesa already has a `gallium/drivers/d3d12/` driver that implements Gallium
(OpenGL) on top of D3D12. This is used for WSL (Windows Subsystem for Linux)
to provide OpenGL via D3D12.

This doesn't help us directly -- it's an *OpenGL-on-D3D12* layer, and we need
a *D3D-on-AMD-hardware* layer.

### Option C: Write a Minimal D3D11 UMD from Scratch

A WDDM User Mode Driver for D3D11 implements the `D3D11_1DDI_*` interfaces.
This is a DLL (`amdgpu_umd.dll`) that dxgkrnl loads. It translates D3D11 state
and draw calls into GPU command buffers (PM4 packets for AMD).

**Architecture:**
```
d3d11.dll
    |
amdgpu_umd.dll (our UMD)
    |  generates PM4 command buffers
    |  calls D3DKMTRender or D3DKMTSubmitCommand
    v
dxgkrnl -> our KMD -> GPU ring buffer
```

**Pros:** Focused scope; can leverage AMD register headers and PM4 formats from
Mesa/radeonsi as reference without porting all of Mesa
**Cons:** Enormous effort to write a D3D11 UMD; need to implement all state tracking,
resource management, shader compilation

### Option D: Write a D3D12 UMD (WDDM 2.0+)

D3D12 UMDs are simpler than D3D11 because D3D12 is lower-level. The UMD generates
GPU command buffers directly, and the D3D12 runtime does less state management.

**Architecture:**
```
d3d12.dll
    |
amdgpu_d3d12_umd.dll (our UMD)
    |  generates PM4 command buffers directly
    |  calls D3DKMTSubmitCommandToHwQueue
    v
dxgkrnl -> our KMD -> GPU hardware queue
```

**Pros:** Closer to how modern AMD drivers work; simpler UMD interface;
GPU command buffers are submitted as-is (no Render/Patch)
**Cons:** Requires WDDM 2.0+ KMD features (GPU VA, hardware queues);
still need shader compilation (DXIL to ISA)

### Option E: Leverage AMD's PAL (Platform Abstraction Layer)

AMD's PAL exists in our codebase at:
`/home/nod/github/TheRock/rocm-systems/shared/amdgpu-windows-interop/pal/`

PAL is AMD's internal hardware abstraction that sits below their proprietary
Windows driver. It handles command buffer generation, memory management, and
GPU programming. However, PAL is designed to work with AMD's proprietary KMD,
not our custom WDDM driver.

**Pros:** Production-quality AMD GPU programming layer; handles all GPU generations
**Cons:** Tightly coupled to AMD's proprietary driver infrastructure; headers-only
in our tree (no full PAL source); would need extensive adaptation

### Option F: Vulkan via RADV + VKD3D-Proton

Use Mesa's RADV (Vulkan for AMD) as the primary API, then use VKD3D-Proton to
translate D3D12 to Vulkan. This is similar to how Steam Deck works on Linux.

**Architecture:**
```
D3D12 Application
    |
VKD3D-Proton (D3D12 -> Vulkan translation, user mode)
    |
RADV Vulkan driver (Mesa)
    |
WDDM winsys backend
    |
our KMD -> GPU
```

**Pros:** RADV is mature; VKD3D-Proton handles D3D12 translation; avoids writing
a D3D UMD from scratch
**Cons:** Performance overhead of translation layer; RADV assumes Linux DRM
for winsys (same porting problem as radeonsi); Vulkan ICD on Windows normally
goes through the Vulkan runtime, not through WDDM UMD path

### Recommended Strategy

**Phase 1-2: Option C (minimal D3D11 UMD) focusing on getting Present working
through actual GPU hardware.**

**Phase 3+: Option D (D3D12 UMD) for modern API support, using Mesa's ACO compiler
for shader compilation and AMD register definitions from Mesa's `amd/` tree.**

The D3D12 path is strategically more important because:
- It's the path ROCm compute on Windows already partially exercises (WDDM 2.0+
  hardware queues are also used for compute dispatch)
- The extended escape structs in `rocm-systems` already define context and
  queue creation for our driver
- D3D12's lower-level model maps more directly to AMD GPU hardware

## Shader Compilation

Shader compilation is the pipeline: HLSL -> DXBC/DXIL -> GPU ISA.

**What the D3D runtime provides:**
- HLSL compilation to DXBC (D3D11) or DXIL (D3D12) is done by Microsoft's
  compiler (dxc.exe / d3dcompiler_47.dll) at application build time or runtime
- The UMD receives DXBC or DXIL bytecode

**What the UMD must do:**
- D3D11 UMD: receives DXBC, must compile to AMD ISA
- D3D12 UMD: receives DXIL, must compile to AMD ISA

**Available compilers in our tree:**
- `mesa-fork/src/amd/compiler/` (ACO) -- AMD's community compiler, generates
  ISA from NIR (Mesa's IR). Would need a DXBC/DXIL to NIR frontend.
- `mesa-fork/src/amd/llvm/` -- LLVM-based backend for AMD ISA
- `mesa-fork/src/microsoft/compiler/` -- Microsoft's DXIL compiler utilities
- `mesa-fork/src/microsoft/spirv_to_dxil/` -- SPIR-V to DXIL (reverse direction)

**Compilation pipeline for D3D11 UMD:**
```
DXBC -> NIR (mesa/src/microsoft/compiler or custom translator) -> ACO -> ISA
```

**Compilation pipeline for D3D12 UMD:**
```
DXIL -> NIR (mesa has DXIL-to-NIR in the D3D12 gallium driver) -> ACO -> ISA
```

Mesa already has the DXIL-to-NIR path working (used by the D3D12 gallium driver
for WSL). The NIR-to-ISA path via ACO is production-quality. The main work is
integrating these into a Windows DLL and connecting them to the WDDM UMD
interface.

## Phased Implementation Plan

### Phase 0: KMD Foundation -- Real Memory Segments (2-3 weeks)

Before any 3D work, the KMD needs proper memory management.

**Changes to `ddi_query.c`:**
- Report 2 segments: VRAM (local, segment 1) and System Memory (aperture, segment 2)
- VRAM segment: base address = VRAM BAR physical address, size = detected VRAM size
- Set proper segment flags: `PopulatedFromSystemMemory`, `CpuVisible`, etc.

**Changes to `ddi_memory.c`:**
- `BuildPagingBuffer`: implement TRANSFER (VRAM <-> system memory), FILL, MAP_APERTURE_SEGMENT
- `CreateAllocation`: support VRAM-preferred allocations with proper segment sets

**Changes needed:**
- Map the VRAM BAR (already classified as `VramBarIndex`)
- Implement basic VRAM allocation tracking
- Handle paging operations (at minimum CPU-side copies for now)

**Validation:** DWM allocates primaries in VRAM, software Present still works

### Phase 1: GPU-Accelerated Present (3-4 weeks)

Get the GPU actually executing commands, starting with 2D blits for Present.

**KMD changes:**
- Initialize the SDMA (System DMA) engine during StartDevice
  - Program SDMA ring buffer in VRAM
  - Set up SDMA doorbell
  - Configure SDMA wptr/rptr
- `SubmitCommand`: write SDMA copy packets to ring instead of CPU blit
- `InterruptRoutine`: handle SDMA completion interrupts
- Real fence tracking: increment fence on SDMA completion
- `PreemptCommand`: SDMA preemption support

**SDMA packet format** (from AMD register headers / Linux amdgpu driver):
```
SDMA_PKT_COPY_LINEAR:
  - Source address (GPU VA or physical)
  - Destination address
  - Copy size
  - Pitch/slice for 2D
```

**No UMD needed yet** -- DWM still uses WARP, but Present goes through GPU SDMA
instead of CPU memcpy.

**Validation:** Desktop renders at full speed; GPU blits visible in GPU profiler

### Phase 2: GFX Engine Initialization (3-4 weeks)

Initialize the 3D/compute engine (GFX ring) so it can execute draw commands.

**KMD changes:**
- Initialize GFX ring buffer (CP - Command Processor)
  - Program ring buffer base/size
  - Set up doorbell
  - Initialize microcode (MEC for compute, PFP/ME/CE for graphics)
- Initialize the GPU memory controller (GMC)
  - Set up GPU page tables (GPUVM)
  - Implement `SetRootPageTable` to program VM context
  - Implement `BuildPagingBuffer` for GPU-side page table updates
- Report 2 engine nodes: GFX (node 0) + SDMA (node 1)
- `GetNodeMetadata`: node 0 = ENGINE_TYPE_3D, node 1 = ENGINE_TYPE_COPY

**This is the hardest KMD phase** because it requires:
- Reading AMD hardware programming guides for GFX ring init
- Microcode loading (PM4 format, CP initialization packets)
- GPU virtual memory setup (page tables, VMID allocation)
- Reference: Linux `amdgpu` kernel driver (`amdgpu_gfx.c`, `gfx_v11_0.c`, `gmc_v11_0.c`)

**Validation:** GPU can execute NOP packets on GFX ring; GPU page tables work

### Phase 3: Minimal D3D11 UMD -- Triangle (8-12 weeks)

Write a minimal User Mode Driver DLL that can render a triangle.

**UMD DLL structure (`amdgpu_umd.dll`):**
```
OpenAdapter10_2()         -- entry point, return DDI table
CreateDevice()            -- create per-device state
CalcPrivateResourceSize() -- allocation size calculations
CreateResource()          -- allocate textures, buffers
SetRenderTargets()        -- bind render targets
VsSetShader()             -- bind vertex shader
PsSetShader()             -- bind pixel shader
Draw()                    -- emit draw packets to command buffer
Flush()                   -- submit command buffer
Present()                 -- present to screen
```

**PM4 command buffer generation:**
- State setup packets: SET_CONTEXT_REG, SET_SH_REG
- Draw packets: DRAW_INDEX_AUTO, DRAW_INDEX_2
- Reference: Mesa's `radeonsi/si_state_draw.c`, `radeonsi/si_gfx_cs.c`

**Shader compilation (minimal):**
- Hardcode a vertex shader and pixel shader for initial testing
- Then integrate ACO compiler for DXBC -> NIR -> ISA

**INF file update:**
- Register `amdgpu_umd.dll` as the D3D11 User Mode Driver
- Set `UserModeDriverName` and `UserModeDriverNameWow` registry values

**KMD changes:**
- `DxgkDdiRender`: validate and copy UMD command buffer to DMA buffer
- `DxgkDdiPatch`: resolve GPU virtual addresses in PM4 packets
- `DxgkDdiSubmitCommand`: write DMA buffer to GFX ring
- `DxgkDdiPresent`: GPU-accelerated flip or BLT

**Validation:** D3D11 triangle renders on screen

### Phase 4: D3D11 Feature Completeness (12-20 weeks)

Fill out the D3D11 UMD to pass WHCK (Windows Hardware Certification Kit) basics.

- Texture sampling, filtering, mipmaps
- Multiple render targets
- Depth/stencil
- Blend state
- Rasterizer state
- Compute shaders
- Stream output
- Queries (occlusion, timestamp, pipeline stats)
- Full DXBC shader compilation via ACO

### Phase 5: D3D12 UMD (12-16 weeks)

Write a D3D12 User Mode Driver using WDDM 2.0+ hardware queue model.

**Requires upgrading KMD to WDDM 2.0+:**
- Change version to `DXGKDDI_INTERFACE_VERSION_WDDM2_0`
- Implement hardware queues: `DxgkDdiCreateHwQueue`, `DxgkDdiDestroyHwQueue`
- GPU-VA memory model: `DxgkDdiCreateAllocation` with GPU virtual addresses
- `DxgkDdiSubmitCommandVirtual` with real GPU command execution

**D3D12 UMD is simpler than D3D11:**
- No state tracking -- D3D12 is explicit
- Command lists map directly to GPU command buffers
- Pipeline state objects = pre-compiled GPU state

**Shader compilation:**
- DXIL -> NIR (using Mesa's `microsoft/compiler/` DXIL reader)
- NIR -> ISA (using ACO)

### Phase 6: Vulkan ICD (Optional, 8-12 weeks)

Port RADV as a Windows Vulkan ICD, using the same KMD/winsys infrastructure.

**This would provide:**
- Native Vulkan support
- VKD3D-Proton compatibility (D3D12 games via Vulkan)
- Complete graphics stack

## Display Output (Beyond POST Framebuffer)

Currently the driver uses the UEFI GOP framebuffer at whatever resolution the
firmware set. Real display support requires:

1. **Display Controller (DCN) initialization** -- AMD's Display Core Next handles
   mode setting, CRTC programming, and encoder configuration
2. **Real mode enumeration** -- read EDID from connected monitors, enumerate
   supported modes
3. **Hardware flip** -- set display scan-out address to point at rendered framebuffer
4. **Multi-monitor support** -- multiple VidPn sources/targets

AMD's DCN code is in the Linux `amdgpu` driver under `drivers/gpu/drm/amd/display/`.
This is a large (~200K lines) display management subsystem. Porting it is a separate
major effort that could be done in parallel with 3D rendering work.

**Incremental approach:**
- Phase 0-2: Keep POST framebuffer, present via SDMA copy to POST FB
- Phase 3+: Initialize DCN for the primary display, add flip support
- Later: Full multi-monitor, hotplug, adaptive sync

## Linux Reference Points

### amdgpu Kernel Driver

The Linux `amdgpu` kernel driver handles hardware initialization that maps to our
KMD work:

| Linux amdgpu | Our WDDM KMD equivalent |
|-------------|------------------------|
| `amdgpu_device_init()` | `AmdGpuStartDevice()` |
| `gfx_v11_0_hw_init()` | Phase 2 GFX engine init |
| `sdma_v6_0_hw_init()` | Phase 1 SDMA engine init |
| `gmc_v11_0_hw_init()` | Phase 2 GPU memory controller init |
| `amdgpu_ring_write()` | `AmdGpuSubmitCommand()` ring writes |
| `amdgpu_vm_init()` | `AmdGpuSetRootPageTable()` |
| `amdgpu_bo_create()` | `AmdGpuCreateAllocation()` |

### Mesa radeonsi

Mesa's radeonsi maps to UMD work:

| Mesa radeonsi | Our WDDM UMD equivalent |
|--------------|------------------------|
| `si_create_context()` | `CreateDevice()` |
| `si_draw()` | `Draw()` / PM4 generation |
| `si_emit_draw_packets()` | PM4 draw command encoding |
| `si_shader_create()` + ACO | Shader compilation |
| `radeon_winsys` | D3DKMT winsys backend |
| `amdgpu_cs_submit()` | `D3DKMTSubmitCommand()` |

## Existing Code to Leverage

### In our codebase:
- **Extended escape structs** (`rocm-systems/.../our_escape_structs.h`) already define
  GPU info queries, context creation, and queue creation for our driver
- **AMD register definitions** from Mesa's `amd/registers/` directory
- **ACO compiler** for shader compilation
- **DXIL to NIR** translator in Mesa's `microsoft/compiler/`
- **PAL headers** show AMD's internal GPU abstraction patterns

### External references:
- **Linux amdgpu driver** for hardware init sequences (GPL, cannot copy directly
  but can use as reference for register programming)
- **Mesa radeonsi/RADV** for PM4 packet formats and GPU state management (MIT, can
  use more directly)
- **Microsoft WDDM documentation** for DDI contracts and validation requirements

## Alternatives Considered

### A. Skip D3D11, go straight to D3D12

Rejected because D3D12 requires WDDM 2.0+ which needs more KMD infrastructure
(GPU VA, hardware queues). D3D11 lets us validate the basic command submission
pipeline with simpler KMD requirements. Also, DWM and many Windows applications
still use D3D11.

### B. Use WARP (software rendering) permanently and focus only on compute

This is what we have today. Rejected as a long-term strategy because:
- No GPU acceleration means poor desktop/application performance
- Cannot run D3D games or GPU-accelerated applications
- Limits the usefulness of passing a GPU to a Windows VM

### C. Port the entire Linux amdgpu + Mesa stack to Windows

Full port of the Linux graphics stack. Rejected because:
- The Linux amdgpu kernel driver is GPL, creating licensing issues for a
  Windows kernel driver
- The DRM/KMS model doesn't map well to WDDM
- Would essentially be reimplementing what AMD's proprietary Windows driver does

### D. Use AMD's open-source Vulkan driver (AMDVLK) instead of RADV

AMDVLK uses PAL internally, which is partially in our tree. However:
- AMDVLK's build system and dependencies are deeply tied to AMD's internal
  infrastructure
- PAL assumes AMD's proprietary KMD
- Less accessible than Mesa for porting

### E. Focus on Vulkan-only (no D3D UMD) and use DXVK for D3D

Use RADV for Vulkan, DXVK for D3D9/D3D11, VKD3D-Proton for D3D12. This works
well on Linux (Steam Deck model) but:
- On Windows, the D3D runtime expects a WDDM UMD, not a Vulkan translation layer
- DXVK/VKD3D-Proton work by hooking D3D calls, which is fragile on native Windows
- Windows applications and DWM use D3D11 natively and expect a real UMD

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| GPU hang during engine init | High | Medium | Implement robust TDR; test in VM first |
| BSOD from KMD bugs | High | High | Develop in VM with GPU passthrough; registry-based diagnostics |
| Shader compiler bugs | Medium | High | Start with hardcoded shaders; use Mesa's test suite |
| WDDM DDI contract violations | Medium | Medium | Reference WHCK tests; study MS sample drivers |
| VRAM corruption | Medium | High | Conservative memory management; validation layers |
| Performance too slow for DWM | Low | Medium | SDMA for present; async compute for rendering |
| GPU microcode compatibility | Medium | High | Test per GPU generation; start with RDNA3/4 only |

## Effort Estimates

| Phase | Description | Effort | Dependencies |
|-------|------------|--------|-------------|
| Phase 0 | Real memory segments | 2-3 weeks | None |
| Phase 1 | GPU-accelerated Present (SDMA) | 3-4 weeks | Phase 0 |
| Phase 2 | GFX engine initialization | 3-4 weeks | Phase 0 |
| Phase 3 | Minimal D3D11 UMD (triangle) | 8-12 weeks | Phase 1, 2 |
| Phase 4 | D3D11 feature completeness | 12-20 weeks | Phase 3 |
| Phase 5 | D3D12 UMD | 12-16 weeks | Phase 2, Phase 3 (partial) |
| Phase 6 | Vulkan ICD | 8-12 weeks | Phase 2 |
| Display | Real display controller init | 6-10 weeks | Phase 0 (parallel) |

**Critical path:** Phase 0 -> Phase 1+2 (parallel) -> Phase 3

**Total to first triangle:** ~16-23 weeks from start
**Total to usable D3D11:** ~40-63 weeks from start

These are rough estimates assuming full-time effort by someone familiar with both
WDDM and AMD GPU architecture. The hardware initialization phases (1 and 2) are
the highest uncertainty because they depend on undocumented hardware programming
sequences.

## Open Questions

1. **Which GPU generation to target first?** RDNA4 (GFX1201, our RX 9070 XT) is
   newest but has less public documentation. RDNA3 (GFX11) or RDNA2 (GFX10) have
   more mature Mesa support.

2. **Can we reuse our compute escape infrastructure?** The extended escapes already
   handle context creation and command submission. Can the 3D path share this
   mechanism, or does it need the formal WDDM DDI path?

3. **WDDM version target?** We currently register as WDDM 1.3. Should we upgrade
   to WDDM 2.0+ immediately (for GPU VA / D3D12 support) or stay at 1.3 for
   initial D3D11 work?

4. **Testing strategy?** GPU passthrough VM is good for development, but some GPU
   features may not work perfectly through IOMMU. Do we need bare-metal testing?

5. **Shader compiler build integration?** ACO is written in C++ and has Mesa build
   system dependencies. How do we build it as a Windows DLL for the UMD?
