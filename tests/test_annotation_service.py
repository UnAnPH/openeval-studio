"""Annotation API math: raw vs effective pass rates and audit trails."""

from unittest.mock import MagicMock

import pytest

from schemas.watcher_models import Session
from server.annotation_service import AnnotationPayload, AnnotationService


@pytest.fixture
def mock_store(monkeypatch):
    store = MagicMock()
    monkeypatch.setattr("server.annotation_service.get_watcher_store", lambda: store)
    return store


def test_human_fail_override_preserves_raw_pass_rate_and_audits(mock_store) -> None:
    session = Session(
        session_id="anno-metrics-1",
        project_name="bench",
        status="completed",
        passed=True,
    )
    mock_store.get_session.return_value = session
    mock_store.list_sessions.return_value = [session]

    updated = AnnotationService.annotate_session(
        "anno-metrics-1",
        AnnotationPayload(human_verdict="FAIL", notes="False negative review", tags=[]),
    )
    assert updated is not None
    assert updated.automated_passed is True
    assert updated.passed is False
    assert len(updated.audit_verdicts) == 1

    metrics = AnnotationService.compute_reactive_metrics()
    assert metrics.raw_pass_rate == 1.0
    assert metrics.effective_pass_rate == 0.0
