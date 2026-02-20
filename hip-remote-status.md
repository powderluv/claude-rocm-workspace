# HIP Remote Client - macOS Port Status

## Overview

Remote HIP execution client library for macOS. Forwards HIP API calls over TCP to a worker running on Linux with AMD GPUs.

## Current State (2026-02-20)

### API Coverage
- **Implemented**: 167 APIs (was 162, was 159, was 153)
- **Total HIP APIs**: 461
- **Coverage**: ~36%

### Validation Status
- **macOS Client**: All 12 test suites passing (278.25s total)
- **Linux Client**: Worker updated and tested
- **Branch**: `users/powderluv/add-hip-remote-projects` (rebased on develop)
- **Worker**: Running on sharkmi300x (AMD Instinct MI300X)

### Test Suites (All Passing - 12 suites)
```
1. hip_remote_basic          - Basic connectivity and device queries (21.02s)
2. hip_remote_extended       - Memory ops (hipMallocAsync, hipMemcpy2D, etc.) (27.97s)
3. hip_remote_phase2         - Device limits, peer access (35.64s)
4. hip_remote_graphs         - Basic graph capture/instantiate/launch (26.39s)
5. hip_remote_ipc            - IPC memory and event handles (12.20s)
6. hip_remote_mempool        - Memory pool APIs (28.43s)
7. hip_remote_graph_nodes    - Graph node APIs (memcpy, memset, empty, dependencies) (55.22s)
8. hip_remote_device_apis    - Device driver and config APIs (19.11s)
9. hip_remote_memory_apis    - Host registration and memory management (9.08s)
10. hip_remote_quick_wins    - Quick Win APIs (stream priority, capture, pointer attrs) (19.50s)
11. hip_remote_func_attrs    - Function attributes for kernel tuning (7.11s)
12. hip_remote_graph_advanced - Advanced graph APIs (clone, update, query) (16.57s)
```

### Recently Completed
1. **Advanced Graph APIs** (5 APIs):
   - hipGraphClone - Clone an existing graph
   - hipGraphNodeGetDependencies - Get dependency nodes of a graph node
   - hipGraphNodeGetDependentNodes - Get nodes that depend on this node
   - hipGraphExecUpdate - Update instantiated graph from original graph
   - hipGraphExecKernelNodeSetParams - Update kernel node params in exec graph

2. **Function Attributes APIs** (3 APIs):
   - hipFuncGetAttributes - Get all function attributes (shared mem, registers, etc.)
   - hipFuncSetAttribute - Set single function attribute
   - hipFuncSetCacheConfig - Set cache configuration for a function

3. **Quick Win APIs** (6 functional APIs):
   - hipDeviceGetStreamPriorityRange - Get stream priority range
   - hipSetValidDevices - Set valid device array
   - hipChooseDevice - Choose best matching device (stub returns device 0)
   - hipStreamGetCaptureInfo - Get stream capture status and ID
   - hipStreamUpdateCaptureDependencies - Update graph capture dependencies
   - hipPointerGetAttribute - Get single pointer attribute
   - hipMemcpyPeer / hipMemcpyPeerAsync - Already existed
   - hipLaunchCooperativeKernelMultiDevice - Stub (returns not supported)

4. **Host Memory Registration** (7 APIs): hipHostRegister, hipHostUnregister, hipHostGetDevicePointer, hipHostGetFlags, hipHostAlloc, hipHostFree, hipMemAllocPitch
5. **Unified Memory Management** (4 APIs): hipMemAdvise, hipMemPrefetchAsync, hipMemRangeGetAttribute, hipMemRangeGetAttributes
6. **Device Driver APIs** (7 APIs): hipDeviceGet, hipDeviceGetName, hipDeviceTotalMem, hipDeviceGetPCIBusId, hipDeviceGetByPCIBusId, hipDeviceComputeCapability, hipDeviceGetUuid
7. **Device Config APIs** (8 APIs): hipDeviceGetCacheConfig, hipDeviceSetCacheConfig, hipDeviceGetSharedMemConfig, hipDeviceSetSharedMemConfig, hipGetDeviceFlags, hipSetDeviceFlags, hipDeviceGetP2PAttribute
8. **IPC APIs** (5 APIs): hipIpcGetMemHandle, ipIpcOpenMemHandle, hipIpcCloseMemHandle, hipIpcGetEventHandle, hipIpcOpenEventHandle
9. **Memory Pool APIs** (9 APIs): hipMemPoolCreate, hipMemPoolDestroy, hipMemPoolSetAttribute, hipMemPoolGetAttribute, hipMallocFromPoolAsync, hipMemPoolTrimTo, hipDeviceGetDefaultMemPool, hipDeviceSetMemPool, hipDeviceGetMemPool
10. **Graph Node APIs** (8+ APIs): hipGraphAddEmptyNode, hipGraphAddMemcpyNode1D, hipGraphAddMemsetNode, hipGraphAddDependencies, hipGraphGetNodes, hipGraphGetRootNodes, hipGraphNodeGetType, hipGraphDestroyNode
11. **Context APIs [Deprecated]** (21 APIs): hipCtxCreate, hipCtxDestroy, hipCtxSetCurrent, hipCtxGetCurrent, hipCtxPushCurrent, hipCtxPopCurrent, hipCtxGetDevice, hipCtxGetApiVersion, hipCtxGetCacheConfig, hipCtxSetCacheConfig, hipCtxGetSharedMemConfig, hipCtxSetSharedMemConfig, hipCtxSynchronize, hipCtxGetFlags, hipCtxEnablePeerAccess, hipCtxDisablePeerAccess, hipDevicePrimaryCtxGetState, hipDevicePrimaryCtxRetain, hipDevicePrimaryCtxRelease, hipDevicePrimaryCtxReset, hipDevicePrimaryCtxSetFlags

## File Locations

### Client (macOS)
- Source: `/Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/`
- Build: `/Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/build/`
- Headers:
  - `include/hip_remote/hip_remote_protocol.h` - Wire protocol definitions
  - `include/hip_remote/hip_remote_client.h` - Client API and HIP type definitions
- Sources:
  - `src/hip_client.c` - Connection management
  - `src/hip_api_device.c` - Device APIs
  - `src/hip_api_memory.c` - Memory APIs (including IPC, mempool)
  - `src/hip_api_stream.c` - Stream and event APIs
  - `src/hip_api_module.c` - Module and kernel launch APIs
  - `src/hip_api_graph.c` - Graph APIs

### Worker (Linux - sharkmi300x)
- Source: `/home/anush/github/TheRock/rocm-systems/projects/hip-remote-worker/`
- Build: `/home/anush/github/TheRock/build-hip-worker/`
- Main: `src/hip_worker_main.c` - Request handlers

### Client (Linux - for testing)
- Source: `/home/anush/github/TheRock/rocm-systems/projects/hip-remote-client/`
- Build: `/home/anush/github/TheRock/rocm-systems/projects/hip-remote-client/build-linux/`

## Development Workflow

### Build Client (macOS)
```bash
cd /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/build
ninja
```

### Sync to Remote
```bash
rsync -av /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-worker/ sharkmi300x:/home/anush/github/TheRock/rocm-systems/projects/hip-remote-worker/
rsync -av /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/include/ sharkmi300x:/home/anush/github/TheRock/rocm-systems/projects/hip-remote-client/include/
```

### Build Worker (Remote)
```bash
ssh sharkmi300x 'cd /home/anush/github/TheRock/build-hip-worker && ninja'
```

### Start Worker (Remote)
```bash
ssh sharkmi300x 'killall -9 hip-worker 2>/dev/null || true; cd /home/anush/github/TheRock/build-hip-worker && TF_WORKER_PORT=18515 TF_DEBUG=1 ./hip-worker </dev/null > /tmp/hip-worker.log 2>&1 &'
```

### SSH Tunnel
```bash
# Check if running
pgrep -f "ssh -f -N -L 50052"

# Start if needed
ssh -f -N -L 50052:localhost:18515 sharkmi300x
```

### Run Tests (macOS)
```bash
cd /Users/setupuser/github/TheRock/rocm-systems/projects/hip-remote-client/build
TF_WORKER_HOST=localhost TF_WORKER_PORT=50052 ctest --output-on-failure
```

### Run Tests (Linux)
```bash
ssh sharkmi300x 'cd /home/anush/github/TheRock/rocm-systems/projects/hip-remote-client/build-linux && TF_WORKER_HOST=localhost TF_WORKER_PORT=18515 ctest --output-on-failure'
```

## Opcode Ranges (hip_remote_protocol.h)

| Range | Category |
|-------|----------|
| 0x01xx | Device operations |
| 0x011C | hipDeviceGetStreamPriorityRange |
| 0x011A-0x011B | hipSetValidDevices, hipChooseDevice |
| 0x02xx | Memory operations |
| 0x0232 | hipPointerGetAttribute |
| 0x0240-0x0244 | IPC operations |
| 0x0250-0x0258 | Memory Pool operations |
| 0x0260-0x0266 | Host Memory Registration |
| 0x0270-0x0273 | Unified Memory Management |
| 0x09xx | Context operations [Deprecated] |
| 0x03xx | Stream operations |
| 0x0309-0x030A | hipStreamGetCaptureInfo, hipStreamUpdateCaptureDependencies |
| 0x04xx | Event operations |
| 0x05xx | Module operations |
| 0x0513 | hipLaunchCooperativeKernelMultiDevice |
| 0x06xx | Graph operations |
| 0x0640-0x064E | Graph Node operations |
| 0x0720-0x072C | Graph operations (create, instantiate, clone, update) |
| 0x07xx | Runtime info |

## Next Steps (Suggested Priority)

1. **Additional Graph APIs** (~35 missing): More graph manipulation and update APIs
2. **Texture/Surface APIs** (~40): Lower priority unless needed for specific workloads
3. **Array & Surface Management** (~15): hipArrayCreate, hipMallocArray, hipMemcpyToArray, etc.
4. **Virtual Memory Management** (~13): hipMemAddressReserve, hipMemCreate, hipMemMap, etc.
5. **Profiler APIs** (~10): hipProfilerStart, hipProfilerStop, etc.

## Notes

- Worker uses `-p PORT` flag or `TF_WORKER_PORT` env var (not `--port`)
- Graph node opcodes were moved to 0x064x to avoid collision with 0x0700 (HIP_OP_RUNTIME_GET_VERSION)
- Variable-length messages used for graph APIs with dependency arrays
- Host memory registration APIs work but have limitations in remote mode (memory on worker, not client)
- Unified memory APIs (hipMemAdvise, hipMemPrefetchAsync, etc.) implemented but require shared address space
- Both macOS and Linux clients validated with full test suite
- Device API opcodes: 0x010C-0x011C (expanded with Quick Wins)
- Host memory opcodes: 0x0260-0x0266
- Unified memory opcodes: 0x0270-0x0273
- Context API opcodes: 0x0900-0x0914 (deprecated, some operations have limited support)
- Quick Win API opcodes: 0x011C, 0x011A-0x011B (device), 0x0232 (memory), 0x0309-0x030A (stream), 0x0513 (module)
- Advanced Graph API opcodes: 0x0728-0x072C (clone, query dependencies, exec update)
- hipChooseDevice is simplified (returns device 0) - full implementation would require complete hipDeviceProp_t
- hipLaunchCooperativeKernelMultiDevice returns hipErrorNotSupported (complex multi-device coordination)
- hipGraphGetEdges already existed at 0x0647, not reimplemented

## Recent Commits (2026-02-20)

- Advanced Graph APIs - complete implementation (5 new APIs)
- `d42861e5f2` - Function Attributes APIs - complete implementation
- `ae578710af` - Quick Win APIs - worker-side implementation and tests
- `5e88a4bdc1` - Quick Win APIs - client-side implementation
- Added 17 new APIs total (5 Graph + 3 Function Attributes + 9 Quick Win)
- All 12 test suites passing on macOS client and Linux worker
- Coverage increased from 33% to 36%
