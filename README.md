# Lightly-Fake-MCP-for-IDA
Not enough token, run out of subscription try this :))

## CLI
`python main/cli.py -h` shows switches for running IDA or Ghidra headless and auto-splitting large text dumps for ChatGPT-sized uploads. Paths to IDA/Ghidra are auto-detected (env, PATH, common install dirs on Windows/Linux), or override with `--ida-path` / `--ghidra-headless`.

Outputs (default `--out-dir runs`):
- Dumps: `runs/ida_dump/<binary>.txt` or `runs/ghidra_dump/<binary>.txt`
- Logs:  `runs/log/<binary>.<tool>.stdout.log|stderr.log` and `runs/log/<binary>.ida.log`

Linux notes:
- IDA: prefers `idat64`/`ida64` in `/usr/local/bin`, `/usr/bin`, `/opt/ida*`, `$IDA_PATH`, `$IDA_HOME`
- Ghidra: uses `analyzeHeadless` in `/usr/local/bin`, `/opt/ghidra*/support`, `$GHIDRA_HEADLESS`, or `$GHIDRA_INSTALL_DIR/support/analyzeHeadless`
