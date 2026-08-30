<!--
  RAG Benchmark Task: Incident Investigation
-->

Our production payment infrastructure is experiencing severe outages during traffic spikes with error code `ERR_POOL_STARVATION` and HTTP 504 Gateway Timeouts on `/api/v2/checkout/submit`.

Your task is to investigate the incident documentation stored in `/app/manuals/incident_playbook.md`, identify the root cause and remediation plan, and generate a structured JSON incident report at `/app/incident_report.json`.

### Required Output Schema (`/app/incident_report.json`):
```json
{
  "incident_id": "INC-8041",
  "error_code": "ERR_POOL_STARVATION",
  "affected_service": "payment-orchestrator",
  "playbook_section_id": "PLAYBOOK-SEC-402-CONCURRENCY",
  "root_cause_summary": "<concise technical summary of why pool starvation happens>",
  "remediation_actions": [
    "<action 1>",
    "<action 2>"
  ],
  "verified_citations": [
    "/app/manuals/incident_playbook.md#PLAYBOOK-SEC-402-CONCURRENCY"
  ]
}
```

Ensure all fields are accurately extracted and citations are grounded in the documentation manual.
