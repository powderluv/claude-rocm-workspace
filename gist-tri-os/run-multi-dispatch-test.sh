#!/bin/bash
# Build + run the macOS eGPU lite:: multi-dispatch unit test (gfx1201).
# Replaces the torch-based dispatch_count probe: a tiny HIP program that launches
# a trivial kernel N times through the same ROCr/lite:: MES path and verifies.
# Self-driving: full power-drain + phase-9 bring-up (retries the flaky KIQ
# activation) then runs the test. Exit 0 only if the test PASSES (survives N
# dispatches + verifies). Usage: bash run-multi-dispatch-test.sh [N]   (default 200)
set -u
N=${1:-200}
ROCK=/Users/anush/github/TheRock
WS=/Users/anush/github/claude-rocm-workspace
SDK=$ROCK/build-macos-egpu/dist/rocm
SRC=$WS/multi_dispatch_test.cpp
BIN=/tmp/multi_dispatch_test

echo "=== build (hipcc, gfx1201) ==="
SDKROOT=$(xcrun --show-sdk-path) ROCM_PATH=$SDK HIP_PATH=$SDK \
  "$SDK/bin/hipcc" --offload-arch=gfx1201 "$SRC" -o "$BIN" || { echo "BUILD FAILED"; exit 2; }

export ROCM_PATH=$SDK HIP_PATH=$SDK DYLD_LIBRARY_PATH="$SDK/lib:$SDK/lib/llvm/lib"
export AMD_GPU_MACOS_FORCE_DIRECT_COMPUTE=1 ROCR_MACOS_HOST_BLIT_ONLY=1 \
       ROCR_MACOS_AQL_SKIP_HOST_COPYBACK=1
export ROCR_MACOS_USE_MES_QUEUE=1 ROCR_MACOS_TRACE_DIRECT_QUEUE=1

# Phase-9 KIQ activation is flaky across cold boots; retry the whole bring-up.
for attempt in 1 2 3 4 5; do
  echo "=== bring-up attempt $attempt ===" ; date +%H:%M:%S
  python3 "$WS/egpu_drain.py" >/dev/null 2>&1 || python3 /tmp/egpu_drain.py >/dev/null 2>&1
  PHASE9_SKIP_NOP=1 PYTHONPATH="$ROCK/userspace_driver/python" \
    "$ROCK/.venv/bin/python" -u "$ROCK/userspace_driver/python/try_phase9_doorbell.py" \
    >/tmp/phase9_ut.log 2>&1
  grep -q "GFX bring-up complete" /tmp/phase9_ut.log || { echo "  phase-9 failed, retry"; continue; }
  cd "$ROCK"
  "$BIN" "$N" >/tmp/ut.out 2>/tmp/ut.err
  rc=$?
  S=$(grep -c 'submit qid=1 ' /tmp/ut.err)
  KF=$(grep -c 'KIQ activation failed' /tmp/ut.err)
  echo "  $(cat /tmp/ut.out)   [submits=$S kiq_fail=$KF rc=$rc]"
  if [ "$KF" -gt 0 ] && [ "$S" -eq 0 ]; then echo "  KIQ flaked, retry"; continue; fi
  exit $rc   # real result (PASS=0, DIED=1, verify-fail=3)
done
echo "bring-up never succeeded after retries"; exit 2
