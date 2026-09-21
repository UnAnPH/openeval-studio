#!/usr/bin/env bash
# OpenEval Studio - Cloud Power Switch Wrapper
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/scripts/cloud_switch.py" "$@"
