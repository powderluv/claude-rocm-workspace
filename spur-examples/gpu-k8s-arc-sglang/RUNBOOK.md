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

_(see entries below)_
## 2026-07-09 — M0 mesh bring-up (CHG — first mutating steps)

- **CHG** all 5 nodes — enabled passwordless sudo (`/etc/sudoers.d/90-spur-nopasswd`, via spur-enable-sudo.sh). ↩ `sudo rm /etc/sudoers.d/90-spur-nopasswd`.
- **CHG** all 5 nodes — installed spur v0.3.0 (`install.sh` → ~/.local/bin) + `sudo apt-get install -y wireguard-tools`. ↩ remove ~/.local/bin/spur*, `apt remove wireguard-tools`.
- **CHG** head-node — `sudo spur net init --cidr <mesh-cidr> --port 51820` → spur0=<mesh-ip>, controller pubkey captured (session/memory, not committed). ↩ `sudo wg-quick down spur0; sudo rm /etc/wireguard/spur0.conf`.
- **CHG** gpu-node-1..4 — `sudo spur net join --endpoint <head-underlay>:51820 --server-key <ctl-pub> --address <mesh-ip-range>` → spur0 up on each. ↩ same as above per node.
- **CHG** all 5 nodes — FULL MESH: `sudo spur net add-peer` fan-out so every node peers the other 4 (/32 AllowedIPs + underlay endpoint). Pod-CIDR AllowedIPs deferred until k0s assigns node podCIDRs (then `spur net mesh`/add-peer with pod /24).
- **RO validate** gpu-node-1: ping over spur0 to <mesh-ip>/.3/.5 all OK; `wg show spur0` = 4 peers, all with live handshakes + non-zero transfer. spur0 MTU=1420 (underlay ens3=1500). → **M0 mesh COMPLETE + validated.**

Next: M1 — install k0s per-node (control plane on head over spur0, Calico bird), workers join.


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

## 2026-07-09 — M1 k0s bring-up over the mesh (CHG) — COMPLETE

- **CHG** all 5 nodes — installed k0s **v1.34.9+k0s.0** (direct release binary → /usr/local/bin/k0s; the get.k0s.io script fails under dash). ↩ `sudo k0s stop && sudo k0s reset`.
- **CHG** head-node — `/etc/k0s/k0s.yaml`: Calico `mode: bird`, `ipAutodetectionMethod: interface=spur0`, mtu 1420, pod 10.42/16, svc 10.43/16, api.address <mesh-ip>; `sudo k0s install controller` (control-plane only, no kubelet) + `k0s start`. Token via `k0s token create --role worker`.
- **CHG** gpu-node-1..4 — `sudo k0s install worker --token-file … --kubelet-extra-args=--node-ip=<mesh-ip-range>` + `k0s start`. All 4 Ready, INTERNAL-IP = mesh IPs.
- **FINDING** k0s `calico.mode: bird` alone leaves the IPPool at **ipipMode: Always** → pods tunnel IPIP-over-WireGuard (a 2nd overlay; route via `tunl0`). Fixed live: `kubectl patch ippool default-ipv4-ippool --type merge -p '{"spec":{"ipipMode":"Never"}}'` → route becomes `via <mesh-ip> dev spur0` (native).
- **CHG** all 5 nodes — added each node's pod /26 block to WireGuard `AllowedIPs` (full mesh) via `spur net add-peer --allowed-ip <mesh>/32,<pod>/26` (the spur-net enhancement's purpose). Then cross-node pod ping + CoreDNS both work natively over spur0, 0% loss.
- **KNOWN LIMITATION** AllowedIPs hold the current Calico /26 blocks; if a node exceeds 64 pods Calico allocates a 2nd (unlisted) block → those pods unreachable cross-node until AllowedIPs are re-reconciled. Hardening follow-on: deterministic per-node pod CIDRs (or reconcile AllowedIPs from Calico blocks). → **M0+M1 COMPLETE.**

## 2026-07-09 — M2 GPU device plugin (CHG) — COMPLETE

- **CHG** head — labelled gpu-node-1..4 `spur.amd.com/compute=true` (by name; chicken-and-egg before the plugin).
- **CHG** head — applied ROCm `rocm/k8s-device-plugin:latest` DaemonSet (kube-system, nodeSelector spur.amd.com/compute). **PATH FIX**: hostPath = STANDARD `/var/lib/kubelet/device-plugins` (NOT /var/lib/k0s/kubelet — k0s root-dir is /var/lib/k0s/kubelet but the device-plugin socket stays at the standard path). Plugin detects 8 GPUs/node (SPX, spx_nps1:8); benign warn "p2pWeights besteffort init failed → default allocation".
- **RO validate** — `amd.com/gpu`: allocatable=8 capacity=8 on all 4 nodes (32 total). A `rocminfo` pod requesting `amd.com/gpu:1` got exactly ONE gfx950 (/dev/kfd + one renderD128 — per-GPU isolation works), rocminfo shows Name: gfx950, CU 256, ISA amdgcn-amd-amdhsa--gfx950.
- **FINDING** GPU pods need `securityContext.supplementalGroups: [44, 992]` (video=44, **render=992** owns /dev/kfd) + `seccompProfile: Unconfined`. Without render(992) the container user can't open /dev/kfd → rocminfo sees no GPU. (renderD* is world-rw via group allusers=226.) Folded into 40-serving manifest.
- **DOUBLE-ALLOC** N/A right now — no spurctld/spurd SCHEDULER is running (only `spur net` was used for the mesh); k8s is the sole GPU manager. If spurd is later run as a scheduling agent on these nodes, drain/partition them (plan M2). → **M2 COMPLETE.**

## 2026-07-09 — M3 fork + ARC auth (CHG) — COMPLETE

- **CHG** GitHub — forked sgl-project/sglang → **powderluv/sglang** (gh authed as powderluv).
- **CHG** head — installed helm v4.2.2 + local-path-provisioner (default StorageClass, for conformance storage tests + sglang PVC).
- **CHG** head — GitHub App "spur-test" (App ID 4258295, Installation 145499546) created by user; validated via JWT→installation-token: permissions administration:write + metadata:read (exactly ARC's repo-scope needs), owner powderluv. Key retrieved from jump-host ~/Downloads → gitignored workspace path (sglang-arc-app.private-key.pem).
- **CHG** head — `kubectl -n arc-runners create secret generic sglang-arc-app` (github_app_id/installation_id/private_key); `helm install arc` = gha-runner-scale-set-controller (arc-systems). Controller Running.
- **RO validate** — throwaway `arc-check` scale set (stock runner image, minRunners:0) → AutoscalingListener came up 1/1 Running, opened a GitHub Actions broker session (runnerscalesets/1/sessions, totalAssignedJobs=0), no auth errors → ARC auth chain WORKS end-to-end. Uninstalled arc-check (secret + controller kept). → **M3 COMPLETE.**

Next M4: build ROCm GPU runner image (ghcr.io/powderluv/sglang-runner) + install the hand-authored dind GPU runner scale set (linux-mi35x-gpu-1). NOTE: local sglang-arc-app.private-key.pem is gitignored; delete after deployment (secret is in-cluster; re-retrievable from jump host).

## 2026-07-09 — M4 GPU runner image + scale set, M5-core validation (CHG) — COMPLETE

- **CHG** all 4 GPU workers already have docker 29 → built the runner image (FROM ghcr.io/actions/actions-runner + docker.io + kubectl + render/video groups; ROCm NOT needed — runner only orchestrates dind) on each, `docker save | k0s ctr -n k8s.io images import` into k0s containerd (2.6GB, tag ghcr.io/powderluv/sglang-runner:rocm, LOCAL — no ghcr push; write:packages absent). Validated pod-runnable (docker+kubectl, runner in docker(123)/video(44)/render groups).
- **CHG** head — `helm install linux-mi35x-gpu-1` (gha-runner-scale-set) with the hand-authored dind spec (imagePullPolicy Never, no ghcr-pull, DOCKER_GROUP_GID=123, /dev/kfd+/dev/dri hostPath into dind, work+dind-sock+dind-externals+podinfo vols, amd.com/gpu:1 on runner, min0/max2). Listener 1/1 Running.
- **CHG** fork — added .github/workflows/arc-gpu-check.yml (workflow_dispatch) committed with **[skip ci]** (avoids the 23 push-triggered upstream workflows firing). `gh workflow run` → EphemeralRunner pod spawned on gpu-node-2 (2/2 runner+dind).
- **RO validate** — job SUCCESS: dind server 29.6.1; `docker run --device=/dev/kfd --device=/dev/dri --group-add 44 --group-add 992 rocm/rocm-terminal rocminfo` → **Name: gfx950** (amdgcn-amd-amdhsa--gfx950). Ephemeral pod auto-cleaned. → **dind GPU passthrough WORKS; M4+M5-core COMPLETE.**

Next: M5-full (real sglang amd_ci_*.sh + test suite on the runner), M6 (gated serving deploy), M6.5 (RDMA).

## 2026-07-09 — K8s conformance validation

- **Sonobuoy quick conformance: PASSED** (5/5, 0 failed) — validates the conformance harness + core API/scheduling/pod-lifecycle.
- **Full certified-conformance (424 specs): DID NOT COMPLETE** — hung in the ginkgo `SynchronizedBeforeSuite` (before any test namespace/pod was created). Diagnosis: e2e.test child deadlocked (PID1 wrapper in do_wait, ~8 CPU-ticks in 83min = idle); NO cluster fault found — all kube-system pods Ready, metrics-server works, CoreDNS works, pod MTU correct at 1420, API reachable from pods (fast 401, no hang). Concluded: a sonobuoy v0.57.5 / conformance-image quirk on k8s 1.34 + k0s, NOT a cluster defect. Deleted the run. Retry option: hydrophone (modern CNCF conformance runner) or a pinned conformance image.
- **Organic validation (all PASS)** stands in as strong full-support evidence: cross-node pod networking native over the mesh, CoreDNS/service discovery, GPU device plugin + scheduling + per-GPU isolation, default StorageClass/dynamic provisioning, RBAC, a REAL GPU workload end-to-end via ARC+dind (rocminfo gfx950), ephemeral runner lifecycle.

## 2026-07-09 — M5-full bring-up: two runner fixes (CHG)

- **BUG (runner evicted mid-run)** First real sglang CI run failed: "the self-hosted runner lost communication with the server." Root cause via `kubectl -n arc-runners get events`: `Warning Evicted ... node was low on resource: ephemeral-storage` — the dind sidecar pulls the ~60 GB `lmsysorg/sglang:*-rocm700-mi35x` image into dockerd's data-root, which by default sits on the container writable layer on the **123 GB root fs**. Free dropped to ~16 GB, below kubelet's ~18 GB (nodefs 15%) eviction threshold → kubelet killed the runner pod. Nodes have plenty of RAM (2.7 TB) and no OOM; purely a root-disk problem.
- **CHG (fix)** `runner-scale-set-values.yaml`: gave dind a `dind-graph` hostPath volume on the **27.9 TB `/mnt/m2m_nobackup`** LVM (8× 3.5 TB NVMe), mounted at `/var/lib/docker` with `subPathExpr: $(POD_NAME)` (POD_NAME via downward API) so each ephemeral runner gets its own data-root — concurrent dockerds must not share one. `helm upgrade linux-mi35x-gpu-1 -n arc-runners ... --version 0.14.2` → revision 2. Verified: new runner pod Running 2/2, dockerd initialized `/mnt/m2m_nobackup/sglang-dind/<pod>/`, root fs stays ~75 GB free during pull (no eviction). TRADE-OFF: per-pod data-root ⇒ NO image cache across jobs (re-pulls sglang each run) and subdirs are NOT GC'd on pod delete → prune `/mnt/m2m_nobackup/sglang-dind/*` periodically. (Alternative rejected: shared per-node data-root + pod anti-affinity — caches the image but risks data-root corruption if two dockerds co-schedule.)
- **BUG (no rocm-smi on runner host)** `ensure_vram_clear.sh` runs `rocm-smi` on the runner host (outside the sglang container) → exit 127 "command not found"; the stock `actions-runner` image has no ROCm.
- **CHG (fix)** `Dockerfile.runner`: install full ROCm for gfx950 from TheRock's multi-arch nightly index — `pip install --break-system-packages --index-url https://rocm.nightlies.amd.com/whl-multi-arch/ "rocm[libraries,device-gfx950]" rocm-sdk-device-gfx950` (pins the device pkg per TheRock #5347). Shims land in `/usr/local/bin` → `rocm-smi`, `amd-smi`, `rocminfo` on PATH. Built on node 135 (7.7 GB), `docker save` → NFS `/home/anushe/sglang-runner-rocm.tar` (chmod 644 for root-squashed `ctr import`), `k0s ctr -n k8s.io images import` on all 4 workers (imagePullPolicy Never; containerd resolves the buildkit index → linux/amd64, attestation manifest ignored). VERIFIED on a GPU node: `rocm-smi --showmeminfo vram` reports 288 GB/GPU across all 8 MI355X — the query `ensure_vram_clear` needs. NON-ISSUE: `rocm-smi --showproductname` prints "get_name, Failed to load a library" (the `amdgpu.ids` marketing-name DB lacks the new 0x75a3 device); cosmetic only, memory/reset queries work — not worth chasing.

## 2026-07-09 — M5-full: libdrm + render-devices footgun (CHG)

- **BUG (rocm-smi libdrm)** In a clean container the full-ROCm image's `rocm-smi` failed hard: `Fail to open libdrm_amdgpu.so: cannot open shared object file` (the `actions-runner` base has no system libdrm, and the ROCm wheels don't bundle it; the earlier "vram works" reading came from host libs leaking into a different exec context). `rocm-smi` dlopens the **unversioned** `libdrm_amdgpu.so`.
- **CHG (fix)** `Dockerfile.runner`: add apt `libdrm-amdgpu1 libdrm2 libnuma1 pciutils` + `ln -sf libdrm_amdgpu.so.1 /usr/lib/x86_64-linux-gnu/libdrm_amdgpu.so && ldconfig`. Rebuilt → **digest e4bf3fc807c6** (7.7 GB), NFS tar, `k0s ctr import` on all 4 workers (this is the live image). VERIFIED in a throwaway container with `/dev/kfd`+`/dev/dri`: `rocm-smi` enumerates GPU[0..7] (no libdrm error); `rocminfo` lists agents incl. gfx950.
- **BUG (arch assert: no /dev/dri in ci_sglang)** With the dind fix, run 29044594744 got PAST eviction + container-start but failed `Assert GPU arch = gfx950`. Root cause in sglang `amd_ci_start_container.sh`: `if [[ -f /etc/podinfo/gha-render-devices ]]; then DEVICE_FLAG=$(cat …); else DEVICE_FLAG="--device /dev/dri"; fi`. The downwardAPI mount ALWAYS creates the file, so the **empty** annotation → `DEVICE_FLAG=""` → `docker run --device=/dev/kfd  …` passes NO /dev/dri → ci_sglang can't enumerate the GPU → `rocminfo | grep gfx950` fails (exit 1). (The "empty default" in the values file was actively broken with this script.)
- **CHG (fix)** `runner-scale-set-values.yaml` annotation `spur.amd.com/render-devices: "--device /dev/dri"` (was `""`). Interim = all render nodes; safe while `maxRunners ≤ GPUs/node`; device-plugin/CDI later pins the allocated renderD. `helm upgrade` → **revision 4**. Re-dispatched fork-ci **run 29045089833** (uses e4bf3fc807c6 + real vram-clear + /dev/dri). Helm revs: r2 (dind, from fork) → r3 (dind, re-applied from committed values) → r4 (render-devices).

## 2026-07-09 — Conformance re-debug (RO)

- **RO** cluster health re-verified: ALL kube-system pods Running/Ready (calico-node ×4, coredns ×2, konnectivity-agent ×4, kube-proxy ×4, metrics-server, amdgpu-device-plugin ×4); 4 nodes Ready, no taints, schedulable. → no perpetually-NotReady system pod, so the e2e `SynchronizedBeforeSuite` hang is NOT a system-pod-readiness deadlock.
- **DECISION (topology):** head-as-worker is NOT warranted for conformance. Control-plane-only is a standard conformant topology (managed k8s hides control-plane nodes and still certifies); adding a kubelet to the head weakens control-plane isolation and would not address a harness-side BeforeSuite deadlock. Capturing the exact blocking call via verbose ginkgo before any further topology change.
- **FINDING (BeforeSuite is fine — hang does NOT reproduce):** focused verbose probe `hydrophone --focus "Pods should be submitted and removed" --verbosity 6` (verbosity ≥6 auto-enables ginkgo `-v`; `--extra-ginkgo-args` needs strict `--key=value`, so `--trace` is rejected) → **`[SynchronizedBeforeSuite] PASSED [0.018 seconds]`**: all 4 kube-system daemonsets report 4/4 ready in 0s, apiserver v1.34.9+k0s, IP family ipv4; then the real `[sig-node] Pods should be submitted and removed [Conformance]` spec PASSED (`Ran 1 of 7144 Specs — 1 Passed`). So the earlier 83-min "stuck at 0/424 in SynchronizedBeforeSuite" was a **transient not-ready condition** during those specific runs (the node-schedulable / daemonset waits), NOT a cluster or topology defect. → **head-as-worker CONFIRMED unnecessary.**
- **CHG** launched the FULL `[Conformance]` suite: `hydrophone --conformance --conformance-image registry.k8s.io/conformance:v1.34.9 --verbosity 6 --output-dir ~/conf-full` (2h cap, verbose so any hang pins to a spec) for a real certified number. Result appended here on completion.

## 2026-07-09 — M5-full: pipeline reaches real tests, 3/6 pass on gfx950 (result)

- Run **29045089833** (helm r4 + image e4bf3fc807c6 + `--device /dev/dri`) ran END-TO-END: checkout → vram-clear (real rocm-smi) → ci_sglang up → **arch assert gfx950 PASS** → install deps → **Run small AMD suite** (`stage-a-test-1-gpu-small-amd`).
- **RESULT: 3/6 pass on real MI355X** — ✓ `quant/test_awq_dequant.py`, ✓ `quant/test_fused_rms_fp8_group_quant.py`, ✓ `attention/test_wave_attention_kernels.py`. Real sglang GPU kernel tests GREEN on gfx950 through the full ARC+dind pipeline. **This validates M5-full end to end.**
- **✗ `core/test_basic_sanity_eagle3.py` — NOT a GPU/cluster defect.** The EAGLE3 sanity test runs `sglang serve --model-path meta-llama/Llama-3.1-8B-Instruct` (GATED) → `huggingface_hub.errors.GatedRepoError: 401 … Access to model meta-llama/Llama-3.1-8B-Instruct is restricted` → server exits 1 → setUpClass fails.
- **ROOT CAUSE:** no `HF_TOKEN` repo secret on powderluv/sglang (`gh secret list` empty) AND `fork-ci.yml` doesn't wire one. `amd_ci_start_container.sh` DOES forward `-e HF_TOKEN="${HF_TOKEN:-}"` into ci_sglang (line 320), so the fix = set an `HF_TOKEN` secret (Llama-3.1 license accepted on HF) + add `env: HF_TOKEN: ${{ secrets.HF_TOKEN }}` to the workflow steps. → pipeline validated; remaining is a credential/config item.

## 2026-07-09 — M5-full GREEN via ungated suite (CHG) — COMPLETE

- **DECISION (user):** close M5-full without HF gating → run the ungated subset (skip the 2 Llama-3.1 tests) rather than provide an HF token.
- Suite `stage-a-test-1-gpu-small-amd` = **6 tests**: 3 GPU-kernel (awq_dequant, fused_rms_fp8_group_quant, wave_attention_kernels — no model), 1 unit (vit_pos_embed_interpolate — `nn.Embedding` stub, no model), + 2 GATED serve tests (`core/test_basic_sanity` and `core/test_basic_sanity_eagle3`, both `meta-llama/Llama-3.1-8B-Instruct`).
- **CHG** powderluv/sglang `.github/workflows/fork-ci.yml` (**commit e861743**): the suite step now runs the 4 ungated files directly (`amd_ci_exec.sh -w /sglang-checkout/test python3 <file>` per file) and skips the 2 gated. Fork-workflow-only → zero sglang-source drift. To restore the full suite: add an `HF_TOKEN` secret + `env: HF_TOKEN: ${{ secrets.HF_TOKEN }}` and switch back to `run_suite.py --suite stage-a-test-1-gpu-small-amd`.
- **FLAKE** run 29046931620 failed at `Install deps` on transient pypi/aliyun `ReadTimeout`s (lmms-eval build dep → setuptools), NOT the change; re-dispatched.
- **RESULT: run 29047900409 SUCCESS.** All 4 ungated tests green on real MI355X (each unittest "OK"). → **M5-full COMPLETE**: the full ARC→dind→sglang pipeline runs real GPU tests on gfx950, green end-to-end. Next: M6 (gated serving auto-deploy), M6.5 (RDMA/DI).

## 2026-07-09 — Backend RoCEv2 IPv6 connectivity CONFIRMED (RO) — corrects earlier "un-IP'd"

- **CORRECTION** to the 2026-07-09 RDMA recon ("backend netdevs un-IP'd"): that was **IPv4-only**. The 8 ionic backend NICs on every node DO carry a **global IPv6** address via **SLAAC** (EUI-64 from the NIC MAC → stable across reboots), one per-rail `fc01:<rail>::/64` prefix advertised by the fabric switches (`proto ra`). RoCE **v2 GIDs** already exist for them (gid1 = the `fc01:` IPv6). IPv6 is not disabled anywhere. Because the addressing is RA/SLAAC (switch-provided), it **auto-restored after the OS reinstall** — no node-local config to lose.
- **RO validate (workflow, 96 ordered pairs):** same-rail `ping6` across all 4 nodes × 8 rails, each bound to its `-I <rail>` interface → **96/96 reachable**, RTT ~0.10–0.28 ms. The fabric ROUTES between the per-`(node,rail)` /64s: `… via fe80::<switch> dev <rail> proto ra … mtu 9000 hoplimit 64 pref high` — **L3 leaf routing per rail, jumbo MTU 9000**. (Each rail's 2nd hextet is a stable rail id: s9=800, s10=700, s11=500, s12=600 on bus2; s9=400, s10=300, s11=100, s12=200 on bus3.)
- **IMPLICATION for M6.5:** the IPv6 RoCEv2 rail fabric is **UP + routed + jumbo**; no manual IP assignment is needed for IPv6, and the MAC-derived addresses are stable enough to reference in RCCL (`NCCL_IB_HCA=ionic_0..7`, GID index = the v2 IPv6 GID). ICMP L3 reachability is proven; the remaining M6.5 checks are RoCE-level bandwidth (`ib_write_bw` per rail) and exposing the fabric to pods (Multus + SR-IOV/RDMA). IPv4 rail IPs remain absent — only needed if an IPv4 RoCE data plane is wanted.

## 2026-07-09 — Full CNCF conformance: 423/424 (the 1 = transient) (RO)

- **hydrophone full `[Conformance]`** (registry.k8s.io/conformance:v1.34.9, verbosity 6): **Ran 424 of 7144 Specs in 6389 s (~106 min) → 423 Passed | 1 Failed | 6720 Skipped.** The earlier "83-min hang at 0/424" did NOT recur — the suite ran end to end. (Prior focused probe already showed `SynchronizedBeforeSuite PASSED [0.018s]`.)
- **The 1 failure is a transient apiserver connection drop, not a cluster/DaemonSet defect:** `[sig-apps] Daemon set [Serial] should verify changes to a daemon set status` failed on `Get "https://10.43.0.1:443/api/v1/nodes": dial tcp 10.43.0.1:443: i/o timeout — error from a previous attempt: http2: client connection lost` — the test lost its HTTP/2 client connection to the apiserver (kubernetes.default VIP / konnectivity) mid-spec. **Re-ran the single spec in isolation → 1 Passed / 0 Failed (SUCCESS)** — confirms transient. **Effective conformance: 424/424.**
- **DECISION (final):** control-plane-only k0s + Calico-bird-over-WireGuard is effectively conformant (423/424, the 1 a network transient). **No head-as-worker topology change** — confirmed twice (focused BeforeSuite probe + full run). If a certified clean run is required, retry (conformance tolerates transient reruns).

## 2026-07-09 — M6.5 RoCE RDMA bandwidth VERIFIED (CHG minor + RO)

- **CHG (orchestration convenience)** Generated a shared ed25519 key in the NFS-shared `~/.ssh` on the workers (`authorized_keys` += the pubkey) so nodes can ssh each other over the mesh. Needed because `ib_write_bw` is a 2-node server/client test and the Mac→jump-host ssh flaps on long / backgrounded connections; running server local + client over the reliable internal mesh ssh sidesteps it. ↩ remove `~/.ssh/id_ed25519*` + the pubkey line from `~/.ssh/authorized_keys`.
- **RO validate (`ib_write_bw`, perftest 4.5):** RDMA WRITE across all 8 backend rails, 135 (server, <mesh-ip>) ← 136 (client), device `ionic_0..7`, **GID index 1** (global IPv6 RoCE v2), OOB over the mesh IPv4 (perftest OOB is IPv4-only — `ai_family: 2`; the RDMA data plane rides the ionic rail via the IPv6 GID). BW average per rail: **361 / 346 / 359 / 360 / 316 / 369 / 377 / 335 Gb/s** → ~316–377 Gb/s on 400 GbE (79–94% line rate), single-QP host memory, untuned. Aggregate ~2.8 Tb/s/node-pair.
- **Conclusion:** the RoCEv2 **RDMA data plane** (not just ICMP L3) works end-to-end over the IPv6 fabric at near line rate. Helper: `~/roce_bw.sh <peer_mesh_ip> <self_mesh_ip> [dur]` on a node. Remaining M6.5 = expose to pods (Multus + SR-IOV/RDMA device plugin) + GPUDirect (`--use_rocm`) + the sglang 2-node DI test.

## 2026-07-09 — M6 serving deploy + containerd relocated to big disk (CHG)

- **CHG** created `sglang` ns; redirected local-path-provisioner to `/mnt/m2m_nobackup/local-path`; set serving image = `lmsysorg/sglang:v0.5.14-rocm700-mi35x` (M5-validated gfx950 image) serving UNGATED `Qwen/Qwen2.5-7B-Instruct`. Applied `40-serving/sglang-service-amd.yaml` (PVC hf-cache 100Gi RWO + Deployment `amd.com/gpu:1`, `supplementalGroups[44,992]`, startup/readiness/liveness probes, ClusterIP svc:30000).
- **BUG (same disk wall as M5, now for serving):** the ~70G sglang image pulls into **containerd on the 123G root** → free hit the ~19.8G kubelet ephemeral-storage eviction threshold → pod **Evicted**, node 136 `DiskPressure=True`. (dind was moved to the big disk in M5, but the SERVING image goes into the node's containerd image store on root.)
- **CHG (durable fix): relocated k0s containerd store to the 28TB disk on node 136.** `k0s stop` → `mv /var/lib/k0s/containerd (81G) → /mnt/m2m_nobackup/containerd` → `ln -s` → `k0s start` (detached `reloc-containerd.sh`, ~42s move). Root freed **8G→89G**; node back Ready, DiskPressure=False; the pulled sglang image **preserved** in the moved store, so the redeploy reused it (pod Running in **4s**, no re-pull). ↩ `k0s stop; rm symlink; mv back; k0s start`.
- **PIN:** the local-path PVC bound to 136 (RWO) → its PV node-affinity auto-pins the serving pod to 136. NOTE: to let serving schedule on 135/137/138 too, relocate their containerd the same way (only 136 done so far). Consider baking this into node bring-up (containerd root on the big disk from the start).
- **READY + `/generate` VALIDATED:** server logged "The server is fired up and ready to roll!", pod went 1/1 Ready. `/generate` ("The capital of France is", max_new_tokens=16, temp=0) → **" Paris. Which of the following statements is true?..."**, 16 tokens, e2e_latency 73 ms on gfx950. **M6 serving works end-to-end.**
- **CHG (all nodes): relocated k0s containerd to the big disk on 135/137/138 too** (rolling, one at a time via `reloc-containerd.sh`, ~15-20 s each; stores 16/15/11 G). All 4 workers now symlink `/var/lib/k0s/containerd -> /mnt/m2m_nobackup/containerd`, all Ready, DiskPressure=False. Serving can now schedule on any node (still PVC-pinned to 136 while that PVC exists). Inter-node SSH key (from the RoCE step) made the rolling orchestration reliable.
- **BUG (probe too aggressive):** default `timeoutSeconds: 1` on the liveness probe killed the healthy-but-busy server (`/health` → `context deadline exceeded`) → exitCode 0 "Completed" restart. **CHG (fix):** added `timeoutSeconds: 10` to startup/readiness/liveness probes in `40-serving/sglang-service-amd.yaml`; re-applied (rolls fast — model on PVC + image both cached). Stability poller running to confirm no further liveness kills.
- **Remaining M6:** wire the gated auto-deploy job (cpu-deploy scale set + namespaced RBAC) so a green fork CI triggers `kubectl apply` of this manifest (artifacts exist in `30-arc/cpu-deploy-values.yaml`; the deploy is currently manual/admin).
- **STABLE:** after the probe fix, the new pod ran **1/1 Ready, 0 restarts, 7+ min**, `/generate` works → **M6 serving DONE** (bar the CI-gating).

## 2026-07-09 — M6.5 pod exposure for RDMA (hostNetwork / Model A) validated (RO+CHG)

- **DESIGN:** the backend fabric is L3-routed with per-node /64 SLAAC prefixes, so SR-IOV/macvlan into a pod netns would need the switches to route per-pod addresses (not set up). **Model A** — `hostNetwork: true` + `/dev/infiniband` hostPath + IPC_LOCK/privileged — is the working exposure: the pod inherits the host's proven RoCE routing + GIDs. (`50-rdma/sglang-2node-tp.yaml` already uses Model A.)
- **HOST ionic userspace = MLNX_OFED** (`rdma-core 2410mlnx54`; ionic provider `libionic1` / `libionic-rdmav34.so`). A stock-ubuntu image's upstream rdma-core likely lacks the ionic provider → the container must ship it. The **sglang mi35x image is compatible**.
- **RO validate (rdma-probe pod, sglang image, hostNetwork + /dev/infiniband + privileged, on 136):** in-pod `ibv_devices` = all **8 ionic**; `ibv_devinfo -d ionic_0/3/7` = **PORT_ACTIVE (4), Ethernet**; `ib_write_bw` present. → **di-verify acceptance #1 (ABI match, no MoRI "no active RDMA device") PASSES in-pod.**
- **CHG (artifact fixes, empirically verified):** `50-rdma/sglang-2node-tp.yaml` image → `lmsysorg/sglang:v0.5.14-rocm700-mi35x`, `supplementalGroups [44,992]`, `NCCL_IB_GID_INDEX` 3 → **1** (show_gids: index 0 = fe80 link-local, index 1 = fc01 global IPv6 RoCEv2).
- **RO validate (acceptance #2 — pod-to-pod RDMA):** two rdma-probe pods (135 + 136, hostNetwork), `ib_write_bw -d ionic_0 -x 1` client(136)→server(135, OOB <mesh-ip>) = **213.64 Gb/s** average over RoCEv2 from inside pods. (Lower than the host's 361 Gb/s on this rail — likely NUMA/CPU placement of the pod process, untuned; still proves the pod RDMA data path.) → **pod exposure validated: pods can do RDMA over the backend fabric.**
- **PENDING:** acceptance #3 — a 2-node GPU workload over RoCE (RCCL all_reduce and/or the sglang 2N DI benchmark).

## 2026-07-09 — M6.5 DI run (2-node TP over RoCE): 6 gotchas fixed, blocked on host iommu=pt (CHG)

Deployed `50-rdma/sglang-di-2node.yaml` — 2-node TP=2 (1 MI355X/node), ungated Qwen2.5-7B, hostNetwork, on 135/136. Fixed a chain of real multi-node-RDMA-on-k8s gotchas (all folded into the artifact):
1. **StatefulSet deadlock** → `podManagementPolicy: Parallel` (OrderedReady: pod-0 Ready needs pod-1's rendezvous).
2. **Wrong rank** → hostNetwork makes `$HOSTNAME` the NODE name; derive `--node-rank` from the pod name via downward API `POD_NAME`.
3. **Rendezvous DNS NXDOMAIN** → `publishNotReadyAddresses: true` on the headless svc (leader isn't Ready during rendezvous); `sglang-di-0.sglang-di` now resolves to the leader's mesh IP.
4. **RCCL `ncclCommInitRank` "unhandled system error"** → container `memlock` was 8 MB (pod default); `ulimit -l unlimited` in the command (privileged allows it). After this RCCL selects the **RoCE transport** — logs show all 64 channels `0[0]->1[0] via NET/IB/0` and `NET/IB/1` (ionic rails), bootstrap over spur0.
5. **GPU-memory registration** → peermem path fails ("unhandled system error"); `NCCL_DMABUF_ENABLE=1` (amdgpu dmabuf GPUDirect) gets RCCL **past comm init** (`ncclCommInitRank Init START` completes).
6. **TP model-worker segfault (exit -11)** in `init_tp_model_worker` — the GPUDirect memory path crashes. `NCCL_NET_GDR_LEVEL=0` (host-staged) reverts to the "unhandled system error", so dmabuf is the right direction and the crash is GPUDirect-stability.
- **ROOT CAUSE (confirmed on host):** `dmesg` = `iommu: Default domain type: Translated`, and `/proc/cmdline` has **no `iommu=` param** → the IOMMU runs in *translated* mode, not passthrough. RCCL explicitly warns `Missing "iommu=pt" ... can lead to system instability or hang`. **GPUDirect RDMA (NIC↔GPU memory), which multi-node RCCL requires, needs `iommu=pt` (or `amd_iommu=pt`) on the kernel cmdline → a reboot of the GPU nodes.** Not a pod/k8s fix.
- **STATE:** pod RDMA exposure fully validated (acceptance #1+#2). DI (acceptance #3) reaches RCCL-over-RoCE channel setup + comm init; the full GPUDirect run is blocked pending the `iommu=pt` host reboot (a provisioning decision). Crash-looping StatefulSet deleted; artifact + env preserved for a re-run post-reboot.
- **DECISION (user, 2026-07-09): do NOT reboot.** (SUPERSEDED below — user reversed and asked to reboot with iommu=pt.) M6.5 stands at: RoCE RDMA verified (316–377 Gb/s/rail) + pod exposure validated (in-pod PORT_ACTIVE, 213 Gb/s pod-to-pod) + RCCL confirmed selecting the RoCE transport.

## 2026-07-09 — iommu=pt reboot campaign (CHG) — prep + gotchas

User reversed: proceed to reboot the 4 GPU nodes with `iommu=pt` for GPUDirect DI. Pre-flight found the boot state was **NOT reboot-safe** — fixed before any reboot:
- **k0sworker enabled ✓, `/mnt/m2m_nobackup` in fstab ✓, containerd symlink target auto-mounts ✓.**
- **GOTCHA 1 — spur0 was NOT persistent.** `spur net` brought up the WireGuard mesh manually; no wg-quick conf, no service. A reboot → no spur0 → node can't rejoin (k0s node-IP is the mesh IP). **Fix:** captured the live interface into `/etc/wireguard/spur0.conf` via `wg showconf` (private key stays on-node) + injected `Address`/`MTU`, `systemctl enable wg-quick@spur0`. **Sub-gotcha:** wg-quick's default route table collides with Calico's pod-CIDR routes (`ip route add ... File exists` → wg-quick rolls back, interface stays down). **Fix: `Table = off`** in `[Interface]` — WireGuard keeps the pod CIDRs in AllowedIPs (cryptokey routing) but Calico owns the kernel routes. VALIDATED on 135 with a live `wg-quick down/up` (spur0 restored, controller + cross-node ping OK, Calico re-added pod routes, node stayed Ready). Applied to all 4 configs.
- **GOTCHA 2 — GRUB cmdline is cloud-image controlled.** `/etc/default/grub` edits are overwritten by `/etc/default/grub.d/50-cloudimg-settings.cfg` (sets `GRUB_CMDLINE_LINUX_DEFAULT="console=..."`), then `90-custom-kernel-args.cfg` appends. **Fix:** drop-in `/etc/default/grub.d/95-iommu-pt.cfg` = `GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT iommu=pt"` (sourced last) + `update-grub` → `iommu=pt` confirmed in the `linux` line of `/boot/grub/grub.cfg`.
- **ROLLING:** one node at a time (verify rejoin before the next). Serving PVC is pinned to 136, so 136's reboot briefly stops serving (reschedules on return).
- **135 canary result: iommu=pt WORKS, but a plain `systemctl reboot` CORRUPTS containerd.** 135 booted with `iommu=pt` + `Default domain type: Passthrough` ✓ and spur0 came back via wg-quick ✓ — but the node stuck NotReady. Root cause chain: the reboot killed the **live** containerd uncleanly → its bolt metadata store (`io.containerd.metadata.v1.bolt`) was left corrupt → on boot containerd **hangs during init and never opens `/run/k0s/containerd.sock`** → `k0s worker` fails (`failed to ping containerd: timed out`) → kubelet never posts its lease → node `Ready=Unknown`. (containerd proc was alive but `Sl`/hung, no fatal log.)
- **RECOVERY (135): fresh containerd root.** `systemctl stop k0sworker; pkill -9 k0s/bin/containerd; mv /mnt/m2m_nobackup/containerd → containerd-old-preboot; mkdir fresh; systemctl start k0sworker` → containerd came up in **12 s**, k0sworker active, **135 Ready with iommu=pt**. Confirms the old store was the problem. COST: 135 lost its cached images (re-pullable; old store preserved at `containerd-old-preboot`).
- **FIX for the remaining nodes: graceful shutdown before reboot** (`graceful-reboot.sh`): `systemctl stop k0sworker` → wait for `k0s/bin/containerd` to fully exit (SIGTERM if needed) → `sync` → `systemctl reboot`. Clean containerd shutdown ⇒ no bolt corruption ⇒ images preserved + clean rejoin. **137 = graceful-reboot canary in progress** (verifying containerd comes up clean with its store intact). If clean → apply to 138, 136.
- **PERSIST NOTE:** the containerd relocation (symlink to the big disk) itself is fine on reboot.
- **CORRECTED ROOT CAUSE (from the 137 canary — NOT corruption).** The 137 graceful reboot preserved the store yet containerd STILL hung → so it isn't unclean-shutdown corruption. `fuser meta.db` revealed the truth: **TWO k0s containerd processes** — a stale one (older PID) holding the `io.containerd.metadata.v1.bolt/meta.db` flock via mmap, and the live one **blocked forever at `"waiting for response from boltdb open"`** (bolt `Open` waits on the flock). On boot k0s's first containerd stalls briefly, k0s restarts it, but the stale one lingers holding the lock ⇒ deadlock ⇒ no socket ⇒ k0s worker fails.
- **CORRECT FIX (preserves images):** after the node boots, if the socket is down, **kill the stale containerd holding the lock** (the oldest `k0s/bin/containerd` PID) → the live one acquires the lock, opens meta.db, creates the socket. On 137: `kill -9 <stale>` → socket UP in seconds, **137 Ready with iommu=pt and its 13-dir image store intact.** (135 was needlessly fresh-rooted before I understood this — it lost images; re-pullable.) → automated in `post-reboot-fix.sh` for 138/136.
- **PROGRESS:** 135 ✓ (iommu=pt, images lost), 137 ✓ (iommu=pt, images kept). Remaining: 138, then 136 (serving).
- **REBOOT CAMPAIGN COMPLETE — all 4 nodes `iommu=pt` / Passthrough, all Ready.** 138: killed the stale lock-holder → Ready, images kept. 136 (big serving-image store, so the deadlock formed later): the timed poll missed it, so used the robust `recover2.sh` (stop k0s → `pkill -9` all containerd → `fuser -k meta.db` → restart → single clean containerd) → socket up in 10 s, **images kept**, serving pod rescheduled on 136. Serving briefly interrupted during 136's reboot, back after (model persisted on the PVC).
- **CONTAINERD DEADLOCK RECOVERY (reusable):** after a reboot, if a node is NotReady with containerd socket down: `recover2.sh` (full clean restart, preserves images) is the reliable one; the lighter fix is `kill -9 <oldest k0s/bin/containerd>` once ≥2 procs exist (the deadlock forms ~1–4 min post-boot, later on big-store nodes). Longer-term: fix k0s's containerd-ready timeout / add an ExecStartPre lock-clear so reboots are hands-off.
- **DI RE-RUN:** with all nodes `iommu=pt`, re-applied `sglang-di-2node.yaml` repinned to **137+138**.
- **DI CONCLUSION: iommu=pt did NOT fix the multi-node DI — it's an RCCL↔ionic RDMA incompatibility, not IOMMU.** Evidence: with iommu=pt/Passthrough active there is NO GPU/IOMMU fault in `dmesg`, and single-node serving of the same model/image works (only multi-node crashes). Tried 4 RCCL paths, all fail at ionic memory registration:
  - GDR-on + peermem → "unhandled system error" at comm init; GDR-on + dmabuf → gets past comm init then `-11` SIGSEGV in `init_tp_model_worker`; GDR-off (host-staged) → **`NCCL WARN Call to ibv_reg_mr failed with error Invalid argument`** (EINVAL); adding `NCCL_IB_PCI_RELAXED_ORDERING=0` → same `ibv_reg_mr` EINVAL.
  - `ib_write_bw` works (small standard MR) but RCCL's MR flags/size are rejected by the ionic (AMD Pensando) provider. Not fixable by NCCL env tuning.
- **CORRECT DI PATH (per sglang's own di-verify.md): MoRI / Mooncake, NOT RCCL.** sglang's MI355X 2N-1P1D disagg CI transfers KV-cache over RDMA via **MoRI/Mooncake** (designed for the ionic fabric), as a **prefill/decode disaggregation** — a larger setup than the TP-over-RCCL proxy I used. That is the real DI benchmark for this hardware; the RCCL-TP route is a dead end on ionic.
- **NET STATE of M6.5:** fabric proven (`ib_write_bw` 316–377 Gb/s/rail; RCCL *selects* the RoCE transport); pod RDMA exposure validated (in-pod PORT_ACTIVE, 213 Gb/s pod-to-pod); `iommu=pt` now on all 4 nodes (good for GPUDirect generally). The 2-node RCCL-TP DI is blocked by RCCL↔ionic; a working DI benchmark requires the MoRI/Mooncake disaggregation path.

## 2026-07-10 — M6.5b: MoRI PD-disaggregation DI (CHG, in progress)

- **RESEARCH (5-agent workflow over sglang code + the MI355X CI):** sglang's own nightly `nightly-amd-mi355x-disagg` (2N 1P1D on MI355X + Pensando ionic) uses **`--disaggregation-transfer-backend mori`** (NOT mooncake/RCCL) with fabric env **`MORI_DISABLE_AUTO_XGMI=1 NCCL_IB_HCA=ionic NCCL_IB_GID_INDEX=1 NCCL_CROSS_NIC=1`** and `--disaggregation-ib-device` = a comma-list of RoCE HCA names. Both MoRI and Mooncake register RDMA memory with **RELAXED_ORDERING OFF by default** — the exact flag whose EINVAL killed RCCL — so they should work where RCCL didn't. Our image `lmsysorg/sglang:v0.5.14-rocm700-mi35x` has **mori + mooncake + sglang_router** all importable.
- **NET MODEL (from the disagg conn code):** prefill+decode MUST be hostNetwork (ionic verbs are host-netns only); the prefill↔decode bootstrap+KV plane is DIRECT node-IP:port (NO ClusterIP in the datapath); pin `SGLANG_HOST_IP=status.hostIP` (node mesh IP) so rank_ip/bootstrap_host are routable; the router (pure HTTP) derives bootstrap_host from the `--prefill` URL host, so it must be the prefill NODE IP.
- **CHG deployed `50-rdma/sglang-disagg-mori.yaml`:** prefill StatefulSet (137/<mesh-ip>, hostNetwork, MoRI, `--disaggregation-mode prefill --port 30000 --disaggregation-bootstrap-port 8998 --disaggregation-ib-device ionic_0,ionic_1,ionic_2,ionic_3`, ungated Qwen2.5-7B tp1) + decode StatefulSet (138/<mesh-ip>, `--disaggregation-mode decode --port 30001 --disaggregation-bootstrap-port 9001`) + router Deployment (`sglang_router.launch_router --pd-disaggregation --prefill http://<mesh-ip>:30000 8998 --decode http://<mesh-ip>:30001 --port 8000 --disable-circuit-breaker`) + ClusterIP svc. `ulimit -l unlimited` + IPC_LOCK + /dev/infiniband + amd.com/gpu:1.
- **EARLY SIGNAL: MoRI does NOT hit RCCL's wall** — prefill logs `CommonKVBootstrapServer started successfully on 0.0.0.0:8998`, no `ibv_reg_mr`/EINVAL. Watching prefill readiness + the MoRI KV-buffer registration, then a `/generate` through the router (the real DI test).

## 2026-07-10 — M6.5b MoRI DI: rendezvous FIXED, RDMA transfer BLOCKED → PAUSED (user)

- **`/generate` hung (curl exit 28) across several attempts.** Full diagnosis (5 layers ruled out): ionic fabric 137↔138 `ib_write_bw` connects; MoRI IOEngine inits (`ionic_0..3` `ctx 6`); NO kernel `reg_mr`/dmabuf/amdgpu errors; address plumbing correct (`get_local_ip_auto=<mesh-ip>`); router config correct. The hang was the **decode↔prefill rendezvous**, then the **RDMA transfer**.
- **ROOT CAUSE #1 (FIXED) — stale ephemeral ZMQ `rank_port`.** sglang's MoRI KVReceiver caches the prefill's random ZMQ `rank_port` (advertised via bootstrap :8998) for the decode's **whole process lifetime** (`disaggregation/common/conn.py connection_pool`). The prefill **crash-restarts once at startup** (first-`/generate` 500 during aiter JIT) → comes back with a NEW rank_port; the decode keeps ZMQ-PUSHing to the dead old port (PUSH never errors) → prefill stuck at `#bootstrap-req: 1`, decode `KVPoll.WaitingForInput` timeout → `KVTransferError: Aborted by AbortReq`. **FIX = ordered restart (prefill ready FIRST, then decode)** so the decode re-resolves the live port. Verified: decode `--log-level debug` logs `Fetched bootstrap info: {rank_port: 35621}` == prefill's `ss -tlnH` bound port; `#bootstrap-req` released 1→0. (Also must restart the router after scaling the decode: it opens the circuit → `No available decode workers`.)
- **ROOT CAUSE #2 (BLOCKED) — ionic lossless-RoCE QoS not configured.** Even with the rendezvous fixed, the transfer hangs: prefill never fires `register_remote_engine`, `rdma resource show` stays `mr 0`. MoRI needs `mori setup` (`mori/tools/env_setup.sh`) → **`nicctl`** to set PFC/DCQCN + export `MORI_RDMA_SL`/`MORI_RDMA_TC` (recipe: no-drop prio 3, DSCP 26 → `TC=104`). On these OS-reinstalled hosts **`nicctl` = "No AMD NICs detected"** (no `nicmgr` daemon; only `amd-nic-metrics-exporter`) → QoS unconfigurable. Setting `MORI_RDMA_SL=3`/`TC=104` manually did NOT fix it; `MORI_DEBUG_INFO=1` gave no RDMA logs. Same class of ionic GPU-RDMA dead-end as RCCL.
- **PAUSED by user.** DI scaled to 0 (config preserved in `50-rdma/sglang-disagg-mori.yaml`; SL/TC + waiting-timeout + debug now baked in). Resume options: Mooncake backend (has `MC_FORCE_TCP=1`), fix `nicctl`/`nicmgr`, or single-node co-located PD. Full write-up in memory `spur-mori-di-rendezvous-and-ionic-blocker`.

## 2026-07-10 — M6 gated auto-deploy pipeline WIRED (CHG)

- **Goal:** green fork CI → auto-deploy the sglang serving (`kubectl apply` from a non-GPU runner). Cluster side built + validated, then the fork CI wired.
- **CHG applied `40-serving/rbac-deployer.yaml`:** SA `sglang-deployer` (ns arc-runners) + Role/RoleBinding (ns sglang) for deploy/svc/pvc/pods/configmaps/ingress — **create/update/patch only** (no delete, no secrets read, no cross-ns). Verified with `auth can-i --as=system:serviceaccount:arc-runners:sglang-deployer`.
- **CHG `helm install linux-cpu-deploy` (gha-runner-scale-set 0.14.2):** non-GPU deploy scale set (`30-arc/cpu-deploy-values.yaml`), reuses the local `sglang-runner:rocm` image (`imagePullPolicy: Never`, has kubectl baked in per `Dockerfile.runner`), `serviceAccountName: sglang-deployer`, no `amd.com/gpu`. Listener connected. (Dropped the nonexistent `ghcr-pull` imagePullSecret; added `imagePullPolicy: Never` + nodeSelector.)
- **VALIDATED deploy path end-to-end** via a one-off Job in arc-runners as the SA: runner image has `kubectl v1.36.2`, the auto-mounted in-cluster token reaches the API (`kubectl get deploy sglang -n sglang` → 1/1 from inside the pod), RBAC scoped (update deploy=yes, get secrets=no).
- **CHG pushed to `powderluv/sglang` main (commit `1792d6fcf`, user-authorized):** `.github/workflows/fork-ci.yml` gains a `deploy` job (`needs: amd-gpu-test`, `if: success()`, `runs-on: linux-cpu-deploy`) that `kubectl apply`s the vendored `deploy/k8s/sglang-service-amd.yaml` (+ `ingress.yaml`, non-fatal) then `rollout status deploy/sglang`; vendored both manifests to `deploy/k8s/`. The serving manifest == the running deploy exactly (idempotent apply). Push triggered run 29097178304 (amd-gpu-test → deploy). NOTE: the push also fires leftover upstream sglang workflows (PR Test XPU/NPU/Xeon) that fail on missing runners — disable those on the fork to cut noise.
