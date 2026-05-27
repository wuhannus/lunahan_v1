# RISC-V RV32IMC Architecture Specification — lunahan_v1

## 1. ISA Overview

lunahan_v1 implements the **RV32IMC** instruction set:

| Extension | Name              | Description                                   |
| --------- | ----------------- | --------------------------------------------- |
| RV32I     | Integer base      | 40 mandatory integer instructions             |
| M         | Multiply/Divide   | 8 instructions (mul/mulh/mulhsu/mulhu + div/rem variants) |
| C         | Compressed         | 16-bit compressed forms of common 32-bit insts |

**XLEN** = 32, **ILEN** = 32 (uncompressed), **CLEN** = 16 (compressed).

Privilege architecture: **Machine mode (M-mode)** only — no supervisor or
user mode in v1. This simplifies the CSR set and trap handling.

---

## 2. Instruction Formats

RV32IMC uses six base formats for 32-bit instructions.

### 2.1 R-Type (Register-Register)

```
  31        25 24    20 19    15 14  12 11     7 6        0
 ┌─────────────┬────────┬────────┬───────┬────────┬──────────┐
 │   funct7    │  rs2   │  rs1   │funct3 │  rd    │  opcode  │
 └─────────────┴────────┴────────┴───────┴────────┴──────────┘
     7 bits     5 bits   5 bits   3 bits  5 bits    7 bits
```

Used by: ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND, MUL*, DIV*, REM*

### 2.2 I-Type (Register-Immediate)

```
  31              20 19    15 14  12 11     7 6        0
 ┌───────────────────┬────────┬───────┬────────┬──────────┐
 │    imm[11:0]      │  rs1   │funct3 │  rd    │  opcode  │
 └───────────────────┴────────┴───────┴────────┴──────────┘
      12 bits         5 bits   3 bits  5 bits    7 bits
```

Used by: ADDI, SLTI, SLTIU, XORI, ORI, ANDI, SLLI, SRLI, SRAI, JALR, LB, LH,
LW, LBU, LHU, CSRRW, CSRRS, CSRRC, CSRRWI, CSRRSI, CSRRCI, ECALL, EBREAK

Shift-immediate variants use only imm[4:0] placed in shamt field; the upper
7 bits encode funct7 variant (0000000 for logical, 0100000 for arithmetic).

### 2.3 S-Type (Store)

```
  31        25 24    20 19    15 14  12 11     7 6        0
 ┌─────────────┬────────┬────────┬───────┬────────┬──────────┐
 │  imm[11:5]  │  rs2   │  rs1   │funct3 │imm[4:0]│  opcode  │
 └─────────────┴────────┴────────┴───────┴────────┴──────────┘
     7 bits     5 bits   5 bits   3 bits  5 bits    7 bits
```

Used by: SB, SH, SW

### 2.4 B-Type (Branch)

```
  31        25 24    20 19    15 14  12 11     7 6        0
 ┌─────────────┬────────┬────────┬───────┬────────┬──────────┐
 │ imm[12|10:5]│  rs2   │  rs1   │funct3 │imm[4:1|11]│opcode │
 └─────────────┴────────┴────────┴───────┴────────┴──────────┘
```

Immediate scrambled: {imm[12], imm[10:5], rs2, rs1, funct3, imm[4:1], imm[11], opcode}

Used by: BEQ, BNE, BLT, BGE, BLTU, BGEU

### 2.5 U-Type (Upper Immediate)

```
  31                                      12 11     7 6        0
 ┌──────────────────────────────────────────┬────────┬──────────┐
 │              imm[31:12]                  │  rd    │  opcode  │
 └──────────────────────────────────────────┴────────┴──────────┘
                   20 bits                    5 bits    7 bits
```

Used by: LUI, AUIPC

### 2.6 J-Type (Jump)

```
  31                                      12 11     7 6        0
 ┌──────────────────────────────────────────┬────────┬──────────┐
 │              imm[20|10:1|11|19:12]       │  rd    │  opcode  │
 └──────────────────────────────────────────┴────────┴──────────┘
```

Immediate scrambled: {imm[20], imm[10:1], imm[11], imm[19:12], 0}

Used by: JAL

### 2.7 C-Type (Compressed, 16-bit)

Compressed instructions fit into four quadrants based on opcode[1:0]:

| Quadrant | opcode[1:0] | Types                                           |
| -------- | ----------- | ----------------------------------------------- |
| C0       | 00          | C.ADDI4SPN, C.FLD, C.LW, C.LD, C.FSD, C.SW, C.SD |
| C1       | 01          | C.ADDI, C.JAL, C.LI, C.LUI, C.ADDI16SP, C.MISC-ALU, C.J, C.BEQZ, C.BNEZ |
| C2       | 10          | C.SLLI, C.FLDSP, C.LWSP, C.LDSP, C.JR, C.MV, C.JALR, C.ADD, C.FSDSP, C.SWSP, C.SDSP |
| C3       | 11          | Reserved (wider instructions)                    |

Each 16-bit instruction maps exactly one 32-bit instruction, simplifying
the decoder: the ID stage expands C into its 32-bit equivalent before
further decoding.

---

## 3. Complete Instruction Listing

### 3.1 RV32I Base Integer Instructions (40)

| Mnemonic  | Type | opcode(7) | funct3(3) | funct7(7)        | Operation                                  |
| --------- | ---- | --------- | --------- | ---------------- | ------------------------------------------ |
| LUI       | U    | 0110111   | —         | —                | rd = imm[31:12] << 12                      |
| AUIPC     | U    | 0010111   | —         | —                | rd = pc + (imm[31:12] << 12)               |
| JAL       | J    | 1101111   | —         | —                | rd = pc+4; pc += imm                       |
| JALR      | I    | 1100111   | 000       | —                | rd = pc+4; pc = (rs1+imm) & ~1             |
| BEQ       | B    | 1100011   | 000       | —                | pc += (rs1==rs2) ? imm : 4                 |
| BNE       | B    | 1100011   | 001       | —                | pc += (rs1!=rs2) ? imm : 4                 |
| BLT       | B    | 1100011   | 100       | —                | pc += (rs1<rs2 signed) ? imm : 4           |
| BGE       | B    | 1100011   | 101       | —                | pc += (rs1>=rs2 signed) ? imm : 4          |
| BLTU      | B    | 1100011   | 110       | —                | pc += (rs1<rs2 unsigned) ? imm : 4         |
| BGEU      | B    | 1100011   | 111       | —                | pc += (rs1>=rs2 unsigned) ? imm : 4        |
| LB        | I    | 0000011   | 000       | —                | rd = sign-ext(mem[rs1+imm][7:0])           |
| LH        | I    | 0000011   | 001       | —                | rd = sign-ext(mem[rs1+imm][15:0])          |
| LW        | I    | 0000011   | 010       | —                | rd = mem[rs1+imm][31:0]                    |
| LBU       | I    | 0000011   | 100       | —                | rd = zero-ext(mem[rs1+imm][7:0])           |
| LHU       | I    | 0000011   | 101       | —                | rd = zero-ext(mem[rs1+imm][15:0])          |
| SB        | S    | 0100011   | 000       | —                | mem[rs1+imm][7:0] = rs2[7:0]              |
| SH        | S    | 0100011   | 001       | —                | mem[rs1+imm][15:0] = rs2[15:0]            |
| SW        | S    | 0100011   | 010       | —                | mem[rs1+imm][31:0] = rs2                   |
| ADDI      | I    | 0010011   | 000       | —                | rd = rs1 + imm                             |
| SLTI      | I    | 0010011   | 010       | —                | rd = (rs1 < imm signed) ? 1 : 0           |
| SLTIU     | I    | 0010011   | 011       | —                | rd = (rs1 < imm unsigned) ? 1 : 0         |
| XORI      | I    | 0010011   | 100       | —                | rd = rs1 ^ imm                             |
| ORI       | I    | 0010011   | 110       | —                | rd = rs1 | imm                             |
| ANDI      | I    | 0010011   | 111       | —                | rd = rs1 & imm                             |
| SLLI      | I    | 0010011   | 001       | 0000000          | rd = rs1 << shamt                          |
| SRLI      | I    | 0010011   | 101       | 0000000          | rd = rs1 >> shamt (logical)               |
| SRAI      | I    | 0010011   | 101       | 0100000          | rd = rs1 >> shamt (arithmetic)            |
| ADD       | R    | 0110011   | 000       | 0000000          | rd = rs1 + rs2                             |
| SUB       | R    | 0110011   | 000       | 0100000          | rd = rs1 - rs2                             |
| SLL       | R    | 0110011   | 001       | 0000000          | rd = rs1 << rs2[4:0]                       |
| SLT       | R    | 0110011   | 010       | 0000000          | rd = (rs1 < rs2 signed) ? 1 : 0           |
| SLTU      | R    | 0110011   | 011       | 0000000          | rd = (rs1 < rs2 unsigned) ? 1 : 0         |
| XOR       | R    | 0110011   | 100       | 0000000          | rd = rs1 ^ rs2                             |
| SRL       | R    | 0110011   | 101       | 0000000          | rd = rs1 >> rs2[4:0] (logical)            |
| SRA       | R    | 0110011   | 101       | 0100000          | rd = rs1 >> rs2[4:0] (arithmetic)         |
| OR        | R    | 0110011   | 110       | 0000000          | rd = rs1 | rs2                             |
| AND       | R    | 0110011   | 111       | 0000000          | rd = rs1 & rs2                             |
| FENCE     | I    | 0001111   | 000       | —                | Ordering hint (treated as NOP)             |
| ECALL     | I    | 1110011   | 000       | 000000000000     | Environment call (trap to M-mode)          |
| EBREAK    | I    | 1110011   | 000       | 000000000001     | Breakpoint (trap to M-mode)                |

### 3.2 M Extension — Multiply/Divide (8)

| Mnemonic  | Type | opcode(7) | funct3(3) | funct7(7) | Operation                                    |
| --------- | ---- | --------- | --------- | --------- | -------------------------------------------- |
| MUL       | R    | 0110011   | 000       | 0000001   | rd = (rs1 * rs2)[31:0]                       |
| MULH      | R    | 0110011   | 001       | 0000001   | rd = (rs1 * rs2)[63:32] (signed)             |
| MULHSU    | R    | 0110011   | 010       | 0000001   | rd = (signed(rs1) * unsigned(rs2))[63:32]    |
| MULHU     | R    | 0110011   | 011       | 0000001   | rd = (rs1 * rs2)[63:32] (unsigned)           |
| DIV       | R    | 0110011   | 100       | 0000001   | rd = rs1 / rs2 (signed)                      |
| DIVU      | R    | 0110011   | 101       | 0000001   | rd = rs1 / rs2 (unsigned)                    |
| REM       | R    | 0110011   | 110       | 0000001   | rd = rs1 % rs2 (signed)                      |
| REMU      | R    | 0110011   | 111       | 0000001   | rd = rs1 % rs2 (unsigned)                    |

Division by zero returns all-1s (-1) for signed, all-1s for unsigned,
per the RISC-V spec. Signed overflow of division (INT_MIN / -1) returns
the dividend unchanged.

### 3.3 C Extension — Compressed (representative subset)

| Mnemonic    | C-Type | 32-bit equivalent                |
| ----------- | ------ | -------------------------------- |
| C.ADDI4SPN  | CIW    | ADDI rd', x2, nzuimm             |
| C.LW        | CL     | LW rd', rs1', uimm               |
| C.SW        | CS     | SW rs2', rs1', uimm              |
| C.ADDI      | CI     | ADDI rd, rd, nzimm               |
| C.JAL       | CJ     | JAL x1, offset                   |
| C.LI        | CI     | ADDI rd, x0, nzimm               |
| C.LUI       | CI     | LUI rd, nzimm                    |
| C.ADDI16SP  | CI     | ADDI x2, x2, nzimm               |
| C.SLLI      | CI     | SLLI rd, rd, shamt               |
| C.J         | CJ     | JAL x0, offset                   |
| C.BEQZ      | CB     | BEQ rs1', x0, offset             |
| C.BNEZ      | CB     | BNE rs1', x0, offset             |
| C.LWSP      | CI     | LW rd, x2, uimm                  |
| C.SWSP      | CSS    | SW rs2, x2, uimm                 |
| C.JR        | CR     | JALR x0, rs1, 0                  |
| C.MV        | CR     | ADD rd, x0, rs2                  |
| C.JALR      | CR     | JALR x1, rs1, 0                  |
| C.ADD       | CR     | ADD rd, rd, rs2                  |
| C.EBREAK    | CR     | EBREAK                          |
| C.NOP       | CI     | ADDI x0, x0, 0                   |

The decoder in the ID stage expands every 16-bit compressed instruction into
its canonical 32-bit equivalent. This keeps the rest of the pipeline
unaware of compression.

---

## 4. Register File

| Name | ABI Name | Description                        | Reset Value |
| ---- | -------- | ---------------------------------- | ----------- |
| x0   | zero     | Hard-wired zero                    | 0           |
| x1   | ra       | Return address                     | 0           |
| x2   | sp       | Stack pointer                      | STACK_TOP   |
| x3   | gp       | Global pointer                     | 0           |
| x4   | tp       | Thread pointer                     | 0           |
| x5-7 | t0-t2    | Temporaries                        | 0           |
| x8   | s0/fp    | Saved register / Frame pointer     | 0           |
| x9   | s1       | Saved register                     | 0           |
| x10-17| a0-a7   | Function arguments / return values | 0           |
| x18-27| s2-s11  | Saved registers                    | 0           |
| x28-31| t3-t6   | Temporaries                        | 0           |

- **x0** always reads as 0; writes to x0 are silently ignored.
- All registers are 32 bits wide.
- The register file has 2 read ports (rs1, rs2) and 1 write port (rd).
- Register read is in the ID stage; write-back is in the WB stage.
- Forwarding from EX/MEM/WB resolves RAW hazards.

---

## 5. CSR Registers (M-mode)

All CSRs are read/written via the Zicsr instructions (CSRRW, CSRRS, CSRRC,
CSRRWI, CSRRSI, CSRRCI). These are executed in the EX stage via the CSR
unit.

| CSR Name     | Number  | Description                                    |
| ------------ | ------- | ---------------------------------------------- |
| mvendorid    | 0xF11   | Vendor ID (0 for non-commercial)               |
| marchid      | 0xF12   | Architecture ID (custom: 0x0000_0001)          |
| mimpid       | 0xF13   | Implementation ID (0x0000_0001 for v1)         |
| mhartid      | 0xF14   | Hardware thread ID (0 for single-core)          |
| mstatus      | 0x300   | Machine status register                        |
| misa         | 0x301   | ISA and extensions (RV32IMC encoded)           |
| mie          | 0x304   | Machine interrupt-enable register              |
| mtvec        | 0x305   | Machine trap-handler base address (vectored)   |
| mscratch     | 0x340   | Machine scratch register (for trap handlers)    |
| mepc         | 0x341   | Machine exception program counter               |
| mcause       | 0x342   | Machine trap cause                              |
| mtval        | 0x343   | Machine trap value (faulting address/bad inst)  |
| mip          | 0x344   | Machine interrupt-pending register              |
| mcycle       | 0xB00   | Machine cycle counter (64-bit)                  |
| minstret     | 0xB02   | Machine instructions-retired counter (64-bit)   |

### mstatus Fields (32-bit)

| Bit(s) | Name    | Description                              |
| ------ | ------- | ---------------------------------------- |
| 3      | MIE     | Machine interrupt enable (global)         |
| 7      | MPIE    | Previous MIE (saved on trap entry)        |
| 11:12  | MPP     | Previous privilege mode (always 11=M)     |

### mcause Encoding

| Bit(s)      | Field      | Description                               |
| ----------- | ---------- | ----------------------------------------- |
| 31          | Interrupt  | 0=exception, 1=interrupt                  |
| 30:0        | Exception Code | See table below                       |

| Code | Type                  | Description                         |
| ---- | --------------------- | ----------------------------------- |
| 0    | Instruction address misaligned | JAL/JALR/Branch to misaligned addr |
| 1    | Instruction access fault | Fetch from invalid address        |
| 2    | Illegal instruction    | Invalid opcode                      |
| 3    | Breakpoint            | EBREAK                              |
| 4    | Load address misaligned | Misaligned load address           |
| 5    | Load access fault     | Load from invalid address           |
| 6    | Store address misaligned | Misaligned store address          |
| 7    | Store access fault    | Store to invalid address            |
| 8    | Environment call from M-mode | ECALL                     |
| 11   | Environment call from M-mode | ECALL (legacy)             |
| 3+31 | Machine software interrupt | MSIP bit set                   |
| 7+31 | Machine timer interrupt | MTIP bit set                      |
| 11+31| Machine external interrupt | MEIP bit set                    |

### mtvec Fields

| Bit(s) | Name | Description                              |
| ------ | ---- | ---------------------------------------- |
| 1:0    | MODE | 0=direct, 1=vectored                     |
| 31:2   | BASE | Trap handler base address (4-byte aligned) |

---

## 6. Memory Map

```
 ┌────────────────────────────┬─────────────────────────────────────┐
 │ Address Range              │ Region                              │
 ├────────────────────────────┼─────────────────────────────────────┤
 │ 0x0000_0000 — 0x0000_0FFF │ ROM (4 KB) — boot/reset vector      │
 │ 0x0000_1000 — 0x0000_1FFF │ CLINT (Core-Local Interruptor)      │
 │ 0x0000_2000 — 0x0FFF_FFFF │ Reserved                            │
 │ 0x1000_0000 — 0x1000_FFFF │ UART (16550-compatible)              │
 │ 0x2000_0000 — 0x2FFF_FFFF │ External memory / peripherals        │
 │ 0x8000_0000 — 0xFFFF_FFFF │ DRAM (2 GB)                          │
 └────────────────────────────┴─────────────────────────────────────┘
```

- **Reset vector**: 0x0000_0000 (boots from ROM)
- **mtvec**: Default 0x0000_0000 (trap handler at reset vector, direct mode)
- Stack pointer initialized at top of DRAM (0xFFFF_FFF0) by boot code.

---

## 7. Exception and Interrupt Handling

### Trap Entry Sequence (in EX/MEM stages)

1. Flush pipeline stages IF, ID (instructions after the faulting one).
2. Save `pc` of faulting instruction to `mepc`.
3. Save cause to `mcause` (with interrupt bit if applicable).
4. Save faulting address or bad instruction to `mtval` if applicable.
5. Set `mstatus.MPIE = mstatus.MIE`, `mstatus.MIE = 0`,
   `mstatus.MPP = 11` (M-mode).
6. Set `pc = mtvec.BASE` (or `mtvec.BASE + 4*cause` if vectored mode).
7. Trap handler executes in M-mode.

### MRET (Machine-mode Return)

1. Set `pc = mepc`.
2. Restore `mstatus.MIE = mstatus.MPIE`.
3. Set `mstatus.MPIE = 1`.

### Interrupt Conditions

Interrupts are only taken when:
- `mstatus.MIE = 1` (global interrupt enable).
- The corresponding bit in `mie` is set.
- The corresponding bit in `mip` is set.

Interrupts are sampled at instruction-retire boundaries (WB stage), not
mid-instruction.

---

## 8. Privilege Modes

lunahan_v1 supports only **Machine mode (M-mode)** — the highest
privilege level. All code runs in M-mode. This is a deliberate
simplification for an embedded-class core.

- RISC-V Privileged ISA 1.12, Machine-level ISA only.
- No PMP (Physical Memory Protection) — v1 targets simple embedded systems.
- No virtual memory — all addresses are physical.
