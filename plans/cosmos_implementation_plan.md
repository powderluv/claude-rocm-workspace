# ROCm Virtual Simulator Plan (CDNA v1, iOS-Simulator UX)

## Summary
- Build a greenfield, Linux-hosted ROCm simulator platform that runs unmodified ROCm/HIP applications in containerized simulated nodes, with host CPU passthrough.
- Keep scope fixed to AMD CDNA v1: emulate an MI300X-like system at cluster scale (8+ nodes x 8 GPUs/node), with AMD-only architecture and no NVIDIA backend.
- Deliver two fidelity modes: functional ISA-correct execution for broad compatibility, plus cycle-accurate mode for selected hot kernel classes (target slowdown budget `<=1000x`).
- Ship open-source from day 1, with `simctl` + GUI launcher experience modeled after iOS simulator workflows.
- Treat the 6-12 month window as alpha/beta milestones; scope-complete v1 is planned as a longer critical path under current staffing.

## Key Implementation Changes
- Create a simulator control plane with:
  - `simctl` CLI (`create-profile`, `boot`, `run`, `attach`, `logs`, `snapshot`, `replay`, `shutdown`).
  - Desktop GUI launcher for device/profile selection, boot state, logs, debugger attach, and replay controls.
  - A daemonized orchestration service (`rocksimd`) exposing gRPC/REST control endpoints used by CLI/GUI/CI.
- Define stable public interfaces/types:
  - **Device Profile Schema (YAML/JSON):** GPU count, CU/LDS/register topology, HBM config, xGMI/PCIe fabric, node count, RCCL fabric params, timing-model toggles.
  - **Workload Manifest Schema:** container image, ROCm runtime version pin, launch command, env vars, mounted artifacts, tracing flags.
  - **Trace/Event Format:** queue ops, dispatch, memory transactions, sync primitives, collectives, timestamps, and deterministic replay checkpoints.
  - **Paravirtual ROCm Device Contract:** KFD/DRM ioctl compatibility layer for unmodified user-space ROCm/HIP binaries.
- Build runtime architecture:
  - Node execution: one container per simulated node, unmodified ROCm user-space stack inside each node.
  - GPU simulation backend: CDNA ISA parser/decoder/executor from GPUOpen specs, wavefront execution semantics, memory/atomics/barrier correctness.
  - Timing backend: pluggable cycle model for selected kernels (GEMM/reduction/collective primitives) with hardware calibration hooks.
  - Cluster model: multi-node virtual fabric with configurable latency/bandwidth/contention for RCCL and distributed training.
- Release phases (scope fixed):
  1. **Phase A (Months 0-3):** control-plane skeleton, profile schema v1, container node lifecycle, initial KFD/DRM compatibility scaffold.
  2. **Phase B (Months 3-7):** CDNA functional ISA core, single-node ROCm app execution, HIP conformance harness, basic GUI.
  3. **Phase C (Months 7-12):** multi-GPU/node + multi-node orchestration, RCCL path, record/replay core, alpha/beta developer workflow.
  4. **Phase D (Months 12+):** scope-complete scale-out (8+ x 8), calibrated cycle mode for hot kernels, performance validation hardening.
- Plan v2 extension point now: RDNA backend plug-in contract kept compatible, but RDNA implementation deferred to v2.

## Test Plan
- ISA correctness:
  - Differential instruction/memory semantics tests against CDNA spec-derived vectors.
  - Kernel-level correctness parity checks versus MI300X hardware outputs.
- ROCm compatibility:
  - Unmodified HIP runtime/compiler test suite pass criteria.
  - End-to-end PyTorch + RCCL distributed workloads across simulated multi-node topologies.
- Determinism and debug:
  - Record/replay reproducibility tests for kernel dispatch and synchronization events.
  - Debugger/profiler attach behavior tests through CLI and GUI flows.
- Timing model quality:
  - Calibrate hot-kernel cycle model with real hardware traces/counters.
  - Enforce per-kernel error thresholds and regression alarms in CI.

## Assumptions and Defaults
- Greenfield implementation with Linux host only.
- AMD-only roadmap (no NVIDIA backend planned).
- CDNA in v1; RDNA support starts in v2.
- Scope is fixed (cluster scale + compatibility + hot-kernel cycle mode); schedule flexes accordingly.
- Real MI300X-class hardware access is available for calibration and parity validation.
- Team size remains 6-10 engineers, so milestone gating and strict subsystem ownership are mandatory.
