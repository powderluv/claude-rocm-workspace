#!/usr/bin/env python3
"""Generate hip-replay plan PDF from the approved plan."""

from fpdf import FPDF
import os
import textwrap

class PlanPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(150, 150, 150)
            w = self.w - self.l_margin - self.r_margin
            self.cell(w / 2, 6, "HIP Record & Replay - Design Plan", align="L")
            self.cell(w / 2, 6, f"Page {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
            self.ln(6)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(180, 180, 180)
        self.cell(0, 10, "ROCm Build Infrastructure - 2026-03-31", align="C")

    def section_title(self, title):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(26, 26, 46)
        self.ln(4)
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(39, 174, 96)
        self.set_line_width(0.8)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def subsection_title(self, title):
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(41, 128, 185)
        self.ln(2)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def subsubsection_title(self, title):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(80, 80, 80)
        self.ln(1)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body_text(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(50, 50, 50)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def bullet(self, text, indent=10):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(50, 50, 50)
        self.set_x(self.l_margin)
        self.multi_cell(0, 5, f"    - {text}")

    def code_block(self, text, font_size=8):
        self.set_font("Courier", "", font_size)
        self.set_text_color(40, 40, 40)
        self.set_fill_color(245, 243, 240)
        x = self.get_x()
        self.set_x(x + 5)
        for line in text.strip().split("\n"):
            self.cell(self.w - self.l_margin - self.r_margin - 10, 4, line, fill=True, new_x="LMARGIN", new_y="NEXT")
            self.set_x(x + 5)
        self.ln(2)

    def table_row(self, cols, widths, bold=False, header=False):
        self.set_font("Helvetica", "B" if bold or header else "", 9)
        if header:
            self.set_fill_color(41, 128, 185)
            self.set_text_color(255, 255, 255)
        else:
            self.set_fill_color(245, 248, 250)
            self.set_text_color(50, 50, 50)
        self.set_draw_color(180, 180, 180)
        h = 6
        for i, (col, w) in enumerate(zip(cols, widths)):
            is_last = (i == len(cols) - 1)
            trunc = col[:int(w / 2.2)] if len(col) > int(w / 2.2) else col
            if is_last:
                self.cell(w, h, trunc, border=1, fill=header, new_x="LMARGIN", new_y="NEXT")
            else:
                self.cell(w, h, trunc, border=1, fill=header)

    def bold_text(self, label, text):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(50, 50, 50)
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "", 10)
        self.multi_cell(0, 5, f"{label}{text}")


def generate_pdf():
    pdf = PlanPDF()
    pdf.set_margins(18, 15, 18)

    # ==================== COVER PAGE ====================
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font("Helvetica", "B", 32)
    pdf.set_text_color(26, 26, 46)
    pdf.cell(0, 15, "HIP Record & Replay", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 20)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 10, "(hip-replay)", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(41, 128, 185)
    pdf.cell(0, 8, "Design Plan", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(20)

    pdf.set_draw_color(200, 200, 200)
    pdf.line(60, pdf.get_y(), pdf.w - 60, pdf.get_y())
    pdf.ln(15)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(80, 80, 80)
    info = [
        ("Project:", "ROCm Build Infrastructure"),
        ("Repository:", "ROCm/TheRock (in-tree) + hip-replay (out-of-tree)"),
        ("Date:", "2026-03-31"),
        ("Status:", "Approved for implementation"),
        ("Platforms:", "Linux + Windows"),
        ("APEX Dependency:", "None"),
    ]
    for label, value in info:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(45, 7, label)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(20)

    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5, "A system to record HIP GPU workloads (kernel launches, memory operations, "
        "buffer contents) and replay them deterministically on any compatible AMD GPU. "
        "Supports both in-tree CLR runtime hooks and out-of-tree LD_PRELOAD/proxy DLL interception.")

    # ==================== ARCHITECTURE DIAGRAM ====================
    pdf.add_page()
    pdf.section_title("1. Architecture Overview")

    pdf.body_text(
        "HRR supports two complementary recording mechanisms that produce the same trace format "
        "and share replay/benchmark tools. The in-tree path (Path A) hooks directly into the CLR "
        "runtime for full internal access. The out-of-tree path (Path B) uses LD_PRELOAD or a proxy "
        "DLL for use with pre-built ROCm installations."
    )

    # Embed SVG as image - first convert key info to text representation
    pdf.ln(2)
    svg_path = os.path.join(os.path.dirname(__file__), "hip-replay-architecture.svg")
    if os.path.exists(svg_path):
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 5, f"[See hip-replay-architecture.svg for vector diagram]", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Text-based architecture diagram
    pdf.code_block("""\
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
              |                            |                            |
              v                            v                            v
     +----------------+          +-----------------+          +------------------+
     |  hrr-replay    |          |   hrr-bench     |          | hrr-bench export |
     |  Replay &      |          |   Benchmark &   |          | Standalone .hip  |
     |  verify        |          |   reproduce     |          | + CMakeLists.txt |
     +----------------+          +-----------------+          +------------------+""", font_size=7)

    # ==================== PATH A: IN-TREE ====================
    pdf.add_page()
    pdf.section_title("2. Path A: In-Tree (CLR Runtime Hooks)")

    pdf.body_text(
        "Recording hooks built directly into the HIP runtime (CLR) in TheRock. Triggered by "
        "HIP_RECORD=1 env var. This is the preferred path because it has full access to internal "
        "state (kernel args, code objects, buffer metadata), needs no external parsing, and follows "
        "the existing hip_apex.h pattern."
    )

    pdf.subsection_title("Implementation Pattern")
    pdf.body_text(
        "Add hip_hrr.h / hip_hrr.cpp to CLR alongside existing hip_apex.h, using the same "
        "insertion points:"
    )

    widths = [35, 55, 45, 40]
    pdf.table_row(["Operation", "CLR File", "Function", "Pattern"], widths, header=True)
    rows = [
        ["Malloc", "hip_memory.cpp", "hipMalloc()", "apex::track_alloc()"],
        ["Free", "hip_memory.cpp", "hipFree()", "apex::track_free()"],
        ["Memcpy", "hip_memory.cpp", "hipMemcpy_common()", "HIP_INIT_API"],
        ["Launch", "hip_module.cpp", "ihipLaunchKernelCmd()", "apex::pre_launch()"],
        ["Module", "hip_module.cpp", "hipModuleLoad()", "HIP_INIT_API"],
        ["Fat binary", "hip_fatbin.cpp", "loadCodeObject()", "LOG_CODE"],
        ["Sync", "hip_device_rt.cpp", "hipDeviceSync()", "HIP_RETURN_DUR"],
    ]
    for row in rows:
        pdf.table_row(row, widths)

    pdf.ln(3)
    pdf.subsection_title("CLR Infrastructure Already Available")
    pdf.bullet("amd::activity_prof::report_activity callback infrastructure")
    pdf.bullet("HIP_CB_SPAWNER_OBJECT(cid) macro at every API entry")
    pdf.bullet("HIP_RETURN_DURATION with timestamp collection")
    pdf.bullet("Correlation IDs for tracing causality")
    pdf.bullet("Flags system in rocclr/utils/flags.hpp for env var registration")
    pdf.bullet("Thread-local state (hip::tls) for per-thread tracking")
    pdf.bullet("KernelParameterDescriptor with type_, size_, offset_ fields")

    # ==================== PATH B: OUT-OF-TREE ====================
    pdf.ln(3)
    pdf.section_title("3. Path B: Out-of-Tree (Portable)")

    pdf.body_text(
        "For use with pre-built ROCm installations where the runtime can't be rebuilt."
    )

    widths = [30, 55, 50, 40]
    pdf.table_row(["Platform", "Mechanism", "Details", "Reference"], widths, header=True)
    pdf.table_row(["Linux", "LD_PRELOAD", "libhrr_record.so", "dlsym(RTLD_NEXT)"], widths)
    pdf.table_row(["Windows", "Proxy DLL", "amdhip64_7.dll", "hip-remote pattern"], widths)

    pdf.ln(2)
    pdf.body_text(
        "Both forward every call to the real HIP runtime, then log the event. Plain C for the "
        "interposer to avoid C++ ABI issues. Requires its own ELF/msgpack parser for kernel "
        "argument introspection."
    )

    # ==================== KERNEL ARG INTROSPECTION ====================
    pdf.section_title("4. Kernel Argument Introspection")

    pdf.body_text(
        "The critical challenge: which kernel args are GPU pointers (need buffer snapshots) "
        "vs scalars (record raw bytes)?"
    )

    pdf.subsection_title("In-Tree (preferred)")
    pdf.body_text(
        "The CLR already parses COMGR metadata when loading code objects. hip_hrr.cpp accesses "
        "the kernel's amd::KernelParameterDescriptor directly -- each parameter already has "
        "type_ (pointer/value), size_, and offset_ fields. No additional parsing needed."
    )

    pdf.subsection_title("Out-of-Tree")
    pdf.body_text(
        "Parse code object ELF .note section, extract msgpack amdhsa.kernels[].args[] metadata. "
        "Each arg has value_kind: global_buffer (pointer), by_value (scalar), hidden_* (runtime). "
        "Build lookup table (code_obj_hash, kernel_name) -> [ArgDescriptor]."
    )

    pdf.subsection_title("Fallback (assembly kernels)")
    pdf.body_text(
        "Blind-scan all 8-byte-aligned positions in kernarg buffer, treat values in GPU address "
        "range as pointers. Same approach hip-remote uses for Tensile/rocBLAS assembly kernels."
    )

    # ==================== TRACE FORMAT ====================
    pdf.add_page()
    pdf.section_title("5. Trace Format Specification")

    pdf.subsection_title("Archive Structure")
    pdf.code_block("""\
capture_YYYYMMDD_HHMMSS.hrr/
  manifest.json              # Device info, ROCm version, capture config
  events.bin                 # Binary event stream (32-byte headers)
  blobs/                     # Content-addressed buffer store (XXH3-128)
    ab/ab1234...xxh3.blob    # Optionally zstd-compressed
  code_objects/              # Captured code object ELFs
    <xxh3_hash>.hsaco""")

    pdf.subsection_title("Event Header (32 bytes, little-endian)")
    pdf.code_block("""\
  magic:          u32  = 0x52524845 ("HRRE")
  version:        u16  = 1
  event_type:     u16  (enum)
  sequence_id:    u64  (monotonic counter)
  timestamp_ns:   u64  (CLOCK_MONOTONIC / QueryPerformanceCounter)
  stream_id:      u32
  device_id:      u16
  payload_length: u16
  [payload bytes follow]""")

    pdf.subsection_title("Event Types")
    widths = [20, 35, 120]
    pdf.table_row(["Code", "Name", "Payload"], widths, header=True)
    events = [
        ["0x01", "MALLOC", "size, ptr_handle, flags"],
        ["0x02", "FREE", "ptr_handle"],
        ["0x03", "MEMCPY", "dst, src, size, kind, blob_hash"],
        ["0x04", "MEMSET", "dst, value, size"],
        ["0x10", "MODULE_LOAD", "code_obj_hash, module_handle"],
        ["0x20", "KERNEL_LAUNCH", "code_obj_hash, kernel_name, grid/block dims, args, buffer snapshots"],
        ["0x30", "STREAM_CREATE", "stream_handle, flags"],
        ["0x32", "STREAM_SYNC", "stream_handle"],
        ["0x50", "DEVICE_SYNC", "(empty)"],
    ]
    for e in events:
        pdf.table_row(e, widths)

    pdf.ln(3)
    pdf.subsection_title("Design Rationale")
    pdf.bold_text("Binary over JSON: ", "Fixed 32-byte headers enable O(1) seeking and memory-mapped "
        "replay. Tens of thousands of events per second.")
    pdf.bold_text("Content-addressed blobs: ", "Model weights (static across kernel launches) stored "
        "once. 7B model trace: ~14GB deduplicated vs hundreds of GB without.")
    pdf.bold_text("XXH3-128 over SHA-256: ", "10-50x faster, not a security application, matches hip-remote.")

    # ==================== RECORDING MODES ====================
    pdf.add_page()
    pdf.section_title("6. Recording Modes")

    widths = [35, 25, 115]
    pdf.table_row(["Mode", "Overhead", "Use Case"], widths, header=True)
    pdf.table_row(["timeline", "Minimal", "Record API call sequence only (no buffer data)"], widths)
    pdf.table_row(["inputs", "Moderate", "Snapshot input buffers before each kernel (default)"], widths)
    pdf.table_row(["full", "High", "Snapshot inputs + outputs (sync after every kernel)"], widths)

    pdf.ln(3)
    pdf.subsection_title("Environment Variables")

    pdf.subsubsection_title("In-Tree (CLR flags system)")
    widths = [60, 115]
    pdf.table_row(["Variable", "Description"], widths, header=True)
    env_vars = [
        ["HIP_RECORD=1", "Enable recording"],
        ["HIP_RECORD_OUTPUT=./capture.hrr", "Output directory"],
        ["HIP_RECORD_MODE=inputs|full|timeline", "Recording mode"],
        ["HIP_RECORD_KERNEL_FILTER=pattern", "Record only matching kernels"],
        ["HIP_RECORD_MAX_BLOB_MB=N", "Skip blobs above threshold"],
        ["HIP_RECORD_COMPRESS=1", "Zstd-compress blobs"],
    ]
    for row in env_vars:
        pdf.table_row(row, widths)

    pdf.ln(2)
    pdf.subsubsection_title("Out-of-Tree (HRR_ prefix)")
    pdf.body_text("Same variables with HRR_ prefix (e.g., HRR_RECORD=1, HRR_OUTPUT=...) to avoid collisions.")

    # ==================== TOOLS ====================
    pdf.section_title("7. Replay Tool")
    pdf.code_block("hrr-replay capture.hrr [--verify] [--timing] [--kernel-filter NAME]")
    pdf.body_text(
        "1. Read manifest, validate GPU compatibility (gfx arch match or warn)\n"
        "2. Load code objects with hipModuleLoadData\n"
        "3. Process events: MALLOC (allocate + handle map), MEMCPY (restore from blobs), "
        "KERNEL_LAUNCH (marshal args, translate handles, launch)\n"
        "4. --verify: compare output buffers (bitwise, ULP, L2 norm)\n"
        "5. --timing: per-kernel and total wall time vs recorded"
    )

    # ==================== BENCHMARK TOOL ====================
    pdf.add_page()
    pdf.section_title("8. Benchmark & Reproduce Tool (hrr-bench)")

    pdf.body_text(
        "Extract and benchmark individual kernels or full application traces. Primary tool for "
        "performance analysis and issue reproduction."
    )

    pdf.subsection_title("8.1 Kernel-Level Benchmarking")
    pdf.code_block("""\
# List all kernels with stats
hrr-bench list capture.hrr

# Benchmark single kernel (1000 iterations, 50 warmup)
hrr-bench kernel capture.hrr --id 1 --iterations 1000 --warmup 50
  Min: 1.801ms  Median: 1.843ms  P95: 1.892ms  P99: 1.923ms

# Benchmark with modified grid/block dims
hrr-bench kernel capture.hrr --id 1 --grid 512,1,1 --block 128,1,1""")

    pdf.subsection_title("8.2 Export to Standalone Repro")
    pdf.code_block("""\
# Export kernel as standalone .hip test
hrr-bench export capture.hrr --id 1 --output gemm_repro/
  Creates: CMakeLists.txt, repro.hip, kernel.hsaco, input_*.bin, expected_output.bin
  Build:   cmake -B build && cmake --build build && ./build/repro

# Export with sanitized data (safe for external bug reports)
hrr-bench export capture.hrr --id 1 --output gemm_repro/ --safe
  Buffer contents randomized, zeros preserved (keeps sparsity patterns)
  Scalar kernel args (dims, strides) preserved
  Code objects preserved (needed for repro)""")

    pdf.subsection_title("8.3 Application-Level Benchmarking")
    pdf.code_block("""\
# Replay full trace with timing comparison
hrr-bench app capture.hrr --iterations 5

# Profile hottest kernels
hrr-bench app capture.hrr --profile

# Compare two captures (before/after optimization, two ROCm versions)
hrr-bench compare before.hrr after.hrr""")

    pdf.subsection_title("8.4 Issue Reproduction")
    pdf.code_block("""\
# Reproduce crash/hang
hrr-bench repro capture.hrr

# Detect NaN/Inf in kernel outputs
hrr-bench repro capture.hrr --check-nan --check-inf

# Binary search for first divergent kernel (regression bisect)
hrr-bench bisect before.hrr after.hrr --tolerance 1e-6

# Stress test single kernel
hrr-bench stress capture.hrr --id 1 --iterations 10000 --verify""")

    pdf.subsection_title("8.5 Implementation Details")
    pdf.bullet("Kernel isolation: set up one kernel's state without full trace replay")
    pdf.bullet("HIP event timing: hipEventRecord/hipEventElapsedTime for GPU-side measurement")
    pdf.bullet("Safe mode (--safe): randomize buffers, preserve zeros (sparsity), preserve scalar args")
    pdf.bullet("Bisect mode: kernel-by-kernel comparison of two captures to find first divergence")
    pdf.bullet("NaN/Inf checker: hipMemcpy + scan after each kernel")

    # ==================== MILESTONES ====================
    pdf.add_page()
    pdf.section_title("9. Implementation Milestones")

    pdf.subsection_title("Phase 1: Kernel-Level Record & Replay")
    widths = [10, 55, 110]
    pdf.table_row(["#", "Milestone", "Scope"], widths, header=True)
    milestones = [
        ["1", "In-tree skeleton", "hip_hrr.h/cpp in CLR, HIP_RECORD flag, hipMalloc/Free/Memcpy hooks, trace writer, blob store"],
        ["2", "Kernel arg capture", "Hook ihipLaunchKernelCommand(), KernelParameterDescriptor access, buffer snapshots"],
        ["3", "Replay tool", "hrr-replay binary, archive reader, handle translator, kernel replayer, verification"],
        ["4", "Benchmark tool", "hrr-bench: kernel isolation, benchmarking, NaN/Inf detection, export, bisect, compare"],
        ["5", "Out-of-tree Linux", "libhrr_record.so with LD_PRELOAD, ELF/msgpack parser, same trace writer"],
        ["6", "Out-of-tree Windows", "amdhip64_7.dll proxy DLL, generated from HIP headers, MSVC build"],
        ["7", "Testing + hardening", "rocBLAS, PyTorch workloads, edge cases, documentation"],
    ]
    for m in milestones:
        pdf.table_row(m, widths)

    pdf.ln(3)
    pdf.subsection_title("Phase 2: Full Application Capture & Replay")
    pdf.table_row(["#", "Milestone", "Scope"], widths, header=True)
    milestones2 = [
        ["8", "Library capture", "Enumerate + copy loaded shared libs on both platforms"],
        ["9", "Python env capture", "Detect Python, freeze packages, capture entry script"],
        ["10", "Portable packaging", "tar.zst archive, launcher scripts, optional Docker export"],
        ["11", "Cross-platform replay", "Validate Linux trace on Windows and vice versa"],
    ]
    for m in milestones2:
        pdf.table_row(m, widths)

    # ==================== RISKS ====================
    pdf.ln(5)
    pdf.section_title("10. Key Risks & Mitigations")

    risks = [
        ("Buffer snapshot overhead",
         "Copying multi-GB buffers to host per kernel can be 10-100x slower.",
         "inputs mode (skip outputs), kernel filtering, content-addressed dedup, lazy snapshot."),
        ("Fat binary format brittleness (out-of-tree)",
         "HIPF/HIPK magic varies across ROCm versions.",
         "In-tree path avoids this entirely. Out-of-tree: GPU_DUMP_CODE_OBJECT=1 fallback."),
        ("Multi-process ML frameworks",
         "PyTorch/vLLM use fork/subprocess; interposition can crash.",
         "hrr_enabled() guard, fork detection, PID filtering, hip::tls state checks."),
        ("Storage size",
         "70B model = ~140GB weights in FP16.",
         "Content-addressed dedup + zstd compression. Weights static = stored once."),
    ]
    for title, problem, mitigation in risks:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(192, 57, 43)
        pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(50, 50, 50)
        pdf.cell(0, 5, f"Problem: {problem}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(39, 174, 96)
        pdf.cell(0, 5, f"Mitigation: {mitigation}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # ==================== ALTERNATIVES ====================
    pdf.add_page()
    pdf.section_title("11. Alternatives Considered")

    widths = [35, 35, 35, 70]
    pdf.table_row(["Decision", "Chosen", "Rejected", "Why"], widths, header=True)
    alts = [
        ["Recording path", "Both in-tree + out-of-tree", "Out-of-tree only",
         "In-tree: full CLR access. Out-of-tree: portability."],
        ["In-tree pattern", "hip_apex.h-style hooks", "rocprofiler, activity callbacks",
         "Direct hooks simpler, lower overhead, access internals."],
        ["Trace format", "Custom binary + JSON", "Protobuf, JSON events",
         "Fixed headers, O(1) seek, no deps, fast write."],
        ["Buffer capture", "Full snapshot + dedup", "GPU page fault (XNACK)",
         "Simpler, works all hardware, dedup handles it."],
        ["Blob hashing", "XXH3-128", "SHA-256",
         "10-50x faster, not security, matches hip-remote."],
        ["Phase 2 pkg", "Custom archive", "CDE, AppImage, Flatpak",
         "Cross-platform eliminates Linux-only options."],
        ["Language", "C++ in-tree, C out-of-tree", "Pure C, Rust",
         "In-tree must be C++ (CLR classes). Out-of-tree C avoids ABI."],
        ["APEX dep", "None", "Shared code",
         "Different goals, no shared state needed."],
    ]
    for a in alts:
        pdf.table_row(a, widths)

    # ==================== PROJECT LAYOUT ====================
    pdf.ln(5)
    pdf.section_title("12. Project Layout")

    pdf.subsection_title("In-Tree (CLR changes)")
    pdf.code_block("""\
rocm-systems/projects/clr/hipamd/src/
  hip_hrr.h              # hrr::enabled(), hrr::record_*()
  hip_hrr.cpp            # Trace writer, blob store, env var init
  hip_hrr_writer.h       # Trace file writer (events.bin + blobs/)
  hip_hrr_writer.cpp     # Content-addressed blob store, XXH3

Modified files:
  hip_memory.cpp         # + hrr::record_malloc/free/memcpy()
  hip_module.cpp         # + hrr::record_module_load/launch()
  hip_fatbin.cpp         # + hrr::record_code_object()
  rocclr/utils/flags.hpp # + HIP_RECORD* env vars
  hipamd/CMakeLists.txt  # + hip_hrr.cpp, xxhash/zstd deps""")

    pdf.subsection_title("Out-of-Tree (standalone)")
    pdf.code_block("""\
hip-replay/
  CMakeLists.txt
  src/
    hrr_core.h             # Shared types
    hrr_writer.c           # Trace writer
    hrr_reader.c           # Trace reader (mmap)
    hrr_alloc_tracker.c    # Pointer-to-handle map
    hrr_code_object.c      # ELF/msgpack parser
    hrr_interposer_linux.c # LD_PRELOAD
    hrr_proxy_win.c        # Windows proxy DLL
    hrr_replay.c           # Replay engine
    hrr_bench.c            # Benchmark & reproduce
    hrr_verify.c           # Output comparison
    hrr_export.c           # Export standalone repro
    hrr_info.c             # Archive info CLI
  tools/
    gen_proxy_dll.py       # Generate proxy from HIP headers""")

    # ==================== VERIFICATION ====================
    pdf.section_title("13. Verification Plan")
    tests = [
        "Unit tests: blob store write/read, event serialization, ELF metadata parsing",
        "Integration: record vectorAdd.hip -> replay -> verify output",
        "rocBLAS: record SGEMM -> replay -> verify (ULP comparison)",
        "PyTorch: record GPT-2 inference -> replay -> verify output tensors",
        "Cross-platform: record on Linux -> replay on different Linux machine",
        "Windows: record with proxy DLL -> replay with hrr-replay.exe",
        "Parity: same workload via in-tree and out-of-tree -> identical trace",
    ]
    for i, t in enumerate(tests, 1):
        pdf.bullet(f"{i}. {t}")

    # Save
    out_path = os.path.join(os.path.dirname(__file__), "hip-replay-design-plan.pdf")
    pdf.output(out_path)
    return out_path


if __name__ == "__main__":
    path = generate_pdf()
    print(f"PDF generated: {path}")
