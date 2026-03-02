import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;
import ghidra.program.model.symbol.SymbolTable;
import ghidra.program.model.symbol.SymbolType;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

/**
 * Minimal headless export script for analyzeHeadless.
 *
 * Usage is controlled by cli.py; it passes "out=/some/path" via script args.
 */
public class GhidraDump extends GhidraScript {

    @Override
    protected void run() throws Exception {
        String outDir = getArgumentValue("out");
        if (outDir == null || outDir.isEmpty()) {
            outDir = getProjectRootDir();
        }

        File outFile = new File(outDir, currentProgram.getName() + ".txt");
        outFile.getParentFile().mkdirs();

        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(outFile), StandardCharsets.UTF_8))) {
            pw.println("# Ghidra dump");
            pw.println("program: " + currentProgram.getExecutablePath());
            pw.println("language: " + currentProgram.getLanguage().getLanguageDescription().getLanguageID());
            pw.println();

            pw.println("[functions]");
            Listing listing = currentProgram.getListing();
            FunctionIterator funcs = listing.getFunctions(true);
            while (funcs.hasNext() && !monitor.isCancelled()) {
                Function f = funcs.next();
                pw.println(f.getEntryPoint() + " " + f.getName());
            }
            pw.println();

            pw.println("[imports]");
            SymbolTable st = currentProgram.getSymbolTable();
            SymbolIterator ext = st.getExternalSymbols();
            while (ext.hasNext() && !monitor.isCancelled()) {
                Symbol s = ext.next();
                pw.println(s.getAddress() + " " + s.getName());
            }

            pw.println();
            pw.println("[exports]");
            SymbolIterator all = st.getSymbolIterator(true);
            while (all.hasNext() && !monitor.isCancelled()) {
                Symbol s = all.next();
                if (s.getSymbolType() == SymbolType.EXPORT) {
                    pw.println(s.getAddress() + " " + s.getName());
                }
            }

            pw.println();
            pw.println("[disasm]");
            InstructionIterator insts = listing.getInstructions(true);
            while (insts.hasNext() && !monitor.isCancelled()) {
                Instruction ins = insts.next();
                pw.println(ins.getAddress() + ": " + ins);
            }

            pw.println();
            pw.println("[pseudocode]");
            DecompInterface ifc = new DecompInterface();
            ifc.openProgram(currentProgram);
            if (!ifc.canDecompile(currentProgram)) {
                pw.println("; decompiler not available for this binary");
            } else {
                FunctionIterator funcs2 = listing.getFunctions(true);
                while (funcs2.hasNext() && !monitor.isCancelled()) {
                    Function f = funcs2.next();
                    DecompileResults res = ifc.decompileFunction(f, 60, monitor);
                    if (!res.decompileCompleted()) {
                        continue;
                    }
                    pw.println(f.getEntryPoint() + " " + f.getName());
                    pw.println(res.getDecompiledFunction().getC());
                    pw.println();
                }
            }
            ifc.dispose();

            pw.println("[callgraph]");
            FunctionIterator funcs3 = listing.getFunctions(true);
            while (funcs3.hasNext() && !monitor.isCancelled()) {
                Function f = funcs3.next();
                for (Function callee : f.getCalledFunctions(monitor)) {
                    pw.println(f.getEntryPoint() + " -> " + callee.getEntryPoint());
                }
            }
        }
    }

    private String getArgumentValue(String key) {
        String[] args = getScriptArgs();
        if (args == null) {
            return null;
        }
        for (String a : args) {
            if (a.startsWith(key + "=")) {
                return a.substring(key.length() + 1);
            }
        }
        return null;
    }

    private String getProjectRootDir() {
        return getState().getProject().getProjectLocator().getProjectDir().getAbsolutePath();
    }
}
