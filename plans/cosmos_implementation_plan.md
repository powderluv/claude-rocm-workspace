# Cosmos Implementation Plan (CDNA v1, iOS-Simulator UX)

## Summary
- Build a greenfield Cosmos project under `TheRock/cosmos/`, Linux-hosted, that runs unmodified HIP/PyTorch/vLLM applications in containerized simulated nodes, with ROCm userspace anchored on a Cosmos-compatible `libhsakmt` over an `amdgpu_lite`-style device contract and host CPU passthrough.
- Keep scope fixed to AMD CDNA v1: emulate an MI300X-like system at cluster scale (8+ nodes x 8 GPUs/node), with AMD-only architecture and no NVIDIA backend.
- Deliver two fidelity modes: functional ISA-correct execution for broad compatibility, plus cycle-accurate mode for selected hot kernel classes (target slowdown budget `<=1000x`).
- Ship open-source from day 1, with `cosmosctl` + GUI launcher experience modeled after iOS simulator workflows.
- Treat the 6-12 month window as alpha/beta milestones; scope-complete v1 is planned as a longer critical path under current staffing.

## Key Implementation Changes
- Repository placement and integration:
  - All Cosmos source, schemas, tooling, and docs live under `TheRock/cosmos/`.
  - Cosmos lives in the TheRock monorepo, but it is not built as part of the top-level TheRock CMake/product graph.
  - Cosmos owns its own packaging, test, and release entry points while reusing monorepo CI infrastructure where helpful.
  - Runtime contract: Cosmos must be able to create an environment, `pip install` prebuilt ROCm, PyTorch, and vLLM wheels, overlay a Cosmos-compatible `libhsakmt`, and run against those published binaries without requiring a full TheRock source build.
- Create a simulator control plane with:
  - `cosmosctl` CLI (`create-profile`, `boot`, `run`, `attach`, `logs`, `snapshot`, `replay`, `shutdown`).
  - Desktop GUI launcher for device/profile selection, boot state, logs, debugger attach, and replay controls.
  - A daemonized orchestration service (`cosmosd`) exposing gRPC/REST control endpoints used by CLI/GUI/CI.
- Define stable public interfaces/types:
  - **Device Profile Schema (YAML/JSON):** GPU count, CU/LDS/register topology, HBM config, xGMI/PCIe fabric, node count, RCCL fabric params, timing-model toggles.
  - **Workload Manifest Schema:** container image, ROCm runtime version pin, launch command, env vars, mounted artifacts, tracing flags.
  - **Trace/Event Format:** queue ops, dispatch, memory transactions, sync primitives, collectives, timestamps, and deterministic replay checkpoints.
  - **Primary ROCm Compatibility Contract:** patched `libhsakmt` speaking an `amdgpu_lite`-style memory/queue/signal/topology interface from C++ userspace.
  - **Fallback Compatibility Contract:** broader KFD/DRM ioctl emulation only for gaps that cannot be absorbed at the `libhsakmt` boundary.
- Build runtime architecture:
  - Node execution: one container per simulated node, published ROCm user-space stack plus a Cosmos-supplied `libhsakmt` overlay inside each node.
  - ROCm interface layer: patched `libhsakmt` targets `amdgpu_lite` semantics directly instead of `/dev/kfd` as the primary v1 integration path.
  - GPU simulation backend: CDNA ISA parser/decoder/executor from GPUOpen specs, wavefront execution semantics, memory/atomics/barrier correctness.
  - Timing backend: pluggable cycle model for selected kernels (GEMM/reduction/collective primitives) with hardware calibration hooks.
  - Cluster model: multi-node virtual fabric with configurable latency/bandwidth/contention for RCCL and distributed training.
- Release phases (scope fixed):
  1. **Phase A (Months 0-3):** control-plane skeleton, profile schema v1, container node lifecycle, initial `libhsakmt`/`amdgpu_lite` compatibility scaffold.
  2. **Phase B (Months 3-7):** CDNA functional ISA core, single-node ROCm app execution, HIP conformance harness, basic GUI.
  3. **Phase C (Months 7-12):** multi-GPU/node + multi-node orchestration, RCCL path, record/replay core, alpha/beta developer workflow.
  4. **Phase D (Months 12+):** scope-complete scale-out (8+ x 8), calibrated cycle mode for hot kernels, performance validation hardening.
- Plan v2 extension point now: RDNA backend plug-in contract kept compatible, but RDNA implementation deferred to v2.

## ROCm Interface Strategy

- Primary v1 path: modify `libhsakmt` in C++ to target `amdgpu_lite` directly, and make Cosmos provide the corresponding low-level device semantics.
- Intended compatibility seam: higher-level HIP/PyTorch/vLLM applications remain unchanged; `libhsakmt` is the main adaptation point.
- Wheel delivery model: install published ROCm/PyTorch/vLLM wheels, then overlay a Cosmos-supplied `libhsakmt` package or shared library replacement.
- Fallback path: introduce targeted KFD/DRM emulation only when runtime bringup exposes behavior that cannot be expressed cleanly through the `libhsakmt` + `amdgpu_lite` contract.
- Validation ladder: cluster topology boot -> virtual GPU queue/memory -> first synthetic dispatch -> `rocminfo` -> HSA runtime startup -> first HIP kernel -> PyTorch smoke -> vLLM smoke -> RCCL/multi-node.

## Bring-Up Milestones

These milestones are the concrete execution path for bringing up Cosmos. The simulator-native milestones come first; the ROCm-facing milestones follow once the virtual cluster and GPU emulator exist.

### Milestone A: Cluster topology boot
- Goal: prove Cosmos can boot and manage a deterministic simulated topology before any ROCm userspace is involved.
- Required Cosmos capabilities:
  - Device profile parsing for nodes, GPUs, and fabric links
  - `cosmosd` lifecycle for create, boot, status, logs, snapshot, and shutdown
  - Per-node container boot with injected synthetic GPU inventory
  - Stable node IDs, GPU IDs, and fabric IDs across reboots/replay
- Concrete deliverables:
  - `ClusterProfile`, `NodeProfile`, `GpuProfile`, and `FabricLinkProfile` data models with stable IDs
  - Runtime instance objects for `ClusterInstance`, `NodeInstance`, and `GpuInstance`
  - A topology loader that materializes the runtime graph from the profile schema
  - A boot-state machine in `cosmosd` covering create, boot, ready, failed, snapshot, replay, and shutdown
  - Boot smoke tests and golden topology snapshots for a minimal `2 nodes x 2 GPUs` profile
- Validation:
  - `cosmosctl` boots and shuts down a minimal multi-node topology repeatably
  - Per-node logs show the expected simulated devices and links
  - Boot traces can be captured and replayed
- Exit criteria: a small simulated cluster can boot repeatably with no ROCm stack present.

### Milestone B: Virtual GPU device model
- Goal: make one virtual CDNA GPU exist as a simulator resource model independent of ROCm.
- Required Cosmos capabilities:
  - GPU memory regions (HBM, LDS, scratch, GPU VA)
  - Queue objects, signal objects, and lifecycle management
  - Internal topology and device property reporting
  - Trace events for alloc/map/queue/signal operations
- Concrete deliverables:
  - `VirtualGpuDevice` object with immutable architectural properties and mutable runtime state
  - Memory subsystem objects for `HbmRegion`, `LdsRegion`, `ScratchRegion`, `GpuVaSpace`, and allocation handles
  - Queue subsystem objects for `ComputeQueueState`, `DmaQueueState`, `SignalState`, and doorbell-equivalent notify state
  - A deterministic allocator for GPU virtual addresses and simulator-visible memory handles
  - Simulator-native queue/memory API tests that do not depend on HSA, HIP, or `libhsakmt`
- Validation:
  - Simulator-native tests can create a virtual GPU, allocate and map memory, create a queue, and signal/wait successfully
  - Resource lifecycle tests cover create, reuse, and teardown
- Exit criteria: simulator-native queue/memory smoke tests pass for one virtual GPU.

### Milestone C: First synthetic dispatch through the emulator
- Goal: prove the virtual GPU can execute a narrow packet/dispatch path and produce correct memory side effects before HIP or HSA enter the picture.
- Suggested first workloads:
  - synthetic no-op dispatch
  - write-one kernel
  - minimal vector-add-style workload with CPU-verified output
- Required Cosmos capabilities:
  - Narrow command submission path
  - Code object loading or equivalent internal kernel representation for the first workload subset
  - Dispatch completion, barriers, and basic memory visibility semantics
  - Deterministic trace capture around dispatch
- Concrete deliverables:
  - `DispatchSubmission`, `DispatchContext`, and `CompletionRecord` runtime objects
  - A narrow command parser for the first supported queue packet subset
  - An executable representation for the first synthetic kernels or a minimal code-object loader for that subset
  - A single-wave or otherwise intentionally constrained execution path that is easy to reason about and debug
  - Golden tests for no-op dispatch, memory writeback, and one simple arithmetic kernel
- Validation:
  - Synthetic workloads execute correctly against CPU-checked outputs
  - Dispatch traces show queue submission, execution, and completion in the expected order
- Exit criteria: the virtual GPU can execute the first non-ROCm compute workload end-to-end.

### Milestone D: Minimal clustered execution
- Goal: extend single-GPU simulation into a minimal multi-GPU and multi-node system before full RCCL/framework bring-up.
- Required Cosmos capabilities:
  - Multi-GPU/node addressing and topology modeling
  - Virtual xGMI/PCIe/fabric latency and bandwidth modeling
  - Basic inter-node copy or collective-style simulator primitives
  - Deterministic cross-node event ordering or sufficient replay hooks
- Concrete deliverables:
  - `FabricModel`, `FabricEndpoint`, and `TransferRoute` objects for intra-node and inter-node links
  - A minimal simulator scheduler or event engine that orders cross-node transfers deterministically
  - A first transport primitive for point-to-point copy, plus one collective-like primitive suitable for smoke testing
  - Cluster-level trace records for transfer start, transfer complete, and synchronization points
  - Synthetic distributed smoke tests for `GPU0 on node0 -> GPU0 on node1` transfer and one collective-style operation
- Validation:
  - A synthetic multi-node copy or collective smoke test runs across at least two simulated nodes
  - Cluster-level traces preserve enough information to replay distributed behavior
- Exit criteria: a cluster-level synthetic workload runs repeatably across simulated nodes.

### Milestone E: `rocminfo` bring-up
- Goal: prove the Cosmos `libhsakmt` path can initialize HSA, enumerate one simulated GPU, and report coherent topology/basic memory properties.
- Required Cosmos capabilities:
  - Device identity and topology reporting at the `amdgpu_lite` boundary
  - HSA agent enumeration data
  - Basic memory heap reporting
  - Minimal signal/event primitives needed during runtime startup
- Required `libhsakmt` capabilities:
  - Open the Cosmos device path
  - Query topology and memory properties through `amdgpu_lite`
  - Complete HSA runtime initialization without `/dev/kfd`
- Validation:
  - `rocminfo` runs successfully in the node container
  - One simulated CDNA GPU is reported with expected name, memory size, and agent properties
  - Startup trace is captured for replay/debug
- Exit criteria: `rocminfo` passes as the first end-to-end proof that the adapted `libhsakmt` can talk to Cosmos.

### Milestone F: HSA queue and memory bring-up
- Goal: prove raw HSA queue creation, memory allocation/mapping, signal handling, and queue submission all work over the Cosmos device contract.
- Required Cosmos capabilities:
  - Device memory allocation and free
  - GPU virtual address assignment and mapping
  - Queue creation/destruction
  - Doorbell or equivalent queue-notify semantics
  - Signal allocation, wait, and completion notification
- Required `libhsakmt` capabilities:
  - Translate HSA queue and memory operations onto `amdgpu_lite`
  - Support queue write pointer/read pointer handling and synchronization
  - Surface errors cleanly when unsupported features are hit
- Validation:
  - A focused HSA smoke harness can allocate memory, create a queue, submit a minimal packet stream, and observe completion through a signal
  - Memory lifecycle tests cover alloc, map, reuse, and free
  - Queue lifecycle tests cover create, submit, synchronize, and destroy
- Exit criteria: a dedicated HSA-level smoke test passes without involving HIP.

### Milestone G: First HIP kernel
- Goal: prove the full path from HIP runtime through HSA into the Cosmos execution core by running one trivial kernel correctly.
- Suggested first workload:
  - a write-one kernel
  - or a minimal vector add kernel with CPU-verified output
- Required software path:
  - HIP runtime startup succeeds on top of the adapted `libhsakmt`
  - `hipMalloc`, `hipMemcpy`, kernel launch, and synchronization succeed for the chosen smoke kernel
- Validation:
  - Kernel output matches CPU expectations
  - Trace shows queue submission, dispatch, and completion in the expected order
  - Failure modes are surfaced with enough detail to debug runtime vs simulator issues
- Exit criteria: one HIP kernel executes successfully end-to-end inside a simulated node.

### Milestone H: PyTorch wheel smoke
- Goal: prove the wheel-based runtime contract is viable for real frameworks, not just simulator-native tests, ROCm tools, and HIP samples.
- Required environment:
  - published ROCm wheel set
  - Cosmos `libhsakmt` overlay
  - pinned PyTorch wheel compatible with the selected ROCm runtime
- Suggested smoke checks:
  - import `torch`
  - detect the simulated GPU device
  - allocate a tensor on the GPU
  - run one simple eager op and one small matmul or elementwise op
  - synchronize and validate output on CPU
- Validation:
  - install flow works from a fresh virtual environment
  - no manual source builds are required
  - logs and traces are preserved for triage
- Exit criteria: a published PyTorch wheel can install and run a minimal GPU workload through Cosmos in a clean environment.

## Repository Layout in TheRock

```text
TheRock/
  cosmos/
    pyproject.toml
    README.md
    compat/
      libhsakmt/
      amdgpu_lite/
    docs/
    schemas/
    proto/
    lib/
      control/
      orchestration/
      device/
      sim/
        topology/
        gpu/
        memory/
        queue/
        exec/
        fabric/
        timing/
        trace/
        replay/
      timing/
      trace/
    tools/
      cosmosctl/
      cosmosd/
    gui/
    container/
    native/
      CMakeLists.txt
    tests/
      unit/
      integration/
      conformance/
      replay/
      perf/
```

## Simulator Source Architecture

The simulator core should be organized around the execution-critical subsystems, not around ROCm-facing APIs.

```text
TheRock/cosmos/
  lib/
    sim/
      topology/
        cluster_profile.h
        topology_loader.h
        cluster_instance.h
      gpu/
        gpu_properties.h
        virtual_gpu_device.h
        signal_state.h
      memory/
        memory_region.h
        gpu_va_space.h
        allocation_table.h
        lds_model.h
      queue/
        queue_state.h
        doorbell_state.h
        packet_stream.h
        submission_queue.h
      exec/
        decoder/
        ir/
        loader/
        interpreter/
        jit/
        dispatch/
      fabric/
        fabric_model.h
        transfer_engine.h
        collective_primitives.h
      timing/
        timing_model.h
        event_scheduler.h
      trace/
        trace_sink.h
        event_types.h
      replay/
        replay_log.h
        replay_engine.h
```

### Topology module: `lib/sim/topology/`
- Responsibility: parse profile data and materialize the runtime cluster graph.
- Main data structures:
  - `ClusterProfile`
  - `NodeProfile`
  - `GpuProfile`
  - `FabricLinkProfile`
  - `ClusterInstance`
  - `NodeInstance`
- Main APIs:
  - `LoadClusterProfile(path) -> ClusterProfile`
  - `MaterializeCluster(profile) -> ClusterInstance`
  - `GetNode(node_id) -> NodeInstance*`
  - `GetGpu(node_id, gpu_id) -> VirtualGpuDevice*`

### GPU module: `lib/sim/gpu/`
- Responsibility: own architectural properties and per-GPU runtime state.
- Main data structures:
  - `GpuProperties`
  - `VirtualGpuDevice`
  - `SignalState`
  - `DeviceRuntimeState`
- Main APIs:
  - `VirtualGpuDevice::Initialize(props)`
  - `VirtualGpuDevice::CreateComputeQueue(desc)`
  - `VirtualGpuDevice::CreateDmaQueue(desc)`
  - `VirtualGpuDevice::CreateSignal(desc)`
  - `VirtualGpuDevice::QueryProperties()`

### Memory module: `lib/sim/memory/`
- Responsibility: implement HBM, LDS, scratch, GPU virtual address space, and allocation/mapping semantics.
- Main data structures:
  - `MemoryRegion`
  - `HbmRegion`
  - `LdsRegion`
  - `ScratchRegion`
  - `GpuVaSpace`
  - `AllocationHandle`
  - `AllocationTable`
- Main APIs:
  - `Alloc(region_kind, size, align, flags) -> AllocationHandle`
  - `MapGpuVa(handle, va_hint) -> GpuVaRange`
  - `UnmapGpuVa(handle)`
  - `Read(va, size)`
  - `Write(va, bytes)`
  - `Copy(dst_va, src_va, size)`

### Queue module: `lib/sim/queue/`
- Responsibility: queue lifecycle, submission buffering, write/read pointer semantics, and doorbell-equivalent notification.
- Main data structures:
  - `QueueDescriptor`
  - `ComputeQueueState`
  - `DmaQueueState`
  - `DoorbellState`
  - `PacketStream`
  - `DispatchSubmission`
- Main APIs:
  - `CreateQueue(desc) -> QueueId`
  - `DestroyQueue(queue_id)`
  - `SubmitPackets(queue_id, PacketStream)`
  - `RingDoorbell(queue_id)`
  - `PollCompletion(queue_id) -> CompletionRecord`

### Execution module: `lib/sim/exec/`
- Responsibility: decode, lower, execute, and optimize the first supported ISA subset and later the broader CDNA execution path.
- Submodules:
  - `decoder/`: generated instruction tables from GPUOpen machine-readable ISA
  - `ir/`: compact simulator IR and superinstructions
  - `loader/`: code object ingestion and kernel metadata extraction
  - `interpreter/`: wavefront/workgroup interpreter
  - `jit/`: hot-block lowering and compilation
  - `dispatch/`: dispatch context, launch, and completion bookkeeping
- Main data structures:
  - `DecodedInstruction`
  - `BasicBlock`
  - `KernelImage`
  - `DispatchContext`
  - `CompletionRecord`
- Main APIs:
  - `DecodeKernel(image) -> KernelImage`
  - `BuildIr(kernel) -> IrModule`
  - `LaunchDispatch(queue, dispatch_ctx)`
  - `StepWave(dispatch_ctx)`
  - `RunToCompletion(dispatch_ctx) -> CompletionRecord`

### Fabric module: `lib/sim/fabric/`
- Responsibility: model intra-node and inter-node connectivity, copies, and collective-style communication primitives.
- Main data structures:
  - `FabricModel`
  - `FabricEndpoint`
  - `TransferRoute`
  - `TransferRequest`
  - `CollectivePlan`
- Main APIs:
  - `RouteTransfer(src_gpu, dst_gpu, bytes) -> TransferRoute`
  - `SubmitTransfer(request)`
  - `SubmitCollective(plan)`
  - `PollFabricEvents()`

### Timing module: `lib/sim/timing/`
- Responsibility: keep functional execution fast while optionally layering calibrated timing and event scheduling on top.
- Main data structures:
  - `TimingMode`
  - `TimingModel`
  - `EventScheduler`
  - `ScheduledEvent`
- Main APIs:
  - `Schedule(event, timestamp)`
  - `AdvanceUntil(timestamp)`
  - `EstimateDispatchCost(dispatch_ctx)`
  - `EstimateTransferCost(route, bytes)`

### Trace and replay modules: `lib/sim/trace/` and `lib/sim/replay/`
- Responsibility: capture deterministic traces and replay simulator state transitions for debugging and CI.
- Main data structures:
  - `TraceEvent`
  - `TraceSink`
  - `ReplayLog`
  - `ReplayEngine`
- Main APIs:
  - `EmitTrace(event)`
  - `FlushTrace(run_id)`
  - `LoadReplay(path) -> ReplayLog`
  - `ReplayUntil(checkpoint_id)`

## Execution Strategy

- Decode from GPUOpen ISA data offline, not at runtime.
- Functional mode should execute at wavefront and workgroup granularity, not per-thread object granularity.
- The default path should be a fast interpreter with IR caching and superinstructions.
- JIT should be reserved for hot blocks or traces after correctness is established.
- Timing should stay layered and optional, with event-driven modeling for cluster/fabric behavior and detailed calibration only for selected kernels.

## Concrete Work Breakdown

### Workstream 0: Monorepo scaffolding and wheel-based runtime contract (Phase A)
- Create `TheRock/cosmos/` with its own `pyproject.toml`, README, test directories, install layout, and developer entry points.
- Keep Cosmos out of the top-level TheRock `add_subdirectory(...)` build graph; any native helpers build through Cosmos-owned entry points only.
- Define the supported environment matrix for prebuilt ROCm, PyTorch, and vLLM wheels, including pinned version sets and the matching Cosmos `libhsakmt` overlay rules.
- Implement bootstrap flows that create a virtual environment, `pip install` the required published wheels, install the Cosmos `libhsakmt` overlay, and launch `cosmosctl` and `cosmosd`.
- Add CI smoke coverage for environment creation, wheel installation, CLI/daemon startup, and a no-GPU host-only simulator smoke run.

**Exit criteria:** a fresh checkout can enter `TheRock/cosmos/`, install Cosmos plus pinned ROCm/PyTorch/vLLM wheels and the matching Cosmos `libhsakmt` overlay, and launch the basic Cosmos workflow without building TheRock itself.

### Workstream 1: Public contracts and schemas (Phase A)
- Define `device_profile.schema.json` for GPU/node/fabric topology and timing toggles.
- Define `workload_manifest.schema.json` for container image, command, mounts, env, traces, and replay settings.
- Define gRPC/REST control APIs used by CLI, GUI, and CI.
- Define trace/replay event schema with stable versioning rules.
- Add schema validation tests and golden examples checked into the repo.

**Exit criteria:** profiles, manifests, and trace files validate in CI and are stable enough for CLI/daemon integration.

### Workstream 2: Control plane and orchestration service (Phase A)
- Implement `cosmosd` lifecycle: create profile, boot node set, run workload, collect logs, shutdown, snapshot, replay.
- Implement `cosmosctl` commands around the daemon APIs.
- Add persistent state for profiles, booted simulator instances, logs, and snapshots.
- Add structured logging, health checks, and failure reporting suitable for CI automation.
- Add auth model for local-only bringup first, with remote control left behind an interface boundary.

**Exit criteria:** Milestone A (cluster topology boot) succeeds for a small deterministic topology under `cosmosctl`/`cosmosd`.

### Workstream 3: Container node runtime (Phase A-B)
- Build node image strategy for published ROCm user-space stacks pinned to specific runtime versions plus a Cosmos `libhsakmt` overlay.
- Implement one-container-per-node orchestration with mounted artifacts, env injection, shared-library overlay handling, and log capture.
- Provide host CPU passthrough for host code while routing GPU-facing interactions into Cosmos.
- Add filesystem snapshotting and deterministic startup state capture.
- Define image/version compatibility policy between daemon, schemas, and ROCm user-space payloads.

**Exit criteria:** a node container can launch published ROCm/HIP user-space binaries with the Cosmos `libhsakmt` overlay under Cosmos control with repeatable startup behavior.

### Workstream 4: `libhsakmt` + `amdgpu_lite` ROCm compatibility path (Phase A-B)
- Land the C++ `libhsakmt` changes needed to target `amdgpu_lite` directly for topology discovery, memory allocation, queue creation, signal/event handling, and synchronization.
- Define the Cosmos-side `amdgpu_lite` contract required by that path: device info, memory alloc/map, queue setup/submission, signal/event wiring, and topology exposure.
- Add compatibility shims for Milestone E (`rocminfo`) and Milestone F (raw HSA queue/memory bring-up) using `libhsakmt` over `amdgpu_lite`.
- Establish tracing hooks at the `amdgpu_lite` request boundary so replay and debug data are captured early.
- Keep full KFD/DRM ioctl emulation as a fallback only for behaviors that cannot be expressed cleanly through the `libhsakmt` path.
- Add differential tests against the real `amdgpu_lite` path where practical.

**Exit criteria:** Milestone E (`rocminfo`) and Milestone F (HSA queue/memory) succeed inside a simulated node using patched `libhsakmt`, without requiring changes in higher-level frameworks.

### Workstream 5: Functional CDNA execution core (Phase B)
- Implement CDNA v1 ISA decode/dispatch/execute pipeline with wavefront semantics.
- Implement memory model correctness for global/LDS/register/atomics/barriers.
- Build loader support for code objects produced by the standard ROCm toolchain.
- Start with simulator-native queue/memory/dispatch tests before depending on HSA or HIP.
- Add instruction- and kernel-level differential tests against spec-derived vectors and hardware outputs.
- Prioritize correctness and determinism before performance.

**Exit criteria:** Milestone B (virtual GPU device model) and Milestone C (first synthetic dispatch) succeed, establishing a usable single-GPU emulator core.

### Workstream 6: ROCm compatibility and developer workflow (Phase B-C)
- Run HIP runtime/compiler test suites against Cosmos single-node execution through the `libhsakmt` overlay path.
- Expand from Milestones E/F into Milestone G (first HIP kernel), then land Milestone H (PyTorch wheel smoke) from published wheels.
- Implement debugger/profiler attach surfaces exposed by `cosmosctl` and the GUI.
- Add error surfacing for unsupported ioctls, ISA features, and runtime behaviors.
- Produce developer docs for creating profiles, booting nodes, running apps, and reading traces.

**Exit criteria:** Milestone G (first HIP kernel) and Milestone H (PyTorch wheel smoke) succeed, and developers can use documented CLI flows to run and debug those workloads.

### Workstream 7: Multi-GPU, multi-node, and RCCL fabric (Phase C)
- Extend orchestration from one node to multi-GPU single-node and then multi-node topologies.
- Implement virtual xGMI/PCIe/fabric models with configurable latency, bandwidth, and contention.
- Add RCCL-specific topology and collective behavior support.
- Add distributed workload manifests and launch coordination across node containers.
- Validate against representative multi-rank collectives and small distributed training jobs.

**Exit criteria:** Milestone D (minimal clustered execution) succeeds, and Cosmos can then scale toward deterministic RCCL-backed workloads across simulated nodes.

### Workstream 8: Record/replay and GUI workflow (Phase C)
- Implement trace capture for queue submissions, memory events, sync operations, collectives, and checkpoints.
- Implement deterministic replay from saved traces and snapshots.
- Build a basic desktop GUI for profile selection, boot state, logs, attach, and replay control.
- Integrate replay artifacts into CI triage workflows.
- Add regression tests that compare replay output and event ordering across runs.

**Exit criteria:** a failing workload can be recorded, replayed, and inspected from both CLI and GUI paths.

### Workstream 9: Cycle model for hot kernels (Phase D)
- Build pluggable timing model interfaces separate from functional execution.
- Start with GEMM, reduction, and collective primitives only.
- Add calibration harnesses against MI300X counters and traces.
- Define acceptable slowdown and accuracy budgets per kernel class.
- Gate model changes with regression thresholds in CI.

**Exit criteria:** selected hot kernels can run in calibrated cycle mode with published error bounds and acceptable regression coverage.

### Workstream 10: Packaging, CI, and release hardening (Phase A-D)
- Define packaging/install outputs for CLI, daemon, schemas, and optional GUI artifacts.
- Add layered CI: schema/unit, host integration, simulator conformance, replay regression, and calibrated perf checks.
- Add crash artifact collection for traces, logs, manifests, and snapshots.
- Establish compatibility matrix by ROCm version, host distro, and supported profile schema version.
- Add alpha/beta release criteria and issue triage labels specific to Cosmos.

**Exit criteria:** Cosmos has repeatable packaging, gating CI, and explicit release criteria inside normal TheRock workflows.

## Near-Term Execution Order
1. Workstream 0: scaffold `TheRock/cosmos/` and establish the wheel-based runtime contract.
2. Workstream 1: freeze schema v1 and daemon/CLI API surface.
3. Workstream 2 / Milestone A: make `cosmosd` and `cosmosctl` boot a deterministic simulated cluster topology.
4. Workstream 5 / Milestone B: bring up the single virtual GPU device model with simulator-native queue and memory operations.
5. Workstream 5 / Milestone C: run the first synthetic dispatch through the emulator.
6. Workstream 7 / Milestone D: extend the simulator to a minimal clustered execution path.
7. Workstream 3: package the node runtime so published ROCm stacks can be injected cleanly once the simulator exists.
8. Workstream 4 / Milestone E: bring up `rocminfo`.
9. Workstream 4 / Milestone F: bring up raw HSA queue and memory operations.
10. Workstream 6 / Milestone G: run the first HIP kernel end-to-end.
11. Workstream 6 / Milestone H: run a PyTorch wheel smoke workload.
12. Workstream 8: add replay and GUI.
13. Workstream 9: calibrate hot-kernel cycle mode.
14. Workstream 10: keep packaging and CI in lockstep with all phases.

## Test Plan
- ISA correctness:
  - Differential instruction/memory semantics tests against CDNA spec-derived vectors.
  - Kernel-level correctness parity checks versus MI300X hardware outputs.
- ROCm compatibility:
  - HIP runtime/compiler test suite pass criteria through the Cosmos `libhsakmt` compatibility layer.
  - End-to-end PyTorch + RCCL distributed workloads across simulated multi-node topologies using published wheels plus the Cosmos overlay.
- Determinism and debug:
  - Record/replay reproducibility tests for kernel dispatch and synchronization events.
  - Debugger/profiler attach behavior tests through CLI and GUI flows.
- Timing model quality:
  - Calibrate hot-kernel cycle model with real hardware traces/counters.
  - Enforce per-kernel error thresholds and regression alarms in CI.

## Assumptions and Defaults
- Greenfield implementation with Linux host only.
- Repository home is `TheRock/cosmos/`, not a separate source repository.
- Cosmos is monorepo-resident but not part of the default TheRock build graph.
- Primary ROCm integration path is a Cosmos-specific C++ `libhsakmt` change that targets `amdgpu_lite` directly.
- Applications and higher-level frameworks remain unchanged; `libhsakmt` is the intended adaptation seam.
- Cosmos runtime validation is based on published wheel installs plus a Cosmos `libhsakmt` overlay, not on building ROCm/PyTorch/vLLM from source inside TheRock.
- AMD-only roadmap (no NVIDIA backend planned).
- CDNA in v1; RDNA support starts in v2.
- Scope is fixed (cluster scale + compatibility + hot-kernel cycle mode); schedule flexes accordingly.
- Real MI300X-class hardware access is available for calibration and parity validation.
- Team size remains 6-10 engineers, so milestone gating and strict subsystem ownership are mandatory.
