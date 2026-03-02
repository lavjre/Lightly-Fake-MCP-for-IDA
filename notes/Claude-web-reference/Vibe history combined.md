# Lightly-Fake-MCP-for-IDA — Full Development History
**Project:** Headless IDA Pro / Ghidra binary analysis runner  
**Repo:** https://github.com/lavjre/Lightly-Fake-MCP-for-IDA

---

## Session 1 — 2026-03-02 (early)
### IDA 9.x Python API Bug Fixes

**Goal:** Get `ida_dump.py` running correctly under IDA Pro 9.2.

---

#### Bug 1 — `idaapi.get_inf_structure` removed in IDA 9.x

```
AttributeError: module 'idaapi' has no attribute 'get_inf_structure'
  File "ida_dump.py", line 44, in dump_header
    fh.write(f"arch: {idaapi.get_inf_structure().procName}\n")
```

**Root cause:** `get_inf_structure()` was deprecated then removed in IDA 9.x. The flat API replacement is `ida_ida.inf_get_procname()`.

**Fix:** Already present in uploaded file — `ida_ida.inf_get_procname()` with try/except fallback to `cvar.inf.procName`.

---

#### Bug 2 — `ida_idaapi.get_kernel_version` removed in IDA 9.x

```
AttributeError: module 'ida_idaapi' has no attribute 'get_kernel_version'
  File "ida_dump.py", line 58, in dump_header
    fh.write(f"ida_version: {ida_idaapi.get_kernel_version()}\n\n")
```

**Fix:** Already present in uploaded file — moved to `ida_kernwin.get_kernel_version()` with try/except.

---

#### Bug 3 — `strtypes` bitmask → list in IDA 9.x

```
TypeError: expected a list
  File "ida_dump.py", line 76, in dump_strings
    strings.setup(strtypes=ida_nalt.STRTYPE_C | ida_nalt.STRTYPE_C_16, ...)
```

**Root cause:** IDA 9.x tightened the `strwinsetup_t.strtypes` binding — it now strictly requires a Python `list`, not an integer bitmask.

**Fix:**
```python
# Before (IDA 8.x worked, IDA 9.x breaks)
strtypes=ida_nalt.STRTYPE_C | ida_nalt.STRTYPE_C_16,

# After
strtypes=[ida_nalt.STRTYPE_C, ida_nalt.STRTYPE_C_16],
```

---

#### Bug 4 — `ida_hexrays.decompiler_initialized` removed in IDA 9.x

```
AttributeError: module 'ida_hexrays' has no attribute 'decompiler_initialized'
  File "ida_dump.py", line 140, in dump_pseudocode
    if not ida_hexrays.decompiler_initialized():
```

**Fix:** Replaced with `init_hexrays_plugin()`, guarded with `hasattr` for backward compat:
```python
try:
    initialized = (
        ida_hexrays.init_hexrays_plugin()
        if hasattr(ida_hexrays, "init_hexrays_plugin")
        else ida_hexrays.decompiler_initialized()
    )
except Exception:
    initialized = False
```

---

#### Outcome

`ida_dump.py` fully functional under IDA Pro 9.2. All four API removals patched.

---

---

## Session 2 — 2026-03-02 (evening)
### Ghidra + JDK Saga + GhidraDump.java Development

**Context:** IDA is working. Now getting `GhidraDump.java` to produce output via `analyzeHeadless`. Long session with multiple dead ends before reaching the correct root cause.

---

### Part A — JDK Version War

#### Attempt 1 — User switched Ghidra to JDK 21

User changed `JAVA_HOME` to `C:\Program Files\Eclipse Adoptium\jdk-21.0.10.7-hotspot` to match their project. Ghidra immediately crashed:

```
ERROR  Abort due to Headless analyzer error:
WindowsResourceReference has been compiled by a more recent version of the Java Runtime
(class file version 69.0), this version of the Java Runtime only recognizes class file versions up to 65.0
```

**Root cause identified:** Class file version 69 = Java 25, version 65 = Java 21. `WindowsResourceReference.java` inside Ghidra 12's `MicrosoftCodeAnalyzerPlugin` was compiled by the previous Ghidra developer run under JDK 25. Running Ghidra under JDK 21 causes it to reject that `.class` file before the user's script ever runs.

**Initial (wrong) conclusion:** "Ghidra 12 requires JDK 25." → advised user to revert to JDK 25 for Ghidra. User did so.

---

#### Attempt 2 — `GhidraDump.java` bugs found and fixed

While Ghidra was back on JDK 25, reviewed `GhidraDump.java` and found five bugs:

| Bug | Original | Fixed |
|-----|----------|-------|
| `println()` override | Overrode `GhidraScript.println()`, crashed before `pw` was initialized | Removed — uses `Msg.info()` for console |
| Broken string regex | `[\\p{Cntrl}\\p{Graph}^ ]` stripped all visible chars | Changed to `\\p{Cntrl}` only |
| Null `getParentFile()` | Crashed on root paths | Added null-check before `mkdirs()` |
| `pw` not closed on exception | No try-with-resources | Wrapped in try-with-resources |
| `getCalledFunctions()` silent failure | No catch in callgraph loop | Added per-function catch |
| Decompiler null result | Only checked `decompileCompleted()` | Added null-check on `dr` first |

---

#### Attempt 3 — `--` breaking script argument passing

User ran `cli.py` and the dump went to the wrong directory. Investigation revealed:

```python
# BROKEN — Ghidra doesn't use "--" as separator for postScript args
"-postScript", script_name, "--", f"out={out_dir}"

# FIXED — args passed directly after script name
"-postScript", script_name, f"out={out_dir}"
```

This was why `GhidraDump.java` always fell back to the project root for output.

---

#### Attempt 4 — User deletes JDK 25

User deleted JDK 25, leaving only JDK 21. Now Ghidra itself cannot start. The `WindowsResourceReference` analyzer is the problem again.

**New approach:** Disable that specific analyzer via `-analysisOptions`:
```python
"-analysisOptions", "Windows Resource References.enabled=false"
```

**But this failed too.** `analyzeHeadless.bat` forwards args via `%*` without quoting. The space in `"Windows Resource References"` caused Ghidra to misparse the entire command line — it tried to use `-analysisOptions` as a directory path.

---

#### Attempt 5 — PreScript approach

Since `-analysisOptions` with spaces can't be passed through `analyzeHeadless.bat`, switched to a **Ghidra pre-script** `DisableWinResRef.java` that disables the analyzer programmatically before analysis starts:

```java
public class DisableWinResRef extends GhidraScript {
    @Override
    protected void run() throws Exception {
        Options opts = currentProgram.getOptions(Program.ANALYSIS_PROPERTIES);
        opts.setBoolean("Windows Resource References", false);
    }
}
```

No spaces in any command-line args. No quoting issues.

---

#### Attempt 6 — JAVA_HOME inheritance

Even with the prescript, Ghidra was still crashing because `cli.py` did `env = os.environ.copy()`, passing `JAVA_HOME=JDK21` to Ghidra's subprocess. Tried `env.pop("JAVA_HOME", None)`.

**Result:** Still crashed. `analyzeHeadless.bat` fell back to `PATH`, and `C:\Program Files\Java\jdk-21\bin` appeared before JDK 25 in PATH. Removing `JAVA_HOME` just made it pick up JDK 21 from PATH instead.

---

#### Attempt 7 — Force JDK 25 via subprocess env

Added `detect_ghidra_jdk()` to scan for JDK ≥ 25 and explicitly set `JAVA_HOME` and prepend its `bin/` to `PATH` in the subprocess env. JDK 25 detected at `C:\Program Files\Eclipse Adoptium\jdk-25.0.2.10-hotspot`. Still crashed.

**Root cause (found via research):** Ghidra's `launch.bat` → `LaunchSupport.jar` has a multi-stage JDK selection that **caches its choice** in `%APPDATA%\ghidra\ghidra_12.0.1_PUBLIC\preferences`. Once the preferences file pointed to JDK 21, LaunchSupport read the cached value and ignored the subprocess `JAVA_HOME` entirely.

---

#### Attempt 8 — Wrapper `.bat` file

Since subprocess env vars were being ignored by LaunchSupport's caching, generated a temporary wrapper `.bat` that used `setlocal` + `call` to pass environment into `analyzeHeadless.bat`'s own scope. `call` inheritance bypasses the LaunchSupport cache.

Still failed — preferences file took priority over everything.

---

#### ACTUAL ROOT CAUSE — Cached `.class` file

After exhausting all JDK switching approaches, reconsidered the original error message. The crash isn't Ghidra's main JVM being the wrong version — it's that **a single cached `.class` file** in Ghidra's OSGi bundle cache was compiled with JDK 25 (from when the user previously ran JDK 25), and JDK 21 can't load it.

```
%APPDATA%\ghidra\ghidra_12.0.1_PUBLIC\osgi\compiled-bundles\36a95d37\
    WindowsResourceReference.class   ← compiled by JDK 25, JDK 21 rejects it
```

`WindowsResourceReference.java` is a **Ghidra script** — not a core Ghidra binary. It gets compiled by whatever JDK is active at the time and cached. The source `.java` is at `C:\Program Files\Ghidra\Ghidra\Features\Decompiler\ghidra_scripts\WindowsResourceReference.java` and is fully compatible with JDK 21.

**Fix:**
```powershell
Remove-Item "$env:APPDATA\ghidra\ghidra_12.0.1_PUBLIC\osgi\compiled-bundles\36a95d37\WindowsResourceReference.class" -Force
```

Ghidra recompiled from `.java` source on the next run using JDK 21. Analysis succeeded in 11 seconds. `WindowsResourceReference` ran cleanly (4.027s). The prescript was no longer needed.

---

### Part B — GhidraDump.java API Fixes for Ghidra 12

During debugging, `GhidraDump.java` also had Ghidra 12-specific API breaks:

| Error | Fix |
|-------|-----|
| `getInstructions(AddressRange, boolean)` — no matching overload | `new AddressSet(block.getStart(), block.getEnd())` — `AddressSet` implements `AddressSetView` |
| `import ghidra.program.model.data.Data` — wrong package | Removed; `Data` is already in `ghidra.program.model.listing.*` |
| `getPrototypeString(boolean, boolean)` — wrong signature | `getPrototypeString(boolean)` — one argument in Ghidra 12 |
| `decomp.setCurrentProgram()` — method doesn't exist | Removed; `openProgram()` sets it implicitly |
| `Listing.getComment(type, addr)` — deprecated | `cu.getComment(CodeUnit.EOL_COMMENT)` via `CodeUnit` |

---

### Part C — Output Format Alignment with `ida_dump.py`

Rewrote `GhidraDump.java` output to exactly match `ida_dump.py` section layout:

- **Filename:** `{stem}.txt` not `{stem}_dump.txt`
- **Section order:** header → functions → strings → imports → exports → **pseudocode** → disasm → callgraph
- **Pseudocode before disasm** (matches IDA order)
- **Address format:** `%016X` (16-char zero-padded hex) everywhere
- **Pseudocode header:** `====` (100 wide) + `FUNCTION :` + `START_EA :` + `====`
- **Disasm header:** `####` (100 wide) + `SEGMENT :` + `TYPE :` + `RANGE :`
- **Labels** printed above each instruction line
- **Inline comments** (EOL + repeatable) indented with `;` and `; (r)`
- **Imports** grouped by module with 2-space indented module name + 4-space entries
- **Callgraph** includes names on both sides: `ADDR name  ->  ADDR name`

---

### Part D — Argument Parsing Fix

Ghidra splits `"out=D:\path"` into `["out", "D:\path"]` at the `=` sign when passing postScript args. Fixed `argOrDefault()` in `GhidraDump.java` to handle both forms:

```java
private String argOrDefault(String key, String def) {
    String[] args = getScriptArgs();
    if (args == null) return def;
    for (int i = 0; i < args.length; i++) {
        if (args[i].startsWith(key + "=")) {          // "key=value" form
            String v = args[i].substring(key.length() + 1).trim();
            return v.isEmpty() ? def : v;
        }
        if (args[i].equals(key) && i + 1 < args.length) {  // ["key", "value"] form
            return args[i + 1];
        }
    }
    return def;
}
```

---

### Part E — OSGi Bundle Cache

After editing `GhidraDump.java`, Ghidra kept using the old compiled version and showing the old errors. Root cause: Ghidra caches compiled scripts in `%APPDATA%\ghidra\...\osgi\compiled-bundles\`. After a failed build, the directory is marked as failed and Ghidra refuses to retry.

**Fix:**
```powershell
Remove-Item -Recurse -Force "$env:APPDATA\ghidra\ghidra_12.0.1_PUBLIC\osgi\compiled-bundles"
```

---

### Session 2 Final Results

**FLRSCRNSVR.SCR (PE, x64):**
- Analysis: 9s, functions: 195, decompiled: 82, strings: 153, imports: 116, exports: 1, instructions: 3,992, callgraph edges: 338

**wallpaper (ELF, x64):**
- Analysis: 1s, functions: 1, strings: 2, instructions: 117

Both dumps produced in the correct output directory with the correct filename, all sections present and matching `ida_dump.py` format.

---

---

## Session 3 — 2026-03-03
### Cross-Platform `cli.py` Rewrite + Docker + README

**Goal:** Make the whole toolchain usable by other users on both Windows and Linux via Docker, and improve `cli.py` cross-platform support.

---

### `cli.py` — Cross-Platform Rewrite

Key problems with the previous version:

| Problem | Fix |
|---------|-----|
| `--out-dir` defaulted to relative `runs/` with flat `ida_dump/`/`ghidra_dump/` subdirs | Changed to `exports/<binary_stem>/` layout; default anchored to `_SCRIPT_DIR` |
| IDA auto-detection Windows-only (only checked registry + `%ProgramFiles%`) | Added Linux paths: `/opt/IDA*/idat64`, `~/.local/bin`, macOS `.app` bundles |
| Ghidra auto-detection Windows-only | Added Linux paths: `/opt/ghidra*/support/analyzeHeadless`, `/usr/local/bin/analyzeHeadless` |
| Linux IDA preferred `ida64` (GUI daemon) | Reordered to prefer `idat64` (terminal/batch mode) on all platforms |
| `analyzeHeadless.bat` on Linux | `.bat` candidates added to Windows list only; Linux candidates are plain `analyzeHeadless` |
| `NOPAUSE=1` not set | Added — suppresses `"Press any key"` prompt in `.bat` |
| `IDA_DUMP_OUT_DIR` not set | Added to env before IDA launch — `ida_dump.py` uses it as fallback if `-S` quoting breaks |
| `DisableWinResRef.java` hardcoded in command | Made conditional — only added if the file actually exists on disk |
| Script paths resolved relative to `parent.parent` | Now anchored to `_SCRIPT_DIR` (directory of `cli.py`) |

---

### Docker — `Dockerfile` (IDA Pro + Ghidra)

Covers the exact repo layout:

```
dissembler/ida_linux/ida-pro_92_x64linux.run   ← copied into build context
dissembler/ida_linux/idakeygen_9.2.py           ← copied into build context
```

Build sequence:
1. Ubuntu 22.04 base + `openjdk-21-jdk` + IDA runtime libs
2. Run `ida-pro_92_x64linux.run --mode unattended --prefix /opt/ida`
3. Copy `idakeygen_9.2.py` into `/opt/ida/` and execute it → IDA licensed
4. Download Ghidra from GitHub Releases (or use local zip via `--build-arg GHIDRA_ZIP=...`)
5. Unzip → `/opt/ghidra/`, `chmod +x analyzeHeadless`
6. Copy `main/`, `scripts/`, `samples/` into `/app/`
7. Entrypoint: `python3 /app/main/cli.py` with all script paths pre-wired

---

### Docker — `Dockerfile.ghidra` (Ghidra-only)

Identical but skips the entire IDA section. No `dissembler/` files needed. For users without an IDA licence.

---

### Docker — `docker-compose.yml`

Two services:
- `analyze` — full image (IDA + Ghidra), mounts `./samples:/targets:ro` and `./runs:/app/runs`
- `ghidra` — Ghidra-only image

```bash
docker compose run --rm analyze -m /targets/FLRSCRNSVR.SCR -t ghidra -v
docker compose run --rm analyze -m /targets/FLRSCRNSVR.SCR -t ida -v
```

---

### `.dockerignore`

Excludes: `runs/`, `*.i64` IDA databases, `docs/`, `notes/`, `dissembler/ida_win/` (Windows installer not needed in Linux image).

---

### `README.md` — Rewritten

Covers:
- Repo layout with correct paths (`main/cli.py`, `scripts/`, `samples/`, `dissembler/`)
- Docker quickstart (build, run, Compose) with exact commands
- Linux manual install for both IDA Pro and Ghidra
- Windows manual install for both IDA Pro and Ghidra
- Full `cli.py` flag reference
- Auto-detection priority table for both tools
- Troubleshooting section covering all known failure modes from this entire session:
  - `UnsupportedClassVersionError` + exact delete command
  - OSGi bundle cache stale + exact delete command  
  - IDA not found + override options
  - Ghidra not found + override options
  - Empty output / wrong location debugging steps

---

## Cumulative File List

| File | Status |
|------|--------|
| `scripts/ida_dump.py` | Fixed — 4 IDA 9.x API removals patched |
| `scripts/GhidraDump.java` | Rewritten — Ghidra 12 API fixes + ida_dump.py format alignment |
| `scripts/DisableWinResRef.java` | Added — pre-script for JDK 21 compat (optional, only used if present) |
| `main/cli.py` | Rewritten — cross-platform Windows+Linux, correct output layout |
| `Dockerfile` | New — IDA Pro + Ghidra, exact repo paths |
| `Dockerfile.ghidra` | New — Ghidra-only lightweight image |
| `docker-compose.yml` | New — two services with volume mounts |
| `.dockerignore` | New |
| `README.md` | Rewritten — full deploy guide for Docker + Linux + Windows |

---

## Key Lessons

**OSGi cache is the most common Ghidra script surprise.** Any time a Ghidra Java script change doesn't take effect, clear `%APPDATA%\ghidra\...\osgi\compiled-bundles` on Windows or `~/.local/share/ghidra/.../osgi/compiled-bundles` on Linux.

**The `WindowsResourceReference` class file error is a one-time artifact.** It only happens when the same Ghidra install has been run under two different JDK major versions. Deleting the cached `.class` permanently resolves it.

**`analyzeHeadless.bat` on Windows cannot reliably pass arguments containing spaces.** The `%*` expansion in batch files does not preserve quoting. Any Ghidra headless option that contains spaces (like analyzer names) must be set via a pre-script, not via `-analysisOptions`.

**Ghidra's `LaunchSupport.jar` caches its JDK selection.** Setting `JAVA_HOME` in a subprocess environment is not sufficient — LaunchSupport reads from a preferences file that takes priority. The reliable way to change the JDK Ghidra uses is either: delete the preferences file, or (better) just delete the stale `.class` file so the script recompiles under whatever JDK is active.

**IDA 9.x removed several global functions.** `get_inf_structure()`, `get_kernel_version()`, and `decompiler_initialized()` are all gone. Use flat API equivalents with `hasattr` guards for backward compatibility.