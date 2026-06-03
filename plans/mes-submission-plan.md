# Plan: Route ROCr macOS compute dispatch through MES (gfx1201, no KMD)

Status: planning. Tracks task #17. Supersedes the hand-rolled direct-HQD submission
for sustained multi-op workloads.

## Why

The macOS eGPU port currently dispatches via a hand-rolled direct HQD: ROCr
(`MacAqlQueue`) transpiles each AQL packet to PM4 and pokes `CP_HQD_PQ_WPTR`
itself — acting as a software CP. This has two consequences:
- **Multi-dispatch ceiling (~13 submits/process):** with no firmware queue
  manager, sustained submission wedges the CP (HSA 0x1000) after a handful of
  dispatches. Blocks any real multi-op PyTorch program.
- **No per-queue scratch setup:** register-spilling kernels (bool reductions,
  conv, etc.) fault because nothing programs scratch at queue scope.

MES (MicroEngine Scheduler) is the firmware queue manager. Letting MES own HQD
activation + doorbell scheduling removes the multi-dispatch ceiling.

## Key discovery

The MES submission stack **already exists, fully implemented**, in the shared
`lite::` backend (`amd_lite_direct_queue.cpp`): `EnsureMesScheduler` (KIQ +
scheduler ring bring-up, SET_HW_RESOURCES/_1, MAP_SCHEDULER, aggregated
doorbells), `MapLegacyQueueWithMes` (compute `ADD_QUEUE` → sets `mes_backed`),
`UnmapLegacyQueueWithMes` (REMOVE_QUEUE), and a submit fork where `mes_backed`
queues only ring the doorbell (no CP_HQD MMIO). It is gated by
`DirectQueueOptions.use_mes_queue`, which the Linux driver enables by default but
**`MacDirectQueueOptions` never sets** — so on macOS the entire MES path is dead
code and we run the direct-HQD path.

## Scope correction (important)

MES `ADD_QUEUE(map_legacy_kq)` does **not** carry scratch base or tmpring, and
gfx_v12 compute-MQD init leaves them 0. So:
- MES fixes the **multi-dispatch** ceiling (MES owns the HQD; ROCr stops poking
  CP_HQD_PQ_WPTR per submit).
- MES does **not** fix **scratch**. Scratch stays the inline-PM4 mechanism
  (`COMPUTE_TMPRING_SIZE` + `COMPUTE_DISPATCH_SCRATCH_BASE`, task #15), which is
  orthogonal and works under MES because `map_legacy_kq` keeps ROCr transpiling
  AQL→PM4 (those are per-dispatch CP regs, independent of who activated the HQD).

## Biggest unknown (de-risk first)

Does MES compute `ADD_QUEUE(map_legacy_kq)` actually activate a userspace HQD on
gfx1201 such that ringing the **queue's own doorbell** (no host MMIO) makes the
CP consume PM4 and run a wave? Scheduler-queue mapping works on hardware; compute
mapping has **never** succeeded. The lite:: frame field offsets and
SET_HW_RESOURCES register bases are transcribed from a Linux ASIC, not derived
for gfx1201. Resolve this in the cheap Python probe before touching ROCr.

## Target architecture (v1)

```
torch/HIP -> AQL ring -> MacAqlQueue::SubmitPackets -> SubmitKernel transpiles
AQL->PM4 (incl. inline scratch regs) -> write PM4 into per-queue ring ->
ring queue doorbell only -> MES (mapped this queue's MQD->HQD at create) sees
doorbell, schedules HQD, CP runs PM4.
```
MES owns activation + scheduling; ROCr still builds PM4; scratch inline. NOT
asking MES to decode AQL (`is_aql_queue`) — that is a deferred v2.

## Milestones

- **M1 — Prove in the Python probe (not ROCr):** in `try_phase9_doorbell.py`
  (the `PHASE9_MAP_COMPUTE` prototype), from the working scheduler-mapped state,
  `ADD_QUEUE` a single compute queue (pipe0/hqd0), write trivial PM4
  (NOP+WRITE_DATA) into its ring, ring **only the queue doorbell**, and verify
  the VRAM marker updates + `CP_HQD_PQ_RPTR` advances with zero host
  `CP_HQD_PQ_WPTR` writes. Then 20+ batches without wedge (direct test of the
  multi-dispatch ceiling). Fix the MES compute ABI here if it fails.
- **M2 — Wire ROCr behind a gate:** `MacDirectQueueOptions().use_mes_queue =
  EnvEnabled("ROCR_MACOS_USE_MES_QUEUE")` (default OFF). Verify macOS satisfies
  the `DirectQueuePlatform` hooks the MES path needs (esp. `FlushHdp`); confirm
  MES/KIQ/per-queue FB offsets (0x1800000/0x1840000/0x1900000+N*0x40000) fit the
  256 MB BAR0.
- **M3 — Single dispatch e2e through ROCr+MES:** one trivial HIP kernel;
  confirm `mes_backed`, doorbell-only submit, no CP_HQD_PQ_WPTR writes, correct
  output. Handle MES-boot-before-ROCr ordering (fail loud if MES pipes inactive).
- **M4 — Multi-dispatch + scratch:** >50 dispatches/process (multi-dispatch
  regression); then enable `ROCR_MACOS_AQL_ENABLE_SCRATCH` and validate a
  scratch kernel under the MES-backed queue; run the torch matmul/reduction set.
- **M5 — Teardown + stability:** REMOVE_QUEUE on destroy, re-create without
  wedging, long ring-wrap soak.

## Risks

1. **MES compute ADD_QUEUE may not activate the HQD on gfx1201** (frame offsets /
   `map_legacy_kq` semantics / SET_HW_RESOURCES bases unproven). De-risk in M1;
   cross-check vs Linux `mes_v12_0_add_hw_queue`/`set_hw_resources` and gfx1201
   IP bases (we have empirical bases).
2. **MES boot ordering / PSP reliability across replug** — `EnsureMesScheduler`
   assumes MES ucode loaded + both pipes active; it doesn't load ucode. Make ROCr
   fail loud (not silently fall back) when `CP_MES_CNTL` pipe bits are unset.
3. **Multi-dispatch wedge may not be solely HQD-ownership** — the per-queue PM4
   ring still exists under MES and `SubmitMesApiFrameOnRing` has no ring-wrap
   straddle guard. Task #16 (straddle-safe ring writes) is coupled to #17, not
   independent. Force a ring wrap in M4 and watch the boundary.

## Keep the direct-HQD path as a gated fallback

Yes — it is the only proven-working compute path today (validation passing).
Gate MES via `ROCR_MACOS_USE_MES_QUEUE` default OFF; flip to ON only after M3–M4
pass repeatedly across replugs. Keep direct-HQD indefinitely as a bring-up A/B
tool. Caveat: it is not a viable fallback for workloads that exceed the
~13-submit ceiling — those need MES.

## Alternatives Considered

- **Hand-map HQDs via KIQ/registers, tinygrad-AM style (no MES scheduler).**
  tinygrad's no-KMD AM backend does exactly this: write the MQD register block,
  set `CP_HQD_ACTIVE=1`, program scratch inline in PM4. This is what our direct
  path already approximates. Rejected as the primary path because it is the same
  software-CP model that hits the multi-dispatch ceiling; MES exists to own
  activation/scheduling so the host stops being the CP. (Retained as the gated
  fallback.)
- **Full AQL MES queue (`is_aql_queue=1`): MES/CP consume AQL directly, scratch
  via the `amd_queue_t` descriptor.** This eliminates per-dispatch PM4 entirely
  and is the eventual end state. Deferred: much larger change, and not required
  to clear either blocker. v1 uses `map_legacy_kq` (ROCr keeps transpiling PM4).
- **Stay on direct-HQD and engineer around the ceiling** (e.g., recycle queues,
  enlarge rings, fix the ~13-submit resource). Rejected: the ceiling is a symptom
  of having no queue manager; chasing per-resource limits is open-ended, while
  MES is the designed solution and already implemented.

## Relevant files

- `core/driver/macos/amd_macos_driver.cpp` — the `use_mes_queue` gate
  (`MacDirectQueueOptions`, ~line 126-136), `SubmitDirectCompute`,
  `CreateDirectComputeQueue`.
- `core/driver/lite/amd_lite_direct_queue.cpp` — MES stack: `EnsureMesScheduler`,
  `MapLegacyQueueWithMes` (~1594), `SubmitMesApiFrameOnRing` (~949), the
  create-fork (~1873), mes_backed submit (~2173), SET_HW_RESOURCES frame
  (~1078), pipe/hqd derivation (`DirectQueuePipe`/`DirectQueueHqd`).
- `core/inc/amd_lite_direct_queue.h` — `DirectQueueOptions.use_mes_queue`
  (default false), platform hooks incl. `FlushHdp`.
- `core/runtime/amd_macos_aql_queue.cpp` — AQL→PM4 transpile + inline scratch.
- `userspace_driver/python/try_phase9_doorbell.py` — MES bring-up + the
  `PHASE9_MAP_COMPUTE` prototype (the M1 harness).
- `userspace_driver/python/amd_gpu_driver/backends/macos/gfx_bringup.py` — PSP
  loads MES (ordering dependency for Risk 2).

Coupled tasks: #15 (scratch, clears blocker #2, independent of MES), #16
(ring-wrap straddle, likely lands alongside #17), #17 (this).
