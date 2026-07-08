#!/usr/bin/env python3
"""spill_probe.py -- torch-free minimal register-spilling kernel launcher for the
gfx1201 (RDNA4) ROCr Windows lite:: direct-HQD scratch path (task #57 / macOS #15).

Builds (if needed) and launches spill_kernel.hip via hipModuleLaunchKernel, which
routes through the SAME ROCr AQL scratch-dispatch code as a torch matmul -- but
with NO torch / hipBLASLt and a known 256-byte private segment. This lets the
gfx12 architected-flat-scratch wave-retirement blocker be reproduced (and, once
fixed, confirmed) in ONE GPU op per power cycle, instead of driving the whole
torch/hipBLASLt GEMM stack.

  PASS  => the minimal spilling wave RETIRES on the direct HQD (fix works).
  HANG  => hipDeviceSynchronize never returns / poison survives (the #57 blocker).

The ROCr direct-compute scratch knobs are set below BEFORE amdhip64_7.dll loads
(they are read at driver init). Export any of them first to override (setdefault).

Run in the win11-gpu guest as the FIRST GPU-touching op after a fresh power cycle:
    python spill_probe.py                 # build if needed, then launch + verify
    python spill_probe.py --co C:\\hiprun\\spill.hsaco --grid 4 --block 64
    python spill_probe.py --build         # force rebuild the .hsaco
    python spill_probe.py --no-launch     # module-load only (isolate load vs dispatch)
"""

import argparse
import ctypes
import os
import pathlib
import shutil
import subprocess
import sys

# --- ROCr lite:: direct-compute scratch env (must be set before amdhip64 init) ---
DEFAULT_ENV = {
    "ROCR_WINDOWS_FORCE_DIRECT_COMPUTE": "1",
    "ROCR_AMDGPU_LITE_HOST_BLIT_ONLY": "1",
    "ROCR_LITE_DEVICE_ONLY_SKIP_MEMSET": "1",
    "ROCR_WINDOWS_SHMEM_APERTURE": "1",
    "ROCR_MACOS_AQL_ENABLE_SCRATCH": "1",
    "ROCR_WINDOWS_AQL_EOP_FENCE": "1",
    "ROCR_MACOS_AQL_SKIP_POST_ACQUIRE": "1",
    "ROCR_WINDOWS_TRACE_DIRECT_QUEUE": "1",
    "ROCR_MACOS_TRACE_AQL": "1",
}


def log(msg: str) -> None:
    print(f"[spill] {msg}", flush=True)


def set_default_env() -> None:
    for key, value in DEFAULT_ENV.items():
        os.environ.setdefault(key, value)


def rocm_sdk_bin() -> pathlib.Path | None:
    """Return the SDK bin dir (mirrors the mm_*.bat `rocm_sdk path --root` step)."""
    try:
        root = subprocess.check_output(
            [sys.executable, "-m", "rocm_sdk", "path", "--root"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not root:
        return None
    return pathlib.Path(root) / "bin"


def add_sdk_to_path(bin_dir: pathlib.Path | None) -> None:
    if bin_dir is None or not bin_dir.is_dir():
        log("WARNING: rocm_sdk bin not found; relying on ambient PATH for amdhip64")
        return
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(bin_dir))
    log(f"SDK bin on PATH: {bin_dir}")


def build_hsaco(src: pathlib.Path, out: pathlib.Path) -> None:
    hipcc = shutil.which("hipcc") or shutil.which("hipcc.bat")
    if hipcc is None:
        raise RuntimeError("hipcc not found on PATH (need the SDK bin dir)")
    cmd = [hipcc, "--genco", "--offload-arch=gfx1201", str(src), "-o", str(out)]
    log("building: " + " ".join(cmd))
    subprocess.run(cmd, check=True)
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"build produced no/empty {out}")
    log(f"built {out} ({out.stat().st_size} bytes)")


# --- HIP ctypes bindings ------------------------------------------------------

HIP_SUCCESS = 0
HIP_MEMCPY_H2D = 1
HIP_MEMCPY_D2H = 2


def load_hip(bin_dir: pathlib.Path | None) -> ctypes.CDLL:
    # Load by explicit SDK-bin path so a stale amdhip64 in the CWD can't win.
    target = "amdhip64_7.dll"
    if bin_dir is not None and (bin_dir / target).is_file():
        target = str(bin_dir / target)
    log(f"loading {target}")
    hip = ctypes.CDLL(target)
    hip.hipMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
    hip.hipMemcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    hip.hipModuleLoad.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
    hip.hipModuleGetFunction.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_char_p]
    hip.hipModuleLaunchKernel.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
        ctypes.c_uint, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    return hip


def ck(hip: ctypes.CDLL, name: str, rc: int) -> None:
    log(f"{name} -> {rc}")
    if rc != HIP_SUCCESS:
        print(f"FAIL {name} hipError={rc}", flush=True)
        sys.exit(rc if rc else 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    # NB: no .resolve() -- realpath() raises WinError 1005 on the Z: virtiofs share.
    here = pathlib.Path(__file__).parent
    ap.add_argument("--co", default=str(here / "spill.hsaco"), help="code object path")
    ap.add_argument("--src", default=str(here / "spill_kernel.hip"))
    ap.add_argument("--grid", type=int, default=4, help="grid dim in BLOCKS")
    ap.add_argument("--block", type=int, default=64, help="block dim in threads")
    ap.add_argument("--iters", type=int, default=1,
                    help="launch the kernel N times on the same queue (#62 repro)")
    ap.add_argument("--build", action="store_true", help="force rebuild the .hsaco")
    ap.add_argument("--no-launch", action="store_true", help="module-load only")
    args = ap.parse_args()

    set_default_env()
    bin_dir = rocm_sdk_bin()
    add_sdk_to_path(bin_dir)

    co = pathlib.Path(args.co)
    if args.build or not co.is_file():
        build_hsaco(pathlib.Path(args.src), co)

    hip = load_hip(bin_dir)
    ck(hip, "hipInit", hip.hipInit(0))
    ndev = ctypes.c_int(-1)
    hip.hipGetDeviceCount(ctypes.byref(ndev))
    log(f"devices={ndev.value}")
    if ndev.value < 1:
        print("NO_DEVICE", flush=True)
        return 1
    ck(hip, "hipSetDevice", hip.hipSetDevice(0))

    total = args.grid * args.block
    nbytes = total * 4

    d = ctypes.c_void_p()
    ck(hip, "hipMalloc", hip.hipMalloc(ctypes.byref(d), nbytes))
    poison = (ctypes.c_int * total)(*([-777] * total))
    ck(hip, "hipMemcpy(H2D poison)",
       hip.hipMemcpy(d, poison, nbytes, HIP_MEMCPY_H2D))
    log(f"d={hex(d.value or 0)} seeded poison ({total} ints)")

    mod = ctypes.c_void_p()
    ck(hip, "hipModuleLoad", hip.hipModuleLoad(ctypes.byref(mod), str(co).encode()))
    fn = ctypes.c_void_p()
    ck(hip, "hipModuleGetFunction",
       hip.hipModuleGetFunction(ctypes.byref(fn), mod, b"spill"))
    log(f"module={hex(mod.value or 0)} fn={hex(fn.value or 0)}")

    if args.no_launch:
        print(f"SPILL_LOADONLY_OK fn={hex(fn.value or 0)}", flush=True)
        return 0

    # kernarg blob { int* out } ; passed via the `extra` buffer (magic 1/2/3).
    kern = (ctypes.c_void_p * 1)(d.value)
    kern_sz = ctypes.c_size_t(ctypes.sizeof(kern))
    extra = (ctypes.c_void_p * 5)(
        ctypes.c_void_p(1), ctypes.cast(kern, ctypes.c_void_p),
        ctypes.c_void_p(2), ctypes.cast(ctypes.byref(kern_sz), ctypes.c_void_p),
        ctypes.c_void_p(3),
    )
    # Launch the spilling kernel --iters times on the SAME default queue to
    # reproduce the multi-op-per-process hang (#62): op 1 retires, a subsequent
    # spilling dispatch on the same queue hangs. Re-seed poison each iteration so
    # a non-executing dispatch is detectable.
    rc = 0
    bad = 0
    for it in range(args.iters):
        ck(hip, f"hipMemcpy(H2D poison it{it})",
           hip.hipMemcpy(d, poison, nbytes, HIP_MEMCPY_H2D))
        log(f"iter {it}: launching spill grid({args.grid},1,1) block({args.block},1,1)")
        ck(hip, f"hipModuleLaunchKernel it{it}",
           hip.hipModuleLaunchKernel(fn, args.grid, 1, 1, args.block, 1, 1,
                                     0, None, None, extra))
        # If a subsequent spilling dispatch never retires (#62), this hangs.
        ck(hip, f"hipDeviceSynchronize it{it}", hip.hipDeviceSynchronize())
        out = (ctypes.c_int * total)()
        ck(hip, f"hipMemcpy(D2H it{it})",
           hip.hipMemcpy(out, d, nbytes, HIP_MEMCPY_D2H))
        bad = sum(1 for t in range(total) if out[t] != 256 * t + 32640)
        poison_left = sum(1 for t in range(total) if out[t] == -777)
        log(f"iter {it}: verify bad={bad}/{total} poison_left={poison_left} "
            f"out[0]={out[0]}")
        if bad:
            rc = 3

    hip.hipFree(d)
    hip.hipModuleUnload(mod)
    print(f"SPILL_DONE iters={args.iters} last_bad={bad}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
