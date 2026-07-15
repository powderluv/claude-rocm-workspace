# buildkite-agent-stack — vLLM Buildkite CI as GPU pods on a SPUR cluster

Optional addon to [`../gpu-k8s-arc-sglang/`](../gpu-k8s-arc-sglang/): runs vLLM's (or any) Buildkite
jobs as ephemeral GPU pods via Buildkite's [agent-stack-k8s](https://github.com/buildkite/agent-stack-k8s),
alongside the ARC (GitHub Actions) stack on the same k0s cluster. Proven on cluster-1 (MI355X/gfx950):
vLLM's committed `dind:true` CI ran `v1/sample` pytest green on the GPUs.

**Start here:** [`install.md`](install.md). (Design rationale is kept in an internal planning doc, not vendored.)

## Files

| File | Role |
|---|---|
| `install.md` | Install guide — two lanes, preflight, per-queue deploy, validation. **Read first.** |
| `buildkite-vllm-values.yaml` | **PRIMARY** helm values: full `dind:true` config (docker:dind sidecar + `rocm/framework-automation` container-0 + hooks). What vLLM's committed steps need. |
| `buildkite-agent-hooks.yaml` | ConfigMap — agent hooks (wait-for-dockerd, Docker Hub login, render-device metadata, `run-amd-test.sh` cache patch). Mounted by the full config. |
| `buildkite-sudoers.yaml` | ConfigMap — passwordless sudo for `buildkite-agent` (hooks/bootstrap need it). |
| `buildkite-secrets.example.yaml` | Template + imperative commands for the **three** required Secrets: `buildkite-agent-token`, `hf-token`, `docker-config`. |
| `values.yaml` | Alternative simple `dind:false` **native** helm values (tests in-pod, no docker daemon). |
| `agent-stack-k8s.template.yaml` | Vendored no-helm render of the simple lane (`kubectl apply` / k0s manifest-deployer path). |
| `gpu-smoke.yaml` | Smoke: a GPU pod → `rocminfo` reports gfx950 (validates the device-plugin path). |
| `dind-smoke.yaml` | Smoke: privileged docker-in-docker + nested `docker run --device /dev/kfd` (validates the dind path). |

## Deploy order (full lane)
1. **Preflight** — discover this cluster's render GID + scratch mount (`install.md` §Preflight).
2. `kubectl create namespace buildkite`
3. Create the 3 Secrets (`buildkite-secrets.example.yaml`).
4. `kubectl apply -f buildkite-agent-hooks.yaml -f buildkite-sudoers.yaml`
5. Per queue: `sed` the placeholders into `buildkite-vllm-values.yaml` → `helm upgrade --install`.
6. Validate: `gpu-smoke.yaml`, `dind-smoke.yaml`, then a real Buildkite build.

## Per-cluster values (a wrong one fails silently — set them in the preflight)
- `__SCRATCH_ROOT__` — node scratch mount for caches + dind graph (default cluster-1: `/mnt/m2m_nobackup`).
- `__RENDER_GID__` — GID owning `/dev/kfd` (default cluster-1: `992`; `getent group render`).
- `__QUEUE__` / `__GPU_COUNT__` / `__MAX_IN_FLIGHT__` — per queue.
- Ephemeral-storage requests are sized to a ~110Gi node root fs — recompute for different nodes (`install.md`).
