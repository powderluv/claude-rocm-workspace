#!/usr/bin/env python3
# Isolate torch.cat as the FIRST GPU op (fresh queue) to tell whether cat itself
# wedges (0x1000) or the broadened baseline only wedged after many ops (multi-op
# accumulation / the open multi-op-chain question).
import sys
import torch

dev = "cuda"
print(f"torch {torch.__version__} device_count={torch.cuda.device_count()}", flush=True)
x = torch.arange(8, dtype=torch.float32).to(dev)
y = torch.cat([x, x])
shape = y.cpu().shape[0]
vals = y.cpu().tolist()
ok = shape == 16 and vals == [0, 1, 2, 3, 4, 5, 6, 7] * 2
print(f"cat shape={shape} vals_ok={vals == [0,1,2,3,4,5,6,7]*2}", flush=True)
print("CAT PASS" if ok else "CAT FAIL", flush=True)
sys.exit(0 if ok else 1)
