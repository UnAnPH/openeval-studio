FROM ubuntu:24.04

WORKDIR /app

RUN apt-get update && apt-get install -y \
        python3 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY src /app/src
COPY bin /app/bin
COPY README.md /app/README.md

RUN chmod +x /app/bin/fetch-file
