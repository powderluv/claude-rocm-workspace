# Cluster deploy runbook (append-only)

Chronological log of **every** action taken against the cluster. One entry per action:
what, where, result, and how to undo. Read-only probes are logged too (marked RO) so the record
is complete. Newest at the bottom. Hostnames here use the scrubbed placeholders from the plan
(`head-node`, `gpu-node-1..4`); the real names live in local memory, not in this committed file.

Legend: `RO` = read-only (no change made) · `CHG` = mutating change · `↩` = rollback.

---

## 2026-07-09 — Recon (RO only, nothing changed)

- **RO** `ssh head-node`: reachable; Ubuntu, kernel 6.8; **no spur, no k8s, no kubectl** installed;
  32 vCPU / 125 GB; default egress via uplink NIC; greenfield.
- **RO** `ssh gpu-node-1` and `gpu-node-4`: **8× AMD Instinct MI355X (gfx950), SPX partition** each
  (`rocminfo`/`rocm-smi`); `/dev/kfd` present; 236 vCPU / 2.7 TB; Ubuntu 24.04 / kernel 6.8;
  no spur, no k8s. → 32 GPUs total across gpu-node-1..4.
- **RO** reachability: `gpu-node-1 → gpu-node-4` ICMP 0% loss / 0.3 ms on a flat routed private /16
  → worker↔worker underlay OK (full WireGuard mesh feasible, no relay needed).
- **RO** egress: head + workers each reach `https://ghcr.io/v2/` (HTTP 401 = reachable) → direct
  per-node internet egress; no head-funnel required.
- **Conclusion:** all three M0 hard gates PASS (gfx950 sglang-supported / mutually reachable /
  egress OK). No changes made. Next mutating action is M0 step 4 (mesh init) — log below when done.

---

## Change log (fill in as we deploy)

<!-- Template — copy per action:
### YYYY-MM-DD  Mxx  <short title>
- **CHG** host=<node> — `<exact command>`  → result: <what happened>
- artifact: cluster-deploy/<dir>/<file>   (checked-in source of this change)
- ↩ rollback: `<command to undo>`
-->

_(no cluster-mutating changes yet)_

## 2026-07-09 — Backend RDMA/RoCE recon (RO only, nothing changed)

- **RO** `ssh gpu-node-1`: **8× AMD Pensando (ionic) 400 GbE NICs** (enP2p0s9-12, enP3p0s9-12), all
  state=up 400000Mb, **no IP** (raw backend fabric). RDMA devices `ionic_0..7` (+ `mlx5_0` on the
  200G `ens3` frontend). Modules: ionic, ionic_rdma, ib_core/uverbs, rdma_cm, mlx5. `/dev/infiniband/uverbs0-8`.
- **RO** `ibv_devinfo -d ionic_0`: transport InfiniBand(0), link_layer Ethernet, **RoCE v2** GIDs,
  PORT_ACTIVE. GPUDirect-capable (amdgpu dmabuf; ionic_rdma 25.08.4.004 default_ats peer-mem). ROCm 7.0.1.
  RDMA netns mode = shared. Backend netdevs un-IP'd → **RoCEv2 rail IPs must be assigned** before cross-node RDMA.
- **Conclusion:** AMD MI355X + 8×-Pensando reference config for sglang DI (MoRI/Mooncake). Exposure plan
  = 50-rdma/ (Multus+SR-IOV or hostNetwork; off spur0). Verify via sglang nightly-amd-mi355x-disagg DI test.
  No changes made.

