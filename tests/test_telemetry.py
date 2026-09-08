"""Tests for TelemetryService (Reviewer latency percentiles and precision/recall stats)."""

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
    report = telemetry_svc.get_telemetry_report()

    assert report.overall_latency.count >= 0
    assert report.grader_metrics.precision >= 0.0
    assert report.grader_metrics.precision <= 1.0
    assert report.grader_metrics.recall >= 0.0
    assert report.grader_metrics.f1_score >= 0.0
