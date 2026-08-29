#!/bin/bash
set -euo pipefail

# Oracle for watch-the-vm.
#
# /app/1983.exe validates its input with a control-flow-flattened, mutating-
# register VM; the accepted string is computed at runtime and never stored, so
# it can't be grepped out. We recover it the dynamic way: emulate the binary
# with Unicorn, replicate main's stack/register prologue, run the VM dispatcher,
# and capture the bytes it writes into the expected-input buffer. The emulator
# lives in solve.py (well over the ~20-line inline budget); it reads
# /app/1983.exe and writes the recovered input to /app/flag.txt.

# Unicorn is deliberately not shipped in the agent image (it would telegraph the
# intended method); install it just for the oracle run. allow_internet = true.
python3 -m pip install --quiet unicorn==2.1.4 2>/dev/null \
  || python3 -m pip install --quiet --break-system-packages unicorn==2.1.4

python3 "$(dirname "$0")/solve.py"
