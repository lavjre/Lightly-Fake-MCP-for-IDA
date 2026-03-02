# Architecture Document

## 1. Problem Being Solved

This project addresses the problem of **automating information extraction from binary files** during reverse engineering. If done manually in IDA or Ghidra, the analyst has to open each binary, wait for auto-analysis, and then export functions, strings, imports/exports, pseudocode, disassembly, and call graphs one by one. This process is time-consuming, hard to repeat consistently, and inefficient when handling many files.

In addition, dump outputs are often large and inconsistent across tools, which makes storage, sharing, and downstream use more difficult. Therefore, the system is designed as an intermediate orchestration layer that can run analysis in headless mode, standardize the output, and manage artifacts in a consistent way.

## 2. Why This Tech Stack / Toolset

**Python CLI** is used as the orchestration layer because it is well suited for automation, file handling, external process execution, logging, and future extension. Python also fits naturally into reverse engineering workflows.

**IDA Pro + IDAPython** is used as the first backend because it provides strong disassembly and decompilation quality, which is useful when detailed pseudocode, function lists, imports/exports, strings, and call graphs are required.

**Ghidra Headless + Java Script** is used as the second backend because it is free, widely used, supports non-GUI execution, and is suitable for automation environments. Supporting both **IDA and Ghidra** makes the system more flexible: the user can choose whichever tool is available, or switch backends if one tool fails in a specific case.

## 3. Main Workflow

```text
User
  -> runs cli.py and selects a backend (IDA or Ghidra)
  -> CLI validates arguments and auto-detects tool paths
  -> loads one or more binary files
  -> calls the corresponding dump script
       IDA    -> ida_dump.py
       Ghidra -> DisableWinResRef.java + GhidraDump.java
  -> backend performs auto-analysis and extracts
       functions, strings, imports, exports,
       pseudocode, disassembly, call graph
  -> CLI standardizes output, writes logs, checks file size
  -> if output is too large, split or zip it
  -> saves results to the output directory
```

## 4. High-Level Architecture

The system consists of three main layers:

- **CLI Orchestrator**: receives input, selects the backend, manages runtime, logs, and output.
- **Backend Adapter**: connects the CLI to IDA or Ghidra in headless mode.
- **Dump Engine**: extracts information from the binary and exports structured text artifacts.

## 5. Conclusion

This architecture makes reverse engineering **faster, more repeatable, easier to extend, and easier to integrate into automated analysis pipelines**. It is a practical solution for malware analysis, crackme tasks, and binary triage when multiple files need to be processed in a standardized way.
