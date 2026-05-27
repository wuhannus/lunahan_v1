# Verification Plan — lunahan_v1

## 1. Verification Philosophy

The verification strategy for lunahan_v1 follows XiangShan's
verification-in-the-loop methodology:

1. **Correctness by construction**: pyCircuit's type system and structural
   checks catch width mismatches, unconnected signals, and combinational
   loops at compile time.
2. **Layered testing**: Unit → Integration → System, with each layer
   providing increasing confidence.
3. **Randomized stress**: Constrained-random instruction sequences expose
   pipeline corner cases (hazards, exceptions, inter-instruction
   dependencies).
4. **Compliance-first**: RISCOF compliance suite ensures architectural
   correctness against the RISC-V specification.
5. **Coverage-driven signoff**: Code, functional, and cross-coverage
   metrics gate tape-out readiness.

---

## 2. Test Levels

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                    VERIFICATION PYRAMID                           │
 │                                                                   │
 │                         ┌─────────┐                               │
 │                         │ RISCOF  │   ~2000 tests                 │
 │                         │ Suite   │   Full ISA compliance         │
 │                         └────┬────┘                               │
 │                              │                                    │
 │                     ┌────────┴────────┐                           │
 │                     │  System Tests   │   Random instruction      │
 │                     │  (hex programs) │   sequences, privilege    │
 │                     └───────┬─────────┘   tests, exceptions       │
 │                             │                                     │
 │                   ┌─────────┴──────────┐                          │
 │                   │  Integration Tests │   Pipeline hazards,      │
 │                   │  (multi-stage)     │   forwarding, flushing    │
 │                   └─────────┬──────────┘                          │
 │                             │                                     │
 │                 ┌───────────┴────────────┐                        │
 │                 │   Unit Tests           │   Per-stage, per-unit  │
 │                 │   (individual modules) │   directed checks       │
 │                 └────────────────────────┘                        │
 └──────────────────────────────────────────────────────────────────┘
```

### 2.1 Unit Tests (per-stage, per-unit)

Test each hardware block in isolation with directed stimulus.

| Module               | Test File                          | Tests |
| -------------------- | ---------------------------------- | ----- |
| ALU                  | `tests/unit/test_alu.py`           | All 21 operations, edge cases, overflow |
| Register File        | `tests/unit/test_regfile.py`       | Read/write, x0 hardwired, forwarding mux |
| Decoder (RV32I)      | `tests/unit/test_decode_rv32i.py`  | All 40 RV32I instructions, illegal detection |
| Decoder (M-ext)      | `tests/unit/test_decode_m.py`      | All 8 M instructions |
| Decoder (C-ext)      | `tests/unit/test_decode_c.py`      | All C instructions expanded correctly |
| Decoder (Illegal)    | `tests/unit/test_decode_illegal.py` | All reserved opcode spaces, RV64-only insts |
| BTB                  | `tests/unit/test_btb.py`           | Hit/miss, update, bimodal counter saturation |
| Multiplier           | `tests/unit/test_mul.py`           | Random operand pairs, edge cases (0, max, min) |
| Divider              | `tests/unit/test_div.py`           | Random pairs, divide by zero, overflow |
| LSU (Load/Store)     | `tests/unit/test_lsu.py`           | All widths, alignment, sign-extend |
| CSR Unit             | `tests/unit/test_csr.py`           | Read/write all CSRs, atomic r/m/w |
| Exception handler    | `tests/unit/test_exception.py`     | All exception types, priority, mret |
| Hazard detection     | `tests/unit/test_hazards.py`       | All RAW patterns, forwarding priority |
| Forwarding logic     | `tests/unit/test_forwarding.py`    | All forward paths, priority muxing |

### 2.2 Integration Tests (pipeline)

Test interactions between pipeline stages with directed instruction sequences.

| Test Scenario                          | Test File                                  |
| -------------------------------------- | ------------------------------------------ |
| Back-to-back dependent ALU instructions| `tests/integration/test_alu_deps.py`       |
| Load-use hazard (1-cycle stall)        | `tests/integration/test_load_use.py`       |
| Store-after-load forwarding            | `tests/integration/test_store_after_load.py` |
| Branch mispredict recovery             | `tests/integration/test_branch_mispred.py`  |
| JAL/JALR link chain                    | `tests/integration/test_jump_chain.py`     |
| I-Cache miss recovery                  | `tests/integration/test_icache_miss.py`     |
| D-Cache miss recovery                  | `tests/integration/test_dcache_miss.py`     |
| Exception in pipeline                  | `tests/integration/test_pipeline_except.py` |
| MRET pipeline redirect                 | `tests/integration/test_mret.py`           |
| CSR read-after-write                   | `tests/integration/test_csr_raw.py`        |
| MUL instructin pipeline interaction    | `tests/integration/test_mul_pipeline.py`   |
| DIV instruction pipeline interaction   | `tests/integration/test_div_pipeline.py`   |
| C extension pipeline mixing            | `tests/integration/test_compressed_mix.py`  |
| Interrupt during long instruction (DIV)| `tests/integration/test_interrupt_div.py`  |
| Full pipeline bubble insertion         | `tests/integration/test_full_bubbles.py`   |

### 2.3 System Tests (ISA compliance)

Full programs loaded as hex files, verified against a golden model (Spike
or the pyCircuit C++ simulator in `--reference` mode).

| Test Source            | Test File / Suite                    | Count |
| ---------------------- | ------------------------------------ | ----- |
| RISCOF RV32I           | `tests/system/riscof/rv32i/`         | ~1000 |
| RISCOF RV32M           | `tests/system/riscof/rv32m/`         | ~200  |
| RISCOF RV32C           | `tests/system/riscof/rv32c/`         | ~500  |
| RISCOF Privilege       | `tests/system/riscof/privilege/`     | ~300  |
| Custom random programs | `tests/system/random/`               | ~500  |
| Dhrystone benchmark    | `tests/system/dhrystone/`            | 1     |
| CoreMark benchmark     | `tests/system/coremark/`             | 1     |
| Custom interrupt tests | `tests/system/interrupt/`            | ~100  |

---

## 3. Test Types

### 3.1 Directed Tests

Hand-crafted instruction sequences targeting specific functionality:

```python
@testbench
def test_addi_forward(self):
    """Back-to-back ADDI with RAW dependency: checks forwarding."""
    prog = [
        0x00100093,  # addi x1, x0, 1
        0x00108093,  # addi x1, x1, 1   # RAW on x1 → must forward
        0x00108093,  # addi x1, x1, 1   # RAW on x1 → must forward
    ]
    self.load_hex(prog)
    self.run_cycles(10)
    assert self.regfile[1] == 3, f"Expected x1=3, got {self.regfile[1]}"
```

### 3.2 Random Tests (Constrained-Random)

Random instruction sequences generated with constraints:

- **Register liveness tracking**: Ensure source registers are written before use.
- **Branch target alignment**: All targets are 2-byte aligned (C ext) or
  4-byte aligned (32-bit).
- **Memory address bounds**: Stay within allocated memory range.
- **No infinite loops**: Max instruction count enforced.
- **Coverage-based tuning**: Bias generation toward uncovered scenarios.

```python
class RandomTestGenerator:
    def generate(self, num_instructions: int, seed: int) -> list[int]:
        rng = random.Random(seed)
        reg_status = {i: 'valid' for i in range(32)}  # track which regs are written
        prog = []
        for _ in range(num_instructions):
            inst = self._generate_inst(rng, reg_status)
            prog.append(inst)
            self._update_reg_status(inst, reg_status)
        prog.append(0x0000006F)  # JAL x0,0 (infinite loop to end)
        return prog
```

### 3.3 RISCOF Compliance Suite

RISCOF (RISC-V Compatibility Framework) provides a standardized set of
assembly tests for each instruction and each extension. The flow:

```
 ┌──────────────────┐    ┌──────────────┐    ┌──────────────┐
 │ RISCOF Test      │    │ DUT Model    │    │ Reference    │
 │ Database         │    │ Plugin        │    │ Model (Spike) │
 │ (YAML)            │    │              │    │              │
 │                   │    │              │    │              │
 │ rv32i_m/add-01.S │───►│ pyCircuit     │    │ Spike        │
 │ rv32i_m/sub-01.S │    │ simulator     │    │              │
 │ ...               │    │              │    │              │
 └──────────────────┘    └──────┬───────┘    └──────┬───────┘
                                │                   │
                                │  Signature file   │  Signature file
                                │  (register dump)  │  (register dump)
                                │                   │
                                └─────────┬─────────┘
                                          │
                                   ┌──────┴──────┐
                                   │  RISCOF     │
                                   │  Comparator │
                                   └──────┬──────┘
                                          │
                                    ┌─────┴─────┐
                                    │  PASS/FAIL │
                                    │  Report    │
                                    └───────────┘
```

RISCOF configuration (`config.ini`):

```ini
[RISCOF]
ReferencePlugin=spike
DUTPlugin=pycircuit

[pycircuit]
pluginpath=scripts/riscof_pycircuit.py
sim_cmd=python -m pycircuit run rtl/lunahan_core.py --sim --hex {hex_file}
```

---

## 4. Coverage Goals

### 4.1 Instruction Coverage: **100%**

Every defined instruction in RV32I, M, and C must be exercised at least once.

| Extension | Total Instructions | Covered | Target |
| --------- | ------------------ | ------- | ------ |
| RV32I     | 40                 | 40      | 100%   |
| M         | 8                  | 8       | 100%   |
| C         | ~27                | 27      | 100%   |
| **Total** | **75**             | **75**  | **100%** |

### 4.2 Code Coverage: **≥ 95%**

Measured via Python `coverage.py` on the pyCircuit simulation model.

| Coverage Metric | Target |
| --------------- | ------ |
| Line coverage   | ≥ 95%  |
| Branch coverage | ≥ 90%  |
| Toggle coverage | ≥ 90%  |

### 4.3 Functional Coverage (Cross-Coverage): **≥ 90%**

Measured via coverage bins defined in the testbench.

| Coverage Group           | Bins                                              | Target |
| ------------------------ | ------------------------------------------------- | ------ |
| ALU operations           | All 21 ops × operand classes (zero, pos, neg, max, min) | 100% |
| Forwarding paths         | EX→ID, MEM→ID, WB→ID × 2 read ports               | 100%   |
| Hazard types             | RAW (all distances), load-use, branch, control    | 100%   |
| Branch outcomes          | Taken/not-taken × correctly/mispredicted          | 100%   |
| BTB states               | 4 bimodal states, hit/miss, update                | 100%   |
| Load/store widths        | Byte, half, word × signed/unsigned                | 100%   |
| Cache states             | Hit, miss, dirty writeback, line fill             | 100%   |
| Exception types          | All 12 exception causes                           | 100%   |
| Interrupt scenarios      | Soft/timer/ext × during various pipeline stages   | 100%   |
| CSR access patterns      | R/W, set, clear × all implemented CSRs            | 100%   |
| Compressed expansion     | All C instruction types                           | 100%   |
| M extension states       | MUL in progress, DIV in progress, results correct | 100%   |

### 4.4 Corner Cases

| Category        | Examples                                              |
| --------------- | ----------------------------------------------------- |
| Arithmetic      | INT_MIN / -1, 0 × MAX, MAX + 1, shift by 31           |
| Memory          | Word access at last valid address, unaligned access   |
| Branch          | Branch to self, backward branch at page boundary      |
| Pipeline        | Full pipeline stall, back-to-back exceptions          |
| CSR             | Write to read-only fields, mstatus.MIE toggles        |
| Interrupts      | Nested interrupt attempt (MIE=0), interrupt during MRET|
| Reset           | Reset mid-instruction, reset during cache miss        |

---

## 5. Testbench Architecture

### 5.1 pyCircuit Testbench Framework

```python
from pycircuit.core import testbench, CycleAwareTb, Assert, Assume
from pycircuit.sim import Simulator

@testbench
class LunahanTB(CycleAwareTb):
    """Top-level testbench for lunahan_v1 core."""

    dut: LunahanCore

    def configure(self):
        self.max_cycles = 100000
        self.timeout_action = "fail"

    def init(self):
        self.dut.reset_n.value = 0
        self.cycle_count = 0
        self.expected_regfile = [0] * 32
        self.expected_memory = {}
        self.golden_model = GoldenModel()  # ISA-level reference

    @clock_edge(posedge=True)
    def step(self):
        self.cycle_count += 1
        if self.cycle_count == 5:
            self.dut.reset_n.value = 1

    def check(self):
        # Compare register state against golden model
        for i in range(32):
            if self.dut.regfile_write_en and self.dut.regfile_wr_addr == i:
                expected = self.golden_model.regfile[i]
                actual = self.dut.regfile_wr_data
                assert expected == actual, \
                    f"x{i} mismatch: expected {expected:08x}, actual {actual:08x}"
```

### 5.2 Checkers

| Checker                    | Description                                              |
| -------------------------- | -------------------------------------------------------- |
| Register state checker     | Compares DUT register writes against golden model        |
| Memory state checker       | Compares DUT memory writes (SW) against golden model     |
| PC trace checker           | Verifies correct instruction flow (no stuck PC)          |
| Illegal instruction checker| Verifies illegal instructions raise correct exception    |
| CSR checker                | Verifies CSR state transitions are per spec              |
| Pipeline invariant checker | Asserts no structural hazards (e.g., dual write to RF)   |
| Deadlock checker           | Fails if PC does not change for >1000 cycles             |
| AXI protocol checker       | Verifies AXI4-Lite handshake compliance                  |

### 5.3 Golden Model (Reference)

The golden model is an instruction-level emulator (similar to Spike but
Python-native) that tracks the architectural state:

```python
class GoldenModel:
    """ISA-level golden reference for comparison."""

    def __init__(self):
        self.regfile = [0] * 32
        self.pc = 0
        self.memory = bytearray(2 * 1024 * 1024)  # 2 MB
        self.csr = {name: 0 for name in CSR_NAMES}

    def step(self, inst: int):
        """Execute one 32-bit instruction and update state."""
        opcode = inst & 0x7F
        rd = (inst >> 7) & 0x1F
        funct3 = (inst >> 12) & 0x7
        rs1 = (inst >> 15) & 0x1F
        rs2 = (inst >> 20) & 0x1F
        funct7 = (inst >> 25) & 0x7F
        # ... decode and execute ...
```

---

## 6. Tools

| Tool          | Version | Purpose                                  |
| ------------- | ------- | ---------------------------------------- |
| pytest        | ≥ 7.0   | Unit and integration test runner         |
| pytest-cov    | ≥ 4.0   | Code coverage measurement                |
| RISCOF        | ≥ 1.0   | ISA compliance suite runner              |
| Spike         | latest  | RISC-V golden reference model            |
| Verilator     | ≥ 5.0   | RTL lint + co-simulation (post-synthesis)|
| cocotb        | ≥ 1.8   | (Optional) Python-based RTL verification |
| coverage.py   | ≥ 7.0   | Python code coverage tool                |
| hypothesis    | ≥ 6.0   | Property-based testing for random tests  |

---

## 7. Regression Pipeline

```
 ┌──────────────────┐
 │  Git Push / PR   │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │  Lint Check      │
 │  (pyCircuit lint) │
 │  (Verilator lint) │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │  Unit Tests      │
 │  (~200 tests)    │
 │  Time: <30 sec   │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │  Integration     │
 │  Tests (~50)     │
 │  Time: <2 min    │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │  Random Tests    │
 │  (100 seeds,     │
 │   1000 inst each)│
 │  Time: <10 min   │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │  RISCOF Suite    │
 │  (~2000 tests)   │
 │  Time: <30 min   │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │  Coverage Report │
 │  (≥95% line)     │
 │  (100% inst)     │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │  Gate-Level Sim  │
 │  (Verilator)     │
 │  Smoke test      │
 │  Time: <5 min    │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │  Signoff / Merge │
 └──────────────────┘
```

### Regression Script

```bash
#!/bin/bash
# scripts/regression.sh — Full regression suite

set -e

echo "=== Lint ==="
python -m pycircuit lint rtl/lunahan_core.py
verilator --lint-only rtl/lunahan_core.v 2>&1 | tee build/reports/lint.log

echo "=== Unit Tests ==="
pytest tests/unit/ -v --cov=rtl --cov-report=html:build/reports/coverage_unit \
    --junitxml=build/reports/junit_unit.xml

echo "=== Integration Tests ==="
pytest tests/integration/ -v --junitxml=build/reports/junit_integration.xml

echo "=== Random Tests ==="
python scripts/run_random_tests.py --seeds 100 --insts 1000 \
    --report build/reports/random_test_report.json

echo "=== RISCOF ==="
riscof run --config=scripts/riscof_config.ini \
    --suite=tests/system/riscof/ \
    --output=build/reports/riscof/

echo "=== Coverage Check ==="
coverage report --fail-under=95

echo "=== Gate-Level Smoke Test ==="
verilator --cc build/core_synth.v --top-module lunahan_core --exe \
    sim/gls_tb.cpp && \
    make -C obj_dir -f Vlunahan_core.mk && \
    ./obj_dir/Vlunahan_core 2>&1 | tee build/reports/gls_smoke.log

echo "=== Regression Complete ==="
```
