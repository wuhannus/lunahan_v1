# lunahan_v1 — CPU Performance Profiling Report

**Generated:** 2026-06-03 10:06:04  
**Core:** lunahan_v1 (RV32IMC)  
**Technology:** sky130_fd_sc_hd @ 100 MHz  
**Pipeline:** 5-stage in-order (IF/ID/EX/MEM/WB)  

## 1. Benchmark Results

| Benchmark | Instructions | Cycles | IPC | CPI | Branch Acc | ICache Hit | DCache Hit | Stalls |
|-----------|-------------|--------|-----|-----|-----------|-----------|-----------|--------|
| Dhrystone-like | 99,999 | 100,000 | 1.0000 | 1.0000 | 0.0% | 98.0% | 94.9% | 0.0% |
| Fibonacci(n=20) | 99,999 | 100,000 | 1.0000 | 1.0000 | 0.0% | 98.1% | 95.0% | 0.0% |
| BubbleSort(n=32) | 5 | 6 | 0.8333 | 1.2000 | 100.0% | 100.0% | 100.0% | 0.0% |
| RandomStream(1K) | 99,999 | 100,000 | 1.0000 | 1.0000 | 100.0% | 98.0% | 94.9% | 0.0% |
| RandomStream(10K) | 99,999 | 100,000 | 1.0000 | 1.0000 | 100.0% | 98.1% | 94.9% | 0.0% |

## 2. Aggregate Performance

- **Average IPC:** 0.9667
- **Average CPI:** 1.0400
- **Best IPC:** 1.0000
- **Average Branch Accuracy:** 60.0%
- **Average ICache Hit Rate:** 98.4%
- **Average DCache Hit Rate:** 95.9%
- **Total Instructions:** 400,001
- **Total Cycles:** 400,006

## 3. Pipeline Analysis

- **Stall cycles:** 0 (0.0% of total)
- **Flush cycles:** 0 (0.0% of total)
- **Forwarding hits:** 0

## 4. Instruction Mix

- **ALU ops:** 11,020 (100.0%)
- **Loads:** 0 (0.0%)
- **Stores:** 0 (0.0%)
- **Branches:** 3 (0.0%)

## 5. PPA Correlation

| Metric | Profiling Result | Physical Design Target | Status |
|--------|-----------------|----------------------|--------|
| Frequency | 100 MHz | 100 MHz | ✓ MET |
| IPC | 0.9667 | > 0.80 | ✓ MET |
| CPI | 1.0400 | < 1.25 | ✓ MET |
| Branch Accuracy | 60.0% | > 85% | ✗ |
| ICache Hit | 98.4% | > 95% | ✓ MET |
| DCache Hit | 95.9% | > 90% | ✓ MET |
| Power | 0.95 mW | < 50 mW | ✓ MET |
| Area | 0.0561 mm² | < 1.0 mm² | ✓ MET |
