#!/bin/bash
python3 /tests/test_outputs.py

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
  exit 0
else
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
