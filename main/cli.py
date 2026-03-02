"""
cli.py — headless disassembly runner
Supports: IDA Pro + Ghidra on Windows and Linux
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from glob import glob
from pathlib import Path
from typing import Iterable, List, Optional

# ── Constants ─────────────────────────────────────────────────────────────────

MB = 1024 * 1024
DEFAULT_MAX_SIZE_MB    = 75
DEFAULT_CHUNK_MB       = 50
DEFAULT_ZIP_THRESHOLD  = 7
IS_WINDOWS             = sys.platform == "win32"

# Directory of cli.py itself — anchor for default script paths
_SCRIPT_DIR = Path(__file__).resolve().parent


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless disassembly runner (IDA / Ghidra) for Windows and Linux."
    )

    # ── Input ──────────────────────────────────────────────────────────────────
    parser.add_argument(
        "binaries", nargs="*",
        help="Input binaries (positional). Optional when -m/--multi is used.",
    )
    parser.add_argument(
        "-m", "--multi", nargs="+",
        help="List of binaries to process sequentially.",
    )

    # ── Output ─────────────────────────────────────────────────────────────────
    parser.add_argument(
        "-d", "--out-dir",
        default=str(_SCRIPT_DIR / "exports"),
        help="Base output directory. Each binary gets its own sub-folder. "
             "(default: <script_dir>/exports)",
    )

    # ── Tool selection ─────────────────────────────────────────────────────────
    parser.add_argument(
        "-t", "--tool", choices=["ida", "ghidra"], default="ida",
        help="Disassembler to use (default: ida).",
    )

    # ── Verbosity ──────────────────────────────────────────────────────────────
    parser.add_argument("-v", "--verbose",  action="store_true", help="Verbose logging.")
    parser.add_argument("-q", "--quiet",    action="store_true", help="Silence non-error output.")
    parser.add_argument("-n", "--dry-run",  action="store_true", help="Print commands without running.")
    parser.add_argument("-f", "--overwrite",action="store_true", help="Overwrite existing output.")

    # ── Size controls ──────────────────────────────────────────────────────────
    parser.add_argument(
        "--pattern",
        default="*.txt,*.text,*.asm,*.c,*.cg,*.graph,*.json",
        help="Comma-separated globs for size-limit enforcement.",
    )
    parser.add_argument("--max-size",      type=int, default=DEFAULT_MAX_SIZE_MB,
                        help="Split files larger than this many MB (default: 75).")
    parser.add_argument("--chunk-size",    type=int, default=DEFAULT_CHUNK_MB,
                        help="Target chunk size in MB when splitting (default: 50).")
    parser.add_argument("--zip-threshold", type=int, default=DEFAULT_ZIP_THRESHOLD,
                        help="Zip parts when count reaches this number (default: 7).")
    parser.add_argument("--no-zip",        action="store_true", help="Never zip split parts.")
    parser.add_argument("--keep-source",   action="store_true", help="Keep original file after splitting.")

    # ── IDA knobs ──────────────────────────────────────────────────────────────
    parser.add_argument(
        "--ida-path", default=None,
        help="Path to ida64/idat64 executable. If omitted, auto-detect.",
    )
    parser.add_argument(
        "--ida-script",
        default=str(_SCRIPT_DIR / "scripts" / "ida_dump.py"),
        help="IDAPython script to run (default: <script_dir>/scripts/ida_dump.py).",
    )

    # ── Ghidra knobs ───────────────────────────────────────────────────────────
    parser.add_argument(
        "--ghidra-headless", default=None,
        help="Path to analyzeHeadless(.bat). If omitted, auto-detect.",
    )
    parser.add_argument(
        "--ghidra-script",
        default=str(_SCRIPT_DIR / "scripts" / "GhidraDump.java"),
        help="Ghidra post-script (default: <script_dir>/scripts/GhidraDump.java).",
    )
    parser.add_argument(
        "--ghidra-prescript",
        default=str(_SCRIPT_DIR / "scripts" / "DisableWinResRef.java"),
        help="Ghidra pre-script for JDK compat fixes on PE files. "
             "Pass empty string to disable.",
    )
    parser.add_argument(
        "--ghidra-project-root", default=None,
        help="Directory for Ghidra projects; defaults to a per-run temp dir.",
    )

    # ── Misc ───────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="Seconds before killing the disassembler (default: no limit).",
    )

    return parser.parse_args()


# ── Logging ───────────────────────────────────────────────────────────────────

def emit(msg: str, args: argparse.Namespace, level: str = "info") -> None:
    if args.quiet:
        return
    if level == "error":
        print(f"[!] {msg}", file=sys.stderr)
        return
    if level == "debug" and not args.verbose:
        return
    if args.verbose:
        print(f"[{level}] {msg}")
        return
    if level in ("info", "warn"):
        print(msg)


# ── Input collection ──────────────────────────────────────────────────────────

def collect_binaries(args: argparse.Namespace) -> List[Path]:
    picks = args.multi or args.binaries
    if not picks:
        raise SystemExit("No input binaries provided. Use positional args or -m/--multi.")
    paths: List[Path] = []
    for item in picks:
        p = Path(item).expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"Input not found: {p}")
        paths.append(p)
    return paths


# ── Output directory helpers ──────────────────────────────────────────────────

def resolve_binary_dirs(binary: Path, args: argparse.Namespace):
    """
    Returns (dump_dir, log_dir) for a specific binary.

    Layout under --out-dir:
        exports/
            <binary_stem>/    ← dump output lives here
            logs/             ← stdout/stderr logs
    """
    root     = Path(args.out_dir).expanduser().resolve()
    dump_dir = root / binary.stem
    log_dir  = root / "logs"
    return dump_dir, log_dir


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def clear_previous_outputs(binary: Path, dump_dir: Path, log_dir: Path,
                            args: argparse.Namespace) -> None:
    if not args.overwrite:
        return
    stem = binary.stem
    for pat in [f"{stem}.txt", f"{stem}.txt.part*", f"{stem}.zip", f"{stem}.zip.part*"]:
        for f in dump_dir.glob(pat):
            f.unlink(missing_ok=True)
    for pat in [f"{stem}_*_stdout.log", f"{stem}_*_stderr.log"]:
        for f in log_dir.glob(pat):
            f.unlink(missing_ok=True)


# ── IDA command builder ───────────────────────────────────────────────────────

def build_ida_command(binary: Path, out_dir: Path, log_dir: Path,
                      args: argparse.Namespace) -> List[str]:
    """
    Windows:  ida64.exe  -A  -L<log>  -S"<script> --out=<dir>"  <binary>
    Linux:    idat64     -A  -L<log>  -S"<script> --out=<dir>"  <binary>

    The IDA_DUMP_OUT_DIR env var is also set so ida_dump.py can find it
    even when -S quoting is awkward on the shell.

    ida64  = GUI daemon  (Windows only, exits after analysis)
    idat64 = terminal/batch mode (preferred on Linux; also works on Windows)
    """
    script_path = Path(args.ida_script).expanduser().resolve()
    log_path    = log_dir / f"{binary.stem}_ida_stdout.log"

    # Use POSIX separators — IDA accepts them on Windows too and they
    # avoid backslash-escape issues when passed through shell layers.
    script_arg = f"{script_path.as_posix()} --out={out_dir.as_posix()}"

    return [
        str(args.ida_path),
        "-A",               # autonomous / batch — no GUI prompts
        f"-L{log_path}",    # IDA internal log (analysis messages)
        f"-S{script_arg}",  # run script after analysis
        str(binary),
    ]


# ── Ghidra command builder ────────────────────────────────────────────────────

def build_ghidra_command(binary: Path, out_dir: Path, args: argparse.Namespace,
                          proj_root: Path) -> List[str]:
    """
    Windows: analyzeHeadless.bat  <proj_dir> <proj_name>
             -import <binary> -scriptPath <dir>
             [-preScript DisableWinResRef.java]
             -postScript GhidraDump.java  out=<out_dir>

    Linux:   analyzeHeadless  (same args, no .bat)

    GhidraDump.java receives  ["out", "<out_dir>"]  because analyzeHeadless
    splits postScript args at '='.  argOrDefault() in the Java script handles
    both "key=value" and ["key", "value"] forms.

    DisableWinResRef.java is only added when the file actually exists.
    It works around the Ghidra 12 / JDK 21 incompatibility with the
    WindowsResourceReferenceAnalyzer that was compiled for JDK 25.
    """
    script_path = Path(args.ghidra_script).expanduser().resolve()
    script_dir  = str(script_path.parent)
    script_name = script_path.name
    proj_name   = binary.stem
    proj_dir    = proj_root / proj_name
    proj_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(args.ghidra_headless),
        str(proj_dir.parent),
        proj_name,
        "-import", str(binary),
        "-scriptPath", script_dir,
    ]

    # Add preScript only if the file actually exists on disk
    if args.ghidra_prescript:
        pre = Path(args.ghidra_prescript).expanduser().resolve()
        if pre.is_file():
            cmd += ["-preScript", pre.name]
        else:
            emit(f"preScript not found (skipping): {pre}", args, level="warn")

    cmd += [
        "-postScript", script_name,
        f"out={out_dir}",
    ]

    return cmd


# ── File size enforcement ─────────────────────────────────────────────────────

def split_file(path: Path, chunk_bytes: int) -> List[Path]:
    parts: List[Path] = []
    with path.open("rb") as src:
        idx = 0
        while True:
            buf = src.read(chunk_bytes)
            if not buf:
                break
            part = path.with_suffix(path.suffix + f".part{idx:03d}")
            part.write_bytes(buf)
            parts.append(part)
            idx += 1
    return parts


def zip_parts(parts: Iterable[Path], dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in parts:
            zf.write(p, arcname=p.name)
    return dest


def enforce_limits(path: Path, args: argparse.Namespace) -> List[Path]:
    max_bytes   = args.max_size   * MB
    chunk_bytes = args.chunk_size * MB

    if not path.is_file():
        return []
    if path.stat().st_size <= max_bytes:
        return [path]

    parts = split_file(path, chunk_bytes)
    if not args.keep_source:
        path.unlink(missing_ok=True)

    if len(parts) >= args.zip_threshold and not args.no_zip:
        zip_path = path.with_suffix(".zip")
        zip_parts(parts, zip_path)
        if not args.keep_source:
            for p in parts:
                p.unlink(missing_ok=True)
        if zip_path.stat().st_size > max_bytes:
            splits = split_file(zip_path, chunk_bytes)
            if not args.keep_source:
                zip_path.unlink(missing_ok=True)
            return splits
        return [zip_path]
    return parts


def enforce_for_patterns(base: Path, args: argparse.Namespace) -> None:
    seen: set = set()
    for pat in [p.strip() for p in args.pattern.split(",") if p.strip()]:
        for f in base.rglob(pat):
            if f not in seen:
                seen.add(f)
                enforce_limits(f, args)


# ── IDA auto-detection ────────────────────────────────────────────────────────

def _ida_exe_names() -> List[str]:
    """Preferred IDA executable names, ordered by preference."""
    if IS_WINDOWS:
        # idat64 = terminal/headless mode; ida64 = GUI daemon (also works with -A)
        return ["idat64.exe", "ida64.exe", "idat.exe", "ida.exe"]
    return ["idat64", "ida64", "idat", "ida"]


def detect_ida_path(args: argparse.Namespace) -> str:
    candidates: List[str] = []

    # 1. Explicit CLI flag
    if args.ida_path:
        candidates.append(args.ida_path)

    # 2. Environment variables
    if "IDA_PATH" in os.environ:
        candidates.append(os.environ["IDA_PATH"])
    if "IDA_HOME" in os.environ:
        base = Path(os.environ["IDA_HOME"])
        for exe in _ida_exe_names():
            candidates.append(str(base / exe))

    # 3. Windows registry (Hex-Rays install records InstallPath)
    if IS_WINDOWS:
        try:
            import winreg
            reg_base = r"SOFTWARE\Hex-Rays"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_base) as hk:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(hk, i)
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                            reg_base + "\\" + sub) as sk:
                            try:
                                install_dir, _ = winreg.QueryValueEx(sk, "InstallPath")
                                for exe in _ida_exe_names():
                                    candidates.append(str(Path(install_dir) / exe))
                            except FileNotFoundError:
                                pass
                        i += 1
                    except OSError:
                        break
        except Exception:
            pass

    # 4. Common install directories
    if IS_WINDOWS:
        for root in dict.fromkeys(filter(None, [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ])):
            rp = Path(root)
            if not rp.exists():
                continue
            for pat in ("IDA*", "Hex-Rays/IDA*", "Hex-Rays\\IDA*"):
                for subdir in rp.glob(pat):
                    if subdir.is_dir():
                        for exe in _ida_exe_names():
                            p = subdir / exe
                            if p.exists():
                                candidates.append(str(p))
        # Shallow drive-root scan (e.g. C:\IDA Pro 9.0\)
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        for pat in ("IDA*", "*/IDA*", "IDA Pro*", "*/IDA Pro*"):
            for subdir in drive.glob(pat):
                if subdir.is_dir():
                    for exe in _ida_exe_names():
                        p = subdir / exe
                        if p.exists():
                            candidates.append(str(p))
    else:
        # Linux / macOS standard prefixes
        for prefix in ("/usr/local", "/usr", str(Path.home() / ".local")):
            for name in _ida_exe_names():
                candidates.append(str(Path(prefix) / "bin" / name))
        for base_str in ("/opt", str(Path.home())):
            bp = Path(base_str)
            if not bp.exists():
                continue
            for pat in (
                "IDA*/idat64", "IDA*/ida64", "IDA*/idat", "IDA*/ida",
                "ida*/idat64", "ida*/ida64",
                "idapro*/idat64", "idapro*/ida64",
                "IDA Pro*/idat64", "IDA Pro*/ida64",
            ):
                candidates.extend(str(p) for p in bp.glob(pat))
        # macOS app bundles
        if sys.platform == "darwin":
            for pat in (
                "/Applications/IDA*/idat64",
                "/Applications/IDA*/ida64",
                "/Applications/IDA*.app/Contents/MacOS/idat64",
                "/Applications/IDA*.app/Contents/MacOS/ida64",
            ):
                candidates.extend(glob(pat))

    # 5. PATH fallback
    candidates.extend(_ida_exe_names())

    return _first_valid(candidates, args, label="IDA",
                        hint="--ida-path or IDA_PATH env var")


# ── Ghidra auto-detection ─────────────────────────────────────────────────────

def detect_ghidra_headless(args: argparse.Namespace) -> str:
    candidates: List[str] = []

    # 1. Explicit CLI flag
    if args.ghidra_headless:
        candidates.append(args.ghidra_headless)

    # 2. Environment variables
    if "GHIDRA_HEADLESS" in os.environ:
        candidates.append(os.environ["GHIDRA_HEADLESS"])
    if "GHIDRA_INSTALL_DIR" in os.environ:
        base = Path(os.environ["GHIDRA_INSTALL_DIR"]) / "support"
        # Try .bat first on Windows, plain script on Linux
        if IS_WINDOWS:
            candidates.append(str(base / "analyzeHeadless.bat"))
        candidates.append(str(base / "analyzeHeadless"))

    # 3. PATH fallback
    if IS_WINDOWS:
        candidates.append("analyzeHeadless.bat")
    candidates.append("analyzeHeadless")

    # 4. Common install directories
    if IS_WINDOWS:
        for root in dict.fromkeys(filter(None, [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ])):
            for pat in [
                "ghidra*\\support\\analyzeHeadless.bat",
                "Ghidra*\\support\\analyzeHeadless.bat",
                "ghidra*\\support\\analyzeHeadless",
                "Ghidra*\\support\\analyzeHeadless",
            ]:
                candidates.extend(glob(os.path.join(root, pat)))
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        for pat in (
            "Ghidra*/support/analyzeHeadless.bat",
            "ghidra*/support/analyzeHeadless.bat",
            "*/Ghidra*/support/analyzeHeadless.bat",
            "*/ghidra*/support/analyzeHeadless.bat",
        ):
            candidates.extend(str(p) for p in drive.glob(pat))
    else:
        for prefix in ("/usr/local", "/usr", str(Path.home() / ".local")):
            candidates.append(str(Path(prefix) / "bin" / "analyzeHeadless"))
        for base_str in ("/opt", str(Path.home())):
            bp = Path(base_str)
            if not bp.exists():
                continue
            for pat in (
                "ghidra*/support/analyzeHeadless",
                "Ghidra*/support/analyzeHeadless",
                "*/ghidra*/support/analyzeHeadless",
                "*/Ghidra*/support/analyzeHeadless",
                "ghidra_*/support/analyzeHeadless",
                "Ghidra_*/support/analyzeHeadless",
            ):
                candidates.extend(str(p) for p in bp.glob(pat))

    return _first_valid(candidates, args, label="Ghidra analyzeHeadless",
                        hint="--ghidra-headless or GHIDRA_HEADLESS env var")


# ── Generic resolver helper ───────────────────────────────────────────────────

def _first_valid(candidates: List[str], args: argparse.Namespace,
                 label: str, hint: str) -> str:
    tried: List[str] = []
    for cand in dict.fromkeys(c for c in candidates if c):
        is_path = os.path.isabs(cand) or os.sep in cand or "/" in cand
        if is_path:
            tried.append(cand)
            if Path(cand).is_file():
                return cand
        else:
            tried.append(f"{cand} (PATH)")
            found = shutil.which(cand)
            if found and Path(found).is_file():
                return str(Path(found))

    if args.verbose:
        emit(
            f"{label} autodetect tried {len(tried)} candidates:\n  " +
            "\n  ".join(tried) +
            f"\n  Tip: use {hint}",
            args, level="warn",
        )
    raise SystemExit(f"Could not locate {label}. Use {hint}.")


# ── Per-binary runner ─────────────────────────────────────────────────────────

def run_one(binary: Path, args: argparse.Namespace) -> None:
    dump_dir, log_dir = resolve_binary_dirs(binary, args)
    ensure_dir(dump_dir)
    ensure_dir(log_dir)
    clear_previous_outputs(binary, dump_dir, log_dir, args)

    temp_proj_root: Optional[Path] = None
    env = os.environ.copy()

    if args.tool == "ghidra":
        if args.ghidra_project_root:
            proj_root = Path(args.ghidra_project_root).expanduser().resolve()
            ensure_dir(proj_root)
        else:
            temp_proj_root = Path(tempfile.mkdtemp(prefix="ghidra_proj_"))
            proj_root = temp_proj_root
        # NOPAUSE suppresses "Press any key to continue" in analyzeHeadless.bat
        env.setdefault("NOPAUSE", "1")
        cmd = build_ghidra_command(binary, dump_dir, args, proj_root)
    else:
        # Pass output dir via env var — ida_dump.py reads IDA_DUMP_OUT_DIR
        # as a reliable fallback when -S quoting is tricky on some shells
        env["IDA_DUMP_OUT_DIR"] = str(dump_dir)
        cmd = build_ida_command(binary, dump_dir, log_dir, args)

    emit(f"{binary.name} -> {args.tool.upper()} @ {dump_dir}", args)
    emit(f"CMD: {' '.join(str(c) for c in cmd)}", args, level="debug")

    if args.dry_run:
        emit("Dry run — command not executed.", args, level="warn")
        return

    stdout_path = log_dir / f"{binary.stem}_{args.tool}_stdout.log"
    rc = 0

    try:
        if args.verbose:
            # Stream output to terminal AND log simultaneously
            with stdout_path.open("w", encoding="utf-8", errors="replace") as out_f:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    env=env,
                )
                assert proc.stdout
                try:
                    for line in proc.stdout:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        out_f.write(line)
                    proc.wait(timeout=args.timeout)
                    rc = proc.returncode
                except subprocess.TimeoutExpired:
                    proc.kill()
                    emit(f"{binary.name}: timed out after {args.timeout}s", args, level="error")
                    return
                except KeyboardInterrupt:
                    proc.kill()
                    raise
        else:
            stderr_path = log_dir / f"{binary.stem}_{args.tool}_stderr.log"
            with stdout_path.open("w",  encoding="utf-8", errors="replace") as out_f, \
                 stderr_path.open("w", encoding="utf-8", errors="replace") as err_f:
                try:
                    result = subprocess.run(
                        cmd, stdout=out_f, stderr=err_f,
                        check=False, timeout=args.timeout, env=env,
                    )
                    rc = result.returncode
                except subprocess.TimeoutExpired:
                    emit(f"{binary.name}: timed out after {args.timeout}s", args, level="error")
                    return
    except KeyboardInterrupt:
        raise

    if rc != 0:
        emit(f"{binary.name}: exited with code {rc}", args, level="error")
        if stdout_path.is_file():
            lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for ln in lines[-30:]:
                emit("  " + ln, args, level="error")
        return

    # Enforce file-size limits on generated output
    enforce_for_patterns(dump_dir, args)

    # Clean up the temporary Ghidra project directory
    if temp_proj_root:
        shutil.rmtree(temp_proj_root, ignore_errors=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # Resolve any relative script paths given on the CLI to absolute
    for attr in ("ida_script", "ghidra_script", "ghidra_prescript"):
        val = getattr(args, attr, None)
        if val:
            p = Path(val)
            if not p.is_absolute():
                p = (_SCRIPT_DIR / p).resolve()
            setattr(args, attr, str(p))

    binaries = collect_binaries(args)

    # Pre-create output root + logs dir
    out_base = Path(args.out_dir).expanduser().resolve()
    ensure_dir(out_base)
    ensure_dir(out_base / "logs")

    # Auto-detect the requested tool once before the loop
    if args.tool == "ida":
        args.ida_path = detect_ida_path(args)
        emit(f"IDA path: {args.ida_path}", args)
    else:
        args.ghidra_headless = detect_ghidra_headless(args)
        emit(f"Ghidra headless: {args.ghidra_headless}", args)

    for binary in binaries:
        run_one(binary, args)


if __name__ == "__main__":
    main()