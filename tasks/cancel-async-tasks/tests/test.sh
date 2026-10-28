#!/bin/bash
set -e

mkdir -p /logs/verifier /app

# Ensure test.py is present in /app
if [ -f "/workspace/tests/test.py" ]; then
    cp /workspace/tests/test.py /app/test.py
elif [ -f "/tests/test.py" ]; then
    cp /tests/test.py /app/test.py
fi

TEST_TARGET="/tests/test_outputs.py"
if [ -f "/workspace/tests/test_outputs.py" ]; then
    TEST_TARGET="/workspace/tests/test_outputs.py"
fi

cd /app
if pytest "$TEST_TARGET" -rA; then
    echo 1 > /logs/verifier/reward.txt
    exit 0
else
    echo 0 > /logs/verifier/reward.txt
    exit 1
fi
