import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from glob import glob
from pathlib import Path
from typing import Iterable, List, Optional


MB = 1024 * 1024
DEFAULT_MAX_SIZE_MB = 75
DEFAULT_CHUNK_MB = 50
DEFAULT_ZIP_THRESHOLD = 7


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless disassembly runner with size capping for ChatGPT-sized uploads."
    )

    parser.add_argument(
        "binaries",
        nargs="*",
        help="Input binaries. Optional when -m/--multi is used.",
    )
    parser.add_argument(
        "-m",
        "--multi",
        nargs="+",
        help="List of binaries to process sequentially (no multithreading).",
    )
    parser.add_argument(
        "-d",
        "--out-dir",
        default="exports",
        help="Base directory for outputs (default: exports).",
    )
    parser.add_argument(
        "-t",
        "--tool",
        choices=["ida", "ghidra"],
        default="ida",
        help="Disassembler to invoke (default: ida).",
    )

    # Unix-style quality-of-life switches
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Silence all non-error output.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show commands without executing them.",
    )
    parser.add_argument(
        "-f",
        "--overwrite",
        action="store_true",
        help="Overwrite existing per-binary output directories.",
    )
    parser.add_argument(
        "--pattern",
        default="*.txt,*.text,*.asm,*.c,*.cg,*.graph,*.json",
        help="Comma-separated glob(s) to enforce size limits on (default: *.txt,*.text,*.asm,*.c,*.cg,*.graph,*.json).",
    )

    # Size controls
    parser.add_argument(
        "--max-size",
        type=int,
        default=DEFAULT_MAX_SIZE_MB,
        help="If a file exceeds this many MB, split it (default: 75).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_MB,
        help="Target chunk size in MB when splitting (default: 50).",
    )
    parser.add_argument(
        "--zip-threshold",
        type=int,
        default=DEFAULT_ZIP_THRESHOLD,
        help="Zip split parts when their count reaches this number (default: 7).",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Never zip split parts.",
    )
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Keep the original oversized file after splitting/zipping.",
    )

    # IDA knobs
    parser.add_argument(
        "--ida-path",
        default=None,
        help="Path to ida64/idat64 executable. If omitted, auto-detect.",
    )
    parser.add_argument(
        "--ida-script",
        default="scripts/ida_dump.py",
        help="IDAPython script to run via -S (default: scripts/ida_dump.py).",
    )

    # Ghidra knobs
    parser.add_argument(
        "--ghidra-headless",
        default=None,
        help="Path to Ghidra analyzeHeadless launcher. If omitted, auto-detect.",
    )
    parser.add_argument(
        "--ghidra-script",
        default="scripts/GhidraDump.java",
        help="Post-script passed to -postScript (default: scripts/GhidraDump.java).",
    )
    parser.add_argument(
        "--ghidra-project-root",
        default=None,
        help="Optional directory to store Ghidra projects; defaults to a temp dir per run.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Seconds before killing a disassembler run (default: no timeout).",
    )

    return parser.parse_args()


def emit(msg: str, args: argparse.Namespace, level: str = "info") -> None:
    if args.quiet:
        return
    if level == "error":
        print(f"[!] {msg}", file=sys.stderr)
        return
    if args.verbose:
        print(f"[{level}] {msg}")
        return
    if level in ("info", "warn"):
        print(msg)


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


def ensure_output_dir(base: Path, overwrite: bool) -> None:
    if base.exists():
        if overwrite:
            shutil.rmtree(base)
            base.mkdir(parents=True, exist_ok=True)
        else:
            base.mkdir(parents=True, exist_ok=True)
    else:
        base.mkdir(parents=True, exist_ok=True)


def build_ida_command(binary: Path, out_dir: Path, args: argparse.Namespace) -> List[str]:
    script_path = Path(args.ida_script).expanduser().resolve()
    log_path = out_dir / "ida_run.log"
    # IDA expects script args inline with -S.
    script_arg = f"{script_path} --out \"{out_dir}\""
    return [
        args.ida_path,
        "-A",
        f"-L{log_path}",
        f"-S{script_arg}",
        str(binary),
    ]


def build_ghidra_command(binary: Path, out_dir: Path, args: argparse.Namespace, proj_root: Path) -> List[str]:
    script_path = Path(args.ghidra_script).expanduser().resolve()
    script_dir = str(script_path.parent)
    script_name = script_path.name
    proj_name = binary.stem
    proj_dir = proj_root / proj_name
    proj_dir.mkdir(parents=True, exist_ok=True)

    # NOTE: We use -preScript instead of -analysisOptions to disable the
    # Java-25-only analyzer. Reason: -analysisOptions "Windows Resource References.enabled=false"
    # contains spaces. analyzeHeadless.bat forwards args via %* without quoting,
    # so the space causes Ghidra to misparse the entire command line.
    # A prescript that calls mgr.setAnalyzerEnabled() has no quoting issues.

    return [
        args.ghidra_headless,
        str(proj_dir.parent),
        proj_name,
        "-import", str(binary),
        "-scriptPath", script_dir,
        "-preScript", "DisableWinResRef.java",   # disables Java-25 analyzer before analysis
        "-postScript", script_name,
        f"out={out_dir}",
    ]


def split_file(path: Path, chunk_bytes: int) -> List[Path]:
    parts: List[Path] = []
    with path.open("rb") as src:
        idx = 0
        while True:
            buf = src.read(chunk_bytes)
            if not buf:
                break
            part = path.with_suffix(path.suffix + f".part{idx:03d}")
            with part.open("wb") as dst:
                dst.write(buf)
            parts.append(part)
            idx += 1
    return parts


def zip_parts(parts: Iterable[Path], dest: Path) -> Path:
    import zipfile

    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in parts:
            zf.write(p, arcname=p.name)
    return dest


def enforce_limits(path: Path, args: argparse.Namespace) -> List[Path]:
    max_bytes = args.max_size * MB
    chunk_bytes = args.chunk_size * MB
    produced: List[Path] = []

    if not path.is_file():
        return produced

    size = path.stat().st_size
    if size <= max_bytes:
        produced.append(path)
        return produced

    parts = split_file(path, chunk_bytes)
    if not args.keep_source:
        path.unlink(missing_ok=True)

    if len(parts) >= args.zip_threshold and not args.no_zip:
        zip_path = path.with_suffix(".zip")
        zip_parts(parts, zip_path)
        produced.append(zip_path)
        if not args.keep_source:
            for p in parts:
                p.unlink(missing_ok=True)
        if zip_path.stat().st_size > max_bytes:
            produced.extend(split_file(zip_path, chunk_bytes))
            if not args.keep_source:
                zip_path.unlink(missing_ok=True)
    else:
        produced.extend(parts)

    return produced


def enforce_for_patterns(base: Path, args: argparse.Namespace) -> None:
    patterns = [p.strip() for p in args.pattern.split(",") if p.strip()]
    seen = set()
    for pat in patterns:
        for file in base.rglob(pat):
            if file in seen:
                continue
            seen.add(file)
            enforce_limits(file, args)


def detect_ida_path(args: argparse.Namespace) -> str:
    candidates: List[str] = []

    # 1. User-provided (highest priority)
    if args.ida_path:
        candidates.append(args.ida_path)

    # 2. Windows registry — enumerate all Hex-Rays subkeys
    if sys.platform == "win32":
        try:
            import winreg
            reg_base = r"SOFTWARE\Hex-Rays"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_base) as hk:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(hk, i)
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_base + "\\" + sub) as sk:
                            try:
                                install_dir, _ = winreg.QueryValueEx(sk, "InstallPath")
                                for exe in ("ida64.exe", "idat64.exe", "ida.exe", "idat.exe"):
                                    candidates.append(str(Path(install_dir) / exe))
                            except FileNotFoundError:
                                pass
                        i += 1
                    except OSError:
                        break
        except Exception:
            pass

    # 3. Environment variables
    if "IDA_PATH" in os.environ:
        candidates.append(os.environ["IDA_PATH"])
    if "IDA_HOME" in os.environ:
        for exe in ("ida64.exe", "idat64.exe", "ida.exe", "idat.exe"):
            candidates.append(str(Path(os.environ["IDA_HOME"]) / exe))

    # 4. Common install dirs — scan with Path.glob (reliable on Windows)
    if sys.platform == "win32":
        search_roots = list(dict.fromkeys(filter(None, [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ])))
        exe_names = ["ida64.exe", "idat64.exe", "ida.exe", "idat.exe"]
        for root in search_roots:
            rp = Path(root)
            if not rp.exists():
                continue
            for subdir in rp.glob("IDA*"):
                if subdir.is_dir():
                    for exe in exe_names:
                        p = subdir / exe
                        if p.exists():
                            candidates.append(str(p))
            for subdir in rp.glob("Hex-Rays/IDA*"):
                if subdir.is_dir():
                    for exe in exe_names:
                        p = subdir / exe
                        if p.exists():
                            candidates.append(str(p))
        # Also check drive root shallowly (e.g. C:/IDA/, D:/tools/IDA/)
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        for subdir in drive.glob("IDA*"):
            if subdir.is_dir():
                for exe in exe_names:
                    p = subdir / exe
                    if p.exists():
                        candidates.append(str(p))
        for subdir in drive.glob("*/IDA*"):
            if subdir.is_dir():
                for exe in exe_names:
                    p = subdir / exe
                    if p.exists():
                        candidates.append(str(p))
    else:
        for prefix in ("/usr/local", "/usr", str(Path.home() / ".local")):
            for name in ("ida64", "idat64"):
                candidates.append(str(Path(prefix) / "bin" / name))
        for base_str in ("/opt", str(Path.home())):
            bp = Path(base_str)
            if bp.exists():
                for pat in ("IDA*/ida64", "IDA*/idat64", "ida*/ida64", "ida*/idat64"):
                    candidates.extend(str(p) for p in bp.glob(pat))

    # 5. PATH lookup (lower priority than known install roots)
    candidates.extend(["idat64", "ida64", "idat", "ida"])

    # 6. Resolve and validate — stop at first hit; log everything for --verbose
    tried: List[str] = []
    for cand in dict.fromkeys(c for c in candidates if c):
        if os.path.isabs(cand) or os.path.sep in cand:
            tried.append(cand)
            if Path(cand).is_file():
                return cand
        else:
            tried.append(f"{cand} (via PATH)")
            found = shutil.which(cand)
            if found and Path(found).is_file():
                return str(Path(found))

    if args.verbose:
        emit(
            "IDA autodetect tried " + str(len(tried)) + " candidates:\n  " + "\n  ".join(tried) +
            "\n  Tip: use --ida-path \"C:\\path\\to\\ida64.exe\" to specify manually.",
            args, level="warn"
        )
    raise SystemExit("Could not locate IDA executable. Provide --ida-path or set IDA_PATH.")


def detect_ghidra_headless(args: argparse.Namespace) -> str:
    candidates: List[str] = []
    tried: List[str] = []

    # 1. User-provided (highest priority)
    if args.ghidra_headless:
        candidates.append(args.ghidra_headless)

    # 2. Environment variables
    if "GHIDRA_HEADLESS" in os.environ:
        candidates.append(os.environ["GHIDRA_HEADLESS"])
    if "GHIDRA_INSTALL_DIR" in os.environ:
        ghidra_base = Path(os.environ["GHIDRA_INSTALL_DIR"])
        candidates.append(str(ghidra_base / "support" / "analyzeHeadless"))
        candidates.append(str(ghidra_base / "support" / "analyzeHeadless.bat"))

    # 3. PATH lookup
    candidates.append("analyzeHeadless")

    # 4. Platform-specific install locations
    if sys.platform == "win32":
        program_dirs = list(dict.fromkeys(filter(None, [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ])))
        for base in program_dirs:
            for pat in ["ghidra*\\support\\analyzeHeadless.bat",
                        "ghidra*\\support\\analyzeHeadless",
                        "Ghidra*\\support\\analyzeHeadless.bat",
                        "Ghidra*\\support\\analyzeHeadless"]:
                candidates.extend(glob(os.path.join(base, pat)))
    else:
        for prefix in ("/usr/local", "/usr", str(Path.home() / ".local")):
            candidates.append(str(Path(prefix) / "bin" / "analyzeHeadless"))
        for base in ("/opt", str(Path.home())):
            for pat in ("ghidra*/support/analyzeHeadless", "Ghidra*/support/analyzeHeadless"):
                if Path(base).exists():
                    candidates.extend(str(p) for p in Path(base).glob(pat))

    # 5. Resolve and validate — stop at first hit
    for cand in dict.fromkeys(c for c in candidates if c):
        if os.path.isabs(cand) or os.path.sep in cand:
            tried.append(cand)
            if Path(cand).is_file():
                return cand
        else:
            tried.append(f"{cand} (via PATH)")
            found = shutil.which(cand)
            if found and Path(found).is_file():
                return str(Path(found))

    if args.verbose:
        emit("Ghidra autodetect tried " + str(len(tried)) + " candidates:\n  " + "\n  ".join(tried), args, level="warn")
    raise SystemExit("Could not locate Ghidra analyzeHeadless. Provide --ghidra-headless or set GHIDRA_HEADLESS.")


def run_one(binary: Path, args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir).expanduser().resolve() / binary.stem

    temp_proj_root: Optional[Path] = None
    env = os.environ.copy()

    if args.tool == "ghidra":
        if args.ghidra_project_root:
            proj_root = Path(args.ghidra_project_root).expanduser().resolve()
            proj_root.mkdir(parents=True, exist_ok=True)
        else:
            temp_proj_root = Path(tempfile.mkdtemp(prefix="ghidra_proj_"))
            proj_root = temp_proj_root
        cmd = build_ghidra_command(binary, out_dir, args, proj_root)
        env.setdefault("NOPAUSE", "1")
    else:
        cmd = build_ida_command(binary, out_dir, args)

    emit(f"{binary.name} -> {args.tool.upper()} @ {out_dir}", args)
    if args.verbose:
        emit("CMD: " + " ".join(str(c) for c in cmd), args, level="debug")

    if args.dry_run:
        emit("Dry run; command not executed.", args, level="warn")
        return

    ensure_output_dir(out_dir, args.overwrite)
    stdout_path = out_dir / "stdout.log"
    result_returncode: int = 0

    try:
        if args.verbose:
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
                    result_returncode = proc.returncode
                except subprocess.TimeoutExpired:
                    proc.kill()
                    emit(f"{binary.name}: timed out after {args.timeout}s", args, level="error")
                    return
                except KeyboardInterrupt:
                    proc.kill()
                    raise
        else:
            stderr_path = out_dir / "stderr.log"
            with stdout_path.open("w", encoding="utf-8", errors="replace") as out_f, \
                 stderr_path.open("w", encoding="utf-8", errors="replace") as err_f:
                try:
                    result = subprocess.run(
                        cmd, stdout=out_f, stderr=err_f,
                        check=False, timeout=args.timeout, env=env,
                    )
                    result_returncode = result.returncode
                except subprocess.TimeoutExpired:
                    emit(f"{binary.name}: timed out after {args.timeout}s", args, level="error")
                    return
    except KeyboardInterrupt:
        raise

    if result_returncode != 0:
        emit(f"{binary.name}: exited with code {result_returncode}", args, level="error")
        if stdout_path.is_file():
            lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for ln in lines[-30:]:
                emit("  " + ln, args, level="error")
        return

    enforce_for_patterns(out_dir, args)

    if temp_proj_root:
        shutil.rmtree(temp_proj_root, ignore_errors=True)


def main() -> None:
    args = parse_args()
    binaries = collect_binaries(args)
    out_base = Path(args.out_dir).expanduser().resolve()
    out_base.mkdir(parents=True, exist_ok=True)

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