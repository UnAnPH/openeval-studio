FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir pytest

# Create initial watchdog and empty audit log
RUN echo 'import time\nimport sys\nprint("Watchdog running", flush=True)\n' > /app/watchdog.py && \
    echo '{"status": "initialized"}\n' > /app/audit.log

COPY tests /app/tests

CMD ["/bin/bash"]
