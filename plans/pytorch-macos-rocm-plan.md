# PyTorch on macOS + ROCm (gfx1201 eGPU) — Implementation Plan

**Status:** in progress · **Target:** import-able `torch` with a HIP backend on Apple
Silicon, running on the RX 9070 XT (gfx1201/RDNA4) Thunderbolt eGPU.
**Last updated:** 2026-06-01.

This plan is grounded in (a) a multi-agent analysis of TheRock's PyTorch build
pipeline, the macOS eGPU port, upstream feasibility, and Apple-Silicon toolchain
blockers, and (b) **observed** results: a clean `USE_ROCM:ON` CMake configure and a
successful `libc10_hip.dylib` build on macOS arm64. Items still inferred (not yet
observed) are marked *(inferred)*.

---

## 1. Goal & scope

Build PyTorch so that, on macOS (Apple Silicon), `import torch` exposes the AMD eGPU
as a HIP device (`torch.version.hip` set, a `cuda`-namespaced device backed by HIP)
and can run real tensor ops on gfx1201. This is **compute-only** (no Metal/display);
it rides the experimental userspace ROCm stack already in this workspace (DriverKit
DEXT + ported ROCR/HSA + a full macOS ROCm SDK), not anything from AMD.

Out of scope (initially): distributed/RCCL, flash-attention/aotriton, fused SDPA,
torch.compile/inductor, profiler (kineto/roctracer), multi-GPU.

## 2. Executive summary / feasibility verdict

**Feasible, and further along than expected.** The build-side blockers that looked
hardest (HIP-on-Mach-O codegen, the CMake HIP toolchain, library packaging) are
already solved in the workspace's `amd-llvm`/SDK: the macOS ROCm clang compiles
`.hip` → arm64-darwin host + amdgcn device and embeds the fatbin in Mach-O `__HIP`
sections; a full macOS ROCm SDK (134 Mach-O dylibs + CMake config packages + 71
amdgcn bitcode libs) exists at `build-macos-egpu/dist/rocm`.

The remaining work splits into:
- **Mechanical (small):** three PyTorch CMake patches (done) + Python packaging.
- **The real hard problem (runtime, not build):** the validated macOS execution path
  is a *single forced compute HQD, strictly serialized, single-process*; PyTorch's
  HIP caching allocator + multiple async streams is fundamentally at odds with it.
  This — not compilation — is the crux and gates anything beyond a single-stream,
  single-process first milestone.

## 3. Current state (observed)

| Component | State |
|-----------|-------|
| macOS ROCm SDK (`build-macos-egpu/dist/rocm`) | Complete: 134 Mach-O arm64 dylibs, `lib/llvm/bin/clang++`, `clang-offload-bundler`, 71 amdgcn bitcode libs |
| ROCm CMake Config packages | All PyTorch-required present (hip, rocblas, hipblas, hipblaslt, **miopen 3.5.1**, hipfft, hipsparse, rocsolver, hipsolver, rocrand, hiprand, rocprim, hipcub, rocthrust, amd_comgr, hiprtc, **hsa-runtime64**, rccl, rocm-core, **hip-lang**). **Only `rocm_smi` missing.** |
| PyTorch checkout | `pytorch/pytorch` nightly `dbc77c6a09` (torch **2.13.0a0**), hipified; branch `users/powderluv/macos-egpu` in `external-builds/pytorch/pytorch` |
| CMake configure (`USE_ROCM:ON`, gfx1201) | **PASSES** — ROCm 7.13, `TORCH_HIP_VERSION=713`, BLAS=Accelerate, `build.ninja` generated |
| `libc10_hip.dylib` | **Builds** → Mach-O arm64 dylib, warnings only |
| `libtorch_hip.dylib` (full ATen HIP, 2680 TUs) | ✅ **Builds + links** → 233 MB Mach-O arm64 dylib (links libamdhip64/MIOpen/rocblas/roctx64) |
| `libtorch_python.dylib` (27 MB) + `libtorch.dylib` | ✅ **Builds + links** (clean — roctx was the only strict-linker issue) |
| `_C` extension + wheel / importable package | ✅ **Built + imports** — `torch-2.13.0a0+gitbf08e02-cp311-macosx-arm64.whl` (121 MB); `torch.version.hip=7.13.60980`, `torch.cuda.is_available()=True`, `device_count()=1` (sees gfx1201 via DEXT); CPU op works. ROCm dylibs not bundled (absolute SDK rpath baked in — delocate later). |

**Validated on hardware (from the eGPU port, serial single-process):** HIP runtime +
hipcc, rocBLAS/hipBLAS+Tensile (SGEMM/SAXPY), rocSOLVER (POTRF), rocRAND. **Fragile:**
rocFFT (C2C only via out-of-process RTC). **Partial:** rocSPARSE (legacy only).
**Builds-but-unvalidated:** MIOpen, RCCL, composable_kernel.

## 4. PyTorch version decision

Currently on **nightly 2.13.0a0** (what TheRock's checkout pulls; matches the macOS
SDK's ROCm 7.13). Trade-off:
- **nightly 2.13.0a0 (current):** matches the SDK's ROCm 7.13 hipify expectations;
  moving HEAD.
- **stable (ROCm/pytorch fork, e.g. 2.9.x):** ROCm-validated hipify, fixed target;
  but built against older ROCm than our 7.13 SDK.

**Recommendation:** stay on the checked-out nightly for the bring-up spike (it
configures+compiles today), and pin to a fork tag once the first op runs, to stop
chasing HEAD. Revisit if nightly churn causes friction.

## 5. The PyTorch source patches (branch `users/powderluv/macos-egpu`)

Seven changes across four files, all macOS-guarded. The CMake ones got configure to
pass; the rest fell out of the actual build and are confirmed by observation. All are
candidates to upstream into the TheRock pytorch fork behind `if(APPLE)`/`__APPLE__`.

**Configure (reach `enable_language(HIP)` + find packages):**
1. **`CMakeLists.txt`** — `USE_ROCM` gate widened to `"LINUX OR WIN32 OR APPLE"`.
   Without it `cmake_dependent_option` force-sets `USE_ROCM=OFF` on Darwin and
   `-DUSE_ROCM=ON` is silently ignored.
2. **`cmake/public/LoadHIP.cmake`** — `rocm_smi` `find_package` made non-`REQUIRED`
   on Apple (the only SDK package missing; only used under distributed, off on mac).
3. **`cmake/public/LoadHIP.cmake`** — `if(APPLE)` branch forcing the HIP compiler id
   (`CMAKE_HIP_COMPILER_FORCED/WORKS/ID/FRONTEND_VARIANT`) + `CMAKE_HIP_COMPILER_ROCM_ROOT`.
   CMake's HIP compiler-id test yields "unknown" on Darwin → it skips the ROCm-root
   probe and `hipconfig` isn't on PATH → "Failed to find ROCm root directory". The
   ROCm clang compiles `.hip` fine, so we force the identity and supply the root.

**Compile (the actual build):**
4. **`cmake/public/LoadHIP.cmake`** — append to `CMAKE_HIP_FLAGS` on Apple:
   `-x hip` (the generated `UfuncCUDA_*.cu` files default to CUDA → "unsupported CUDA
   gpu architecture: gfx1201"); `-isysroot ${CMAKE_OSX_SYSROOT}` (HIP device compile
   couldn't find libc++ `<cmath>`); `-D_VSTD=std` (ROCm's bundled thrust uses the
   `_VSTD` macro the current macOS SDK libc++ removed); `-DNDEBUG` (macOS
   `assert`→`__assert_rtn` is host-only → breaks rocPRIM/hipCUB device code).
5. **`torch/headeronly/util/Half.h`** — `std::memcpy`→`__builtin_memcpy` on Apple in
   the 3 `__host__ __device__` bit-cast helpers (host-only `std::memcpy` in device code).
6. **`aten/src/ATen/native/hip/int4mm.hip`** — extend the existing `_WIN32 && USE_ROCM`
   raw-`memcpy` guard to `__APPLE__` (same host-only `std::memcpy`-in-device issue).

**Link:**
7. **`cmake/Dependencies.cmake`** — link `ROCM_ROCTX_LIB` into `torch_hip` on Apple.
   The hipified profiler stub references roctx symbols; Linux leaves them undefined in
   the `.so` (resolved later via `torch_python`), but the macOS linker is strict.

Note: item 4's `-isysroot`/`-D_VSTD` are a consequence of the Xcode 26.4→26.5 SDK roll
mid-effort (newer libc++); they may need revisiting on other SDK versions.

## 6. Reproducing the configure

```bash
SDK=/Users/anush/github/TheRock/build-macos-egpu/dist/rocm
PT=/Users/anush/github/TheRock/external-builds/pytorch/pytorch
export ROCM_PATH=$SDK HIP_PATH=$SDK PYTORCH_ROCM_ARCH=gfx1201
export PATH="$SDK/bin:$SDK/lib/llvm/bin:$PATH"
# venv cmake must be >=3.27,<4 (3.31 ok; 4.x trips old third_party cmake_minimum)
cmake -S "$PT" -B "$PT/build-macos-rocm" -GNinja \
  -DCMAKE_AR=$(brew --prefix llvm)/bin/llvm-ar \
  -DCMAKE_RANLIB=$(brew --prefix llvm)/bin/llvm-ranlib \
  -DUSE_ROCM=ON -DUSE_CUDA=OFF -DUSE_XPU=OFF -DUSE_MPS=OFF \
  -DCMAKE_HIP_ARCHITECTURES=gfx1201 \
  -DUSE_ROCM_CK_GEMM=OFF -DUSE_ROCM_CK_SDPA=OFF \
  -DUSE_FLASH_ATTENTION=OFF -DUSE_MEM_EFF_ATTENTION=OFF \
  -DUSE_KINETO=OFF -DUSE_MAGMA=OFF -DUSE_MKLDNN=OFF -DUSE_FBGEMM=OFF \
  -DUSE_DISTRIBUTED=OFF -DBUILD_TEST=OFF -DBLAS=vecLib \
  -DCMAKE_OSX_ARCHITECTURES=arm64
```
Prereqs in the venv: `numpy`, `pyyaml`, `typing_extensions` (from
`pytorch/requirements.txt`), `cmake>=3.27,<4`, `ninja`; Homebrew `llvm` (for
`llvm-ar` @response-file support) and `libomp` (OpenMP).

## 7. Phased plan

- **Phase A — Clean configure.** ✅ Done.
- **Phase B — Build `libtorch_hip.dylib`.** In progress. The full ATen HIP kernel
  surface for gfx1201; the toolchain stress test. Watch for: any kernel using a
  construct the macOS clang/Mach-O path rejects, the large final link, and GNU
  linker flags on the `torch_hip` link *(inferred — c10_hip linked clean)*.
- **Phase C — Python packaging → importable torch.** Either continue raw cmake/ninja
  and assemble the `torch/` package + `_C` extension manually, or switch to
  `setup.py develop` driven by env vars (`USE_ROCM=1 PYTORCH_ROCM_ARCH=gfx1201
  ROCM_PATH=... USE_*=0 ...` + `CMAKE_ARGS`). setup.py is the supported route to a
  wheel but re-runs its own configure; the env must reproduce §6. Confirm
  `torch.version.hip` is populated.
- **Phase D — HIP runtime spike (needs hardware).** Before trusting torch on the GPU,
  compile+run one gfx1201 kernel from a plain C++ process against `libamdhip64.dylib`
  on the phase-9-brought-up eGPU. De-risks the single-forced-HQD path from a
  non-Python process.
- **Phase E DONE (2026-06-01): first PyTorch eager GPU op runs on the eGPU.**
  `torch.ones(4, device='cuda') + 1.0` → `[2,2,2,2]` on gfx1201, correct, no wedge.
  Recipe: run phase-9 bring-up (`PHASE9_SKIP_NOP=1 PHASE9_SEND_SET_HW_RSRC=1
  PHASE9_SEND_SET_HW_RSRC_1=1 PHASE9_MAP_SCHED=1` → 6 fences signaled) once per
  power-cycle, then run torch with the validated ROCr direct-queue env (HOST_BLIT_ONLY,
  SKIP_DESTROY, DEQUEUE_AFTER_SUBMIT, ROTATE_BACKING_AFTER_DEQUEUE, MAX_QUEUES=6,
  PQ_CONTROL=userspace) + `DYLD_LIBRARY_PATH=$SDK/lib`.
  Root cause of the pre-bring-up failure: `hsa_queue_create failed` (no HQD) →
  `hipErrorIllegalState`; `hipMalloc` already worked. Also validated: a 128x128 fp32
  matmul (hipBLASLt/rocBLAS+Tensile) matches CPU to 2.3e-05, no wedge. Still
  single-stream/sequential; multi-op chains, concurrent/multi-stream, and real models
  are unproven (the caching-allocator vs single-HQD question below).
- **Phase E — First milestone: `import torch` + one eager op.** On one default stream
  (no concurrent streams). Operationally: phase-9 bring-up must have run since the
  last power-cycle. Expect to constrain torch to a single stream.
- **Phase F — Broaden ops; confront the runtime model.** Map which ATen ops work on
  the serialized HQD; investigate whether the caching allocator + a long-lived
  process stays safe (validation discipline says cross-process HQD reuse is unsafe
  and parallel submits poison the queue). Multi-stream needs the pending MES
  scheduler queue-activation work, not a torch-side fix.

## 8. Hard blockers & risks

1. **Execution model mismatch (HARD).** Validated path = single forced compute HQD,
   strict serialization, single-process; max 6 HQDs, HQD6/7 poison, cross-process
   reuse unsafe, dequeue-after-submit required. PyTorch assumes multiple async
   `hipStream`s + an async caching allocator. *Mitigation:* force single default
   stream initially; real multi-stream depends on MES queue activation (pending).
2. **Phase-9 bring-up mandatory (HARD, operational).** A Python bring-up must run once
   per power-cycle before any HIP process; a wedge needs a physical enclosure
   power-cycle (no software FLR). torch attaches to an already-up GPU.
3. **256 MB BAR0 (ReBAR off).** May constrain `hipMalloc`/pinned memory/large
   allocations under the DEXT `IOConnectMapMemory64` model. *(inferred)*
4. **Library maturity.** MIOpen builds but is unvalidated on hw (it's a required link
   once `USE_ROCM`); rocFFT fragile; rocSPARSE partial. Conv-heavy / FFT / sparse
   workloads are at risk.
5. **Multithreaded import-time loading.** torch's `_C` (.so) loading `libamdhip64.dylib`
   via `@rpath` under a hardened/SIP Python may need `DYLD_*`/install_name handling.
   *(inferred)*
6. **Entitlements.** Distribution needs Apple-approved managed DriverKit entitlements;
   development works via the DEXT's `allow-any-userclient-access`.

## 9. Alternatives considered

- **PyTorch MPS backend instead of ROCm.** Rejected: MPS targets the Apple integrated
  GPU via Metal; the AMD eGPU is compute-only and not a Metal device. MPS would not
  use the eGPU or the ROCm stack at all.
- **tinygrad TinyGPU runtime instead of ROCm/HIP.** Rejected as the torch target:
  TinyGPU (Apple-approved, RDNA3+/Ampere+ eGPU compute) exposes only tinygrad's
  runtime, not a HIP/HSA API. PyTorch-ROCm links HIP, so TinyGPU doesn't provide the
  link surface. (It does validate the hardware premise.)
- **Stable ROCm/pytorch fork tag (2.9.x) instead of nightly 2.13.0a0.** Considered;
  see §4. Deferred — nightly configures+compiles against our ROCm 7.13 SDK today;
  pin later.
- **TheRock `build_prod_wheels.py` harness instead of direct cmake/setup.py.**
  Rejected for bring-up: it is Linux/Windows-only, assumes `rocm-sdk` pip wheels with
  `.so` names and `RTLD_GLOBAL` preload, and has no Darwin branch. Direct cmake/setup
  against the local `.dylib` SDK is the bring-up path; a Darwin arm of the harness is
  a later productization step.
- **IREE/Fusilli compile path instead of eager ROCm.** Complementary, not a
  replacement — different execution model (already validated for one pointwise op via
  IREE). Eager ROCm torch is the goal here.
- **Wait for the MES scheduler (real multi-queue) before any torch.** Rejected as a
  gate: a single-stream eager torch is achievable on the direct HQD now; multi-stream
  is a Phase-F concern.

## 10. Open questions

- Does the full `libtorch_hip.dylib` link clean, or surface GNU-linker-flag /
  `--whole-archive` / version-script issues the way ROCm's own libs once did?
- Can torch be constrained to a single `hipStream` end-to-end (allocator + ops), and
  does a long-lived process stay safe on the single HQD?
- Does `setup.py` reliably propagate `USE_ROCM` on Darwin (historically fragile,
  pytorch #103312), or is manual package assembly cleaner?
- Does `torch.version.hip` get populated on a Darwin build (version-gen path), needed
  for `IS_HIP_EXTENSION` and the `cuda`-device shim?
- How does `hipMalloc` behave under the 256 MB BAR window for realistic tensor sizes?
