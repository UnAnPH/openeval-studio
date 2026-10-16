from sqlalchemy import text

from server.db import SessionLocal, get_current_user_id
from server.telemetry import (
    TelemetryService,
    calculate_percentiles,
)
from server.watcher_store import get_watcher_store


def test_calculate_percentiles():
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    res = calculate_percentiles(values)

    assert res.count == 10
    assert res.min_ms == 10.0
    assert res.max_ms == 100.0
    assert res.mean_ms == 55.0
    assert res.p50_ms == 55.0
    assert res.p95_ms > 90.0
    assert res.p99_ms > 95.0


def test_calculate_percentiles_empty():
    res = calculate_percentiles([])
    assert res.count == 0
    assert res.p50_ms == 0.0


def test_telemetry_service_report():
    store = get_watcher_store()
    telemetry_svc = TelemetryService(store=store)

    with SessionLocal() as db:
        user_id = get_current_user_id(db)
        # Clear existing reviews for user to guarantee deterministic telemetry assertion
        db.execute(text("DELETE FROM reviews WHERE user_id = :uid"), {"uid": user_id})
        # Ensure seed session exists
        db.execute(
            text(
                "INSERT INTO sessions (id, user_id, title, status, created_at, updated_at) "
                "VALUES ('seed-sess-1', :uid, 'Seed Session', 'active', NOW(), NOW()) "
                "ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id},
        )
        # Seed 4 deterministic reviews:
        # Row 1: stage="triage", latency_ms=10.0, decision="block", score=6 -> TP
        # Row 2: stage="triage", latency_ms=20.0, decision="block", score=2 -> FP
        # Row 3: stage="deep_review", latency_ms=30.0, decision="allow", score=7 -> FN
        # Row 4: stage="rule", latency_ms=40.0, decision="allow", score=1 -> TN
        seed_sql = text(
            "INSERT INTO reviews (id, user_id, session_id, tool_name, tool_input, stage, latency_ms, decision, score, created_at) "
            "VALUES (:id, :uid, 'seed-sess-1', 'bash', 'ls', :stage, :lat, :dec, :score, NOW())"
        )
        db.execute(seed_sql, {"id": "rev-seed-1", "uid": user_id, "stage": "triage", "lat": 10.0, "dec": "block", "score": 6})
        db.execute(seed_sql, {"id": "rev-seed-2", "uid": user_id, "stage": "triage", "lat": 20.0, "dec": "block", "score": 2})
        db.execute(seed_sql, {"id": "rev-seed-3", "uid": user_id, "stage": "deep_review", "lat": 30.0, "dec": "allow", "score": 7})
        db.execute(seed_sql, {"id": "rev-seed-4", "uid": user_id, "stage": "rule", "lat": 40.0, "dec": "allow", "score": 1})
        db.commit()

    try:
        report = telemetry_svc.get_telemetry_report()

        assert report.overall_latency.count == 4
        assert report.overall_latency.p50_ms == 25.0
        assert report.overall_latency.p95_ms == 38.5
        assert report.overall_latency.mean_ms == 25.0
        assert report.overall_latency.min_ms == 10.0
        assert report.overall_latency.max_ms == 40.0

        assert report.grader_metrics.total_evaluated == 4
        assert report.grader_metrics.true_positives == 2
        assert report.grader_metrics.false_positives == 1
        assert report.grader_metrics.false_negatives == 0
        assert report.grader_metrics.true_negatives == 1
        assert report.grader_metrics.precision == 0.667
        assert report.grader_metrics.recall == 1.0
        assert report.grader_metrics.f1_score == 0.8
    finally:
        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            db.execute(text("DELETE FROM reviews WHERE user_id = :uid AND session_id LIKE 'seed-%'"), {"uid": user_id})
            db.commit()
