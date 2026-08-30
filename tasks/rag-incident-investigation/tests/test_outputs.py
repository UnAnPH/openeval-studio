"""Held-out Verifier for RAG Incident Investigation Benchmark."""

import json
from pathlib import Path
import pytest

REPORT_PATH = Path("/app/incident_report.json")


def test_incident_report_exists():
    """Verify the generated report file exists."""
    assert REPORT_PATH.is_file(), f"Expected report file at {REPORT_PATH}"


def test_incident_report_schema_and_facts():
    """Verify structured fields and ground-truth root cause extraction."""
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("incident_id") == "INC-8041"
    assert data.get("error_code") == "ERR_POOL_STARVATION"
    assert data.get("affected_service") == "payment-orchestrator"
    assert data.get("playbook_section_id") == "PLAYBOOK-SEC-402-CONCURRENCY"

    root_cause = data.get("root_cause_summary", "").lower()
    assert "timeout" in root_cause or "pool" in root_cause or "semaphore" in root_cause

    actions = data.get("remediation_actions", [])
    assert isinstance(actions, list) and len(actions) >= 1

    citations = data.get("verified_citations", [])
    assert isinstance(citations, list) and len(citations) >= 1
    assert any("PLAYBOOK-SEC-402-CONCURRENCY" in c or "incident_playbook.md" in c for c in citations)
