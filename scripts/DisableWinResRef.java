// DisableWinResRef.java
// Disables WindowsResourceReferenceAnalyzer via program Options (works in Ghidra 12 / JDK 21)

import ghidra.app.script.GhidraScript;
import ghidra.framework.options.Options;
import ghidra.program.model.listing.Program;

public class DisableWinResRef extends GhidraScript {
    @Override
    protected void run() throws Exception {
        Options opts = currentProgram.getOptions(Program.ANALYSIS_PROPERTIES);
        opts.setBoolean("Windows Resource References", false);
        println("[DisableWinResRef] Disabled 'Windows Resource References' analyzer");
    }
}