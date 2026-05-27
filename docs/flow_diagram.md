# Build Flow Diagram — lunahan_v1

## Overview

The lunahan_v1 build flow comprises four phases:

```
 ┌────────────────────────────────────────────────────────────────────────┐
 │                       LUNANAH_V1 BUILD FLOW                           │
 │                                                                        │
 │  ┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐ │
 │  │ Phase 1  │    │   Phase 2    │    │   Phase 3    │    │ Phase 4  │ │
 │  │ Python   │───►│   MLIR → C++ │───►│ Synthesis →  │───►│ Signoff  │ │
 │  │ → MLIR   │    │   & Verilog  │    │  OpenROAD P&R│    │  & Tape  │ │
 │  └──────────┘    └──────────────┘    └──────────────┘    └──────────┘ │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Python Source → pyCircuit Frontend → MLIR

```
 ┌───────────────────────────────────────────────────────────────────┐
 │ PHASE 1 — PYTHON → MLIR                                           │
 │                                                                    │
 │  ┌──────────────┐    ┌─────────────────┐    ┌──────────────────┐  │
 │  │ rtl/          │    │ pyCircuit       │    │ build/            │  │
 │  │               │    │ Frontend         │    │                   │  │
 │  │ lunahan_core  │───►│                  │───►│ lunahan_core.pyc  │  │
 │  │   .py         │    │  - Parse Python  │    │  (MLIR bytecode)  │  │
 │  │               │    │    AST           │    │                   │  │
 │  │ parameters.py │    │  - Infer types   │    │ lunahan_core      │  │
 │  │               │    │  - Emit .pyc     │    │   .mlir           │  │
 │  └──────────────┘    │    MLIR dialect   │    │  (textual MLIR)   │  │
 │                      └─────────────────┘    └──────────────────┘  │
 │                                                                    │
 │  Command:                                                          │
 │  ┌───────────────────────────────────────────────────────────────┐ │
 │  │ $ python scripts/build_mlir.py rtl/lunahan_core.py \           │ │
 │  │     --out build/                                                │ │
 │  │                                                                 │ │
 │  │ $ pyc dump-mlir build/lunahan_core.pyc \                       │ │
 │  │     --top lunahan_core --out build/lunahan_core.mlir            │ │
 │  └───────────────────────────────────────────────────────────────┘ │
 │                                                                    │
 │  Outputs:                                                          │
 │    build/lunahan_core.pyc     — Compiled MLIR module (binary)      │
 │    build/lunahan_core.mlir    — Textual MLIR dump (debuggable)     │
 │    build/lunahan_core.hir     — High-level IR dump (optional)      │
 └───────────────────────────────────────────────────────────────────┘
```

The pyCircuit frontend processes the Python source AST and emits a
`.pyc` file containing MLIR bytecode. This bytecode uses the `pyc`
(pycircuit) dialect with `pyc.module`, `pyc.domain`, `pyc.domain_inst`,
and `pyc.pipeline` ops.

Key transformations during this phase:

1. **Python AST → HIR**: Decorators (`@module`, `@domain`, `@pipeline`,
   `@testbench`) are recognized; class bodies are converted to hardware
   hierarchy.
2. **HIR → MLIR (pyc dialect)**: Registers, wires, combinational logic,
   and finite-state machines are lowered.
3. **Type inference**: Explicit type annotations (`UInt[32]`, `SInt[32]`,
   `Bool`) are resolved; unannotated signals are inferred from usage.
4. **Lint checks**: Unused signals, width mismatches, timing violations
   (combinational loops) are reported.

### pyCircuit V5 Key APIs

```python
from pycircuit.core import module, domain, pipeline, CycleAwareCircuit
from pycircuit.core import UInt, SInt, Bool, Valid, Ready, Bits
from pycircuit.core import Signal, Reg, RegNext, Wire, when, switch
from pycircuit.core import Cat, Mux, MuxLookup, Assert, Assume
```

---

## Phase 2: MLIR → pycc Compiler → C++ Simulation / Verilog RTL

```
 ┌───────────────────────────────────────────────────────────────────┐
 │ PHASE 2 — MLIR → C++ SIMULATION & VERILOG RTL                     │
 │                                                                    │
 │  ┌──────────────────┐    ┌─────────────────┐    ┌──────────────┐  │
 │  │ build/            │    │ pycc Compiler    │    │ build/        │  │
 │  │                   │    │                  │    │               │  │
 │  │ lunahan_core.pyc  │───►│  pyc dialect     │───►│ lunahan_core  │  │
 │  │ (MLIR bytecode)   │    │  → comb dialect  │    │   .v          │  │
 │  │                   │    │  → seq dialect   │    │ (Verilog RTL) │  │
 │  └──────────────────┘    │  → hw dialect    │    │               │  │
 │                          │  → sv dialect    │    │ lunahan_core  │  │
 │                          │  → LLVM IR       │    │   .cpp        │  │
 │                          │  → C++ source    │    │ (C++ model)   │  │
 │                          └─────────────────┘    └──────────────┘  │
 │                                                                    │
 │  Commands:                                                         │
 │  ┌───────────────────────────────────────────────────────────────┐ │
 │  │ # C++ cycle-accurate simulation                                │ │
 │  │ $ pycc emit-cpp build/lunahan_core.pyc \                       │ │
 │  │     --top lunahan_core --out build/lunahan_core.cpp            │ │
 │  │ $ g++ -O2 -std=c++17 build/lunahan_core.cpp \                  │ │
 │  │     -o build/lunahan_core_sim                                  │ │
 │  │ $ ./build/lunahan_core_sim --hex program.hex                   │ │
 │  │                                                                 │ │
 │  │ # Verilog RTL emission                                         │ │
 │  │ $ pycc emit-verilog build/lunahan_core.pyc \                   │ │
 │  │     --top lunahan_core --out build/lunahan_core.v              │ │
 │  └───────────────────────────────────────────────────────────────┘ │
 │                                                                    │
 │  Outputs:                                                          │
 │    build/lunahan_core.cpp     — C++ cycle-accurate model          │
 │    build/lunahan_core_sim     — Compiled simulator binary          │
 │    build/lunahan_core.v       — Synthesizable Verilog RTL          │
 │    build/lunahan_core_sim.vcd — (Optional) VCD waveform            │
 └───────────────────────────────────────────────────────────────────┘
```

The pycc compiler applies a sequence of MLIR passes:

1. **pyc-to-comb**: Lowers pyCircuit combinational logic to the `comb`
   dialect (combinational ops: add, sub, mux, etc.).
2. **pyc-to-seq**: Lowers registers and pipeline stages to the `seq`
   dialect (sequential ops: `seq.compreg`, `seq.firreg`).
3. **comb-to-hw**: Lowers comb dialect to the `hw` dialect (hardware
   types: `hw.struct`, `hw.array`, `hw.constant`).
4. **seq-to-sv**: Lowers seq dialect to the `sv` (SystemVerilog) dialect.
5. **hw-to-sv**: Lowers hw dialect to sv dialect.
6. **sv-to-verilog** / **sv-to-cpp**: Emits either Verilog RTL or
   LLVM-IR-then-C++ for simulation.

### C++ Simulation Features

- **Cycle-accurate**: Every clock edge is modeled; reg writes happen at
  posedge.
- **VCD dumping**: `--trace` flag emits VCD for waveform viewing in GTKWave.
- **Memory pre-loading**: `--hex <file>` loads program into the memory
  model before simulation.
- **Performance**: C++ simulation runs at ~100 KHz (cycles/sec) on a
  modern laptop — suitable for quick iteration.

---

## Phase 3: Verilog → Yosys Synthesis → OpenROAD P&R → GDSII

```
 ┌───────────────────────────────────────────────────────────────────┐
 │ PHASE 3 — SYNTHESIS → PLACE & ROUTE → GDSII                       │
 │                                                                    │
 │  ┌───────────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐  │
 │  │ build/         │  │ Yosys     │  │ OpenROAD  │  │ KLayout   │  │
 │  │                │  │           │  │           │  │           │  │
 │  │ core.v  ───────┼─►│ abc       │──►│ floorplan │──►│ gds viewer│ │
 │  │ (Verilog)      │  │ synth     │  │ place     │  │           │  │
 │  │                │  │           │  │ cts       │  │ layout    │  │
 │  │ sky130 lib ────┼──┼───────────┼──│ route     │──│ gdsii     │  │
 │  └───────────────┘  └───────────┘  └───────────┘  └───────────┘  │
 │                                                                    │
 │  Commands:                                                         │
 │  ┌───────────────────────────────────────────────────────────────┐ │
 │  │ # Yosys synthesis with ABC                                      │ │
 │  │ $ yosys -c scripts/yosys_synth.tcl                              │ │
 │  │                                                                 │ │
 │  │ # OpenROAD flow (floorplan → place → CTS → route)              │ │
 │  │ $ openroad -script scripts/openroad_flow.tcl                    │ │
 │  │                                                                 │ │
 │  │ # Export GDSII                                                   │ │
 │  │ $ openroad -script scripts/export_gds.tcl                       │ │
 │  └───────────────────────────────────────────────────────────────┘ │
 │                                                                    │
 │  Outputs:                                                          │
 │    build/core_synth.v          — Gate-level netlist (sky130)       │
 │    build/core_synth.sdc        — Timing constraints (SDC)          │
 │    build/core.def              — Design Exchange Format            │
 │    build/core_routed.def       — Fully routed DEF                  │
 │    build/core.spef             — Parasitic extraction (SPEF)       │
 │    build/core.sdf              — Standard Delay Format             │
 │    build/core.gds              — GDSII stream (final layout)       │
 │    build/reports/timing.rpt    — Setup/hold timing report          │
 │    build/reports/area.rpt      — Cell area utilization             │
 │    build/reports/power.rpt     — Power estimation report           │
 │    build/reports/drc.rpt       — DRC violations                    │
 │    build/reports/lvs.rpt       — LVS report                        │
 └───────────────────────────────────────────────────────────────────┘
```

### Yosys Synthesis Script (`scripts/yosys_synth.tcl`)

```tcl
# Read Verilog
read_verilog build/lunahan_core.v

# Hierarchy check
hierarchy -top lunahan_core

# Generic synthesis (techmap to generic cells)
proc; opt; fsm; opt; memory; opt

# Technology mapping to sky130
techmap -map $::env(PDK_ROOT)/sky130A/libs.tech/techmap/sky130_techmap.v
abc -liberty $::env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib

# Cleanup
opt_clean; stat; check

# Write output
write_verilog -noattr build/core_synth.v
```

### OpenROAD Flow Script (`scripts/openroad_flow.tcl`)

```tcl
# Read design and library
read_lef $::env(PLATFORM_DIR)/lef/sky130_fd_sc_hd.tlef
read_lef $::env(PLATFORM_DIR)/lef/sky130_fd_sc_hd_merged.lef
read_liberty $::env(PLATFORM_DIR)/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
read_verilog build/core_synth.v
read_sdc build/core_synth.sdc
link_design lunahan_core

# Floorplan
initialize_floorplan -core_utilization 60 -core_aspect_ratio 1.0
place_pins

# Placement
global_placement
detailed_placement

# Clock Tree Synthesis
repair_clock_inverters
clock_tree_synthesis -root_buf "sky130_fd_sc_hd__clkbuf_16" \
    -buf_list "sky130_fd_sc_hd__clkbuf_16" \
    -sink_clustering_enable
detailed_placement

# Routing
global_route
detailed_route

# Reports
report_checks -path_delay min_max
report_power
report_design_area

# Write outputs
write_def build/core_routed.def
write_spef build/core.spef
```

### KLayout GDSII Viewer

```bash
klayout build/core.gds
```

---

## Phase 4: Post-Layout Simulation + Signoff Checks

```
 ┌───────────────────────────────────────────────────────────────────┐
 │ PHASE 4 — POST-LAYOUT VERIFICATION & SIGNOFF                      │
 │                                                                    │
 │  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐     │
 │  │ Gate-Level    │    │ STA           │    │ DRC/LVS       │     │
 │  │ Simulation    │    │ (Setup/Hold)  │    │ (Magic/       │     │
 │  │               │    │               │    │  netgen)      │     │
 │  │ core_synth.v  │    │ core.spef     │    │               │     │
 │  │ + core.sdf    │───►│ core.sdc      │───►│ core.gds      │────►│
 │  │               │    │               │    │ core_synth.v  │     │
 │  └───────┬───────┘    └───────────────┘    └───────────────┘     │
 │          │                                                         │
 │          ▼                                                         │
 │  ┌───────────────┐                                                 │
 │  │ Verilator     │                                                 │
 │  │ GLS co-sim    │                                                 │
 │  └───────────────┘                                                 │
 │                                                                    │
 │  Outputs:                                                          │
 │    build/reports/timing_signon.rpt   — Final timing signoff        │
 │    build/reports/drc_clean.rpt       — DRC-clean signoff           │
 │    build/reports/lvs_clean.rpt       — LVS-clean signoff           │
 │    build/reports/gls_passed.rpt      — Gate-level sim report       │
 └───────────────────────────────────────────────────────────────────┘
```

---

## Tool Versions and Dependencies

| Tool             | Version   | Role                                      | Installation                       |
| ---------------- | --------- | ----------------------------------------- | ---------------------------------- |
| Python           | ≥ 3.10    | Runtime for pyCircuit                     | `brew install python@3.10`        |
| pyCircuit        | ≥ 5.0.0   | Python→MLIR frontend                      | `pip install pycircuit`           |
| pycc             | ≥ 0.8.0   | MLIR→C++/Verilog backend                  | `pip install pycc`                |
| Verilator        | ≥ 5.0     | RTL co-simulation, lint                   | `brew install verilator`          |
| GTKWave          | ≥ 3.3     | Waveform viewer                           | `brew install gtkwave`            |
| Yosys            | ≥ 0.40    | Logic synthesis                           | `brew install yosys`              |
| OpenROAD         | ≥ 2.0     | Place & route                             | `brew install openroad`           |
| KLayout          | ≥ 0.28    | GDS viewer                                | `brew install klayout`            |
| Magic            | ≥ 8.3     | DRC/LVS                                   | `brew install magic`              |
| Netgen           | ≥ 1.5     | LVS netlist comparison                    | `brew install netgen`             |
| sky130 PDK       | latest    | SkyWater 130 nm open PDK                  | `git clone` + `make timing`       |
| RISCOF           | ≥ 1.0     | RISC-V compliance framework               | `pip install riscof`              |

### Environment Variables

```bash
export PDK_ROOT=$HOME/pdk
export PDK=sky130A
export OPENROAD_EXE=$(which openroad)
export YOSYS_EXE=$(which yosys)
```

---

## Command Quick Reference

```bash
# =============================================
# Phase 1: Python → MLIR
# =============================================
# Build MLIR bytecode
python scripts/build_mlir.py rtl/lunahan_core.py --out build/

# Inspect MLIR
pyc dump-mlir build/lunahan_core.pyc --top lunahan_core

# =============================================
# Phase 2: MLIR → C++ / Verilog
# =============================================
# C++ simulation
pycc emit-cpp build/lunahan_core.pyc --top lunahan_core -o build/lunahan_core.cpp
g++ -O2 -std=c++17 build/lunahan_core.cpp -o build/lunahan_core_sim

# Run simulation with a program
./build/lunahan_core_sim --hex tests/system/rv32ui-p-add.hex --cycles 10000

# Emit Verilog
pycc emit-verilog build/lunahan_core.pyc --top lunahan_core -o build/lunahan_core.v

# =============================================
# Phase 3: Synthesis → Place & Route
# =============================================
# Logic synthesis
yosys -c scripts/yosys_synth.tcl

# Static timing analysis (pre-layout)
sta -no_spef build/core_synth.v build/core_synth.sdc

# Place & Route
openroad -script scripts/openroad_flow.tcl

# =============================================
# Phase 4: Signoff
# =============================================
# Gate-level simulation with Verilator
verilator --cc build/core_synth.v --top-module lunahan_core \
    -CFLAGS "-I$VERILATOR_ROOT/include" \
    -LDFLAGS "-L$VERILATOR_ROOT/lib -lverilated"

# DRC with Magic
magic -dnull -noconsole -rcfile $PDK_ROOT/sky130A/libs.tech/magic/sky130A.magicrc \
    <<EOF
gds read build/core.gds
drc check
drc catchup
drc why
EOF

# LVS with netgen
netgen -batch lvs "build/core_synth.spice lunahan_core" \
    "build/core_layout.spice lunahan_core" \
    $PDK_ROOT/sky130A/libs.tech/netgen/sky130A_setup.tcl \
    build/reports/lvs.rpt
```

---

## Directory Layout After Full Build

```
lunahan_v1/
├── build/
│   ├── lunahan_core.pyc            # Phase 1: MLIR bytecode
│   ├── lunahan_core.mlir           # Phase 1: Textual MLIR dump
│   ├── lunahan_core.cpp            # Phase 2: C++ simulation model
│   ├── lunahan_core_sim            # Phase 2: Compiled simulator
│   ├── lunahan_core.v              # Phase 2: Verilog RTL
│   ├── core_synth.v                # Phase 3: Gate-level netlist
│   ├── core_synth.sdc              # Phase 3: Timing constraints
│   ├── core_routed.def             # Phase 3: Routed DEF
│   ├── core.spef                   # Phase 3: Parasitic extraction
│   ├── core.sdf                    # Phase 3: SDF back-annotation
│   ├── core.gds                    # Phase 3: GDSII layout
│   └── reports/
│       ├── timing.rpt              # Phase 3: Timing analysis
│       ├── area.rpt                # Phase 3: Area report
│       ├── power.rpt               # Phase 3: Power report
│       ├── drc.rpt                 # Phase 4: DRC violations
│       └── lvs.rpt                 # Phase 4: LVS comparison
└── ...
```
