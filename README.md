# Lightly-Fake-MCP-for-IDA

Headless binary analysis runner that produces structured text dumps from **IDA Pro** or **Ghidra** — with both outputs using the same section layout so downstream tooling (LLM prompts, grep, parsers) works identically regardless of which disassembler generated the file.

---

## Output format

Every dump — whether from IDA or Ghidra — contains these eight sections in order:

```
# header           arch / compiler / function count
[functions]        address + name for every function
[strings]          defined strings ≥ 4 chars
[imports]          grouped by module / DLL
[exports]          entry-point symbols
[pseudocode]       decompiled C (Hex-Rays / Ghidra decompiler)
[disasm]           full disassembly, all segments, labels + inline comments
[callgraph]        caller → callee edge list
```

Dumps land in `runs/ghidra_dump/` or `runs/ida_dump/`.

---

## Repo layout

```
Lightly-Fake-MCP-for-IDA/
├── main/
│   └── cli.py                     # orchestrator — run this
├── scripts/
│   ├── ida_dump.py                # IDAPython headless export
│   ├── GhidraDump.java            # Ghidra headless export
│   └── DisableWinResRef.java      # Ghidra pre-script (JDK 21 compat fix)
├── samples/                       # demo binaries
│   ├── FLRSCRNSVR.SCR             # Windows PE screensaver
│   └── wallpaper                  # Linux ELF
├── dissembler/
│   ├── ida_linux/
│   │   ├── ida-pro_92_x64linux.run   ← IDA Pro installer (already in repo)
│   │   └── idakeygen_9.2.py          ← keygen / licence patcher
│   └── ida_win/
│       ├── ida-pro_92_x64win.exe
│       └── idakeygen_9.2.py
├── runs/                          # output dumps + logs (git-ignored)
├── Dockerfile                     # IDA Pro + Ghidra image
├── Dockerfile.ghidra              # Ghidra-only image (no IDA licence needed)
├── docker-compose.yml
└── README.md
```

---

## Quick start — Docker (recommended for all platforms)

Docker is the fastest way to get everything running. No local IDA or Ghidra install needed on the host.

### 1. Clone the repo

```bash
git clone https://github.com/lavjre/Lightly-Fake-MCP-for-IDA.git
cd Lightly-Fake-MCP-for-IDA
```

### 2. Build

```bash
# Full image: IDA Pro 9.2 + Ghidra 11  (downloads Ghidra from GitHub at build time)
docker build -t lfm-ida .

# OR: Ghidra-only image  (no IDA licence needed, smaller build)
docker build -f Dockerfile.ghidra -t lfm-ghidra .
```

> **Offline / slow connection?** Download the Ghidra zip manually and pass it as a build arg:
> ```bash
> # Put the zip in the repo root, then:
> docker build \
>   --build-arg GHIDRA_ZIP=ghidra_11.3_PUBLIC_20250219.zip \
>   --build-arg GHIDRA_URL="" \
>   -t lfm-ida .
> ```

### 3. Run

```bash
# ── Ghidra (recommended — no licence needed) ──────────────────────────────

# Analyse the bundled demo binaries
docker run --rm \
    -v $(pwd)/samples:/targets:ro \
    -v $(pwd)/runs:/app/runs \
    lfm-ida  -m /targets/FLRSCRNSVR.SCR /targets/wallpaper  -t ghidra  -v

# Your own binary
docker run --rm \
    -v /path/to/your/bins:/targets:ro \
    -v $(pwd)/runs:/app/runs \
    lfm-ida  -m /targets/mybinary.exe  -t ghidra  -v

# ── IDA Pro ───────────────────────────────────────────────────────────────

docker run --rm \
    -v $(pwd)/samples:/targets:ro \
    -v $(pwd)/runs:/app/runs \
    lfm-ida  -m /targets/FLRSCRNSVR.SCR  -t ida  -v

# ── Ghidra-only image ─────────────────────────────────────────────────────

docker run --rm \
    -v $(pwd)/samples:/targets:ro \
    -v $(pwd)/runs:/app/runs \
    lfm-ghidra  -m /targets/wallpaper  -t ghidra  -v
```

Results appear on your host at:
```
runs/
├── ghidra_dump/
│   ├── FLRSCRNSVR.txt
│   └── wallpaper.txt
└── logs/
    ├── FLRSCRNSVR_ghidra_stdout.log
    └── wallpaper_ghidra_stdout.log
```

### Using Docker Compose

```bash
# Build all images
docker compose build

# Ghidra analysis  (full image)
docker compose run --rm analyze -m /targets/FLRSCRNSVR.SCR -t ghidra -v

# IDA Pro analysis
docker compose run --rm analyze -m /targets/FLRSCRNSVR.SCR -t ida -v

# Ghidra-only image
docker compose run --rm ghidra -m /targets/wallpaper -t ghidra -v

# Override sample directory
TARGETS=/path/to/bins docker compose run --rm analyze -m /targets/sample.exe -t ghidra -v
```

---

## Manual install — Linux

### IDA Pro 9.2

```bash
# Step 1 — run the installer
chmod +x dissembler/ida_linux/ida-pro_92_x64linux.run
./dissembler/ida_linux/ida-pro_92_x64linux.run --mode unattended --prefix /opt/ida

# Step 2 — copy keygen next to the IDA binaries and run it
cp dissembler/ida_linux/idakeygen_9.2.py /opt/ida/
cd /opt/ida
python3 idakeygen_9.2.py

# Step 3 — verify
/opt/ida/idat64 --version
```

### Ghidra 11.x (Linux)

```bash
# Requires Java 21  (NOT JDK 25 — see troubleshooting below)
sudo apt install openjdk-21-jdk          # Debian / Ubuntu
sudo dnf install java-21-openjdk-devel   # Fedora / RHEL
brew install openjdk@21                  # macOS

# Download and install
wget https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_11.3_build/ghidra_11.3_PUBLIC_20250219.zip
unzip ghidra_11.3_PUBLIC_20250219.zip -d /opt
mv /opt/ghidra_11.3_PUBLIC /opt/ghidra
chmod +x /opt/ghidra/support/analyzeHeadless

# Verify
/opt/ghidra/support/analyzeHeadless --help
```

### Run without Docker

```bash
cd Lightly-Fake-MCP-for-IDA

# Ghidra (auto-detects /opt/ghidra)
python3 main/cli.py -m samples/FLRSCRNSVR.SCR samples/wallpaper -t ghidra -v

# IDA  (auto-detects /opt/ida)
python3 main/cli.py -m samples/FLRSCRNSVR.SCR -t ida -v

# Explicit paths
python3 main/cli.py \
    -m samples/FLRSCRNSVR.SCR \
    -t ghidra \
    --ghidra-headless /opt/ghidra/support/analyzeHeadless \
    --ghidra-script   scripts/GhidraDump.java \
    -v
```

---

## Manual install — Windows

### IDA Pro 9.2

1. Run `dissembler\ida_win\ida-pro_92_x64win.exe` → install to e.g. `C:\Program Files\IDA Pro 9.2\`
2. Copy `dissembler\ida_win\idakeygen_9.2.py` into the same folder
3. Open a terminal in that folder: `python idakeygen_9.2.py`
4. `cli.py` will auto-detect IDA via the Windows registry

### Ghidra 11.x (Windows)

1. Install **JDK 21** from [Adoptium](https://adoptium.net/) — do **not** use JDK 25
2. Download `ghidra_11.3_PUBLIC_20250219.zip` from [Ghidra Releases](https://github.com/NationalSecurityAgency/ghidra/releases)
3. Unzip to e.g. `C:\Program Files\Ghidra\`
4. `cli.py` will auto-detect via `%ProgramFiles%\Ghidra*`

### Run on Windows

```powershell
cd Lightly-Fake-MCP-for-IDA

# Ghidra
python main\cli.py -m samples\FLRSCRNSVR.SCR -t ghidra -v

# IDA
python main\cli.py -m samples\FLRSCRNSVR.SCR -t ida -v
```

---

## CLI reference

```
python3 main/cli.py [binaries ...] [options]

Input:
  binaries              positional binary paths
  -m, --multi <files>   one or more binaries (same effect as positional)

Output:
  -d, --out-dir DIR     base output dir  (default: ./runs)
                        ghidra → runs/ghidra_dump/<name>.txt
                        ida    → runs/ida_dump/<name>.txt

Tool:
  -t, --tool ida|ghidra   (default: ida)

Verbosity:
  -v, --verbose         stream disassembler output to terminal live
  -q, --quiet           errors only
  -n, --dry-run         print command, don't run it
  -f, --overwrite       delete previous output for this binary

Size limits:
  --max-size MB         split files larger than this  (default: 75)
  --chunk-size MB       size of each split part       (default: 50)
  --zip-threshold N     zip when part count ≥ N       (default: 7)
  --no-zip              never zip
  --keep-source         keep original after splitting

IDA:
  --ida-path PATH       explicit path to idat64/ida64
  --ida-script PATH     IDAPython script  (default: scripts/ida_dump.py)

Ghidra:
  --ghidra-headless PATH   explicit path to analyzeHeadless
  --ghidra-script PATH     post-script    (default: scripts/GhidraDump.java)
  --ghidra-prescript PATH  pre-script     (default: scripts/DisableWinResRef.java)
  --ghidra-project-root    reuse a Ghidra project dir instead of temp

  --timeout SECONDS     kill disassembler after N seconds
```

### Examples

```bash
# Analyse two binaries with Ghidra, verbose
python3 main/cli.py -m samples/FLRSCRNSVR.SCR samples/wallpaper -t ghidra -v

# IDA, custom output dir, overwrite previous run
python3 main/cli.py -m target.exe -t ida -d /tmp/out -f

# Dry run — print the command without executing
python3 main/cli.py -m target.elf -t ghidra -n -v

# Timeout after 10 minutes
python3 main/cli.py -m big_binary -t ghidra --timeout 600
```

---

## Auto-detection order

`cli.py` searches for tool executables in this priority order:

| Priority | IDA Pro | Ghidra |
|----------|---------|--------|
| 1 | `--ida-path` flag | `--ghidra-headless` flag |
| 2 | `IDA_PATH` env var | `GHIDRA_HEADLESS` env var |
| 3 | `IDA_HOME` env var | `GHIDRA_INSTALL_DIR` env var |
| 4 | Windows registry `HKLM\SOFTWARE\Hex-Rays` | — |
| 5 | `%ProgramFiles%\IDA*` / `/opt/ida*` | `%ProgramFiles%\Ghidra*` / `/opt/ghidra*` |
| 6 | `PATH` | `PATH` |

---

## Troubleshooting

### `UnsupportedClassVersionError: WindowsResourceReference`

Ghidra 12 ships one component compiled for JDK 25. Under JDK 21 it crashes. Fix: delete the cached `.class` file so Ghidra recompiles it from source using your JDK.

```bash
# Linux
find ~/.local/share/ghidra -name "WindowsResourceReference.class" -delete 2>/dev/null
find ~/.config/ghidra      -name "WindowsResourceReference.class" -delete 2>/dev/null

# Windows PowerShell
Remove-Item "$env:APPDATA\ghidra\*\osgi\compiled-bundles\*\WindowsResourceReference.class" `
    -Force -Recurse -ErrorAction SilentlyContinue
```

Run again — Ghidra recompiles automatically and the error disappears permanently.

### `Failed to get OSGi bundle` / scripts don't compile

The Ghidra OSGi build cache is stale. Clear it:

```bash
# Linux
rm -rf ~/.local/share/ghidra/*/osgi/compiled-bundles/
# or
rm -rf ~/.config/ghidra/*/osgi/compiled-bundles/

# Windows PowerShell
Remove-Item -Recurse -Force "$env:APPDATA\ghidra\*\osgi\compiled-bundles"
```

### `Could not locate IDA executable`

```bash
# Option A — explicit flag
python3 main/cli.py -m target -t ida --ida-path /opt/ida/idat64

# Option B — environment variable
export IDA_PATH=/opt/ida/idat64
python3 main/cli.py -m target -t ida
```

### `Could not locate Ghidra analyzeHeadless`

```bash
# Option A
python3 main/cli.py -m target -t ghidra --ghidra-headless /opt/ghidra/support/analyzeHeadless

# Option B
export GHIDRA_INSTALL_DIR=/opt/ghidra
python3 main/cli.py -m target -t ghidra
```

### Output file is empty or in wrong location

Run with `-v` and look for the `[GhidraDump] Output:` line — it shows exactly where the file was written.

```bash
python3 main/cli.py -m samples/wallpaper -t ghidra -v 2>&1 | grep -i "output\|complete\|failed"
```

---

## Licence

Runner / helper scripts: MIT.  
IDA Pro and Ghidra are subject to their own respective licences.
