"""Tests verifying correlation request ID generation, propagation, and echoing."""

import pytest
from fastapi.testclient import TestClient

from server.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/gate/evaluate", "/api/watcher/evaluate"])
def test_evaluate_endpoint_request_id_lifecycle(client: TestClient, path: str) -> None:
    """Fails if evaluate responses omit generated X-Request-ID or fail to echo client-supplied IDs."""
    payload = {
        "tool_name": "bash",
        "arguments": {"cmd": "echo hello"},
        "agent_id": "req-id-test",
        "session_id": "test-req-id-session",
    }

    # 1. Request without X-Request-ID generates and returns a new ID
    resp1 = client.post(path, json=payload)
    assert resp1.status_code == 200
    gen_id = resp1.headers.get("X-Request-ID")
    assert gen_id is not None
    assert len(gen_id) >= 16

    # 2. Request with explicit X-Request-ID echoes that exact ID
    custom_id = "test-custom-request-id-998877"
    resp2 = client.post(path, json=payload, headers={"X-Request-ID": custom_id})
    assert resp2.status_code == 200
    assert resp2.headers.get("X-Request-ID") == custom_id


def test_evaluate_endpoint_logs_gate_decision_with_request_id(client: TestClient) -> None:
    """Fails if evaluate gateway log is missing or omits tool, stage, decision, or request_id."""
    import logging

    from server.log import setup_logger

    setup_logger()

    class RecordCollector(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    collector = RecordCollector()
    gw_logger = logging.getLogger("openeval.policy_gateway")
    orig_level = gw_logger.level
    gw_logger.setLevel(logging.INFO)
    gw_logger.addHandler(collector)
    try:
        custom_id = "test-log-corr-id-123"
        resp = client.post(
            "/api/gate/evaluate",
            json={
                "tool_name": "bash",
                "arguments": {"cmd": "echo hello"},
                "agent_id": "req-id-test",
                "session_id": "test-req-id-log-session",
            },
            headers={"X-Request-ID": custom_id},
        )
        assert resp.status_code == 200

        matching_records = [
            rec
            for rec in collector.records
            if rec.name == "openeval.policy_gateway" and "tool=" in rec.getMessage()
        ]
        assert len(matching_records) >= 1
        rec = matching_records[0]
        msg = rec.getMessage()
        assert "tool=bash" in msg
        assert any(s in msg for s in ("stage=rule", "stage=triage"))
        assert "decision=allow" in msg
        assert "elapsed_ms=" in msg
        assert getattr(rec, "request_id", None) == custom_id
    finally:
        gw_logger.removeHandler(collector)
        gw_logger.setLevel(orig_level)
