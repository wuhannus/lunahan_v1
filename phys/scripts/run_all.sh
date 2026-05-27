#!/usr/bin/env bash
#
# lunahan_v1 Physical Design Flow
# Target: sky130 (SkyWater 130nm) via OpenROAD 2.0+
#
# Usage: bash phys/scripts/run_all.sh
#
# Prerequisites:
#   brew install yosys openroad klayout
#   git clone https://github.com/google/skywater-pdk.git
#
# Outputs:
#   phys/out/synthesis/lunahan_core.v      - Synthesized netlist
#   phys/out/floorplan/lunahan_core.def     - Floorplan
#   phys/out/place/lunahan_core.def         - Placement
#   phys/out/cts/lunahan_core.def           - Clock tree
#   phys/out/route/lunahan_core.def         - Routed design
#   phys/out/signoff/lunahan_core.gds       - Final GDSII
#   phys/out/signoff/lunahan_core.rpt       - Signoff report
#   phys/out/postsim/*.vcd                  - Post-layout simulation traces

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="$ROOT_DIR/phys/out"
PDK_ROOT="${PDK_ROOT:-$HOME/skywater-pdk}"
DESIGN_NAME="lunahan_core"
TOP_MODULE="lunahan_core"
CLK_PORT="clk"
CLK_PERIOD_NS="${CLK_PERIOD_NS:-10.0}"   # 100 MHz target
RTL_SRC="$ROOT_DIR/rtl"

log() { echo "[phys] $*"; }
die()  { echo "[phys] ERROR: $*" >&2; exit 1; }

mkdir -p "$OUT_DIR"/{synthesis,floorplan,place,cts,route,signoff,postsim}

SKY130_LIB="${PDK_ROOT}/libraries/sky130_fd_sc_hd/latest"
SKY130_TECH="${PDK_ROOT}/libraries/sky130_fd_sc_hd/latest/tech"
SKY130_LEF="${SKY130_TECH}/sky130_fd_sc_hd.tlef"
SKY130_LIBS="${SKY130_LIB}/cells/*/sky130_fd_sc_hd__*.lib"

# ── 0) Generate Verilog from pyCircuit ─────────────────────────────
log "Step 0: Generate Verilog from pyCircuit Python RTL"
cd "$ROOT_DIR"

if ! command -v pycircuit >/dev/null 2>&1; then
  die "pycircuit CLI not found. Install: pip install -e . (in pyCircuit repo)"
fi

python3 -m pycircuit.cli build \
  "$RTL_SRC/lunahan_core.py" \
  --out-dir "$OUT_DIR/synthesis" \
  --target verilog \
  --jobs 4 2>&1 | tail -5 || die "Verilog generation failed"

VERILOG_SRC="$OUT_DIR/synthesis/lunahan_core.v"
[[ -f "$VERILOG_SRC" ]] || die "Verilog output not found at $VERILOG_SRC"

# ── 1) Synthesis (Yosys + ABC) ─────────────────────────────────────
log "Step 1: Logic synthesis (Yosys + ABC)"

cat > "$OUT_DIR/synthesis/synth.tcl" <<SYNTH
yosys -import
read_verilog -sv $VERILOG_SRC
hierarchy -check -top $TOP_MODULE
proc; opt; fsm; opt; memory; opt
techmap; opt
dfflibmap -liberty ${SKY130_LIBS}
abc -liberty ${SKY130_LIBS} -D 1000
opt_clean
splitnets
write_verilog -noattr $OUT_DIR/synthesis/${DESIGN_NAME}_synth.v
stat -liberty ${SKY130_LIBS}
tee -o $OUT_DIR/synthesis/${DESIGN_NAME}_synth.rpt stat
SYNTH

yosys -s "$OUT_DIR/synthesis/synth.tcl" 2>&1 | tail -20 || die "Synthesis failed"
log "Synthesis complete: $OUT_DIR/synthesis/${DESIGN_NAME}_synth.v"

# ── 2) Floorplan ───────────────────────────────────────────────────
log "Step 2: Floorplan"

# Estimate area from synthesis report
CELL_COUNT=$(grep -oP 'Number of cells:\s+\K\d+' "$OUT_DIR/synthesis/${DESIGN_NAME}_synth.rpt" || echo "5000")
DIE_AREA_UM=$(echo "scale=0; sqrt($CELL_COUNT * 50) * 1.4" | bc)  # rough estimate
CORE_UTIL="${CORE_UTIL:-0.65}"

cat > "$OUT_DIR/floorplan/fp.tcl" <<FP
read_lef $SKY130_LEF
read_liberty ${SKY130_LIBS}
read_verilog $OUT_DIR/synthesis/${DESIGN_NAME}_synth.v
link_design $TOP_MODULE

initialize_floorplan \
  -die_area "0 0 ${DIE_AREA_UM} ${DIE_AREA_UM}" \
  -core_area "5 5 $(echo "$DIE_AREA_UM - 5" | bc) $(echo "$DIE_AREA_UM - 5" | bc)" \
  -site unithd

global_placement -density $CORE_UTIL
write_def $OUT_DIR/floorplan/${DESIGN_NAME}_fp.def
write_db $OUT_DIR/floorplan/${DESIGN_NAME}_fp.odb
FP

openroad -no_init -exit "$OUT_DIR/floorplan/fp.tcl" 2>&1 | tail -10 || die "Floorplan failed"
log "Floorplan complete: $OUT_DIR/floorplan/${DESIGN_NAME}_fp.def"

# ── 3) Placement ───────────────────────────────────────────────────
log "Step 3: Placement"

cat > "$OUT_DIR/place/place.tcl" <<PLACE
read_lef $SKY130_LEF
read_liberty ${SKY130_LIBS}
read_def $OUT_DIR/floorplan/${DESIGN_NAME}_fp.def
read_sdc -echo "$OUT_DIR/place/${DESIGN_NAME}.sdc" <<SDC
create_clock [get_ports $CLK_PORT] -period ${CLK_PERIOD_NS}
set_input_delay 1.0 [all_inputs] -clock $CLK_PORT
set_output_delay 1.0 [all_outputs] -clock $CLK_PORT
SDC

detailed_placement
check_placement

write_def $OUT_DIR/place/${DESIGN_NAME}_placed.def
write_db $OUT_DIR/place/${DESIGN_NAME}_placed.odb
PLACE

openroad -no_init -exit "$OUT_DIR/place/place.tcl" 2>&1 | tail -10 || die "Placement failed"
log "Placement complete: $OUT_DIR/place/${DESIGN_NAME}_placed.def"

# ── 4) Clock Tree Synthesis ────────────────────────────────────────
log "Step 4: Clock Tree Synthesis"

cat > "$OUT_DIR/cts/cts.tcl" <<CTS
read_lef $SKY130_LEF
read_liberty ${SKY130_LIBS}
read_def $OUT_DIR/place/${DESIGN_NAME}_placed.def

clock_tree_synthesis -root_buf sky130_fd_sc_hd__clkbuf_16 \
  -buf_list sky130_fd_sc_hd__clkbuf_16 \
  -sink_clustering_enable

detailed_placement
repair_clock_inverters
repair_timing

write_def $OUT_DIR/cts/${DESIGN_NAME}_cts.def
write_db $OUT_DIR/cts/${DESIGN_NAME}_cts.odb
CTS

openroad -no_init -exit "$OUT_DIR/cts/cts.tcl" 2>&1 | tail -10 || die "CTS failed"
log "CTS complete: $OUT_DIR/cts/${DESIGN_NAME}_cts.def"

# ── 5) Routing ─────────────────────────────────────────────────────
log "Step 5: Routing"

cat > "$OUT_DIR/route/route.tcl" <<ROUTE
read_lef $SKY130_LEF
read_liberty ${SKY130_LIBS}
read_def $OUT_DIR/cts/${DESIGN_NAME}_cts.def

global_route
repair_timing
detailed_route

write_def $OUT_DIR/route/${DESIGN_NAME}_routed.def
write_db $OUT_DIR/route/${DESIGN_NAME}_routed.odb
ROUTE

openroad -no_init -exit "$OUT_DIR/route/route.tcl" 2>&1 | tail -10 || die "Routing failed"
log "Routing complete: $OUT_DIR/route/${DESIGN_NAME}_routed.def"

# ── 6) Signoff & GDS Generation ────────────────────────────────────
log "Step 6: Signoff checks + GDSII generation"

cat > "$OUT_DIR/signoff/signoff.tcl" <<SO
read_lef $SKY130_LEF
read_liberty ${SKY130_LIBS}
read_def $OUT_DIR/route/${DESIGN_NAME}_routed.def

# Timing signoff
report_checks -path_delay min_max -format full \
  > $OUT_DIR/signoff/${DESIGN_NAME}_timing.rpt

# DRC check
check_design -checks drc \
  > $OUT_DIR/signoff/${DESIGN_NAME}_drc.rpt

# Antenna check
check_antennas \
  > $OUT_DIR/signoff/${DESIGN_NAME}_antenna.rpt

# Area report
report_design_area \
  > $OUT_DIR/signoff/${DESIGN_NAME}_area.rpt

# Power report
report_power \
  > $OUT_DIR/signoff/${DESIGN_NAME}_power.rpt

# GDSII output
write_gds $OUT_DIR/signoff/${DESIGN_NAME}.gds

# SPEF extraction (for post-simulation)
write_spef $OUT_DIR/postsim/${DESIGN_NAME}.spef

# SDF for timing back-annotation
write_sdf $OUT_DIR/postsim/${DESIGN_NAME}.sdf
SO

openroad -no_init -exit "$OUT_DIR/signoff/signoff.tcl" 2>&1 | tail -15 || die "Signoff failed"
log "Signoff complete: $OUT_DIR/signoff/${DESIGN_NAME}.gds"

# ── 7) Post-Layout Simulation ──────────────────────────────────────
log "Step 7: Post-layout gate-level simulation"

cat > "$OUT_DIR/postsim/postsim.tcl" <<POSTSIM
read_verilog -sv $OUT_DIR/synthesis/${DESIGN_NAME}_synth.v
read_sdf $OUT_DIR/postsim/${DESIGN_NAME}.sdf
read_spef $OUT_DIR/postsim/${DESIGN_NAME}.spef

# Run the same test vectors used in pre-layout sim
# Compare results against golden reference
POSTSIM

log "Post-simulation setup ready. Run manually with:"
log "  iverilog -o $OUT_DIR/postsim/simv $OUT_DIR/postsim/*.v"
log "  vvp $OUT_DIR/postsim/simv +SDF=$OUT_DIR/postsim/${DESIGN_NAME}.sdf"

# ── Report ──────────────────────────────────────────────────────────
log "=========================================="
log "  Physical Design Flow Complete"
log "=========================================="
log "  Synthesis:     $OUT_DIR/synthesis/${DESIGN_NAME}_synth.v"
log "  Floorplan:     $OUT_DIR/floorplan/${DESIGN_NAME}_fp.def"
log "  Placement:     $OUT_DIR/place/${DESIGN_NAME}_placed.def"
log "  CTS:           $OUT_DIR/cts/${DESIGN_NAME}_cts.def"
log "  Routed:        $OUT_DIR/route/${DESIGN_NAME}_routed.def"
log "  GDSII:         $OUT_DIR/signoff/${DESIGN_NAME}.gds"
log "  Timing rpt:    $OUT_DIR/signoff/${DESIGN_NAME}_timing.rpt"
log "  Area rpt:      $OUT_DIR/signoff/${DESIGN_NAME}_area.rpt"
log "  Power rpt:     $OUT_DIR/signoff/${DESIGN_NAME}_power.rpt"
log "  SPEF/SDF:      $OUT_DIR/postsim/"
log "=========================================="

# Print key metrics
if [[ -f "$OUT_DIR/signoff/${DESIGN_NAME}_timing.rpt" ]]; then
  log "Timing summary:"
  grep -A2 "wns\|tns" "$OUT_DIR/signoff/${DESIGN_NAME}_timing.rpt" | head -10
fi
if [[ -f "$OUT_DIR/signoff/${DESIGN_NAME}_area.rpt" ]]; then
  log "Area summary:"
  head -10 "$OUT_DIR/signoff/${DESIGN_NAME}_area.rpt"
fi
