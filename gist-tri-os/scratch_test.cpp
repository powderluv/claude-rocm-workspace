// gfx12 scratch validation: a kernel that register-spills to private/scratch
// (large local array with dynamic indexing), dispatched through the macOS lite::
// MES path. Pre-fix this faulted 0x1000 (FLAT_SCRATCH=0 / GCVM perm-fault@VA0).
#include <hip/hip_runtime.h>
#include <cstdio>
#include <cstdlib>

extern "C" __global__ void scratch_kernel(unsigned* out, unsigned n) {
  unsigned tid = threadIdx.x + blockIdx.x * blockDim.x;
  if (tid >= n) return;
  unsigned local[96];
#pragma unroll 1
  for (unsigned i = 0; i < 96; i++) local[i] = tid * 7u + i;
  unsigned s = 0;
#pragma unroll 1
  for (unsigned i = 0; i < 96; i++) s += local[(local[i] + i) % 96u];  // dynamic idx -> scratch
  out[tid] = s;
}

static unsigned ref(unsigned t) {
  unsigned local[96];
  for (unsigned i = 0; i < 96; i++) local[i] = t * 7u + i;
  unsigned s = 0;
  for (unsigned i = 0; i < 96; i++) s += local[(local[i] + i) % 96u];
  return s;
}

int main(int argc, char** argv) {
  const unsigned N = argc > 1 ? atoi(argv[1]) : 256;
  unsigned* d_out = nullptr;
  unsigned* h_out = new unsigned[N];
  if (hipMalloc(&d_out, N * sizeof(unsigned)) != hipSuccess) { printf("hipMalloc fail\n"); return 2; }
  hipLaunchKernelGGL(scratch_kernel, dim3((N + 63) / 64), dim3(64), 0, 0, d_out, N);
  hipError_t e = hipDeviceSynchronize();
  if (e != hipSuccess) { printf("SCRATCH DISPATCH FAILED: %s\n", hipGetErrorString(e)); return 1; }
  hipMemcpy(h_out, d_out, N * sizeof(unsigned), hipMemcpyDeviceToHost);
  int mism = 0;
  for (unsigned t = 0; t < N; t++) {
    unsigned exp = ref(t);
    if (h_out[t] != exp) { if (mism < 3) printf("  mism t=%u got=%u exp=%u\n", t, h_out[t], exp); mism++; }
  }
  if (mism) { printf("SCRATCH VERIFY FAIL: %d/%u mismatches\n", mism, N); return 3; }
  printf("SCRATCH PASS: all %u correct (spilling kernel ran via MES+scratch)\n", N);
  return 0;
}
