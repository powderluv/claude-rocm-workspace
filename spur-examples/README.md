# spur-examples

Runnable, reproducible examples for [SPUR](https://github.com/rocm/spur) — kept **separate from the
core `rocm/spur` code** so each example can evolve, be versioned, and be `git clone`d independently.

Each example is a self-contained directory: a declarative cluster/config definition, all manifests
and Helm values, pinned versions, an append-only runbook, and `bootstrap.sh`/`teardown.sh`.

## Examples

| Example | What it does |
|---|---|
| [`gpu-k8s-arc-sglang/`](gpu-k8s-arc-sglang/) | Host a real **AMD-GPU Kubernetes cluster on SPUR nodes** (k0s over the WireGuard mesh, Calico `bird` native routing, ROCm device plugin), run **ARC** self-hosted GitHub Actions runners on it, and **auto-deploy sglang** serving from a fork's CI on green. Target hardware: MI355X (gfx950). |
| [`buildkite-agent-stack/`](buildkite-agent-stack/) | **Optional addon** to `gpu-k8s-arc-sglang/`: run vLLM's (or any) **Buildkite CI as ephemeral GPU pods** via `agent-stack-k8s`, beside ARC on the same cluster. Proven: vLLM's committed `dind:true` suite ran pytest green on gfx950. |

## Conventions

- **Declarative first** — a checked-in manifest/values/config over an ad-hoc command.
- **Pin everything** by digest in each example's `versions.lock.md`.
- **Append-only `RUNBOOK.md`** — every cluster-mutating action logged (host + command + result +
  rollback); anything done by hand gets promoted into a script.
- **Idempotent bring-up, reverse-ordered teardown.**
- **No plaintext secrets** — templates + sealed-secrets/SOPS only.

These examples are also the concrete seed of SPUR's own `spur cluster` / `spur k8s up` capability:
`gpu-k8s-arc-sglang/10-cluster/k0sctl.yaml` is exactly the kind of cluster definition SPUR will
template and wrap.
