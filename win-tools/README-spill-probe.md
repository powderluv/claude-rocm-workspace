# Direct-queue multi-dispatch spill probe (gfx1201)

Torch-free reproducer for the ROCr `lite::` Windows direct-queue multi-dispatch
ceiling. Launches a minimal **register-spilling** kernel N times on one queue and
verifies each result — the harness that root-caused and validated the HQD
`QUEUE_SIZE` / ring-size fix (the old hard stall at dispatch ~14).

## Files
- `spill_kernel.hip` — minimal kernel with a 1 KB `volatile` private array, forcing
  a scratch (private-segment) allocation so each dispatch exercises spill/scratch.
  Result is `out[t] == 256*t + 32640`.
- `spill.hsaco` — prebuilt gfx1201 code object.
  Rebuild: `/opt/rocm-*/bin/hipcc --genco --offload-arch=gfx1201 spill_kernel.hip -o spill.hsaco`
- `spill_probe_mi.py` — ctypes harness. Sets the ROCr direct-compute env (via
  `setdefault`, so any var can be overridden) **before** loading `amdhip64_7.dll`
  from the `rocm_sdk` bin dir, then `hipModuleLaunchKernel` `--iters N` times on the
  same queue, re-seeding a poison value each iteration and checking the output.

## Run (win11-gpu VM)
```
B:\tvenv\Scripts\python.exe -u spill_probe_mi.py --co spill.hsaco --iters 150
```
Expect `SPILL_DONE iters=150 last_bad=0` after the QUEUE_SIZE fix (previously hung
at iter 14). Useful override for bisecting completion paths:
`set ROCR_WINDOWS_AQL_EOP_FENCE=0` (drops the per-dispatch EOP fence).

## What it proved
The ceiling was exactly at a 1024-dword (0x400) ring boundary: the direct-path HQD
descriptor hardcoded `CP_HQD_PQ_CONTROL.QUEUE_SIZE = 9` (1024 dwords) while
`kDirectComputeRingSize` had grown to 8192 dwords, so the CP wrapped its rptr at
1024 while ROCr wrote the full ring. Fix: derive `QUEUE_SIZE` from the ring buffer
(matching the MES path). `EOP_FENCE=0` shifting the wall 14→15 (fewer dwords per
dispatch) was the fingerprint that pinned it to the ring boundary.
