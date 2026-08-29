#!/bin/bash
# Verifier bootstrap. uv and the editable workspace env are baked into the image
# (see environment/Dockerfile), so we don't reinstall the runtime chain here.
# We run pytest *inside* the common.ai project environment so test_outputs.py
# imports the agent's on-disk /app/airflow source, not a PyPI copy. pytest is
# test-only, so it's layered on via --with rather than baked into the image.
#
# No `set -e`: a failing pytest must still fall through to write reward.txt = 0.

mkdir -p /logs/verifier

uv run --project /app/airflow/providers/common/ai --no-sync \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
status=$?

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
