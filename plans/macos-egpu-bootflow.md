# macOS eGPU Boot Flow — RX 9070 XT / gfx1201

**Branch:** `users/powderluv/egpu-build`
**Last updated:** 2026-04-20
**Partners:** `macos-egpu-bootflow.svg` (architecture diagram)

This document captures the bring-up flow we've implemented to drive an
AMD Radeon RX 9070 XT (Navi 48 / gfx1201) via a Razer Core X V2
Thunderbolt enclosure, from the user-space Python driver down through
the ROCmGPU DriverKit extension (DEXT) to the hardware. It reflects
the state of the tree as of today: everything up through SMU's SOC
power-domain DPM works and the `DisallowGfxOff` mailbox is live;
enabling the GFX power domain is blocked on a deeper SOC-level init
step (see §4).

## 1. Hardware stack

- **Host:** Apple Silicon (M-series) Mac, macOS.
- **Link:** Thunderbolt 4, running PCIe 3.0 x4 with **ReBAR off**
  (256 MB VRAM BAR window, not the full 32 GB).
- **Enclosure:** Razer Core X V2.
- **GPU:** RX 9070 XT (device 0x7551 rev 0xC0, non-kicker), gfx1201
  (GC 12.0.1), SMU 14.0.3, PSP 14.0.3, MMHUB 4.1.0, NBIO 6.3.1,
  SDMA 7.0.1, MP0/MP1 14.0.3.

## 2. Software stack

- **ROCmGPU.dext** (DriverKit system extension)
  — signed for this Mac, matches on the AMD PCI class.
  - Exposes escape selectors: `open`, `close`, `get_info`, `cfg_rd/wr`,
    `mmio_rd/wr`, `map_bar`, `alloc_dma`, `free_dma`, `enable_msi`, `reset`.
  - DMA buffers are mapped through the Apple DART IOMMU; returned
    bus addresses land in the `0x8000_0000+` window.
- **User-space Python driver**
  (`userspace_driver/python/amd_gpu_driver/backends/macos`):
  - `iokit_client.py` — IOKit glue to the DEXT.
  - `psp_bootloader.py` — PSP SOS / component bootloader chain.
  - `psp_ring.py` — PSP KM ring create/destroy.
  - `psp_cmd.py` — PSP GFX ring command submission + PSP `fw_type` enum.
  - `smu.py` — SMU mailbox + feature-enable orchestrator.
  - `gmc.py` — MMHUB init skeleton (not on the hot path yet).
  - `gfx_firmware.py` — PSP-based IMU loader (kept for gfx11 parity).
  - `gfx_autoload.py` — gfx12 backdoor autoload + IMU boot plumbing.
- **ROCR-Runtime** on macOS (`rocm-systems` submodule)
  — `MacOsDriver` backend attached to `core::Driver`; `MacGpuAgent`
  surfaces the card to `hsa_iterate_agents`. Not the focus of this
  doc but motivates the bring-up work.

## 3. Boot flow — phase-by-phase

Status legend (matches SVG colors):
- ✅ works end-to-end, lives in the module tree
- ⚠️ partial / discovery-script only, not yet productized
- ❌ blocked — identified root cause, fix requires more infrastructure

### Phase 1 — DEXT attach + PCI enable ✅

`IOKitClient.open()` finds the service published by ROCmGPU.dext and
opens an `IOUserClient`. The DEXT has already enabled PCI memory +
bus mastering by the time user-space connects.

### Phase 2 — BAR mapping ✅

- **BAR0 (VRAM, 256 MB prefetchable):** `map_bar(0)` returns a CPU
  pointer to a 256 MB window that starts at VRAM offset 0 post-POST.
- **BAR2 (doorbell, 2 MB):** `map_bar(2)`.
- **BAR5 (MMIO, 512 KB):** `map_bar(5)` — all register reads/writes
  go through this BAR.

### Phase 3 — IP discovery ✅

`ip_discovery.py` reads the IP-discovery binary via `MM_INDEX/MM_DATA`
indirect VRAM access (the table lives at `VRAM_SIZE - 64 KB`, past
our 256 MB BAR window). We currently use the empirical base
addresses for gfx1201 (see `memory/gfx1201-register-bases.md`) as
those have been confirmed against a real discovery read.

### Phase 4 — NBIO init ⚠️

The Windows backend has `nbio_init.py` with the NBIO 6.3.1 / v7.11
programming (doorbell aperture, HDP flush plumbing, selfring, interrupt
control), but we haven't wired it into the macOS path. vBIOS-POST
defaults have been enough so far for every flow we've exercised.

### Phase 5 — PSP bootloader chain → SOS alive ✅

`psp_bootloader.load_sos` walks the combined-SOS container
(`psp_14_0_3_sos.bin`) and hands each component to the ROM PSP
bootloader via the C2PMSG_35/36 mailbox in the order
`KDB → SPL → SYS_DRV → SOC_DRV → INTF_DRV → DBG_DRV → RAS_DRV →
IPKEYMGR_DRV → SOS`. The SOS alive check reads C2PMSG_81 (a
version-style sign-of-life register) — **not** C2PMSG_35 as earlier
versions of this code assumed. See `memory/phase8-psp-sos-blocker.md`
for the history of that bug.

### Phase 6 — PSP KM ring ✅

`psp_ring.ring_create(destroy_first=True)` DMA-allocates a 32 KB ring
buffer and tells SOS about it via C2PMSG_69/70/71/64. We also reset
C2PMSG_67 (PSP wptr) to zero after a fresh create — on re-runs over
a card whose MMIO state survived a DEXT reconnect, the stale wptr
would have caused the next submit to write at a non-zero frame index
while PSP's rptr is at zero, silently eating the command.

### Phase 7 — SMU firmware load via PSP ✅

`smu.smu_bring_up` opens its own command context, copies the **ucode
payload** out of `smu_14_0_3.bin` (`blob[ucode_off : ucode_off +
ucode_size]` — **not** the full container; that returns PSP status
`0x11`), and submits a `GFX_CMD_ID_LOAD_IP_FW(SMU)` via the KM ring.
PSP returns status 0, the SMU firmware starts.

### Phase 8 — SMU mailbox handshake ✅

- `GetSmuVersion` → `0x00684c00` (sanity).
- `SetDriverDramAddrHigh/Low` (message IDs **0x0E / 0x0F** — not 0x04 /
  0x05, those are `SetAllowedFeaturesMaskLow/High`) pointing at a
  VRAM MC address near the top of VRAM (offset
  `vram_size - 0x20000`). VRAM was chosen because it's
  unconditionally inside the FB aperture — SMU can read it without
  any MMHUB/AGP/GART setup.

### Phase 9 — SMU EnableAllSmuFeatures(FEATURE_PWR_SOC) ✅

The argument to `EnableAllSmuFeatures` is a `FEATURE_PWR_DOMAIN_e`
**selector**, not a feature bitmask. `FEATURE_PWR_ALL = 0` and
`FEATURE_PWR_GFX = 4` hang the SMU on a bare GPU. `FEATURE_PWR_SOC =
3` works with zero MMHUB / GFXHUB setup and brings SOC DPM online
(UCLK, FCLK, SOCCLK, LCLK, DPM_DCN). `GetRunningSmuFeaturesLow/High`
returns a live bitmask afterward (low 0x38B2D8B9, high 0xD18C).

### Phase 10 — GfxOff mailbox ✅

`PPSMC_MSG_DisallowGfxOff` (0x29) and `PPSMC_MSG_AllowGfxOff` (0x28)
both ACK cleanly. This is the original Phase 8.5c goal — SMU
mailbox is live and controllable.

### Phase 11 — VRAM autoload buffer ✅ (writes stick; contents not yet
acted on)

`gfx_autoload.build_autoload_buffer` maps BAR0 and slice-copies 23 MB
of content into low VRAM: parsed `gc_12_0_1_toc.bin` for the
`SOC24_FIRMWARE_ID_*` → offset/size table, then placed
`RLC_G_UCODE`, RLC sub-firmwares (SRLG / SRLS / RLX6_UCODE /
RLX6_DRAM_BOOT), SDMA (`sdma_7_0_1.bin`), RS64 PFP / ME / MEC
instructions + data (replicated into the P0..P3 stack slots),
MES P0/P1 ucode + data, and the TOC itself (with the
`RLC_TOC_FORMAT_API << 24 | 1` patch at the tail DWORD). A
sentinel round-trip via `MM_INDEX/MM_DATA` confirms the BAR0
writes are visible to the GPU.

### Phase 12 — IMU boot ❌ **blocker**

`gfx_autoload.run_imu_boot` programs
`GFX_IMU_RLC_BOOTLOADER_ADDR_{HI,LO}` + `SIZE`, streams IMU IRAM /
DRAM (66 KB each) into IMU SRAM via `GFX_IMU_{I,D}_RAM_ADDR/DATA`,
seals with `ADDR = ucode_version` (the integrity commit write
`imu_v12_0_load_microcode` does at the end), writes the
`GFX_IMU_C2PMSG_ACCESS_CTRL0/1` unlock values, and then clears bit 0
of `GFX_IMU_CORE_CTRL` to unhalt the IMU.

**The unhalt write silently fails.** Pre-write `CORE_CTRL = 0x09`
(halted + DRESET, the power-on default). We write `0x08`. The
immediate read-back still returns `0x09`. A 100 ms sampling loop
confirms neither `CORE_CTRL` nor `GFX_IMU_GFX_RESET_CTRL` (stuck at
`0x30` — bits 4 + 5 = GRBM + DFLL, the power-on values) ever
change. Linux's `imu_v12_0_wait_for_reset_status` expects `RESET_CTRL
& 0x1F == 0x1F`; we never get above 0x30.

`DisallowGfxOff` does not unblock the write. Something about the
GFX block's power/clock-gating state is still rejecting our
register writes.

### Phase 13 — RLC backdoor autoload — not yet reached

After IMU is alive, IMU reads from the RLC bootloader addr and
wakes RLC; RLC then pulls everything else from the autoload buffer.
`gfx_v12_0_wait_for_rlc_autoload_complete` is the Linux equivalent
of the final handshake.

### Phase 14 — SMU EnableAllSmuFeatures(FEATURE_PWR_GFX) — blocked by 12

Can't test until IMU + RLC come up in Phase 12/13.

## 4. The current blocker, in detail

**Symptom.** Writes to `GFX_IMU_CORE_CTRL` (base `0xA000 + 0x40B6`,
byte `0x382D8`) are silently dropped. The register reads as a
consistent power-on value (`0x09`), but any attempt to clear a bit
doesn't modify it. Other GC registers we touch behave similarly —
`RLC_CNTL` reads 0 and writes don't stick either.

**Likely cause.** The GFX power/clock-gating state is still
"asleep." On a Linux system, one or more of the following has
already happened before `gfx_v12_0_rlc_backdoor_autoload_enable`
runs, and on our setup none of them has:

1. **NBIO programming** (`backends/windows/nbio_init.py` — unused
   on macOS). HDP flush routing, doorbell aperture enable, selfring
   setup. Some of these may gate MMIO delivery to GC.
2. **SOC-level overrides.** Tinygrad's `AM_SOC` init (not yet
   investigated in detail) programs a `PG_OVERRIDE` / `CG_OVERRIDE`
   register sequence that disables power gating on GFX so MMIO
   writes actually land.
3. **Full GMC init on MMHUB *and* GFXHUB.** May be required for
   MMIO routing, not just DMA. Our `gmc.init_mmhub()` is half of
   this and has the side effect of breaking PSP DMA (enabling
   L1 TLB with `SYSTEM_ACCESS_MODE=3` faults on DART-mapped
   buffers) — so any real GMC init needs to keep an AGP identity
   window or a GART entry for system memory reachability.
4. **A specific SMU feature** that powers the GFX rail. We've
   enabled `FEATURE_PWR_SOC`; there may be an SMU-level "power up
   GFX" primitive (separate from `AllowGfxOff` / `DisallowGfxOff`)
   that Linux's `dpm_set_gfx_power_up_by_imu` analog triggers.

**Cheap next experiments (would narrow the cause in one or two
replugs each):**

- Try the "no-IMU" alt path in `gfx_v12_0_rlc_backdoor_autoload_enable`:
  write `RLC_GPM_THREAD_ENABLE` + `RLC_CNTL`. If those writes also
  don't stick we've confirmed this is a blanket GC-block gate, not
  something specific to IMU. If they *do* stick, we have a
  different bring-up path.
- Try setting a scratch register (`GRBM_GFX_CNTL`, `SCRATCH_REG0`)
  and reading back. If any GC write sticks anywhere, the gate is
  finer-grained than "no GC access at all."
- Run the existing NBIO init first, then re-probe. Cheap since
  NBIO already has a module.

## 5. Key findings (the "watch out" list)

Each of these cost a meaningful iteration and is non-obvious from
reading Linux amdgpu alone:

- **PSP SOS liveness is C2PMSG_81**, not C2PMSG_35.
- **PSP `LOAD_IP_FW` expects raw ucode bytes**, not the
  `common_firmware_header`-wrapped container. Status `0x11` is the
  rejection code.
- **PSP wptr (`C2PMSG_67`) survives a DEXT reconnect**. Reset it
  to zero after (re-)creating the ring, otherwise the first post-
  reset submit writes at a stale index and PSP silently ignores it.
- **SMU message IDs**: `SetDriverDramAddr{High,Low} = 0x0E / 0x0F`,
  not 0x04 / 0x05 (those are `SetAllowedFeaturesMask{Low,High}`,
  which return `0xFD = CmdRejectedPrereq` when a driver table isn't
  set up).
- **`EnableAllSmuFeatures` arg is a `FEATURE_PWR_DOMAIN_e` selector**,
  not a bitmask. 3=SOC works on a bare GPU; 0=ALL and 4=GFX need
  more infrastructure.
- **IMU header offsets** (`imu_iram_offset_bytes`,
  `imu_dram_offset_bytes`) are **ignored by amdgpu** — IRAM is at
  `ucode_array_offset_bytes`, DRAM is at `ucode_off + iram_size`.
  Taking the header fields literally puts the container header into
  IMU IRAM and IMU rejects it.
- **RLC `TOC_V2` bit layout**: `size_x16` at bit 12, `size` at bits
  14..31. Getting the bit positions off by 2 (e.g., `size_x16` at
  bit 14) produces sizes that look plausibly in-bounds but are
  exactly half the real value.
- **`ctypes.memset` on the BAR0 VRAM mapping SIGBUSes past ~1 MB**
  on Apple Silicon, but `(ctypes.c_ubyte * n).from_address(p)[:] =
  zeros` works at any size.
- **gfx12 RLC is not loaded via PSP** `LOAD_IP_FW`; the path is
  "backdoor autoload" into a VRAM buffer that RLC self-reads once
  IMU releases it. PSP status `0xFFFF0000` is the "wrong ASIC path"
  tell.
- **Programming MMHUB (`init_mmhub`) before PSP DMA breaks PSP DMA.**
  Enabling L1 TLB with `SYSTEM_ACCESS_MODE=3` and no AGP/GART
  identity window → DART-mapped bus addrs page-fault and the next
  PSP ring submit times out.

## 6. Mapping between this document and the tree

| Phase                          | Code path                                                                |
|--------------------------------|--------------------------------------------------------------------------|
| DEXT attach / BAR map / MMIO   | `backends/macos/iokit_client.py`                                         |
| IP discovery                   | `backends/macos/ip_discovery.py` + `backends/windows/ip_discovery.py`    |
| PSP bootloader chain           | `backends/macos/psp_bootloader.py` (`load_sos`)                          |
| PSP KM ring                    | `backends/macos/psp_ring.py`                                             |
| PSP ring command submission    | `backends/macos/psp_cmd.py`                                              |
| SMU bring-up (Phases 7–9)      | `backends/macos/smu.py` (`smu_bring_up`)                                 |
| MMHUB init (stashed)           | `backends/macos/gmc.py` (`init_mmhub`)                                   |
| gfx12 autoload + IMU boot      | `backends/macos/gfx_autoload.py`                                         |
| IMU via PSP (gfx11 parity)     | `backends/macos/gfx_firmware.py`                                         |
| End-to-end smoke tests         | `try_smu_enable_with_agp.py`, `try_autoload_gfx.py`, `try_autoload_resume.py` |
