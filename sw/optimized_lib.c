/*
 * lunahan_v1 — Hand-optimized C library routines
 *
 * Pipeline tuning for 5-stage in-order (IF/ID/EX/MEM/WB):
 *   - Forwarding: EX→EX (0-cycle), MEM→EX (1-cycle stall), WB→EX (2-cycle)
 *   - Load-use penalty: 1 stall cycle if load result consumed next instruction
 *   - Branch mispredict penalty: 2 cycles
 *   - Multi-cycle: MUL=4, DIV=32
 *   - DCache: 4 KB direct-mapped, 16 B line, write-back
 *
 * Strategies used:
 *   - Separate load from use by >=1 instruction
 *   - Unroll loops by 4× to amortize branch cost
 *   - Align data to 16 B to exploit spatial locality in cache line
 *   - Use word-aligned accesses where possible (avoid unaligned penalty)
 *   - Minimize pointer updates that create dependencies
 */

#include <stddef.h>
#include <stdint.h>

/* --------------------------------------------------------------------------
 * memcpy — optimized aligned block copy
 *
 * Pipeline strategy:
 *   - 16-byte unrolled loop (matches DCache line size)
 *   - Batch 4 loads before 4 stores to create enough scheduling distance
 *     between lw and sw so that forwarding covers load latency.
 *   - 4-word group: lw,lw,lw,lw → sw,sw,sw,sw gives 3+ cycles between
 *     first lw and first sw — well past the load-use stall window.
 *   - Fallback byte copy for unaligned or short transfers.
 */
void *memcpy(void *restrict dest, const void *restrict src, size_t n)
{
    uint8_t       *d8  = (uint8_t       *)dest;
    const uint8_t *s8  = (const uint8_t *)src;
    uint32_t      *d32 = (uint32_t      *)dest;
    const uint32_t *s32 = (const uint32_t *)src;

    if (n == 0) return dest;

    /* Head: align destination to 4-byte boundary (byte copy).
     * Branch prediction: not-taken path is 0-cost if src already aligned. */
    if (((uintptr_t)d8 & 3) != 0) {
        size_t head = 4 - ((uintptr_t)d8 & 3);
        if (head > n) head = n;
        for (size_t i = 0; i < head; i++)
            *d8++ = *s8++;
        n -= head;
    }

    /* 16-byte unrolled word copy.
     * Load 4 words (16 B = one cache line fill), then store them.
     * This grouping exploits the DCache's 16 B line: if the load misses,
     * the entire line is filled, making the next 3 loads hit. */
    size_t chunks = n >> 4;                   /* n / 16 */
    if (chunks > 0 && ((uintptr_t)s8 & 3) == 0) {
        d32 = (uint32_t *)d8;
        s32 = (const uint32_t *)s8;
        for (size_t i = 0; i < chunks; i++) {
            uint32_t w0 = s32[0];              /* load: IF/ID/EX/MEM/WB */
            uint32_t w1 = s32[1];              /* load: pipeline advances */
            uint32_t w2 = s32[2];              /* load: DCache line hit */
            uint32_t w3 = s32[3];
            d32[0] = w0;                       /* store: w0 ready (WB→...) */
            d32[1] = w1;                       /* store: w1 ready */
            d32[2] = w2;
            d32[3] = w3;
            d32 += 4;
            s32 += 4;
        }
        d8 = (uint8_t *)d32;
        s8 = (const uint8_t *)s32;
        n &= 15;
    }

    /* 4-byte tail word copy */
    if (n >= 4 && ((uintptr_t)s8 & 3) == 0) {
        size_t words = n >> 2;
        d32 = (uint32_t *)d8;
        s32 = (const uint32_t *)s8;
        for (size_t i = 0; i < words; i++)
            *d32++ = *s32++;
        d8 = (uint8_t *)d32;
        s8 = (const uint8_t *)s32;
        n &= 3;
    }

    /* Byte tail */
    while (n--)
        *d8++ = *s8++;

    return dest;
}

/* --------------------------------------------------------------------------
 * memset — optimized aligned buffer fill
 *
 * Pipeline strategy:
 *   - Replicate byte value into 32-bit word once (avoid reload).
 *   - 16-byte unrolled store loop (DCache-line-friendly).
 *   - Stores don't create load-use hazards on lunahan_v1 (no read-after-write
 *     forwarding needed), so we can store back-to-back freely.
 */
void *memset(void *dest, int c, size_t n)
{
    uint8_t  *d8  = (uint8_t *)dest;
    uint32_t *d32;
    uint32_t  word;

    if (n == 0) return dest;

    /* Build 32-bit word: 0xCCCCCCCC */
    uint8_t byte = (uint8_t)c;
    word = (uint32_t)byte
         | ((uint32_t)byte << 8)
         | ((uint32_t)byte << 16)
         | ((uint32_t)byte << 24);

    /* Head: align to 4-byte boundary */
    if (((uintptr_t)d8 & 3) != 0) {
        size_t head = 4 - ((uintptr_t)d8 & 3);
        if (head > n) head = n;
        for (size_t i = 0; i < head; i++)
            *d8++ = byte;
        n -= head;
    }

    /* 16-byte unrolled store (4 words per iteration).
     * No load-use hazard: purely stores, pipeline can issue them
     * back-to-back without stalls. Unrolling 4× reduces branch count by 4×,
     * saving mispredict cycles for in-loop branches. */
    d32 = (uint32_t *)d8;
    size_t chunks = n >> 4;
    for (size_t i = 0; i < chunks; i++) {
        d32[0] = word;
        d32[1] = word;
        d32[2] = word;
        d32[3] = word;
        d32 += 4;
    }
    n &= 15;

    /* 4-byte tail */
    if (n >= 4) {
        size_t words = n >> 2;
        for (size_t i = 0; i < words; i++)
            *d32++ = word;
        d8 = (uint8_t *)d32;
        n &= 3;
    } else {
        d8 = (uint8_t *)d32;
    }

    /* Byte tail */
    while (n--)
        *d8++ = byte;

    return dest;
}

/* --------------------------------------------------------------------------
 * strlen — word-at-a-time string length
 *
 * Pipeline strategy:
 *   - Word-at-a-time scan uses a bit-twiddling "has zero byte" trick.
 *   - Each word load creates a load-use dependency on the haszero check.
 *     To mitigate: interleave the next load address computation (addi)
 *     between lw and haszero to give the load time to complete.
 *   - For short strings (<4 chars), the aligned-byte pre-scan finishes fast.
 */
size_t strlen(const char *s)
{
    const char *p = s;

    /* Align to 4-byte boundary (byte scan).
     * For the common case of already-aligned strings, the branch
     * is not-taken (0-cycle penalty). */
    while (((uintptr_t)p & 3) != 0) {
        if (*p == '\0') return (size_t)(p - s);
        p++;
    }

    const uint32_t *w = (const uint32_t *)p;

    /* haszero: detect a zero byte in a 32-bit word.
     * Subtracts 0x01010101 and checks for 0x80808080 bits. */
    for (;;) {
        uint32_t val = *w;
        /* Schedule w+1 compute here to separate from the load */
        w++;
        /* haszero(v) = ((v - 0x01010101) & ~v & 0x80808080) */
        uint32_t sub = val - 0x01010101u;
        if ((sub & ~val & 0x80808080u) != 0) {
            /* Found a zero byte; find which byte.
             * Re-wind w to this word's start. */
            const uint8_t *bp = (const uint8_t *)(w - 1);
            if (bp[0] == '\0') return (size_t)(bp + 0 - s);
            if (bp[1] == '\0') return (size_t)(bp + 1 - s);
            if (bp[2] == '\0') return (size_t)(bp + 2 - s);
            return (size_t)(bp + 3 - s);
        }
    }
}

/* --------------------------------------------------------------------------
 * strcmp — optimized string comparison
 *
 * Pipeline strategy:
 *   - Word-at-a-time comparison with haszero trick.
 *   - Both loads (s1, s2) are issued before comparison to allow the
 *     DCache to pipeline the memory accesses.
 *   - Early-out on mismatch: check XOR result before haszero to avoid
 *     unnecessary zero-detection when strings differ.
 */
int strcmp(const char *s1, const char *s2)
{
    /* Align to 4-byte if both pointers share alignment */
    while (((uintptr_t)s1 & 3) != 0) {
        unsigned char c1 = (unsigned char)*s1++;
        unsigned char c2 = (unsigned char)*s2++;
        if (c1 != c2) return (int)c1 - (int)c2;
        if (c1 == 0) return 0;
    }

    const uint32_t *w1 = (const uint32_t *)s1;
    const uint32_t *w2 = (const uint32_t *)s2;

    for (;;) {
        uint32_t a = *w1;
        uint32_t b = *w2;
        w1++;
        w2++;

        /* If words differ, find the differing byte */
        if (a != b) {
            const uint8_t *bp1 = (const uint8_t *)(w1 - 1);
            const uint8_t *bp2 = (const uint8_t *)(w2 - 1);
            for (int i = 0; i < 4; i++) {
                if (bp1[i] != bp2[i])
                    return (int)(unsigned char)bp1[i] - (int)(unsigned char)bp2[i];
                if (bp1[i] == 0)
                    return 0;
            }
        }

        /* haszero(a): check if any byte in a is zero */
        uint32_t sub = a - 0x01010101u;
        if ((sub & ~a & 0x80808080u) != 0)
            return 0;
    }
}

/* --------------------------------------------------------------------------
 * delay_cycles — precise cycle-count delay
 *
 * Pipeline strategy:
 *   - NOP-based loop: each iteration is 1 NOP (1 cycle).
 *   - For large counts, use a counted loop. For small counts (<10),
 *     we emit inline NOPs to avoid branch overhead.
 *   - The loop itself costs ~3 cycles per iteration (addi + bnez).
 *     To get exactly `cycles` of delay, subtract loop overhead.
 *
 *   Target: 100 MHz → 1 cycle = 10 ns.
 *   Accuracy: ±2 cycles due to branch mispredict on final iteration.
 */
void delay_cycles(uint32_t cycles)
{
    if (cycles == 0) return;

    /* Small delays: unrolled NOP chain. Each NOP = 1 cycle.
     * No branches → zero mispredict penalty. */
    if (cycles <= 8) {
        /* Jump table via computed goto would be ideal; use if/else chain
         * which compiles to a series of conditional branches.
         * Worst case: ~2 mispredicts for 8 cycles */
        if (cycles >= 8) __asm__ volatile("nop");
        if (cycles >= 7) __asm__ volatile("nop");
        if (cycles >= 6) __asm__ volatile("nop");
        if (cycles >= 5) __asm__ volatile("nop");
        if (cycles >= 4) __asm__ volatile("nop");
        if (cycles >= 3) __asm__ volatile("nop");
        if (cycles >= 2) __asm__ volatile("nop");
        /* cycles >= 1 is always true here */
        __asm__ volatile("nop");
        return;
    }

    /* Large delays: counted NOP loop.
     * Overhead per iteration: c.addi (1 cycle) + c.bnez (1 cycle taken,
     * 2 cycles mispredict on exit). ~3 cycles total.
     * Subtract 2 for init, divide by 3 for per-iteration cost.
     *
     * We use a volatile asm block to prevent the compiler from
     * optimizing away the delay loop. */
    uint32_t count = (cycles > 3) ? ((cycles - 2) / 3) : 1;

    __asm__ volatile(
        "1:                          \n"
        "   c.addi %0, -1            \n"
        "   c.bnez %0, 1b            \n"
        : "+r"(count)
        :
        : "memory"
    );
}

/* --------------------------------------------------------------------------
 * memmove — overlapping copy
 *
 * Pipeline strategy:
 *   - If dest <= src: forward copy (identical to memcpy).
 *   - If dest > src: backward copy from end, reading 4 bytes at a time.
 *   - Same scheduling rules as memcpy for the forward path.
 */
void *memmove(void *dest, const void *src, size_t n)
{
    if (n == 0) return dest;

    uint8_t       *d8 = (uint8_t       *)dest;
    const uint8_t *s8 = (const uint8_t *)src;

    if (d8 <= s8) {
        /* Forward copy — delegate to memcpy logic */
        return memcpy(dest, src, n);
    }

    /* Backward copy */
    d8 += n;
    s8 += n;

    /* Byte tail to align */
    while (n > 0 && ((uintptr_t)d8 & 3) != 0) {
        d8--;
        s8--;
        *d8 = *s8;
        n--;
    }

    /* Backward word copy, 4x unrolled */
    uint32_t      *d32 = (uint32_t      *)d8;
    const uint32_t *s32 = (const uint32_t *)s8;
    size_t chunks = n >> 4;
    for (size_t i = 0; i < chunks; i++) {
        d32--;
        s32--;
        uint32_t w3 = s32[0];
        uint32_t w2 = s32[-1];
        uint32_t w1 = s32[-2];
        uint32_t w0 = s32[-3];
        d32[0]  = w3;
        d32[-1] = w2;
        d32[-2] = w1;
        d32[-3] = w0;
        d32 -= 3;
        s32 -= 3;
    }
    n &= 15;
    d8 = (uint8_t *)d32;
    s8 = (const uint8_t *)s32;

    /* Backward byte tail */
    while (n > 0) {
        d8--;
        s8--;
        *d8 = *s8;
        n--;
    }

    return dest;
}

/* --------------------------------------------------------------------------
 * __mulsi3 / __divsi3 / __modsi3 — stubs for -march=rv32imc
 *
 * Since lunahan_v1 implements M-extension in hardware, these should
 * never be called. Included as weak fallbacks for link safety.
 */

__attribute__((weak))
int __mulsi3(int a, int b) { return a * b; }

__attribute__((weak))
unsigned int __udivsi3(unsigned int a, unsigned int b) { return a / b; }

__attribute__((weak))
int __divsi3(int a, int b) { return a / b; }

__attribute__((weak))
unsigned int __umodsi3(unsigned int a, unsigned int b) { return a % b; }

__attribute__((weak))
int __modsi3(int a, int b) { return a % b; }
