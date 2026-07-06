#!/usr/bin/env python3
# Non-scratch PyTorch baseline on the gfx1201 eGPU. Inputs generated on CPU and
# moved to device so we test the target ops (add/mul/matmul/reduction), not RNG.
# Reductions (sum/mean/max) exercise the #13 kernarg-translation fix. Avoids
# register-spilling/scratch ops (deferred, #15/#24).
import sys
import torch

dev = "cuda"
results = []


def rec(name, ok):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}", flush=True)


print(f"torch {torch.__version__} hip={torch.version.hip} device_count={torch.cuda.device_count()}", flush=True)

# 1. elementwise add (known-good baseline)
a = torch.ones(4, device=dev)
rec("ones+1", (a + 1).cpu().tolist() == [2, 2, 2, 2])

# 2. pointwise float ops
x = torch.arange(8, dtype=torch.float32).to(dev)
rec("mul", (x * 2).cpu().tolist() == [0, 2, 4, 6, 8, 10, 12, 14])
rec("sub", (x - 1).cpu().tolist() == [-1, 0, 1, 2, 3, 4, 5, 6])
rec("abs", (x - 4).abs().cpu().tolist() == [4, 3, 2, 1, 0, 1, 2, 3])

# 3. comparison (bool output)
rec("le", (x <= 3).cpu().tolist() == [True] * 4 + [False] * 4)

# 4. matmul (hipBLASLt/rocBLAS); verify against CPU ref (allclose runs on CPU tensors)
A = torch.randn(128, 128).to(dev)
B = torch.randn(128, 128).to(dev)
gpu = (A @ B).cpu()
ref = A.cpu() @ B.cpu()
rec("matmul128", torch.allclose(gpu, ref, atol=1e-3))

# 5. native reductions (the #13 kernarg fix; .item() syncs the scalar result)
s = torch.arange(100, dtype=torch.float32).to(dev)
rec("sum", abs(s.sum().item() - 4950.0) < 1e-3)
rec("mean", abs(s.mean().item() - 49.5) < 1e-3)
rec("max", abs(s.max().item() - 99.0) < 1e-3)

# 6. more common pointwise / memory ops (non-scratch)
rec("relu", (x - 4.0).relu().cpu().tolist() == [0, 0, 0, 0, 0, 1, 2, 3])
rec("neg", (-x).cpu().tolist() == [0, -1, -2, -3, -4, -5, -6, -7])
M = torch.arange(6, dtype=torch.float32).reshape(2, 3).to(dev)
rec("transpose", M.t().contiguous().cpu().tolist() == [[0, 3], [1, 4], [2, 5]])
rec("cat", torch.cat([x, x]).cpu().shape[0] == 16)
rec("reshape", x.reshape(2, 4).cpu().tolist() == [[0, 1, 2, 3], [4, 5, 6, 7]])
rec("slice", x[2:6].cpu().tolist() == [2, 3, 4, 5])
bc = (x.reshape(8, 1) + x.reshape(1, 8)).cpu()
rec("broadcast_add", bc[1][1].item() == 2.0 and bc[7][7].item() == 14.0)

npass = sum(1 for _, ok in results if ok)
print(f"=== {npass}/{len(results)} passed ===", flush=True)
sys.exit(0 if npass == len(results) else 1)
