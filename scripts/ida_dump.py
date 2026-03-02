"""
Headless IDAPython export script.

Usage (headless):
    ida64.exe -A -Sida_dump.py --out "path/to/out_dir" binary

Exports (in order):
    1. Header        – arch, bitness, IDA version, counts
    2. Functions     – address + name list
    3. Strings       – C and UTF-16 strings
    4. Imports       – grouped by module
    5. Exports       – ordinal entries
    6. Pseudocode    – Hex-Rays F5 for every function (errors written, never skipped)
    7. Disassembly   – ALL segments, ALL heads, with labels + inline comments
    8. Call graph    – caller -> callee edges (with names)

Improvements over v1 (merged from dump.py reference):
    - auto_wait() so analysis is complete before dumping
    - safe_generate_line() with raw-byte fallback (no blank lines in disasm)
    - ALL segments dumped (not just SEG_CODE), annotated with type
    - Labels (ida_name.get_name) printed above each head
    - Inline comments (non-repeatable + repeatable) after each disasm line
    - Pseudocode: every function gets an entry; decompile errors recorded, never silently skipped
    - Pseudocode summary stats (total / ok / failed)
    - Call graph includes function names, not just addresses
    - Addresses widened to 016X for 64-bit
    - Progress printing for headless runs
"""

import sys
from pathlib import Path

import ida_auto
import ida_bytes
import ida_entry
import ida_funcs
import ida_ida
import ida_lines
import ida_nalt
import ida_name
import ida_segment
import idaapi
import idautils


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_out_dir(argv) -> Path:
    import os

    env_out = os.environ.get("IDA_DUMP_OUT_DIR")
    if env_out:
        return Path(env_out)

    for i, arg in enumerate(argv):
        if arg.startswith("--out="):
            return Path(arg.split("=", 1)[1].strip().strip('"'))
        if arg == "--out" and i + 1 < len(argv):
            return Path(argv[i + 1].strip().strip('"'))
    return Path.cwd() / "ida_dump"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_tags(s: str) -> str:
    return ida_lines.tag_remove(s) if s else ""


def wait_analysis():
    """Block until IDA auto-analysis is finished."""
    try:
        ida_auto.auto_wait()
    except Exception:
        pass


def safe_generate_line(ea: int) -> str:
    """
    Return a disasm line for ea.
    Falls back to raw 'db XX XX ...' if IDA cannot generate one,
    so callers never receive an empty string.
    """
    line = ida_lines.generate_disasm_line(ea, ida_lines.GENDSM_REMOVE_TAGS)
    if line:
        return strip_tags(line).rstrip()
    size = ida_bytes.get_item_size(ea) or 1
    raw  = ida_bytes.get_bytes(ea, size) or b""
    return "db " + " ".join(f"{b:02X}" for b in raw)


def hexrays_ready() -> bool:
    try:
        import ida_hexrays
        if hasattr(ida_hexrays, "init_hexrays_plugin"):
            return bool(ida_hexrays.init_hexrays_plugin())
        return bool(ida_hexrays.decompiler_initialized())  # IDA < 9
    except Exception:
        return False


def progress(msg: str):
    print(f"[ida_dump] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Section 1 – Header
# ---------------------------------------------------------------------------

def dump_header(fh):
    fh.write("# IDA dump\n")
    fh.write(f"input_file: {ida_nalt.get_input_file_path()}\n")

    try:
        procname = ida_ida.inf_get_procname()
    except Exception:
        inf = getattr(getattr(idaapi, "cvar", None), "inf", None)
        procname = getattr(inf, "procName", "unknown") if inf else "unknown"

    try:
        bitness = 64 if ida_ida.inf_is_64bit() else 32
    except Exception:
        inf = getattr(getattr(idaapi, "cvar", None), "inf", None)
        bitness = 64 if inf and getattr(inf, "is_64bit", lambda: False)() else 32

    try:
        from ida_kernwin import get_kernel_version
        ida_ver = get_kernel_version()
    except Exception:
        ida_ver = getattr(idaapi, "get_kernel_version", lambda: "unknown")()

    fh.write(f"arch:       {procname}\n")
    fh.write(f"bitness:    {bitness}\n")
    fh.write(f"func_count: {ida_funcs.get_func_qty()}\n")
    fh.write(f"ida_version:{ida_ver}\n\n")


# ---------------------------------------------------------------------------
# Section 2 – Function list
# ---------------------------------------------------------------------------

def dump_functions(fh):
    fh.write("[functions]\n")
    for ea in idautils.Functions():
        name = ida_funcs.get_func_name(ea) or f"sub_{ea:X}"
        fh.write(f"{ea:016X}  {name}\n")
    fh.write("\n")


# ---------------------------------------------------------------------------
# Section 3 – Strings
# ---------------------------------------------------------------------------

def dump_strings(fh, min_len: int = 4):
    fh.write("[strings]\n")
    strings = idautils.Strings(default_setup=False)
    strings.setup(
        strtypes=[ida_nalt.STRTYPE_C, ida_nalt.STRTYPE_C_16],
        ignore_instructions=False,
        minlen=min_len,
    )
    for st in strings:
        try:
            text = str(st)
        except Exception:
            text = ""
        fh.write(f"{st.ea:016X}  {text}\n")
    fh.write("\n")


# ---------------------------------------------------------------------------
# Section 4 – Imports
# ---------------------------------------------------------------------------

def dump_imports(fh):
    fh.write("[imports]\n")
    nimps = ida_nalt.get_import_module_qty()
    for i in range(nimps):
        mod_name = ida_nalt.get_import_module_name(i) or f"module_{i}"
        fh.write(f"\n  {mod_name}:\n")

        def cb(ea, name, ord_, _mod=mod_name):
            fh.write(f"    {ea:016X}  {name or f'ord_{ord_}'}\n")
            return True

        ida_nalt.enum_import_names(i, cb)
    fh.write("\n")


# ---------------------------------------------------------------------------
# Section 5 – Exports
# ---------------------------------------------------------------------------

def dump_exports(fh):
    fh.write("[exports]\n")
    qty = ida_entry.get_entry_qty()
    for i in range(qty):
        ordv = ida_entry.get_entry_ordinal(i)
        ea   = ida_entry.get_entry(ordv)
        name = ida_entry.get_entry_name(ordv) or f"ord_{ordv}"
        fh.write(f"{ea:016X}  {name}\n")
    fh.write("\n")


# ---------------------------------------------------------------------------
# Section 6 – Pseudocode (Hex-Rays)
# ---------------------------------------------------------------------------

def dump_pseudocode(fh):
    fh.write("[pseudocode]\n")

    if not hexrays_ready():
        fh.write("; Hex-Rays decompiler not available or failed to initialize\n\n")
        return

    import ida_hexrays

    funcs  = sorted(idautils.Functions())
    total  = len(funcs)
    dumped = 0
    failed = 0

    for idx, f_ea in enumerate(funcs, 1):
        name = ida_funcs.get_func_name(f_ea) or f"sub_{f_ea:X}"
        if idx % 200 == 0 or idx == total:
            progress(f"[pseudocode] {idx}/{total}")

        fh.write("\n" + "=" * 100 + "\n")
        fh.write(f"FUNCTION : {name}\n")
        fh.write(f"START_EA : 0x{f_ea:X}\n")
        fh.write("=" * 100 + "\n\n")

        func = ida_funcs.get_func(f_ea)
        if not func:
            failed += 1
            fh.write("// [SKIPPED] get_func() returned None\n")
            continue

        try:
            cfunc = ida_hexrays.decompile(func)
            if not cfunc:
                raise RuntimeError("decompile() returned None")
            for sl in cfunc.get_pseudocode():
                fh.write(f"    {strip_tags(sl.line)}\n")
            dumped += 1
        except Exception as exc:
            failed += 1
            fh.write(f"// [DECOMPILE FAILED] {repr(exc)}\n")

    fh.write(f"\n[pseudocode_summary]\n")
    fh.write(f"total:  {total}\n")
    fh.write(f"ok:     {dumped}\n")
    fh.write(f"failed: {failed}\n\n")


# ---------------------------------------------------------------------------
# Section 7 – Full disassembly (ALL segments)
# ---------------------------------------------------------------------------

_SEG_TYPE_NAMES = {
    ida_segment.SEG_CODE:   "CODE",
    ida_segment.SEG_DATA:   "DATA",
    ida_segment.SEG_BSS:    "BSS",
    ida_segment.SEG_NULL:   "NULL",
    ida_segment.SEG_XTRN:   "XTRN",
    ida_segment.SEG_COMM:   "COMM",
    ida_segment.SEG_ABSSYM: "ABS",
}


def dump_disasm(fh):
    fh.write("[disasm]\n")
    seg_starts = sorted(idautils.Segments())
    total = len(seg_starts)

    for sidx, s_ea in enumerate(seg_starts, 1):
        seg = ida_segment.getseg(s_ea)
        if not seg:
            continue

        seg_name = ida_segment.get_segm_name(seg) or "SEG"
        seg_type = _SEG_TYPE_NAMES.get(seg.type, f"type{seg.type}")
        start, end = seg.start_ea, seg.end_ea

        progress(f"[disasm] segment {sidx}/{total}: {seg_name} ({seg_type})")

        fh.write("\n" + "#" * 100 + "\n")
        fh.write(f"SEGMENT : {seg_name}\n")
        fh.write(f"TYPE    : {seg_type}\n")
        fh.write(f"RANGE   : 0x{start:X} - 0x{end:X}\n")
        fh.write("#" * 100 + "\n\n")

        for ea in idautils.Heads(start, end):
            # Label / name
            label = ida_name.get_name(ea)
            if label:
                fh.write(f"{label}:\n")

            line = safe_generate_line(ea)
            fh.write(f"  {ea:016X}:  {line}\n")

            # Non-repeatable comment
            cmt = ida_bytes.get_cmt(ea, 0)
            if cmt:
                fh.write(f"                          ; {cmt}\n")
            # Repeatable comment
            rcmt = ida_bytes.get_cmt(ea, 1)
            if rcmt:
                fh.write(f"                          ; (r) {rcmt}\n")

        fh.write("\n")


# ---------------------------------------------------------------------------
# Section 8 – Call graph
# ---------------------------------------------------------------------------

def dump_callgraph(fh):
    fh.write("[callgraph]\n")
    seen: set = set()
    for caller_ea in idautils.Functions():
        caller_name = ida_funcs.get_func_name(caller_ea) or f"sub_{caller_ea:X}"
        for insn in idautils.FuncItems(caller_ea):
            for target in idautils.CodeRefsFrom(insn, 0):
                callee = ida_funcs.get_func(target)
                if not callee:
                    continue
                edge = (caller_ea, callee.start_ea)
                if edge in seen:
                    continue
                seen.add(edge)
                callee_name = ida_funcs.get_func_name(callee.start_ea) or f"sub_{callee.start_ea:X}"
                fh.write(
                    f"{caller_ea:016X} {caller_name}"
                    f"  ->  "
                    f"{callee.start_ea:016X} {callee_name}\n"
                )
    fh.write("\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    progress("waiting for auto-analysis...")
    wait_analysis()

    out_dir = parse_out_dir(sys.argv[1:])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(ida_nalt.get_root_filename()).stem}.txt"

    progress(f"output -> {out_path}")

    with out_path.open("w", encoding="utf-8", errors="replace") as fh:
        dump_header(fh)
        dump_functions(fh)
        dump_strings(fh)
        dump_imports(fh)
        dump_exports(fh)
        dump_pseudocode(fh)   # pseudocode before raw disasm
        dump_disasm(fh)
        dump_callgraph(fh)

    progress(f"done -> {out_path}")
    idaapi.qexit(0)


if __name__ == "__main__":
    main()