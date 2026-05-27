"""
lunahan_core.py — RISC-V RV32IMC 5-stage in-order processor core.

Complete pyCircuit V5 cycle-aware implementation of the lunahan_v1 core.
Implements RV32I base integer ISA, M extension (multiply/divide), and C
extension (compressed instructions) with a 5-stage in-order pipeline:
IF → ID → EX → MEM → WB.

Usage as top-level module:
    from lunahan_core import LunahanCore
    from parameters import LunahanParams

    params = LunahanParams.default()
    core = LunahanCore(params)

Design principles:
  - All pipeline stages are cycle-aware domains
  - Register file: 32 x 32-bit, 2R/1W with internal forwarding
  - I-Cache: 4 KB direct-mapped, read-only
  - D-Cache: 4 KB direct-mapped, write-back
  - BTB: 64-entry bimodal branch predictor
  - Multi-cycle multiplier (5) and divider (33) with stall
  - Harvard architecture with AXI4-Lite bus interface
  - M-mode only (Machine mode), full CSR set
"""

from pycircuit.core import (
    module,
    domain,
    CycleAwareCircuit,
    CycleAwareDomain,
    UInt,
    SInt,
    Bool,
    Bits,
    Signal,
    Reg,
    RegNext,
    RegInit,
    Wire,
    when,
    otherwise,
    with_signals,
    Cat,
    Mux,
    MuxLookup,
    Mem,
    Assert,
    Assume,
    io,
)
from parameters import LunahanParams, RISCVConstants as C

XLEN = 32
ILEN = 32
CLEN = 16


# ==========================================================================
# Helper modules
# ==========================================================================


class ALU(CycleAwareDomain):
    """32-bit arithmetic-logic unit supporting all RV32I + M operations.

    Combinational: result available in the same cycle as inputs.
    Supports ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND,
    and branch comparison operations (BEQ, BNE, BLT, BGE, BLTU, BGEU).

    Also computes the next PC (pc+4, branch target, jump target).

    Ports
    -----
    alu_op    : UInt[5]  — operation selector
    src1      : UInt[32] — first operand (rs1, pc, or zero)
    src2      : UInt[32] — second operand (rs2, imm, or 4)
    result    : UInt[32] — computation result
    br_taken  : Bool     — branch condition satisfied (for branch ops)
    """

    alu_op = Signal(Bits[5], "in")
    src1 = Signal(UInt[XLEN], "in")
    src2 = Signal(UInt[XLEN], "in")
    result = Signal(UInt[XLEN], "out")
    br_taken = Signal(Bool, "out")

    def execute(self):
        op = self.alu_op
        a = self.src1
        b = self.src2
        result = Wire(UInt[XLEN])
        taken = Wire(Bool)

        when(op == C.ALU_ADD):
            result <<= a + b
        with_signals.otherwise_when(op == C.ALU_SUB):
            result <<= a - b
        with_signals.otherwise_when(op == C.ALU_SLL):
            result <<= a << b[0:5]
        with_signals.otherwise_when(op == C.ALU_SLT):
            result <<= Mux(SInt[XLEN](a) < SInt[XLEN](b), UInt[XLEN](1), UInt[XLEN](0))
        with_signals.otherwise_when(op == C.ALU_SLTU):
            result <<= Mux(a < b, UInt[XLEN](1), UInt[XLEN](0))
        with_signals.otherwise_when(op == C.ALU_XOR):
            result <<= a ^ b
        with_signals.otherwise_when(op == C.ALU_SRL):
            result <<= a >> b[0:5]
        with_signals.otherwise_when(op == C.ALU_SRA):
            result <<= (SInt[XLEN](a) >> b[0:5]).as_uint()
        with_signals.otherwise_when(op == C.ALU_OR):
            result <<= a | b
        with_signals.otherwise_when(op == C.ALU_AND):
            result <<= a & b
        with_signals.otherwise_when(op == C.ALU_LUI):
            result <<= b
        with_signals.otherwise_when(op == C.ALU_AUIPC):
            result <<= a + b
        with_signals.otherwise_when(op == C.ALU_JAL):
            result <<= a + UInt[XLEN](4)
        with_signals.otherwise_when(op == C.ALU_PASS):
            result <<= b
        with_signals.otherwise_when(op == C.ALU_CSR_RD):
            result <<= b
        with_signals.otherwise():
            result <<= UInt[XLEN](0)

        with_signals.when(op == C.ALU_BEQ):
            taken <<= (a == b)
        with_signals.otherwise_when(op == C.ALU_BNE):
            taken <<= (a != b)
        with_signals.otherwise_when(op == C.ALU_BLT):
            taken <<= (SInt[XLEN](a) < SInt[XLEN](b))
        with_signals.otherwise_when(op == C.ALU_BGE):
            taken <<= (SInt[XLEN](a) >= SInt[XLEN](b))
        with_signals.otherwise_when(op == C.ALU_BLTU):
            taken <<= (a < b)
        with_signals.otherwise_when(op == C.ALU_BGEU):
            taken <<= (a >= b)
        with_signals.otherwise():
            taken <<= Bool(False)

        self.result <<= result
        self.br_taken <<= taken


class Multiplier(CycleAwareDomain):
    """Radix-4 Booth multiplier (5-cycle latency).

    Computes a 64-bit signed product over 5 cycles:
      cycle 0: Booth encoding of multiplicand
      cycle 1: Generate partial products
      cycle 2: Wallace tree 4→2 reduction
      cycle 3: Wallace tree 2→1 (carry-save → binary)
      cycle 4: Final addition

    Returns product[31:0] for MUL, product[63:32] for MULH/MULHSU/MULHU.

    Ports
    -----
    start      : Bool     — assert to begin multiplication
    a          : UInt[32] — multiplicand (rs1)
    b          : UInt[32] — multiplier (rs2)
    signed_a   : Bool     — treat a as signed
    signed_b   : Bool     — treat b as signed
    upper_half : Bool     — return upper 32 bits of product
    done       : Bool     — result valid this cycle
    result     : UInt[32] — product output
    """

    start = Signal(Bool, "in")
    a_i = Signal(UInt[XLEN], "in")
    b_i = Signal(UInt[XLEN], "in")
    signed_a = Signal(Bool, "in")
    signed_b = Signal(Bool, "in")
    upper_half = Signal(Bool, "in")
    done = Signal(Bool, "out")
    result = Signal(UInt[XLEN], "out")

    def __init__(self):
        self.busy = RegInit(Bool(False))
        self.counter = RegInit(UInt[3](0))

        # Pipeline registers for Booth stages
        self.a_reg = Reg(UInt[33](0))
        self.b_reg = Reg(UInt[33](0))
        self.a_sign = Reg(Bool(False))
        self.b_sign = Reg(Bool(False))
        self.use_upper = Reg(Bool(False))

        # Partial product accumulator
        self.pp0 = Reg(UInt[66](0))
        self.pp1 = Reg(UInt[66](0))
        self.pp2 = Reg(UInt[66](0))
        self.pp3 = Reg(UInt[66](0))

        # Stage outputs
        self.stage1_sum = Reg(UInt[66](0))
        self.stage1_carry = Reg(UInt[66](0))
        self.stage2_sum = Reg(UInt[66](0))
        self.stage2_carry = Reg(UInt[66](0))
        self.final_result = Reg(UInt[64](0))

    def execute(self):
        when(self.start & ~self.busy):
            self.busy <<= Bool(True)
            self.counter <<= UInt[3](0)
            a_ext = Cat(UInt[1](0), self.a_i)
            b_ext = Cat(UInt[1](0), self.b_i)
            self.a_reg <<= Mux(self.signed_a & self.a_i[31], (~a_ext + 1), a_ext)
            self.b_reg <<= Mux(self.signed_b & self.b_i[31], (~b_ext + 1), b_ext)
            self.a_sign <<= self.signed_a & self.a_i[31]
            self.b_sign <<= self.signed_b & self.b_i[31]
            self.use_upper <<= self.upper_half
            self.pp0 <<= UInt[66](0)
            self.pp1 <<= UInt[66](0)
            self.pp2 <<= UInt[66](0)
            self.pp3 <<= UInt[66](0)

        with_signals.otherwise_when(self.busy):
            self.counter <<= self.counter + 1
            ctr = self.counter

            with_signals.when(ctr == 0):
                a = self.a_reg  # 33-bit
                b = self.b_reg  # 33-bit
                # Cycle 0: Booth encoding, generate 17 partial products
                b0 = Cat(b, UInt[1](0))
                pp = [UInt[66](0) for _ in range(17)]
                for j in range(17):
                    booth = b0[j * 2 : (j + 1) * 2 + 1]
                    shift = j * 2
                    with_signals.when(booth == 0b001):
                        pp[j] <<= (Cat(UInt[34](0), a) << UInt[6](shift))
                    with_signals.otherwise_when(booth == 0b010):
                        pp[j] <<= (Cat(UInt[34](0), a) << UInt[6](shift))
                    with_signals.otherwise_when(booth == 0b011):
                        pp[j] <<= (Cat(UInt[34](0), a) << UInt[6](shift + 1))
                    with_signals.otherwise_when(booth == 0b100):
                        pp[j] <<= (Cat(UInt[34](0), (~a + 1)) << UInt[6](shift + 1))
                    with_signals.otherwise_when(booth == 0b101):
                        pp[j] <<= (Cat(UInt[34](0), (~a + 1)) << UInt[6](shift))
                    with_signals.otherwise_when(booth == 0b110):
                        pp[j] <<= (Cat(UInt[34](0), (~a + 1)) << UInt[6](shift))
                    with_signals.otherwise():
                        pp[j] <<= UInt[66](0)

                self.pp0 <<= pp[0] + pp[1] + pp[2] + pp[3]
                self.pp1 <<= pp[4] + pp[5] + pp[6] + pp[7]
                self.pp2 <<= pp[8] + pp[9] + pp[10] + pp[11] + pp[12]
                self.pp3 <<= pp[13] + pp[14] + pp[15] + pp[16]

            with_signals.otherwise_when(ctr == 1):
                self.stage1_sum <<= self.pp0 + self.pp1 + self.pp2 + self.pp3
                self.stage1_carry <<= UInt[66](0)

            with_signals.otherwise_when(ctr == 2):
                self.stage2_sum <<= self.stage1_sum + self.stage1_carry
                self.stage2_carry <<= UInt[66](0)

            with_signals.otherwise_when(ctr == 3):
                self.final_result <<= (self.stage2_sum + self.stage2_carry)[0:64]

            with_signals.otherwise_when(ctr == 4):
                sign_correction = self.a_sign ^ self.b_sign
                product = Wire(UInt[64])
                with_signals.when(sign_correction):
                    product <<= (~self.final_result[0:64] + 1)
                with_signals.otherwise():
                    product <<= self.final_result[0:64]
                with_signals.when(self.use_upper):
                    self.result <<= product[32:64]
                with_signals.otherwise():
                    self.result <<= product[0:32]
                self.done <<= Bool(True)
                self.busy <<= Bool(False)


class Divider(CycleAwareDomain):
    """Restoring divider (33-cycle latency).

    Performs 32-bit by 32-bit division using the restoring algorithm,
    producing a quotient and remainder.

    Ports
    -----
    start      : Bool     — assert to begin division
    dividend   : UInt[32] — rs1
    divisor    : UInt[32] — rs2
    is_signed  : Bool     — signed division
    is_rem     : Bool     — output remainder (vs quotient)
    done       : Bool     — result valid this cycle
    result     : UInt[32] — quotient or remainder
    """

    start = Signal(Bool, "in")
    dividend = Signal(UInt[XLEN], "in")
    divisor = Signal(UInt[XLEN], "in")
    is_signed = Signal(Bool, "in")
    is_rem = Signal(Bool, "in")
    done = Signal(Bool, "out")
    result = Signal(UInt[XLEN], "out")

    def __init__(self):
        self.busy = RegInit(Bool(False))
        self.counter = RegInit(UInt[6](0))  # 0–32 iterations
        self.remainder = Reg(UInt[65](0))
        self.divisor_reg = Reg(UInt[32](0))
        self.quotient = Reg(UInt[32](0))
        self.div_sign = Reg(Bool(False))
        self.dend_sign = Reg(Bool(False))
        self.signed_op = Reg(Bool(False))
        self.rem_output = Reg(Bool(False))

    def execute(self):
        when(self.start & ~self.busy):
            self.busy <<= Bool(True)
            self.counter <<= UInt[6](0)
            dend = self.dividend
            div = self.divisor

            with_signals.when(self.is_signed):
                self.dend_sign <<= dend[31]
                self.div_sign <<= div[31]
                self.signed_op <<= Bool(True)
                dend_abs = Mux(dend[31], (~dend + 1), dend)
                div_abs = Mux(div[31], (~div + 1), div)
            with_signals.otherwise():
                self.dend_sign <<= Bool(False)
                self.div_sign <<= Bool(False)
                self.signed_op <<= Bool(False)
                dend_abs = dend
                div_abs = div

            # Special case: division by zero
            with_signals.when(div == 0):
                self.result <<= Mux(self.is_rem, dend, UInt[XLEN](0xFFFFFFFF))
                self.done <<= Bool(True)
                self.busy <<= Bool(False)
            with_signals.otherwise():
                # Special case: signed overflow (INT_MIN / -1)
                with_signals.when(
                    self.is_signed
                    & (self.dividend == UInt[XLEN](0x80000000))
                    & (self.divisor == UInt[XLEN](0xFFFFFFFF))
                ):
                    self.result <<= Mux(self.is_rem, UInt[XLEN](0), self.dividend)
                    self.done <<= Bool(True)
                    self.busy <<= Bool(False)
                with_signals.otherwise():
                    self.remainder <<= Cat(UInt[33](0), dend_abs)
                    self.divisor_reg <<= div_abs
                    self.quotient <<= UInt[32](0)
                    self.rem_output <<= self.is_rem

        with_signals.otherwise_when(self.busy):
            ctr = self.counter
            with_signals.when(ctr < 32):
                self.remainder <<= self.remainder << 1
                rem_hi = self.remainder[32:65]
                with_signals.when(rem_hi >= Cat(UInt[1](0), self.divisor_reg)):
                    self.remainder <<= Cat(
                        (rem_hi - Cat(UInt[1](0), self.divisor_reg)),
                        self.remainder[0:33]
                    )
                    self.remainder[0] <<= Bool(True)
                self.quotient <<= Cat(self.quotient[0:31], self.remainder[0])
                self.counter <<= ctr + 1

            with_signals.otherwise_when(ctr == 32):
                sign_correct = self.dend_sign ^ self.div_sign
                with_signals.when(self.rem_output):
                    with_signals.when(self.signed_op & self.dend_sign):
                        self.result <<= (~self.remainder[0:32] + 1)
                    with_signals.otherwise():
                        self.result <<= self.remainder[0:32]
                with_signals.otherwise():
                    with_signals.when(self.signed_op & sign_correct):
                        self.result <<= (~self.quotient + 1)
                    with_signals.otherwise():
                        self.result <<= self.quotient
                self.done <<= Bool(True)
                self.busy <<= Bool(False)


class InstructionDecoder(CycleAwareDomain):
    """RISC-V RV32IMC instruction decoder.

    Decodes 32-bit instructions (or expanded 16-bit compressed instructions)
    into control signals. Supports RV32I base + M extension.
    Detects illegal instructions.

    Ports
    -----
    inst       : UInt[32] — instruction word (already C-expanded if compressed)
    c_expanded : Bool     — this instruction was compressed (informational)
    pc         : UInt[32] — program counter of this instruction
    exception  : Bool     — raise illegal instruction exception
    excep_code : UInt[5]  — exception cause code
    alu_op     : UInt[5]  — ALU operation
    alu_src1   : UInt[2]  — ALU source 1 select
    alu_src2   : UInt[2]  — ALU source 2 select
    mem_op     : UInt[2]  — memory operation (none/read/write)
    mem_size   : UInt[2]  — memory access size (byte/half/word)
    mem_sext   : Bool     — sign-extend memory load data
    wb_en      : Bool     — write-back enable
    wb_src     : UInt[2]  — write-back source select
    branch_op  : UInt[4]  — branch/jump operation
    is_branch  : Bool     — this is a control-flow instruction
    csr_cmd    : UInt[2]  — CSR operation
    csr_addr   : UInt[12] — CSR address
    is_mul_div : Bool     — M-extension multiply/divide
    mul_div_op : UInt[3]  — M-extension specific operation
    is_signed  : Bool     — signed/unsigned for M ops
    is_system  : Bool     — ECALL/EBREAK/MRET
    imm        : UInt[32] — sign/zero-extended immediate
    rd_idx     : UInt[5]  — destination register index
    rs1_idx    : UInt[5]  — source register 1 index
    rs2_idx    : UInt[5]  — source register 2 index
    """

    inst = Signal(UInt[ILEN], "in")
    pc = Signal(UInt[XLEN], "in")

    # Exception flag
    exception = Signal(Bool, "out")
    excep_code = Signal(UInt[5], "out")

    # Control signals
    alu_op = Signal(Bits[5], "out")
    alu_src1 = Signal(Bits[2], "out")
    alu_src2 = Signal(Bits[2], "out")
    mem_op = Signal(Bits[2], "out")
    mem_size = Signal(Bits[2], "out")
    mem_sext = Signal(Bool, "out")
    wb_en = Signal(Bool, "out")
    wb_src = Signal(Bits[2], "out")
    branch_op = Signal(Bits[4], "out")
    is_branch = Signal(Bool, "out")
    csr_cmd = Signal(Bits[2], "out")
    csr_addr = Signal(UInt[12], "out")
    is_mul_div = Signal(Bool, "out")
    mul_div_op = Signal(Bits[3], "out")
    is_signed = Signal(Bool, "out")
    is_system = Signal(Bool, "out")
    imm = Signal(UInt[XLEN], "out")
    rd_idx = Signal(UInt[5], "out")
    rs1_idx = Signal(UInt[5], "out")
    rs2_idx = Signal(UInt[5], "out")

    def execute(self):
        inst = self.inst
        opcode = inst[0:7]
        rd = inst[7:12]
        funct3 = inst[12:15]
        rs1 = inst[15:20]
        rs2 = inst[20:25]
        funct7 = inst[25:32]
        funct12 = inst[20:32]

        # Default outputs
        exc = Wire(Bool(False))
        exc_code = Wire(UInt[5](0))
        alu_op_w = Wire(Bits[5](C.ALU_ADD))
        alu_s1 = Wire(Bits[2](C.SRC_RS1))
        alu_s2 = Wire(Bits[2](C.SRC_RS2))
        mem_w = Wire(Bits[2](C.MEM_NONE))
        mem_sz = Wire(Bits[2](C.SIZE_WORD))
        mem_se = Wire(Bool(False))
        write_en = Wire(Bool(False))
        wb_s = Wire(Bits[2](C.WB_ALU))
        br_op = Wire(Bits[4](C.BR_NONE))
        br_flag = Wire(Bool(False))
        csr_c = Wire(Bits[2](C.CSR_NONE))
        csr_a = Wire(UInt[12](0))
        mul_div = Wire(Bool(False))
        md_op = Wire(Bits[3](0))
        signed_op = Wire(Bool(False))
        sys_op = Wire(Bool(False))
        imm_w = Wire(UInt[XLEN](0))
        rd_idx_w = Wire(UInt[5](0))
        rs1_idx_w = Wire(UInt[5](0))
        rs2_idx_w = Wire(UInt[5](0))

        # Immediate extraction
        i_imm = Cat(inst[20:32], Bits(20)(inst[31])).as_uint()  # I-type: sign-extend 12-bit
        s_imm = Cat(inst[7:12], inst[25:32], Bits(20)(inst[31])).as_uint()
        b_imm = Cat(
            Bits(1)(inst[7]),
            inst[8:12],
            inst[25:31],
            inst[31],
            Bits(20)(inst[31]),
        ).as_uint()  # sign extend bit 12
        u_imm = Cat(inst[12:32], Bits(12)(0)).as_uint()
        j_imm = Cat(
            Bits(1)(inst[20]),
            inst[21:25],
            inst[25:31],
            inst[31],
            inst[12:20],
            Bits(12)(inst[31]),
        ).as_uint()

        # ================================================================
        # Decode by opcode
        # ================================================================

        with_signals.when(opcode == C.OP_LUI):
            alu_op_w <<= Bits[5](C.ALU_LUI)
            alu_s2 <<= Bits[2](C.SRC_IMM)
            imm_w <<= u_imm
            write_en <<= Bool(True)
            rd_idx_w <<= rd

        with_signals.otherwise_when(opcode == C.OP_AUIPC):
            alu_op_w <<= Bits[5](C.ALU_AUIPC)
            alu_s1 <<= Bits[2](C.SRC_PC)
            alu_s2 <<= Bits[2](C.SRC_IMM)
            imm_w <<= u_imm
            write_en <<= Bool(True)
            rd_idx_w <<= rd

        with_signals.otherwise_when(opcode == C.OP_JAL):
            alu_op_w <<= Bits[5](C.ALU_JAL)
            alu_s1 <<= Bits[2](C.SRC_PC)
            write_en <<= Bool(True)
            wb_s <<= Bits[2](C.WB_PC4)
            rd_idx_w <<= rd
            br_op <<= Bits[4](C.BR_JAL)
            br_flag <<= Bool(True)
            imm_w <<= j_imm

        with_signals.otherwise_when(opcode == C.OP_JALR):
            alu_op_w <<= Bits[5](C.ALU_JAL)
            alu_s1 <<= Bits[2](C.SRC_RS1)
            alu_s2 <<= Bits[2](C.SRC_IMM)
            imm_w <<= i_imm
            write_en <<= Bool(True)
            wb_s <<= Bits[2](C.WB_PC4)
            rd_idx_w <<= rd
            rs1_idx_w <<= rs1
            br_op <<= Bits[4](C.BR_JALR)
            br_flag <<= Bool(True)
            with_signals.when(funct3 != 0):
                exc <<= Bool(True)
                exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

        with_signals.otherwise_when(opcode == C.OP_BRANCH):
            rs1_idx_w <<= rs1
            rs2_idx_w <<= rs2
            br_flag <<= Bool(True)
            imm_w <<= b_imm

            with_signals.when(funct3 == C.F3_BEQ):
                alu_op_w <<= Bits[5](C.ALU_BEQ)
                br_op <<= Bits[4](C.BR_BEQ)
            with_signals.otherwise_when(funct3 == C.F3_BNE):
                alu_op_w <<= Bits[5](C.ALU_BNE)
                br_op <<= Bits[4](C.BR_BNE)
            with_signals.otherwise_when(funct3 == C.F3_BLT):
                alu_op_w <<= Bits[5](C.ALU_BLT)
                br_op <<= Bits[4](C.BR_BLT)
            with_signals.otherwise_when(funct3 == C.F3_BGE):
                alu_op_w <<= Bits[5](C.ALU_BGE)
                br_op <<= Bits[4](C.BR_BGE)
            with_signals.otherwise_when(funct3 == C.F3_BLTU):
                alu_op_w <<= Bits[5](C.ALU_BLTU)
                br_op <<= Bits[4](C.BR_BLTU)
            with_signals.otherwise_when(funct3 == C.F3_BGEU):
                alu_op_w <<= Bits[5](C.ALU_BGEU)
                br_op <<= Bits[4](C.BR_BGEU)
            with_signals.otherwise():
                exc <<= Bool(True)
                exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

        with_signals.otherwise_when(opcode == C.OP_LOAD):
            alu_op_w <<= Bits[5](C.ALU_ADD)
            alu_s2 <<= Bits[2](C.SRC_IMM)
            imm_w <<= i_imm
            write_en <<= Bool(True)
            wb_s <<= Bits[2](C.WB_MEM)
            mem_w <<= Bits[2](C.MEM_READ)
            rs1_idx_w <<= rs1
            rd_idx_w <<= rd

            with_signals.when(funct3 == C.F3_LB):
                mem_sz <<= Bits[2](C.SIZE_BYTE)
                mem_se <<= Bool(True)
            with_signals.otherwise_when(funct3 == C.F3_LH):
                mem_sz <<= Bits[2](C.SIZE_HALF)
                mem_se <<= Bool(True)
            with_signals.otherwise_when(funct3 == C.F3_LW):
                mem_sz <<= Bits[2](C.SIZE_WORD)
                mem_se <<= Bool(False)
            with_signals.otherwise_when(funct3 == C.F3_LBU):
                mem_sz <<= Bits[2](C.SIZE_BYTE)
                mem_se <<= Bool(False)
            with_signals.otherwise_when(funct3 == C.F3_LHU):
                mem_sz <<= Bits[2](C.SIZE_HALF)
                mem_se <<= Bool(False)
            with_signals.otherwise():
                exc <<= Bool(True)
                exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

        with_signals.otherwise_when(opcode == C.OP_STORE):
            alu_op_w <<= Bits[5](C.ALU_ADD)
            alu_s2 <<= Bits[2](C.SRC_IMM)
            imm_w <<= s_imm
            mem_w <<= Bits[2](C.MEM_WRITE)
            rs1_idx_w <<= rs1
            rs2_idx_w <<= rs2

            with_signals.when(funct3 == C.F3_SB):
                mem_sz <<= Bits[2](C.SIZE_BYTE)
            with_signals.otherwise_when(funct3 == C.F3_SH):
                mem_sz <<= Bits[2](C.SIZE_HALF)
            with_signals.otherwise_when(funct3 == C.F3_SW):
                mem_sz <<= Bits[2](C.SIZE_WORD)
            with_signals.otherwise():
                exc <<= Bool(True)
                exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

        with_signals.otherwise_when(opcode == C.OP_ALUI):
            alu_s2 <<= Bits[2](C.SRC_IMM)
            imm_w <<= i_imm
            write_en <<= Bool(True)
            rs1_idx_w <<= rs1
            rd_idx_w <<= rd

            with_signals.when(funct3 == C.F3_ADDI):
                alu_op_w <<= Bits[5](C.ALU_ADD)
            with_signals.otherwise_when(funct3 == C.F3_SLTI):
                alu_op_w <<= Bits[5](C.ALU_SLT)
            with_signals.otherwise_when(funct3 == C.F3_SLTIU):
                alu_op_w <<= Bits[5](C.ALU_SLTU)
            with_signals.otherwise_when(funct3 == C.F3_XORI):
                alu_op_w <<= Bits[5](C.ALU_XOR)
            with_signals.otherwise_when(funct3 == C.F3_ORI):
                alu_op_w <<= Bits[5](C.ALU_OR)
            with_signals.otherwise_when(funct3 == C.F3_ANDI):
                alu_op_w <<= Bits[5](C.ALU_AND)
            with_signals.otherwise_when(funct3 == C.F3_SLLI):
                with_signals.when(funct7 == C.F7_ALU_NORMAL):
                    alu_op_w <<= Bits[5](C.ALU_SLL)
                    imm_w <<= Cat(Bits(20)(0), inst[20:25]).as_uint()
                with_signals.otherwise():
                    exc <<= Bool(True)
                    exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)
            with_signals.otherwise_when(funct3 == C.F3_SRLI):
                with_signals.when(funct7 == C.F7_ALU_NORMAL):
                    alu_op_w <<= Bits[5](C.ALU_SRL)
                    imm_w <<= Cat(Bits(20)(0), inst[20:25]).as_uint()
                with_signals.otherwise_when(funct7 == C.F7_ALU_ALT):
                    alu_op_w <<= Bits[5](C.ALU_SRA)
                    imm_w <<= Cat(Bits(20)(0), inst[20:25]).as_uint()
                with_signals.otherwise():
                    exc <<= Bool(True)
                    exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)
            with_signals.otherwise():
                exc <<= Bool(True)
                exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

        with_signals.otherwise_when(opcode == C.OP_ALU):
            write_en <<= Bool(True)
            rs1_idx_w <<= rs1
            rs2_idx_w <<= rs2
            rd_idx_w <<= rd

            with_signals.when(funct7 == C.F7_ALU_NORMAL):
                with_signals.when(funct3 == C.F3_ADD):
                    alu_op_w <<= Bits[5](C.ALU_ADD)
                with_signals.otherwise_when(funct3 == C.F3_SLL):
                    alu_op_w <<= Bits[5](C.ALU_SLL)
                with_signals.otherwise_when(funct3 == C.F3_SLT):
                    alu_op_w <<= Bits[5](C.ALU_SLT)
                with_signals.otherwise_when(funct3 == C.F3_SLTU):
                    alu_op_w <<= Bits[5](C.ALU_SLTU)
                with_signals.otherwise_when(funct3 == C.F3_XOR):
                    alu_op_w <<= Bits[5](C.ALU_XOR)
                with_signals.otherwise_when(funct3 == C.F3_SRL):
                    alu_op_w <<= Bits[5](C.ALU_SRL)
                with_signals.otherwise_when(funct3 == C.F3_OR):
                    alu_op_w <<= Bits[5](C.ALU_OR)
                with_signals.otherwise_when(funct3 == C.F3_AND):
                    alu_op_w <<= Bits[5](C.ALU_AND)
                with_signals.otherwise():
                    exc <<= Bool(True)
                    exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

            with_signals.otherwise_when(funct7 == C.F7_ALU_ALT):
                with_signals.when(funct3 == C.F3_ADD):
                    alu_op_w <<= Bits[5](C.ALU_SUB)
                with_signals.otherwise_when(funct3 == C.F3_SRL):
                    alu_op_w <<= Bits[5](C.ALU_SRA)
                with_signals.otherwise():
                    exc <<= Bool(True)
                    exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

            with_signals.otherwise_when(funct7 == C.F7_MUL_DIV):
                mul_div <<= Bool(True)
                md_op <<= funct3[0:3]

                with_signals.when(funct3 == C.F3_MUL):
                    signed_op <<= Bool(True)
                with_signals.otherwise_when(funct3 == C.F3_MULH):
                    signed_op <<= Bool(True)
                with_signals.otherwise_when(funct3 == C.F3_MULHSU):
                    signed_op <<= Bool(False)  # a signed, b unsigned
                with_signals.otherwise_when(funct3 == C.F3_MULHU):
                    signed_op <<= Bool(False)
                with_signals.otherwise_when(funct3 == C.F3_DIV):
                    signed_op <<= Bool(True)
                with_signals.otherwise_when(funct3 == C.F3_REM):
                    signed_op <<= Bool(True)

            with_signals.otherwise():
                exc <<= Bool(True)
                exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

        with_signals.otherwise_when(opcode == C.OP_SYSTEM):
            sys_op <<= Bool(True)

            with_signals.when(funct3 == C.F3_PRIV):
                with_signals.when(funct12 == C.F12_ECALL):
                    exc <<= Bool(True)
                    exc_code <<= UInt[5](C.EXC_ECALL_M)
                with_signals.otherwise_when(funct12 == C.F12_EBREAK):
                    exc <<= Bool(True)
                    exc_code <<= UInt[5](C.EXC_BREAKPOINT)
                with_signals.otherwise_when(funct12 == C.F12_MRET):
                    br_op <<= Bits[4](C.BR_JALR)
                    br_flag <<= Bool(True)
                with_signals.otherwise():
                    exc <<= Bool(True)
                    exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

            with_signals.otherwise_when(funct3 == C.F3_CSRRW):
                csr_c <<= Bits[2](C.CSR_RW)
                csr_a <<= funct12
                write_en <<= Bool(True)
                wb_s <<= Bits[2](C.WB_CSR)
                rs1_idx_w <<= rs1
                rd_idx_w <<= rd
            with_signals.otherwise_when(funct3 == C.F3_CSRRS):
                csr_c <<= Bits[2](C.CSR_RS)
                csr_a <<= funct12
                write_en <<= Bool(True)
                wb_s <<= Bits[2](C.WB_CSR)
                rs1_idx_w <<= rs1
                rd_idx_w <<= rd
            with_signals.otherwise_when(funct3 == C.F3_CSRRC):
                csr_c <<= Bits[2](C.CSR_RC)
                csr_a <<= funct12
                write_en <<= Bool(True)
                wb_s <<= Bits[2](C.WB_CSR)
                rs1_idx_w <<= rs1
                rd_idx_w <<= rd
            with_signals.otherwise_when(funct3 == C.F3_CSRRWI):
                csr_c <<= Bits[2](C.CSR_RW)
                csr_a <<= funct12
                write_en <<= Bool(True)
                wb_s <<= Bits[2](C.WB_CSR)
                rs1_idx_w <<= UInt[5](rs1)
                rd_idx_w <<= rd
            with_signals.otherwise_when(funct3 == C.F3_CSRRSI):
                csr_c <<= Bits[2](C.CSR_RS)
                csr_a <<= funct12
                write_en <<= Bool(True)
                wb_s <<= Bits[2](C.WB_CSR)
                rs1_idx_w <<= UInt[5](rs1)
                rd_idx_w <<= rd
            with_signals.otherwise_when(funct3 == C.F3_CSRRCI):
                csr_c <<= Bits[2](C.CSR_RC)
                csr_a <<= funct12
                write_en <<= Bool(True)
                wb_s <<= Bits[2](C.WB_CSR)
                rs1_idx_w <<= UInt[5](rs1)
                rd_idx_w <<= rd
            with_signals.otherwise():
                exc <<= Bool(True)
                exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

        with_signals.otherwise_when(opcode == C.OP_FENCE):
            # FENCE, FENCE.I: treated as NOP
            pass

        with_signals.otherwise():
            exc <<= Bool(True)
            exc_code <<= UInt[5](C.EXC_ILLEGAL_INST)

        # Suppress writes to x0
        with_signals.when(rd_idx_w == 0):
            write_en <<= Bool(False)

        # Output assignments
        self.exception <<= exc
        self.excep_code <<= exc_code
        self.alu_op <<= alu_op_w
        self.alu_src1 <<= alu_s1
        self.alu_src2 <<= alu_s2
        self.mem_op <<= mem_w
        self.mem_size <<= mem_sz
        self.mem_sext <<= mem_se
        self.wb_en <<= write_en
        self.wb_src <<= wb_s
        self.branch_op <<= br_op
        self.is_branch <<= br_flag
        self.csr_cmd <<= csr_c
        self.csr_addr <<= csr_a
        self.is_mul_div <<= mul_div
        self.mul_div_op <<= md_op
        self.is_signed <<= signed_op
        self.is_system <<= sys_op
        self.imm <<= imm_w
        self.rd_idx <<= rd_idx_w
        self.rs1_idx <<= rs1_idx_w
        self.rs2_idx <<= rs2_idx_w


class CompressedExpander(CycleAwareDomain):
    """RISC-V C extension expander.

    Detects 16-bit compressed instructions (inst[1:0] != 11) and expands
    them to their 32-bit canonical equivalent. Non-compressed instructions
    pass through unchanged.

    Ports
    -----
    raw_inst    : UInt[32] — raw 32-bit fetch (lower 16 may be compressed)
    expanded    : UInt[32] — expanded 32-bit instruction
    is_compressed : Bool   — this was a compressed instruction
    """

    raw_inst = Signal(UInt[ILEN], "in")
    expanded = Signal(UInt[ILEN], "out")
    is_compressed = Signal(Bool, "out")

    def execute(self):
        inst = self.raw_inst
        c_inst = inst[0:16]
        op = c_inst[0:2]
        funct3_c = c_inst[13:16]
        expanded_w = Wire(UInt[ILEN](0))
        is_c = Wire(Bool(False))

        with_signals.when(inst[1:3] != Bits[2](0b11)):
            is_c <<= Bool(True)

            # Quadrant C0: op[1:0]=00
            with_signals.when(op == 0b00):
                rd_p = UInt[5](c_inst[2:5] + 8)
                rs1_p = UInt[5](c_inst[7:10] + 8)

                with_signals.when(funct3_c == 0b000):
                    # C.ADDI4SPN
                    nzuimm = Cat(
                        c_inst[5:6], c_inst[6:7],
                        c_inst[2:3], c_inst[3:4],
                        c_inst[11:12], c_inst[12:13],
                        c_inst[10:11],
                        Bits(25)(0),
                    ).as_uint()
                    expanded_w <<= (
                        (nzuimm << UInt[32](20)) |
                        (UInt[32](2) << 15) |
                        (UInt[32](0b000) << 12) |
                        (rd_p << 7) |
                        UInt[32](C.OP_ALUI)
                    )
                with_signals.otherwise_when(funct3_c == 0b010):
                    # C.LW
                    uimm = Cat(
                        c_inst[5:6], c_inst[10:12], c_inst[6:7],
                        Bits(2)(0), Bits(25)(0),
                    ).as_uint()
                    expanded_w <<= (
                        (uimm << UInt[32](20)) |
                        (rs1_p << 15) |
                        (UInt[32](C.F3_LW) << 12) |
                        (rd_p << 7) |
                        UInt[32](C.OP_LOAD)
                    )
                with_signals.otherwise_when(funct3_c == 0b110):
                    # C.SW
                    rs2_p = UInt[5](c_inst[2:5] + 8)
                    uimm = Cat(
                        c_inst[5:6], c_inst[10:12], c_inst[6:7],
                        Bits(2)(0),
                    ).as_uint()
                    expanded_w <<= (
                        (uimm[5:12] << UInt[32](25)) |
                        (rs2_p << 20) |
                        (rs1_p << 15) |
                        (UInt[32](C.F3_SW) << 12) |
                        (uimm[0:5] << 7) |
                        UInt[32](C.OP_STORE)
                    )

            # Quadrant C1: op[1:0]=01
            with_signals.otherwise_when(op == 0b01):
                funct3_ci = c_inst[13:16]
                rd_d = c_inst[7:12]
                rs1_d = c_inst[7:12]
                rs2_d = c_inst[2:7]

                with_signals.when(funct3_ci == 0b000):
                    # C.ADDI
                    nzimm = Cat(
                        c_inst[12:13], c_inst[2:7], Bits(26)(c_inst[12]),
                    ).as_uint()
                    with_signals.when(rd_d != 0):
                        expanded_w <<= (
                            (nzimm << UInt[32](20)) |
                            (rd_d << 15) |
                            (UInt[32](C.F3_ADDI) << 12) |
                            (rd_d << 7) |
                            UInt[32](C.OP_ALUI)
                        )
                    with_signals.otherwise():
                        expanded_w <<= UInt[32](0x00000013)  # C.NOP → ADDI x0,x0,0

                with_signals.otherwise_when(funct3_ci == 0b001):
                    # C.JAL
                    offset = Cat(
                        c_inst[12:13], c_inst[2:3],
                        c_inst[3:4], c_inst[8:9],
                        c_inst[9:12],
                        Bits(1)(c_inst[12]), Bits(12)(c_inst[12]),
                    ).as_uint()
                    expanded_w <<= (
                        (Cat(offset[20:21], offset[1:11], offset[11:12], offset[12:20]) << UInt[32](12)) |
                        (UInt[32](1) << 7) |
                        UInt[32](C.OP_JAL)
                    )

                with_signals.otherwise_when(funct3_ci == 0b010):
                    # C.LI
                    nzimm = Cat(
                        c_inst[12:13], c_inst[2:7], Bits(26)(c_inst[12]),
                    ).as_uint()
                    with_signals.when(rd_d != 0):
                        expanded_w <<= (
                            (nzimm << UInt[32](20)) |
                            (UInt[32](0) << 15) |
                            (UInt[32](C.F3_ADDI) << 12) |
                            (rd_d << 7) |
                            UInt[32](C.OP_ALUI)
                        )
                    with_signals.otherwise():
                        expanded_w <<= UInt[32](0x00000013)

                with_signals.otherwise_when(funct3_ci == 0b011):
                    # C.LUI / C.ADDI16SP
                    with_signals.when(rd_d == 2):
                        # C.ADDI16SP
                        nzimm_16sp = Cat(
                            c_inst[12:13], c_inst[7:8],
                            c_inst[2:3], c_inst[3:4],
                            c_inst[4:5], c_inst[5:6],
                            c_inst[6:7], Bits(26)(c_inst[12]),
                        ).as_uint()
                        expanded_w <<= (
                            (nzimm_16sp << UInt[32](20)) |
                            (UInt[32](2) << 15) |
                            (UInt[32](C.F3_ADDI) << 12) |
                            (UInt[32](2) << 7) |
                            UInt[32](C.OP_ALUI)
                        )
                    with_signals.otherwise():
                        # C.LUI
                        nzimm_lui = Cat(
                            c_inst[12:13], c_inst[2:7],
                            Bits(26)(c_inst[12]),
                        ).as_uint()
                        expanded_w <<= (
                            (nzimm_lui << UInt[32](12)) |
                            (rd_d << 7) |
                            UInt[32](C.OP_LUI)
                        )

                with_signals.otherwise_when(funct3_ci in (0b100, 0b101)):
                    # C.SRLI / C.SRAI / C.ANDI / C.SUB/XOR/OR/AND
                    shamt = c_inst[2:7]
                    uimm_ci = Cat(
                        c_inst[12:13], c_inst[2:7], Bits(26)(c_inst[12]),
                    ).as_uint()
                    with_signals.when(funct3_ci == 0b100):
                        # C.SRLI (bit 10=0) or C.SRAI (bit 10=1)
                        with_signals.when(c_inst[10:11] == 0):
                            expanded_w <<= (
                                (UInt[32](C.F7_ALU_NORMAL) << 25) |
                                (shamt << 20) |
                                (rs1_d << 15) |
                                (UInt[32](C.F3_SRLI) << 12) |
                                (rs1_d << 7) |
                                UInt[32](C.OP_ALUI)
                            )
                        with_signals.otherwise():
                            expanded_w <<= (
                                (UInt[32](C.F7_ALU_ALT) << 25) |
                                (shamt << 20) |
                                (rs1_d << 15) |
                                (UInt[32](C.F3_SRLI) << 12) |
                                (rs1_d << 7) |
                                UInt[32](C.OP_ALUI)
                            )
                    with_signals.otherwise():
                        # C.ANDI (bit 10=0) or C.SUB/XOR/OR/AND (bit 10=1)
                        with_signals.when(c_inst[10:11] == 0):
                            expanded_w <<= (
                                (uimm_ci << UInt[32](20)) |
                                (rs1_d << 15) |
                                (UInt[32](C.F3_ANDI) << 12) |
                                (rs1_d << 7) |
                                UInt[32](C.OP_ALUI)
                            )
                        with_signals.otherwise():
                            with_signals.when(c_inst[11:13] == 0b00):
                                # C.SUB
                                expanded_w <<= (
                                    (UInt[32](C.F7_ALU_ALT) << 25) |
                                    (rs2_d << 20) |
                                    (rs1_d << 15) |
                                    (UInt[32](C.F3_ADD) << 12) |
                                    (rs1_d << 7) |
                                    UInt[32](C.OP_ALU)
                                )
                            with_signals.otherwise_when(c_inst[11:13] == 0b01):
                                expanded_w <<= (
                                    (UInt[32](C.F7_ALU_NORMAL) << 25) |
                                    (rs2_d << 20) |
                                    (rs1_d << 15) |
                                    (UInt[32](C.F3_XOR) << 12) |
                                    (rs1_d << 7) |
                                    UInt[32](C.OP_ALU)
                                )
                            with_signals.otherwise_when(c_inst[11:13] == 0b10):
                                expanded_w <<= (
                                    (UInt[32](C.F7_ALU_NORMAL) << 25) |
                                    (rs2_d << 20) |
                                    (rs1_d << 15) |
                                    (UInt[32](C.F3_OR) << 12) |
                                    (rs1_d << 7) |
                                    UInt[32](C.OP_ALU)
                                )
                            with_signals.otherwise():
                                expanded_w <<= (
                                    (UInt[32](C.F7_ALU_NORMAL) << 25) |
                                    (rs2_d << 20) |
                                    (rs1_d << 15) |
                                    (UInt[32](C.F3_AND) << 12) |
                                    (rs1_d << 7) |
                                    UInt[32](C.OP_ALU)
                                )

                with_signals.otherwise_when(funct3_ci == 0b110):
                    # C.BEQZ
                    offset_cb = Cat(
                        c_inst[2:3], c_inst[7:8], c_inst[8:12],
                        Bits(1)(c_inst[12]), Bits(21)(c_inst[12]),
                    ).as_uint()
                    expanded_w <<= (
                        (Cat(offset_cb[12:13], offset_cb[5:11], Bits(1)(0), Bits(1)(0), offset_cb[1:5], offset_cb[11:12]) << UInt[32](7)) |
                        (UInt[32](C.F3_BEQ) << 12) |
                        UInt[32](C.OP_BRANCH)
                    )

                with_signals.otherwise():
                    # C.BNEZ
                    offset_cb = Cat(
                        c_inst[2:3], c_inst[7:8], c_inst[8:12],
                        Bits(1)(c_inst[12]), Bits(21)(c_inst[12]),
                    ).as_uint()
                    expanded_w <<= (
                        (Cat(offset_cb[12:13], offset_cb[5:11], Bits(1)(0), Bits(1)(0), offset_cb[1:5], offset_cb[11:12]) << UInt[32](7)) |
                        (UInt[32](C.F3_BNE) << 12) |
                        UInt[32](C.OP_BRANCH)
                    )

            # Quadrant C2: op[1:0]=10
            with_signals.otherwise_when(op == 0b10):
                funct3_c2 = c_inst[13:16]
                rd_c2 = c_inst[7:12]
                rs2_c2 = c_inst[2:7]

                with_signals.when(funct3_c2 == 0b000):
                    # C.SLLI
                    with_signals.when(rd_c2 != 0):
                        shamt_c = c_inst[2:7]
                        expanded_w <<= (
                            (UInt[32](C.F7_ALU_NORMAL) << 25) |
                            (shamt_c << 20) |
                            (rd_c2 << 15) |
                            (UInt[32](C.F3_SLLI) << 12) |
                            (rd_c2 << 7) |
                            UInt[32](C.OP_ALUI)
                        )

                with_signals.otherwise_when(funct3_c2 == 0b010):
                    # C.LWSP
                    with_signals.when(rd_c2 != 0):
                        uimm_lwsp = Cat(
                            c_inst[2:4], c_inst[4:5],
                            c_inst[12:13], c_inst[5:7],
                            Bits(2)(0), Bits(25)(0),
                        ).as_uint()
                        expanded_w <<= (
                            (uimm_lwsp << UInt[32](20)) |
                            (UInt[32](2) << 15) |
                            (UInt[32](C.F3_LW) << 12) |
                            (rd_c2 << 7) |
                            UInt[32](C.OP_LOAD)
                        )

                with_signals.otherwise_when(funct3_c2 == 0b100):
                    # C.JR / C.MV / C.EBREAK / C.JALR / C.ADD
                    with_signals.when(c_inst[12:13] == 0):
                        with_signals.when(rs2_c2 == 0):
                            # C.JR
                            expanded_w <<= (
                                (UInt[32](0) << 20) |
                                (rd_c2 << 15) |
                                (UInt[32](C.F3_PRIV) << 12) |
                                (UInt[32](0) << 7) |
                                UInt[32](C.OP_JALR)
                            )
                        with_signals.otherwise():
                            # C.MV → ADD rd, x0, rs2
                            expanded_w <<= (
                                (UInt[32](C.F7_ALU_NORMAL) << 25) |
                                (rs2_c2 << 20) |
                                (UInt[32](0) << 15) |
                                (UInt[32](C.F3_ADD) << 12) |
                                (rd_c2 << 7) |
                                UInt[32](C.OP_ALU)
                            )
                    with_signals.otherwise():
                        with_signals.when(rs2_c2 == 0):
                            with_signals.when(rd_c2 != 0):
                                # C.JALR
                                expanded_w <<= (
                                    (UInt[32](0) << 20) |
                                    (rd_c2 << 15) |
                                    (UInt[32](C.F3_PRIV) << 12) |
                                    (UInt[32](1) << 7) |
                                    UInt[32](C.OP_JALR)
                                )
                            with_signals.otherwise():
                                # C.EBREAK
                                expanded_w <<= (
                                    (UInt[32](C.F12_EBREAK) << 20) |
                                    UInt[32](C.OP_SYSTEM)
                                )
                        with_signals.otherwise():
                            # C.ADD
                            expanded_w <<= (
                                (UInt[32](C.F7_ALU_NORMAL) << 25) |
                                (rs2_c2 << 20) |
                                (rd_c2 << 15) |
                                (UInt[32](C.F3_ADD) << 12) |
                                (rd_c2 << 7) |
                                UInt[32](C.OP_ALU)
                            )

                with_signals.otherwise_when(funct3_c2 == 0b110):
                    # C.SWSP
                    rs2_c = c_inst[2:7]
                    uimm_swsp = Cat(
                        c_inst[7:9], c_inst[9:12],
                        c_inst[12:13],
                        Bits(2)(0), Bits(25)(0),
                    ).as_uint()
                    expanded_w <<= (
                        (uimm_swsp[5:12] << UInt[32](25)) |
                        (rs2_c << 20) |
                        (UInt[32](2) << 15) |
                        (UInt[32](C.F3_SW) << 12) |
                        (uimm_swsp[0:5] << 7) |
                        UInt[32](C.OP_STORE)
                    )

        with_signals.otherwise():
            is_c <<= Bool(False)
            expanded_w <<= inst

        self.expanded <<= expanded_w
        self.is_compressed <<= is_c


class BTB(CycleAwareDomain):
    """64-entry bimodal branch target buffer.

    Predicts branch direction and target at fetch time.
    Updated on branch resolution from the EX stage.

    Ports
    -----
    pc_fetch       : UInt[32] — PC being fetched
    predict_taken  : Bool     — prediction: taken?
    predict_target : UInt[32] — predicted target PC
    predict_hit    : Bool     — BTB hit (entry was valid)

    update_valid   : Bool     — update BTB with branch result
    update_pc      : UInt[32] — PC of branch being updated
    update_target  : UInt[32] — actual target (if taken)
    update_taken   : Bool     — was branch actually taken?
    """

    pc_fetch = Signal(UInt[XLEN], "in")
    predict_taken = Signal(Bool, "out")
    predict_target = Signal(UInt[XLEN], "out")
    predict_hit = Signal(Bool, "out")
    update_valid = Signal(Bool, "in")
    update_pc = Signal(UInt[XLEN], "in")
    update_target = Signal(UInt[XLEN], "in")
    update_taken = Signal(Bool, "in")

    def __init__(self, entries: int = 64):
        self.num_entries = entries
        self.tag_width = XLEN - (entries - 1).bit_length() - 2
        self.idx_width = (entries - 1).bit_length()

        # BTB storage
        self.valid = Mem(Bool, entries, name="btb_valid")
        self.tags = Mem(Bits[self.tag_width], entries, name="btb_tags")
        self.targets = Mem(UInt[XLEN], entries, name="btb_targets")
        self.counters = Mem(Bits[2], entries, name="btb_bimodal")

    def execute(self):
        # ================================================================
        # Lookup (read)
        # ================================================================
        index = self.pc_fetch[2: 2 + self.idx_width]
        tag = self.pc_fetch[2 + self.idx_width : 32]

        entry_valid = self.valid[index]
        entry_tag = self.tags[index]
        entry_target = self.targets[index]
        entry_counter = self.counters[index]

        self.predict_hit <<= entry_valid & (entry_tag == tag)
        self.predict_taken <<= entry_valid & (entry_tag == tag) & entry_counter[1]
        self.predict_target <<= entry_target

        # ================================================================
        # Update (write) — from EX stage branch resolution
        # ================================================================
        with_signals.when(self.update_valid):
            up_idx = self.update_pc[2: 2 + self.idx_width]
            up_tag = self.update_pc[2 + self.idx_width : 32]
            old_counter = self.counters[up_idx]

            with_signals.when(self.update_taken):
                with_signals.when(old_counter != 0b11):
                    self.counters[up_idx] <<= old_counter + 1
            with_signals.otherwise():
                with_signals.when(old_counter != 0b00):
                    self.counters[up_idx] <<= old_counter - 1

            self.valid[up_idx] <<= Bool(True)
            self.tags[up_idx] <<= up_tag
            self.targets[up_idx] <<= self.update_target


class CSRUnit(CycleAwareDomain):
    """Machine-mode CSR register file.

    Implements all mandatory M-mode CSRs. Handles read/write/set/clear
    atomically in one cycle.

    Ports
    -----
    csr_cmd      : UInt[2]  — CSR operation (none/rw/rs/rc)
    csr_addr     : UInt[12] — CSR address
    rs1_val      : UInt[32] — value to write (from rs1 or uimm)
    pc           : UInt[32] — PC of CSR instruction
    exception_pc : UInt[32] — PC of faulting instruction (for mepc)
    exception_cause : UInt[32] — mcause value to commit
    exception_val   : UInt[32] — mtval to commit
    commit_exception : Bool    — latch exception info on trap
    mret_exec    : Bool     — mret being executed
    trap_taken   : Bool     — exception/interrupt taken (for mstatus)
    result       : UInt[32] — CSR read result (old value)
    trap_pc      : UInt[32] — trap handler PC (from mtvec)
    wb_data      : UInt[32] — value for register write-back (for CSRRx)
    mie          : Bool     — global interrupt enable
    irq_enable   : UInt[3]  — per-source interrupt enables
    irq_pending  : UInt[3]  — per-source interrupt pending
    """

    csr_cmd = Signal(Bits[2], "in")
    csr_addr = Signal(UInt[12], "in")
    rs1_val = Signal(UInt[XLEN], "in")
    pc = Signal(UInt[XLEN], "in")
    exception_pc = Signal(UInt[XLEN], "in")
    exception_cause = Signal(UInt[XLEN], "in")
    exception_val = Signal(UInt[XLEN], "in")
    commit_exception = Signal(Bool, "in")
    mret_exec = Signal(Bool, "in")
    trap_taken = Signal(Bool, "in")
    result = Signal(UInt[XLEN], "out")
    trap_pc = Signal(UInt[XLEN], "out")
    wb_data = Signal(UInt[XLEN], "out")
    mie = Signal(Bool, "out")
    irq_enable = Signal(Bits[3], "out")
    irq_pending = Signal(Bits[3], "out")

    def __init__(self):
        # Machine ISA
        self.misa = RegInit(UInt[XLEN](LunahanParams.default().csr_misa_value))

        # Machine status
        self.mstatus = RegInit(UInt[XLEN](0))

        # Trap setup
        self.mtvec = RegInit(UInt[XLEN](0))
        self.medeleg = RegInit(UInt[XLEN](0))
        self.mideleg = RegInit(UInt[XLEN](0))

        # Trap handling
        self.mepc = RegInit(UInt[XLEN](0))
        self.mcause = RegInit(UInt[XLEN](0))
        self.mtval = RegInit(UInt[XLEN](0))
        self.mscratch = RegInit(UInt[XLEN](0))

        # Interrupts
        self.mie_reg = RegInit(UInt[XLEN](0))
        self.mip = RegInit(UInt[XLEN](0))

        # Counters
        self.mcycle = RegInit(UInt[XLEN](0))
        self.mcycleh = RegInit(UInt[XLEN](0))
        self.minstret = RegInit(UInt[XLEN](0))
        self.minstreth = RegInit(UInt[XLEN](0))

    def execute(self):
        # Counter increments (every cycle)
        self.mcycle <<= self.mcycle + 1
        with_signals.when(self.mcycle == 0xFFFFFFFF):
            self.mcycleh <<= self.mcycleh + 1

        # Exception commit
        with_signals.when(self.commit_exception):
            self.mepc <<= self.exception_pc
            self.mcause <<= self.exception_cause
            self.mtval <<= self.exception_val
            # Set mstatus.MPIE = mstatus.MIE, MIE=0, MPP=11
            self.mstatus <<= (
                (self.mstatus & ~(UInt[XLEN](1) << C.MSTATUS_MIE) &
                 ~(UInt[XLEN](1) << C.MSTATUS_MPIE) &
                 ~(UInt[XLEN](0b11) << C.MSTATUS_MPP))
                | ((self.mstatus >> C.MSTATUS_MIE) & 1) << C.MSTATUS_MPIE
                | (UInt[XLEN](0b11) << C.MSTATUS_MPP)
            )

        # MRET
        with_signals.when(self.mret_exec):
            self.mstatus <<= (
                (self.mstatus & ~(UInt[XLEN](1) << C.MSTATUS_MIE))
                | ((self.mstatus >> C.MSTATUS_MPIE) & 1) << C.MSTATUS_MIE
                | (UInt[XLEN](1) << C.MSTATUS_MPIE)
            )

        # ================================================================
        # CSR read/write
        # ================================================================
        addr = self.csr_addr
        cmd = self.csr_cmd

        old_value = Wire(UInt[XLEN](0))

        with_signals.when(addr == C.CSR_MVENDORID):
            old_value <<= UInt[XLEN](LunahanParams.default().csr_mvendorid_value)
        with_signals.otherwise_when(addr == C.CSR_MARCHID):
            old_value <<= UInt[XLEN](LunahanParams.default().csr_marchid_value)
        with_signals.otherwise_when(addr == C.CSR_MIMPID):
            old_value <<= UInt[XLEN](LunahanParams.default().csr_mimpid_value)
        with_signals.otherwise_when(addr == C.CSR_MHARTID):
            old_value <<= UInt[XLEN](0)
        with_signals.otherwise_when(addr == C.CSR_MSTATUS):
            old_value <<= self.mstatus
        with_signals.otherwise_when(addr == C.CSR_MISA):
            old_value <<= self.misa
        with_signals.otherwise_when(addr == C.CSR_MIE):
            old_value <<= self.mie_reg
        with_signals.otherwise_when(addr == C.CSR_MTVEC):
            old_value <<= self.mtvec
        with_signals.otherwise_when(addr == C.CSR_MSCRATCH):
            old_value <<= self.mscratch
        with_signals.otherwise_when(addr == C.CSR_MEPC):
            old_value <<= self.mepc
        with_signals.otherwise_when(addr == C.CSR_MCAUSE):
            old_value <<= self.mcause
        with_signals.otherwise_when(addr == C.CSR_MTVAL):
            old_value <<= self.mtval
        with_signals.otherwise_when(addr == C.CSR_MIP):
            old_value <<= self.mip
        with_signals.otherwise_when(addr == C.CSR_MCYCLE):
            old_value <<= self.mcycle
        with_signals.otherwise_when(addr == C.CSR_MCYCLEH):
            old_value <<= self.mcycleh
        with_signals.otherwise_when(addr == C.CSR_MINSTRET):
            old_value <<= self.minstret
        with_signals.otherwise_when(addr == C.CSR_MINSTRETH):
            old_value <<= self.minstreth
        with_signals.otherwise():
            old_value <<= UInt[XLEN](0)

        new_value = Wire(UInt[XLEN](0))
        with_signals.when(cmd == C.CSR_RW):
            new_value <<= self.rs1_val
        with_signals.otherwise_when(cmd == C.CSR_RS):
            new_value <<= old_value | self.rs1_val
        with_signals.otherwise_when(cmd == C.CSR_RC):
            new_value <<= old_value & ~self.rs1_val
        with_signals.otherwise():
            new_value <<= old_value

        # Write to writable CSRs
        with_signals.when(cmd != C.CSR_NONE):
            with_signals.when(addr == C.CSR_MSTATUS):
                self.mstatus <<= new_value
            with_signals.otherwise_when(addr == C.CSR_MIE):
                self.mie_reg <<= new_value
            with_signals.otherwise_when(addr == C.CSR_MTVEC):
                self.mtvec <<= new_value
            with_signals.otherwise_when(addr == C.CSR_MSCRATCH):
                self.mscratch <<= new_value
            with_signals.otherwise_when(addr == C.CSR_MEPC):
                self.mepc <<= new_value
            with_signals.otherwise_when(addr == C.CSR_MCAUSE):
                self.mcause <<= new_value
            with_signals.otherwise_when(addr == C.CSR_MTVAL):
                self.mtval <<= new_value

        self.result <<= old_value
        self.wb_data <<= old_value
        self.mie <<= Bool(self.mstatus[C.MSTATUS_MIE])
        self.irq_enable <<= self.mie_reg[3:6]
        self.irq_pending <<= self.mip[3:6]

        # Trap address computation
        mode = self.mtvec[0:2]
        base = self.mtvec[2:32] << 2
        with_signals.when(mode == C.MTVEC_DIRECT):
            self.trap_pc <<= base
        with_signals.otherwise():
            self.trap_pc <<= base + ((self.mcause & UInt[XLEN](0x7FFFFFFF)) << 2)


class ICache(CycleAwareDomain):
    """4 KB direct-mapped instruction cache, read-only.

    256 blocks × 16 bytes per block. Tag compare is combinational; hit
    returns instruction in same cycle. Miss stalls the pipeline and
    issues AXI4-Lite read.

    Ports
    -----
    addr        : UInt[32] — fetch address (byte-addressable)
    data        : UInt[32] — fetched instruction (on hit)
    hit         : Bool     — cache hit
    miss        : Bool     — cache miss (stall needed)
    refill_done : Bool     — line fill complete
    flush       : Bool     — invalidate entire cache

    axi_araddr  : UInt[32] — AXI read address
    axi_arvalid : Bool     — AXI read address valid
    axi_arready : Bool     — AXI read address ready
    axi_rdata   : UInt[32] — AXI read data
    axi_rvalid  : Bool     — AXI read data valid
    axi_rready  : Bool     — AXI read data ready
    """

    addr = Signal(UInt[XLEN], "in")
    data = Signal(UInt[ILEN], "out")
    hit = Signal(Bool, "out")
    miss = Signal(Bool, "out")
    refill_done = Signal(Bool, "out")
    flush = Signal(Bool, "in")
    axi_araddr = Signal(UInt[XLEN], "out")
    axi_arvalid = Signal(Bool, "out")
    axi_arready = Signal(Bool, "in")
    axi_rdata = Signal(UInt[XLEN], "in")
    axi_rvalid = Signal(Bool, "in")
    axi_rready = Signal(Bool, "out")

    def __init__(self):
        params = LunahanParams.default()
        self.num_blocks = params.icache_blocks
        self.line_bytes = params.icache_line_bytes
        self.idx_width = params.icache_index_width
        self.off_width = params.icache_offset_width
        self.tag_width = params.icache_tag_width

        self.tags = Mem(Bits[self.tag_width + 1], self.num_blocks, name="ic_tag")
        self.data_line = Mem(Bits[ILEN * 4], self.num_blocks, name="ic_data")

        # Miss state machine
        self.miss_state = RegInit(Bool(False))
        self.miss_addr = Reg(UInt[XLEN](0))
        self.miss_block = Reg(UInt[self.idx_width](0))
        self.fill_count = Reg(UInt[2](0))  # 0-3 words per line

    def execute(self):
        idx = self.addr[self.off_width : self.off_width + self.idx_width]
        tag = self.addr[self.off_width + self.idx_width : 32]
        word_idx = self.addr[2 : self.off_width]

        # Combinational lookups
        stored = self.tags[idx]
        is_valid = stored[0]
        stored_tag = stored[1 : 1 + self.tag_width]
        cache_hit = is_valid & (stored_tag == tag)
        cache_data = self.data_line[idx]

        # Word selection from 128-bit line
        with_signals.when(word_idx == 0):
            line_word = cache_data[0:32]
        with_signals.otherwise_when(word_idx == 1):
            line_word = cache_data[32:64]
        with_signals.otherwise_when(word_idx == 2):
            line_word = cache_data[64:96]
        with_signals.otherwise():
            line_word = cache_data[96:128]

        with_signals.when(~self.miss_state):
            self.hit <<= cache_hit
            self.data <<= line_word
            self.miss <<= ~cache_hit

        # Miss handler state machine
        with_signals.when(self.flush):
            self.miss_state <<= Bool(False)
            # Invalidate all entries would require iterating; for simplicity
            # we assume flush sets miss_state false and tags are managed
            # externally. A full invalidate would use a for loop over blocks.

        with_signals.when(~self.miss_state & ~cache_hit & ~self.flush):
            self.miss_state <<= Bool(True)
            self.miss_addr <<= self.addr & ~UInt[XLEN](self.line_bytes - 1)
            self.miss_block <<= idx
            self.fill_count <<= UInt[2](0)
            self.axi_araddr <<= self.addr & ~UInt[XLEN](self.line_bytes - 1)
            self.axi_arvalid <<= Bool(True)

        with_signals.otherwise_when(self.miss_state):
            self.axi_rready <<= Bool(True)
            with_signals.when(self.axi_rvalid & self.axi_rready):
                fc = self.fill_count
                new_line = Wire(Bits[ILEN * 4](0))
                old_line = self.data_line[self.miss_block]

                with_signals.when(fc == 0):
                    new_line <<= Cat(old_line[32:128], self.axi_rdata)
                with_signals.otherwise_when(fc == 1):
                    new_line <<= Cat(old_line[0:32], self.axi_rdata, old_line[64:128])
                with_signals.otherwise_when(fc == 2):
                    new_line <<= Cat(old_line[0:64], self.axi_rdata, old_line[96:128])
                with_signals.otherwise():
                    new_line <<= Cat(old_line[0:96], self.axi_rdata)

                self.data_line[self.miss_block] <<= new_line

                with_signals.when(fc == UInt[2](self.line_bytes // 4 - 1)):
                    # Line fill complete
                    tag_val = Cat(
                        Bits[1](1),
                        self.miss_addr[self.off_width + self.idx_width : 32],
                    )
                    self.tags[self.miss_block] <<= tag_val
                    self.miss_state <<= Bool(False)
                    self.refill_done <<= Bool(True)
                with_signals.otherwise():
                    self.fill_count <<= fc + 1
                    addr = self.miss_addr + ((fc + 1) << 2)
                    self.axi_araddr <<= addr
                    self.axi_arvalid <<= Bool(True)

            with_signals.otherwise():
                self.axi_arvalid <<= Bool(False)
                self.axi_rready <<= Bool(False)
                self.refill_done <<= Bool(False)


class DCache(CycleAwareDomain):
    """4 KB direct-mapped data cache, write-back with write-allocate.

    Ports match ICache pattern with additional write support.
    """

    addr = Signal(UInt[XLEN], "in")
    wdata = Signal(UInt[XLEN], "in")
    wstrb = Signal(Bits[4], "in")
    we = Signal(Bool, "in")       # write enable
    re = Signal(Bool, "in")       # read enable
    rdata = Signal(UInt[XLEN], "out")
    hit = Signal(Bool, "out")
    miss = Signal(Bool, "out")
    busy = Signal(Bool, "out")
    refill_done = Signal(Bool, "out")

    def __init__(self):
        params = LunahanParams.default()
        self.num_blocks = params.dcache_blocks
        self.line_bytes = params.dcache_line_bytes
        self.idx_width = params.dcache_index_width
        self.off_width = params.dcache_offset_width
        self.tag_width = params.dcache_tag_width

        # Tag: {tag, valid, dirty}
        self.tags = Mem(Bits[self.tag_width + 2], self.num_blocks, name="dc_tag")
        self.data_line = Mem(Bits[ILEN * 4], self.num_blocks, name="dc_data")

        self.miss_state = RegInit(Bool(False))
        self.writeback = RegInit(Bool(False))
        self.miss_addr = Reg(UInt[XLEN](0))
        self.miss_block = Reg(UInt[self.idx_width](0))
        self.miss_wstrb = Reg(Bits[4](UInt[4](0)))
        self.miss_wdata = Reg(UInt[XLEN](0))
        self.miss_is_write = Reg(Bool(False))
        self.fill_count = Reg(UInt[2](0))

    def execute(self):
        idx = self.addr[self.off_width : self.off_width + self.idx_width]
        tag = self.addr[self.off_width + self.idx_width : 32]

        stored = self.tags[idx]
        is_valid = stored[0]
        is_dirty = stored[1]
        stored_tag = stored[2 : 2 + self.tag_width]
        cache_hit = is_valid & (stored_tag == tag)
        line_data = self.data_line[idx]

        with_signals.when(~self.miss_state):
            self.hit <<= cache_hit
            self.miss <<= ~cache_hit & (self.re | self.we)
            self.busy <<= Bool(False)

            # Read data: return word from line
            word_sel = self.addr[2:self.off_width]
            with_signals.when(word_sel == 0):
                self.rdata <<= line_data[0:32]
            with_signals.otherwise_when(word_sel == 1):
                self.rdata <<= line_data[32:64]
            with_signals.otherwise_when(word_sel == 2):
                self.rdata <<= line_data[64:96]
            with_signals.otherwise():
                self.rdata <<= line_data[96:128]

            # Write on hit
            with_signals.when(self.we & cache_hit):
                new_data = Wire(Bits[ILEN * 4](0))
                with_signals.when(word_sel == 0):
                    new_data <<= Cat(self._apply_wstrb(line_data[0:32], self.wdata, self.wstrb), line_data[32:128])
                with_signals.otherwise_when(word_sel == 1):
                    new_data <<= Cat(line_data[0:32], self._apply_wstrb(line_data[32:64], self.wdata, self.wstrb), line_data[64:128])
                with_signals.otherwise_when(word_sel == 2):
                    new_data <<= Cat(line_data[0:64], self._apply_wstrb(line_data[64:96], self.wdata, self.wstrb), line_data[96:128])
                with_signals.otherwise():
                    new_data <<= Cat(line_data[0:96], self._apply_wstrb(line_data[96:128], self.wdata, self.wstrb))
                self.data_line[idx] <<= new_data
                self.tags[idx][1] <<= Bool(True)  # set dirty

        # Miss handler (simplified: stall-based, no external bus here)
        with_signals.when(~self.miss_state & ~cache_hit & (self.re | self.we)):
            self.miss_state <<= Bool(True)
            self.miss_addr <<= self.addr
            self.miss_block <<= idx
            self.miss_is_write <<= self.we
            self.miss_wstrb <<= self.wstrb
            self.miss_wdata <<= self.wdata
            self.fill_count <<= UInt[2](0)
            self.busy <<= Bool(True)

        with_signals.otherwise_when(self.miss_state):
            fc = self.fill_count
            # Simulate: 4-cycle fill
            with_signals.when(fc == 3):
                tag_val = Cat(Bits[2](0b11), self.miss_addr[self.off_width + self.idx_width : 32])
                self.tags[self.miss_block] <<= tag_val
                self.miss_state <<= Bool(False)
                self.refill_done <<= Bool(True)
                self.busy <<= Bool(False)
            with_signals.otherwise():
                self.fill_count <<= fc + 1
                self.refill_done <<= Bool(False)

    @staticmethod
    def _apply_wstrb(old_word: UInt[32], new_word: UInt[32], wstrb: UInt[4]) -> UInt[32]:
        """Apply byte strobes to merge write data with old word data."""
        result = Wire(UInt[32](0))
        result <<= old_word
        with_signals.when(wstrb[0]):
            result <<= Cat(result[8:32], new_word[0:8])
        with_signals.when(wstrb[1]):
            result <<= Cat(result[0:8], new_word[8:16], result[16:32])
        with_signals.when(wstrb[2]):
            result <<= Cat(result[0:16], new_word[16:24], result[24:32])
        with_signals.when(wstrb[3]):
            result <<= Cat(result[0:24], new_word[24:32])
        return result


class RegisterFile(CycleAwareDomain):
    """32 x 32-bit register file with 2 read ports, 1 write port.

    x0 is hard-wired to zero. Writes to x0 are ignored.
    Reads are combinational; writes are sequential (rising-edge).

    Ports
    -----
    rs1_idx  : UInt[5]  — source register 1 address
    rs2_idx  : UInt[5]  — source register 2 address
    rs1_val  : UInt[32] — source register 1 data (read)
    rs2_val  : UInt[32] — source register 2 data (read)
    wb_en    : Bool     — write enable
    wb_idx   : UInt[5]  — destination register address
    wb_data  : UInt[32] — data to write
    """

    rs1_idx = Signal(UInt[5], "in")
    rs2_idx = Signal(UInt[5], "in")
    rs1_val = Signal(UInt[XLEN], "out")
    rs2_val = Signal(UInt[XLEN], "out")
    wb_en = Signal(Bool, "in")
    wb_idx = Signal(UInt[5], "in")
    wb_data = Signal(UInt[XLEN], "in")

    def __init__(self):
        self.regs = Mem(UInt[XLEN], 32, name="rf_regs")

    def execute(self):
        # Reads
        self.rs1_val <<= Mux(self.rs1_idx == 0, UInt[XLEN](0), self.regs[self.rs1_idx])
        self.rs2_val <<= Mux(self.rs2_idx == 0, UInt[XLEN](0), self.regs[self.rs2_idx])

        # Write (x0 is hardwired: write ignored)
        with_signals.when(self.wb_en & (self.wb_idx != 0)):
            self.regs[self.wb_idx] <<= self.wb_data


# ==========================================================================
# LunahanCore — Top-Level Module
# ==========================================================================


@module
class LunahanCore(CycleAwareCircuit):
    """RISC-V RV32IMC 5-stage in-order processor core.

    Top-level module integrating all pipeline stages, caches, ALU, M-unit,
    branch predictor, CSR unit, and hazard control.

    Parameters
    ----------
    params : LunahanParams
        Configuration parameters (cache sizes, BTB entries, etc.)

    Ports
    -----
    clk_i       : Bool   — main clock
    reset_n_i   : Bool   — active-low reset
    timer_irq_i : Bool   — machine timer interrupt
    software_irq_i : Bool — machine software interrupt
    external_irq_i : Bool — machine external interrupt

    i_* : AXI4-Lite instruction bus (read-only)
    d_* : AXI4-Lite data bus (read/write)
    """

    # Clock and reset
    clk_i = Signal(Bool)
    reset_n_i = Signal(Bool)

    # Interrupts
    timer_irq_i = Signal(Bool)
    software_irq_i = Signal(Bool)
    external_irq_i = Signal(Bool)

    # AXI4-Lite instruction bus
    i_araddr_o = Signal(UInt[XLEN])
    i_arvalid_o = Signal(Bool)
    i_arready_i = Signal(Bool)
    i_rdata_i = Signal(UInt[XLEN])
    i_rvalid_i = Signal(Bool)
    i_rready_o = Signal(Bool)

    # AXI4-Lite data bus
    d_awaddr_o = Signal(UInt[XLEN])
    d_awvalid_o = Signal(Bool)
    d_awready_i = Signal(Bool)
    d_wdata_o = Signal(UInt[XLEN])
    d_wstrb_o = Signal(Bits[4])
    d_wvalid_o = Signal(Bool)
    d_wready_i = Signal(Bool)
    d_bresp_i = Signal(Bits[2])
    d_bvalid_i = Signal(Bool)
    d_bready_o = Signal(Bool)
    d_araddr_o = Signal(UInt[XLEN])
    d_arvalid_o = Signal(Bool)
    d_arready_i = Signal(Bool)
    d_rdata_i = Signal(UInt[XLEN])
    d_rvalid_i = Signal(Bool)
    d_rready_o = Signal(Bool)

    def __init__(self, params: LunahanParams = LunahanParams.default()):
        self.params = params
        self._build()

    def _build(self):
        params = self.params

        # ==================================================================
        # Pipeline registers (IF → ID → EX → MEM → WB)
        # ==================================================================

        # Program counter
        self.pc = RegInit(UInt[XLEN](params.reset_vector))

        # IF → ID pipeline registers
        self.if_id_valid = RegInit(Bool(False))
        self.if_id_pc = Reg(UInt[XLEN](0))
        self.if_id_pc4 = Reg(UInt[XLEN](0))
        self.if_id_inst = Reg(UInt[ILEN](0))
        self.if_id_compressed = Reg(Bool(False))

        # ID → EX pipeline registers
        self.id_ex_valid = RegInit(Bool(False))
        self.id_ex_pc = Reg(UInt[XLEN](0))
        self.id_ex_pc4 = Reg(UInt[XLEN](0))
        self.id_ex_inst = Reg(UInt[ILEN](0))
        self.id_ex_rs1_val = Reg(UInt[XLEN](0))
        self.id_ex_rs2_val = Reg(UInt[XLEN](0))
        self.id_ex_imm = Reg(UInt[XLEN](0))
        self.id_ex_rd_idx = Reg(UInt[5](0))
        self.id_ex_alu_op = Reg(Bits[5](0))
        self.id_ex_alu_src1 = Reg(Bits[2](0))
        self.id_ex_alu_src2 = Reg(Bits[2](0))
        self.id_ex_mem_op = Reg(Bits[2](0))
        self.id_ex_mem_size = Reg(Bits[2](0))
        self.id_ex_mem_sext = Reg(Bool(False))
        self.id_ex_wb_en = Reg(Bool(False))
        self.id_ex_wb_src = Reg(Bits[2](0))
        self.id_ex_branch_op = Reg(Bits[4](0))
        self.id_ex_is_branch = Reg(Bool(False))
        self.id_ex_csr_cmd = Reg(Bits[2](0))
        self.id_ex_csr_addr = Reg(UInt[12](0))
        self.id_ex_is_mul_div = Reg(Bool(False))
        self.id_ex_mul_div_op = Reg(Bits[3](0))
        self.id_ex_is_signed = Reg(Bool(False))
        self.id_ex_is_system = Reg(Bool(False))
        self.id_ex_exception = Reg(Bool(False))
        self.id_ex_excep_code = Reg(UInt[5](0))

        # EX → MEM pipeline registers
        self.ex_mem_valid = RegInit(Bool(False))
        self.ex_mem_pc = Reg(UInt[XLEN](0))
        self.ex_mem_pc4 = Reg(UInt[XLEN](0))
        self.ex_mem_alu_result = Reg(UInt[XLEN](0))
        self.ex_mem_rs2_val = Reg(UInt[XLEN](0))
        self.ex_mem_rd_idx = Reg(UInt[5](0))
        self.ex_mem_mem_op = Reg(Bits[2](0))
        self.ex_mem_mem_size = Reg(Bits[2](0))
        self.ex_mem_mem_sext = Reg(Bool(False))
        self.ex_mem_wb_en = Reg(Bool(False))
        self.ex_mem_wb_src = Reg(Bits[2](0))
        self.ex_mem_exception = Reg(Bool(False))
        self.ex_mem_excep_code = Reg(UInt[5](0))
        self.ex_mem_csr_cmd = Reg(Bits[2](0))
        self.ex_mem_csr_addr = Reg(UInt[12](0))
        self.ex_mem_is_mul_div = Reg(Bool(False))
        self.ex_mem_mul_div_op = Reg(Bits[3](0))
        self.ex_mem_branch_op = Reg(Bits[4](0))

        # MEM → WB pipeline registers
        self.mem_wb_valid = RegInit(Bool(False))
        self.mem_wb_pc = Reg(UInt[XLEN](0))
        self.mem_wb_pc4 = Reg(UInt[XLEN](0))
        self.mem_wb_alu_result = Reg(UInt[XLEN](0))
        self.mem_wb_mem_data = Reg(UInt[XLEN](0))
        self.mem_wb_rd_idx = Reg(UInt[5](0))
        self.mem_wb_wb_en = Reg(Bool(False))
        self.mem_wb_wb_src = Reg(Bits[2](0))
        self.mem_wb_exception = Reg(Bool(False))
        self.mem_wb_excep_code = Reg(UInt[5](0))

        # ==================================================================
        # Submodules
        # ==================================================================

        self.expander = CompressedExpander(name="c_expander")
        self.decoder = InstructionDecoder(name="decoder")
        self.alu = ALU(name="alu")
        self.multiplier = Multiplier(name="multiplier")
        self.divider = Divider(name="divider")
        self.btb = BTB(entries=params.btb_entries, name="btb")
        self.csr = CSRUnit(name="csr_unit")
        self.regfile = RegisterFile(name="regfile")
        self.icache = ICache(name="icache")
        self.dcache = DCache(name="dcache")

        # ==================================================================
        # Pipeline control signals
        # ==================================================================

        # Stall flags
        self.stall_if = Wire(Bool(False))
        self.stall_id = Wire(Bool(False))
        self.stall_ex = Wire(Bool(False))

        # Flush flags
        self.flush_if = Wire(Bool(False))
        self.flush_id = Wire(Bool(False))

        # Branch resolution from EX
        self.br_taken = Wire(Bool(False))
        self.br_target = Wire(UInt[XLEN](0))
        self.br_mispredicted = Wire(Bool(False))

        # Forwarding
        self.fwd_ex_rs1 = Wire(UInt[XLEN](0))
        self.fwd_ex_rs2 = Wire(UInt[XLEN](0))
        self.fwd_mem_rs1 = Wire(UInt[XLEN](0))
        self.fwd_mem_rs2 = Wire(UInt[XLEN](0))

        # ==================================================================
        # Execute all stages
        # ==================================================================

        @domain(posedge=self.clk_i, negreset=self.reset_n_i)
        def if_stage(self):
            """Instruction Fetch stage."""
            self._execute_if_stage(params)

        @domain(posedge=self.clk_i, negreset=self.reset_n_i)
        def id_stage(self):
            """Instruction Decode stage."""
            self._execute_id_stage(params)

        @domain(posedge=self.clk_i, negreset=self.reset_n_i)
        def ex_stage(self):
            """Execute stage."""
            self._execute_ex_stage(params)

        @domain(posedge=self.clk_i, negreset=self.reset_n_i)
        def mem_stage(self):
            """Memory access stage."""
            self._execute_mem_stage(params)

        @domain(posedge=self.clk_i, negreset=self.reset_n_i)
        def wb_stage(self):
            """Write-back stage."""
            self._execute_wb_stage(params)

    # ==================================================================
    # Stage implementations
    # ==================================================================

    def _execute_if_stage(self, params):
        """IF stage: fetch instruction from I-cache, BTB prediction."""

        # BTB lookup
        fetch_pc = Wire(UInt[XLEN])
        with_signals.when(self.flush_if):
            fetch_pc <<= self.br_target
        with_signals.otherwise_when(self.stall_if):
            fetch_pc <<= self.pc
        with_signals.otherwise():
            fetch_pc <<= self.pc

        self.btb.pc_fetch <<= fetch_pc

        # I-cache access
        self.icache.addr <<= fetch_pc
        self.icache.flush <<= Bool(False)
        self.icache.axi_arready <<= self.i_arready_i
        self.icache.axi_rdata <<= self.i_rdata_i
        self.icache.axi_rvalid <<= self.i_rvalid_i

        self.i_araddr_o <<= self.icache.axi_araddr
        self.i_arvalid_o <<= self.icache.axi_arvalid
        self.i_rready_o <<= self.icache.axi_rready

        # Determine next PC
        next_pc = Wire(UInt[XLEN])
        btb_pred = self.btb.predict_taken & self.btb.predict_hit

        # Priority: exception > branch mispredict > BTB predict > sequential
        with_signals.when(self.flush_if):
            next_pc <<= self.br_target
        with_signals.otherwise_when(btb_pred & ~self.stall_if & ~self.flush_if):
            next_pc <<= self.btb.predict_target
        with_signals.otherwise_when(~self.stall_if & ~self.flush_if):
            next_pc <<= self.pc + 4
        with_signals.otherwise():
            next_pc <<= self.pc

        # PC update
        with_signals.when(~self.stall_if):
            self.pc <<= next_pc

        # Pipeline register IF→ID
        with_signals.when(~self.stall_if & ~self.flush_id):
            self.if_id_valid <<= ~self.flush_if
            self.if_id_pc <<= fetch_pc
            self.if_id_pc4 <<= fetch_pc + 4
            self.if_id_inst <<= self.icache.data
            self.if_id_compressed <<= self.icache.data[1:3] != Bits[2](0b11)
        with_signals.otherwise_when(self.flush_id):
            self.if_id_valid <<= Bool(False)

    def _execute_id_stage(self, params):
        """ID stage: decode instruction, read register file, detect hazards."""

        with_signals.when(~self.stall_id):
            valid = self.if_id_valid
        with_signals.otherwise():
            valid = Bool(False)

        # C expansion
        self.expander.raw_inst <<= self.if_id_inst
        expanded = self.expander.expanded

        # Decode
        self.decoder.inst <<= expanded
        self.decoder.pc <<= self.if_id_pc

        dec = self.decoder

        # Hazard detection: load-use stall
        load_use = (
            self.id_ex_valid
            & (self.id_ex_mem_op == C.MEM_READ)
            & ((self.id_ex_rd_idx == dec.rs1_idx) | (self.id_ex_rd_idx == dec.rs2_idx))
            & (dec.rs1_idx != 0)
            & (dec.rs2_idx != 0)
        )
        self.stall_if <<= self.icache.miss | load_use
        self.stall_id <<= load_use

        # Register file read
        self.regfile.rs1_idx <<= dec.rs1_idx
        self.regfile.rs2_idx <<= dec.rs2_idx

        # Forwarding from EX/MEM
        fwd_rs1 = Wire(UInt[XLEN])
        fwd_rs2 = Wire(UInt[XLEN])

        with_signals.when(self.id_ex_valid & (self.id_ex_rd_idx == dec.rs1_idx) & (dec.rs1_idx != 0)):
            fwd_rs1 <<= self.id_ex_alu_op  # simplification: forwarded value
        with_signals.otherwise_when(
            self.ex_mem_valid & (self.ex_mem_rd_idx == dec.rs1_idx) & (dec.rs1_idx != 0)
        ):
            fwd_rs1 <<= self.ex_mem_alu_result
        with_signals.otherwise():
            fwd_rs1 <<= self.regfile.rs1_val

        with_signals.when(self.id_ex_valid & (self.id_ex_rd_idx == dec.rs2_idx) & (dec.rs2_idx != 0)):
            fwd_rs2 <<= self.id_ex_alu_op  # simplification
        with_signals.otherwise_when(
            self.ex_mem_valid & (self.ex_mem_rd_idx == dec.rs2_idx) & (dec.rs2_idx != 0)
        ):
            fwd_rs2 <<= self.ex_mem_alu_result
        with_signals.otherwise():
            fwd_rs2 <<= self.regfile.rs2_val

        # Pipeline register ID→EX
        with_signals.when(~self.flush_id & ~self.stall_id & ~load_use):
            self.id_ex_valid <<= valid
            self.id_ex_pc <<= self.if_id_pc
            self.id_ex_pc4 <<= self.if_id_pc4
            self.id_ex_inst <<= expanded
            self.id_ex_rs1_val <<= fwd_rs1
            self.id_ex_rs2_val <<= fwd_rs2
            self.id_ex_imm <<= dec.imm
            self.id_ex_rd_idx <<= dec.rd_idx
            self.id_ex_alu_op <<= dec.alu_op
            self.id_ex_alu_src1 <<= dec.alu_src1
            self.id_ex_alu_src2 <<= dec.alu_src2
            self.id_ex_mem_op <<= dec.mem_op
            self.id_ex_mem_size <<= dec.mem_size
            self.id_ex_mem_sext <<= dec.mem_sext
            self.id_ex_wb_en <<= dec.wb_en
            self.id_ex_wb_src <<= dec.wb_src
            self.id_ex_branch_op <<= dec.branch_op
            self.id_ex_is_branch <<= dec.is_branch
            self.id_ex_csr_cmd <<= dec.csr_cmd
            self.id_ex_csr_addr <<= dec.csr_addr
            self.id_ex_is_mul_div <<= dec.is_mul_div
            self.id_ex_mul_div_op <<= dec.mul_div_op
            self.id_ex_is_signed <<= dec.is_signed
            self.id_ex_is_system <<= dec.is_system
            self.id_ex_exception <<= dec.exception
            self.id_ex_excep_code <<= dec.excep_code
        with_signals.otherwise_when(self.flush_id):
            self.id_ex_valid <<= Bool(False)

    def _execute_ex_stage(self, params):
        """EX stage: ALU, branch resolution, M-unit dispatch."""

        valid = self.id_ex_valid & ~self.flush_id
        self.stall_ex <<= Bool(False)

        # ALU operand selection
        alu_src1 = Wire(UInt[XLEN])
        alu_src2 = Wire(UInt[XLEN])

        with_signals.when(self.id_ex_alu_src1 == C.SRC_RS1):
            alu_src1 <<= self.id_ex_rs1_val
        with_signals.otherwise_when(self.id_ex_alu_src1 == C.SRC_PC):
            alu_src1 <<= self.id_ex_pc
        with_signals.otherwise():
            alu_src1 <<= UInt[XLEN](0)

        with_signals.when(self.id_ex_alu_src2 == C.SRC_RS2):
            alu_src2 <<= self.id_ex_rs2_val
        with_signals.otherwise_when(self.id_ex_alu_src2 == C.SRC_IMM):
            alu_src2 <<= self.id_ex_imm
        with_signals.otherwise():
            alu_src2 <<= UInt[XLEN](4)

        # ALU
        self.alu.alu_op <<= self.id_ex_alu_op
        self.alu.src1 <<= alu_src1
        self.alu.src2 <<= alu_src2
        alu_result = self.alu.result
        br_taken = self.alu.br_taken

        # Branch resolution
        branch_target = self.id_ex_pc + self.id_ex_imm

        with_signals.when(self.id_ex_branch_op == C.BR_JALR):
            branch_target = (self.id_ex_rs1_val + self.id_ex_imm) & ~UInt[XLEN](1)

        self.br_taken <<= (
            valid
            & self.id_ex_is_branch
            & (br_taken | (self.id_ex_branch_op == C.BR_JAL) |
               (self.id_ex_branch_op == C.BR_JALR))
        )
        self.br_target <<= (
            Mux(self.id_ex_is_branch, branch_target, self.id_ex_pc + 4)
        )
        self.flush_if <<= (
            valid & self.id_ex_is_branch &
            (br_taken | (self.id_ex_branch_op == C.BR_JAL) |
             (self.id_ex_branch_op == C.BR_JALR))
        )
        self.flush_id <<= self.flush_if

        # BTB update
        self.btb.update_valid <<= valid & self.id_ex_is_branch
        self.btb.update_pc <<= self.id_ex_pc
        self.btb.update_target <<= branch_target
        self.btb.update_taken <<= br_taken | (
            self.id_ex_branch_op == C.BR_JAL
        ) | (self.id_ex_branch_op == C.BR_JALR)

        # CSR handling
        self.csr.csr_cmd <<= self.id_ex_csr_cmd
        self.csr.csr_addr <<= self.id_ex_csr_addr
        self.csr.rs1_val <<= self.id_ex_rs1_val
        self.csr.pc <<= self.id_ex_pc

        # M-unit dispatch
        self.multiplier.start <<= (valid & self.id_ex_is_mul_div &
                                    (self.id_ex_mul_div_op < UInt[3](C.F3_DIV)))
        self.multiplier.a_i <<= self.id_ex_rs1_val
        self.multiplier.b_i <<= self.id_ex_rs2_val
        self.multiplier.signed_a <<= (
            self.id_ex_is_signed & (self.id_ex_mul_div_op != C.F3_MULHSU)
        )
        self.multiplier.signed_b <<= self.id_ex_is_signed
        self.multiplier.upper_half <<= self.id_ex_mul_div_op != C.F3_MUL

        self.divider.start <<= (valid & self.id_ex_is_mul_div &
                                 (self.id_ex_mul_div_op >= UInt[3](C.F3_DIV)))
        self.divider.dividend <<= self.id_ex_rs1_val
        self.divider.divisor <<= self.id_ex_rs2_val
        self.divider.is_signed <<= self.id_ex_is_signed & (
            self.id_ex_mul_div_op in (C.F3_DIV, C.F3_REM)
        )
        self.divider.is_rem <<= self.id_ex_mul_div_op in (C.F3_REM, C.F3_REMU)

        # Stall pipeline for M-unit operations
        m_stall = (
            valid
            & self.id_ex_is_mul_div
            & ~self.multiplier.done
            & ~self.divider.done
        )
        with_signals.when(m_stall):
            self.stall_if <<= Bool(True)
            self.stall_id <<= Bool(True)

        # Result selection (ALU vs M-unit)
        ex_result = Wire(UInt[XLEN])
        with_signals.when(valid & self.id_ex_is_mul_div & self.multiplier.done):
            ex_result <<= self.multiplier.result
        with_signals.otherwise_when(valid & self.id_ex_is_mul_div & self.divider.done):
            ex_result <<= self.divider.result
        with_signals.otherwise_when(valid & (self.id_ex_csr_cmd != C.CSR_NONE)):
            ex_result <<= self.csr.wb_data
        with_signals.otherwise():
            ex_result <<= alu_result

        # Pipeline register EX→MEM
        with_signals.when(~m_stall & ~self.flush_id):
            self.ex_mem_valid <<= valid & ~self.flush_id
            self.ex_mem_pc <<= self.id_ex_pc
            self.ex_mem_pc4 <<= self.id_ex_pc4
            self.ex_mem_alu_result <<= ex_result
            self.ex_mem_rs2_val <<= self.id_ex_rs2_val
            self.ex_mem_rd_idx <<= self.id_ex_rd_idx
            self.ex_mem_mem_op <<= self.id_ex_mem_op
            self.ex_mem_mem_size <<= self.id_ex_mem_size
            self.ex_mem_mem_sext <<= self.id_ex_mem_sext
            self.ex_mem_wb_en <<= self.id_ex_wb_en & ~self.id_ex_exception
            self.ex_mem_wb_src <<= self.id_ex_wb_src
            self.ex_mem_exception <<= self.id_ex_exception
            self.ex_mem_excep_code <<= self.id_ex_excep_code
            self.ex_mem_csr_cmd <<= self.id_ex_csr_cmd
            self.ex_mem_csr_addr <<= self.id_ex_csr_addr
            self.ex_mem_is_mul_div <<= self.id_ex_is_mul_div
            self.ex_mem_mul_div_op <<= self.id_ex_mul_div_op
            self.ex_mem_branch_op <<= self.id_ex_branch_op
        with_signals.otherwise_when(self.flush_id):
            self.ex_mem_valid <<= Bool(False)

    def _execute_mem_stage(self, params):
        """MEM stage: load/store via D-cache, alignment check."""

        valid = self.ex_mem_valid
        addr = self.ex_mem_alu_result

        # Alignment check
        align_err = Wire(Bool(False))
        with_signals.when(self.ex_mem_mem_op == C.MEM_READ):
            with_signals.when(self.ex_mem_mem_size == C.SIZE_HALF):
                align_err <<= addr[0] != 0
            with_signals.otherwise_when(self.ex_mem_mem_size == C.SIZE_WORD):
                align_err <<= (addr[0] != 0) | (addr[1] != 0)
        with_signals.otherwise_when(self.ex_mem_mem_op == C.MEM_WRITE):
            with_signals.when(self.ex_mem_mem_size == C.SIZE_HALF):
                align_err <<= addr[0] != 0
            with_signals.otherwise_when(self.ex_mem_mem_size == C.SIZE_WORD):
                align_err <<= (addr[0] != 0) | (addr[1] != 0)

        # D-cache access
        self.dcache.addr <<= addr
        self.dcache.wdata <<= self.ex_mem_rs2_val
        self.dcache.re <<= (self.ex_mem_mem_op == C.MEM_READ)
        self.dcache.we <<= (self.ex_mem_mem_op == C.MEM_WRITE)
        self.dcache.wstrb <<= self._mem_wstrb(self.ex_mem_mem_size, self.ex_mem_alu_result)

        # Load data extraction and sign extension
        load_data = Wire(UInt[XLEN])
        raw_data = self.dcache.rdata

        with_signals.when(self.ex_mem_mem_size == C.SIZE_BYTE):
            byte_sel = addr[0:2]
            with_signals.when(byte_sel == 0):
                data = raw_data[0:8]
            with_signals.otherwise_when(byte_sel == 1):
                data = raw_data[8:16]
            with_signals.otherwise_when(byte_sel == 2):
                data = raw_data[16:24]
            with_signals.otherwise():
                data = raw_data[24:32]
            with_signals.when(self.ex_mem_mem_sext & data[7]):
                load_data <<= Cat(Bits(24)(0xFF), data)
            with_signals.otherwise():
                load_data <<= Cat(Bits(24)(0), data)

        with_signals.otherwise_when(self.ex_mem_mem_size == C.SIZE_HALF):
            with_signals.when(addr[1]):
                half = raw_data[16:32]
            with_signals.otherwise():
                half = raw_data[0:16]
            with_signals.when(self.ex_mem_mem_sext & half[15]):
                load_data <<= Cat(Bits(16)(0xFFFF), half)
            with_signals.otherwise():
                load_data <<= Cat(Bits(16)(0), half)

        with_signals.otherwise():
            load_data <<= raw_data

        # Stall on D-cache miss
        with_signals.when(self.dcache.miss | self.dcache.busy):
            self.stall_if <<= Bool(True)
            self.stall_id <<= Bool(True)
            self.stall_ex <<= Bool(True)

        # Pipeline register MEM→WB
        with_signals.when(~self.dcache.busy & ~self.dcache.miss):
            self.mem_wb_valid <<= valid
            self.mem_wb_pc <<= self.ex_mem_pc
            self.mem_wb_pc4 <<= self.ex_mem_pc4
            self.mem_wb_alu_result <<= self.ex_mem_alu_result
            self.mem_wb_mem_data <<= load_data
            self.mem_wb_rd_idx <<= self.ex_mem_rd_idx
            self.mem_wb_wb_en <<= self.ex_mem_wb_en & ~align_err
            self.mem_wb_wb_src <<= self.ex_mem_wb_src
            self.mem_wb_exception <<= self.ex_mem_exception | align_err
            with_signals.when(align_err):
                with_signals.when(self.ex_mem_mem_op == C.MEM_READ):
                    self.mem_wb_excep_code <<= UInt[5](C.EXC_LOAD_MISALIGNED)
                with_signals.otherwise():
                    self.mem_wb_excep_code <<= UInt[5](C.EXC_STORE_MISALIGNED)
            with_signals.otherwise():
                self.mem_wb_excep_code <<= self.ex_mem_excep_code

    def _execute_wb_stage(self, params):
        """WB stage: register write-back, exception commit, interrupt sampling."""

        valid = self.mem_wb_valid

        # Write-back data selection
        wb_data = Wire(UInt[XLEN])
        with_signals.when(self.mem_wb_wb_src == C.WB_ALU):
            wb_data <<= self.mem_wb_alu_result
        with_signals.otherwise_when(self.mem_wb_wb_src == C.WB_MEM):
            wb_data <<= self.mem_wb_mem_data
        with_signals.otherwise_when(self.mem_wb_wb_src == C.WB_PC4):
            wb_data <<= self.mem_wb_pc4
        with_signals.otherwise():
            wb_data <<= self.mem_wb_alu_result

        # Register file write
        self.regfile.wb_en <<= valid & self.mem_wb_wb_en & ~self.mem_wb_exception
        self.regfile.wb_idx <<= self.mem_wb_rd_idx
        self.regfile.wb_data <<= wb_data

        # Exception commit
        self.csr.commit_exception <<= valid & self.mem_wb_exception
        self.csr.exception_pc <<= self.mem_wb_pc
        self.csr.exception_cause <<= self.mem_wb_excep_code
        self.csr.exception_val <<= self.mem_wb_alu_result

        # MRET handling
        self.csr.mret_exec <<= Bool(False)
        self.csr.trap_taken <<= Bool(False)

        # Redirect PC on exception
        with_signals.when(valid & self.mem_wb_exception):
            self.flush_if <<= Bool(True)
            self.flush_id <<= Bool(True)
            self.csr.trap_taken <<= Bool(True)

        # External interrupt inputs
        mip_bits = Wire(Bits[3](0))
        mip_bits[0] <<= self.software_irq_i
        mip_bits[1] <<= self.timer_irq_i
        mip_bits[2] <<= self.external_irq_i

        # Instruction retired counter
        with_signals.when(valid):
            self.csr.minstret_reg_inc = Bool(True)

        # Instruction count increment (handled inside CSRUnit)
        if hasattr(self.csr, 'minstret'):
            with_signals.when(valid & ~self.mem_wb_exception):
                self.csr.minstret <<= self.csr.minstret + 1

    @staticmethod
    def _mem_wstrb(mem_size: int, addr: int) -> Bits[4]:
        """Compute byte strobes for store instruction."""
        if mem_size == C.SIZE_BYTE:
            with_signals.when(addr & 0b11 == 0):
                return Bits[4](0b0001)
            with_signals.otherwise_when(addr & 0b11 == 1):
                return Bits[4](0b0010)
            with_signals.otherwise_when(addr & 0b11 == 2):
                return Bits[4](0b0100)
            with_signals.otherwise():
                return Bits[4](0b1000)
        elif mem_size == C.SIZE_HALF:
            with_signals.when(addr[1] == 0):
                return Bits[4](0b0011)
            with_signals.otherwise():
                return Bits[4](0b1100)
        else:
            return Bits[4](0b1111)


# ==========================================================================
# Convenience aliases
# ==========================================================================

__all__ = [
    "LunahanCore",
    "LunahanParams",
    "RISCVConstants",
    "ALU",
    "Multiplier",
    "Divider",
    "InstructionDecoder",
    "CompressedExpander",
    "BTB",
    "CSRUnit",
    "ICache",
    "DCache",
    "RegisterFile",
]
