# Buildkite Agent Stack for Kubernetes — install on a SPUR MI355X cluster

Runs vLLM's (or any) Buildkite jobs as ephemeral GPU pods on a SPUR-managed k0s cluster, alongside
the ARC (GitHub Actions) stack. (Design rationale is kept in an internal planning doc, not vendored.)
File index for this directory: [`README.md`](README.md).

**Two lanes** (pick per queue):
- **Full `dind:true` lane (PRIMARY, proven on hardware)** — `buildkite-vllm-values.yaml`: a `docker:dind`
  sidecar + `rocm/framework-automation` container-0 + agent hooks. This is what vLLM's **committed**
  CI steps need (they run `run-amd-test.sh` → `docker run --device /dev/kfd` inside the pod). On
  cluster-1 this ran `v1/sample` pytest **green on gfx950**.
- **Simple `dind:false` native lane (alternative)** — `values.yaml` / vendored `agent-stack-k8s.template.yaml`:
  tests run in-pod, no docker daemon. Only for a trusted pipeline that emits `dind:false` steps.

The rest of this doc is the **full lane**; the simple lane is at the end.

## What runs where
`agent-stack-k8s` is a **namespaced** controller (Role/RoleBinding/SA, **no CRD/ClusterRole**) — one
controller **per queue**. It polls the Buildkite Agent API and creates one k8s Job (pod) per Buildkite
job. GPUs are requested via `amd.com/gpu` so kube-scheduler is the single GPU source-of-truth across
ARC + Buildkite. Chart: `oci://ghcr.io/buildkite/helm/agent-stack-k8s` **0.45.0** (needs k8s ≥ 1.29).

## Prerequisites from the Buildkite side (maintainer-gated, like the sglang GitHub App)
The vLLM / AMD CI owners must, in a dedicated Buildkite cluster for hardware CI:
1. Create the queue(s) — e.g. `amd_mi355_1`, `amd_mi355_2`, `amd_mi355_4`, `amd_mi355_8`.
2. Point ROCm steps at those queues (vLLM's committed steps are **`dind: true`** → use the full lane).
   Set `allowed_teams` on the `block:` gate so only maintainers unblock AMD tests on fork PRs.
3. Issue the **cluster agent token** (per-cluster scope — that token is the isolation boundary).

## Preflight — per-cluster values (discover these ON the new cluster first)
The manifests carry placeholders/defaults tuned to cluster-1. On a second cluster, find the real
values and use them in the `sed` below (a wrong value fails **silently**, so do this first):

```bash
export KUBECONFIG=<path to this cluster's admin kubeconfig>   # whatever k0sctl kubeconfig wrote

# render GID that owns /dev/kfd  (wrong GID -> pod can't open /dev/kfd -> rocminfo sees no GPU)
ssh <a-gpu-node> 'getent group render; stat -c "%g %n" /dev/kfd'      # e.g. 992

# node scratch MOUNT for caches + dind graph (wrong path -> DirectoryOrCreate lands on the ~110Gi
# root fs and evicts under load). Must be a real big-disk mount on EVERY worker.
ssh <a-gpu-node> 'df -h; mountpoint -q /mnt/<scratch> && echo OK'     # e.g. /mnt/m2m_nobackup

# GPU node label the device-plugin/labeller set (used by nodeSelector)
kubectl get nodes -l spur.amd.com/compute=true
```

Also required (cluster prerequisites, same as the ARC side): the AMD **device-plugin** advertising
`amd.com/gpu`, and **containerd relocated onto the scratch disk** on every worker (the multi-GB dind
image + model pulls will fill a small root fs otherwise — see `../gpu-k8s-arc-sglang/RUNBOOK.md`).

## Packaging decision
Buildkite is **not** embedded in core SPUR (unlike the local-path storage addon). It ships as
**vendored static manifests** applied by `kubectl` or the k0s manifest deployer, token supplied at
deploy, images pulled on-demand from ghcr. Net SPUR footprint: **zero**.

## Install — full `dind:true` lane (recommended)

```bash
export KUBECONFIG=<control host admin kubeconfig>

# 0. namespace
kubectl create namespace buildkite

# 1. the THREE required Secrets (names + keys are exact; see buildkite-secrets.example.yaml).
#    Values can come from a sourced chmod-600 env file.
kubectl -n buildkite create secret generic buildkite-agent-token \
  --from-literal=BUILDKITE_AGENT_TOKEN="$BUILDKITE_AGENT_TOKEN"
kubectl -n buildkite create secret generic hf-token \
  --from-literal=TOKEN="$HF_TOKEN"
kubectl -n buildkite create secret docker-registry docker-config \
  --docker-server=https://index.docker.io/v1/ \
  --docker-username="$DOCKERHUB_USERNAME" --docker-password="$DOCKERHUB_TOKEN"

# 2. the two ConfigMaps the pod mounts (agent hooks + sudoers) — REQUIRED, or pods fail to schedule
#    on the missing configMap volumes.
kubectl apply -f buildkite-agent-hooks.yaml -f buildkite-sudoers.yaml

# 3. one controller per queue. Fill ALL placeholders per release:
#    __QUEUE__ __GPU_COUNT__ __MAX_IN_FLIGHT__ and the per-cluster __SCRATCH_ROOT__ + __RENDER_GID__.
#    (queue:max-in-flight below are cluster-1's; tune max-in-flight vs your GPU count & ARC.)
SCRATCH=/mnt/m2m_nobackup          # <- your preflight scratch mount
RENDER_GID=992                     # <- your preflight render GID
for spec in 1:16 2:8 4:4 8:2; do
  q="${spec%%:*}"; max="${spec##*:}"; rel="bk-amd-mi355-$q"
  sed -e "s/__QUEUE__/amd_mi355_$q/g" -e "s/__GPU_COUNT__/$q/g" -e "s/__MAX_IN_FLIGHT__/$max/g" \
      -e "s#__SCRATCH_ROOT__#$SCRATCH#g" -e "s/__RENDER_GID__/$RENDER_GID/g" \
      buildkite-vllm-values.yaml > /tmp/bk-$q.yaml
  helm upgrade --install "$rel" oci://ghcr.io/buildkite/helm/agent-stack-k8s \
    --namespace buildkite --version 0.45.0 --values /tmp/bk-$q.yaml
done

# 4. controllers connected? (expect: Starting controller cluster-name=<...> organization-slug=<...>)
kubectl -n buildkite get pods
kubectl -n buildkite logs deploy/bk-amd-mi355-1-agent-stack-k8s | tail
```
Nothing runs until a Buildkite build targets one of the queues; then the controller creates the
full-config pod (dind sidecar + rocm image) and vLLM's `dind:true` steps run on the GPUs.

### Ephemeral-storage sizing (load-bearing knob)
`buildkite-vllm-values.yaml` requests **`container-0` ephemeral 10Gi / limit 30Gi** and **checkout 4Gi**
— NOT the upstream reference's 50Gi/10Gi. Reason: all heavy data (dind `/var/lib/docker`, HF + docker
caches) is on the `__SCRATCH_ROOT__` hostPaths, so real root-fs use per pod is a few GB. Reserving
50Gi against a ~110Gi node root fs **jams the fan-out at ~1 pod/node** (`FailedScheduling: Insufficient
ephemeral-storage`). If your nodes have a different root-fs size, recompute: `request ≈ free-root-GB /
desired-pods-per-node`. cpu/memory (12000m/200Gi) are sized for MI355X nodes — lower them for smaller nodes.

## Validate
```bash
kubectl apply -f gpu-smoke.yaml   # device-plugin gives a pod gfx950 (edit GID/scratch/image inside first)
kubectl apply -f dind-smoke.yaml  # privileged dind starts + nested `docker run --device /dev/kfd` works
# then have a real Buildkite build target a queue and watch a job pod run.
```

## Install — simple `dind:false` native lane (alternative)
For a trusted pipeline that emits `dind:false` steps (tests run in-pod, no docker daemon):
```bash
kubectl -n buildkite create secret generic buildkite-agent-token \
  --from-literal=BUILDKITE_AGENT_TOKEN="$BUILDKITE_AGENT_TOKEN"
sed -e 's/__QUEUE__/amd_mi355_2/' -e 's/__GPU_COUNT__/2/' \
    -e 's#__SCRATCH_ROOT__#/mnt/m2m_nobackup#' -e 's/__RENDER_GID__/992/' values.yaml > /tmp/bk.yaml
helm upgrade --install bk-amd-mi355-2 oci://ghcr.io/buildkite/helm/agent-stack-k8s \
  --namespace buildkite --version 0.45.0 --values /tmp/bk.yaml
```
Or the no-helm vendored path: `sed` the same placeholders into `agent-stack-k8s.template.yaml` and
`kubectl apply` it (or `tee` into `/var/lib/k0s/manifests/buildkite-<q>/manifest.yaml`).
Air-gap: `helm pull … --version 0.45.0` vendors the 16 KB chart; side-load the two buildkite images.

## Trusted vs untrusted lanes
- **Trusted lane** (only internal pipelines target it): `prohibit-kubernetes-plugin: false`.
- **Untrusted lane** (fork PRs can reach it): set `prohibit-kubernetes-plugin: true` so fork YAML can't
  override the pod spec; this controller's `pod-spec-patch` is authoritative. Combine with vLLM's
  existing `block:` + `allowed_teams` gate, non-root pods, and a NetworkPolicy blocking node-metadata.

## Coexistence with ARC
Same cluster, same `amd.com/gpu` device-plugin → kube-scheduler arbitrates GPUs across ARC + Buildkite
(no double-count). Cap each controller with `max-in-flight`; hard-partition by tainting a node subset
+ `nodeSelector`/`tolerations` if a lane must not contend. Wire **Kueue** for real fair-share.

## Do NOT
- Run the slurm-buildkite (`sbatch`) path against the SAME GPUs — k8s and Slurm are blind to each
  other and will double-book. Partition at node granularity if you ever run both.
