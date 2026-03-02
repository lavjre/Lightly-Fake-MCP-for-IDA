# Debug Report — IDA Autodetect Failure
**File:** `cli.py` | **Session:** March 2026

---

## Summary

Autodetect silently failed across 4 separate fix iterations before the root cause was identified. The final cause was a **breaking change in IDA 9.x** that renamed its executables. All previous fixes were correct but irrelevant because they were searching for filenames that no longer exist.

---

## Timeline

### Iteration 1 — Original Bug Report
**Symptom:**
```
[warn] IDA autodetect tried 0 paths:
Could not locate IDA executable.
```
`tried` was completely empty — the logging itself was broken.

**Root cause:** The resolution loop only appended to `tried` when `resolved` was not `None`. Bare name candidates like `ida64` that aren't on PATH resolve to `None` via `shutil.which`, so they were silently skipped and never logged.

**Fix:** Split the loop into two explicit branches — absolute paths log and check `.is_file()` directly; bare names log with `(via PATH)` suffix before calling `shutil.which`.

---

### Iteration 2 — Registry & Glob Not Running
**Symptom:**
```
[warn] IDA autodetect tried 4 candidates:
  ida64 (via PATH)
  idat64 (via PATH)
  ida (via PATH)
  idat (via PATH)
```
Still only 4 candidates — the Windows registry and `Program Files` glob blocks weren't producing anything.

**Suspected cause:** `sys.platform != "win32"` or stale file on disk.

**Diagnosis run:**
```powershell
python -c "import sys; print(sys.platform)"   # win32 ✓
python -c "import ast; src=open('cli.py').read(); print('NEW' if 'SystemDrive' in src else 'OLD')"  # NEW ✓
```
Both confirmed correct — meaning the blocks were running but producing empty results.

---

### Iteration 3 — Registry Has No Entry, Glob Finds the Folder
**Symptom:** Same 4 candidates.

**Diagnosis run:**
```powershell
python -c "
import winreg
try:
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Hex-Rays') as hk:
        ...
except Exception as e:
    print('registry error:', e)
"
```

**Output:**
```
--- REGISTRY ---
registry error: [WinError 2] The system cannot find the file specified
--- GLOB ---
scanning: C:\Program Files | exists: True
  found: C:\Program Files\IDA Professional 9.2
```

**Findings:**
- IDA 9.x does **not** write a registry entry under `HKLM\SOFTWARE\Hex-Rays` — the key doesn't exist at all
- The glob **does** find the folder `IDA Professional 9.2` correctly
- But the code then looks for `ida64.exe` / `idat64.exe` inside it — which don't exist in IDA 9.x

---

### Iteration 4 — Root Cause: IDA 9.x Renamed Executables
**Diagnosis run:**
```powershell
dir "C:\Program Files\IDA Professional 9.2\*.exe"
```

**Output:**
```
ida.exe
idat.exe
hvui.exe
idapyswitch.exe
...
```

**Root cause confirmed:** IDA 9.x dropped the `64` suffix from its executables.

| Version | GUI executable | Headless executable |
|---------|---------------|---------------------|
| IDA ≤ 8.x | `ida64.exe` | `idat64.exe` |
| IDA 9.x | `ida.exe` | `idat.exe` |

The glob found `C:\Program Files\IDA Professional 9.2` correctly on every iteration, but then searched for `ida64.exe` inside it — a file that no longer exists.

---

## Final Fix

Added `ida.exe` and `idat.exe` to every exe search list throughout the function:

```python
exe_names = ["ida64.exe", "idat64.exe", "ida.exe", "idat.exe"]
```

Applied to all three search sites:
- Registry `InstallPath` lookup
- `IDA_HOME` environment variable expansion  
- `Program Files` glob subdirectory scan
- Drive root shallow scan

---

## Other Bugs Fixed Along the Way

| Bug | Fix |
|-----|-----|
| `tried` list always empty | Log all candidates unconditionally, not just resolved ones |
| Absolute paths never validated with `.is_file()` | Explicit branch: check `.is_file()` before returning |
| No candidate deduplication | `dict.fromkeys()` to deduplicate while preserving order |
| `glob.glob()` with `\` patterns unreliable on Windows | Replaced with `Path.glob()` using `/` separators |
| Registry lookup used single hardcoded key `IDA` | Enumerate all subkeys under `Hex-Rays` with `winreg.EnumKey` |
| Linux/macOS paths missing entirely | Added `/usr/local/bin`, `/opt/IDA*`, `~/.local/bin` |
| Broad drive root globs (`C:\`, `D:\`, `E:\`) could hang | Replaced with depth-limited `Program Files` scans only |

---

## Lesson

When autodetect fails silently, the first fix should always be **making the logging exhaustive** — log every candidate regardless of whether it resolves, so the next run tells you exactly what was and wasn't searched. In this case, good logging in iteration 1 led directly to the diagnosis in iteration 3.

The actual root cause (renamed executables) had nothing to do with the original code bugs — it would have failed even with a perfectly written autodetect, because the target filename simply didn't exist in the search lists.