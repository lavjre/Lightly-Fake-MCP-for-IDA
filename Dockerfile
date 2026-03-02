# ─────────────────────────────────────────────────────────────────────────────
# Lightly-Fake-MCP-for-IDA  —  Full image  (IDA Pro 9.2 + Ghidra 11)
#
# IDA installer is already in the repo at:
#   dissembler/ida_linux/ida-pro_92_x64linux.run
#   dissembler/ida_linux/idakeygen_9.2.py
#
# Ghidra is downloaded from GitHub at build time (no extra files needed).
# To use a local zip instead, pass build args:
#   docker build \
#     --build-arg GHIDRA_ZIP=ghidra_11.3_PUBLIC_20250219.zip \
#     --build-arg GHIDRA_URL="" \
#     -t lfm-ida .
#   (place the zip in the repo root first)
# ─────────────────────────────────────────────────────────────────────────────

FROM ubuntu:22.04

ARG GHIDRA_VERSION=11.3
ARG GHIDRA_DATE=20250219
ARG GHIDRA_ZIP=""
ARG GHIDRA_URL="https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_DATE}.zip"

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# ── System packages ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Python
    python3 python3-pip \
    # Java 21  (Ghidra requirement)
    # NOTE: do NOT use JDK 25 — breaks WindowsResourceReferenceAnalyzer
    openjdk-21-jdk \
    # IDA Pro Linux runtime libraries
    libglib2.0-0 libsm6 libxrender1 libfontconfig1 libxext6 \
    libxcb1 libx11-xcb1 libc6 libstdc++6 libgcc-s1 libssl3 \
    # Tooling
    unzip wget file ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV PATH="$JAVA_HOME/bin:$PATH"

# ── Install IDA Pro 9.2 ───────────────────────────────────────────────────────
#
# Repo layout:
#   dissembler/ida_linux/ida-pro_92_x64linux.run   ← self-extracting installer
#   dissembler/ida_linux/idakeygen_9.2.py           ← keygen / licence patcher
#
# Steps:
#   1. Run installer in unattended mode  →  /opt/ida
#   2. Copy keygen beside the IDA binaries
#   3. Execute keygen so IDA runs without a licence prompt

COPY dissembler/ida_linux/ida-pro_92_x64linux.run /tmp/ida_installer.run
COPY dissembler/ida_linux/idakeygen_9.2.py         /tmp/idakeygen_9.2.py

RUN chmod +x /tmp/ida_installer.run \
    && /tmp/ida_installer.run --mode unattended --prefix /opt/ida \
    && echo "[IDA] installer finished" \
    && cp /tmp/idakeygen_9.2.py /opt/ida/idakeygen_9.2.py \
    && cd /opt/ida \
    && python3 idakeygen_9.2.py \
    && echo "[IDA] keygen applied" \
    && rm /tmp/ida_installer.run /tmp/idakeygen_9.2.py

ENV IDA_HOME=/opt/ida
ENV PATH="$IDA_HOME:$PATH"

# ── Install Ghidra ────────────────────────────────────────────────────────────
#
# Mode A (default): download from GitHub Releases at build time
# Mode B:           set GHIDRA_ZIP=<filename> and place the zip in repo root

COPY . /build_ctx/

RUN if [ -n "$GHIDRA_ZIP" ] && [ -f "/build_ctx/$GHIDRA_ZIP" ]; then \
        echo "[Ghidra] using local zip: $GHIDRA_ZIP" \
        && cp "/build_ctx/$GHIDRA_ZIP" /tmp/ghidra.zip ; \
    else \
        echo "[Ghidra] downloading: $GHIDRA_URL" \
        && wget -q --show-progress "$GHIDRA_URL" -O /tmp/ghidra.zip ; \
    fi \
    && unzip -q /tmp/ghidra.zip -d /opt \
    && mv /opt/ghidra_* /opt/ghidra \
    && chmod +x /opt/ghidra/support/analyzeHeadless \
    && rm /tmp/ghidra.zip \
    && echo "[Ghidra] installed at /opt/ghidra"

ENV GHIDRA_INSTALL_DIR=/opt/ghidra
ENV PATH="$GHIDRA_INSTALL_DIR/support:$PATH"

# ── Application layout ────────────────────────────────────────────────────────
#
# Repo structure inside the image:
#   /app/main/cli.py            ← entrypoint
#   /app/scripts/               ← ida_dump.py  GhidraDump.java  DisableWinResRef.java
#   /app/samples/               ← demo binaries
#   /app/runs/                  ← output root (mount a volume here)

WORKDIR /app

RUN cp -r /build_ctx/main    /app/main    \
    && cp -r /build_ctx/scripts /app/scripts \
    && cp -r /build_ctx/samples /app/samples \
    && mkdir -p /app/runs/ida_dump /app/runs/ghidra_dump /app/runs/logs \
    && rm -rf /build_ctx

# ── Smoke test (at build time) ────────────────────────────────────────────────
RUN python3 --version \
    && java -version \
    && (idat64 --version 2>&1 | head -1 || echo "[warn] idat64 start check skipped") \
    && echo "[+] Image ready"

# ── Entrypoint ────────────────────────────────────────────────────────────────
#
# cli.py lives at  main/cli.py  in the repo.
# Default flags wire up the correct script/output paths inside the container.
#
# Quick run:
#   docker run --rm \
#     -v $(pwd)/samples:/targets:ro \
#     -v $(pwd)/runs:/app/runs \
#     lfm-ida  -m /targets/FLRSCRNSVR.SCR  -t ghidra  -v
#
ENTRYPOINT ["python3", "/app/main/cli.py",
            "--ida-script",       "/app/scripts/ida_dump.py",
            "--ghidra-script",    "/app/scripts/GhidraDump.java",
            "--ghidra-prescript", "/app/scripts/DisableWinResRef.java",
            "--out-dir",          "/app/runs"]
CMD ["--help"]
