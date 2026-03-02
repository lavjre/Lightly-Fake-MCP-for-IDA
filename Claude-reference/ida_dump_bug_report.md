# IDA Dump Script — Bug Fix Report

**Project:** `Lightly-Fake-MCP-for-IDA`
**Script:** `ida_dump.py`
**IDA Version:** IDA Professional 9.2 (v9.2.0.250718)
**Date:** 2026-03-02

---

## Summary

The `ida_dump.py` headless export script encountered four runtime errors when run against IDA Professional 9.2. All errors were caused by API breakages introduced in IDA 9.x, where several legacy functions were removed or had their signatures changed.

---

## Bug #1 — `idaapi.get_inf_structure()` removed
**Error:** `AttributeError: module 'idaapi' has no attribute 'get_inf_structure'`
**Fix:** Replaced with `ida_ida.inf_get_procname()` / `inf_is_64bit()` with try/except fallback.

## Bug #2 — `ida_idaapi.get_kernel_version()` removed
**Error:** `AttributeError: module 'ida_idaapi' has no attribute 'get_kernel_version'`
**Fix:** Import from `ida_kernwin` instead, with fallback to `idaapi.get_kernel_version`.

## Bug #3 — `strtypes` expects a list, not a bitmask
**Error:** `TypeError: expected a list`
**Fix:** `strtypes=ida_nalt.STRTYPE_C | ida_nalt.STRTYPE_C_16` → `strtypes=[ida_nalt.STRTYPE_C, ida_nalt.STRTYPE_C_16]`

## Bug #4 — `ida_hexrays.decompiler_initialized()` removed
**Error:** `AttributeError: module 'ida_hexrays' has no attribute 'decompiler_initialized'`
**Fix:** Replaced with `init_hexrays_plugin()` behind a `hasattr` guard + try/except.

---

## Summary Table

| # | Function | Module | Change |
|---|----------|--------|--------|
| 1 | `get_inf_structure()` | `idaapi` | → `ida_ida.inf_get_procname()` |
| 2 | `get_kernel_version()` | `ida_idaapi` | → `ida_kernwin.get_kernel_version()` |
| 3 | `strtypes` bitmask | `ida_strlist` | → `list` |
| 4 | `decompiler_initialized()` | `ida_hexrays` | → `init_hexrays_plugin()` |

All fixes are backwards-compatible with IDA 7.6+.