# Microarchitecture — lunahan_v1

## 1. Pipeline Overview

lunahan_v1 implements a classic 5-stage in-order RISC pipeline, single-issue,
with Harvard split caches and a simple bimodal branch predictor.

```
                    ┌─────────────┐
                    │   BTB (64)  │
                    │ + Bimodal   │
                    │   Predict   │
                    └──────┬──────┘
                           │
    ┌──────┐    ┌──────┐    │    ┌──────┐    ┌──────┐    ┌──────┐
    │  IF  │───►│  ID  │───►│    │  EX  │───►│ MEM  │───►│  WB  │
    │      │    │      │    │    │      │    │      │    │      │
    │ ┌──┐ │    │ ┌──┐ │    │    │ ┌──┐ │    │ ┌──┐ │    │ ┌──┐ │
    │ │PC│ │    │ │RF│ │    │    │ │AL│ │    │ │LS│ │    │ │RF│ │
    │ │  │ │    │ │R │◄┼────┼────┼─┤U │ │    │ │U │◄┼────┼─┤W │ │
    │ │+4│ │    │ │D │ │    │    │ │  │ │    │ │  │ │    │ │R │ │
    │ └──┘ │    │ └──┘ │    │    │ │ M│ │    │ └──┘ │    │ └──┘ │
    │      │    │      │    │    │ │ U│ │    │      │    │      │
    │ ICache│    │Decode│    │    │ │ L│ │    │DCache│    │Write-│
    │ 4 KB │    │+CExp │    │    │ │  │ │    │ 4 KB │    │ back │
    │      │    │      │    │    │ │DI│ │    │      │    │      │
    │      │    │Hazard│    │    │ │ V│ │    │      │    │      │
    │      │    │Detect│    │    │ └──┘ │    │      │    │      │
    │      │    │      │    │    │      │    │      │    │      │
    │Branch│    │Branch│    │    │Branch│    │      │    │      │
    │Pred  │    │Detect│    │    │Resolv│    │      │    │      │
    │      │    │      │    │    │+Flush│    │      │    │      │
    └──┬───┘    └──┬───┘    │    └──┬───┘    └──┬───┘    └──┬───┘
       │           │         │       │           │           │
       ▼           ▼         ▼       ▼           ▼           ▼
    [Pipeline registers between stages carry control + data signals]
```

### Pipeline Register Contents

| Signal Group  | IF→ID               | ID→EX                  | EX→MEM                  | MEM→WB               |
| ------------- | ------------------- | ---------------------- | ----------------------- | -------------------- |
| PC            | pc, pc+4            | pc, pc+4               | pc, pc+4                | pc, pc+4             |
| Instruction   | raw instruction     | decoded ctrl signals   | ALU result              | memory read data     |
| Register idx  | —                   | rs1_idx, rs2_idx       | rd_idx                  | rd_idx               |
| Operands      | —                   | rs1_val, rs2_val       | rs2_val (for store)    | —                    |
| Immediate     | —                   | imm                    | —                       | —                    |
| Ctrl signals  | —                   | alu_op, mem_rw, wb_en  | mem_rw, wb_en           | wb_en                |
| Valid         | inst_valid          | inst_valid             | inst_valid              | inst_valid           |
| Exception     | —                   | —                      | exception info          | —                    |

---

## 2. IF Stage — Instruction Fetch

```
 ┌─────────────────────────────────────────────────────────────┐
 │                       IF STAGE                               │
 │                                                              │
 │  ┌───────────┐        ┌──────────────┐        ┌───────────┐ │
 │  │           │        │              │        │           │ │
 │  │    PC     │───────►│  I-Cache     │───────►│ Pipeline  │ │
 │  │ Generator │        │  (4 KB, DM)  │        │ Register  │ │
 │  │           │        │              │        │ (IF → ID) │ │
 │  │ ┌───────┐ │        │  ┌────────┐  │        │           │ │
 │  │ │ +4    │ │        │  │ Tag    │  │        │ pc        │ │
 │  │ │ adder │ │        │  │ SRAM   │  │        │ inst      │ │
 │  │ └───────┘ │        │  │ (256x1)│  │        │ valid     │ │
 │  │ ┌───────┐ │        │  └────────┘  │        │           │ │
 │  │ │Branch │ │        │  ┌────────┐  │        │           │ │
 │  │ │Target │ │        │  │ Data   │  │        │           │ │
 │  │ │ Mux   │ │        │  │ SRAM   │  │        │           │ │
 │  │ └───────┘ │        │  │ (256x1)│  │        │           │ │
 │  │           │        │  └────────┘  │        │           │ │
 │  └───────────┘        └──────────────┘        └───────────┘ │
 │                                                              │
 │  Control inputs:                                              │
 │    - stall_if:     Stall due to ID hazard or memory wait      │
 │    - flush_if:     Flush due to branch mispredict or trap     │
 │    - branch_taken: Redirect PC from EX stage                  │
 │    - branch_target: Target PC from EX stage                   │
 └─────────────────────────────────────────────────────────────┘
```

### PC Generation Logic

```
next_pc =
    | exception_taken   → mtvec_base           // trap
    | mret_executed     → mepc                 // mret
    | branch_taken      → branch_target        // taken branch
    | stall_if          → pc                    // stall
    else                → pc + 4                // sequential
```

### Branch Prediction — BTB (Branch Target Buffer)

- **Size**: 64 entries, direct-mapped
- **Tag**: pc[11:6] (6 bits)
- **Data per entry**: {tag[5:0], target_pc[31:2], valid, bimodal_counter[1:0]}
- **Bimodal predictor**: 2-bit saturating counter per entry
  - 00, 01: Predict not-taken (weak, strong)
  - 10, 11: Predict taken (weak, strong)
  - Update on branch resolution in EX stage
- **Prediction**: At fetch, if BTB hit AND valid AND counter ≥ 10, predict
  taken and redirect PC to cached target.

### I-Cache (Instruction Cache)

- **Capacity**: 4 KB (4096 bytes)
- **Organization**: Direct-mapped, 256 blocks × 16 bytes/block
- **Line size**: 16 bytes (4 instructions per line)
- **Write policy**: Not applicable (read-only; code is loaded before execution)
- **Hit latency**: 1 cycle (combinational tag compare + data read)
- **Miss penalty**: Variable (stall until AXI4-Lite read completes), typically
  4–16 cycles depending on memory latency
- **Tag SRAM**: 256 entries × 21 bits (tag[31:11])
- **Data SRAM**: 256 entries × 128 bits (16 bytes per line, byte-write masked on invalidate)

On miss, the IF stage asserts `stall_if` and issues an AXI4-Lite read
request for the missing 16-byte line. When the line returns, it is written
into the cache, and the instruction is re-fetched.

### PC Width

PC is 32 bits, byte-addressable. The bottom 2 bits of the program counter
are always 0 for 32-bit instructions. For 16-bit compressed instructions,
the bottom bit may be 0 or 2 (4-byte boundaries); the IF stage fetches
32 bits always and the ID stage handles the half-word selection.

---

## 3. ID Stage — Instruction Decode & Register Read

```
 ┌─────────────────────────────────────────────────────────────┐
 │                       ID STAGE                               │
 │                                                              │
 │  ┌───────────┐   ┌──────────────┐   ┌──────────────┐       │
 │  │           │   │              │   │              │       │
 │  │ Compressed│──►│ Instruction  │──►│  Control     │       │
 │  │ Expander  │   │ Decoder      │   │  Signal Gen  │       │
 │  │ (C→32bit)│   │              │   │              │       │
 │  └───────────┘   └──────────────┘   └──────────────┘       │
 │                                                              │
 │                         ┌──────────────┐                     │
 │                         │  Register    │                     │
 │    rs1_idx ────────────►│  File        │────► rs1_val        │
 │    rs2_idx ────────────►│  Read Port   │────► rs2_val        │
 │                         │  2R/1W       │                     │
 │                         └──────────────┘                     │
 │                                                              │
 │  ┌──────────────────────────────────────────────────┐       │
 │  │           Hazard Detection Unit                   │       │
 │  │                                                   │       │
 │  │  RAW check:  if (ex_rd == id_rs1) → forward       │       │
 │  │              if (ex_rd == id_rs2) → forward       │       │
 │  │              if (mem_rd == id_rs1 && !ex_forward) │       │
 │  │               → forward from MEM                  │       │
 │  │                                                   │       │
 │  │  Load hazard: if (ex_rd == id_rs1 && ex_is_load)  │       │
 │  │               → stall 1 cycle                     │       │
 │  │  same with rs2                                      │       │
 │  │                                                   │       │
 │  │  Branch detect: opcode == BRANCH/JAL/JALR          │       │
 │  │                → signal to IF for prediction       │       │
 │  └──────────────────────────────────────────────────┘       │
 │                                                              │
 │  Output to ID→EX pipeline register:                          │
 │    pc, pc+4, inst, rs1_val, rs2_val, imm,                    │
 │    alu_op[4:0], alu_src1_sel, alu_src2_sel,                  │
 │    mem_rw, mem_size[1:0], mem_sign_ext,                      │
 │    wb_en, rd_idx, csr_cmd, exception_info                    │
 └─────────────────────────────────────────────────────────────┘
```

### Compressed Instruction Expander

Detects 16-bit compressed instructions by checking `inst[1:0] != 11`.
Maps each C instruction to its 32-bit canonical form before the main
decoder sees it. Implemented as a combinational lookup table.

Key expansion mappings:

```
C.ADDI4SPN → ADDI rd', x2, nzuimm
C.LW       → LW   rd', uimm(rs1')
C.SW       → SW   rs2', uimm(rs1')
C.ADDI     → ADDI rd/rs1, rd/rs1, nzimm
C.LI       → ADDI rd, x0, nzimm
C.LUI      → LUI  rd, nzimm
C.J        → JAL  x0, offset
C.JAL      → JAL  x1, offset
C.BEQZ     → BEQ  rs1', x0, offset
C.BNEZ     → BNE  rs1', x0, offset
C.LWSP     → LW   rd, uimm(x2)
C.SWSP     → SW   rs2, uimm(x2)
C.JR       → JALR x0, rs1, 0
C.MV       → ADD  rd, x0, rs2
C.ADD      → ADD  rd/rs1, rd/rs1, rs2
C.EBREAK   → EBREAK
C.NOP      → ADDI x0, x0, 0
```

### Main Decoder

Decodes the 7-bit opcode, 3-bit funct3, and 7-bit funct7 fields into control
signals.

| Control Signal | Width | Description                            |
| -------------- | ----- | -------------------------------------- |
| alu_op         | 5     | ALU operation select                   |
| alu_src1_sel   | 2     | 0=rs1, 1=pc, 2=0, 3=csr              |
| alu_src2_sel   | 2     | 0=rs2, 1=imm, 2=4, 3=csr             |
| mem_rw         | 2     | 0=none, 1=read, 2=write               |
| mem_size       | 2     | 0=byte, 1=half, 2=word                |
| mem_sign_ext   | 1     | 1=sign-extend load data                |
| wb_en          | 1     | Enable register write-back             |
| wb_src_sel     | 2     | 0=alu, 1=mem, 2=pc+4, 3=csr           |
| csr_cmd        | 3     | CSR operation type (if CSR instruction)|
| branch_op      | 3     | Branch comparison type                 |
| is_branch      | 1     | This instruction is a branch/jump      |
| is_mul_div     | 1     | M-extension operation                  |
| mul_div_op     | 3     | Specific M operation                   |
| is_system      | 1     | ECALL/EBREAK/CSR/MRET                 |
| excep_illegal  | 1     | Raise illegal instruction exception    |

### Hazard Detection Unit

**RAW (Read-After-Write) Forwarding**:
- EX hazard: result available at end of EX, forwarded to ID for next
  instruction. No stall needed.
- MEM hazard: if EX stage is a load, the data arrives at end of MEM.
  A 1-cycle stall is inserted (pipeline bubble in EX).

**Control Hazard (Branch/Jump) resolution**:
- Unconditional jumps (JAL, JALR): Resolved in ID (target computed, PC
  redirected). 1-cycle penalty if not predicted.
- Conditional branches: Predicted in IF (BTB). Resolved in EX.
  Mispredict flushes IF+ID. 2-cycle penalty on mispredict.

**Structural Hazard**: None by design — separate I/D caches, single-issue.

**Stall Conditions**:
1. I-Cache miss (IF stalled until line returns).
2. D-Cache miss (MEM stalled until line returns).
3. Load-use hazard (ID stalled 1 cycle).
4. Multi-cycle MUL/DIV in EX (ID stalled until EX completes).

---

## 4. EX Stage — Execute

```
 ┌─────────────────────────────────────────────────────────────┐
 │                       EX STAGE                               │
 │                                                              │
 │  ┌───────────┐   ┌──────────────┐   ┌──────────────┐       │
 │  │ Operand   │   │              │   │              │       │
 │  │ Select    │──►│  ALU         │──►│  Result      │       │
 │  │ (src1/2)  │   │  (32-bit)    │   │  Mux         │       │
 │  └───────────┘   └──────────────┘   └──────────────┘       │
 │                                                              │
 │  ┌──────────────────────────────────┐                       │
 │  │  Multiplier/Divider Unit         │                       │
 │  │                                  │                       │
 │  │  RADIX-4 BOOTH                   │                       │
 │  │  ┌────────────┐                  │                       │
 │  │  │ Booth      │    5 cycles      │                       │
 │  │  │ Encoder    │──────────────────►│  result[31:0]        │
 │  │  │ + Wallace  │    (mul)          │  (mul/div)           │
 │  │  │ Tree       │                  │                       │
 │  │  └────────────┘                  │                       │
 │  │                                  │                       │
 │  │  RESTORING DIVISION              │                       │
 │  │  ┌────────────┐                  │                       │
 │  │  │ Shift-sub  │   33 cycles      │                       │
 │  │  │ Iterative  │──────────────────►│  quotient, remainder │
 │  │  └────────────┘    (div)         │                       │
 │  └──────────────────────────────────┘                       │
 │                                                              │
 │  ┌──────────────┐   ┌──────────────┐                        │
 │  │ Branch       │   │  CSR Unit    │                        │
 │  │ Resolution   │   │              │                        │
 │  │              │   │ Read/Write   │                        │
 │  │ Compare rs1  │   │ CSRs         │                        │
 │  │ vs rs2       │   │              │                        │
 │  └──────────────┘   └──────────────┘                        │
 │                                                              │
 │  Output to EX→MEM pipeline register:                         │
 │    pc, pc+4, alu_result[31:0], rs2_val[31:0],               │
 │    mem_rw, mem_size, mem_sign_ext, rd_idx, wb_en,           │
 │    wb_src_sel, exception_info                                │
 └─────────────────────────────────────────────────────────────┘
```

### ALU Operations

| alu_op[4:0] | Operation             | Description                         |
| ----------- | --------------------- | ----------------------------------- |
| 0x00        | ADD                   | rs1 + rs2 (or rs1 + imm)            |
| 0x01        | SUB                   | rs1 - rs2                           |
| 0x02        | SLL                   | rs1 << rs2[4:0]                     |
| 0x03        | SLT                   | (rs1 < rs2 signed) ? 1 : 0         |
| 0x04        | SLTU                  | (rs1 < rs2 unsigned) ? 1 : 0       |
| 0x05        | XOR                   | rs1 ^ rs2                           |
| 0x06        | SRL                   | rs1 >> rs2[4:0] (logical)          |
| 0x07        | SRA                   | rs1 >> rs2[4:0] (arithmetic)       |
| 0x08        | OR                    | rs1 | rs2                            |
| 0x09        | AND                   | rs1 & rs2                           |
| 0x0A        | LUI                   | imm (passed through)                |
| 0x0B        | AUIPC                 | pc + imm (via src1=pc, src2=imm)    |
| 0x0C        | JAL/JALR (link)      | pc + 4 (for link register)         |
| 0x0D        | BEQ                   | rs1 == rs2                          |
| 0x0E        | BNE                   | rs1 != rs2                          |
| 0x0F        | BLT                   | rs1 < rs2 signed                    |
| 0x10        | BGE                   | rs1 >= rs2 signed                   |
| 0x11        | BLTU                  | rs1 < rs2 unsigned                  |
| 0x12        | BGEU                  | rs1 >= rs2 unsigned                 |
| 0x13        | PASS_RS2             | Pass through rs2 (for store)        |
| 0x14        | CSR_READ             | CSR read data                       |
| 0x15        | CSR_WRITE            | CSR write data (rs1 | imm)         |

### Multiplier (M-extension)

- **Algorithm**: Radix-4 Booth encoding with Wallace tree reduction
- **Latency**: 5 cycles (pipelined stages: booth_encode → partial_products →
  wallace_4to2 → wallace_2to1 → final_add)
- **Result**: Lower 32 bits of 64-bit product
- **Special cases**: MULH, MULHSU, MULHU return upper 32 bits
- **Integration**: When MUL is issued, EX stage asserts `ex_stall` and the
  ID stage stalls until the multiplier completes. Compressed instructions
  in IF continue to be decoded but stall propagates.

### Divider (M-extension)

- **Algorithm**: Restoring division (shift-subtract iterative)
- **Latency**: 33 cycles (1 cycle per bit + setup/teardown)
- **Signed division**: Convert to unsigned, operate, fix sign at end
- **Edge cases**: Division by zero → return all-1s (-1 for signed, max for unsigned);
  Signed overflow (INT_MIN/-1) → return INT_MIN
- **Integration**: Same stall mechanism as multiplier; ID stalls for full 33 cycles.

### Branch Resolution

Branch condition is evaluated in EX using ALU. If the branch is:
- **Correctly predicted**: No penalty (prediction matched actual outcome).
- **Mispredicted**: Assert `flush_if` and `flush_id`; redirect PC to
  correct target (pc+4 for not-taken, pc+imm for taken). Update BTB
  entry with actual outcome.

Forwarding from EX and MEM stages is available to the ALU for branch
comparison inputs (rs1, rs2) to avoid stalls on data-dependent branches.

### CSR Unit

Executes CSR instructions (CSRRW, CSRRS, CSRRC, and immediate forms).
- **Read**: Mux out the selected CSR value (or old CSR value before write).
- **Write**: Apply the specified operation (r/w, set bits, clear bits).
- **Atomicity**: Read-modify-write in one cycle (same EX stage).
- **Illegal CSR access**: Any CSR access to unsupported CSR numbers raises
  illegal instruction exception.

---

## 5. MEM Stage — Memory Access

```
 ┌─────────────────────────────────────────────────────────────┐
 │                      MEM STAGE                               │
 │                                                              │
 │  ┌──────────────────────────────────────┐                   │
 │  │        Load/Store Unit (LSU)          │                   │
 │  │                                       │                   │
 │  │  ┌──────────┐    ┌──────────────┐    │                   │
 │  │  │ Address  │    │  D-Cache     │    │                   │
 │  │  │ Gen      │───►│  (4 KB, DM)  │    │                   │
 │  │  │          │    │              │    │                   │
 │  │  │ rs1+imm  │    │  ┌────────┐  │    │                   │
 │  │  └──────────┘    │  │ Tag    │  │    │                   │
 │  │                  │  │ SRAM   │  │    │                   │
 │  │                  │  │ (256x1)│  │    │                   │
 │  │                  │  └────────┘  │    │                   │
 │  │                  │  ┌────────┐  │    │                   │
 │  │                  │  │ Data   │  │    │                   │
 │  │                  │  │ SRAM   │  │    │                   │
 │  │                  │  │ (256x1)│  │    │                   │
 │  │                  │  └────────┘  │    │                   │
 │  │                  └──────────────┘    │                   │
 │  │                                       │                   │
 │  │  ┌──────────────────────────┐        │                   │
 │  │  │ Alignment Checker        │        │                   │
 │  │  │ + Misaligned Exception   │        │                   │
 │  │  └──────────────────────────┘        │                   │
 │  │                                       │                   │
 │  │  ┌──────────────────────────┐        │                   │
 │  │  │ Data Alignment           │        │                   │
 │  │  │ (byte/half/word shift)   │        │                   │
 │  │  │ + Sign Extension         │        │                   │
 │  │  └──────────────────────────┘        │                   │
 │  └──────────────────────────────────────┘                   │
 │                                                              │
 │  Output to MEM→WB pipeline register:                         │
 │    pc, pc+4, alu_result[31:0], mem_read_data[31:0],         │
 │    rd_idx[4:0], wb_en, wb_src_sel, exception_info            │
 └─────────────────────────────────────────────────────────────┘
```

### D-Cache (Data Cache)

- **Capacity**: 4 KB (4096 bytes)
- **Organization**: Direct-mapped, 256 blocks × 16 bytes/block
- **Line size**: 16 bytes
- **Write policy**: Write-back with write-allocate
- **Hit latency**: 1 cycle (load) or 1 cycle (store, data written on hit)
- **Miss handling**: On read/write miss, stall the pipeline and issue an
  AXI4-Lite transaction to fetch the line from main memory. On write-back,
  dirty line is written to memory before loading the new line.
- **Dirty bit**: 1 bit per line. Set when store hits. Cleared when line
  is cleanly loaded.
- **Tag SRAM**: 256 entries × {tag[31:11], valid, dirty} = 23 bits
- **Data SRAM**: 256 entries × 128 bits (16 bytes, byte-writable via mask)

### Load/Store Addressing

```
load_address  = alu_result    (computed in EX: rs1 + imm)
store_address = alu_result    (computed in EX: rs1 + imm)
```

Load instructions: LW (32-bit), LH (16-bit signed), LHU (16-bit unsigned),
LB (8-bit signed), LBU (8-bit unsigned).

Store instructions: SW (32-bit), SH (16-bit), SB (8-bit).

### Alignment Checking

| Instruction | Required Alignment | Misaligned Behavior                      |
| ----------- | ------------------ | ---------------------------------------- |
| LW          | 4-byte             | Raise load address misaligned exception  |
| LH / LHU    | 2-byte             | Raise load address misaligned exception  |
| LB / LBU    | 1-byte (always)    | Never misaligned                         |
| SW          | 4-byte             | Raise store address misaligned exception |
| SH          | 2-byte             | Raise store address misaligned exception |
| SB          | 1-byte (always)    | Never misaligned                         |

lunahan_v1 does NOT support misaligned loads/stores in hardware. Any
misaligned access raises a precise exception. Software must handle
misaligned access via trap handler.

---

## 6. WB Stage — Write-Back

```
 ┌─────────────────────────────────────────────────────────────┐
 │                       WB STAGE                               │
 │                                                              │
 │  ┌──────────────────────────────────────┐                   │
 │  │         Write-Back Unit               │                   │
 │  │                                       │                   │
 │  │  wb_src_sel:                          │                   │
 │  │    0 → alu_result (R-type, I-type)    │                   │
 │  │    1 → mem_read_data (load)           │────► regfile      │
 │  │    2 → pc+4 (JAL/JALR link)           │     write port   │
 │  │    3 → csr_read_data                  │                   │
 │  │                                       │                   │
 │  └──────────────────────────────────────┘                   │
 │                                                              │
 │  ┌──────────────────────────────────────┐                   │
 │  │  Register File Write                  │                   │
 │  │                                       │                   │
 │  │  if (wb_en && rd_idx != 0) {          │                   │
 │  │    regfile[rd_idx] = wb_data;         │                   │
 │  │  }                                    │                   │
 │  └──────────────────────────────────────┘                   │
 │                                                              │
 │  ┌──────────────────────────────────────┐                   │
 │  │  Exception & Interrupt Commit         │                   │
 │  │                                       │                   │
 │  │  Write x0 is silently ignored          │                   │
 │  │  SCALL/EBREAK exceptions committed    │                   │
 │  │  Interrupts sampled at WB boundary    │                   │
 │  └──────────────────────────────────────┘                   │
 └─────────────────────────────────────────────────────────────┘
```

---

## 7. Pipeline Control

### Stalling

The pipeline is stalled by disabling the PC update and inserting a bubble
(NOP) into the next pipeline register.

| Stall Condition              | Stages Stalled | Duration        |
| ---------------------------- | -------------- | --------------- |
| I-Cache miss                 | IF             | Until line returns |
| D-Cache miss                 | IF, ID, EX, MEM | Until line returns |
| Load-use hazard              | IF, ID         | 1 cycle          |
| MUL in progress              | IF, ID         | 4 cycles (MUL is 5 total) |
| DIV in progress              | IF, ID         | 32 cycles (DIV is 33 total) |
| CSR read-after-write hazard  | IF, ID         | 1 cycle          |

### Forwarding

Forwarding multiplexers in the ID stage select among:
- `regfile[rs]` (no hazard)
- `ex_result` (EX forwarding)
- `mem_result` (MEM forwarding)
- `wb_result` (WB forwarding — only needed if write port is late)

Priority: EX > MEM > WB > regfile. This means if data is available in
multiple pipeline stages, use the most recent one.

| Producer Stage | Consumer Stage | Forward Path  | Stall Required? |
| -------------- | -------------- | ------------- | --------------- |
| EX (ALU op)    | ID (next inst) | ex_result → id | No              |
| MEM (load)     | ID (next inst) | mem_result → id | 1 cycle (load-use) |
| WB             | ID             | wb_result → id | No              |

### Flushing

When a flush is signaled:
- Flushed pipeline registers are invalidated (`inst_valid = 0`).
- The IF stage PC is redirected to the exception handler or branch target.
- Any in-flight write-back from the flushed instruction is suppressed.

**Flush Conditions**:
1. Branch mispredict (partial flush: IF + ID stages only).
2. Exception taken (full flush: IF + ID stages; EX/MEM/WB complete or
   are suppressed depending on exception source).
3. MRET executed (full flush: IF + ID; jump to mepc).
4. ECALL/EBREAK (flows through to WB; exception is committed at WB).

### Exception Handling in the Pipeline

Exceptions are detected in specific stages:

| Exception                          | Detected In | Action                                   |
| ---------------------------------- | ----------- | ---------------------------------------- |
| Instruction access fault           | IF          | Flag in IF→ID; commit in WB               |
| Illegal instruction                | ID          | Flag in ID→EX; commit in WB               |
| ECALL / EBREAK                    | ID/EX       | Flag; commit in WB                        |
| Load address misaligned            | MEM         | Flag in MEM→WB; commit in WB              |
| Load access fault                  | MEM         | Flag in MEM→WB; commit in WB              |
| Store address misaligned           | MEM         | Flag; commit in WB; store not performed    |
| Store access fault                 | MEM         | Flag; commit in WB; store not performed    |

When an exception is committed in WB, all earlier instructions (already
in pipeline) are allowed to complete. The faulting instruction's PC is
saved to mepc, and the pipeline is redirected to the trap handler.

---

## 8. Memory System

### Harvard Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     MEMORY HIERARCHY                         │
│                                                              │
│  ┌──────────────┐            ┌──────────────┐               │
│  │  I-Cache     │            │  D-Cache     │               │
│  │  4 KB, DM    │            │  4 KB, DM    │               │
│  │  16B/line    │            │  16B/line    │               │
│  │  Read-only   │            │  Write-back  │               │
│  └──────┬───────┘            └──────┬───────┘               │
│         │                           │                        │
│         │     AXI4-Lite Bus         │                        │
│         └───────────┬───────────────┘                        │
│                     │                                        │
│              ┌──────┴───────┐                                │
│              │  AXI4-Lite   │                                │
│              │  Interconnect│                                │
│              └──────┬───────┘                                │
│                     │                                        │
│         ┌───────────┼──────────────┐                        │
│         │           │              │                         │
│    ┌────┴────┐ ┌────┴────┐  ┌─────┴─────┐                  │
│    │ Boot    │ │ CLINT   │  │  External  │                  │
│    │ ROM     │ │         │  │  Memory    │                  │
│    │ 4 KB    │ │ 16 KB   │  │  (DRAM)    │                  │
│    └─────────┘ └─────────┘  └───────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

### AXI4-Lite Bus Interface

| Signal      | Width | Direction | Description             |
| ----------- | ----- | --------- | ----------------------- |
| awaddr      | 32    | Master→Slave | Write address         |
| awvalid     | 1     | Master→Slave | Write address valid   |
| awready     | 1     | Slave→Master | Write address ready   |
| wdata       | 32    | Master→Slave | Write data            |
| wstrb       | 4     | Master→Slave | Write byte strobes    |
| wvalid      | 1     | Master→Slave | Write data valid      |
| wready      | 1     | Slave→Master | Write data ready      |
| bresp       | 2     | Slave→Master | Write response        |
| bvalid      | 1     | Slave→Master | Write response valid  |
| bready      | 1     | Master→Slave | Write response ready  |
| araddr      | 32    | Master→Slave | Read address          |
| arvalid     | 1     | Master→Slave | Read address valid    |
| arready     | 1     | Slave→Master | Read address ready    |
| rdata       | 32    | Slave→Master | Read data             |
| rresp       | 2     | Slave→Master | Read response         |
| rvalid      | 1     | Slave→Master | Read data valid       |
| rready      | 1     | Master→Slave | Read data ready       |

---

## 9. Performance Analysis

### Ideal IPC Analysis

| Instruction Type          | CPI (no hazards) | Frequency (SPEC-like) |
| ------------------------- | ---------------- | --------------------- |
| ALU reg-reg               | 1                | ~40%                  |
| ALU immediate             | 1                | ~20%                  |
| Load                      | 2 (load-use stall) | ~15%               |
| Store                     | 1                | ~10%                  |
| Branch (taken)            | 2 (1-cycle penalty) | ~10%              |
| Jump                      | 2 (1-cycle penalty) | ~3%               |
| MUL                       | 5                | ~1%                   |
| DIV                       | 33               | <0.1%                 |

Weighted CPI (ideal, cache hits):
= 0.40×1 + 0.20×1 + 0.15×2 + 0.10×1 + 0.10×2 + 0.03×2 + 0.01×5 + 0.001×33
= 0.40 + 0.20 + 0.30 + 0.10 + 0.20 + 0.06 + 0.05 + 0.033
= 1.343 CPI
→ IPC ≈ 0.74

With 90% branch prediction accuracy (BTB), taken branch penalty drops:
= 0.10×0.10×2 + 0.10×0.90×1 = 0.02 + 0.09 = 0.11
Weighted CPI (with BTB): 0.40+0.20+0.30+0.10+0.11+0.06+0.05+0.033 = 1.253
→ IPC ≈ 0.80

For non-memory, non-branch workloads (pure ALU):
→ IPC ≈ 1.0 (achieving design target)

### Cache Performance

| Parameter            | I-Cache             | D-Cache             |
| -------------------- | ------------------- | ------------------- |
| Size                 | 4 KB                | 4 KB                |
| Associativity        | Direct-mapped       | Direct-mapped       |
| Line size            | 16 bytes            | 16 bytes            |
| Hit latency          | 1 cycle             | 1 cycle             |
| Miss penalty (min)   | 4 cycles            | 4 cycles            |
| Miss rate (est.)     | <2% (small loops)   | <5% (streaming data)|

### Target Operating Point

| Metric              | Target        |
| ------------------- | ------------- |
| fmax (sky130, tt)   | ≥ 50 MHz      |
| Core area           | ≤ 0.25 mm²    |
| Gate count          | ~20 K gates   |
| Power (est.)        | ≤ 15 mW       |
| IPC (Dhrystone-like)| ≥ 0.8         |
