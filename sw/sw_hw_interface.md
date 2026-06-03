# lunahan_v1 Software-Hardware Interface Specification

## 1. Core Overview

| Parameter       | Value                              |
|-----------------|------------------------------------|
| ISA             | RV32IMC (I + M + Compressed)       |
| Pipeline        | 5-stage in-order: IF→ID→EX→MEM→WB |
| Register File   | 32 × 32-bit GPRs                   |
| ICache          | 4 KB direct-mapped, 16 B line      |
| DCache          | 4 KB direct-mapped, 16 B line, write-back |
| BTB             | 64-entry bimodal, 2-bit saturating counter |
| Bus Interface   | AXI4-Lite, 32-bit addr, 32-bit data |
| Target Clock    | 100 MHz (sky130)                   |
| ABI             | ilp32, soft-float                  |

---

## 2. Memory Map

| Region  | Start      | End        | Size     | Description              |
|---------|------------|------------|----------|--------------------------|
| IMEM    | 0x00000000 | 0x00000FFF | 4 KB     | Instruction memory (I$)  |
| ROM     | 0x00001000 | 0x00001FFF | 4 KB     | Boot ROM                 |
| CLINT   | 0x02000000 | 0x0200FFFF | 64 KB    | Core-Local Interruptor   |
| PLIC    | 0x0C000000 | 0x0FFFFFFF | 64 MB    | Platform-Level Interrupt Controller |
| DMEM    | 0x10000000 | 0x10000FFF | 4 KB     | Data memory (D$)         |
| MMIO    | 0x20000000 | 0x2FFFFFFF | 256 MB   | Memory-mapped I/O        |

### 2.1 CLINT Register Map (offset from 0x02000000)

| Offset  | Width | Description                    |
|---------|-------|--------------------------------|
| 0x0000  | 32    | msip — software interrupt      |
| 0x4000  | 64    | mtimecmp — timer compare       |
| 0xBFF8  | 64    | mtime — current timer value    |

### 2.2 MMIO Region (base 0x20000000, example assignments)

| Offset  | Width | Description     |
|---------|-------|-----------------|
| 0x0000  | 32    | UART0 TXDATA    |
| 0x0004  | 32    | UART0 RXDATA    |
| 0x0008  | 32    | UART0 STATUS    |
| 0x0100  | 32    | GPIO_IN         |
| 0x0104  | 32    | GPIO_OUT        |
| 0x0108  | 32    | GPIO_DIR        |

---

## 3. Register File Conventions

| Reg   | ABI Name | Description                | Saver   |
|-------|----------|----------------------------|---------|
| x0    | zero     | Hard-wired zero            | —       |
| x1    | ra       | Return address             | Caller  |
| x2    | sp       | Stack pointer              | Callee  |
| x3    | gp       | Global pointer             | —       |
| x4    | tp       | Thread pointer             | —       |
| x5    | t0       | Temporary 0                | Caller  |
| x6    | t1       | Temporary 1                | Caller  |
| x7    | t2       | Temporary 2                | Caller  |
| x8    | s0 / fp  | Saved 0 / Frame pointer    | Callee  |
| x9    | s1       | Saved 1                    | Callee  |
| x10   | a0       | Argument 0 / Return value  | Caller  |
| x11   | a1       | Argument 1 / Return value  | Caller  |
| x12   | a2       | Argument 2                 | Caller  |
| x13   | a3       | Argument 3                 | Caller  |
| x14   | a4       | Argument 4                 | Caller  |
| x15   | a5       | Argument 5                 | Caller  |
| x16   | a6       | Argument 6                 | Caller  |
| x17   | a7       | Argument 7                 | Caller  |
| x18   | s2       | Saved 2                    | Callee  |
| x19   | s3       | Saved 3                    | Callee  |
| x20   | s4       | Saved 4                    | Callee  |
| x21   | s5       | Saved 5                    | Callee  |
| x22   | s6       | Saved 6                    | Callee  |
| x23   | s7       | Saved 7                    | Callee  |
| x24   | s8       | Saved 8                    | Callee  |
| x25   | s9       | Saved 9                    | Callee  |
| x26   | s10      | Saved 10                   | Callee  |
| x27   | s11      | Saved 11                   | Callee  |
| x28   | t3       | Temporary 3                | Caller  |
| x29   | t4       | Temporary 4                | Caller  |
| x30   | t5       | Temporary 5                | Caller  |
| x31   | t6       | Temporary 6                | Caller  |

### 3.1 Calling Convention (ilp32, soft-float)

- Arguments passed in a0–a7; excess on stack.
- Return values in a0 (32-bit) or a0:a1 (64-bit).
- Stack aligned to 16 bytes at function call boundary.
- Caller-saved: ra, t0–t6, a0–a7.
- Callee-saved: sp, gp, tp, s0–s11.

---

## 4. CSR Registers

| CSR      | Address | Description                          |
|----------|---------|--------------------------------------|
| mstatus  | 0x300   | Machine status: MIE(3), MPIE(7)      |
| mie      | 0x304   | Machine interrupt enable             |
| mip      | 0x344   | Machine interrupt pending            |
| mcause   | 0x342   | Machine cause (exception/interrupt)  |
| mtvec    | 0x305   | Machine trap-handler base address    |
| mepc     | 0x341   | Machine exception program counter    |
| mhartid  | 0xF14   | Hardware thread ID                   |
| mscratch | 0x340   | Machine scratch register             |
| mtval    | 0x343   | Machine trap value                   |

### 4.1 mcause Exception Codes

| Code  | Type        | Description              |
|-------|-------------|--------------------------|
| 0     | Interrupt   | User software interrupt  |
| 3     | Interrupt   | Machine software int     |
| 7     | Interrupt   | Machine timer interrupt  |
| 11    | Interrupt   | Machine external int     |
| 0     | Exception   | Instruction addr misalign|
| 1     | Exception   | Instruction access fault |
| 2     | Exception   | Illegal instruction      |
| 3     | Exception   | Breakpoint               |
| 4     | Exception   | Load address misaligned  |
| 5     | Exception   | Load access fault        |
| 6     | Exception   | Store address misaligned |
| 7     | Exception   | Store access fault       |
| 8     | Exception   | ECALL from U-mode        |
| 11    | Exception   | ECALL from M-mode        |

Interrupt codes have bit 31 (mcause[31]) set. Exception codes do not.

### 4.2 Interrupt/Exception Vector Table

mtvec.MODE = 0 (Direct): All traps jump to mtvec.BASE.
mtvec.MODE = 1 (Vectored): Sync exceptions → BASE, interrupts → BASE + 4 × cause.

Default: mtvec = 0x00000100, MODE = 0 (direct).

---

## 5. Pipeline Hazard Model

### 5.1 Data Hazards

```
Instruction sequence:   IF | ID | EX | MEM | WB
  add x5, x4, x3                 IF | ID | EX | MEM | WB
  sub x7, x5, x6                      IF | ID | —  | EX | MEM | WB
                                        Stall 1 cycle
                                        (forward EX→EX)
```

#### Forwarding Paths

| Path   | Source Stage | Dest Stage | Latency | Use Case                |
|--------|-------------|------------|---------|-------------------------|
| EX→EX  | EX (ALU out)| EX (src op)| 0 cycles| Back-to-back ALU ops    |
| MEM→EX | MEM (mem rd)| EX (src op)| 1 cycle | Load followed by ALU    |
| WB→EX  | WB (reg wr) | EX (src op)| 2 cycles| Load followed by ALU    |
|        |             |            |         | (bypass needed or stall)|

#### Load-Use Hazard

```
  lw  x5, 0(x4)     IF | ID | EX | MEM | WB
  add x7, x5, x6         IF | ID | EX | —  | —  | Stall 1 cycle, then forward MEM→EX
```

**Penalty: 1 stall cycle.** The load data is available only after MEM; the dependent ALU op must wait.

#### Multi-Cycle Operations

| Instruction | Cycles | Pipeline Behavior                        |
|-------------|--------|------------------------------------------|
| MUL         | 4      | EX stage repeats 4× (stalls IF,ID)       |
| MULH        | 4      | EX stage repeats 4×                       |
| DIV/REM     | 32     | EX stage repeats 32× (early-out possible)|

Multi-cycle ops stall the pipeline at EX; IF and ID are also stalled.

### 5.2 Control Hazards

#### Branch Mispredict Penalty: 2 cycles

```
  beq x5,x6,target   IF | ID | EX | MEM | WB
  (wrong next PC)         IF | ID | —  | —  | —  2 bubbles, then fetch correct target
  target_inst:                                  IF | ID | EX | MEM | WB
```

BTB provides next-PC prediction in IF. 2-bit saturating counters:
- 00,01 = not-taken; 10,11 = taken.
- Misprediction flushes ID and EX; fetches correct target.
- Taken penalty: 2 cycles. Not-taken: 0 cycles (sequential fetch is default).

### 5.3 Structural Hazards

- Single write port on register file: no structural hazard for WB (in-order).
- ICache and DCache are independent (Harvard): no memory port conflicts.
- AXI4-Lite bus is shared; simultaneous I$ miss + D$ access serializes.

---

## 6. Cache Behavior

### 6.1 ICache (Instruction Cache)

| Parameter       | Value                         |
|-----------------|-------------------------------|
| Size            | 4096 bytes (4 KB)             |
| Associativity   | Direct-mapped                 |
| Line size       | 16 bytes (4 × 32-bit words)   |
| Lines           | 256                           |
| Miss penalty    | ~8 cycles (AXI4-Lite fetch)   |
| Hit latency     | 1 cycle                       |
| Fill policy     | Critical word first           |

**Miss rate model:**
- Sequential access: 2% (1 miss per 8 cache lines on average).
- Jump/branch targets: 8% (opportunistic, depends on code layout).
- Cache line alignment: fetch group starts at PC[31:4] boundary.

### 6.2 DCache (Data Cache)

| Parameter       | Value                         |
|-----------------|-------------------------------|
| Size            | 4096 bytes (4 KB)             |
| Associativity   | Direct-mapped                 |
| Line size       | 16 bytes (4 × 32-bit words)   |
| Lines           | 256                           |
| Write policy    | Write-back, write-allocate    |
| Miss penalty    | ~10 cycles                    |
| Hit latency     | 1 cycle (read), 1 cycle (write-hit) |

**Miss rate model:**
- Average: 5%.
- Sequential array access: < 1% (spatial locality within 16 B line).
- Random/pointer chasing: up to 15%.

### 6.3 Cache Line Address Decoding

For a 32-bit address A:
```
A[31:12] — page/region selector
A[11:4]  — cache index (8 bits → 256 lines)
A[3:2]   — word within line (4 words)
A[1:0]   — byte within word
Tag      = A[31:12] (upper 20 bits)
```

---

## 7. AXI4-Lite Bus Protocol

### 7.1 Signal List

| Signal       | Width | Dir (from core) | Description                  |
|-------------|-------|----------------|------------------------------|
| AWADDR      | 32    | O              | Write address                |
| AWVALID     | 1     | O              | Write address valid          |
| AWREADY     | 1     | I              | Write address ready          |
| WDATA       | 32    | O              | Write data                   |
| WSTRB       | 4     | O              | Write byte strobes           |
| WVALID      | 1     | O              | Write data valid             |
| WREADY      | 1     | I              | Write data ready             |
| BRESP       | 2     | I              | Write response (00=OKAY)     |
| BVALID      | 1     | I              | Write response valid         |
| BREADY      | 1     | O              | Write response ready         |
| ARADDR      | 32    | O              | Read address                 |
| ARVALID     | 1     | O              | Read address valid           |
| ARREADY     | 1     | I              | Read address ready           |
| RDATA       | 32    | I              | Read data                    |
| RRESP       | 2     | I              | Read response (00=OKAY)      |
| RVALID      | 1     | I              | Read data valid              |
| RREADY      | 1     | O              | Read data ready              |

### 7.2 Handshake Rules

**Address channel:** AWVALID + AWREADY → AWADDR consumed (write). ARVALID + ARREADY → ARADDR consumed (read).

**Data channel:** WVALID + WREADY → WDATA consumed (write). RVALID + RREADY → RDATA consumed (read).

**Response channel:** BVALID + BREADY → BRESP consumed (write).

All channels are independent. The core follows AXI4-Lite ordering:
- Write: AW → W → B (address valid before data, response last).
- Read: AR → R (address before data).

### 7.3 Burst Support

AXI4-Lite does not support bursts. All transfers are single-beat, 4-byte (32-bit). Unaligned accesses are decomposed into two bus transactions by the LSU.

---

## 8. MVENDORID / MARCHID / MIMPID

| CSR      | Address | Value       | Description                     |
|----------|---------|-------------|----------------------------------|
| mvendorid| 0xF11   | 0x00000000  | Vendor ID (0 = non-commercial)  |
| marchid  | 0xF12   | 0x00000001  | Architecture ID (lunahan_v1)    |
| mimpid   | 0xF13   | 0x00000001  | Implementation version 1.0      |

---

## 9. Reset and Boot Sequence

1. Reset deasserts; PC = 0x00000000 (IMEM base).
2. First instruction fetched from ICache (cold miss → AXI4-Lite read from IMEM).
3. _start (crt0.s) initializes sp, gp, clears BSS, copies .data.
4. main() called.
5. mstatus.MIE = 0 at reset (interrupts globally disabled).
6. mtvec = 0x00000000 default (direct mode), must be set by software.

---

## 10. Performance Summary

| Metric                | Value           |
|-----------------------|-----------------|
| Peak IPC (no hazards) | 1.0             |
| Typical IPC (Dhrystone)| ~0.8–0.9        |
| Branch mispredict rate| ~5% (with BTB)  |
| Load-use stall rate   | ~3%             |
| CPI (CoreMark)        | ~1.35           |
| DMIPS/MHz             | ~1.2            |
