#!/bin/bash
set -euo pipefail

# Oracle for enforce-allowed-tables.
#
# The reference implementation lives beside this script (sql.py, sql_validation.py).
# We drop it over the seeded Airflow source in /app/airflow so the query-time
# allowed_tables enforcement is live. Harbor mounts the solution dir at /solution;
# resolving the script's own directory keeps this correct regardless of CWD.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_PKG=/app/airflow/providers/common/ai/src/airflow/providers/common/ai

# Fail loudly if the destination isn't where we expect — otherwise a stray copy
# would leave the seed unmodified and the failure would only surface later.
for dst in "$AI_PKG/toolsets/sql.py" "$AI_PKG/utils/sql_validation.py"; do
    [[ -f "$dst" ]] || { echo "Expected seed file missing: $dst" >&2; exit 1; }
done

cp "$SCRIPT_DIR/sql.py"            "$AI_PKG/toolsets/sql.py"
cp "$SCRIPT_DIR/sql_validation.py" "$AI_PKG/utils/sql_validation.py"
