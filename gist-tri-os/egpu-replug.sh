#!/bin/bash
# egpu_replug: power-cycle the eGPU via the iBoot G2 and wait for it to
# re-enumerate over Thunderbolt (DEXT reload) — a full replacement for a manual
# physical replug, so test loops can self-drive. Exit 0 once the GPU is back.
#
# Usage: bash egpu-replug.sh   (then run phase-9 / your test)
set -uo pipefail
WS=/Users/anush/github/claude-rocm-workspace
ROCK=/Users/anush/github/TheRock

echo "=== egpu_replug: power-cycling eGPU via iBoot G2 ==="; date
python3 "$WS/egpu-powercycle.py" || { echo "egpu_replug: power-cycle FAILED" >&2; exit 1; }

echo "=== egpu_replug: waiting for eGPU to re-enumerate (DEXT) ==="
PYTHONPATH="$ROCK/userspace_driver/python" "$ROCK/.venv/bin/python" - <<'PY'
import sys, time
from amd_gpu_driver.backends.macos.iokit_client import IOKitClient
deadline = time.time() + 45
while time.time() < deadline:
    try:
        c = IOKitClient(); c.open()
        info = c.get_info()
        if info.device_id == 0x7551:
            print(f"egpu_replug: eGPU re-enumerated device=0x{info.device_id:04x} "
                  f"rev=0x{info.revision_id:02x}")
            sys.exit(0)
    except Exception:
        pass
    time.sleep(3)
print("egpu_replug: eGPU did NOT re-enumerate within 45s", file=sys.stderr)
sys.exit(1)
PY
rc=$?
[ $rc -eq 0 ] && echo "=== egpu_replug: ready ===" || echo "=== egpu_replug: FAILED ===" >&2
exit $rc
