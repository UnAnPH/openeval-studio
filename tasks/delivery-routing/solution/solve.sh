#!/bin/bash
set -e

cd /app

echo "Restoring unbroken C++ reference implementations..."
cp -r /solution/correct_repo/Phase-2/* Phase-2/
cp -r /solution/correct_repo/Phase-3/* Phase-3/

echo "Cleaning up any cross-platform object files..."
rm -f Phase-2/*.o Phase-3/*.o

echo "Compiling native Linux binaries..."
make phase2
make phase3

echo "solve.sh completed. "
