// GhidraDump.java — Ghidra 12 / JDK 21 — ida_dump.py compatible output

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.*;
import ghidra.util.Msg;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class GhidraDump extends GhidraScript {

    private PrintWriter pw;
    private String outPath;
    private boolean skipAsm;
    private boolean skipDecompile;

    @Override
    protected void run() throws Exception {
        log("=== GhidraDump starting ===");
        log("Java: " + System.getProperty("java.version"));
        log("Args: " + java.util.Arrays.toString(getScriptArgs()));

        outPath       = argOrDefault("out", getProjectRootDir());
        skipAsm       = "true".equalsIgnoreCase(argOrDefault("skip_asm", "false"));
        skipDecompile = "true".equalsIgnoreCase(argOrDefault("skip_decompile", "false"));

        if (currentProgram == null)
            throw new IllegalStateException("currentProgram is null");

        log("Program:   " + currentProgram.getName());
        log("Language:  " + currentProgram.getLanguage().getLanguageID());
        log("Functions: " + currentProgram.getFunctionManager().getFunctionCount());
        log("out=       " + outPath);

        String raw  = currentProgram.getName();
        String stem = raw.contains(".") ? raw.substring(0, raw.lastIndexOf('.')) : raw;
        stem = stem.replaceAll("[^\\w.\\-]", "_");
        File outFile = new File(outPath, stem + ".txt");

        File parent = outFile.getParentFile();
        if (parent != null) parent.mkdirs();
        log("Output: " + outFile.getAbsolutePath());

        try (PrintWriter writer = new PrintWriter(new BufferedWriter(
                new OutputStreamWriter(new FileOutputStream(outFile), StandardCharsets.UTF_8)))) {
            this.pw = writer;

            writeHeader();
            writeFunctions();
            writeStrings(4);
            writeImports();
            writeExports();
            if (!skipDecompile) writePseudocode();
            if (!skipAsm)       writeDisasm();
            writeCallgraph();

            pw.flush();
            log("=== GhidraDump COMPLETE: " + outFile.getAbsolutePath() + " ===");
        } catch (Exception e) {
            log("!!! FAILED: " + e);
            throw e;
        }
    }

    private void writeHeader() {
        pw.println("# Ghidra dump");
        pw.println("input_file: " + currentProgram.getExecutablePath());
        pw.println("arch:       " + currentProgram.getLanguage().getLanguageID());
        pw.println("compiler:   " + currentProgram.getCompilerSpec().getCompilerSpecID());
        pw.println("func_count: " + currentProgram.getFunctionManager().getFunctionCount());
        pw.println("java:       " + System.getProperty("java.version"));
        if (skipDecompile) pw.println("note:       pseudocode skipped");
        if (skipAsm)       pw.println("note:       disasm skipped");
        pw.println();
    }

    private void writeFunctions() {
        pw.println("[functions]");
        int count = 0;
        FunctionIterator fi = currentProgram.getFunctionManager().getFunctions(true);
        while (fi.hasNext() && !monitor.isCancelled()) {
            Function f = fi.next();
            pw.printf("%016X  %s%n", f.getEntryPoint().getOffset(), f.getName());
            count++;
        }
        pw.println();
        log("writeFunctions: " + count);
    }

    private void writeStrings(int minLen) {
        pw.println("[strings]");
        int count = 0, skip = 0;
        DataIterator di = currentProgram.getListing().getDefinedData(true);
        while (di.hasNext() && !monitor.isCancelled()) {
            Data d = di.next();
            if (d == null) { skip++; continue; }
            String t = d.getDataType().getName().toLowerCase();
            if (!t.contains("string") && !t.contains("unicode")) { skip++; continue; }
            String val = d.getDefaultValueRepresentation();
            if (val == null || val.length() < minLen) { skip++; continue; }
            String clean = val.replaceAll("\\p{Cntrl}", " ").trim();
            if (clean.length() >= minLen) {
                pw.printf("%016X  %s%n", d.getAddress().getOffset(), clean);
                count++;
            } else { skip++; }
        }
        pw.println();
        log("writeStrings: wrote=" + count + " skipped=" + skip);
    }

    private void writeImports() {
        pw.println("[imports]");
        Map<String, List<Symbol>> byModule = new LinkedHashMap<>();
        for (Symbol s : currentProgram.getSymbolTable().getExternalSymbols()) {
            try {
                String mod = s.getParentNamespace().getName();
                byModule.computeIfAbsent(mod, k -> new ArrayList<>()).add(s);
            } catch (Exception ignored) {}
        }
        int total = 0;
        for (Map.Entry<String, List<Symbol>> e : byModule.entrySet()) {
            pw.println();
            pw.println("  " + e.getKey() + ":");
            for (Symbol s : e.getValue()) {
                try {
                    pw.printf("    %016X  %s%n", s.getAddress().getOffset(), s.getName());
                    total++;
                } catch (Exception ignored) {}
            }
        }
        pw.println();
        log("writeImports: " + total);
    }

    private void writeExports() {
        pw.println("[exports]");
        int count = 0;
        for (Symbol s : currentProgram.getSymbolTable().getAllSymbols(true)) {
            try {
                if (s.getSymbolType() == SymbolType.FUNCTION && s.isExternalEntryPoint()) {
                    pw.printf("%016X  %s%n", s.getAddress().getOffset(), s.getName());
                    count++;
                }
            } catch (Exception ignored) {}
        }
        pw.println();
        log("writeExports: " + count);
    }

    private void writePseudocode() {
        pw.println("[pseudocode]");
        DecompInterface decomp = new DecompInterface();
        decomp.toggleCCode(true);
        if (!decomp.openProgram(currentProgram)) {
            pw.println("; Decompiler failed: " + decomp.getLastMessage());
            pw.println();
            log("Decompiler open FAILED");
            return;
        }
        int ok = 0, fail = 0;
        try {
            List<Function> funcs = new ArrayList<>();
            FunctionIterator fi = currentProgram.getFunctionManager().getFunctions(true);
            while (fi.hasNext()) funcs.add(fi.next());
            int total = funcs.size();

            for (int idx = 0; idx < total && !monitor.isCancelled(); idx++) {
                Function f = funcs.get(idx);
                if ((idx + 1) % 200 == 0 || idx + 1 == total)
                    log("writePseudocode: " + (idx + 1) + "/" + total);

                pw.println();
                pw.println("=".repeat(100));
                pw.println("FUNCTION : " + f.getName());
                pw.printf ("START_EA : 0x%X%n", f.getEntryPoint().getOffset());
                pw.println("=".repeat(100));
                pw.println();

                try {
                    DecompileResults dr = decomp.decompileFunction(f, 45, monitor);
                    if (dr == null || !dr.decompileCompleted()) {
                        pw.println("// [DECOMPILE FAILED] " + (dr != null ? dr.getErrorMessage() : "null"));
                        fail++;
                    } else {
                        for (String line : dr.getDecompiledFunction().getC().split("\n"))
                            pw.println("    " + line);
                        ok++;
                    }
                } catch (Exception e) {
                    pw.println("// [DECOMPILE FAILED] " + e.getMessage());
                    fail++;
                }
            }
        } finally {
            decomp.dispose();
        }
        pw.println();
        pw.println("[pseudocode_summary]");
        pw.println("total:  " + (ok + fail));
        pw.println("ok:     " + ok);
        pw.println("failed: " + fail);
        pw.println();
        log("writePseudocode: ok=" + ok + " fail=" + fail);
    }

    private void writeDisasm() {
        pw.println("[disasm]");
        MemoryBlock[] blocks = currentProgram.getMemory().getBlocks();
        int instrCount = 0;

        for (int bidx = 0; bidx < blocks.length && !monitor.isCancelled(); bidx++) {
            MemoryBlock block = blocks[bidx];
            log("writeDisasm: block " + (bidx + 1) + "/" + blocks.length + ": " + block.getName());

            pw.println();
            pw.println("#".repeat(100));
            pw.println("SEGMENT : " + block.getName());
            pw.println("TYPE    : " + blockType(block));
            pw.printf ("RANGE   : 0x%X - 0x%X%n",
                    block.getStart().getOffset(), block.getEnd().getOffset());
            pw.println("#".repeat(100));
            pw.println();

            // AddressSet implements AddressSetView — fixes the compile error
            AddressSet range = new AddressSet(block.getStart(), block.getEnd());
            InstructionIterator ii = currentProgram.getListing().getInstructions(range, true);
            while (ii.hasNext() && !monitor.isCancelled()) {
                try {
                    Instruction insn = ii.next();

                    // Labels above the line
                    for (Symbol sym : currentProgram.getSymbolTable().getSymbols(insn.getAddress())) {
                        if (sym.getSymbolType() == SymbolType.LABEL ||
                            sym.getSymbolType() == SymbolType.FUNCTION)
                            pw.println(sym.getName() + ":");
                    }

                    pw.printf("  %016X:  %s%n", insn.getAddress().getOffset(), insn);

                    // Inline comments via CodeUnit (non-deprecated API)
                    CodeUnit cu = currentProgram.getListing().getCodeUnitAt(insn.getAddress());
                    if (cu != null) {
                        String cmt = cu.getComment(CodeUnit.EOL_COMMENT);
                        if (cmt != null && !cmt.isEmpty())
                            pw.printf("                            ; %s%n", cmt);
                        String rcmt = cu.getComment(CodeUnit.REPEATABLE_COMMENT);
                        if (rcmt != null && !rcmt.isEmpty())
                            pw.printf("                            ; (r) %s%n", rcmt);
                    }

                    instrCount++;
                    if (instrCount % 100_000 == 0) log("writeDisasm: " + instrCount + "...");
                } catch (Exception e) {
                    pw.println("; ERROR: " + e.getMessage());
                }
            }
        }
        pw.println();
        log("writeDisasm: " + instrCount + " instructions");
    }

    private String blockType(MemoryBlock b) {
        if (b.isExecute())      return "CODE";
        if (!b.isInitialized()) return "BSS";
        return "DATA";
    }

    private void writeCallgraph() {
        pw.println("[callgraph]");
        int edges = 0;
        for (Function caller : currentProgram.getFunctionManager().getFunctions(true)) {
            if (monitor.isCancelled()) break;
            try {
                for (Function callee : caller.getCalledFunctions(monitor)) {
                    pw.printf("%016X %s  ->  %016X %s%n",
                            caller.getEntryPoint().getOffset(), caller.getName(),
                            callee.getEntryPoint().getOffset(), callee.getName());
                    edges++;
                }
            } catch (Exception ignored) {}
        }
        pw.println();
        log("writeCallgraph: edges=" + edges);
    }

    private void log(String msg) {
        String line = "[GhidraDump] " + msg;
        Msg.info(this, line);
        System.out.println(line);
    }

    // Ghidra splits "out=D:\path" into args ["out", "D:\path"] at the = sign.
    // This handles both "key=value" and "key" "value" forms.
    private String argOrDefault(String key, String def) {
        String[] args = getScriptArgs();
        if (args == null) return def;
        for (int i = 0; i < args.length; i++) {
            if (args[i].startsWith(key + "=")) {
                String v = args[i].substring(key.length() + 1).trim();
                return v.isEmpty() ? def : v;
            }
            if (args[i].equals(key) && i + 1 < args.length) {
                return args[i + 1];
            }
        }
        return def;
    }

    private String getProjectRootDir() {
        return state.getProject().getProjectLocator().getProjectDir().getAbsolutePath();
    }
}