"""Collaborative Annotation & Reactive Metrics Engine for OpenEval & Watcher.

Enables researchers and security reviewers to:
  1. Override automated verdict (PASS <-> FAIL) with audit trails.
  2. Apply inline behavioral tags (@scheming, @eval_awareness, @unfaithful_cot).
  3. Reactively recalculate derived fleet metrics (pass@k, safety pass rate, failure breakdown).
"""

import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.watcher_models import Session
from server.watcher_store import get_watcher_store

logger = logging.getLogger("openeval.server.annotation_service")


class AnnotationPayload(BaseModel):
    """Payload for annotating a session or trajectory turn."""

    model_config = ConfigDict(extra="ignore")

    human_verdict: Literal["PASS", "FAIL"] | None = Field(
        default=None,
        description="Human override of evaluation outcome",
    )
    notes: str | None = Field(default=None, description="Reviewer justification or notes")
    tags: list[str] = Field(default_factory=list, description="Behavioral tags, e.g. @scheming")
    reviewer: str = Field(default="reviewer", description="Reviewer handle or email")
    annotator_id: str | None = Field(
        default=None,
        description="Shared annotator id (alias for reviewer; preferred in collab UI)",
    )

    @model_validator(mode="after")
    def _sync_annotator_id(self) -> "AnnotationPayload":
        if self.annotator_id and (not self.reviewer or self.reviewer == "reviewer"):
            self.reviewer = self.annotator_id
        elif self.reviewer and self.reviewer != "reviewer" and not self.annotator_id:
            self.annotator_id = self.reviewer
        return self


class ReactiveMetrics(BaseModel):
    """Derived fleet-wide safety and performance metrics calculated reactively."""

    model_config = ConfigDict(extra="ignore")

    total_sessions: int
    evaluated_sessions: int
    passed_sessions: int
    failed_sessions: int
    raw_pass_rate: float
    effective_pass_rate: float  # Accounting for human overrides
    human_overrides_count: int
    behavioral_tag_counts: dict[str, int]
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class AnnotationService:
    """Service handling collaborative annotations and reactive metric updates."""

    @classmethod
    def annotate_session(
        cls,
        session_id: str,
        payload: AnnotationPayload,
    ) -> Session | None:
        """Apply human annotation, update audit verdict, and persist."""
        store = get_watcher_store()
        session = store.get_session(session_id)
        if not session:
            return None

        now_iso = datetime.now(UTC).isoformat()

        if payload.human_verdict is not None:
            session.human_verdict_override = payload.human_verdict
            session.passed = payload.human_verdict == "PASS"

        if payload.notes is not None:
            session.human_notes = payload.notes
            session.human_reviewer = payload.reviewer
            session.human_review_notes = payload.notes

        if payload.tags:
            # Store in audit verdicts
            audit_entry = {
                "reviewer": payload.reviewer,
                "timestamp": now_iso,
                "verdict": payload.human_verdict,
                "tags": payload.tags,
                "notes": payload.notes or "",
            }
            session.audit_verdicts.append(audit_entry)
            for tag in payload.tags:
                session.audit_overrides[tag] = True

        session.updated_at = now_iso
        store.record_session(session)
        logger.info(
            "Session '%s' annotated by %s (verdict=%s)",
            session_id,
            payload.reviewer,
            payload.human_verdict,
        )
        return session

    @classmethod
    def compute_reactive_metrics(cls) -> ReactiveMetrics:
        """Recalculate derived metrics dynamically across all recorded sessions."""
        store = get_watcher_store()
        sessions = store.list_sessions()

        total = len(sessions)
        evaluated = 0
        raw_passed = 0
        effective_passed = 0
        failed = 0
        overrides_count = 0
        tag_counts: dict[str, int] = {}

        for s in sessions:
            if s.passed is not None or s.human_verdict_override is not None:
                evaluated += 1

                # Raw baseline pass
                if s.passed is True and s.human_verdict_override is None:
                    raw_passed += 1

                # Effective pass (taking human override if present)
                effective_verdict = s.human_verdict_override or ("PASS" if s.passed else "FAIL")
                if effective_verdict == "PASS":
                    effective_passed += 1
                else:
                    failed += 1

            if s.human_verdict_override is not None:
                overrides_count += 1

            # Count tags
            if hasattr(s, "audit_overrides") and s.audit_overrides:
                for tag in s.audit_overrides:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        raw_rate = round(raw_passed / max(1, evaluated), 4)
        effective_rate = round(effective_passed / max(1, evaluated), 4)

        return ReactiveMetrics(
            total_sessions=total,
            evaluated_sessions=evaluated,
            passed_sessions=effective_passed,
            failed_sessions=failed,
            raw_pass_rate=raw_rate,
            effective_pass_rate=effective_rate,
            human_overrides_count=overrides_count,
            behavioral_tag_counts=tag_counts,
        )
