"""
Headless IDAPython export script.

Usage inside IDA:
    ida64.exe -A -Sida_dump.py --out "path/to/out" binary

Exports:
    - header
    - functions
    - strings
    - imports
    - exports
    - full disassembly text view (code segments)
    - Hex-Rays pseudocode per function (when available)
    - call graph edges (caller -> callee)
"""

import sys
from pathlib import Path

import ida_bytes
import ida_funcs
import ida_idaapi
import ida_lines
import ida_nalt
import ida_segment
import ida_entry
import idaapi
import idautils


def parse_out_dir(argv) -> Path:
    for i, arg in enumerate(argv):
        if arg.startswith("--out="):
            return Path(arg.split("=", 1)[1])
        if arg == "--out" and i + 1 < len(argv):
            return Path(argv[i + 1])
    return Path.cwd() / "ida_dump"


def dump_header(fh):
    fh.write("# IDA dump\n")
    fh.write(f"input_file: {ida_nalt.get_input_file_path()}\n")
    fh.write(f"arch: {idaapi.get_inf_structure().procName}\n")
    fh.write(f"bitness: {idaapi.get_inf_structure().is_64bit() and 64 or 32}\n")
    fh.write(f"func_count: {ida_funcs.get_func_qty()}\n")
    fh.write(f"ida_version: {ida_idaapi.get_kernel_version()}\n\n")


def dump_functions(fh):
    fh.write("[functions]\n")
    for ea in idautils.Functions():
        name = ida_funcs.get_func_name(ea)
        fh.write(f"{ea:08X} {name}\n")
    fh.write("\n")


def dump_strings(fh, min_len=4):
    fh.write("[strings]\n")
    strings = idautils.Strings(default_setup=False)
    strings.setup(
        strtypes=ida_nalt.STRTYPE_C | ida_nalt.STRTYPE_C_16,
        ignore_instructions=False,
        minlen=min_len,
    )
    for st in strings:
        try:
            text = str(st)
        except Exception:
            text = ""
        fh.write(f"{st.ea:08X} {text}\n")
    fh.write("\n")


def dump_imports(fh):
    fh.write("[imports]\n")
    nimps = ida_nalt.get_import_module_qty()

    def cb(ea, name, _ord):
        fh.write(f"{ea:08X} {name}\n")
        return True

    for i in range(nimps):
        mod_name = ida_nalt.get_import_module_name(i)
        fh.write(f"{mod_name}:\n")
        ida_nalt.enum_import_names(i, cb)
    fh.write("\n")


def dump_exports(fh):
    fh.write("[exports]\n")
    qty = ida_entry.get_entry_qty()
    for i in range(qty):
        ordv = ida_entry.get_entry_ordinal(i)
        ea = ida_entry.get_entry(ordv)
        name = ida_entry.get_entry_name(ordv) or f"ord_{ordv}"
        fh.write(f"{ea:08X} {name}\n")
    fh.write("\n")


def dump_disasm(fh):
    fh.write("[disasm]\n")
    for seg_start in idautils.Segments():
        seg = ida_segment.getseg(seg_start)
        if not seg or seg.type != ida_segment.SEG_CODE:
            continue
        fh.write(f"; segment {seg.name} {seg.start_ea:08X}-{seg.end_ea:08X}\n")
        for ea in idautils.Heads(seg.start_ea, seg.end_ea):
            if not ida_bytes.is_code(ida_bytes.get_flags(ea)):
                continue
            line = ida_lines.generate_disasm_line(ea, ida_lines.GENDSM_REMOVE_TAGS) or ""
            line = ida_lines.tag_remove(line)
            fh.write(f"{ea:08X}: {line}\n")
    fh.write("\n")


def dump_pseudocode(fh):
    fh.write("[pseudocode]\n")
    try:
        import ida_hexrays
    except Exception:
        fh.write("; Hex-Rays decompiler not available\n\n")
        return

    if not ida_hexrays.init_hexrays():
        fh.write("; Hex-Rays failed to initialize\n\n")
        return

    for f_ea in idautils.Functions():
        func = ida_funcs.get_func(f_ea)
        if not func:
            continue
        try:
            cfunc = ida_hexrays.decompile(func)
        except ida_hexrays.DecompilationFailure:
            continue
        fh.write(f"{f_ea:08X} {ida_funcs.get_func_name(f_ea)}\n")
        for line in cfunc.get_pseudocode():
            fh.write(f"    {ida_lines.tag_remove(line.line)}\n")
        fh.write("\n")
    fh.write("\n")


def dump_callgraph(fh):
    fh.write("[callgraph]\n")
    seen = set()
    for caller_ea in idautils.Functions():
        for insn in idautils.FuncItems(caller_ea):
            for target in idautils.CodeRefsFrom(insn, 0):
                callee = ida_funcs.get_func(target)
                if not callee:
                    continue
                edge = (caller_ea, callee.start_ea)
                if edge in seen:
                    continue
                seen.add(edge)
                fh.write(f"{caller_ea:08X} -> {callee.start_ea:08X}\n")
    fh.write("\n")


def main():
    out_dir = parse_out_dir(sys.argv[1:])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(ida_nalt.get_root_filename()).stem}.txt"

    with out_path.open("w", encoding="utf-8", errors="replace") as fh:
        dump_header(fh)
        dump_functions(fh)
        dump_strings(fh)
        dump_imports(fh)
        dump_exports(fh)
        dump_disasm(fh)
        dump_pseudocode(fh)
        dump_callgraph(fh)

    idaapi.qexit(0)


if __name__ == "__main__":
    main()
