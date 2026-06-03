/* lunahan_v1 linker script
 * GCC: -march=rv32imc -mabi=ilp32 -T sw/link.ld
 *
 * Memory layout:
 *   IMEM:  0x00000000 - 0x0000FFFF (64 KB)
 *   CLINT: 0x02000000 - 0x0200FFFF
 *   PLIC:  0x0C000000 - 0x0FFFFFFF
 *   DMEM:  0x10000000 - 0x1000FFFF (64 KB)
 *   MMIO:  0x20000000 - 0x2FFFFFFF
 *
 * Stack at 0x1000F000, grows downward (4 KB).
 * Heap grows upward from end of BSS to stack limit.
 */

OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
  IMEM  (rx)  : ORIGIN = 0x00000000, LENGTH = 0x00010000
  CLINT (rw)  : ORIGIN = 0x02000000, LENGTH = 0x00010000
  PLIC  (rw)  : ORIGIN = 0x0C000000, LENGTH = 0x04000000
  DMEM  (rw)  : ORIGIN = 0x10000000, LENGTH = 0x00010000
  MMIO  (rw)  : ORIGIN = 0x20000000, LENGTH = 0x10000000
}

PHDRS
{
  text   PT_LOAD FLAGS(5);   /* R+X */
  rodata PT_LOAD FLAGS(4);   /* R   */
  data   PT_LOAD FLAGS(6);   /* R+W */
}

SECTIONS
{
  /* ---- TEXT: placed in IMEM at 0x00000000 ---- */
  .text : ALIGN(16)
  {
    _text_start = .;
    *(.text.init)       /* crt0 _start */
    *(.text .text.*)
    *(.text.unlikely .text.unlikely.*)
    _text_end = .;
  } > IMEM :text

  /* ---- RODATA: follows TEXT in IMEM ---- */
  .rodata : ALIGN(16)
  {
    _rodata_start = .;
    *(.rodata .rodata.*)
    *(.srodata .srodata.*)
    *(.gnu.linkonce.r.*)
    _rodata_end = .;
  } > IMEM :rodata

  /* ---- DATA: load address in IMEM, VMA in DMEM ---- */
  .data : ALIGN(16)
  {
    _data_start = .;
    *(.data .data.*)
    *(.sdata .sdata.*)
    *(.gnu.linkonce.d.*)
    . = ALIGN(4);
    _data_end = .;
  } > DMEM AT > IMEM :data

  _data_load_start = LOADADDR(.data);

  /* ---- BSS: follows .data in DMEM ---- */
  .bss : ALIGN(16)
  {
    _bss_start = .;
    *(.bss .bss.*)
    *(.sbss .sbss.*)
    *(.gnu.linkonce.b.*)
    *(COMMON)
    . = ALIGN(4);
    _bss_end = .;
  } > DMEM

  /* ---- HEAP: grows upward from end of BSS ---- */
  .heap (NOLOAD) : ALIGN(16)
  {
    __heap_start = .;
    . = . + 0x0000C000;          /* 48 KB heap (max, adjust as needed) */
    __heap_end = .;
  } > DMEM

  /* ---- STACK: top of DMEM, grows downward ---- */
  .stack (NOLOAD) : ALIGN(16)
  {
    . = 0x1000F000;              /* Absolute stack top address */
    __stack_top = .;
  } > DMEM

  /* ---- CLINT: mapped at 0x02000000 ---- */
  .clint (NOLOAD) : ALIGN(16)
  {
    *(.clint .clint.*)
  } > CLINT

  /* ---- PLIC: mapped at 0x0C000000 ---- */
  .plic (NOLOAD) : ALIGN(16)
  {
    *(.plic .plic.*)
  } > PLIC

  /* ---- MMIO: mapped at 0x20000000 ---- */
  .mmio (NOLOAD) : ALIGN(16)
  {
    *(.mmio .mmio.*)
  } > MMIO

  /* Discard unwanted sections */
  /DISCARD/ :
  {
    *(.comment)
    *(.note .note.*)
    *(.eh_frame)
    *(.eh_frame_hdr)
  }
}

/* Exported symbols for crt0 and runtime */
PROVIDE(__stack_top = __stack_top);
PROVIDE(__heap_start = __heap_start);
PROVIDE(__heap_end  = __heap_end);
PROVIDE(_data_load_start = _data_load_start);
PROVIDE(_data_start = _data_start);
PROVIDE(_data_end   = _data_end);
PROVIDE(_bss_start  = _bss_start);
PROVIDE(_bss_end    = _bss_end);
