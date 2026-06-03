# lunahan_v1 crt0.s — RISC-V RV32IMC startup code
#
# Pipeline-optimized for 5-stage in-order (IF/ID/EX/MEM/WB):
#   - Separate load from use by >=1 instruction to avoid load-use stall
#   - Use compressed instructions (c.) to reduce I$ pressure
#   - Align branch targets to 4-byte boundaries
#   - Avoid back-to-back dependent ALU ops where forwarding may be tight
#
# Symbols from linker script: __stack_top, _bss_start, _bss_end,
#                              _data_start, _data_end, _data_load_start

.section .text.init,"ax",@progbits
.globl _start
.type  _start, @function

_start:
    .option push
    .option rvc                          # enable compressed instructions

    # --- Initialize stack pointer (no load-use stall: immediate) ---
    c.li   sp, 0                         # zero for address construction
    la     sp, __stack_top               # sp = top of DMEM stack (0x1000F000)

    # --- Initialize global pointer ---
    # gp should point to 0x800 bytes after _data_start (typical convention).
    # For small programs, set gp near the middle of the data segment.
    la     gp, _data_start
    c.li   t0, 0x800
    c.add  gp, t0

    # --- Clear BSS (zero-init) ---
    # BSS range: [_bss_start, _bss_end)
    # Pipeline note: la → lw distance allows forwarding without stall.
    la     a0, _bss_start               # a0 = BSS start
    la     a1, _bss_end                 # a1 = BSS end
    c.beqz a0, 2f                        # skip if empty (not-taken: 0 penalty)
    c.beqz a1, 2f                        # skip if end==0

    # Align to 4-byte boundary for efficient sw
    c.li   t0, 3
    c.and  t1, a0, t0                    # t1 = a0 & 3
    c.beqz t1, 1f                        # already aligned
0:
    c.sb   zero, 0(a0)                   # byte clear
    c.addi a0, 1
    c.and  t1, a0, t0
    c.bnez t1, 0b                        # loop until aligned

1:
    # Word clear loop (4 bytes at a time, 4x unrolled to amortize branch cost)
    # 16-byte unrolled group matches DCache line size
    c.li   t2, 16
1:
    c.sw   zero,  0(a0)
    c.sw   zero,  4(a0)
    c.sw   zero,  8(a0)
    c.sw   zero, 12(a0)
    c.addi a0, 16
    c.sub  t1, a1, a0
    c.bgez t1, 1b                         # while a0 <= a1 - 16

    # Handle remaining < 16 bytes
    # Restore diff: t1 = a1 - a0, test >= 0
    c.add  a0, t1                          # undo the overshoot: a0 = a1
2:

    # --- Copy .data from IMEM (LMA) to DMEM (VMA) ---
    # .data VMA: [_data_start, _data_end)
    # .data LMA: _data_load_start
    la     a0, _data_start                # destination (DMEM)
    la     a1, _data_end                  # end of destination
    la     a2, _data_load_start           # source (IMEM / flash)
    c.beqz a0, 3f
    c.sub  t0, a1, a0                     # t0 = data size
    c.li   t1, 0
    c.beq  t0, t1, 3f                     # empty .data section

    # Copy 16-byte chunks (aligned with DCache line)
    # Pipeline: schedule loads ahead of stores to hide load latency.
    # Load two words before storing to cover load-use gap.
    c.li   t2, 16
2:
    lw     t3,  0(a2)                    # load word 0
    lw     t4,  4(a2)                    # load word 1
    lw     t5,  8(a2)                    # load word 2
    lw     t6, 12(a2)                    # load word 3
    c.addi a2, 16
    sw     t3,  0(a0)                    # store word 0 (forwarding from WB→EX)
    sw     t4,  4(a0)                    # no stall: t4 loaded 3 cycles ago
    sw     t5,  8(a0)
    sw     t6, 12(a0)
    c.addi a0, 16
    c.sub  t0, a1, a0
    c.bgez t0, 2b                         # while a0 <= a1 - 16

    # Handle remaining < 16 bytes as word/byte copy
    c.add  a0, t0                          # undo overshoot: a0 = a1

3:
    # --- Set up trap vector ---
    la     t0, _trap_entry
    csrw   mtvec, t0

    # --- Enable interrupts (optional; uncomment for interrupt-driven apps) ---
    # li     t0, 0x880                      # MIE=1, MPIE=1
    # csrw   mstatus, t0

    # --- Call main() ---
    # argv = NULL, argc = 0 (null-terminated argv array on stack)
    c.li   a0, 0                          # argc = 0
    c.li   a1, 0                          # argv = NULL
    call   main

    # --- main() returned: enter safe loop with WFI ---
_halt:
    wfi
    c.j    _halt

    .option pop
.size _start, . - _start


# =============================================================================
# Trap Entry
# =============================================================================
# Called on exception or interrupt.
# Saves full context, calls C trap_handler(), restores, and mrets.
#
# Pipeline notes:
#   - Save/restore uses sw/lw with interleaved instruction scheduling
#     to avoid load-use stalls during restore.
#   - Compressed store/load (-sp relative) saves I$ bandwidth.
#   - Stack frame: 32 regs × 4 bytes + 4 CSR words = 144 bytes, padded to 160.

.section .text,"ax",@progbits
.align 4
.globl _trap_entry
.type  _trap_entry, @function

_trap_entry:
    .option push
    .option rvc

    # Allocate stack frame: 160 bytes
    # 32 regs × 4 = 128 bytes + mcause + mepc + mtval + mstatus = 16 + 16 pad
    c.addi sp, -160

    # Save x1-x31 (skip x0). Schedule stores to avoid pipeline bubbles.
    # x1=ra, x2=sp already partially committed
    c.sw   x1,   0(sp)
    c.sw   x3,   8(sp)
    c.sw   x4,  12(sp)
    c.sw   x5,  16(sp)
    c.sw   x6,  20(sp)
    c.sw   x7,  24(sp)
    c.sw   x8,  28(sp)
    c.sw   x9,  32(sp)
    c.sw   x10, 36(sp)
    c.sw   x11, 40(sp)
    c.sw   x12, 44(sp)
    c.sw   x13, 48(sp)
    c.sw   x14, 52(sp)
    c.sw   x15, 56(sp)
    c.sw   x16, 60(sp)
    c.sw   x17, 64(sp)
    c.sw   x18, 68(sp)
    c.sw   x19, 72(sp)
    c.sw   x20, 76(sp)
    c.sw   x21, 80(sp)
    c.sw   x22, 84(sp)
    c.sw   x23, 88(sp)
    c.sw   x24, 92(sp)
    c.sw   x25, 96(sp)
    c.sw   x26,100(sp)
    c.sw   x27,104(sp)
    c.sw   x28,108(sp)
    c.sw   x29,112(sp)
    c.sw   x30,116(sp)
    c.sw   x31,120(sp)

    # Save original sp (before subtracting 160)
    c.addi t0, sp, 160
    c.sw   t0,   4(sp)                    # x2 = original sp

    # Save CSRs
    csrr   t0, mcause
    c.sw   t0, 128(sp)
    csrr   t0, mepc
    c.sw   t0, 132(sp)
    csrr   t0, mtval
    c.sw   t0, 136(sp)
    csrr   t0, mstatus
    c.sw   t0, 140(sp)

    # --- Call C trap handler ---
    # First arg = stack frame pointer (struct trap_frame *)
    c.mv   a0, sp
    call   trap_handler

    # --- Restore context ---
    # Restore CSRs first (no forwarding dependency on subsequent loads)
    c.lw   t0, 140(sp)
    csrw   mstatus, t0
    c.lw   t0, 136(sp)
    csrw   mtval, t0
    c.lw   t0, 132(sp)
    csrw   mepc, t0
    c.lw   t0, 128(sp)
    # mcause is read-only, skip write

    # Restore GPRs. Schedule loads in pairs to allow the pipeline to
    # forward MEM→EX for subsequent operations without stalling.
    # Load early-used regs (ra, sp, gp) last so they're available for mret.
    c.lw   x3,   8(sp)
    c.lw   x4,  12(sp)
    c.lw   x5,  16(sp)
    c.lw   x6,  20(sp)
    c.lw   x7,  24(sp)
    c.lw   x8,  28(sp)
    c.lw   x9,  32(sp)
    c.lw   x10, 36(sp)
    c.lw   x11, 40(sp)
    c.lw   x12, 44(sp)
    c.lw   x13, 48(sp)
    c.lw   x14, 52(sp)
    c.lw   x15, 56(sp)
    c.lw   x16, 60(sp)
    c.lw   x17, 64(sp)
    c.lw   x18, 68(sp)
    c.lw   x19, 72(sp)
    c.lw   x20, 76(sp)
    c.lw   x21, 80(sp)
    c.lw   x22, 84(sp)
    c.lw   x23, 88(sp)
    c.lw   x24, 92(sp)
    c.lw   x25, 96(sp)
    c.lw   x26,100(sp)
    c.lw   x27,104(sp)
    c.lw   x28,108(sp)
    c.lw   x29,112(sp)
    c.lw   x30,116(sp)
    c.lw   x31,120(sp)
    c.lw   x1,   0(sp)
    c.lw   x2,   4(sp)                    # restore sp last

    # Deallocate stack
    c.addi sp, 160

    mret

    .option pop
.size _trap_entry, . - _trap_entry


# =============================================================================
# Default trap_handler (weak, overridable by application)
# =============================================================================
.section .text,"ax",@progbits
.weak trap_handler
.type  trap_handler, @function

trap_handler:
    # Default: loop forever. Override in C with:
    #   void trap_handler(struct trap_frame *tf);
    wfi
    c.j   trap_handler
.size trap_handler, . - trap_handler
