#!/usr/bin/env bash
set -euo pipefail

cat << 'EOF' > /app/incident_report.json
{
  "incident_id": "INC-8041",
  "error_code": "ERR_POOL_STARVATION",
  "affected_service": "payment-orchestrator",
  "playbook_section_id": "PLAYBOOK-SEC-402-CONCURRENCY",
  "root_cause_summary": "Connection timeout inheritance improperly scoped in payment-orchestrator leading to connection pool exhaustion during upstream latency spikes.",
  "remediation_actions": [
    "Configure connect_timeout = 2.0s and read_timeout = 3.5s explicitly on PoolManager",
    "Enforce try/finally handle release around dispatch_settlement_batch()"
  ],
  "verified_citations": [
    "/app/manuals/incident_playbook.md#PLAYBOOK-SEC-402-CONCURRENCY"
  ]
}
EOF

echo "Incident report generated successfully."
