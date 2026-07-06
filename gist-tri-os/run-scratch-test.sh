#!/bin/bash
# Build + run the gfx12 scratch test through the macOS lite:: MES path with
# ROCR_MACOS_AQL_ENABLE_SCRATCH=1. Self-driving: drain + phase-9 bring-up (retry
# the flaky KIQ activation) then run. Validates the MQD-scratch + MES-re-map fix
# (#15): pre-fix a spilling kernel faulted 0x1000 (FLAT_SCRATCH=0).
set -u
ROCK=/Users/anush/github/TheRock
WS=/Users/anush/github/claude-rocm-workspace
SDK=$ROCK/build-macos-egpu/dist/rocm
SRC=$WS/scratch_test.cpp
BIN=/tmp/scratch_test

echo "=== build (hipcc gfx1201) ==="
SDKROOT=$(xcrun --show-sdk-path) ROCM_PATH=$SDK HIP_PATH=$SDK \
  "$SDK/bin/hipcc" --offload-arch=gfx1201 "$SRC" -o "$BIN" || { echo "BUILD FAILED"; exit 2; }

export ROCM_PATH=$SDK HIP_PATH=$SDK DYLD_LIBRARY_PATH="$SDK/lib:$SDK/lib/llvm/lib"
export AMD_GPU_MACOS_FORCE_DIRECT_COMPUTE=1 ROCR_MACOS_HOST_BLIT_ONLY=1 \
       ROCR_MACOS_AQL_SKIP_HOST_COPYBACK=1 ROCR_MACOS_DIRECT_QUEUE_SKIP_DESTROY=1
export ROCR_MACOS_USE_MES_QUEUE=1 ROCR_MACOS_AQL_ENABLE_SCRATCH=1 \
       ROCR_MACOS_TRACE_DIRECT_QUEUE=1

for attempt in 1 2 3 4 5; do
  echo "=== bring-up attempt $attempt ==="; date +%H:%M:%S
  python3 "$WS/egpu_drain.py" >/dev/null 2>&1 || python3 /tmp/egpu_drain.py >/dev/null 2>&1
  PHASE9_SKIP_NOP=1 PYTHONPATH="$ROCK/userspace_driver/python" \
    "$ROCK/.venv/bin/python" -u "$ROCK/userspace_driver/python/try_phase9_doorbell.py" \
    >/tmp/phase9_scratch.log 2>&1
  grep -q "GFX bring-up complete" /tmp/phase9_scratch.log || { echo "  phase-9 failed, retry"; continue; }
  cd "$ROCK"
  "$BIN" 256 >/tmp/scr.out 2>/tmp/scr.err
  rc=$?
  echo "  RESULT: $(cat /tmp/scr.out)   [rc=$rc]"
  echo "  --- scratch trace ---"
  grep -iE "AQL scratch base|set-scratch|WRAP-FAULT|aborting with error|fault" /tmp/scr.err | tail -6
  KF=$(grep -c "KIQ activation failed" /tmp/scr.err)
  if [ "$KF" -gt 0 ] && [ "$rc" -ne 0 ]; then echo "  KIQ flaked, retry"; continue; fi
  exit $rc
done
echo "bring-up never succeeded"; exit 2
