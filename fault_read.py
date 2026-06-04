#!/usr/bin/env python3
# Post-fault register dump for gfx1201 count-15 CP 0x1000.
# Run IMMEDIATELY after a torch process faults at submit ~15 (do NOT drain first).
# Opens the DEXT WITHOUT any bring-up/reset/BAR-map. Pure mmio_read32 only.
import sys
sys.path.insert(0, "/Users/anush/github/TheRock/userspace_driver/python")
from amd_gpu_driver.backends.macos.iokit_client import IOKitClient

MMIO = 5
GC0, GC1, MMHUB, OSSSYS = 0x1260, 0xA000, 0x1A000, 0x10A0

c = IOKitClient()
c.open()  # side-effect-free w.r.t. GPU state; does NOT reset or map a BAR
try:
    def rd(base, off):
        return c.mmio_read32(MMIO, (base + off) * 4)

    print("=== GFXHUB GPUVM (compute-queue VMID0) ===")
    st_lo = rd(GC0, 0x15d0); st_hi = rd(GC0, 0x15d1)
    ad_lo = rd(GC0, 0x15d2); ad_hi = rd(GC0, 0x15d3)
    print(f"GCVM_FAULT_STATUS_LO32 = 0x{st_lo:08x}  HI32 = 0x{st_hi:08x}")
    if st_lo:
        print(f"  MORE_FAULTS={st_lo&1} WALKER_ERR={(st_lo>>1)&1} "
              f"PERM={(st_lo>>4)&1} MAPPING_ERR={(st_lo>>8)&1} "
              f"CID={(st_lo>>9)&0x1f} RW={(st_lo>>18)&1} VMID={(st_lo>>20)&0xf}")
    va = ((ad_hi << 32) | ad_lo) << 12
    print(f"GCVM_FAULT_ADDR = 0x{va:012x} (lo=0x{ad_lo:08x} hi=0x{ad_hi:08x})")
    print(f"GCVM_FAULT_CNTL = 0x{rd(GC0,0x15cc):08x}")

    print("=== MMHUB GPUVM (MES/system side) ===")
    mm_lo = rd(MMHUB, 0x04f0)
    print(f"MMVM_FAULT_STATUS_LO32 = 0x{mm_lo:08x}  "
          f"ADDR_LO=0x{rd(MMHUB,0x04f2):08x} HI=0x{rd(MMHUB,0x04f3):08x}  "
          f"(caution: gmc.py init may have stomped ADDR regs)")

    print("=== CP / GRBM engine ===")
    print(f"GRBM_STATUS  = 0x{rd(GC0,0x0da4):08x} (CP_BUSY b29, SPI b22, GUI b31)")
    print(f"GRBM_STATUS2 = 0x{rd(GC0,0x0da2):08x} (CPF b28, CPC b29, CPG b30, RLC b26)")
    print(f"CP_STAT      = 0x{rd(GC0,0x0f40):08x}")
    print(f"CP_CPC_STATUS= 0x{rd(GC0,0x0e24):08x}")

    print("=== CP interrupt latched status (un-drained events) ===")
    print(f"CP_INT_CNTL        = 0x{rd(GC0,0x1de9):08x} (b18/19/20 = cmp/cntx-busy/cntx-empty enables)")
    print(f"CP_INT_STATUS      = 0x{rd(GC0,0x1dea):08x}")
    print(f"CP_INT_STATUS_RING0= 0x{rd(GC0,0x1e0d):08x} <- bits set = CP completion ints that fired, never acked")

    print("=== MES liveness (read twice) ===")
    h1 = rd(GC1, 0x280d); p1 = rd(GC1, 0x2813)
    import time; time.sleep(0.02)
    h2 = rd(GC1, 0x280d); p2 = rd(GC1, 0x2813)
    print(f"CP_MES_HEADER_DUMP {h1:#x} -> {h2:#x}  ({'advancing' if h2!=h1 else 'FROZEN'})")
    print(f"CP_MES_INSTR_PNTR  {p1:#x} -> {p2:#x}  ({'advancing' if p2!=p1 else 'FROZEN'})")

    print("=== IH ring (expected unconfigured) ===")
    ih = rd(OSSSYS, 0x00c2)
    print(f"IH_STATUS = 0x{ih:08x} (IDLE b0, RB_FULL b3, RB_OVERFLOW b5=0x20)")
    print(f"IH_RB_CNTL=0x{rd(OSSSYS,0x0080):08x} WPTR=0x{rd(OSSSYS,0x0082):08x} RPTR=0x{rd(OSSSYS,0x0081):08x}")

    # Compute queue HQD state (me=1 MEC, pipe=0, hqd=0). This WRITES GRBM_GFX_CNTL
    # to select the HQD -- do it LAST (perturbs the GRBM index). Confirms whether
    # the queue was descheduled (ACTIVE=0) and how far the CP consumed (rptr).
    print("=== compute HQD state (me=1 pipe=0 hqd=0) -- dechedule check ===")
    def wr(base, off, v): c.mmio_write32(MMIO, (base + off) * 4, v & 0xFFFFFFFF)
    # GRBM_GFX_CNTL select: (me<<2)|pipe with the queue index too; probe uses
    # (me<<2)|pipe for me/pipe and a separate queue field. gfx12: MEID[3:2],
    # PIPEID[1:0], QUEUEID[5:4]. Select me=1,pipe=0,queue=0:
    wr(GC1, 0x0900, (1 << 2) | (0 << 0) | (0 << 4))  # regGRBM_GFX_CNTL (B1)
    act = rd(GC0, 0x1fab)        # CP_HQD_ACTIVE
    rptr = rd(GC0, 0x1fb3)       # CP_HQD_PQ_RPTR (dwords consumed)
    wptr_lo = rd(GC0, 0x1fdf)    # CP_HQD_PQ_WPTR_LO
    db = rd(GC0, 0x1fb8)         # CP_HQD_PQ_DOORBELL_CONTROL
    wr(GC1, 0x0900, 0)           # deselect
    print(f"CP_HQD_ACTIVE = 0x{act:08x}  ({'ACTIVE' if act&1 else 'DESCHEDULED/inactive'})")
    print(f"CP_HQD_PQ_RPTR = 0x{rptr:08x} ({rptr} dwords = {rptr//69} dispatches of 69dw)")
    print(f"CP_HQD_PQ_WPTR_LO = 0x{wptr_lo:08x}  DOORBELL_CONTROL = 0x{db:08x}")
finally:
    c.close()
