# Claude Code Skills for ROCm Workspace

## Active Projects

### 1. HIP Remote Client (macOS Port)

**Status**: In Progress - 22% API coverage (102/461 APIs)
**Details**: See `hip-remote-status.md`

Remote HIP execution for macOS. Client library forwards HIP calls over TCP to Linux worker with AMD GPUs.

**Quick Resume Commands**:
```bash
# Ensure tunnel is up
pgrep -f "ssh -f -N -L 50052" || ssh -f -N -L 50052:localhost:18515 sharkmi300x

# Check worker status
ssh sharkmi300x 'pgrep -f hip-worker && echo "Worker running"'

# Run all tests
cd /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/build
TF_WORKER_HOST=localhost TF_WORKER_PORT=50052 ctest --output-on-failure
```

**Last Working On**: Graph Node APIs (completed and tested)
**Next**: Add more device/memory APIs

---

### 2. hipSOLVER FP32 Cholesky Accuracy

**Status**: Planned (see plan file)
**Location**: Remote sharkmi300x

Fix FP32 Cholesky producing ~1e-2 max error by using FP64 accumulation for dot products.

**Plan File**: `/Users/setupuser/.claude/plans/async-cuddling-wreath.md`

---

## Remote Systems

### sharkmi300x
- 8x AMD MI300X GPUs
- HIP worker runs on port 18515
- SSH tunnel: localhost:50052 → sharkmi300x:18515
- ROCm installed, pytorch container available

---

## Useful Commands

### Git (TheRock repo)
```bash
cd /Users/setupuser/github/TheRock
git status
git log --oneline -10
```

### Build hip-remote-client
```bash
cd /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/build
ninja
```

### Full sync and rebuild workflow
```bash
# 1. Build locally
cd /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/build && ninja

# 2. Sync to remote
rsync -av /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-worker/ sharkmi300x:/home/anush/github/TheRock/rocm-systems/projects/hip-remote-worker/
rsync -av /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/include/ sharkmi300x:/home/anush/github/TheRock/rocm-systems/projects/hip-remote-client/include/

# 3. Rebuild worker
ssh sharkmi300x 'cd /home/anush/github/TheRock/build-hip-worker && ninja'

# 4. Restart worker
ssh sharkmi300x 'killall -9 hip-worker 2>/dev/null || true; cd /home/anush/github/TheRock/build-hip-worker && TF_WORKER_PORT=18515 TF_DEBUG=1 ./hip-worker </dev/null > /tmp/hip-worker.log 2>&1 &'

# 5. Run tests
TF_WORKER_HOST=localhost TF_WORKER_PORT=50052 ctest --output-on-failure
```
