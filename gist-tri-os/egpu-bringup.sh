#!/bin/bash
# egpu_bringup: egpu_replug (power-cycle) + phase-9, with retry on the cold-boot
# PSP-timeout flakiness (the documented fix is another power-cycle). Inherits any
# PHASE9_* env flags the caller sets. Writes the phase-9 log to $PHASE9_LOG
# (default /tmp/phase9.log). Exit 0 when "GFX bring-up complete" is seen.
ROCK=/Users/anush/github/TheRock
WS=/Users/anush/github/claude-rocm-workspace
LOG=${PHASE9_LOG:-/tmp/phase9.log}
ATTEMPTS=${EGPU_BRINGUP_RETRIES:-4}
for attempt in $(seq 1 "$ATTEMPTS"); do
  echo "=== egpu_bringup attempt $attempt/$ATTEMPTS ==="
  bash "$WS/egpu-replug.sh" || { echo "egpu_bringup: replug failed, retrying" >&2; continue; }
  cd "$ROCK" || exit 1
  PYTHONPATH=userspace_driver/python "$ROCK/.venv/bin/python" -u \
      userspace_driver/python/try_phase9_doorbell.py >"$LOG" 2>&1
  if grep -q "GFX bring-up complete" "$LOG"; then
    echo "egpu_bringup: phase-9 OK (attempt $attempt) GFX=1 MESfences=$(grep -c 'fence signaled' "$LOG")"
    exit 0
  fi
  echo "egpu_bringup: phase-9 failed attempt $attempt (PSP cold-boot?); re-cycling" >&2
  tail -3 "$LOG" | grep -vE '^[[:space:]]*$' >&2
done
echo "egpu_bringup: phase-9 never completed after $ATTEMPTS attempts" >&2
exit 1
