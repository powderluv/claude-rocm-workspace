# AISOLVE-68: hipSOLVER FP32 Cholesky Accuracy Investigation

## Summary

hipSOLVER's FP32 Cholesky decomposition (`hipsolverDnSpotrf`) produces results that differ from CPU reference significantly, while FP64 and MAGMA (PyTorch's GPU cholesky via MAGMA) match much more closely.

## Test Environment

- **GPU:** AMD Instinct MI300X
- **ROCm:** 7.2 (in container)
- **PyTorch:** 2.9.1+rocm7.2.0
- **Matrix size tested:** 128 to 4096

## Reproduction Results

### hipSOLVER FP32 Error vs Matrix Size

| N | Max Abs Error | Relative Reconstruction Error |
|---|---------------|-------------------------------|
| 128 | 8.28e-04 | 4.46e-07 |
| 256 | 3.43e-04 | 4.51e-07 |
| 512 | 1.58e-03 | 6.86e-07 |
| 1024 | 6.68e-03 | 1.14e-06 |
| 2048 | 4.80e-03 | 1.61e-06 |
| 4096 | 4.25e-02 | 2.52e-06 |

### MAGMA (torch.linalg.cholesky GPU) FP32 Error vs Matrix Size

| N | Max Abs Error | Relative Reconstruction Error |
|---|---------------|-------------------------------|
| 128 | 0.00e+00 | 2.67e-07 |
| 256 | 0.00e+00 | 1.80e-07 |
| 512 | 0.00e+00 | 7.84e-07 |
| 1024 | 0.00e+00 | 1.24e-06 |
| 2048 | 0.00e+00 | 1.83e-06 |
| 4096 | 4.47e-02 | 2.25e-06 |

### hipSOLVER FP64 (Reference)

| Metric | Value |
|--------|-------|
| Max abs error | 4.26e-11 |
| Reconstruction error | 6.85e-16 |

## Key Observations

1. **hipSOLVER FP32 has significant error** (~1e-3 to 1e-2) compared to CPU reference
2. **MAGMA matches CPU exactly** for matrices up to ~2048, suggesting hybrid CPU-GPU execution
3. **Reconstruction error is acceptable** for both implementations (~1e-6 to 1e-7)
4. **Error grows with matrix size** - consistent with accumulated rounding errors in blocked algorithm
5. **FP64 works correctly** - the algorithm is correct, precision is the issue

## Root Cause Analysis

### Primary Factors

1. **Large FP32 Block Size (180 vs 127 for FP64)**

   From `ideal_sizes.hpp:285`:
   ```cpp
   #define POTRF_BLOCKSIZE(T) ((sizeof(T) == 4) ? 180 : (sizeof(T) == 8) ? 127 : 90)
   ```

   Larger blocks mean more accumulated rounding errors in:
   - TRSM (triangular solve) operations
   - SYRK/HERK (symmetric rank-k update) operations

2. **No FP64 Accumulation in FP32 Dot Products**

   In `roclapack_potf2.hpp:228`, the diagonal computation uses:
   ```cpp
   rocblasCall_dot<COMPLEX, T>(handle, j, A, ...)
   ```

   This computes `L[j,j] = sqrt(A[j,j] - sum(L[j,0:j-1]^2))`. The dot product accumulates FP32 values in FP32, losing precision compared to CPU BLAS which often uses FP64 accumulators.

3. **Specialized Small Kernel All-FP32**

   In `roclapack_potf2_specialized_kernels.hpp`, the `potf2_simple` function performs:
   ```cpp
   auto const lkk = std::sqrt(akk);  // FP32 sqrt
   A[j0k] = (A[j0k] / conj_lkk);     // FP32 division
   A[ij] = A[ij] - vi * conj(vj);    // FP32 accumulation
   ```

   All operations stay in FP32 without higher-precision intermediates.

### Why MAGMA Matches CPU Exactly (for smaller matrices)

MAGMA uses a **hybrid CPU-GPU algorithm** where:
- Panel factorization happens on CPU (using MKL/OpenBLAS with FP64 accumulators)
- Only matrix updates (TRSM, SYRK) happen on GPU
- This gives identical results to CPU for the critical diagonal elements

For very large matrices (4096+), even MAGMA shows some error due to GPU-only execution paths.

## Proposed Fixes

### Option 1: Reduce FP32 Block Size (Quick Win)

Change `ideal_sizes.hpp`:
```cpp
// Current
#define POTRF_BLOCKSIZE(T) ((sizeof(T) == 4) ? 180 : (sizeof(T) == 8) ? 127 : 90)

// Proposed - match FP64 block size
#define POTRF_BLOCKSIZE(T) ((sizeof(T) == 4) ? 127 : (sizeof(T) == 8) ? 127 : 90)
```

**Pros:** Simple change, smaller accumulated error
**Cons:** Possible performance regression, doesn't address precision fundamentally

### Option 2: Use FP64 Accumulation for FP32 Dot Products

Modify `rocblasCall_dot` to use FP64 accumulation when the input type is FP32.

**Pros:** Addresses root cause, matches CPU BLAS behavior
**Cons:** Requires rocBLAS changes, moderate performance impact

### Option 3: Use Kahan Summation in Specialized Kernel

In `potf2_simple`, replace:
```cpp
A[ij] = A[ij] - vi * conj(vj);
```

With compensated summation to reduce rounding errors.

**Pros:** No precision type changes needed
**Cons:** Complex implementation, moderate performance impact

### Option 4: Hybrid CPU-GPU Like MAGMA (Major Change)

Implement hybrid algorithm where panel factorization happens on CPU.

**Pros:** Would match MAGMA/CPU results exactly
**Cons:** Major architectural change, requires CPU memory management

## Recommendation

**Short-term:** Implement Option 1 (reduce block size) and measure impact
**Medium-term:** Investigate Option 2 (FP64 accumulation for dot products)
**Long-term:** Consider Option 4 for applications requiring exact CPU match

## Test Commands

```bash
# Reproduce issue in container
docker run --rm --device=/dev/kfd --device=/dev/dri --group-add video \
  -v /home/anush/workspace:/workspace rocm/pytorch:latest \
  python3 /workspace/time_test_hipsolver_ext.py \
  --so /workspace/hipsolver_ext_rocm72.so --check --dtype fp32 --n 2048
```

## Files Analyzed

| File | Purpose |
|------|---------|
| `rocm-libraries/projects/rocsolver/library/src/include/ideal_sizes.hpp:285` | Block size definitions |
| `rocm-libraries/projects/rocsolver/library/src/lapack/roclapack_potrf.hpp` | Blocked POTRF algorithm |
| `rocm-libraries/projects/rocsolver/library/src/lapack/roclapack_potf2.hpp` | Unblocked POTF2 algorithm |
| `rocm-libraries/projects/rocsolver/library/src/specialized/roclapack_potf2_specialized_kernels.hpp` | Small matrix specialized kernel |

## References

- [MAGMA Library potrf documentation](https://icl.utk.edu/projectsfiles/magma/doxygen/group__magma__potrf.html)
- [PyTorch ROCm installation](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/3rd-party/pytorch-install.html)
