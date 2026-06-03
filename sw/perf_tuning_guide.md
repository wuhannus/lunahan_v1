# lunahan_v1 Performance Tuning Guide

## 1. Pipeline Hazard Reference

```
  Clock:   1   2   3   4   5   6   7   8
  ─────────────────────────────────────────
  Instr A  IF  ID  EX  MEM WB
  Instr B      IF  ID  EX  MEM WB
  Instr C          IF  ID  EX  MEM WB
```

### 1.1 Data Hazards — Forwarding Paths

| Producer → Consumer    | Gap  | Stall? | How to avoid                              |
|------------------------|------|--------|--------------------------------------------|
| ALU result → ALU input | 1    | 0      | EX→EX forwarding; no action needed         |
| Load → ALU input       | 1    | **1 cy**  | Separate by ≥1 independent instruction  |
| Load → Store data      | 1    | **1 cy**  | Schedule store address calc in between  |
| Load → Load address    | 1    | **1 cy**  | Not common; use byte offset instead     |
| MUL/DIV → ALU input    | 1    | **N cy**  | MUL=4, DIV=32; schedule unrelated work  |

**Key rule:** Never use a load result in the very next instruction.

#### Example — Bad (1 stall cycle)
```c
int x = *ptr;       // lw t0, 0(a0)    → t0 ready at end of MEM
int y = x + 1;      // addi t1, t0, 1  → stalls 1 cycle waiting for t0
```

#### Example — Good (0 stall cycles)
```c
int x = *ptr;       // lw t0, 0(a0)    → t0 ready at end of MEM
*ptr2 = *ptr3;      // lw t2, 0(a1); sw t2, 0(a2)  ← independent
int y = x + 1;      // addi t1, t0, 1  → forwarding covers the gap
```

### 1.2 Control Hazards — Branch Mispredict Penalty

| Branch type       | Taken penalty | Not-taken penalty | BTB role                     |
|-------------------|---------------|-------------------|------------------------------|
| Conditional (beq) | 2 cycles      | 0 cycles          | BTB predicts direction       |
| Unconditional (jal)| 2 cycles     | N/A               | BTB predicts target address  |
| Indirect (jalr)   | 2 cycles      | N/A               | BTB predicts target; may mispredict on dynamic dispatch |

**Loop branch optimization:** Place the conditional branch at the loop bottom (do-while pattern). The taken path is predicted correctly by the 2-bit counter after the first iteration, giving 0 penalty for all subsequent iterations. The final (not-taken) iteration costs 2 cycles — amortized to near zero for long loops.

```c
// Bad: branch at top, always mispredicts first iteration
for (i = 0; i < n; i++) { ... }

// Good: compiler generates do-while for countable loops
i = 0;
do { ... } while (++i < n);
```

### 1.3 Structural Hazards

lunahan_v1 has **one write port** on the register file and independent I$/D$. Structural hazards are rare:

- **No WB conflict**: In-order pipeline means at most one instruction writes back per cycle.
- **Bus conflict**: Simultaneous I$ miss + D$ access serializes on AXI4-Lite. Cost: ~10 extra cycles.
  - **Mitigation**: Interleave load-heavy code with compute to avoid I$ miss during D$ access.

---

## 2. Code Scheduling Rules

### 2.1 Forwarding Window

| Producer EX stage | Result available in | Consumer needs by | Scheduling rule                 |
|-------------------|---------------------|-------------------|---------------------------------|
| Cycle N (EX)      | Cycle N (end of EX) | Cycle N+1 (ID)    | 0 gap needed (EX→EX forwarding) |
| Cycle N (MEM)     | Cycle N (end of MEM)| Cycle N+1 (ID)    | 1 gap needed (MEM→EX forwarding)|
| Cycle N (WB)      | Cycle N (end of WB) | Cycle N+1 (ID)    | 2 gap needed (bypass/WB→EX)     |

```
Producer in EX, consumer in EX (gap 0):
  add x5, x4, x3    EX─┐
  sub x7, x5, x6    ID EX  ← forward result directly

Producer in MEM, consumer in EX (gap 1):
  lw  x5, 0(x4)     MEM─┐
  nop                    │ needed
  add x7, x5, x6    ID  EX ← forward from MEM

Producer in WB, consumer in EX (gap 2):
  lw  x5, 0(x4)     WB─────┐
  nop                       │
  nop                       │
  add x7, x5, x6    ID  EX ← forward/bypass from WB
```

### 2.2 Multi-Cycle Instruction Scheduling

**MUL (4 cycles):** The EX stage is locked for 4 cycles. IF and ID stall. Schedule 3 independent instructions **before** the MUL, not after — they can execute while MUL occupies EX only if already in flight.

```c
// Bad: everything stalls behind MUL
int c = a * b;   // MUL locks EX for 4 cycles; ID stalls
int d = x + y;   // waits 4+ cycles

// Good: issue independent work before MUL
int d = x + y;   // enters pipeline, completes
int e = *ptr;    // memory operation independent of ALU
int c = a * b;   // MUL now: d and e already past EX
```

**DIV (32 cycles):** Avoid division in hot paths. Use lookup tables for small divisors (e.g., divide by 10 via reciprocal multiplication), or restructure algorithms.

### 2.3 Cache Line Alignment

```
Address     I$ line (4 KB, 256 lines × 16 B)
───────────────────────────────────────────────
0x00000000  Line 0   → _start (good: aligned)
0x00000010  Line 1
0x00000020  Line 2
0x00000030  Line 3
```

- Align hot functions to 16 B: `-falign-functions=16` in tuning.mk
- Align loop headers to 16 B: `-falign-loops=16`
- If a tight loop crosses a 16 B boundary, it occupies **2 cache lines**, doubling the miss rate.
- **Check alignment**: `objdump -d | grep '<loop_label>:'` and verify `addr & 0xF == 0`.

---

## 3. Loop Optimization Patterns

### 3.1 Unrolling

Unroll by 4× to match 16 B cache line and amortize branch cost:

```c
// Original (branch every iteration, ~5-8% mispredict)
for (i = 0; i < n; i++) sum += a[i];

// Unrolled 4× (branch every 4 iterations)
for (i = 0; i < n - 3; i += 4) {
    sum += a[i] + a[i+1] + a[i+2] + a[i+3];
}
for (; i < n; i++) sum += a[i];
```

**Caution:** Don't unroll beyond 4× on lunahan_v1 — the I$ is only 4 KB. Beyond 4× unroll, the loop body spans >2 cache lines, and inter-loop conflict misses rise sharply.

### 3.2 Software Pipelining for 5-Stage Pipeline

For a 5-stage pipeline, the optimal software-pipelined schedule has 4 iterations in flight:

```
Iter 0: LD 0 | <gap> | ADD 0 | <gap> | ST 0
Iter 1:       | LD 1  | <gap> | ADD 1 | <gap> | ST 1
Iter 2:                | LD 2  | <gap> | ADD 2 | <gap> | ST 2
Iter 3:                         | LD 3  | <gap> | ADD 3 | <gap> | ST 3
```

This pattern eliminates all load-use stalls and keeps the pipeline full. For simple arithmetic loops (e.g., `sum += *ptr++`), unrolling 2× and interleaving loads with ALU ops is sufficient:

```c
// 2× unrolled + interleaved: 0 stall cycles
for (i = 0; i < n; i += 2) {
    int v0 = a[i];                  // load
    int v1 = a[i+1];                // load (no dependency on v0)
    sum0 += v0;                     // v0 forwarded from MEM→EX (1 gap, but v1 load fills it)
    sum1 += v1;                     // v1 forwarded
}
```

### 3.3 Loop-Invariant Code Motion

Hoist loop invariants manually when the invariant is a **load** — the compiler may not hoist loads due to aliasing uncertainty:

```c
// Before: loads limit every iteration
for (i = 0; i < n; i++)
    a[i] *= scale;      // lw scale from memory every iteration (5% D$ miss)

// After: hoist load
int s = scale;          // single load, 0.05 × expected miss = near 0
for (i = 0; i < n; i++)
    a[i] *= s;          // s is in register, 0-cycle access
```

### 3.4 Loop Nest Optimization

For nested loops on lunahan_v1:
- **Favor inner loops that fit in 1–2 I$ lines** (16–32 instructions).
- **Interchange loops** so the inner loop accesses contiguous memory (D$ line reuse).
- **Tiling** is typically unnecessary for 4 KB D$ — the working set usually fits entirely.

---

## 4. Cache-Friendly Data Layout

### 4.1 Struct Packing

```c
// Bad: 12 bytes, spans 2 cache lines if misaligned
struct sensor_bad {
    uint32_t timestamp;  // 4 B
    uint8_t  id;         // 1 B
    uint32_t value;      // 4 B + 3 B padding
    uint16_t flags;      // 2 B — now 12 B total
};

// Good: 8 bytes, fits in 1 cache line
struct sensor_good {
    uint32_t timestamp;  // 4 B
    uint32_t value;      // 4 B
    uint8_t  id;         // 1 B
    uint16_t flags;      // 2 B + 1 B padding = 12 B... still 12
};
// Better: 8 bytes by merging id/flags into a bitfield
struct sensor_best {
    uint32_t timestamp;  // 4 B
    uint32_t value;      // 4 B
    uint8_t  id;         // 1 B
    uint16_t flags;      // 2 B — use __attribute__((packed)) if needed
} __attribute__((packed, aligned(16)));
```

### 4.2 Array Alignment

Align arrays to 16 B to avoid crossing cache lines mid-element:

```c
int16_t samples[1024] __attribute__((aligned(16)));
```

For the 4 KB D$, two arrays that are both `N × 256` bytes apart (e.g., both at offset 0 modulo 256) will **conflict** on every access. This is the classic direct-mapped thrashing problem. Avoid it by:
- Padding arrays to odd sizes (e.g., 260 bytes instead of 256).
- OR placing arrays in different memory regions if possible.

### 4.3 Write-Back Awareness

The D$ is **write-back, write-allocate**. A write miss triggers:
1. Read the cache line from memory (fill).
2. Modify the target word.
3. Mark the line dirty.

This means a `memset` on a cold cache costs **read + write** bandwidth for every 16-byte line. Use `memset` only after data has been read or when you're about to write the entire line:

```c
// Inefficient: memset triggers write-allocate (read + write for each line)
memset(buf, 0, 4096);

// Efficient: fill buf with data from peripheral, then zero the remainder
read_from_uart(buf, actual_len);
memset(buf + actual_len, 0, 4096 - actual_len);  // only partial lines
```

---

## 5. Function Call Overhead Analysis

### 5.1 Cost Breakdown

| Operation           | Cycles | Notes                                       |
|---------------------|--------|---------------------------------------------|
| `jal func`          | 2      | BTB predicts target; 0 mispredict           |
| `jalr ra` (return)  | 2      | BTB predicts return; may mispredict         |
| `c.jal func`        | 2      | Compressed version, same cost, 1 byte saved |
| Save ra to stack    | 2      | `sw ra, offset(sp)` — store hit             |
| Restore ra          | 2+1    | `lw ra, offset(sp)` + 1 cycle load-use      |
| Save s0-s11         | 2 each | `sw` per callee-saved reg                   |
| Restore s0-s11      | 3 each | `lw` + load-use stall                       |

**Minimum non-leaf call cost:** ~12 cycles (save ra + jal + restore ra + jalr).
**Minimum leaf call (no stack frame):** ~4 cycles (jal + jalr).

### 5.2 Inlining Heuristic

Inline if:
- Function body ≤ 10 instructions AND called once.
- Function is a trivial getter/setter.
- The call overhead (12 cy) exceeds the function body cost.

Don't inline if:
- Function is >20 instructions — I$ bloat cancels the call savings.
- Function is called from 3+ locations — I$ lines evict each other.

---

## 6. C-to-RISC-V Code Pattern Mapping

### 6.1 Arithmetic

| C pattern            | Optimal RISC-V         | Cycles | Notes                                |
|----------------------|------------------------|--------|--------------------------------------|
| `x = a + b`          | `add rd, rs1, rs2`     | 1      | EX→EX forwarding if back-to-back     |
| `x = a * b` (small)  | `mul rd, rs1, rs2`     | 4      | Use shift+add if multiplying by constant |
| `x = a * 10`         | `slli t, a, 3; add t, a, t; add rd, t, t` | 3 | Faster than MUL (4 cy)              |
| `x = a / b`          | `div rd, rs1, rs2`     | 32     | Avoid in hot loops                   |
| `x = a / 8`          | `srai rd, rs1, 3`      | 1      | Signed shift (power-of-2 divisor)    |
| `x = a % 8`          | `andi rd, rs1, 7`      | 1      | Power-of-2 modulo                    |

### 6.2 Memory Access

| C pattern               | Optimal RISC-V              | Cycles  | Notes                                |
|-------------------------|------------------------------|---------|--------------------------------------|
| `x = *p`                | `lw rd, 0(rs1)`             | 2 (hit) | D$ hit: 1 cy + load-use: +1 if used  |
| `*p = x`                | `sw rs2, 0(rs1)`            | 1 (hit) | D$ write-hit: 1 cycle                |
| `x = p->field`          | `lw rd, OFFSET(rs1)`        | 2       | Immediate offset, 0-cost address calc|
| `x = arr[i]`             | `slli t, i, 2; add t, a0, t; lw rd, 0(t)` | 4 | Use `c.slli`+`c.add` for compressed |
| `*p++ = x`              | `sw rs2, 0(rs1); c.addi rs1, 4` | 2 | Post-increment pattern              |

### 6.3 Control Flow

| C pattern         | Optimal RISC-V         | Taken | Not-taken | Notes                        |
|-------------------|------------------------|-------|-----------|------------------------------|
| `if (a == b)`     | `beq a, b, label`      | 2     | 0         | BTB predicts after training  |
| `if (a < b)`      | `blt a, b, label`      | 2     | 0         | Signed comparison            |
| `for (i=0;i<n;i++)`| `addi; bne` at bottom | 0     | 2         | Do-while pattern; 2 cy on exit|
| `switch (x)`      | Jump table (jalr)      | 2     | —         | BTB indirect prediction      |
| `switch (x)` dense| `slli; add; lw; jalr`  | 6     | —         | Table at rodata              |

### 6.4 Bit Manipulation

| C pattern           | Optimal RISC-V         | Cycles | Notes                     |
|---------------------|------------------------|--------|---------------------------|
| `x & 0xFF`          | `andi rd, rs, 0xFF`    | 1      |                          |
| `x << n`            | `slli rd, rs, n`       | 1      |                          |
| `x & (1<<bit)`      | `slli t, rs, 31-bit; bltz t, label` | 2 | Branch on bit            |
| `x = y & -y`        | `neg t, rs; and rd, t, rs`| 2   | Isolate lowest set bit   |
| `x = __builtin_clz(v)` |Use table or bit-scan loop | 2-10 | No CLZ in RV32I          |

---

## 7. Benchmark-Driven Tuning Workflow

### 7.1 Profile → Correlate → Fix

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────┐
│ Build with   │────▶│ objdump + analyze│────▶│ Apply tuning │
│ tuning.mk    │     │ .lst file        │     │ rules        │
└──────────────┘     └──────────────────┘     └─────────────┘
         │                                            │
         │          ←────── iterate ──────────────    │
         └────────────────────────────────────────────┘
```

### 7.2 Crude Cycle-Count Profiling

With `mcycle` CSR and `delay_cycles` calibration:

```c
uint32_t start, end;
__asm__ volatile("csrr %0, mcycle" : "=r"(start));
function_under_test();
__asm__ volatile("csrr %0, mcycle" : "=r"(end));
uint32_t elapsed = end - start;
printf("Cycles: %lu\n", elapsed);
```

### 7.3 Interpreting Results

| Metric                     | Expected value | If worse, check...                   |
|----------------------------|----------------|--------------------------------------|
| memcpy 1 KB (aligned)      | ~300 cycles    | Load-use stalls, cache misses         |
| memset 1 KB                | ~260 cycles    | Write-allocate misses                 |
| strlen 100-char string     | ~35 cycles     | Alignment, word-at-a-time working     |
| Function call (leaf)       | 4 cycles       | BTB mispredict on return             |
| Loop (100 iterations)      | ~105 cycles    | Misaligned header, no do-while       |
| DIV operation              | 32 cycles      | Can't improve; restructure algorithm |

### 7.4 I$ Miss Analysis

```
objdump -d build/firmware.elf > build/firmware.lst
```

Count instructions in each hot function. If a function > 256 instructions (~1 KB, or 64 cache lines), it will thrash itself in the 4 KB I$ — split it into sub-functions.

### 7.5 D$ Miss Analysis

Instrument loads with address logging (if available via debug interface). Common fixes:
- Align struct arrays to 16 B.
- Pack structs to ≤ 16 B.
- Avoid interleaved access to arrays at 256 B offsets (cache conflict).

---

## 8. Quick Reference Card

```
┌──────────────────────────────────────────────────────────────────┐
│  lunahan_v1 PIPELINE TUNING CHEAT SHEET                         │
├──────────────────────────────────────────────────────────────────┤
│  Load-use gap:         1 instruction minimum                    │
│  Branch mispredict:    2 cycles (taken), 0 cycles (not-taken)   │
│  MUL latency:          4 cycles (stall IF/ID)                   │
│  DIV latency:          32 cycles (stall IF/ID)                  │
│  I$ line:              16 bytes (align functions + loops)       │
│  D$ line:              16 bytes (align arrays + structs)        │
│  D$ policy:            Write-back, write-allocate               │
│  BTB entries:          64 (2-bit saturating counters)           │
│  Compressed insns:     Use c.* for ~25% code size reduction     │
│  Optimal unroll:       4× (matches 16 B line)                   │
│  Function align:       16 bytes (-falign-functions=16)          │
│  Loop pattern:         Do-while (branch at bottom)              │
│  Struct target size:   ≤ 16 bytes (one D$ line)                 │
│  Array stride:         Avoid multiples of 256 B                 │
└──────────────────────────────────────────────────────────────────┘
```

---

## 9. Example: Optimizing a Hot Loop

### Before (naive)
```c
for (i = 0; i < 100; i++)
    sum += data[i] * coeff[i];
```
**Profile:** ~1400 cycles (14/iteration: 2 loads + MUL(4) + ADD + branch overhead).

### After (tuned)
```c
int s0 = 0, s1 = 0, s2 = 0, s3 = 0;
const int *d = data;
const int *c = coeff;
for (i = 0; i < 100; i += 4) {
    int v0 = d[0], v1 = d[1], v2 = d[2], v3 = d[3];  // 4 loads, DCache line hit
    int k0 = c[0], k1 = c[1], k2 = c[2], k3 = c[3];  // 4 loads
    s0 += v0 * k0;                                     // MUL (4 cy) — s1 calc fills gap
    s1 += v1 * k1;
    s2 += v2 * k2;
    s3 += v3 * k3;
    d += 4; c += 4;
}
sum = s0 + s1 + s2 + s3;
```
**Profile:** ~700 cycles (7/iteration: loads interleaved, MUL stalls partially hidden by parallel accumulators, 4× fewer branches).

Speedup: **2×** from scheduling + unrolling alone.
