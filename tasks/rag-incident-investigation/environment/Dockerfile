FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    procps \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest pydantic

COPY manuals/ /app/manuals/

CMD ["tail", "-f", "/dev/null"]
