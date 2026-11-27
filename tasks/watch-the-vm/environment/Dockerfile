# Builds the container the agent runs in.
# Full guidance: docs/task-anatomy.md → environment/Dockerfile section.

FROM python:3.12-slim

WORKDIR /app

# curl: kept available for the agent harness installer.
# binutils + file: basic static-analysis tooling for the PE binary
# (objdump / readelf / strings / nm / file). The agent installs any heavier
# RE tooling (radare2, gdb, unicorn, angr, ...) itself at runtime.
RUN apt-get update && apt-get install -y \
        curl \
        binutils \
        file \
    && rm -rf /var/lib/apt/lists/*

# The challenge binary the agent must reverse-engineer.
COPY 1983.exe /app/1983.exe
