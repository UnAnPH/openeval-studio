# Builds the container the agent runs in.
#
# Two stages on purpose: the agent must only ever see the *stripped binary*
# extractor. The C source and the compiler live in the build stage and never
# reach /app — the .archive format has to be reverse-engineered from
# /app/extract, so leaking the source or symbols would hand it over.

# ---- build stage: compile + strip the reference extractor ----
FROM ubuntu:24.04 AS build
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*
COPY src/extract.c /tmp/extract.c
RUN gcc -O3 -funroll-loops -fomit-frame-pointer -fno-asynchronous-unwind-tables -fno-unwind-tables -static -o /tmp/extract /tmp/extract.c && strip /tmp/extract
# ---- runtime stage: what the agent actually gets ----
FROM ubuntu:24.04
WORKDIR /app

RUN apt-get update && apt-get install -y \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# The reference extractor: stripped, no source, no symbols.
COPY --from=build /tmp/extract /app/extract


