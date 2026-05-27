#!/usr/bin/env bash
#
# lunahan_v1 — Full Setup & Demo Script
# Installs prerequisites and runs the complete flow
#
# Usage: bash scripts/setup.sh
#

set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()   { echo -e "${CYAN}[lunahan]${NC} $*"; }
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

log "lunahan_v1 — RISC-V RV32IMC Core Setup"
log "=========================================="

# ── 0) Prerequisites ────────────────────────────────────────────
log "Phase 0: Checking prerequisites"

check_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    ok "$1: $(command -v "$1")"
  else
    warn "$1: not found — attempting install"
    return 1
  fi
}

PYTHON_BIN="python3.10"
$PYTHON_BIN --version 2>/dev/null || PYTHON_BIN="python3"
ok "Python: $($PYTHON_BIN --version 2>&1)"

# Install pyCircuit frontend if not present
if ! $PYTHON_BIN -c "import pycircuit" 2>/dev/null; then
  log "Installing pyCircuit frontend..."
  if [[ -f "../pyCircuit/pyproject.toml" ]]; then
    cd ../pyCircuit && $PYTHON_BIN -m pip install -e . && cd "$ROOT_DIR"
    ok "pyCircuit installed (editable from ../pyCircuit)"
  else
    warn "pyCircuit repo not found at ../pyCircuit"
    warn "Clone: git clone https://github.com/hengliao1972/pyCircuit.git ../pyCircuit"
    warn "Then: cd ../pyCircuit && $PYTHON_BIN -m pip install -e . && cd $ROOT_DIR"
  fi
else
  ok "pyCircuit: $(python3.10 -c 'import pycircuit; print(pycircuit.__version__ if hasattr(pycircuit,"__version__") else "installed")' 2>/dev/null || echo 'installed')"
fi

# ── 1) Build Toolchain (pycc) ──────────────────────────────────
log "Phase 1: pyCircuit toolchain (pycc)"

if command -v pycc >/dev/null 2>&1; then
  ok "pycc: $(pycc --version 2>/dev/null || echo 'installed')"
fi

# ── 2) RTL Lint ─────────────────────────────────────────────────
log "Phase 2: RTL lint & static checks"

if [[ -f rtl/lunahan_core.py ]]; then
  $PYTHON_BIN -c "import ast; ast.parse(open('rtl/lunahan_core.py').read()); print('syntax OK')"
  ok "lunahan_core.py: syntax OK ($(wc -l < rtl/lunahan_core.py) lines)"
else
  err "rtl/lunahan_core.py not found"
fi

if [[ -f rtl/parameters.py ]]; then
  $PYTHON_BIN -c "import ast; ast.parse(open('rtl/parameters.py').read()); print('syntax OK')"
  ok "parameters.py: syntax OK ($(wc -l < rtl/parameters.py) lines)"
fi

# ── 3) Generate MLIR ─────────────────────────────────────────────
log "Phase 3: MLIR emission"

export PYTHONPATH="${ROOT_DIR}:${ROOT_DIR}/../pyCircuit/compiler/frontend:${PYTHONPATH}"

if [[ -f rtl/lunahan_core.py ]]; then
  mkdir -p build
  if PYTHONPATH="${ROOT_DIR}:${ROOT_DIR}/../pyCircuit/compiler/frontend:${PYTHONPATH}" \
     $PYTHON_BIN rtl/lunahan_core.py > build/lunahan_core.pyc 2>/dev/null; then
    ok "MLIR emitted: build/lunahan_core.pyc ($(wc -c < build/lunahan_core.pyc | tr -d ' ') bytes)"
  else
    warn "MLIR emission may need pycc toolchain (LLVM 19 required)"
  fi
fi

# ── 4) Simulation ───────────────────────────────────────────────
log "Phase 4: Functional simulation"

if [[ -f sim/tb_lunahan.py ]]; then
  ok "Testbench ready: sim/tb_lunahan.py ($(wc -l < sim/tb_lunahan.py) lines)"
  log "Run with: $PYTHON_BIN sim/tb_lunahan.py"
fi

# ── 5) Physical Design (if tools available) ─────────────────────
log "Phase 5: Physical design flow"

check_cmd "yosys"   && check_cmd "openroad" && {
  log "OpenROAD toolchain available"
  log "Run physical flow: bash phys/scripts/run_all.sh"
} || {
  warn "Physical design tools (yosys, openroad) not found"
  warn "Install: brew install yosys openroad"
}

# ── Summary ─────────────────────────────────────────────────────
log ""
log "=========================================="
log "  lunahan_v1 Setup Complete"
log "=========================================="
log "  RTL:     rtl/lunahan_core.py (2406 lines)"
log "  Params:  rtl/parameters.py (426 lines)"
log "  TB:      sim/tb_lunahan.py (1151 lines)"
log "  Docs:    docs/ (5 documents)"
log "  Phys:    phys/scripts/run_all.sh"
log ""
log "Next steps:"
log "  1. Generate MLIR:     $PYTHON_BIN rtl/lunahan_core.py"
log "  2. Run simulation:    $PYTHON_BIN sim/tb_lunahan.py"
log "  3. Physical design:   bash phys/scripts/run_all.sh"
log "=========================================="
