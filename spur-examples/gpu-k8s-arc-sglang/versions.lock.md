# versions.lock — pin everything (fill in at deploy time)

Reproducibility depends on pinning. Record **exact versions** and, for images, the **digest**
(`repo@sha256:…`), not just a tag. Update this file in the same commit as any change that bumps a
version.

| Layer | Component | Pinned version / digest | Source of truth | Notes |
|---|---|---|---|---|
| Distro | k8s distribution | **k0s** `v1.32.4+k0s.0` (bump to the pinned release) | `10-cluster/k0sctl.yaml` | locked 2026-07-09 |
| Distro | Kubernetes version | 1.32.4 (from k0s `v1.32.4+k0s.0`; >=1.32 for AMD DRA) | k0s release | |
| Network | CNI | **Calico `bird`** (BGP native, no overlay) over spur0 | `10-cluster/k0sctl.yaml` | VXLAN = fallback; needs spur-net AllowedIPs |
| GPU | ROCm k8s-device-plugin | `docker.io/rocm/k8s-device-plugin@sha256:6f225b2bb9b69c677855fbcb327e0e24a64687712adb77271c565839f8385766` | `20-gpu/` | + node-labeller; captured 2026-07-14 |
| GPU | (host) amdgpu / ROCm | in-box on Ubuntu 24.04 / k6.8 | node image | record `rocm-smi --version` |
| ARC | gha-runner-scale-set-controller | chart **0.14.2**; image `ghcr.io/actions/gha-runner-scale-set-controller@sha256:1b4c7f62e971ab259a4b8798e48e2adaad4af747f45990f474ea5feefa03531d` | `30-arc/` | OCI chart; captured 2026-07-14 |
| ARC | gha-runner-scale-set | chart **0.14.2** | `30-arc/` | must match controller |
| ARC | runner image | `ghcr.io/powderluv/sglang-runner:rocm` — **local-only (`imagePullPolicy: Never`), NOT pushed to a registry**; build `30-arc/Dockerfile.runner` + `ctr images import` on every worker (see RUNBOOK) or push + switch pull policy | `30-arc/Dockerfile.runner` | ROCm + docker CLI + kubectl |
| ARC | docker:dind | `docker@sha256:…` | `30-arc/` | sidecar |
| Serving | sglang image | `docker.io/lmsysorg/sglang@sha256:f1d38fd8076e69fdd103858149f9953a936cf9b322b2aa2cc97b08474d9a3623` (running) | `40-serving/` | captured 2026-07-14 |
| Serving | model | _TBD (ungated default preferred)_ | `40-serving/` | + HF license note |
| Addons | ingress / LB / storage | _TBD (only if distro doesn't bundle)_ | `20/40` | k0s: ingress-nginx + MetalLB + local-path |
| Buildkite | agent-stack-k8s chart | `oci://ghcr.io/buildkite/helm/agent-stack-k8s` **0.45.0** | `../buildkite-agent-stack/` | one release per queue; needs k8s ≥1.29 |
| Buildkite | agent-stack-k8s controller | `ghcr.io/buildkite/agent-stack-k8s/controller:0.45.0@sha256:82ff9283870b51cb518ccf7b02d5cd422bcb1fba383a1833888a3cdb6db86cb9` | vendored template | |
| Buildkite | buildkite agent image | `ghcr.io/buildkite/agent@sha256:410e4e4b17dd3f97b5e05ec1669e86ffa8e4a11f4446c61e31ff013b28766ebe` | vendored template | job-pod agent + checkout containers |
| Buildkite | container-0 (rocm) | `rocm/framework-automation:rocm-7.2-ubuntu24-bk3.120.1` | `buildkite-vllm-values.yaml` | rocminfo + docker CLI + agent; pin digest at deploy |
| Buildkite | docker:dind sidecar | `docker:dind` (pin `docker@sha256:…`) | `buildkite-vllm-values.yaml` | privileged dind for `dind:true` steps |

Also record once, at deploy time: `uname -r`, `containerd --version` (CDI needs >=2.0 for the DRA
path), `rocm-smi --version`, and the exact distro installer URL/commit.
