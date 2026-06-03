# lunahan_v1 — GCC/LLVM optimization flags Makefile
# =============================================================================
# Target: RV32IMC, ilp32 ABI, 100 MHz (sky130)
# Pipeline: 5-stage in-order (IF/ID/EX/MEM/WB), forwarding, BTB
# Caches: 4 KB I$, 4 KB D$ (direct-mapped, 16 B line, write-back)
#
# Usage:
#   make -f sw/tuning.mk PROFILE=release
#   make -f sw/tuning.mk PROFILE=debug
# =============================================================================

PROFILE ?= release

# ---- Toolchain ----
CROSS    = riscv32-unknown-elf
CC       = $(CROSS)-gcc
AS       = $(CROSS)-as
LD       = $(CROSS)-ld
OBJCOPY  = $(CROSS)-objcopy
OBJDUMP  = $(CROSS)-objdump
SIZE     = $(CROSS)-size

# ---- Architecture & ABI ----
# -march=rv32imc: I=Base integer, M=Multiply/Divide, C=Compressed
#                  M-extension handled by hardware (MUL=4cyc, DIV=32cyc).
#                  C-extension reduces code size by ~25%, lowering I$ pressure.
# -mabi=ilp32:    int=32, long=32, pointer=32, no hardware float.
#                  Soft-float avoids FPU register save/restore overhead.
ARCH_FLAGS = -march=rv32imc -mabi=ilp32

# ---- Common Flags ----
# -ffreestanding:      No standard library, no main() special-casing.
#                      Needed for bare-metal crt0-based programs.
# -nostdlib:           Don't link libc (use optimized_lib.c instead).
# -fno-builtin:        Disable compiler builtins that assume libc.
#                       Let the compiler generate inline code or call
#                       our optimized routines.
# -fno-stack-protector: No canary — saves 2 instructions per function entry.
# -ffunction-sections:  Place each function in separate ELF section,
#                       enabling linker GC (--gc-sections) to drop
#                       unused code, reducing I$ footprint.
# -fdata-sections:      Same for data — reduces D$ footprint.
COMMON_FLAGS = -ffreestanding -nostdlib -fno-builtin \
               -fno-stack-protector -ffunction-sections -fdata-sections

# ---- Linker ----
LINKER_SCRIPT = sw/link.ld
# --gc-sections:           Remove unused sections (reduces binary size).
# -Map output.map:         Generate symbol map for analysis.
# --print-memory-usage:    Show memory region utilization.
LDFLAGS = -T $(LINKER_SCRIPT) -nostdlib -Wl,--gc-sections \
          -Wl,-Map=build/output.map -Wl,--print-memory-usage

# ============================================================================
#  RELEASE Profile — maximize performance on lunahan_v1 pipeline
# ============================================================================
# -O2:          Classic optimization level. Balances speed and code size.
#               -O3 may unroll too aggressively, causing I$ thrashing on 4 KB I$.
# -fno-tree-vectorize:
#               Disable auto-vectorization. RV32IMC has no SIMD, so
#               vectorization attempts produce scalar code with extra
#               overhead (loop peeling, versioning) that wastes I$ space.
# -fno-move-loop-invariants:
#               In a 5-stage pipeline with limited forwarding, hoisting
#               loop invariants can create long live ranges that increase
#               register pressure and cause spills to D$. For small loops
#               (the typical case on a 4 KB I$), recomputing is often cheaper
#               than the load-use stall from a spill reload.
# -fno-schedule-insns:
#               Disable GCC's instruction scheduling pass. The lunahan_v1
#               pipeline is simple enough that the compiler's generic
#               scheduling model may do more harm than good. We schedule
#               critical sections by hand in assembly (crt0.s).
# -falign-functions=16:
#               Align function entry points to 16 bytes (cache line boundary).
#               Reduces I$ conflict misses when hot functions share a
#               cache set. Each function that crosses a 16 B boundary
#               uses one extra cache line.
# -falign-jumps=16:
#               Align branch targets to 16 bytes. Loop headers and
#               if/else targets that align to cache lines reduce the
#               cold-start miss count.
# -falign-loops=16:
#               Align loop entries to 16 bytes. Each loop header that
#               fits in a single cache line avoids mid-loop fetch stalls.
# -fomit-frame-pointer:
#               Free up s0 (fp) for general use. Saves prologue/epilogue
#               instructions (addi sp + sw fp). Register pressure benefit
#               is significant on a 32-register file.
# -frename-registers:
#               Break false dependencies, giving the pipeline scheduler
#               more freedom to reorder instructions for forwarding.
# -finline-functions:
#               Inline small functions (<= 10 instructions). Eliminates
#               JAL/JALR overhead (call=2cy + ret=2cy + mispredict=2cy).
#               Careful: too much inlining bloats I$. We set a small
#               inlining threshold to balance.
# --param max-inline-insns-single=20:
#               Inline at most 20 instructions per call site.
# -fweb:
#               Partition variables into live-range "webs". Helps GCC
#               schedule stores and loads with better spacing for forwarding.
# -ftree-reassoc:
#               Reassociate arithmetic to reduce dependency chain depth.
#               Shorter dependency chains = fewer forwarding stalls.
# -fno-common:
#               Allocate globals in .bss/.data explicitly. Prevents
#               COMMON blocks that the linker may place sub-optimally.
RELEASE_CFLAGS = -O2 \
                 -fno-tree-vectorize \
                 -fno-move-loop-invariants \
                 -fno-schedule-insns \
                 -falign-functions=16 \
                 -falign-jumps=16 \
                 -falign-loops=16 \
                 -fomit-frame-pointer \
                 -frename-registers \
                 -finline-functions \
                 --param max-inline-insns-single=20 \
                 -fweb \
                 -ftree-reassoc \
                 -fno-common \
                 -DNDEBUG

# ============================================================================
#  DEBUG Profile — minimal optimization, debuggable
# ============================================================================
# -Og:          Optimize debugging experience. Keeps variables in scope,
#               avoids code motion that confuses stepping.
#               Still applies basic optimizations (no -O0) so execution
#               speed is within ~50% of -O2.
# -g3:          Include macro definitions in debug info.
# -fno-omit-frame-pointer:
#               Keep frame pointer for reliable backtraces.
DEBUG_CFLAGS = -Og -g3 -fno-omit-frame-pointer -DDEBUG

# ---- Select profile ----
ifeq ($(PROFILE),release)
  CFLAGS = $(ARCH_FLAGS) $(COMMON_FLAGS) $(RELEASE_CFLAGS)
else ifeq ($(PROFILE),debug)
  CFLAGS = $(ARCH_FLAGS) $(COMMON_FLAGS) $(DEBUG_CFLAGS)
else
  $(error Unknown PROFILE "$(PROFILE)". Use "release" or "debug".)
endif

# ---- Source files ----
C_SRCS   = main.c sw/optimized_lib.c
ASM_SRCS = sw/crt0.s

C_OBJS   = $(C_SRCS:%.c=build/%.o)
ASM_OBJS = $(ASM_SRCS:%.s=build/%.o)
OBJS     = $(ASM_OBJS) $(C_OBJS)

# ---- Build targets ----
.PHONY: all clean compile_flags

all: build/firmware.elf build/firmware.bin build/firmware.lst

build/firmware.elf: $(OBJS) $(LINKER_SCRIPT)
	@mkdir -p build
	$(LD) $(ARCH_FLAGS) $(LDFLAGS) $(OBJS) -o $@
	$(SIZE) $@

build/firmware.bin: build/firmware.elf
	$(OBJCOPY) -O binary $< $@

build/firmware.lst: build/firmware.elf
	$(OBJDUMP) -d -S $< > $@

build/%.o: %.s
	@mkdir -p $(dir $@)
	$(AS) $(ARCH_FLAGS) -march=rv32imc -c $< -o $@

build/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

compile_flags:
	@echo "=== lunahan_v1 compile flags ($(PROFILE)) ==="
	@echo "ARCH_FLAGS  = $(ARCH_FLAGS)"
	@echo "CFLAGS      = $(CFLAGS)"
	@echo "LDFLAGS     = $(LDFLAGS)"
	@echo ""
	@echo "=== Flag rationale ==="
	@echo "-march=rv32imc        : RV32I base + M (mul/div) + C (compressed)"
	@echo "                          MUL=4cy, DIV=32cy in hardware"
	@echo "                          C reduces I$ pressure by ~25%"
	@echo "-mabi=ilp32            : 32-bit int/long/ptr, soft-float"
	@echo "-O2                    : Speed with controlled code growth"
	@echo "-fno-tree-vectorize    : No SIMD → vectorization is dead code"
	@echo "-falign-*=16           : Align to 16B I$ line, reduce conflict misses"
	@echo "-fomit-frame-pointer   : Free s0, save prologue/epilogue"
	@echo "-ffunction-sections    : Enable --gc-sections (drop dead code)"
	@echo "-fweb                  : Split live ranges for better scheduling"

clean:
	rm -rf build

# ---- Quick analysis targets ----
# Disassemble and count instructions per function
profile_functions: build/firmware.lst
	@echo "=== Top functions by instruction count ==="
	@awk '/^[0-9a-f]+ <[^>]+>:/{fn=$$2} /^[[:space:]]+[0-9a-f]+:/{cnt[fn]++} END{for(f in cnt) print cnt[f], f}' $< | sort -rn | head -20
