"""Reviewer Latency & Grader Precision/Recall Telemetry Service for OpenEval Watcher.

Computes:
1. Reviewer Latency Percentiles (p50, p95, p99) across Triage, Deep Review, and Rule gates.
2. Grader Accuracy Metrics: Precision, Recall, F1 Score, and Confusion Matrix against labeled ground truth.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from server.db import SessionLocal, get_current_user_id
from server.watcher_store import WatcherStore, get_watcher_store


class LatencyPercentiles(BaseModel):
    """Percentile distribution of evaluation latency."""

    model_config = ConfigDict(extra="ignore")

    count: int = Field(default=0)
    p50_ms: float = Field(default=0.0)
    p95_ms: float = Field(default=0.0)
    p99_ms: float = Field(default=0.0)
    mean_ms: float = Field(default=0.0)
    min_ms: float = Field(default=0.0)
    max_ms: float = Field(default=0.0)


class GraderMetrics(BaseModel):
    """Classification performance metrics for Watcher graders."""

    model_config = ConfigDict(extra="ignore")

    total_evaluated: int = Field(default=0)
    blocked_count: int = Field(default=0)
    labeled_count: int = Field(default=0)
    precision: float = Field(default=0.0)
    recall: float = Field(default=0.0)
    f1_score: float = Field(default=0.0)
    true_positives: int = Field(default=0)
    false_positives: int = Field(default=0)
    false_negatives: int = Field(default=0)
    true_negatives: int = Field(default=0)


class TelemetryReport(BaseModel):
    """Fleet-wide performance, reliability, and precision telemetry."""

    model_config = ConfigDict(extra="ignore")

    overall_latency: LatencyPercentiles
    triage_latency: LatencyPercentiles
    deep_review_latency: LatencyPercentiles
    rule_latency: LatencyPercentiles
    grader_metrics: GraderMetrics


def calculate_percentiles(values: list[float]) -> LatencyPercentiles:
    """Compute p50, p95, and p99 percentiles from a series of measurements."""
    if not values:
        return LatencyPercentiles()

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def percentile(p: float) -> float:
        if n == 1:
            return sorted_vals[0]
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        d = k - f
        return round(sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f]), 2)

    return LatencyPercentiles(
        count=n,
        p50_ms=percentile(0.50),
        p95_ms=percentile(0.95),
        p99_ms=percentile(0.99),
        mean_ms=round(sum(sorted_vals) / n, 2),
        min_ms=round(sorted_vals[0], 2),
        max_ms=round(sorted_vals[-1], 2),
    )


class TelemetryService:
    """Analyzes DuckDB review latencies and grader accuracy metrics."""

    def __init__(self, store: WatcherStore | None = None) -> None:
        self.store = store or get_watcher_store()

    def get_telemetry_report(self) -> TelemetryReport:
        """Aggregate review latencies and calculate non-circular gate/audit metrics."""
        override_by_id: dict[str, str | None] = {}
        if self.store:
            try:
                for s in self.store.list_sessions():
                    if s.trajectory and s.trajectory.reviews:
                        for rev in s.trajectory.reviews:
                            if rev.id and rev.human_override in ("allow", "deny"):
                                override_by_id[rev.id] = rev.human_override
            except Exception:  # noqa: BLE001
                pass

        rows_list: list[Any] = []
        try:
            with SessionLocal() as db:
                user_id = get_current_user_id(db)
                fetched = db.execute(
                    text(
                        "SELECT id, stage, latency_ms, decision, score FROM reviews WHERE user_id = :uid"
                    ),
                    {"uid": user_id},
                ).fetchall()
                if not fetched:
                    from server.db import get_user_id_by_slug

                    demo_uid = get_user_id_by_slug(db, "demo")
                    if demo_uid is not None and demo_uid != user_id:
                        fetched = db.execute(
                            text(
                                "SELECT id, stage, latency_ms, decision, score FROM reviews WHERE user_id = :uid"
                            ),
                            {"uid": demo_uid},
                        ).fetchall()
                rows_list = [
                    (r[1], r[2], r[3], r[4], override_by_id.get(str(r[0]))) for r in fetched
                ]
        except Exception:  # noqa: BLE001
            rows_list = []

        if not rows_list and self.store:
            try:
                for s in self.store.list_sessions():
                    if s.trajectory and s.trajectory.reviews:
                        for rev in s.trajectory.reviews:
                            rows_list.append(
                                (
                                    rev.stage,
                                    rev.latency_ms,
                                    rev.decision,
                                    rev.score,
                                    rev.human_override,
                                )
                            )
            except Exception:  # noqa: BLE001
                pass

        all_latencies: list[float] = []
        triage_latencies: list[float] = []
        deep_latencies: list[float] = []
        rule_latencies: list[float] = []

        parsed_rows: list[tuple[bool, int, str | None]] = []
        blocked_count = 0
        labeled_count = 0

        for r in rows_list:
            stage = str(r[0])
            lat = float(r[1]) if r[1] is not None else 0.0
            decision = str(r[2]).lower()
            score = int(r[3]) if r[3] is not None else 1
            human_override = r[4] if len(r) > 4 else None

            all_latencies.append(lat)
            if stage == "triage":
                triage_latencies.append(lat)
            elif stage == "deep_review":
                deep_latencies.append(lat)
            elif stage == "rule":
                rule_latencies.append(lat)

            predicted_positive = decision in ("block", "deny", "ask", "escalate")
            if predicted_positive:
                blocked_count += 1
            if human_override in ("allow", "deny"):
                labeled_count += 1
            parsed_rows.append((predicted_positive, score, human_override))

        tp = 0
        fp = 0
        fn = 0
        tn = 0

        for predicted_positive, score, human_override in parsed_rows:
            if labeled_count > 0:
                if human_override not in ("allow", "deny"):
                    continue
                actual_positive = human_override == "deny"
            else:
                # Without human overrides, compare gate enforcement decision against severity >= 5.
                actual_positive = score >= 5

            if predicted_positive and actual_positive:
                tp += 1
            elif predicted_positive and not actual_positive:
                fp += 1
            elif not predicted_positive and actual_positive:
                fn += 1
            else:
                tn += 1

        total = len(parsed_rows)
        precision = round(tp / (tp + fp), 3) if (tp + fp) > 0 else 0.0
        recall = round(tp / (tp + fn), 3) if (tp + fn) > 0 else 0.0
        f1 = (
            round(2 * (precision * recall) / (precision + recall), 3)
            if (precision + recall) > 0
            else 0.0
        )

        return TelemetryReport(
            overall_latency=calculate_percentiles(all_latencies),
            triage_latency=calculate_percentiles(triage_latencies),
            deep_review_latency=calculate_percentiles(deep_latencies),
            rule_latency=calculate_percentiles(rule_latencies),
            grader_metrics=GraderMetrics(
                total_evaluated=total,
                blocked_count=blocked_count,
                labeled_count=labeled_count,
                precision=precision,
                recall=recall,
                f1_score=f1,
                true_positives=tp,
                false_positives=fp,
                false_negatives=fn,
                true_negatives=tn,
            ),
        )
