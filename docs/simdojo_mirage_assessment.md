# Simdojo Assessment for Mirage

Date: 2026-03-11

## Verdict

Yes, but only with a narrow definition of "simulation core".

`simdojo` is a good candidate for Mirage's discrete-event and timing backbone
for package, fabric, and system modeling. It is not a good replacement for
Mirage's current functional GPU ISA execution core.

Recommended split:

- Use `simdojo` for time, topology, partitioning, clock domains, and
  inter-component message flow.
- Keep Mirage's existing GPU executor for dispatch-level functional execution
  and ISA semantics.

## Why Simdojo Fits

`simdojo` already provides several high-value capabilities Mirage will
otherwise need to build:

- A hierarchical component graph with active composites, ports, and links.
  This maps well to package, GPU, SDMA, HBM controller, xGMI, PCIe, NIC, and
  node-level components.
- Clock-domain support for timing-mode subsystem models.
- A conservative PDES engine with partitioning and LBTS synchronization, which
  is directly useful for multi-GPU and multi-node timing simulation.
- A built-in topology partitioner for scaling timing simulation across host
  cores.
- Per-epoch service callbacks that fit Mirage progress reporting, metrics,
  watchdogs, and trace snapshots.

This makes `simdojo` a credible base for:

- cluster fabric timing
- doorbells and queue scheduling
- SDMA timing
- interrupt and event routing
- HBM and memory-controller timing
- multi-node orchestration timing

## Where Simdojo Does Not Fit Well

`simdojo` is not a good replacement for Mirage's current inner GPU execution
loop.

The main mismatches are:

- It is message and event oriented. Events carry `std::unique_ptr<Message>`
  and handlers use `std::function`. That is too heavy for per-instruction,
  per-wave, or per-memory-op hot paths.
- Its PDES engine requires positive latency on cross-partition links. That is
  fine for fabric and coarse subsystems, but awkward for tightly coupled GPU
  internals with same-cycle or effectively zero-latency interactions.
- Conservative LBTS plus timestamp advances works best when there is real
  lookahead. GPUs have many tiny-latency, high-frequency interactions. If
  Mirage models compute units, schedulers, LDS, cache slices, and scoreboards
  too finely as separate partitions, barrier and null-message overhead will
  dominate.
- Mirage today is a direct functional simulator with queue submission, memory,
  decode cache, and compiled interpreter flow. Replacing that with per-event
  PDES would likely regress functional-mode speed.

Because of that, Mirage should not:

- make each ISA instruction a `simdojo` event
- make each wavefront a chatty cross-partition actor
- make functional mode depend on the PDES loop

## Best Integration Model

Use `simdojo` as the outer timing shell, with Mirage's existing executor as a
leaf engine.

Suggested shape:

- `MirageTopologyBuilder`
  - converts Mirage package and node profiles into a `simdojo::Topology`
- `GpuPackageComponent`
  - owns one or more Mirage virtual GPUs
- `CommandProcessorComponent`
  - receives dispatch messages and schedules queue and doorbell timing
- `FunctionalExecutorComponent`
  - calls the existing Mirage functional executor at dispatch granularity
- `SdmaComponent`
  - models DMA scheduling and completion timing
- `HbmControllerComponent`
  - models latency, bandwidth, and contention at coarse granularity
- `FabricEndpointComponent`
  - models xGMI, PCIe, NIC, and collective-transport timing

In that model:

- functional mode stays mostly as Mirage is today
- timing mode submits dispatches into `simdojo`
- `simdojo` advances time and resource contention
- when a kernel or micro-op batch is ready to execute, Mirage's existing
  functional executor runs it
- completion returns into the event graph as a timed completion event

That gives Mirage reuse of `simdojo` without forcing a rewrite of the ISA
engine.

## What To Reuse First

Highest-value reuse:

- topology construction and partitioning
- simulation engine and LBTS epoch control
- component, port, and link model
- clock domains and clock-driven components

Lowest-value reuse right now:

- none of Mirage's current functional ISA execution should be rewritten around
  `simdojo`

## Recommended Decision

Recommended approach:

- adopt `simdojo` for Mirage's timing and discrete-event substrate
- do not make it the sole simulation core for functional execution
- keep Mirage's current functional GPU simulator as the execution engine
- introduce `simdojo` first for:
  - cluster and fabric timing
  - package and subsystem scheduling
  - SDMA, interrupt, and doorbell timing
  - timing-mode orchestration
- delay fine-grained GPU microarchitecture modeling inside `simdojo` until the
  coarser timing model proves useful and performant

## Risks

Main risks if Mirage overuses `simdojo`:

- too much event and message overhead
- poor PDES scaling with low lookahead
- partitioning choices that do not match GPU execution locality
- slowing down the default functional path

Main upside if Mirage uses it narrowly:

- Mirage gets a ready-made event engine, partitioning model, and topology
  framework for the part of the simulator that actually benefits from
  discrete-event simulation

## Conclusion

`simdojo` is a strong fit for the outer timing and topology layer of Mirage,
especially for cluster-, package-, and subsystem-level simulation. It is not a
good fit as a replacement for Mirage's current functional GPU ISA executor.

The best path is a hybrid design:

- `simdojo` as the timing shell
- Mirage's existing executor as the functional compute engine

That gives Mirage a practical way to add discrete-event simulation without
giving up the speed and simplicity of the current functional core.
