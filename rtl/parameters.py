"""
lunahan_v1 parameter definitions.

All configurable parameters for the lunahan_v1 RISC-V RV32IMC core.
These are isolated to enable easy sweeping, A/B testing, and synthesis
experiments without modifying the core source.
"""

from dataclasses import dataclass, field
from typing import ClassVar

# ==========================================================================
# Top-level parameters
# ==========================================================================

@dataclass(frozen=True)
class LunahanParams:
    """Lunahan core configuration parameters.

    All widths, depths, and sizes are defined here. The core module reads
    this configuration at elaboration time and sizes all internal structures
    accordingly.

    Use `LunahanParams.default()` for the standard RV32IMC configuration,
    or construct a custom instance for experiments (e.g., different cache
    sizes, larger BTB, etc.).
    """

    # ------------------------------------------------------------------
    # ISA configuration
    # ------------------------------------------------------------------
    xlen: int = 32
    """Data width (XLEN). RV32 = 32. Do NOT change for RV32IMC."""

    ilen: int = 32
    """Instruction width for 32-bit uncompressed instructions."""

    clen: int = 16
    """Compressed instruction width."""

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------
    pipeline_depth: int = 5
    """Number of pipeline stages: IF, ID, EX, MEM, WB."""

    # ------------------------------------------------------------------
    # Register file
    # ------------------------------------------------------------------
    rf_entries: int = 32
    """Number of integer registers (x0–x31). Always 32 for RV32."""

    rf_read_ports: int = 2
    """Read ports (rs1, rs2)."""

    rf_write_ports: int = 1
    """Write port (rd)."""

    # ------------------------------------------------------------------
    # Caches
    # ------------------------------------------------------------------
    icache_size_bytes: int = 4096
    """I-cache capacity in bytes (4 KB)."""

    icache_line_bytes: int = 16
    """I-cache line size in bytes."""

    icache_associativity: int = 1
    """I-cache associativity (1 = direct-mapped)."""

    dcache_size_bytes: int = 4096
    """D-cache capacity in bytes (4 KB)."""

    dcache_line_bytes: int = 16
    """D-cache line size in bytes."""

    dcache_associativity: int = 1
    """D-cache associativity (1 = direct-mapped)."""

    # ------------------------------------------------------------------
    # Branch predictor (BTB)
    # ------------------------------------------------------------------
    btb_entries: int = 64
    """Branch target buffer entries, direct-mapped."""

    btb_bimodal_bits: int = 2
    """Bimodal counter width (2-bit saturating)."""

    # ------------------------------------------------------------------
    # Multiplier / Divider
    # ------------------------------------------------------------------
    mul_latency: int = 5
    """Radix-4 Booth multiplier latency in cycles."""

    div_latency: int = 33
    """Restoring divider latency in cycles (1 per bit + overhead)."""

    # ------------------------------------------------------------------
    # CSR
    # ------------------------------------------------------------------
    csr_mvendorid_value: int = 0
    """Machine vendor ID — 0 = non-commercial, as allowed by spec."""

    csr_marchid_value: int = 0x00000001
    """Machine architecture ID — custom for lunahan_v1."""

    csr_mimpid_value: int = 0x00000001
    """Machine implementation ID — v1.0."""

    csr_misa_value: int = 0x40001104
    """MISA CSR reset value: RV32IMC (bits: MXL=1, Extensions: IMC)."""

    # ------------------------------------------------------------------
    # Memory map
    # ------------------------------------------------------------------
    rom_base: int = 0x0000_0000
    """Boot ROM base address (reset vector)."""

    rom_size_bytes: int = 4096
    """Boot ROM size."""

    clint_base: int = 0x0000_1000
    """CLINT base address."""

    clint_size_bytes: int = 0x0001_0000
    """CLINT address space."""

    uart_base: int = 0x1000_0000
    """UART peripheral base address."""

    uart_size_bytes: int = 0x0001_0000
    """UART address space."""

    dram_base: int = 0x8000_0000
    """Main memory (DRAM) base address."""

    dram_size_bytes: int = 0x8000_0000
    """Main memory size (2 GB max, addressable up to 0xFFFF_FFFF)."""

    stack_top: int = 0xFFFF_FFF0
    """Initial stack pointer value (top of addressable memory, 16-byte aligned)."""

    # ------------------------------------------------------------------
    # Clock and reset
    # ------------------------------------------------------------------
    reset_vector: int = 0x0000_0000
    """Address to fetch first instruction after reset."""

    # ------------------------------------------------------------------
    # Physical parameters (for OpenROAD estimates)
    # ------------------------------------------------------------------
    target_fmax_mhz: int = 50
    """Target operating frequency, MHz."""

    target_area_um2: int = 250_000
    """Target core area, µm² (0.25 mm²)."""

    # ==================================================================
    # Derived parameters (computed automatically)
    # ==================================================================

    @property
    def pc_width(self) -> int:
        """PC register width. Same as XLEN for RV32."""
        return self.xlen

    @property
    def rf_addr_width(self) -> int:
        """Register file address width (5 bits for 32 regs)."""
        return (self.rf_entries - 1).bit_length()

    @property
    def icache_blocks(self) -> int:
        """Number of blocks in I-cache."""
        return self.icache_size_bytes // self.icache_line_bytes // self.icache_associativity

    @property
    def dcache_blocks(self) -> int:
        """Number of blocks in D-cache."""
        return self.dcache_size_bytes // self.dcache_line_bytes // self.dcache_associativity

    @property
    def icache_index_width(self) -> int:
        """I-cache index bit width."""
        return (self.icache_blocks - 1).bit_length()

    @property
    def dcache_index_width(self) -> int:
        """D-cache index bit width."""
        return (self.dcache_blocks - 1).bit_length()

    @property
    def icache_offset_width(self) -> int:
        """I-cache block offset bit width."""
        return (self.icache_line_bytes - 1).bit_length()

    @property
    def dcache_offset_width(self) -> int:
        """D-cache block offset bit width."""
        return (self.dcache_line_bytes - 1).bit_length()

    @property
    def icache_tag_width(self) -> int:
        """I-cache tag bit width."""
        return self.xlen - self.icache_index_width - self.icache_offset_width

    @property
    def dcache_tag_width(self) -> int:
        """D-cache tag bit width."""
        return self.xlen - self.dcache_index_width - self.dcache_offset_width

    @property
    def btb_index_width(self) -> int:
        """BTB index bit width."""
        return (self.btb_entries - 1).bit_length()

    @property
    def btb_tag_width(self) -> int:
        """BTB tag bit width (from PC bits)."""
        return self.xlen - self.btb_index_width - 2

    # ==================================================================
    # Helper
    # ==================================================================

    @classmethod
    def default(cls) -> "LunahanParams":
        """Return the default RV32IMC configuration."""
        return cls()

    def to_dict(self) -> dict:
        """Serialize parameters to a plain dict (useful for JSON export)."""
        import dataclasses
        return dataclasses.asdict(self)


# ==========================================================================
# Shared constants (not configurable — tied to RISC-V ISA)
# ==========================================================================

class RISCVConstants:
    """RISC-V ISA constants used throughout the core.

    These are NOT parameters — they are defined by the RISC-V
    specification and should not be changed.
    """

    # Opcodes (7-bit)
    OP_LUI:    int = 0b0110111
    OP_AUIPC:  int = 0b0010111
    OP_JAL:    int = 0b1101111
    OP_JALR:   int = 0b1100111
    OP_BRANCH: int = 0b1100011
    OP_LOAD:   int = 0b0000011
    OP_STORE:  int = 0b0100011
    OP_ALUI:   int = 0b0010011
    OP_ALU:    int = 0b0110011
    OP_FENCE:  int = 0b0001111
    OP_SYSTEM: int = 0b1110011

    # ALU funct3 for OP_ALUI and OP_ALU
    F3_ADDI  = F3_ADD  = 0b000
    F3_SLLI  = F3_SLL  = 0b001
    F3_SLTI  = F3_SLT  = 0b010
    F3_SLTIU = F3_SLTU = 0b011
    F3_XORI  = F3_XOR  = 0b100
    F3_SRLI  = F3_SRL  = 0b101
    F3_SRAI  = F3_SRA  = 0b101  # Same funct3, different funct7
    F3_ORI   = F3_OR   = 0b110
    F3_ANDI  = F3_AND  = 0b111

    # Branch funct3
    F3_BEQ:  int = 0b000
    F3_BNE:  int = 0b001
    F3_BLT:  int = 0b100
    F3_BGE:  int = 0b101
    F3_BLTU: int = 0b110
    F3_BGEU: int = 0b111

    # Load/Store funct3
    F3_LB:  int = 0b000
    F3_LH:  int = 0b001
    F3_LW:  int = 0b010
    F3_LBU: int = 0b100
    F3_LHU: int = 0b101
    F3_SB:  int = 0b000
    F3_SH:  int = 0b001
    F3_SW:  int = 0b010

    # funct7 bits
    F7_ALU_NORMAL:  int = 0b0000000
    F7_ALU_ALT:     int = 0b0100000   # SUB, SRA, SRAI
    F7_MUL_DIV:     int = 0b0000001   # M extension

    # M extension funct3
    F3_MUL:    int = 0b000
    F3_MULH:   int = 0b001
    F3_MULHSU: int = 0b010
    F3_MULHU:  int = 0b011
    F3_DIV:    int = 0b100
    F3_DIVU:   int = 0b101
    F3_REM:    int = 0b110
    F3_REMU:   int = 0b111

    # System funct3
    F3_PRIV:   int = 0b000  # ECALL, EBREAK, MRET
    F3_CSRRW:  int = 0b001
    F3_CSRRS:  int = 0b010
    F3_CSRRC:  int = 0b011
    F3_CSRRWI: int = 0b101
    F3_CSRRSI: int = 0b110
    F3_CSRRCI: int = 0b111

    # System funct12 (immediate field for ECALL/EBREAK/MRET)
    F12_ECALL:  int = 0x000
    F12_EBREAK: int = 0x001
    F12_MRET:   int = 0x302

    # CSR addresses (12-bit)
    CSR_MVENDORID:  int = 0xF11
    CSR_MARCHID:    int = 0xF12
    CSR_MIMPID:     int = 0xF13
    CSR_MHARTID:    int = 0xF14
    CSR_MSTATUS:    int = 0x300
    CSR_MISA:       int = 0x301
    CSR_MIE:        int = 0x304
    CSR_MTVEC:      int = 0x305
    CSR_MSCRATCH:   int = 0x340
    CSR_MEPC:       int = 0x341
    CSR_MCAUSE:     int = 0x342
    CSR_MTVAL:      int = 0x343
    CSR_MIP:        int = 0x344
    CSR_MCYCLE:     int = 0xB00
    CSR_MCYCLEH:    int = 0xB80
    CSR_MINSTRET:   int = 0xB02
    CSR_MINSTRETH:  int = 0xB82

    # mstatus bit positions
    MSTATUS_MIE:  int = 3
    MSTATUS_MPIE: int = 7
    MSTATUS_MPP:  int = 11  # 2-bit field at [12:11]

    # mcause exception codes
    EXC_INST_MISALIGNED:  int = 0
    EXC_INST_ACCESS:      int = 1
    EXC_ILLEGAL_INST:     int = 2
    EXC_BREAKPOINT:       int = 3
    EXC_LOAD_MISALIGNED:  int = 4
    EXC_LOAD_ACCESS:      int = 5
    EXC_STORE_MISALIGNED: int = 6
    EXC_STORE_ACCESS:     int = 7
    EXC_ECALL_M:          int = 11

    # Interrupt codes (mcause[31] = 1)
    IRQ_SOFTWARE:  int = 3
    IRQ_TIMER:     int = 7
    IRQ_EXTERNAL:  int = 11

    # mtvec mode
    MTVEC_DIRECT:   int = 0
    MTVEC_VECTORED: int = 1

    # ALU operation encodings (internal control signals)
    ALU_ADD:    int = 0x00
    ALU_SUB:    int = 0x01
    ALU_SLL:    int = 0x02
    ALU_SLT:    int = 0x03
    ALU_SLTU:   int = 0x04
    ALU_XOR:    int = 0x05
    ALU_SRL:    int = 0x06
    ALU_SRA:    int = 0x07
    ALU_OR:     int = 0x08
    ALU_AND:    int = 0x09
    ALU_LUI:    int = 0x0A
    ALU_AUIPC:  int = 0x0B
    ALU_JAL:    int = 0x0C  # pc+4 for link
    ALU_BEQ:    int = 0x0D
    ALU_BNE:    int = 0x0E
    ALU_BLT:    int = 0x0F
    ALU_BGE:    int = 0x10
    ALU_BLTU:   int = 0x11
    ALU_BGEU:   int = 0x12
    ALU_PASS:   int = 0x13  # pass rs2 through (store)
    ALU_CSR_RD: int = 0x14  # CSR read data

    # Write-back source select
    WB_ALU: int = 0
    WB_MEM: int = 1
    WB_PC4: int = 2
    WB_CSR: int = 3

    # ALU source select
    SRC_RS1:  int = 0
    SRC_PC:   int = 1
    SRC_ZERO: int = 2
    SRC_CSR:  int = 3
    SRC_RS2:  int = 0
    SRC_IMM:  int = 1
    SRC_FOUR: int = 2

    # Memory operation
    MEM_NONE:  int = 0
    MEM_READ:  int = 1
    MEM_WRITE: int = 2

    # Memory size
    SIZE_BYTE: int = 0
    SIZE_HALF: int = 1
    SIZE_WORD: int = 2

    # CSR operation
    CSR_NONE:  int = 0
    CSR_RW:    int = 1
    CSR_RS:    int = 2
    CSR_RC:    int = 3

    # Branch operation
    BR_NONE: int = 0
    BR_JAL:  int = 1
    BR_JALR: int = 2
    BR_BEQ:  int = 3
    BR_BNE:  int = 4
    BR_BLT:  int = 5
    BR_BGE:  int = 6
    BR_BLTU: int = 7
    BR_BGEU: int = 8
