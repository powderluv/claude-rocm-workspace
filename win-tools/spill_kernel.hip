// spill_kernel.hip -- minimal gfx1201 (RDNA4) register-spilling kernel for the
// ROCr Windows lite:: direct-HQD scratch path (task #57 / macOS #15).
//
// A `volatile`, dynamically-indexed private array forces a nonzero per-work-item
// private (scratch) segment, so a dispatch of this kernel exercises gfx12
// architected flat scratch -- WITHOUT torch / hipBLASLt / rocBLAS. The output is
// deterministic and host-verifiable so a wave that never retires (the #57 hang)
// is detectable: the pre-seeded poison survives.
//
// A 256-int (1 KB) private array, written AND read at runtime-dependent indices,
// is too large to promote to VGPRs and can't be SROA'd, so it lands in scratch.
//
//   write: buf[(i*37 + t) & 255] = t + i   (37 coprime to 256 -> bijection i->idx)
//   read : acc += buf[(i*53 + t) & 255]     (53 coprime to 256 -> visits every slot)
// Both index sets cover all 256 slots, so (working through the modular inverse):
//   out[t] = sum_slots buf = 256*t + sum_{k=0..255} k = 256*t + 32640.
// Deterministic + host-verifiable; a never-retiring wave leaves the -777 poison.
//
// Build: hipcc --genco --offload-arch=gfx1201 spill_kernel.hip -o spill.hsaco
// (spill_probe.py builds it automatically if the .hsaco is missing.) Verify it
// actually spills: llvm-objdump -d --mcpu=gfx1201 spill.hsaco | grep scratch_

#define __HIP_PLATFORM_AMD__
#include <hip/hip_runtime.h>

extern "C" __global__ void spill(int* out) {
    int t = blockIdx.x * blockDim.x + threadIdx.x;

    volatile int buf[256];           // 1 KB private (scratch) segment / work-item
#pragma unroll 1
    for (int i = 0; i < 256; ++i) buf[(i * 37 + t) & 255] = t + i;

    int acc = 0;
#pragma unroll 1
    for (int i = 0; i < 256; ++i) acc += buf[(i * 53 + t) & 255];

    out[t] = acc;                    // == 256*t + 32640
}
