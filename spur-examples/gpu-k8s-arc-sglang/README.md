# gpu-k8s-arc-sglang — reproducible SPUR-hosted GPU K8s cluster + ARC + sglang

Goal: **every change we make to the cluster is captured as code or logged**, so bringing up a
similar cluster later is `git clone && ./bootstrap.sh` (and eventually `spur k8s up`). This is the
`gpu-k8s-arc-sglang` example in the **`spur-examples`** repo (e.g. `github.com/rocm/spur-examples`),
kept separate from the core `rocm/spur` code; it is the concrete seed of the SPUR `spur cluster` /
`spur k8s up` capability (Phase 2 of the plan `spur-native-k8s-arc-sglang.md`).

**Distro: k0s** (see plan §9) — bootstrapped declaratively via `k0sctl` from `10-cluster/k0sctl.yaml`,
which doubles as the reproducible cluster spec SPUR will template. The **ARC + sglang integration is
the worked example** in this tree: `30-arc/` (controller + GPU dind runner scale set + CPU deploy
scale set for `github.com/powderluv/sglang`) and `40-serving/` (the AMD sglang serving manifest +
the fork CI/CD workflow that tests on GPU and auto-deploys on green). Follow it to reproduce the
whole GitHub-Actions-on-GPU-k8s loop, or swap `40-serving` for a different workload.

**Optional addon — Buildkite CI:** [`../buildkite-agent-stack/`](../buildkite-agent-stack/) runs
vLLM's (or any) Buildkite jobs as ephemeral GPU pods (via `agent-stack-k8s`) beside ARC on this same
cluster. Deploy it after the cluster + device-plugin are up; it reuses the same `amd.com/gpu`
device-plugin, GPU node label, scratch mount, and containerd-on-scratch relocation as this tree.

## Principles

1. **Declarative first.** Prefer a checked-in manifest / Helm values file / distro config over an
   ad-hoc command. If a distro (k0s) offers a declarative cluster spec (`k0sctl.yaml`), that file
   *is* the cluster definition.
2. **Append-only runbook.** Anything genuinely done by hand goes in `RUNBOOK.md` **as it happens**
   (command + host + result + how to undo), then gets promoted into a script under the numbered
   dirs. Nothing about the cluster lives only in someone's shell history.
3. **Pin everything.** `versions.lock.md` pins the distro/k8s version, every Helm chart version,
   and every image by **digest** (not just tag), so a rebuild is byte-reproducible.
4. **Idempotent + ordered.** `bootstrap.sh` re-runs safely; `teardown.sh` reverses in the correct
   order (ARC runner scale sets *before* the controller, k8s before the mesh, re-admit drained
   SPUR nodes last).
5. **No plaintext secrets.** GitHub App key / HF token via sealed-secrets or SOPS; only templates
   and `*.example` files are committed.

## Layout

```
cluster-deploy/
  RUNBOOK.md            # append-only chronological log of every cluster-mutating action
  versions.lock.md      # pinned versions + image digests
  inventory/            # node inventory (scrubbed) — k0sctl.yaml (k0s) or nodes.env (k3s)
  00-network/           # WireGuard mesh, firewall ports, sysctls, MTU
  10-cluster/           # distro install config (k0sctl.yaml / k3s INSTALL_K3S_EXEC / rke2 config.yaml)
  20-gpu/               # AMD device-plugin + node-labeller manifests, node labels, CDI
  30-arc/               # ARC controller + runner scale-set Helm values, GitHub App secret template
  40-serving/           # sglang serving manifest + fork CI workflow
  bootstrap.sh          # idempotent, ordered driver: network → cluster → gpu → arc → serving
  teardown.sh           # reverse-order teardown
  Makefile              # up / gpu / arc / serve / down / capture
```

## Manual prerequisites (not yet fully scripted — do these on a second cluster)

These are load-bearing steps currently captured in `RUNBOOK.md` narrative rather than in
`bootstrap.sh`; a second-cluster operator must do them by hand until they're promoted into scripts:

1. **Relocate containerd onto the scratch disk on every worker** *before* pulling images. The runner,
   `docker:dind`, and model images are tens of GB and will fill a small (~110–123 GiB) root fs →
   `DiskPressure` eviction. (Same scratch mount the Buildkite addon uses.)
2. **Publish the ARC runner image.** `ghcr.io/powderluv/sglang-runner:rocm` is `imagePullPolicy: Never`
   (local-only, never pushed). Either build `30-arc/Dockerfile.runner` and
   `docker save | k0s ctr -n k8s.io images import` on every worker, or push it to a registry and
   switch the pull policy to `IfNotPresent` + add a pull secret.
3. **`50-rdma/` is experimental, not turnkey.** It needs host `iommu=pt` (GRUB drop-in + reboot) and
   currently hits an unresolved ionic NIC QoS blocker (see `RUNBOOK.md` + the MoRI DI notes). Don't
   apply it expecting a working 2-node DI on a fresh cluster.

## How changes get captured during deployment

- Read-only probing → recorded in `RUNBOOK.md` under a dated heading (already seeded with the
  2026-07-09 recon).
- Each milestone (M0…M9 in the plan) lands its artifacts in the matching numbered dir **and** a
  one-line RUNBOOK entry referencing the file + the exact `kubectl/helm/curl` that applied it.
- After each session: `make capture` snapshots live cluster state (`kubectl get all -A -o yaml`,
  `helm list -A`, applied CRDs, node labels) into `state/<date>/` as a drift check against the tree.
