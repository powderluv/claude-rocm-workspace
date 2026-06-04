// Minimal multi-dispatch unit test for the macOS eGPU lite:: path (gfx1201).
// Reproduces the torch dispatch_count "count-~15 ceiling" WITHOUT torch:
// launches a trivial increment kernel N times through the same ROCr/HIP MES
// path, synchronizing each iteration (like torch's .item()), and verifies the
// result. Reports exactly which dispatch failed.
//
// Build:
//   ROCM_PATH=<dist> HIP_PATH=<dist> SDKROOT=$(xcrun --show-sdk-path) \
//   <dist>/bin/hipcc --offload-arch=gfx1201 multi_dispatch_test.cpp -o multi_dispatch_test
// Run (after phase-9 bring-up, with the lite:: MES env):
//   ROCR_MACOS_USE_MES_QUEUE=1 ... ./multi_dispatch_test [N]
#include <hip/hip_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <unistd.h>

__global__ void inc(float* x, int n) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) x[i] += 1.0f;
}

#define CHECK(call, what)                                                   \
  do {                                                                      \
    hipError_t _e = (call);                                                 \
    if (_e != hipSuccess) {                                                 \
      printf("FAIL: %s: %s\n", what, hipGetErrorString(_e));                \
      return 2;                                                             \
    }                                                                       \
  } while (0)

int main(int argc, char** argv) {
  const int N = argc > 1 ? atoi(argv[1]) : 200;
  const int delay_us = argc > 2 ? atoi(argv[2]) : 0;  // inter-dispatch idle (diag)
  const int n = 256;
  float* x = nullptr;
  CHECK(hipMalloc(&x, n * sizeof(float)), "hipMalloc");
  CHECK(hipMemset(x, 0, n * sizeof(float)), "hipMemset");
  CHECK(hipDeviceSynchronize(), "init sync");

  int survived = 0;
  for (int i = 0; i < N; i++) {
    hipLaunchKernelGGL(inc, dim3(1), dim3(n), 0, 0, x, n);
    hipError_t e = hipDeviceSynchronize();  // forces completion (like torch .item())
    if (e != hipSuccess) {
      printf("DIED at dispatch %d (of %d): %s\n", i, N, hipGetErrorString(e));
      return 1;
    }
    survived = i + 1;
    if (delay_us) usleep(delay_us);  // extra idle between dispatches (time-vs-count test)
  }

  float h[256];
  CHECK(hipMemcpy(h, x, n * sizeof(float), hipMemcpyDeviceToHost), "copyback");
  bool ok = true;
  for (int i = 0; i < n; i++)
    if (h[i] != (float)N) { ok = false; break; }
  printf("SURVIVED %d dispatches; verify=%s (x[0]=%.1f expected=%d)\n",
         survived, ok ? "PASS" : "FAIL", h[0], N);
  return ok ? 0 : 3;
}
