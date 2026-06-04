#!/usr/bin/env python3
# Standalone IH-ring drainer for the macOS gfx1201 bring-up.
# Continuously advances IH_RB_RPTR toward IH_RB_WPTR so the IH ring (set up by
# try_phase9_doorbell.py PHASE9_IH_RING=1) never fills and back-pressures the
# MES/CP completion path. Runs as a SEPARATE process alongside the ROCr/HIP
# dispatch process (the dispatch loop lives in ROCr, not in phase-9), so this
# also tests whether the DEXT allows a second concurrent MMIO client.
#   IH_DRAIN_SECONDS=<n> (default 60)   ROCR ... (env unused)
import os, sys, time
sys.path.insert(0, "/Users/anush/github/TheRock/userspace_driver/python")
from amd_gpu_driver.backends.macos.iokit_client import IOKitClient

MMIO = 5
IH = 0x10a0            # OSSSYS base (dword)
IH_RB_RPTR, IH_RB_WPTR = 0x81, 0x82
MASK = 0x3ffff         # 256KB ring, byte granularity

c = IOKitClient()
c.open()
def rd(o): return c.mmio_read32(MMIO, (IH + o) * 4)
def wr(o, v): c.mmio_write32(MMIO, (IH + o) * 4, v & 0xffffffff)

dur = float(os.environ.get("IH_DRAIN_SECONDS", "60"))
print(f"[ih_drainer] client open OK; draining for {dur:.0f}s "
      f"(initial WPTR=0x{rd(IH_RB_WPTR)&MASK:x} RPTR=0x{rd(IH_RB_RPTR)&MASK:x})",
      flush=True)
last = -1
advances = 0
maxw = 0
deadline = time.time() + dur
next_hb = time.time() + 0.5
while time.time() < deadline:
    w = rd(IH_RB_WPTR) & MASK
    if w != last:
        wr(IH_RB_RPTR, w)
        if w > maxw: maxw = w
        last = w
        advances += 1
        print(f"[ih_drainer] WPTR moved -> 0x{w:x} (advance #{advances})", flush=True)
    if time.time() >= next_hb:
        print(f"[ih_drainer] hb WPTR=0x{rd(IH_RB_WPTR)&MASK:x} RPTR=0x{rd(IH_RB_RPTR)&MASK:x} advances={advances}", flush=True)
        next_hb = time.time() + 0.5
    time.sleep(0.0005)
print(f"[ih_drainer] done: rptr advances={advances} maxwptr=0x{maxw:x}", flush=True)
c.close()
