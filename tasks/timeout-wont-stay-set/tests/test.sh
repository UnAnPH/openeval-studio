#!/bin/bash
set -uo pipefail

# --- Deterministic black hole + verifier deps --------------------------------
# Install tooling, then (best-effort; requires NET_ADMIN) blackhole the
# unreachable TEST-NET range so the verifier's connects deterministically hang
# instead of depending on host egress behavior (which can flake to a fast fail).
apt-get update && apt-get install -y curl iproute2 && rm -rf /var/lib/apt/lists/*
if ip route add blackhole 192.0.2.0/24 2>/dev/null; then
  echo "blackhole route 192.0.2.0/24: installed (deterministic hang)"
else
  echo "blackhole route 192.0.2.0/24: unavailable (no NET_ADMIN) - relying on host drop + self-check retry"
fi

# --- Gray-box probe (Java) ---------------------------------------------------
# Recompile the gateway from source (never trust /app/out), compile the verifier
# against the agent's classes (which enforces the PoolManager public API), then
# run it. The verifier writes its verdict to $RESULT for pytest to read.
RESULT=/tmp/verifier_result.json
APPOUT=/tmp/appout
VEROUT=/tmp/verout
rm -f "$RESULT"
rm -rf "$APPOUT" "$VEROUT"; mkdir -p "$APPOUT" "$VEROUT"

if ! javac -d "$APPOUT" -cp "/app/lib/*" $(find /app/src -name '*.java') 2>/tmp/app-compile.log; then
  printf '{"stage":"app-compile","harnessValid":false}' > "$RESULT"
elif ! javac -d "$VEROUT" -cp "$APPOUT:/app/lib/*" /tests/Verifier.java 2>/tmp/verifier-compile.log; then
  printf '{"stage":"verifier-compile","harnessValid":false}' > "$RESULT"
else
  java -cp "$VEROUT:$APPOUT:/app/lib/*" Verifier "$RESULT" >/tmp/verifier-run.log 2>&1 || true
  [ -s "$RESULT" ] || printf '{"stage":"verifier-run","harnessValid":false}' > "$RESULT"
fi
echo "verifier result: $(cat "$RESULT")"

# --- pytest assertions over the verdict --------------------------------------
curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
source $HOME/.local/bin/env

uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
