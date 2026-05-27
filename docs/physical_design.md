# Physical Design — lunahan_v1

## 1. Overview

The physical implementation of lunahan_v1 targets the **SkyWater 130 nm**
open-source PDK through the **OpenROAD** flow. All tools used are
open-source, ensuring full reproducibility.

```
 ┌──────────────────────────────────────────────────────────────┐
 │                  PHYSICAL DESIGN FLOW                        │
 │                                                              │
 │  Verilog RTL ──────► Yosys Synthesis ────► Gate Netlist      │
 │                                              + SDC            │
 │                                                              │
 │  Gate Netlist ─────► OpenROAD Floorplan ──► DEF              │
 │  SDC, LEF, LIB ────► OpenROAD Placement ►                    │
 │                     ► OpenROAD CTS ──────►                    │
 │                     ► OpenROAD Routing ──► Routed DEF        │
 │                                                              │
 │  Routed DEF ──────► OpenROAD RC Extraction ──► SPEF          │
 │                     OpenROAD STA ──────────► Timing Report   │
 │                     Magic DRC ─────────────► DRC Report      │
 │                     Netgen LVS ────────────► LVS Report       │
 │                                                              │
 │  GDSII Export ────► KLayout Viewer ────────► Signoff         │
 └──────────────────────────────────────────────────────────────┘
```

---

## 2. Toolchain

| Tool              | Version   | Purpose                                      |
| ----------------- | --------- | -------------------------------------------- |
| Yosys             | ≥ 0.40    | RTL synthesis, technology mapping            |
| ABC (via Yosys)   | latest    | Logic optimization, technology-independent   |
| OpenROAD          | ≥ 2.0     | Floorplan, placement, CTS, routing, STA      |
| OpenSTA           | latest    | Static timing analysis (integrated in OpenROAD) |
| Magic             | ≥ 8.3     | DRC (design rule checking)                   |
| Netgen            | ≥ 1.5     | LVS (layout vs. schematic)                   |
| KLayout           | ≥ 0.28    | GDSII viewer, layout inspection              |
| sky130 PDK        | latest    | SkyWater 130 nm open PDK                     |

### PDK Installation

```bash
export PDK_ROOT=$HOME/pdk
git clone https://github.com/google/skywater-pdk.git $PDK_ROOT/skywater-pdk
cd $PDK_ROOT/skywater-pdk
git submodule update --init libraries/sky130_fd_sc_hd/latest
make timing
```

### Standard Cell Library

| Parameter               | sky130_fd_sc_hd                  |
| ----------------------- | -------------------------------- |
| Technology node         | 130 nm                           |
| Voltage (nominal)       | 1.8 V                            |
| Temperature (nominal)   | 25°C                             |
| Height                  | 3.33 µm (12 tracks)              |
| Track pitch             | 0.46 µm                          |
| Metal layers            | 5 (M1–M5) + local interconnect   |
| Cell count              | ~400 standard cells              |
| Typical gate density    | ~300 K gates / mm²               |

---

## 3. Synthesis Strategy

### Yosys Synthesis Script (`scripts/yosys_synth.tcl`)

```tcl
# =============================================
# lunahan_v1 Yosys Synthesis Script
# =============================================

# Read Verilog design
read_verilog build/lunahan_core.v

# Set top module
hierarchy -top lunahan_core

# =============================================
# Synthesis passes (technology-independent)
# =============================================

# Process hierarchy
proc

# Constant propagation and basic optimization
opt

# Finite state machine extraction and optimization
fsm; opt

# Memory inference → registers + muxes
memory; opt

# Technology-independent optimization
opt_clean

# =============================================
# Technology mapping to sky130
# =============================================

# Map to internal cells
techmap

# Map to sky130 standard cells
dfflibmap -liberty $::env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib

# ABC logic optimization with sky130 library
abc -liberty $::env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib \
    -D 1000 \
    -constr "set_driving_cell sky130_fd_sc_hd__buf_1; set_load 0.05"

# Final cleanup
opt_clean

# =============================================
# Reports
# =============================================

# Statistics
stat -liberty $::env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib

# Design check
check

# =============================================
# Write outputs
# =============================================

# Gate-level netlist (Verilog)
write_verilog -noattr -noexpr build/core_synth.v

# SDC constraints file
write_sdc build/core_synth.sdc
```

### SDC Timing Constraints (`build/core_synth.sdc`)

```tcl
# =============================================
# lunahan_v1 Timing Constraints
# =============================================

# Clock definition
set clk_name  clk
set clk_port  clk_i
set clk_period 20.0          ;# 50 MHz target
set clk_uncertainty 1.0       ;# clock uncertainty (jitter + skew budget)
set clk_transition 0.5        ;# clock transition time

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]
set_clock_uncertainty $clk_uncertainty [get_clocks $clk_name]
set_clock_transition $clk_transition [get_clocks $clk_name]

# Reset
set reset_port reset_n_i
set_input_delay -clock $clk_name -max 2.0 [get_ports $reset_port]
set_input_delay -clock $clk_name -min 0.0 [get_ports $reset_port]

# Input delays (AXI4-Lite read response, timer interrupt)
# These arrive from external peripherals
set_input_delay -clock $clk_name -max 5.0 [get_ports {rdata_i rvalid_i rresp_i bvalid_i bresp_i}]
set_input_delay -clock $clk_name -min 1.0 [get_ports {rdata_i rvalid_i rresp_i bvalid_i bresp_i}]

# Output delays (AXI4-Lite requests, addresses, write data)
set_output_delay -clock $clk_name -max 5.0 [get_ports {awaddr_o awvalid_o wdata_o wstrb_o wvalid_o bready_o araddr_o arvalid_o rready_o}]
set_output_delay -clock $clk_name -min 1.0 [get_ports {awaddr_o awvalid_o wdata_o wstrb_o wvalid_o bready_o araddr_o arvalid_o rready_o}]

# Load constraints
set_load 0.05 [all_outputs]

# Driving cell assumption
set_driving_cell -lib_cell sky130_fd_sc_hd__buf_1 [all_inputs]

# False paths
set_false_path -from [get_ports reset_n_i]

# Multicycle paths
# MUL takes 5 cycles → set multicycle for mul-related logic
# Actually, MUL is pipelined within EX, so no multicycle needed
```

---

## 4. Floorplan

### Core Area Estimation

| Component               | Gate Count (est.) | Area Ratio |
| ----------------------- | ----------------- | ---------- |
| Register File (32×32)   | ~4000             | 20%        |
| ALU + Forwarding Muxes  | ~2500             | 12.5%      |
| Decoder + C Expander    | ~2000             | 10%        |
| I-Cache (tag+data SRAM) | ~3000             | 15%        |
| D-Cache (tag+data SRAM) | ~3500             | 17.5%      |
| Multiplier/Divider      | ~2000             | 10%        |
| CSR Unit                | ~1000             | 5%         |
| Pipeline control + misc | ~2000             | 10%        |
| **Total**               | **~20,000**       | **100%**   |

Total cell area at sky130 density (~300 K gates/mm²):
→ ~0.067 mm² (active gates only)

With utilization target of **60%**: total core area ≈ **0.11 mm²**

We round up to **0.25 mm²** for margin and routing congestion.

### Aspect Ratio and Dimensions

Square aspect ratio (1:1):
- Width: 500 µm
- Height: 500 µm
- Target utilization: 60%

### Pin Placement

| Pin Group            | Edge       | Description                          |
| -------------------- | ---------- | ------------------------------------ |
| clk_i, reset_n_i     | Left (W)   | Global clock and reset               |
| AXI4-Lite (read)     | Left (W)   | Read address, read data response     |
| AXI4-Lite (write)    | Right (E)  | Write address, write data, response  |
| Interrupt inputs     | Top (N)    | Timer, software, external interrupts |
| Debug/test signals   | Bottom (S) | Optional debug outputs               |

### OpenROAD Floorplan Commands

```tcl
# Initialize floorplan
initialize_floorplan \
    -site unithd \
    -die_area "0 0 500 500" \
    -core_area "10 10 490 490" \
    -utilization 60

# Place pins
place_pins -hor_layers "met3" -ver_layers "met2" \
    -random -random_seed 42
```

---

## 5. Placement

### Global Placement

- **Algorithm**: RePlAce (analytical, electrostatics-based) or ePlace
- **Density target**: 60%
- **Routability-driven**: Enable routability estimation during placement

```tcl
# Global placement
global_placement -density 0.60 \
    -pad_left 2 -pad_right 2 \
    -routability_driven
```

### Detailed Placement

- Legalization: snap to rows, remove overlaps
- Optimization: gate sizing, buffer insertion for timing

```tcl
detailed_placement

# Optional: timing-driven repacking
repair_design
```

### Placement Constraints (grouping)

```tcl
# Group register file cells together
create_rp_group -name regfile \
    -util 0.70 \
    -cells [get_cells -regex ".*regfile.*"]

# Group cache tag+data SRAM cells
create_rp_group -name icache \
    -util 0.70 \
    -cells [get_cells -regex ".*icache.*"]

create_rp_group -name dcache \
    -util 0.70 \
    -cells [get_cells -regex ".*dcache.*"]
```

---

## 6. Clock Tree Synthesis (CTS)

lunahan_v1 uses a **single clock domain** (no clock gating in v1).

### CTS Configuration

```tcl
# Clock tree synthesis
clock_tree_synthesis \
    -root_buf "sky130_fd_sc_hd__clkbuf_16" \
    -buf_list "sky130_fd_sc_hd__clkbuf_16" \
    -wire_unit 10 \
    -sink_clustering_enable \
    -sink_clustering_size 20 \
    -sink_clustering_max_diameter 60

# Fix hold violations post-CTS
repair_clock_inverters

# Legalize placement after clock buffer insertion
detailed_placement

# Post-CTS timing
report_checks -path_delay min_max
```

### CTS Targets

| Metric            | Target        |
| ----------------- | ------------- |
| Clock skew (max)  | ≤ 100 ps      |
| Clock latency     | ≤ 2 ns        |
| Slew rate         | ≤ 500 ps      |
| Max capacitance   | ≤ 50 fF       |
| Max fanout        | ≤ 16          |

---

## 7. Routing

### Global Routing

```tcl
global_route \
    -guide_file build/route.guide \
    -verbose
```

### Detailed Routing

```tcl
detailed_route \
    -output_drc build/reports/route_drc.rpt \
    -verbose
```

### Metal Layer Usage

| Layer | Purpose                              | Direction    |
| ----- | ------------------------------------ | ------------ |
| M1    | Standard cell internal, local routing | Horizontal  |
| M2    | Intermediate routing, clock          | Vertical    |
| M3    | Longer intermediate routing, clock   | Horizontal  |
| M4    | Power grid (VDD/VSS), global routing | Vertical    |
| M5    | Power grid, clock, top-level routing | Horizontal  |

Clock is routed primarily on M3 (horizontal) and M2/M4 (vertical) for
equalized delay.

### Antenna Check

```tcl
# Antenna rule check
check_antenna
```

---

## 8. Signoff

### 8.1 Static Timing Analysis (STA)

```tcl
# Read SPEF (parasitics) for post-route timing
read_spef build/core.spef

# Setup timing
report_checks -path_delay max \
    -group_count 10 \
    -format full_clock_expanded \
    > build/reports/setup_timing.rpt

# Hold timing
report_checks -path_delay min \
    -group_count 10 \
    -format full_clock_expanded \
    > build/reports/hold_timing.rpt

# Slack histogram
report_tns
report_wns
```

| Check        | Condition        | Target             |
| ------------ | ---------------- | ------------------ |
| Setup (WNS)  | tt, 25°C, 1.8V  | ≥ 0 ps (positive slack) |
| Hold (WNS)   | ff, -40°C, 1.98V| ≥ 0 ps             |
| Setup (WNS)  | ss, 125°C, 1.62V| ≥ 0 ps             |

### 8.2 DRC (Design Rule Check)

Magic is used for DRC:

```bash
magic -dnull -noconsole <<EOF
gds read build/core.gds
load lunahan_core
select top cell
drc check
drc catchup
set drc_report build/reports/drc.rpt
drc listall
quit
EOF
```

Target: **0 DRC violations**.

### 8.3 LVS (Layout vs. Schematic)

Netgen compares the extracted SPICE netlist from layout against the
synthesized gate-level netlist:

```bash
netgen -batch lvs \
    "build/core_synth.spice lunahan_core" \
    "build/core_layout.spice lunahan_core" \
    $PDK_ROOT/sky130A/libs.tech/netgen/sky130A_setup.tcl \
    build/reports/lvs.rpt
```

Target: **LVS clean** (no mismatches between layout and schematic).

### 8.4 Post-Layout Gate-Level Simulation

Use Verilator with SDF back-annotation:

```bash
verilator --cc build/core_synth.v \
    --top-module lunahan_core \
    -CFLAGS "-I$VERILATOR_ROOT/include" \
    -LDFLAGS "-L$VERILATOR_ROOT/lib -lverilated" \
    --timing \
    --sdf-sim build/core.sdf

make -C obj_dir -f Vlunahan_core.mk

./obj_dir/Vlunahan_core \
    +trace \
    +hex=tests/system/dhrystone/dhrystone.hex \
    +cycles=100000
```

### 8.5 Power Estimation

OpenROAD provides power estimation using switching activity data:

```tcl
# Read switching activity (VCD from gate-level simulation)
read_activity -vcd build/core_sim.vcd

# Power report
report_power \
    -corner tt \
    > build/reports/power.rpt
```

| Power Component   | Target       |
| ----------------- | ------------ |
| Total dynamic     | ≤ 10 mW      |
| Leakage           | ≤ 2 mW       |
| Clock tree        | ≤ 3 mW       |
| **Total**         | **≤ 15 mW**  |

---

## 9. GDSII Export

```tcl
# Write GDSII stream
write_gds build/core.gds

# Write final DEF
write_def build/core_final.def
```

### GDSII Layer Map (sky130)

```tcl
# Layer mapping for GDSII
set tech_lef $::env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/techlef/sky130_fd_sc_hd__nom.tlef
set layer_map $::env(PDK_ROOT)/sky130A/libs.tech/openroad/layer_map
```

---

## 10. Target Metrics Summary

| Metric                     | Target          | Notes                              |
| -------------------------- | --------------- | ---------------------------------- |
| Technology                 | sky130 (130 nm) | SkyWater open PDK                  |
| Voltage                    | 1.8 V           | Nominal                            |
| Temperature range          | -40°C to 125°C  | Commercial/industrial              |
| Max frequency (tt, 25°C)  | ≥ 50 MHz        | Post-route, with SPEF              |
| Core area                  | ≤ 0.25 mm²      | Including SRAM macros              |
| Gate count                 | ~20 K           | Standard cell instances            |
| SRAM instances             | 4 (2 per cache) | I$ tag, I$ data, D$ tag, D$ data   |
| Utilization                | 60%             | Cell area / core area              |
| Power (total, 50 MHz)      | ≤ 15 mW         | Post-route estimation              |
| Power density              | ≤ 60 mW/mm²     | Total power / core area            |
| DRC violations             | 0               | Magic DRC                          |
| LVS clean                  | Yes             | Netgen LVS                         |
| Setup TNS                  | 0 ns            | All corners                        |
| Hold TNS                   | 0 ns            | All corners                        |

---

## 11. Future: Macro Hardening

For production-quality tapeout, the SRAM arrays (cache tag and data) should
be replaced with compiled SRAM macros (e.g., OpenRAM-generated) for better
density and characterized timing. The current flow uses register-based
SRAM models for simplicity; macro integration would reduce area by ~40%.

```tcl
# Future: replace SRAM register arrays with OpenRAM macros
# read_lef $::env(PDK_ROOT)/sky130A/libs.ref/sky130_sram_macros/lef/sky130_sram_1kbyte_1rw1r_32x256_8.lef
# read_liberty $::env(PDK_ROOT)/sky130A/libs.ref/sky130_sram_macros/lib/sky130_sram_1kbyte_1rw1r_32x256_8_tt_025C_1v80.lib
```
