#!/usr/bin/env python3
"""Generate hip-replay plan PDF with full content from the markdown plan."""

from fpdf import FPDF


class PlanPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(150, 150, 150)
            hw = (self.w - self.l_margin - self.r_margin) / 2
            self.cell(hw, 6, "HIP Record & Replay - Design Plan")
            self.cell(hw, 6, f"Page {self.page_no()}", align="R",
                      new_x="LMARGIN", new_y="NEXT")
            self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(180, 180, 180)
        self.cell(0, 10, "ROCm Build Infrastructure - 2026-03-31",
                  align="C")

    def h1(self, text):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(26, 26, 46)
        self.ln(3)
        self.multi_cell(0, 8, text)
        self.set_draw_color(39, 174, 96)
        self.set_line_width(0.8)
        self.line(self.l_margin, self.get_y(),
                  self.w - self.r_margin, self.get_y())
        self.ln(3)

    def h2(self, text):
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(41, 128, 185)
        self.ln(2)
        self.multi_cell(0, 7, text)
        self.ln(1)

    def h3(self, text):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(80, 80, 80)
        self.ln(1)
        self.multi_cell(0, 6, text)
        self.ln(1)

    def p(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(50, 50, 50)
        self.set_x(self.l_margin)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def bullet(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(50, 50, 50)
        self.set_x(self.l_margin)
        self.multi_cell(0, 5, f"  - {text}")

    def code(self, text, size=7.5):
        self.set_font("Courier", "", size)
        self.set_text_color(40, 40, 40)
        self.set_fill_color(245, 243, 240)
        pw = self.w - self.l_margin - self.r_margin - 6
        for line in text.strip().split("\n"):
            self.set_x(self.l_margin + 3)
            self.cell(pw, 3.8, line, fill=True,
                      new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def table(self, headers, rows, col_pcts):
        """Draw a table with text wrapping. col_pcts is list of width %."""
        total_w = self.w - self.l_margin - self.r_margin
        widths = [total_w * p / 100 for p in col_pcts]
        self.set_draw_color(200, 200, 200)

        # Header row
        self.set_font("Helvetica", "B", 8.5)
        self.set_fill_color(41, 128, 185)
        self.set_text_color(255, 255, 255)
        y0 = self.get_y()
        x = self.l_margin
        max_h = 5.5
        for hdr, w in zip(headers, widths):
            self.set_xy(x, y0)
            self.cell(w, max_h, hdr, border=1, fill=True)
            x += w
        self.set_xy(self.l_margin, y0 + max_h)

        # Data rows
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(50, 50, 50)
        for ri, row in enumerate(rows):
            fill = ri % 2 == 0
            if fill:
                self.set_fill_color(248, 249, 250)
            else:
                self.set_fill_color(255, 255, 255)

            # Calculate row height by finding max lines needed
            line_h = 4.2
            max_lines = 1
            for cell_text, w in zip(row, widths):
                cell_w = w - 2  # padding
                if cell_w < 5:
                    cell_w = 5
                text_w = self.get_string_width(cell_text)
                lines = max(1, int(text_w / cell_w) + 1)
                max_lines = max(max_lines, lines)
            row_h = max_lines * line_h

            # Check page break
            if self.get_y() + row_h > self.h - self.b_margin:
                self.add_page()

            y0 = self.get_y()
            x = self.l_margin
            for cell_text, w in zip(row, widths):
                self.set_xy(x, y0)
                # Draw background and border
                self.rect(x, y0, w, row_h, style="DF" if fill else "D")
                # Write text with wrapping
                self.set_xy(x + 1, y0 + 0.5)
                self.multi_cell(w - 2, line_h, cell_text)
                x += w
            self.set_xy(self.l_margin, y0 + row_h)
        self.ln(2)


def build():
    pdf = PlanPDF()
    pdf.set_margins(18, 15, 18)

    # ===== COVER PAGE =====
    pdf.add_page()
    pdf.ln(45)
    pdf.set_font("Helvetica", "B", 32)
    pdf.set_text_color(26, 26, 46)
    pdf.cell(0, 15, "HIP Record & Replay", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 20)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 10, "(hip-replay)", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(41, 128, 185)
    pdf.cell(0, 8, "Design Plan", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(18)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(55, pdf.get_y(), pdf.w - 55, pdf.get_y())
    pdf.ln(12)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(80, 80, 80)
    for label, value in [
        ("Project:", "ROCm Build Infrastructure"),
        ("Repository:", "ROCm/TheRock (in-tree) + hip-replay (out-of-tree)"),
        ("Date:", "2026-03-31"),
        ("Status:", "Approved for implementation"),
        ("Platforms:", "Linux + Windows"),
        ("APEX Dependency:", "None"),
    ]:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(45, 7, label)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(15)

    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5,
        "A system to record HIP GPU workloads (kernel launches, memory "
        "operations, buffer contents) and replay them deterministically on "
        "any compatible AMD GPU. Supports both in-tree CLR runtime hooks "
        "and out-of-tree LD_PRELOAD/proxy DLL interception.")

    # ===== CONTEXT =====
    pdf.add_page()
    pdf.h1("1. Context")
    pdf.p("We need a system to record HIP GPU workloads (kernel launches, "
           "memory operations, buffer contents) and replay them "
           "deterministically on any compatible AMD GPU. Use cases:")
    pdf.bullet("Bug reproduction: Capture a failing workload and replay "
               "on a different machine")
    pdf.bullet("Performance regression testing: Record a reference workload, "
               "replay across ROCm versions")
    pdf.bullet("Kernel-level debugging: Isolate individual kernels with "
               "their exact input state")
    pdf.bullet("Portable benchmarking: Package a complete GPU workload "
               "for cross-machine comparison")
    pdf.ln(2)
    pdf.p("No existing tool covers this. roctracer/rocprofiler trace timing "
           "but not buffer contents. hip-remote sends HIP calls over TCP "
           "but doesn't record them. GPU_DUMP_CODE_OBJECT dumps code objects "
           "but not kernel args or buffers.")
    pdf.p("No APEX dependency. APEX was only referenced as a design pattern "
           "for LD_PRELOAD interposer structure. HRR is completely "
           "standalone -- no shared code, no linking, no runtime dependency.")

    # ===== ARCHITECTURE =====
    pdf.h1("2. Architecture Overview")
    pdf.p("HRR supports two complementary recording mechanisms that produce "
           "the same trace format and share replay/benchmark tools.")
    pdf.code("""\
                    Path A: In-Tree              Path B: Out-of-Tree
                    +----------------+           +--------------------+
                    | CLR Runtime    |           | LD_PRELOAD / Proxy |
Application ------>| hip_hrr.cpp    |           | hrr_interposer.c   |
                    | (env gated)    |           | (dlsym forward)    |
                    +-------+--------+           +--------+-----------+
                            |                              |
                            v                              v
                    +----------------------------------------------+
                    |           Shared: HRR Trace Writer            |
                    |  - events.bin (binary event stream)           |
                    |  - blobs/ (content-addressed, XXH3-128)      |
                    |  - code_objects/ (captured .hsaco ELFs)       |
                    |  - manifest.json (device info, config)        |
                    +----------------------+-----------------------+
                                           |
              +----------------------------+----------------------------+
              v                            v                            v
     +----------------+          +-----------------+         +------------------+
     |  hrr-replay    |          |   hrr-bench     |         | hrr-bench export |
     |  Replay &      |          |   Benchmark &   |         | Standalone .hip  |
     |  verify        |          |   reproduce     |         | + CMakeLists.txt |
     +----------------+          +-----------------+         +------------------+""")

    # ===== PATH A =====
    pdf.add_page()
    pdf.h1("3. Path A: In-Tree (CLR Runtime Hooks)")
    pdf.p("Recording hooks built directly into the HIP runtime (CLR) in "
           "TheRock. Triggered by HIP_RECORD=1 env var. This is the "
           "preferred path because:")
    pdf.bullet("Full access to internal state (kernel args, code objects, "
               "buffer metadata)")
    pdf.bullet("No need to reverse-engineer the public API surface or parse "
               "fat binaries externally")
    pdf.bullet("The CLR already has the hip_apex.h pattern as a template -- "
               "env-var gated hooks at malloc/free/launch insertion points")
    pdf.bullet("Works on both Linux and Windows without separate "
               "interposer builds")
    pdf.bullet("Can access COMGR metadata directly (already loaded by "
               "the runtime)")

    pdf.h2("Implementation Pattern")
    pdf.p("Add hip_hrr.h / hip_hrr.cpp to CLR alongside existing "
           "hip_apex.h, using the same insertion points:")
    pdf.table(
        ["Operation", "CLR File", "Function", "Existing Pattern"],
        [
            ["Malloc", "hip_memory.cpp:770", "hipMalloc()", "apex::track_alloc() at line 380"],
            ["Free", "hip_memory.cpp:786", "hipFree()", "apex::track_free() at line 101"],
            ["Memcpy", "hip_memory.cpp:809", "hipMemcpy_common()", "HIP_INIT_API macro"],
            ["Kernel launch", "hip_module.cpp:363", "ihipLaunchKernelCommand()", "apex::pre_launch()"],
            ["Module load", "hip_module.cpp:57", "hipModuleLoad()", "HIP_INIT_API macro"],
            ["Fat binary", "hip_fatbin.cpp:42", "FatBinaryInfo::loadCodeObject()", "LOG_CODE mask logging"],
            ["Sync", "hip_device_runtime.cpp", "hipDeviceSynchronize()", "HIP_RETURN_DURATION"],
        ],
        [14, 22, 28, 36])

    pdf.h2("CLR Infrastructure Already Available")
    pdf.bullet("amd::activity_prof::report_activity callback infrastructure "
               "for profiler hooks")
    pdf.bullet("HIP_CB_SPAWNER_OBJECT(cid) macro at every API entry")
    pdf.bullet("HIP_RETURN_DURATION with timestamp collection")
    pdf.bullet("Correlation IDs (amd::activity_prof::correlation_id) for "
               "tracing causality")
    pdf.bullet("Flags system in rocclr/utils/flags.hpp for env var "
               "registration")
    pdf.bullet("Thread-local state (hip::tls) for per-thread tracking")
    pdf.bullet("KernelParameterDescriptor with type_, size_, offset_ fields")

    # ===== PATH B =====
    pdf.h1("4. Path B: Out-of-Tree (Portable)")
    pdf.p("For use with pre-built ROCm installations where you can't "
           "rebuild the runtime.")
    pdf.table(
        ["Platform", "Mechanism", "Details", "Reference"],
        [
            ["Linux", "LD_PRELOAD", "libhrr_record.so", "dlsym(RTLD_NEXT) forwarding"],
            ["Windows", "Proxy DLL", "amdhip64_7.dll", "hip-remote proxy DLL pattern"],
        ],
        [15, 20, 25, 40])
    pdf.p("Both forward every call to the real HIP runtime, then log the "
           "event. Plain C for the interposer (avoids C++ ABI issues).")
    pdf.p("The out-of-tree path requires its own ELF/msgpack parser for "
           "kernel arg introspection (since it can't access CLR internals). "
           "The in-tree path gets this for free from COMGR.")

    # ===== APIS TO CAPTURE =====
    pdf.h1("5. APIs to Capture")
    pdf.table(
        ["Category", "APIs"],
        [
            ["Memory", "hipMalloc, hipFree, hipMallocManaged, hipMallocAsync, hipFreeAsync"],
            ["Transfer", "hipMemcpy, hipMemcpyAsync, hipMemset, hipMemsetAsync"],
            ["Module", "hipModuleLoad, hipModuleLoadData, hipModuleUnload, hipModuleGetFunction"],
            ["Launch", "hipLaunchKernel, hipModuleLaunchKernel, hipExtModuleLaunchKernel"],
            ["Sync", "hipDeviceSynchronize, hipStreamSynchronize, hipEventSynchronize"],
            ["Stream", "hipStreamCreate, hipStreamCreateWithFlags, hipStreamDestroy"],
            ["Event", "hipEventCreate, hipEventRecord, hipEventDestroy"],
            ["Fat binary", "__hipRegisterFatBinary (out-of-tree only; in-tree hooks FatBinaryInfo)"],
        ],
        [15, 85])

    # ===== KERNEL ARG INTROSPECTION =====
    pdf.h1("6. Kernel Argument Introspection")
    pdf.p("The critical challenge: which kernel args are GPU pointers "
           "(need buffer snapshots) vs scalars (record raw bytes)?")
    pdf.h2("In-Tree (preferred)")
    pdf.p("The CLR already parses COMGR metadata when loading code objects. "
           "hip_hrr.cpp accesses the kernel's amd::KernelParameterDescriptor "
           "directly -- each parameter already has type_ (pointer/value), "
           "size_, and offset_ fields. No additional parsing needed.")
    pdf.h2("Out-of-Tree")
    pdf.p("Parse code object ELF .note section, extract msgpack "
           "amdhsa.kernels[].args[] metadata. Each arg has value_kind: "
           "global_buffer (pointer), by_value (scalar), hidden_* (runtime). "
           "Build lookup table (code_obj_hash, kernel_name) -> "
           "[ArgDescriptor].")
    pdf.h2("Fallback (assembly kernels)")
    pdf.p("Blind-scan all 8-byte-aligned positions in kernarg buffer, treat "
           "values in GPU address range as pointers. Same approach hip-remote "
           "uses for Tensile/rocBLAS assembly kernels.")

    # ===== TRACE FORMAT =====
    pdf.add_page()
    pdf.h1("7. Trace Format Specification")
    pdf.h2("Archive Structure")
    pdf.code("""\
capture_YYYYMMDD_HHMMSS.hrr/
  manifest.json              # Device info, ROCm version, capture config
  events.bin                 # Binary event stream (32-byte headers)
  blobs/                     # Content-addressed buffer store (XXH3-128)
    ab/ab1234...xxh3.blob    # Optionally zstd-compressed
  code_objects/              # Captured code object ELFs
    <xxh3_hash>.hsaco""")

    pdf.h2("Event Header (32 bytes, little-endian)")
    pdf.code("""\
magic:          u32  = 0x52524845 ("HRRE")
version:        u16  = 1
event_type:     u16  (enum)
sequence_id:    u64  (monotonic counter)
timestamp_ns:   u64  (CLOCK_MONOTONIC / QueryPerformanceCounter)
stream_id:      u32
device_id:      u16
payload_length: u16
[payload bytes follow]""")

    pdf.h2("Event Types")
    pdf.table(
        ["Code", "Name", "Payload"],
        [
            ["0x0001", "MALLOC", "size: u64, ptr_handle: u64, flags: u32"],
            ["0x0002", "FREE", "ptr_handle: u64"],
            ["0x0003", "MEMCPY", "dst_handle: u64, src_handle: u64, size: u64, kind: u32, blob_hash: [u8;16]"],
            ["0x0004", "MEMSET", "dst_handle: u64, value: u32, size: u64"],
            ["0x0010", "MODULE_LOAD", "code_obj_hash: [u8;16], module_handle: u64"],
            ["0x0011", "MODULE_UNLOAD", "module_handle: u64"],
            ["0x0020", "KERNEL_LAUNCH", "code_obj_hash, kernel_name, grid/block dims, shared_mem, args[], buffer_snapshots[]"],
            ["0x0030", "STREAM_CREATE", "stream_handle: u64, flags: u32"],
            ["0x0031", "STREAM_DESTROY", "stream_handle: u64"],
            ["0x0032", "STREAM_SYNC", "stream_handle: u64"],
            ["0x0040", "EVENT_CREATE", "event_handle: u64, flags: u32"],
            ["0x0041", "EVENT_RECORD", "event_handle: u64, stream_handle: u64"],
            ["0x0042", "EVENT_SYNC", "event_handle: u64"],
            ["0x0050", "DEVICE_SYNC", "(empty)"],
            ["0x00FF", "MARKER", "name_len: u16, name: [u8] (user annotation)"],
        ],
        [10, 18, 72])

    pdf.h2("KERNEL_LAUNCH Payload Detail")
    pdf.code("""\
code_obj_hash:       [u8;16]   # identifies which .hsaco
kernel_name_len:     u16
kernel_name:         [u8; kernel_name_len]
grid_dim:            [u32; 3]
block_dim:           [u32; 3]
shared_mem_bytes:    u32
num_args:            u16
num_buffer_snapshots: u16
args:                [KernelArg; num_args]
buffer_snapshots:    [BufferSnapshot; num_buffer_snapshots]

KernelArg:
  value_kind: u8  (0=scalar, 1=global_buffer, 2=hidden, 3=struct)
  size:       u16
  data:       [u8; size]  # scalar: raw bytes; pointer: ptr_handle u64

BufferSnapshot:
  ptr_handle:  u64
  offset:      u64
  length:      u64
  blob_hash:   [u8;16]  # reference into blobs/ directory
  direction:   u8       # 0=input, 1=output""")

    pdf.h2("Design Rationale")
    pdf.p("Binary over JSON: Fixed 32-byte headers enable O(1) seeking and "
           "memory-mapped replay. Tens of thousands of events per second -- "
           "JSON parsing overhead would dominate.")
    pdf.p("Content-addressed blobs: Model weights (static across kernel "
           "launches) are stored once. A 7B model trace: ~14GB weights "
           "(deduplicated) + ~2GB activations = ~16GB total. Without dedup "
           "it would be hundreds of GB.")
    pdf.p("XXH3-128 over SHA-256: 10-50x faster, not a security "
           "application, matches hip-remote.")

    # ===== RECORDING MODES =====
    pdf.add_page()
    pdf.h1("8. Recording Modes")
    pdf.table(
        ["Mode", "Overhead", "Use Case"],
        [
            ["HIP_RECORD_MODE=timeline", "Minimal", "Record API call sequence only (no buffer data)"],
            ["HIP_RECORD_MODE=inputs", "Moderate", "Snapshot input buffers before each kernel (default)"],
            ["HIP_RECORD_MODE=full", "High", "Snapshot inputs + outputs (sync after every kernel)"],
        ],
        [30, 12, 58])
    pdf.p("Additional controls:")
    pdf.bullet("HIP_RECORD_KERNEL_FILTER=matmul_* -- record only matching kernels")
    pdf.bullet("HIP_RECORD_MAX_BLOB_MB=1024 -- skip buffers above threshold")
    pdf.bullet("HIP_RECORD_PID_FILTER=main -- record only main process (for multi-process frameworks)")
    pdf.p("In-tree uses HIP_RECORD* env vars registered in CLR's flags "
           "system. Out-of-tree uses HRR_* prefix to avoid collisions.")

    # ===== ENV VARS =====
    pdf.h2("Environment Variables")
    pdf.h3("In-Tree (CLR flags system)")
    pdf.table(
        ["Variable", "Description"],
        [
            ["HIP_RECORD=1", "Enable recording"],
            ["HIP_RECORD_OUTPUT=./capture.hrr", "Output directory"],
            ["HIP_RECORD_MODE=inputs|full|timeline", "Recording mode"],
            ["HIP_RECORD_KERNEL_FILTER=pattern", "Record only matching kernels"],
            ["HIP_RECORD_MAX_BLOB_MB=N", "Skip blobs above threshold"],
            ["HIP_RECORD_COMPRESS=1", "Zstd-compress blobs"],
        ],
        [45, 55])

    pdf.h3("Out-of-Tree (HRR_ prefix)")
    pdf.p("Same variables with HRR_ prefix (e.g., HRR_RECORD=1, "
           "HRR_OUTPUT=...) to avoid collisions with CLR flags.")

    # ===== REPLAY TOOL =====
    pdf.h1("9. Replay Tool")
    pdf.code("hrr-replay capture.hrr [--verify] [--timing] [--kernel-filter NAME]")
    pdf.p("1. Read manifest, validate GPU compatibility (gfx arch match "
           "or warn)")
    pdf.p("2. Load code objects with hipModuleLoadData")
    pdf.p("3. Process events sequentially:")
    pdf.bullet("MALLOC: allocate, build handle-to-pointer map (reuse "
               "hip-remote's vaddr translation pattern)")
    pdf.bullet("MEMCPY: restore buffer contents from blob store")
    pdf.bullet("KERNEL_LAUNCH: marshal args (translate handles to real "
               "pointers), launch")
    pdf.p("4. With --verify: compare output buffers against recorded "
           "snapshots (bitwise, ULP, L2 norm)")
    pdf.p("5. With --timing: report per-kernel and total wall time vs "
           "recorded")

    # ===== BENCHMARK TOOL =====
    pdf.add_page()
    pdf.h1("10. Benchmark & Reproduce Tool (hrr-bench)")
    pdf.p("Extract and benchmark individual kernels or full application "
           "traces. Primary tool for performance analysis and issue "
           "reproduction.")

    pdf.h2("10.1 Kernel-Level Benchmarking")
    pdf.code("""\
# List all kernels in a capture with stats
hrr-bench list capture.hrr
  ID  Kernel                        Grid        Block     Calls  Avg(us)
  1   _ZN4gemm...                   [256,1,1]   [256,1,1]   47   1842.3
  2   _ZN8softmax...                [128,1,1]   [128,1,1]   47    312.1
  3   _ZN4relu...                   [512,1,1]   [64,1,1]    94     28.7

# Benchmark a single kernel (restore inputs, run N iterations, report stats)
hrr-bench kernel capture.hrr --id 1 --iterations 1000 --warmup 50
  Kernel: _ZN4gemm...  Grid: [256,1,1]  Block: [256,1,1]  SharedMem: 32768
  Iterations: 1000  Warmup: 50
  Min: 1.801ms  Median: 1.843ms  Mean: 1.849ms  P95: 1.892ms  P99: 1.923ms
  Throughput: 541.3 kernel/s   Recorded: 1.842ms  Delta: +0.4%

# Benchmark with modified grid/block dims (for tuning)
hrr-bench kernel capture.hrr --id 1 --grid 512,1,1 --block 128,1,1

# Benchmark all kernels matching a pattern
hrr-bench kernel capture.hrr --filter "gemm*" --iterations 100""")

    pdf.h2("10.2 Export to Standalone Repro")
    pdf.code("""\
# Export kernel as standalone .hip test (for sharing/filing bugs)
hrr-bench export capture.hrr --id 1 --output gemm_repro/
  Creates:
    gemm_repro/
      CMakeLists.txt     # Build with: cmake -B build && cmake --build build
      repro.hip          # Standalone HIP program that runs this one kernel
      kernel.hsaco       # Code object
      input_0.bin        # Input buffer snapshots
      input_1.bin
      expected_output.bin

# Export with sanitized data (for sharing repros without leaking model weights)
hrr-bench export capture.hrr --id 1 --output gemm_repro/ --safe
  Same structure, but buffer contents are randomized.
  Zero values are preserved (keeps sparsity patterns intact).
  Scalar kernel args (dims, strides) are preserved.
  Code objects are preserved (kernel binary needed for repro).
  Safe for external bug reports -- no proprietary data leaked.""")

    pdf.h2("10.3 Application-Level Benchmarking")
    pdf.code("""\
# Replay full trace with timing comparison
hrr-bench app capture.hrr --iterations 5
  Run 1: 4.823s (recorded: 4.801s, +0.5%)
  Run 2: 4.819s (-0.1%)    Run 3: 4.821s (+0.0%)
  Run 4: 4.818s (-0.1%)    Run 5: 4.820s (+0.0%)
  Mean: 4.820s  StdDev: 0.002s  vs Recorded: +0.4%

# Profile hottest kernels (sorted by total time)
hrr-bench app capture.hrr --profile
  Kernel                     Calls  Total(ms)  Avg(ms)   % Time
  _ZN4gemm...                  47    86,588     1,842     73.2%
  _ZN8softmax...               47    14,669       312     12.4%
  _ZN4relu...                  94     2,698        29      2.3%

# Compare two captures (before/after optimization, two ROCm versions)
hrr-bench compare before.hrr after.hrr
  Kernel                     Before(ms)  After(ms)  Delta
  _ZN4gemm...                   1,842      1,623    -11.9%
  _ZN8softmax...                  312        315     +1.0%
  Total                         4,801      4,512     -6.0%""")

    pdf.h2("10.4 Issue Reproduction")
    pdf.code("""\
# Reproduce a crash or hang (replay until failure)
hrr-bench repro capture.hrr
  Replaying 15,234 events...
  Event 8,421: KERNEL_LAUNCH _ZN4gemm... -> hipErrorLaunchFailure
  Kernel args dumped to crash_dump/

# Reproduce with additional diagnostics
hrr-bench repro capture.hrr --check-nan --check-inf
  Event 3,201: KERNEL_LAUNCH _ZN8softmax... output contains NaN
  Input buffers saved to nan_dump/

# Binary search for first divergent kernel (regression bisect)
hrr-bench bisect before.hrr after.hrr --tolerance 1e-6
  First divergence at event 4,892: _ZN4gemm...
  Max abs diff: 0.00234  Max ULP diff: 47
  Input buffers: identical.  Likely cause: kernel code changed

# Stress test a single kernel
hrr-bench stress capture.hrr --id 1 --iterations 10000 --verify
  Running 10,000 times with verification...
  Iteration 7,234: output mismatch (max ULP diff: 3, tolerance: 0)
  Saved divergent output to stress_fail_7234.bin""")

    pdf.h2("10.5 Implementation Details")
    pdf.bullet("Kernel isolation: set up one kernel's state (allocations + "
               "input buffers) without replaying full trace. Uses event "
               "index to find dependencies.")
    pdf.bullet("Iteration loop: restore input buffers from blob store "
               "before each iteration (ensures clean state).")
    pdf.bullet("HIP event timing: uses hipEventRecord / "
               "hipEventElapsedTime for GPU-side kernel timing (not "
               "wall clock).")
    pdf.bullet("Export mode: generates standalone .hip + CMakeLists.txt + "
               "buffer data. Build with cmake. Self-contained -- no HRR "
               "dependency. For filing bug reports.")
    pdf.bullet("Safe mode (--safe): randomizes buffer contents while "
               "preserving zeros. Keeps sparsity patterns intact, strips "
               "proprietary data. Scalar args preserved.")
    pdf.bullet("NaN/Inf checker: after each kernel, hipMemcpy + scan for "
               "NaN/Inf in output buffers.")
    pdf.bullet("Bisect mode: replays both captures kernel-by-kernel, "
               "comparing outputs to find first divergence.")

    # ===== MILESTONES =====
    pdf.add_page()
    pdf.h1("11. Implementation Milestones")

    pdf.h2("Phase 1: Kernel-Level Record & Replay")
    pdf.table(
        ["#", "Milestone", "Scope"],
        [
            ["1", "In-tree skeleton", "Add hip_hrr.h/cpp to CLR. Register HIP_RECORD flag. Hook hipMalloc/Free/Memcpy. Trace writer + blob store."],
            ["2", "Kernel arg capture (in-tree)", "Hook ihipLaunchKernelCommand(). Access KernelParameterDescriptor for pointer/scalar identification. Buffer snapshots."],
            ["3", "Replay tool", "hrr-replay binary. Archive reader, handle translator, kernel replayer, output verification."],
            ["4", "Benchmark & reproduce tool", "hrr-bench with kernel isolation, iteration benchmarking, NaN/Inf detection, export-to-standalone-hip, bisect mode, compare mode."],
            ["5", "Out-of-tree interposer (Linux)", "libhrr_record.so with LD_PRELOAD. ELF/msgpack parser for kernel arg introspection. Same trace writer."],
            ["6", "Out-of-tree proxy DLL (Windows)", "amdhip64_7.dll proxy. Generated from HIP headers. MSVC build."],
            ["7", "Testing + hardening", "Real workloads (rocBLAS, PyTorch), edge cases, documentation."],
        ],
        [4, 26, 70])

    pdf.h2("Phase 2: Full Application Capture & Replay")
    pdf.table(
        ["#", "Milestone", "Scope"],
        [
            ["8", "Library capture", "Enumerate + copy loaded shared libs on both platforms."],
            ["9", "Python env capture", "Detect Python, freeze packages, capture entry script."],
            ["10", "Portable packaging", "tar.zst archive, launcher scripts, optional Docker export."],
            ["11", "Cross-platform replay", "Validate Linux trace on Windows and vice versa."],
        ],
        [4, 26, 70])

    # ===== RISKS =====
    pdf.h1("12. Key Risks & Mitigations")
    risks = [
        ("Buffer snapshot overhead",
         "Copying multi-GB buffers to host per kernel launch can be 10-100x slower.",
         "inputs mode (skip outputs), kernel filtering, content-addressed dedup (same data hashes to same blob), lazy snapshot (skip if hash unchanged)."),
        ("Fat binary format brittleness (out-of-tree only)",
         "HIPF/HIPK magic varies across ROCm versions.",
         "The in-tree path avoids this entirely since CLR already handles format evolution. Out-of-tree mitigates with GPU_DUMP_CODE_OBJECT=1 fallback."),
        ("Multi-process ML frameworks",
         "PyTorch/vLLM use fork/subprocess. Interposition can crash multi-process init.",
         "hrr_enabled() guard, fork detection, PID filtering. In-tree path can check hip::tls state directly."),
        ("Storage size",
         "70B parameter model = ~140GB weights in FP16.",
         "Content-addressed dedup + zstd compression. Weights are static across launches = stored once."),
    ]
    for title, problem, mitigation in risks:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(192, 57, 43)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5, title)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(50, 50, 50)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 4.5, f"Problem: {problem}")
        pdf.set_text_color(39, 174, 96)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 4.5, f"Mitigation: {mitigation}")
        pdf.ln(2)

    # ===== PHASE 2 =====
    pdf.add_page()
    pdf.h1("13. Phase 2: Full Application Capture & Replay")
    pdf.h2("Research Summary")
    pdf.p("No existing tool handles cross-platform GPU application "
           "packaging:")
    pdf.bullet("CDE: Dead (last updated ~2014), Linux-only, no GPU support")
    pdf.bullet("AppImage/Flatpak: Linux-only, explicitly exclude GPU "
               "drivers/libs")
    pdf.bullet("Docker/OCI: Strong for Linux, weak for Windows, requires "
               "Docker runtime")
    pdf.bullet("LD_PRELOAD file tracing / Detours: Can discover deps but "
               "can't capture GPU state")

    pdf.h2("Approach: Custom Archive")
    pdf.code("""\
capture.hrr/                     (Phase 1 trace data)
  manifest.json
  events.bin
  blobs/
  code_objects/
  environment/                   (Phase 2 additions)
    env_vars.json                # Relevant env vars at capture time
    python/
      requirements.txt           # pip freeze output
      sys_path.json              # Python path
      entry_script.py            # Captured entry point
    libs/                        # Captured shared libraries
      linux/
        libamdhip64.so.6         # Exact HIP runtime used
        libhsa-runtime64.so.1
        librocsolver.so.0
      windows/
        amdhip64_7.dll
    loader.sh                    # Linux replay launcher
    loader.bat                   # Windows replay launcher""")

    pdf.h2("Library Capture")
    pdf.table(
        ["Platform", "Discovery", "Mechanism"],
        [
            ["Linux", "/proc/self/maps at init + dlopen hook", "In-tree: CLR constructor. Out-of-tree: LD_PRELOAD"],
            ["Windows", "EnumerateLoadedModules64 + LoadLibraryW hook", "In-tree: DllMain. Out-of-tree: Detours/IAT patch"],
        ],
        [12, 40, 48])
    pdf.p("Launcher scripts set LD_LIBRARY_PATH / PATH to point at "
           "captured libs before running replay.")

    pdf.h2("Packaging Decision")
    pdf.p("Default: tar.zst archive with embedded launcher script (works "
           "everywhere, no runtime deps).")
    pdf.p("Optional exports: Docker image, AppImage (Linux only).")

    # ===== ALTERNATIVES =====
    pdf.add_page()
    pdf.h1("14. Alternatives Considered")
    pdf.table(
        ["Decision", "Chosen", "Rejected", "Why"],
        [
            ["Recording path", "Both in-tree + out-of-tree", "Out-of-tree only", "In-tree gives full CLR access; out-of-tree provides portability"],
            ["In-tree pattern", "hip_apex.h-style hooks", "rocprofiler-register, activity callbacks", "Direct hooks simpler, lower overhead, access internal state"],
            ["Trace format", "Custom binary + JSON manifest", "Protobuf, JSON events", "Fixed headers for O(1) seek, no external deps, fast write"],
            ["Buffer capture", "Full snapshot + dedup", "GPU page fault tracking (XNACK)", "Simpler, works on all hardware, dedup handles common case"],
            ["Blob hashing", "XXH3-128", "SHA-256", "10-50x faster, not a security application, matches hip-remote"],
            ["Phase 2 packaging", "Custom archive + launcher", "CDE, AppImage, Flatpak, Docker-only", "Cross-platform requirement eliminates Linux-only options"],
            ["Language", "C++ (in-tree) + C (out-of-tree)", "Pure C, Rust", "In-tree must be C++ for CLR classes; out-of-tree C avoids ABI"],
            ["APEX dependency", "None", "Shared interposer code", "Different goals (prefetch vs record); no shared state needed"],
        ],
        [16, 20, 24, 40])

    # ===== PROJECT LAYOUT =====
    pdf.h1("15. Project Layout")
    pdf.h2("In-Tree (CLR changes in TheRock)")
    pdf.code("""\
rocm-systems/projects/clr/hipamd/src/
  hip_hrr.h              # HRR API: hrr::enabled(), hrr::record_*()
  hip_hrr.cpp            # Implementation: trace writer, blob store, env var init
  hip_hrr_writer.h       # Trace file writer (events.bin + blobs/)
  hip_hrr_writer.cpp     # Content-addressed blob store, XXH3 hashing

Modified files (minimal, following hip_apex.h pattern):
  hip_memory.cpp         # + hrr::record_malloc/free/memcpy() calls
  hip_module.cpp         # + hrr::record_module_load/launch() calls
  hip_fatbin.cpp         # + hrr::record_code_object() at extraction
  rocclr/utils/flags.hpp # + register HIP_RECORD* env vars
  hipamd/CMakeLists.txt  # + hip_hrr.cpp to sources, xxhash/zstd deps""")

    pdf.h2("Out-of-Tree (standalone repo)")
    pdf.code("""\
hip-replay/
  CMakeLists.txt
  src/
    hrr_core.h             # Shared types (event structs, blob hash)
    hrr_writer.c           # Trace writer
    hrr_reader.c           # Trace reader (memory-mapped)
    hrr_alloc_tracker.c    # Pointer-to-handle mapping
    hrr_code_object.c      # ELF/msgpack parser (out-of-tree only)
    hrr_interposer_linux.c # LD_PRELOAD entry points
    hrr_proxy_win.c        # Windows proxy DLL
    hrr_replay.c           # Replay engine
    hrr_bench.c            # Benchmark & reproduce tool
    hrr_verify.c           # Output buffer comparison
    hrr_export.c           # Export kernel to standalone .hip repro
    hrr_info.c             # Archive info CLI tool
  tools/
    gen_proxy_dll.py       # Generate proxy DLL from HIP headers
  tests/
    test_record_replay.hip # Round-trip tests""")

    # ===== CRITICAL FILES =====
    pdf.add_page()
    pdf.h1("16. Critical Files (In-Tree Path)")
    pdf.p("These are the CLR source files that need modifications:")
    pdf.table(
        ["File", "What to add"],
        [
            ["rocm-systems/.../hip_hrr.h", "New file. hrr::enabled(), hrr::record_malloc(), hrr::record_launch(), etc."],
            ["rocm-systems/.../hip_hrr.cpp", "New file. Init, trace writer, blob store, env var parsing."],
            ["rocm-systems/.../hip_memory.cpp", "Add hrr::record_malloc/free/memcpy() at existing APEX hook points (lines 101, 380, 809)."],
            ["rocm-systems/.../hip_module.cpp", "Add hrr::record_module_load() at line 57, hrr::record_launch() at line 363."],
            ["rocm-systems/.../hip_fatbin.cpp", "Add hrr::record_code_object() at loadCodeObject() (line 42)."],
            ["rocm-systems/.../flags.hpp", "Register HIP_RECORD, HIP_RECORD_MODE, etc. using existing DEFINE_FLAG macro."],
            ["rocm-systems/.../CMakeLists.txt", "Add hip_hrr.cpp to sources, optional xxhash/zstd dependency."],
        ],
        [30, 70])

    # ===== VERIFICATION =====
    pdf.h1("17. Verification Plan")
    pdf.bullet("1. Unit tests: blob store write/read, event serialization "
               "round-trip, ELF metadata parsing")
    pdf.bullet("2. Integration test: record vectorAdd.hip -> replay -> "
               "verify output matches")
    pdf.bullet("3. rocBLAS test: record SGEMM -> replay -> verify numerical "
               "accuracy (ULP comparison)")
    pdf.bullet("4. PyTorch test: record GPT-2 inference (single forward "
               "pass) -> replay -> verify output tensors")
    pdf.bullet("5. Cross-platform test: record on Linux -> replay on "
               "Linux (same machine, different machine)")
    pdf.bullet("6. Windows test: record with proxy DLL -> replay with "
               "hrr-replay.exe")
    pdf.bullet("7. In-tree vs out-of-tree parity: Record same workload "
               "with both paths, verify identical trace output")

    # Save
    import os
    out = os.path.join(os.path.dirname(__file__), "hip-replay-design-plan.pdf")
    pdf.output(out)
    print(f"PDF generated: {out}")
    print(f"Pages: {pdf.page_no()}")


if __name__ == "__main__":
    build()
