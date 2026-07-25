#!/bin/bash
# Run the non-scratch PyTorch baseline on the gfx1201 eGPU. Reconciled to the
# CURRENT lite:: source (post-refactor): the 2026-06-01 knobs ROTATE_BACKING/
# MAX_QUEUES/PQ_CONTROL/DEQUEUE_AFTER_SUBMIT/HOST_BLIT_ONLY are gone (now defaults),
# so this mirrors the maintained run-multi-dispatch-test.sh bring-up + env.
# Self-driving: drain + phase-9 bring-up (retry the flaky KIQ activation) then run.
set -u
ROCK=/Users/anush/github/TheRock
WS=/Users/anush/github/claude-rocm-workspace
SDK=$ROCK/build-macos-egpu/dist/rocm
TORCH_VENV=$ROCK/external-builds/pytorch/.venv-torch

# Match the proven-working run-multi-dispatch-test.sh env exactly (it creates a
# ROCr queue + dispatches HIP kernels successfully on the current code).
export ROCM_PATH=$SDK HIP_PATH=$SDK DYLD_LIBRARY_PATH="$SDK/lib:$SDK/lib/llvm/lib"
export AMD_GPU_MACOS_FORCE_DIRECT_COMPUTE=1 ROCR_MACOS_HOST_BLIT_ONLY=1 \
       ROCR_MACOS_AQL_SKIP_HOST_COPYBACK=1
# MES-backed compute path is the DEFAULT (matches the Linux/Windows lite:: path).
# The per-process / isolate cross-process wedge is fixed by the #66/#67 MES
# scheduler-HQD teardown (default-on in ROCr), so multi-process torch is robust.
# Opt into the legacy DIRECT path with ROCR_MACOS_DIRECT=1 (the ROCR gate is
# presence-based, so this wrapper only exports the var when MES is wanted).
[ "${ROCR_MACOS_DIRECT:-0}" = "1" ] || export ROCR_MACOS_USE_MES_QUEUE=1
# Diagnostic trace (set TORCH_TRACE=1 to capture the failing HSA call):
if [ "${TORCH_TRACE:-0}" = "1" ]; then
  export AMD_LOG_LEVEL=4 ROCR_MACOS_TRACE_AQL=1 ROCR_MACOS_TRACE_DIRECT_QUEUE=1 HIP_LAUNCH_BLOCKING=1
fi
# Serialize launches (workaround for the single-HQD async race that wedges e.g. cat):
if [ "${TORCH_BLOCKING:-0}" = "1" ]; then export HIP_LAUNCH_BLOCKING=1; fi

for attempt in 1 2 3 4 5; do
  echo "=== bring-up attempt $attempt ==="; date +%H:%M:%S
  python3 "$WS/egpu_drain.py" >/dev/null 2>&1 || python3 /tmp/egpu_drain.py >/dev/null 2>&1
  # SKIP_NOP-only bring-up (exact multi-dispatch recipe): ROCr's hsa_queue_create
  # maps the MES scheduler itself (KIQ MAP_SCHEDULER), so pre-mapping it here
  # (PHASE9_MAP_SCHED) double-maps and wedges the scheduler ring (rptr=0 timeout).
  PHASE9_SKIP_NOP=1 PYTHONPATH="$ROCK/userspace_driver/python" \
    "$ROCK/.venv/bin/python" -u "$ROCK/userspace_driver/python/try_phase9_doorbell.py" \
    >/tmp/phase9_torch.log 2>&1
  grep -q "GFX bring-up complete" /tmp/phase9_torch.log || { echo "  phase-9 failed, retry"; continue; }
  echo "=== run torch baseline ==="
  cd /tmp
  "$TORCH_VENV/bin/python" -u "${TORCH_TEST:-$WS/torch_baseline.py}" >/tmp/torch_base.out 2>/tmp/torch_base.err
  rc=$?
  cat /tmp/torch_base.out
  echo "  [rc=$rc]"
  grep -iE "0x1000|aborting with error|HSA_STATUS_ERROR|Fatal|hipError|hsa_queue_create" /tmp/torch_base.err | tail -5
  KF=$(grep -c "KIQ activation failed" /tmp/torch_base.err)
  if [ "$KF" -gt 0 ] && [ "$rc" -ne 0 ]; then echo "  KIQ flaked, retry"; continue; fi
  exit $rc
done
echo "bring-up never succeeded"; exit 2
