# lunahan_v1 — A RISC-V RV32IMC Core in pyCircuit

**lunahan_v1** is an open-source, synthesizable RISC-V core implementing the
**RV32IMC** instruction set. It combines the silicon-proven microarchitecture
methodology of **XiangShan** with the Python-native agile design flow of
**pyCircuit**, producing a clean 5-stage in-order pipeline described entirely
in Python that lowers through MLIR to synthesizable Verilog.

---

## Philosophy

### XiangShan DNA
The [XiangShan](https://github.com/OpenXiangShan/XiangShan) project demonstrated
that high-performance RISC-V cores can be built with agile hardware design
methodologies, rigorous verification, and open-source toolchains. lunahan_v1
adopts the same principles:

- **Microarchitecture-first**: Every pipeline decision is documented,
  justified, and verified against real RISC-V compliance suites.
- **Verification-in-the-loop**: RISCOF architectural compliance, random
  instruction stress tests, and coverage-driven verification from day one.
- **Open-source physical design**: Targeting the SkyWater 130 nm open PDK
  through the OpenROAD flow — no proprietary tools required.

### pyCircuit Agility
[pyCircuit](https://github.com/pycircuit) treats Python as a first-class HDL:

- **Python → MLIR → Verilog**: Hardware described in Python is lowered through
  MLIR dialects (`pyc` / `comb` / `seq`) to synthesizable Verilog RTL.
- **Cycle-accurate simulation**: The same Python source drives C++/MLIR-native
  simulation with cycle-precise semantics.
- **Zero-translation testbenches**: `@testbench` decorators let you write
  verification logic in the same language as the design.

---

## Architecture Overview

```
                    ┌──────────────────────────────────────────┐
                    │            lunahan_v1 Core               │
                    │                                          │
   AXI4-Lite  ◄────►│  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐    │
   (Instr Bus)      │  │ IF │→│ ID │→│ EX │→│MEM │→│ WB │    │
                    │  └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘    │
                    │     │      │      │      │      │       │
   I-Cache (4 KB) ◄─┤     │      │      │      │      │       │
   D-Cache (4 KB) ◄─┤     │      │      │      │      │       │
                    │     ▼      ▼      ▼      ▼      ▼       │
                    │  [PC Gen] [Dec]  [ALU]  [LSU]  [RF Wr] │
                    │                                          │
                    └──────────────────────────────────────────┘

   ISA: RV32IMC (Integer base + Multiply/Divide + Compressed)
   Pipeline: 5-stage in-order, single-issue
   Privilege: Machine mode (M-mode)
   Memory: Harvard (split I/D caches, each 4 KB direct-mapped)
   Bus: AXI4-Lite (32-bit data, 32-bit address)
```

### Key Features

| Feature          | Specification                                     |
| ---------------- | ------------------------------------------------- |
| ISA              | RV32IMC (I base, M extension, C extension)        |
| Pipeline         | 5-stage in-order (IF, ID, EX, MEM, WB)            |
| Issue width      | Single-issue                                      |
| Branch predictor | 64-entry bimodal BTB with 2-bit saturating counter |
| ALU              | 32-bit with all RV32I ops (add/sub/logic/shift/slt)|
| Multiplier       | 32×32 → 32-bit, 5-cycle Booth radix-4              |
| Divider          | 32/32 → 32-bit, 33-cycle restoring division        |
| Register file    | 32 × 32-bit, 2-read 1-write                        |
| I-Cache          | 4 KB, direct-mapped, 16-byte lines, write-through  |
| D-Cache          | 4 KB, direct-mapped, 16-byte lines, write-back     |
| CSRs             | Full M-mode CSRs (mstatus, mie, mip, mcause, ...)  |
| Exceptions       | Illegal instruction, ecall/ebreak, misaligned addr  |
| Interrupts       | Timer, software, external (3 sources via PLIC)      |
| Bus interface    | AXI4-Lite, 32-bit data, 32-bit address              |

---

## Build Flow

```
 ┌──────────────┐     ┌───────────────────┐     ┌──────────────────┐
 │ Python       │     │  MLIR / pycc      │     │  Hardware        │
 │ Source       │     │  Compiler          │     │  Artifacts       │
 │              │     │                    │     │                  │
 │ core.py      │────►│  pyCircuit         │────►│  .pyc (MLIR)     │
 │ params.py    │     │  Frontend           │     │  + C++ sim       │
 │ tb_core.py   │     │                    │     │  + Verilog RTL   │
 └──────────────┘     └───────────────────┘     └────────┬─────────┘
                                                         │
                        ┌───────────────────┐            │
                        │  Yosys + OpenROAD │◄───────────┘
                        │  Physical Design  │
                        │                   │
                        │  Synthesis → P&R  │────►  GDSII
                        │  → GDSII           │
                        └───────────────────┘
```

---

## Directory Structure

```
lunahan_v1/
├── README.md                    # This file
├── docs/
│   ├── architecture_spec.md     # Full RV32IMC ISA specification
│   ├── flow_diagram.md          # Detailed build flow & tool versions
│   ├── microarchitecture.md     # 5-stage pipeline microarchitecture
│   ├── verification_plan.md     # Verification strategy & coverage
│   └── physical_design.md       # OpenROAD + sky130 physical design
├── rtl/
│   ├── lunahan_core.py          # Top-level core module
│   └── parameters.py            # Configurable parameters
├── sim/
│   └── tb_lunahan.py            # pyCircuit testbench
├── tests/
│   ├── unit/                    # Per-stage unit tests
│   ├── integration/             # Pipeline integration tests
│   └── system/                  # RISCOF compliance tests
└── scripts/
    ├── build_mlir.py            # MLIR emission script
    ├── build_verilog.py         # Verilog generation script
    └── run_riscof.py            # RISCOF regression runner
```

---

## Quick Start

### Prerequisites

| Tool         | Version   | Purpose                        |
| ------------ | --------- | ------------------------------ |
| Python       | ≥ 3.10    | pyCircuit runtime              |
| pyCircuit    | ≥ 5.0     | Python→MLIR→Verilog compiler   |
| pycc         | ≥ 0.8     | MLIR→C++/Verilog backend       |
| Verilator    | ≥ 5.0     | RTL co-simulation              |
| Yosys        | ≥ 0.40    | Logic synthesis                |
| OpenROAD     | ≥ 2.0     | Place & route                  |
| KLayout      | ≥ 0.28    | GDS viewer                     |
| sky130 PDK   | latest    | SkyWater 130 nm open PDK       |

### Simulation

```bash
# Python-native cycle-accurate simulation
python -m pycircuit run rtl/lunahan_core.py --sim

# Run a hex program through the testbench
python sim/tb_lunahan.py --hex tests/system/rv32ui-p-add.hex

# RISCOF compliance suite
python scripts/run_riscof.py --target lunahan_v1
```

### Synthesis & Place-and-Route

```bash
# Emit MLIR
python scripts/build_mlir.py rtl/lunahan_core.py --out build/core.pyc

# Generate Verilog
pycc emit-verilog build/core.pyc --top lunahan_core --out build/core.v

# Yosys synthesis
yosys -p "read_verilog build/core.v; synth -top lunahan_core; write_verilog build/core_synth.v"

# OpenROAD place & route
openroad -script scripts/openroad_flow.tcl
```

---

## Target Metrics

| Metric          | Target       |
| --------------- | ------------ |
| Frequency (sky130) | ≥ 50 MHz  |
| Core area       | ≤ 0.25 mm²   |
| IPC (non-memory, non-branch) | ≥ 0.95 |
| Cycles/MHz      | 1             |
| Power (est.)    | ≤ 15 mW      |

---

## License

Apache 2.0 — see LICENSE file (not yet created; will be added before first
public release).

## References

- [XiangShan](https://github.com/OpenXiangShan/XiangShan) — Open-source
  high-performance RISC-V processor
- [pyCircuit](https://github.com/pycircuit) — Python-native agile hardware
  design
- [RISCOF](https://github.com/riscv-software-src/riscof) — RISC-V
  compliance framework
- [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD) — Open-source
  digital design flow
- [sky130 PDK](https://github.com/google/skywater-pdk) — SkyWater 130 nm
  open PDK
