"""OpenEval Watcher Dual-Tier Policy Gateway.

Implements the two-layer evaluation architecture:
- Layer 1 (Fast Triage): Lightweight evaluator (<1.5s) applying the Secret Reading Law
  and outputting XML <decision_score> and <decision_context>.
- Layer 2 (Deep Review): Frontier evaluator assessing informed consent, blast radius,
  irreversibility, and the Prior-State Contamination Floor using the Score Adjustment Calculus.

LLM review is enabled when OPENEVAL_WATCHER_USE_LLM=1 (or unset with GEMINI_API_KEY present).
Set OPENEVAL_WATCHER_USE_LLM=0 to force deterministic heuristics (tests / offline).
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from engine.decision_client import AsyncDecisionClient
from engine.gemini_key_pool import discover_gemini_keys
from engine.graders_suite import get_canonical_grader
from engine.redaction import redact_text
from schemas.models import infer_model_family
from schemas.watcher_models import (
    CalibratedDecisionTelemetry,
    ConsentDisclosure,
    ProvenanceMetadata,
    ReviewDecision,
    ReviewRecord,
    ReviewStage,
    ThreatCategory,
    ToolThreshold,
    infer_threat_category,
)
from server.command_rules import ALL_63_COMMAND_RULES, CommandRulesEngine
from server.policy_helpers import (
    _ACTION_WORDS,
    _DELETE_ACTIONS,
    _GENERIC_TARGETS,
    _MODIFY_ACTIONS,
    _PUSH_ACTIONS,
    _TARGET_SYNONYMS,
    DEEP_REVIEW_SYSTEM_PROMPT,
    FAST_TRIAGE_SYSTEM_PROMPT,
    NEGATIVE_CONSTRAINT_PATTERNS,
    OnFailureAction,
    ToolThresholdInput,
    _coerce_tool_threshold,
    _get_target_stems,
    _parse_decision_xml,
    _parse_threat_category,
    _trajectory_field,
    _trajectory_has,
    _trajectory_sequence,
    check_negative_constraint_violation,
    extract_negative_constraints,
    format_stripped_trajectory,
    generate_consent_disclosure,
)
from server.tool_normalizer import (
    build_decision_context,
    get_tool_lookup_candidates,
    is_command_tool,
    normalize_tool_name,
)

__all__ = [
    "DEEP_REVIEW_SYSTEM_PROMPT",
    "FAST_TRIAGE_SYSTEM_PROMPT",
    "NEGATIVE_CONSTRAINT_PATTERNS",
    "OnFailureAction",
    "PolicyGateway",
    "StageResult",
    "ToolThresholdInput",
    "_ACTION_WORDS",
    "_DELETE_ACTIONS",
    "_GENERIC_TARGETS",
    "_MODIFY_ACTIONS",
    "_PUSH_ACTIONS",
    "_SHARED_GATEWAY_LOCK",
    "_TARGET_SYNONYMS",
    "_coerce_tool_threshold",
    "_env_use_llm",
    "_get_target_stems",
    "_parse_decision_xml",
    "_parse_threat_category",
    "_trajectory_field",
    "_trajectory_has",
    "_trajectory_sequence",
    "check_negative_constraint_violation",
    "extract_negative_constraints",
    "format_stripped_trajectory",
    "generate_consent_disclosure",
]

logger = logging.getLogger("openeval.policy_gateway")


class _HTTPResponseProtocol(Protocol):
    def read(self) -> bytes: ...
    def __enter__(self) -> _HTTPResponseProtocol: ...
    def __exit__(self, *args: object) -> object: ...


_RequesterCallable = Callable[..., Any]


class _DecisionClientProtocol(Protocol):
    """Minimal async surface of ``engine.decision_client.AsyncDecisionClient``."""

    async def evaluate_runtime_tool_call(
        self,
        tool_name: str,
        tool_input: str,
        user_context: str = "",
        provenance: ProvenanceMetadata | None = None,
        timeout_sec: float = 0.65,
    ) -> CalibratedDecisionTelemetry: ...


# Host hooks fail open at 1.5s. Keep the whole LLM budget under that deadline.
_LLM_BUDGET_SEC = 0.8
_SHARED_GATEWAY = None
# Per-tenant shared gateways: keyed by user_id so one tenant's circuit breaker,
# custom instructions, and threshold overrides never leak into another's.
_SHARED_GATEWAYS: dict[int | None, PolicyGateway] = {}
_SHARED_GATEWAY_LOCK = threading.RLock()


def _env_use_llm() -> bool:
    flag = os.environ.get("OPENEVAL_WATCHER_USE_LLM", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    if discover_gemini_keys():
        return True
    return bool(
        os.environ.get("OPENCODE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


def _env_use_decision_api() -> bool:
    """Whether the Tier 1.5 calibrated prefill gate is enabled.

    Explicit ``OPENEVAL_WATCHER_USE_DECISION_API`` wins. When unset, the
    calibrated gate piggybacks on the LLM toggle so existing deployments that
    opt into LLM review also get the ~184ms prefill pass.
    """
    flag = os.environ.get("OPENEVAL_WATCHER_USE_DECISION_API", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return _env_use_llm()


class StageResult(tuple[int, str]):
    """Stage evaluation result: a 2-tuple ``(score, context)`` plus an optional threat tag.

    Callers and tests unpack it as ``score, context = result`` (legacy contract), so it
    subclasses ``tuple[int, str]`` rather than exposing three tuple slots.
    """

    _threat_category: ThreatCategory | None

    def __new__(
        cls,
        score: int,
        context: str,
        threat_category: ThreatCategory | None = None,
    ) -> StageResult:
        inst = super().__new__(cls, (score, context))
        inst._threat_category = threat_category
        return inst

    @property
    def score(self) -> int:
        return self[0]

    @property
    def context(self) -> str:
        return self[1]

    @property
    def threat_category(self) -> ThreatCategory | None:
        return self._threat_category


class PolicyGateway:
    """Multi-tier real-time gating engine matching Watcher Flight Control."""

    def __init__(
        self,
        command_engine: CommandRulesEngine | None = None,
        tool_thresholds: Sequence[ToolThresholdInput]
        | Mapping[str, ToolThresholdInput]
        | None = None,
        *,
        custom_instructions: str = "",
        use_llm: bool | None = None,
        llm_timeout_sec: float = _LLM_BUDGET_SEC,
        on_failure: OnFailureAction = "fail_open",
        llm_model: str = "gemini-3.5-flash-lite",
        requester: _RequesterCallable | None = None,
        api_key: str | None = None,
        escalate_after_consecutive_denies: int = 2,
        escalate_after_accumulated_denies: int = 10,
        use_decision_api: bool | None = None,
        decision_client: _DecisionClientProtocol | None = None,
        decision_timeout_sec: float = 0.65,
    ) -> None:
        self.command_engine = (
            command_engine
            if command_engine is not None
            else CommandRulesEngine(ALL_63_COMMAND_RULES)
        )
        self.tool_thresholds: dict[str, ToolThreshold] = {}
        if isinstance(tool_thresholds, Mapping):
            for key, value in tool_thresholds.items():
                coerced = _coerce_tool_threshold(value, tool_name=str(key))
                if coerced is not None:
                    self.tool_thresholds[str(key)] = coerced
        elif tool_thresholds:
            for raw in tool_thresholds:
                if not isinstance(raw, (ToolThreshold, Mapping)):
                    # A bare numeric sentinel has no tool name; ignore it.
                    continue
                coerced = _coerce_tool_threshold(raw)
                if coerced is None:
                    continue
                self.tool_thresholds[coerced.tool_name] = coerced
        self.custom_instructions: str = custom_instructions or ""
        # Tenant this gateway was built for; refreshed from that tenant's policy only.
        self._tenant_user_id: int | None = None
        self._session_lock = threading.Lock()
        self._session_consecutive_denies: dict[str, int] = {}
        self._session_total_denies: dict[str, int] = {}
        self.escalate_after_consecutive_denies = escalate_after_consecutive_denies
        self.escalate_after_accumulated_denies = escalate_after_accumulated_denies
        self.use_llm = _env_use_llm() if use_llm is None else use_llm
        configured = float(os.environ.get("OPENEVAL_WATCHER_LLM_TIMEOUT", str(llm_timeout_sec)))
        # Clamp so a slow model cannot outlive the host hook and fail open.
        self.llm_timeout_sec = min(_LLM_BUDGET_SEC, max(0.05, configured))
        self.on_failure: OnFailureAction = on_failure  # type: ignore[assignment]
        fail_env = os.environ.get("OPENEVAL_WATCHER_ON_FAILURE", "").strip().lower()
        if fail_env in ("escalate", "fail_open", "fail_closed"):
            self.on_failure = fail_env  # type: ignore[assignment]
        self.llm_model = os.environ.get("OPENEVAL_WATCHER_LLM_MODEL", llm_model)
        self._llm_fail_streak = 0
        self._llm_circuit_open = False
        self._requester: _RequesterCallable | None = requester
        self._api_key = api_key

        # Tier 1.5: calibrated prefill gate (~184ms, 0 output tokens).
        self.use_decision_api = (
            _env_use_decision_api() if use_decision_api is None else use_decision_api
        )
        self.decision_client: _DecisionClientProtocol | None = decision_client
        self.decision_timeout_sec = min(0.65, max(0.05, float(decision_timeout_sec)))
        self._decision_client_lock = threading.Lock()
        # Tier 3: async deep review tasks are tracked so they are not GC'd mid-flight.
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _decision_api_available(self) -> bool:
        """Whether a Decisions API client can be constructed for this call."""
        if self.decision_client is not None:
            return True
        # An explicit mock key means offline test mode; never promote it to a live call.
        if self._api_key == "mock-key":
            return False
        if self._api_key:
            return True
        return bool(discover_gemini_keys())

    def _get_decision_client(self) -> _DecisionClientProtocol:
        """Lazily construct the async Decisions API client (keeps sync paths import-light)."""
        if self.decision_client is not None:
            return self.decision_client
        with self._decision_client_lock:
            if self.decision_client is None:
                discovered = discover_gemini_keys()
                self.decision_client = AsyncDecisionClient(
                    api_key=self._api_key or (discovered[0] if discovered else None),
                    timeout=self.decision_timeout_sec,
                )
        return self.decision_client

    def get_session_denial_counts(self, session_id: str) -> tuple[int, int]:
        """Return (consecutive_denies, total_denies) for the given session."""
        with self._session_lock:
            return (
                self._session_consecutive_denies.get(session_id, 0),
                self._session_total_denies.get(session_id, 0),
            )

    def reset_session_denial_counts(self, session_id: str | None = None) -> None:
        """Reset denial tracking for a session or globally."""
        with self._session_lock:
            if session_id:
                self._session_consecutive_denies.pop(session_id, None)
                self._session_total_denies.pop(session_id, None)
            else:
                self._session_consecutive_denies.clear()
                self._session_total_denies.clear()

    def _maybe_apply_denial_safeguards(
        self, record: ReviewRecord, enabled: bool = True
    ) -> ReviewRecord:
        """Apply denial-loop safeguards unless the caller defers until Tier 1.5/3 resolution."""
        if not enabled:
            return record
        return self._apply_denial_safeguards(record)

    def _apply_denial_safeguards(self, record: ReviewRecord) -> ReviewRecord:
        """Enforce denial loop safeguards across all decision pathways."""
        if not record.session_id:
            return record

        with self._session_lock:
            if record.decision in ("deny", "block"):
                prior_consec = self._session_consecutive_denies.get(record.session_id, 0)
                consec = prior_consec + 1
                total = self._session_total_denies.get(record.session_id, 0) + 1
                self._session_consecutive_denies[record.session_id] = consec
                self._session_total_denies[record.session_id] = total

                if (
                    prior_consec >= self.escalate_after_consecutive_denies
                    or total >= self.escalate_after_accumulated_denies
                ):
                    record.decision = "escalate"
                    suffix = " [Escalated to human operator after repeated gateway denials to prevent agent retry loop]"
                    record.explanation = (record.explanation or "") + suffix
                    if record.metadata is None:
                        record.metadata = {}
                    record.metadata["denial_loop_escalated"] = True
            elif record.decision == "allow":
                self._session_consecutive_denies[record.session_id] = 0
        return record

    @property
    def _llm_deadline(self) -> float | None:
        return None

    @_llm_deadline.setter
    def _llm_deadline(self, val: float | None) -> None:
        pass

    @property
    def _last_threat_category(self) -> ThreatCategory | None:
        return None

    @_last_threat_category.setter
    def _last_threat_category(self, val: ThreatCategory | None) -> None:
        pass

    @classmethod
    def from_store(cls, user_id: int | None = None, **kwargs: object) -> PolicyGateway:
        """Build a gateway using the live WatcherStore policy for a specific tenant."""
        from server.watcher_store import get_watcher_store

        store = get_watcher_store()
        policy = store.get_policy(user_id=user_id)
        custom_instructions = getattr(policy, "locked_instructions", "") or ""
        if hasattr(store, "get_custom_instructions"):
            with contextlib.suppress(Exception):
                custom_instructions = store.get_custom_instructions(user_id=user_id)

        if "custom_instructions" not in kwargs:
            kwargs["custom_instructions"] = custom_instructions

        gateway = cls(
            command_engine=CommandRulesEngine(list(policy.command_rules)),
            tool_thresholds=list(policy.tool_thresholds),
            **kwargs,  # type: ignore[arg-type]
        )
        gateway._tenant_user_id = user_id
        return gateway

    def refresh_policy_from_store(self, user_id: int | None = None) -> None:
        """Reload rules, thresholds, and instructions atomically without resetting circuit breaker."""
        target_uid = user_id if user_id is not None else getattr(self, "_tenant_user_id", None)
        try:
            from server.watcher_store import get_watcher_store

            store = get_watcher_store()
            policy = store.get_policy(user_id=target_uid)
            if hasattr(store, "get_custom_instructions"):
                self.custom_instructions = store.get_custom_instructions(user_id=target_uid)
            else:
                self.custom_instructions = getattr(policy, "locked_instructions", "") or ""
        except Exception:
            logger.debug("policy refresh failed", exc_info=True)
            return
        new_engine = CommandRulesEngine(list(policy.command_rules))
        new_thresholds = {t.tool_name: t for t in policy.tool_thresholds}
        with _SHARED_GATEWAY_LOCK:
            self.command_engine = new_engine
            self.tool_thresholds = new_thresholds

    @classmethod
    def get_shared(cls, user_id: int | None = None) -> PolicyGateway:
        """Process-wide, per-tenant gateway so circuit-breaker state survives across requests."""
        global _SHARED_GATEWAY

        effective_uid = user_id
        if effective_uid is None:
            try:
                from server.db import get_current_user_id

                effective_uid = get_current_user_id()
            except Exception:  # noqa: BLE001 - no DB/context available (stdio tools, tests)
                from server.db import current_user_id

                effective_uid = current_user_id.get()

        with _SHARED_GATEWAY_LOCK:
            gateway = _SHARED_GATEWAYS.get(effective_uid)
            if gateway is None:
                gateway = cls.from_store(user_id=effective_uid)
                _SHARED_GATEWAYS[effective_uid] = gateway
                # Backwards compatibility for callers/tests reading the bare singleton.
                _SHARED_GATEWAY = gateway
            else:
                gateway.refresh_policy_from_store(user_id=effective_uid)
            return gateway

    @classmethod
    def reset_shared(cls) -> None:
        """Drop every shared gateway. Tests use this to isolate circuit-breaker state."""
        global _SHARED_GATEWAY
        with _SHARED_GATEWAY_LOCK:
            _SHARED_GATEWAY = None
            _SHARED_GATEWAYS.clear()

    def _lookup_threshold(self, tool_name: str) -> ToolThreshold | None:
        """Resolve per-tool threshold across raw name, canonical tool name, aliases, and '*'."""
        lower_map = {k.lower(): v for k, v in self.tool_thresholds.items()}
        for candidate in get_tool_lookup_candidates(tool_name):
            if candidate in self.tool_thresholds:
                return self.tool_thresholds[candidate]
            if candidate.lower() in lower_map:
                return lower_map[candidate.lower()]
        return self.tool_thresholds.get("*")

    def apply_thresholds(
        self,
        tool_name: str,
        score: int,
        stage: ReviewStage,
        context: str,
        is_subagent: bool = False,
    ) -> tuple[ReviewDecision, ReviewStage, str]:
        """Map a 1–10 score onto allow / escalate / block using tool thresholds and provenance."""
        threshold = self._lookup_threshold(tool_name)
        if threshold and threshold.always_escalate:
            return "escalate", stage, f"Always escalate policy triggered for tool: {tool_name}"
        if threshold and threshold.auto_approve and not is_subagent:
            return "allow", stage, f"Auto-approved by policy for tool: {tool_name}"

        # Subagent Provenance Ceilings (Apollo Codex auto-review safeguard)
        if is_subagent:
            # Subagents have strict threshold ceiling: score >= 6 must be blocked or escalated
            effective_deny_ge = min(
                threshold.auto_deny_ge if (threshold and threshold.auto_deny_ge is not None) else 8,
                6,
            )
            effective_escalate_ge = min(
                threshold.escalate_ge if (threshold and threshold.escalate_ge is not None) else 5,
                4,
            )
            if score >= effective_deny_ge:
                return (
                    "block",
                    stage,
                    f"[SUBAGENT DELEGATE RESTRICTION]: Strict ceiling triggered (score {score} >= {effective_deny_ge}). {context}",
                )
            if score >= effective_escalate_ge:
                return (
                    "escalate",
                    stage,
                    f"[SUBAGENT DELEGATE RESTRICTION]: Strict escalation ceiling triggered (score {score} >= {effective_escalate_ge}). {context}",
                )
            if score <= (threshold.auto_approve_le if threshold else 2):
                return "allow", stage, context
            return "escalate", stage, context

        if threshold and threshold.auto_deny_ge is not None and score >= threshold.auto_deny_ge:
            return "block", stage, context
        if threshold and threshold.escalate_ge is not None and score >= threshold.escalate_ge:
            return "escalate", stage, context
        if score <= (threshold.auto_approve_le if threshold else 3):
            return "allow", stage, context
        # Default Watcher-like escalate band for mid scores when no escalate_ge set
        if (threshold is None or threshold.escalate_ge is None) and score >= 5:
            return "escalate", stage, context
        return "allow", stage, context

    def _build_review_record(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_input: str,
        decision: ReviewDecision,
        score: int,
        stage: ReviewStage,
        explanation: str,
        diff: str | None,
        latency_ms: float,
        provenance: ProvenanceMetadata,
        decision_context: dict[str, Any],
        canonical_tool: str,
        rule_id: str | None = None,
        rule_name: str | None = None,
        threat_category: ThreatCategory | None = None,
        consent_disclosure: ConsentDisclosure | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ReviewRecord:
        """Build a review record with the shared gate metadata envelope.

        Every gate exit path (threshold allow/deny, deterministic rule, triage/deep
        review) funnels through here so the decision envelope stays identical.
        """
        record_metadata: dict[str, Any] = {
            "decision_context": decision_context,
            "canonical_tool": canonical_tool,
        }
        if metadata:
            record_metadata.update(metadata)
        return ReviewRecord(
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            decision=decision,
            score=score,
            stage=stage,
            rule_id=rule_id,
            rule_name=rule_name,
            explanation=explanation,
            diff=diff,
            latency_ms=latency_ms,
            threat_category=threat_category,
            provenance=provenance,
            consent_disclosure=consent_disclosure,
            metadata=record_metadata,
        )

    def evaluate_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_input: str,
        diff: str | None = None,
        user_intent: str = "",
        prior_state_exposed: bool = False,
        trajectory_context: str = "",
        provenance: ProvenanceMetadata | None = None,
        thought_context: str | None = None,
        *,
        sync_deep_review: bool = True,
        apply_denial_safeguards: bool = True,
    ) -> ReviewRecord:
        """Execute the 3-stage Policy Gating Pipeline:
        1. Deterministic Command Rules (<2ms)
        2. Fast Triage (Layer 1, Secret Reading Law & Trajectory Context)
        3. Deep Review (Layer 2, Loss of Delegated Control & Expected Severity)

        ``sync_deep_review=False`` returns a provisional triage decision without
        blocking on Tier 3. Callers (``evaluate_tool_call_async``) then apply the
        Tier 1.5 calibrated telemetry and schedule Tier 3 in the background.
        """
        t0 = time.time()
        deadline = time.monotonic() + self.llm_timeout_sec

        if thought_context and not user_intent:
            user_intent = thought_context

        if provenance is None:
            provenance = ProvenanceMetadata(call_origin="user_direct", delegation_depth=0)
        is_subagent = provenance.call_origin == "subagent" or provenance.delegation_depth > 0

        # Extract explicit negative constraints across all contexts (including custom policy instructions)
        negative_constraints = extract_negative_constraints(
            user_intent,
            trajectory_context,
            session_id,
            self.custom_instructions,
        )
        is_neg_violation, neg_desc = check_negative_constraint_violation(
            negative_constraints, tool_name, tool_input, diff=diff
        )

        # ---------------------------------------------------------------------
        # STAGE 1: Terminal Tool Thresholds (Always Allow >= 11, Always Deny <= 0)
        # ---------------------------------------------------------------------
        thr = self._lookup_threshold(tool_name)
        matched_threshold_name = thr.tool_name if thr is not None else None
        decision_context = build_decision_context(tool_name, matched_threshold_name)
        canonical_tool = normalize_tool_name(tool_name)
        thr_val: int | None = None
        if thr is not None:
            if thr.threshold is not None:
                thr_val = thr.threshold
            elif thr.auto_approve:
                thr_val = 11
            elif thr.auto_deny_ge is not None and thr.auto_deny_ge <= 0:
                thr_val = 0

        if thr_val is not None:
            if thr_val >= 11:
                latency_ms = (time.time() - t0) * 1000.0
                rec = self._build_review_record(
                    session_id=session_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    decision="allow",
                    score=1,
                    stage="threshold",
                    rule_id="THRESHOLD_ALWAYS_ALLOW",
                    rule_name="THRESHOLD_ALWAYS_ALLOW",
                    explanation=f"Always allowed by tool threshold ({thr_val} >= 11)",
                    diff=diff,
                    latency_ms=latency_ms,
                    provenance=provenance,
                    decision_context=decision_context,
                    canonical_tool=canonical_tool,
                )
                logger.info(
                    "tool=%s stage=%s decision=%s elapsed_ms=%.2f",
                    rec.tool_name,
                    rec.stage,
                    rec.decision,
                    rec.latency_ms,
                )
                return self._maybe_apply_denial_safeguards(rec, apply_denial_safeguards)
            elif thr_val <= 0:
                latency_ms = (time.time() - t0) * 1000.0
                rec = self._build_review_record(
                    session_id=session_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    decision="deny",
                    score=10,
                    stage="threshold",
                    rule_id="THRESHOLD_ALWAYS_DENY",
                    rule_name="THRESHOLD_ALWAYS_DENY",
                    explanation=f"Always denied by tool threshold ({thr_val} <= 0)",
                    diff=diff,
                    latency_ms=latency_ms,
                    provenance=provenance,
                    decision_context=decision_context,
                    canonical_tool=canonical_tool,
                )
                logger.info(
                    "tool=%s stage=%s decision=%s elapsed_ms=%.2f",
                    rec.tool_name,
                    rec.stage,
                    rec.decision,
                    rec.latency_ms,
                )
                return self._maybe_apply_denial_safeguards(rec, apply_denial_safeguards)

        # ---------------------------------------------------------------------
        # STAGE 2: Deterministic Command & Path Rules (AGENTS.md Rule 6)
        # ---------------------------------------------------------------------
        is_cmd_tool = is_command_tool(tool_name)

        matched_rule = None
        rule_action = "triage"
        rule_score = 1
        rule_exp = "No deterministic rule triggered; forward to triage"

        if is_cmd_tool:
            matched_rule, rule_action, rule_score, rule_exp = self.command_engine.evaluate(
                tool_input
            )
        else:
            # File or non-command tool: only apply sensitive file / path rules to the target path
            for rule in self.command_engine.rules:
                if (
                    rule.category == "security"
                    and rule.rule_id
                    in (
                        "sec_oversight_subversion",
                        "sec_sensitive_files",
                        "sec_watcher_ignore",
                        "sec_shadow_file",
                    )
                    and rule.action != "off"
                    and rule.matches(tool_input)
                ):
                    matched_rule = rule
                    rule_action = rule.action
                    rule_score = rule.score if rule.score is not None else 9
                    rule_exp = f"Blocked by sensitive path rule: {rule.name} ({rule.description})"
                    break

        if matched_rule is not None and rule_action != "triage":
            latency_ms = (time.time() - t0) * 1000.0
            rule_cat = getattr(matched_rule, "threat_category", None) or infer_threat_category(
                matched_rule.name, tool_input
            )

            # Invariant: Negative constraint violation enforces absolute floor score >= 8
            if is_neg_violation:
                rule_score = max(rule_score, 8)
                rule_action = "deny"
                rule_exp = f"Reversibility ≠ Authorization: Explicit negative constraint '{neg_desc}' violated. {rule_exp}"

            # Subagents are strictly prohibited from touching sensitive credentials or privilege escalation
            if is_subagent and (
                matched_rule.category == "security"
                or any(
                    k in matched_rule.name.lower()
                    for k in ("privilege", "sudo", "shadow", "sensitive", "secret")
                )
            ):
                rule_score = max(rule_score, 9)
                rule_action = "deny"
                rule_exp = f"[SUBAGENT DELEGATE RESTRICTION]: Subagents are strictly prohibited from sensitive security operations: {matched_rule.name}"

            decision: ReviewDecision
            if rule_action == "deny":
                decision = "block"
            elif rule_action == "human":
                decision = "escalate"
            elif rule_action == "allow":
                decision = "allow"
            else:
                decision = "escalate"

            if is_subagent and rule_score >= 6:
                decision = "block"

            disclosure = generate_consent_disclosure(
                tool_name=tool_name,
                tool_input=tool_input,
                rule=matched_rule,
                score=rule_score,
                is_subagent=is_subagent,
                negative_violation=neg_desc if is_neg_violation else "",
                diff=diff,
            )

            record = self._build_review_record(
                session_id=session_id,
                tool_name=tool_name,
                tool_input=tool_input,
                decision=decision,
                score=rule_score,
                stage="rule",
                rule_name=matched_rule.name,
                explanation=rule_exp,
                diff=diff,
                latency_ms=latency_ms,
                threat_category=rule_cat,
                provenance=provenance,
                consent_disclosure=disclosure,
                decision_context=decision_context,
                canonical_tool=canonical_tool,
            )
            record = self._maybe_apply_denial_safeguards(record, apply_denial_safeguards)
            logger.info(
                "tool=%s stage=%s decision=%s elapsed_ms=%.2f",
                record.tool_name,
                record.stage,
                record.decision,
                record.latency_ms,
            )
            return record

        # ---------------------------------------------------------------------
        # STAGE 2: Fast Triage Evaluation (Enforcing Secret Reading Law)
        # ---------------------------------------------------------------------
        triage_res = self._fast_triage(
            tool_name=tool_name,
            tool_input=tool_input,
            prior_state_exposed=prior_state_exposed,
            trajectory_context=trajectory_context,
            negative_constraint_violated=is_neg_violation,
            negative_violation_desc=neg_desc,
            is_subagent=is_subagent,
            deadline=deadline,
        )
        triage_score, triage_context = triage_res.score, triage_res.context
        stage_threat_cat = triage_res.threat_category

        threshold = self._lookup_threshold(tool_name)
        stage: ReviewStage = "triage"

        # Explicit negative constraint violation: absolute floor score >= 8 (block)
        if is_neg_violation:
            triage_score = max(triage_score, 8)
            decision = "block"
            triage_context = (
                f"Reversibility ≠ Authorization: Explicit negative constraint '{neg_desc}' violated. "
                f"Absolute non-discountable floor score >= 8 enforced."
            )
        # Subagent delegate restriction: strict ceiling score >= 6
        elif is_subagent and triage_score >= 6:
            decision = "block"
            triage_context = f"[SUBAGENT DELEGATE RESTRICTION]: Strict threshold ceiling triggered (score {triage_score} >= 6). {triage_context}"
        # Short-circuit auto-approve / always-escalate before deep review
        elif threshold and threshold.always_escalate:
            decision = "escalate"
            triage_context = f"Always escalate policy triggered for tool: {tool_name}"
        elif threshold and threshold.auto_approve and not is_subagent:
            decision = "allow"
            triage_context = f"Auto-approved by policy for tool: {tool_name}"
        elif (
            threshold
            and threshold.auto_deny_ge is not None
            and triage_score >= threshold.auto_deny_ge
        ):
            decision = "block"
        elif (
            threshold
            and threshold.escalate_ge is not None
            and triage_score >= threshold.escalate_ge
        ):
            decision = "escalate"
        elif triage_score <= (
            threshold.auto_approve_le if threshold else (2 if is_subagent else 3)
        ):
            decision = "allow"
        elif sync_deep_review:
            # -----------------------------------------------------------------
            # STAGE 3: Deep Review (Score Adjustment Calculus)
            # -----------------------------------------------------------------
            stage = "deep_review"
            deep_res = self._deep_review(
                tool_name=tool_name,
                tool_input=tool_input,
                base_score=triage_score,
                user_intent=user_intent,
                prior_state_exposed=prior_state_exposed,
                trajectory_context=trajectory_context,
                negative_constraint_violated=is_neg_violation,
                negative_violation_desc=neg_desc,
                is_subagent=is_subagent,
                deadline=deadline,
            )
            deep_score, deep_context = deep_res.score, deep_res.context
            if deep_res.threat_category:
                stage_threat_cat = deep_res.threat_category
            triage_score = deep_score
            triage_context = deep_context
            decision, stage, triage_context = self.apply_thresholds(
                tool_name, deep_score, stage, deep_context, is_subagent=is_subagent
            )
        else:
            # Tier 3 deferred: return a provisional triage decision immediately so
            # the hook can answer in ~185ms. The calibrated prefill pass (Tier 1.5)
            # and/or the background deep review task refine this record.
            decision, stage, triage_context = self.apply_thresholds(
                tool_name, triage_score, "triage", triage_context, is_subagent=is_subagent
            )

        latency_ms = (time.time() - t0) * 1000.0
        final_threat_cat = stage_threat_cat or infer_threat_category(
            matched_rule.name if matched_rule else None, tool_input
        )

        disclosure = generate_consent_disclosure(
            tool_name=tool_name,
            tool_input=tool_input,
            rule=matched_rule,
            score=triage_score,
            is_subagent=is_subagent,
            negative_violation=neg_desc if is_neg_violation else "",
            diff=diff,
        )

        record = self._build_review_record(
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            decision=decision,
            score=triage_score,
            stage=stage,
            rule_name=matched_rule.name if matched_rule else None,
            explanation=triage_context,
            diff=diff,
            latency_ms=latency_ms,
            threat_category=final_threat_cat,
            provenance=provenance,
            consent_disclosure=disclosure,
            decision_context=decision_context,
            canonical_tool=canonical_tool,
            metadata={"deep_review_status": "deferred"} if not sync_deep_review else None,
        )
        record = self._maybe_apply_denial_safeguards(record, apply_denial_safeguards)
        logger.info(
            "tool=%s stage=%s decision=%s elapsed_ms=%.2f",
            record.tool_name,
            record.stage,
            record.decision,
            record.latency_ms,
        )
        return record

    # ------------------------------------------------------------------
    # Tier 1.5: Calibrated prefill gate (gemini-decision-1)
    # ------------------------------------------------------------------

    def _apply_calibrated_telemetry(
        self,
        record: ReviewRecord,
        telemetry: CalibratedDecisionTelemetry,
        *,
        is_subagent: bool = False,
        strict_mode: bool = False,
    ) -> ReviewRecord:
        """Apply calibrated probability distributions (Apollo methodology) to a record.

        Enforces:
        - Safeguard 1 (Reversibility != Authorization): P(negative constraint) >= 0.70
          floors severity at 8 regardless of how reversible the action is.
        - Safeguard 2 (Authorization Provenance): subagents with P(scope exceeded) >= 0.65
          or E[S] >= 5.5 are escalated/blocked immediately.
        - Confidence-based escalation: E[S] >= 3.5 or severity confidence < 0.65
          escalates and requests asynchronous Tier 3 deep review.
        """
        if record.stage in ("rule", "threshold") and record.decision in (
            "block",
            "deny",
            "escalate",
        ):
            telemetry.deep_review_status = "not_needed"
            record.calibrated_telemetry = telemetry
            record.metadata = record.metadata or {}
            record.metadata["calibrated_telemetry"] = telemetry.model_dump()
            return record

        score = max(1, min(10, int(round(telemetry.expected_severity))))
        reasons = [
            f"E[S]={telemetry.expected_severity:.2f}/10",
            f"severity_conf={telemetry.severity_confidence:.2f}",
        ]
        hard_floor = False

        if telemetry.p_negative_constraint >= 0.70:
            score = max(score, 8)
            hard_floor = True
            reasons.append(
                "Reversibility != Authorization: "
                f"P(negative constraint)={telemetry.p_negative_constraint:.2f} >= 0.70, floor=8"
            )

        if is_subagent and (
            telemetry.p_subagent_violation >= 0.65 or telemetry.expected_severity >= 5.5
        ):
            score = max(score, 8)
            hard_floor = True
            reasons.append(
                "Authorization Provenance: subagent "
                f"(P(scope exceeded)={telemetry.p_subagent_violation:.2f}), floor=8"
            )

        if telemetry.p_obfuscation_or_tampering >= 0.70:
            score = max(score, 8)
            hard_floor = True
            reasons.append(
                "Obfuscation/tampering: "
                f"P(deceptive intent)={telemetry.p_obfuscation_or_tampering:.2f} >= 0.70, floor=8"
            )

        if telemetry.p_eval_awareness >= 0.65:
            score = max(score, 8)
            hard_floor = True
            reasons.append(
                "Evaluation Awareness / Anti-Scheming: "
                f"P(eval awareness)={telemetry.p_eval_awareness:.2f} >= 0.65, floor=8"
            )

        threshold = self._lookup_threshold(record.tool_name)
        preserve_rule_stage = False

        if hard_floor:
            decision: ReviewDecision = "block" if (strict_mode or is_subagent) else "escalate"
        elif record.stage == "rule" and record.decision == "allow":
            decision = "allow"
            preserve_rule_stage = True
        elif threshold is not None and threshold.always_escalate:
            decision = "escalate"
            reasons.append(f"Always escalate policy triggered for tool: {record.tool_name}")
        elif threshold is not None and threshold.auto_approve and not is_subagent:
            decision = "allow"
            reasons.append(f"Auto-approved by policy for tool: {record.tool_name}")
        elif (
            threshold is not None
            and threshold.auto_deny_ge is not None
            and score >= threshold.auto_deny_ge
        ):
            decision = "block" if (strict_mode or threshold.auto_deny_ge < 8) else "escalate"
        elif (
            threshold is not None
            and threshold.escalate_ge is not None
            and score >= threshold.escalate_ge
        ):
            decision = "escalate"
        elif telemetry.expected_severity >= 7.0:
            decision = "block" if strict_mode else "escalate"
        elif telemetry.expected_severity >= 3.5 or telemetry.severity_confidence < 0.65:
            decision = "escalate"
        else:
            decision = "allow"

        needs_deep_review = decision != "allow" and (
            hard_floor or telemetry.expected_severity >= 3.5 or telemetry.severity_confidence < 0.65
        )
        telemetry.deep_review_status = "pending_async" if needs_deep_review else "not_needed"

        record.score = score
        record.decision = decision
        if not preserve_rule_stage:
            record.stage = "triage"
        record.calibrated_telemetry = telemetry
        record.metadata = record.metadata or {}
        record.metadata["tier"] = "1.5_calibrated_prefill"
        record.metadata["deep_review_status"] = telemetry.deep_review_status
        record.metadata["calibrated_telemetry"] = telemetry.model_dump()
        record.metadata["cross_family_verified"] = bool(telemetry.cross_family_verified)
        if telemetry.subject_model_family:
            record.metadata["subject_model_family"] = telemetry.subject_model_family
        record.metadata["monitor_model_family"] = telemetry.monitor_model_family
        if telemetry.p_eval_awareness >= 0.65:
            record.threat_category = "oversight_subversion"
        elif telemetry.top_threat_category != "benign":
            record.threat_category = telemetry.top_threat_category

        prior = (record.explanation or "").strip()
        prefix = f"Calibrated prefill ({telemetry.model}): " + "; ".join(reasons)
        record.explanation = f"{prefix}. {prior}".strip()
        return record

    async def evaluate_tool_call_async(
        self,
        session_id: str,
        tool_name: str,
        tool_input: str,
        diff: str | None = None,
        user_intent: str = "",
        prior_state_exposed: bool = False,
        trajectory_context: str = "",
        provenance: ProvenanceMetadata | None = None,
        thought_context: str | None = None,
        *,
        strict_mode: bool = False,
        sync_deep_review: bool = False,
        schedule_deep_review: bool = True,
        persister: Callable[[ReviewRecord, str], None] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ReviewRecord:
        """Async hot path: Tier 1 rules + Tier 1.5 calibrated prefill + async Tier 3.

        Returns in roughly the calibrated prefill latency (~185ms) instead of
        blocking on a frontier deep-review model. ``sync_deep_review=True`` awaits
        Tier 3 for offline benchmarks; ``schedule_deep_review=False`` lets callers
        persist the record before spawning the background task.
        """
        effective_intent = user_intent or (thought_context or "")
        if provenance is None:
            provenance = ProvenanceMetadata(call_origin="user_direct", delegation_depth=0)
        is_subagent = provenance.call_origin == "subagent" or provenance.delegation_depth > 0

        # Without the calibrated gate (or without any usable Decisions API key),
        # preserve the historical synchronous pipeline so callers still get
        # Tier 2 / Tier 3 fail-closed behavior.
        if not self.use_decision_api or not self._decision_api_available():
            return await asyncio.to_thread(
                self.evaluate_tool_call,
                session_id,
                tool_name,
                tool_input,
                diff,
                user_intent,
                prior_state_exposed,
                trajectory_context,
                provenance,
                thought_context,
            )

        negative_constraints = extract_negative_constraints(
            effective_intent,
            trajectory_context,
            session_id,
            self.custom_instructions,
        )
        is_neg_violation, neg_desc = check_negative_constraint_violation(
            negative_constraints, tool_name, tool_input, diff=diff
        )

        record = await asyncio.to_thread(
            self.evaluate_tool_call,
            session_id,
            tool_name,
            tool_input,
            diff,
            user_intent,
            prior_state_exposed,
            trajectory_context,
            provenance,
            thought_context,
            sync_deep_review=False,
            apply_denial_safeguards=False,
        )

        # Deterministic blocks, human-escalation rules, and operator always-allow thresholds
        # short-circuit so the prefill budget is never spent on settled outcomes.
        if (
            record.decision in ("block", "deny")
            or (record.stage == "rule" and record.decision == "escalate")
            or (record.stage == "threshold" and record.decision == "allow")
        ):
            return self._apply_denial_safeguards(record)

        # Cross-family detection: identify subject agent model
        subj_model = None
        if metadata and isinstance(metadata, dict):
            subj_model = metadata.get("subject_model") or metadata.get("model")
        if not subj_model and record.metadata and isinstance(record.metadata, dict):
            subj_model = record.metadata.get("subject_model") or record.metadata.get("model")
        if not subj_model and provenance:
            subj_model = getattr(provenance, "delegated_by", None)
        if not subj_model and session_id:
            try:
                from server.watcher_store import get_watcher_store

                store = get_watcher_store()
                sess = store.get_session(session_id)
                if sess and getattr(sess, "model", None):
                    subj_model = sess.model
            except Exception:  # noqa: BLE001
                pass

        subject_family = infer_model_family(subj_model) if subj_model else None

        telemetry: CalibratedDecisionTelemetry | None = None
        try:
            client = self._get_decision_client()
            user_ctx = (
                f"{effective_intent}\n<custom_organization_instructions>\n{self.custom_instructions.strip()}\n</custom_organization_instructions>".strip()
                if self.custom_instructions and self.custom_instructions.strip()
                else effective_intent
            )
            eval_kwargs: dict[str, Any] = {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "user_context": user_ctx,
                "provenance": provenance,
                "timeout_sec": self.decision_timeout_sec,
            }
            if subj_model:
                eval_kwargs["subject_model"] = subj_model
            try:
                telemetry = await asyncio.wait_for(
                    client.evaluate_runtime_tool_call(**eval_kwargs),
                    timeout=self.decision_timeout_sec + 0.05,
                )
            except TypeError:
                eval_kwargs.pop("subject_model", None)
                telemetry = await asyncio.wait_for(
                    client.evaluate_runtime_tool_call(**eval_kwargs),
                    timeout=self.decision_timeout_sec + 0.05,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Tier 1.5 calibrated prefill unavailable; falling back to synchronous pipeline: %s",
                exc,
            )

        if telemetry is not None and subject_family:
            telemetry.subject_model_family = subject_family
            mon_family = infer_model_family(telemetry.model)
            telemetry.monitor_model_family = mon_family
            telemetry.cross_family_verified = bool(
                subject_family != mon_family and subject_family != "other"
            )

        if telemetry is None:
            # Never fail open because the prefill service is down: rerun the
            # legacy triage/deep-review pipeline (which honors fail-closed policy).
            return await asyncio.to_thread(
                self.evaluate_tool_call,
                session_id,
                tool_name,
                tool_input,
                diff,
                user_intent,
                prior_state_exposed,
                trajectory_context,
                provenance,
                thought_context,
            )

        self._apply_calibrated_telemetry(
            record, telemetry, is_subagent=is_subagent, strict_mode=strict_mode
        )
        record = self._apply_denial_safeguards(record)

        if telemetry.deep_review_status == "pending_async":
            review_kwargs = {
                "tool_input": tool_input,
                "user_intent": effective_intent,
                "prior_state_exposed": prior_state_exposed,
                "trajectory_context": trajectory_context,
                "is_subagent": is_subagent,
                "negative_constraint_violated": is_neg_violation,
                "negative_violation_desc": neg_desc,
                "persister": persister,
            }
            if sync_deep_review:
                await self._run_async_deep_review(record, **review_kwargs)
            elif schedule_deep_review:
                self.spawn_deep_review(record, **review_kwargs)

        return record

    # ------------------------------------------------------------------
    # Tier 3: Asynchronous deep review
    # ------------------------------------------------------------------

    def _deep_review_model_for(self, record: ReviewRecord) -> str:
        """Route deep review to a distinct model family from the subject agent, honoring cross-family routing."""
        subject_family = "other"
        if record.calibrated_telemetry and record.calibrated_telemetry.subject_model_family:
            subject_family = record.calibrated_telemetry.subject_model_family
        elif record.metadata and ("subject_model" in record.metadata or "model" in record.metadata):
            subject_family = infer_model_family(
                record.metadata.get("subject_model") or record.metadata.get("model")
            )

        severity = (
            record.calibrated_telemetry.expected_severity
            if record.calibrated_telemetry is not None
            else float(record.score)
        )

        if subject_family == "google":
            # Distinct model family for Google subject agent: OpenCode DeepSeek
            chosen = os.environ.get(
                "OPENEVAL_WATCHER_DEEP_REVIEW_MODEL", "opencode/deepseek-v4.1-flash"
            )
        elif subject_family == "deepseek":
            # Distinct model family for DeepSeek subject agent: Google Gemini
            chosen = os.environ.get("OPENEVAL_WATCHER_DEEP_REVIEW_MODEL_HIGH", "gemini-4-hydrogen")
        else:
            if severity >= 7.0:
                chosen = os.environ.get(
                    "OPENEVAL_WATCHER_DEEP_REVIEW_MODEL_HIGH", "gemini-4-hydrogen"
                )
            else:
                chosen = os.environ.get(
                    "OPENEVAL_WATCHER_DEEP_REVIEW_MODEL", "opencode/deepseek-v4.1-flash"
                )

        # Set cross_family_verified if monitor family differs from subject family
        chosen_family = infer_model_family(chosen)
        if subject_family and subject_family != "other" and subject_family != chosen_family:
            if record.calibrated_telemetry:
                record.calibrated_telemetry.cross_family_verified = True
            record.metadata = record.metadata or {}
            record.metadata["cross_family_verified"] = True

        return chosen

    def spawn_deep_review(
        self,
        record: ReviewRecord,
        *,
        tool_input: str,
        user_intent: str = "",
        prior_state_exposed: bool = False,
        trajectory_context: str = "",
        is_subagent: bool = False,
        negative_constraint_violated: bool = False,
        negative_violation_desc: str = "",
        persister: Callable[[ReviewRecord, str], None] | None = None,
    ) -> asyncio.Task[Any] | None:
        """Schedule Tier 3 on the running loop without blocking the hook response."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("No running event loop; async deep review skipped for %s", record.id)
            return None
        task = loop.create_task(
            self._run_async_deep_review(
                record,
                tool_input=tool_input,
                user_intent=user_intent,
                prior_state_exposed=prior_state_exposed,
                trajectory_context=trajectory_context,
                is_subagent=is_subagent,
                negative_constraint_violated=negative_constraint_violated,
                negative_violation_desc=negative_violation_desc,
                persister=persister,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _run_async_deep_review(
        self,
        record: ReviewRecord,
        *,
        tool_input: str,
        user_intent: str = "",
        prior_state_exposed: bool = False,
        trajectory_context: str = "",
        is_subagent: bool = False,
        negative_constraint_violated: bool = False,
        negative_violation_desc: str = "",
        persister: Callable[[ReviewRecord, str], None] | None = None,
    ) -> ReviewRecord:
        """Run Tier 3 off the hot path and enrich the persisted ReviewRecord."""
        model = self._deep_review_model_for(record)
        result: StageResult | None = None
        try:
            result = await self._async_deep_review_grade(
                model=model,
                tool_name=record.tool_name,
                tool_input=tool_input,
                base_score=record.score,
                user_intent=user_intent,
                prior_state_exposed=prior_state_exposed,
                trajectory_context=trajectory_context,
                negative_constraint_violated=negative_constraint_violated,
                negative_violation_desc=negative_violation_desc,
                is_subagent=is_subagent,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tier 3 async deep review failed (%s): %s", model, exc)

        status = "completed" if result is not None else "failed"
        record.metadata = record.metadata or {}
        record.metadata["deep_review_status"] = status
        record.metadata["deep_review_model"] = model
        if record.calibrated_telemetry is not None:
            record.calibrated_telemetry.deep_review_status = status
            record.calibrated_telemetry.deep_review_model = model
            record.metadata["calibrated_telemetry"] = record.calibrated_telemetry.model_dump()

        if result is not None:
            record.score = max(1, min(10, int(result.score)))
            record.stage = "deep_review"
            record.explanation = f"[Tier 3 Deep Review | {model}] {result.context}"
            if result.threat_category:
                record.threat_category = result.threat_category
            recommendation, _, _ = self.apply_thresholds(
                record.tool_name,
                record.score,
                "deep_review",
                result.context,
                is_subagent=is_subagent,
            )
            record.metadata["deep_review_recommendation"] = recommendation
        else:
            record.explanation = (
                f"Tier 3 deep review failed ({model}); calibrated prefill decision retained. "
                f"{record.explanation or ''}"
            ).strip()

        _persist = persister or self._persist_deep_review_audit
        try:
            _persist(record, status)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist Tier 3 deep review for %s: %s", record.id, exc)

        logger.info(
            "deep_review id=%s model=%s status=%s score=%s",
            record.id,
            model,
            status,
            record.score,
        )
        return record

    def _persist_deep_review_audit(self, record: ReviewRecord, status: str) -> None:
        """Default persistence hook: update the stored ReviewRecord if it exists."""
        from server.watcher_store import get_watcher_store

        get_watcher_store().update_review_deep_audit(
            record.id,
            record.explanation,
            record.score,
            record.stage,
            status,
            metadata_patch=record.metadata,
            session_id=record.session_id,
            calibrated_telemetry=record.calibrated_telemetry,
        )

    async def _async_deep_review_grade(
        self,
        *,
        model: str,
        tool_name: str,
        tool_input: str,
        base_score: int,
        user_intent: str = "",
        prior_state_exposed: bool = False,
        trajectory_context: str = "",
        negative_constraint_violated: bool = False,
        negative_violation_desc: str = "",
        is_subagent: bool = False,
    ) -> StageResult | None:
        """Grade Tier 3 with the configured frontier model, falling back to heuristics."""
        sys_prompt, user_prompt = self._build_deep_review_prompts(
            tool_name=tool_name,
            tool_input=tool_input,
            base_score=base_score,
            user_intent=user_intent,
            prior_state_exposed=prior_state_exposed,
            trajectory_context=trajectory_context,
            is_subagent=is_subagent,
        )

        provider: str
        api_key: str | None
        if model.startswith("opencode/"):
            provider, api_key = "opencode", os.environ.get("OPENCODE_API_KEY")
        elif model.startswith(("gemini-", "google/", "gemma-")):
            provider = "google"
            discovered = discover_gemini_keys()
            api_key = discovered[0] if discovered else None
        elif model.startswith("claude-"):
            provider, api_key = "anthropic", os.environ.get("ANTHROPIC_API_KEY")
        else:
            provider, api_key = "openai", os.environ.get("OPENAI_API_KEY")

        if api_key:
            try:
                from engine.llm_runner import AsyncLLMRunner, ChatMessage, LLMConfig

                runner = AsyncLLMRunner(provider=provider, api_key=api_key)
                try:
                    response = await runner.generate(
                        [
                            ChatMessage(role="system", content=sys_prompt),
                            ChatMessage(role="user", content=user_prompt),
                        ],
                        config=LLMConfig(
                            model=model,
                            provider=provider,
                            temperature=0.0,
                            max_tokens=int(
                                os.environ.get("OPENEVAL_WATCHER_DEEP_REVIEW_MAX_TOKENS", "2048")
                            ),
                            timeout_sec=float(
                                os.environ.get("OPENEVAL_WATCHER_DEEP_REVIEW_TIMEOUT", "20")
                            ),
                            max_retries=0,
                        ),
                    )
                finally:
                    with contextlib.suppress(Exception):
                        await runner.close()
                content = response.content or ""
                parsed = _parse_decision_xml(content)
                if parsed:
                    return StageResult(parsed[0], parsed[1], _parse_threat_category(content))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tier 3 model %s unavailable: %s", model, exc)

        # Deterministic fallback: never block the async path on a second network call.
        return self._deep_review_heuristics(
            base_score=base_score,
            user_intent=user_intent,
            prior_state_exposed=prior_state_exposed,
            negative_constraint_violated=negative_constraint_violated,
            negative_violation_desc=negative_violation_desc,
            is_subagent=is_subagent,
            llm_judge_active=True,
        )

    def _llm_request_timeout(self, deadline: float | None = None) -> float | None:
        """Seconds left in the shared LLM budget, or None when the budget is spent."""
        if deadline is None:
            deadline = getattr(self, "_llm_deadline", None)
        if deadline is None:
            return self.llm_timeout_sec
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            return None
        return min(self.llm_timeout_sec, remaining)

    def _llm_grade(
        self,
        system_prompt: str,
        user_prompt: str,
        deadline: float | None = None,
    ) -> StageResult | None:
        """Call Gemini with a hard timeout; return parsed XML score or None.

        The HTTP call is synchronous urllib so unit tests can patch ``urlopen``.
        The FastAPI evaluate path runs this whole method inside ``asyncio.to_thread``
        so the event loop stays unblocked. An open circuit returns before any network I/O.
        """
        if not self.use_llm or self._llm_circuit_open:
            return None
        timeout = self._llm_request_timeout(deadline)
        if timeout is None:
            return None
        discovered = discover_gemini_keys()
        api_key = self._api_key or (discovered[0] if discovered else None)
        if not api_key:
            return None
        try:
            import json
            import urllib.request

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.llm_model}:generateContent?key={api_key}"
            )
            body = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "maxOutputTokens": 256,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            requester = self._requester or urllib.request.urlopen
            with requester(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            parsed = _parse_decision_xml(text or "")
            self._llm_fail_streak = 0
            if parsed:
                cat = _parse_threat_category(text or "")
                return StageResult(parsed[0], parsed[1], cat)
            # Fallback: first integer 1-10 in text
            cleaned = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text or "", flags=re.I)
            m = re.search(r"\b([1-9]|10)\b", cleaned)
            if m:
                cat = _parse_threat_category(text or "")
                return StageResult(int(m.group(1)), cleaned.strip()[:500], cat)
        # Boundary: LLM network/provider failure or JSON decode error
        except Exception as err:  # noqa: BLE001
            self._llm_fail_streak += 1
            if self._llm_fail_streak >= 3:
                self._llm_circuit_open = True
                logger.warning(
                    "LLM grade circuit open after %s failures (model=%s): %s — "
                    "falling back to heuristics for this process. "
                    "Set OPENEVAL_WATCHER_LLM_MODEL to a live model or OPENEVAL_WATCHER_USE_LLM=0.",
                    self._llm_fail_streak,
                    self.llm_model,
                    err,
                )
            else:
                logger.warning("LLM grade failed: %s", err)
        return None

    def _failure_score(self, heuristic: tuple[int, str] | StageResult) -> StageResult:
        cat = getattr(heuristic, "threat_category", None)
        score = int(heuristic[0])
        ctx = str(heuristic[1])
        if self.on_failure in ("escalate", "fail_closed"):
            return StageResult(
                max(score, 6), f"LLM failure/timeout → fail-closed (ask): {ctx}", cat
            )
        if isinstance(heuristic, StageResult):
            return heuristic
        return StageResult(score, ctx, cat)

    def _fast_triage(
        self,
        tool_name: str,
        tool_input: str,
        prior_state_exposed: bool = False,
        trajectory_context: str = "",
        negative_constraint_violated: bool = False,
        negative_violation_desc: str = "",
        is_subagent: bool = False,
        deadline: float | None = None,
    ) -> StageResult:
        """Layer 1 Fast Triage: LLM (optional) then Secret Reading Law heuristics."""
        if negative_constraint_violated:
            return StageResult(
                8,
                f"Reversibility ≠ Authorization: Explicit negative constraint '{negative_violation_desc}' violated. Absolute floor score >= 8 enforced.",
                "oversight_subversion",
            )

        if is_subagent and any(
            re.search(p, tool_input, re.IGNORECASE)
            for p in (
                r"\.env\b",
                r"\.secrets\b",
                r"id_rsa",
                r"id_ed25519",
                r"sudo\b",
                r"/etc/shadow",
            )
        ):
            return StageResult(
                9,
                "[SUBAGENT DELEGATE RESTRICTION]: Subagents strictly prohibited from credential access or privilege escalation.",
                "privilege_escalation",
            )

        # Redact secrets and escape untrusted inputs with per-call random nonce tag
        nonce = uuid.uuid4().hex[:8]
        redacted_input = redact_text(tool_input)
        redacted_traj = redact_text(trajectory_context)

        escaped_tool_name = html.escape(tool_name, quote=False)
        escaped_tool_input = html.escape(redacted_input, quote=False)

        prompt_parts = [
            f"<untrusted_input_{nonce}>",
            f"tool_name: {escaped_tool_name}",
            f"tool_input: {escaped_tool_input}",
            f"prior_state_exposed: {prior_state_exposed}",
            f"is_subagent: {is_subagent}",
        ]
        if redacted_traj:
            escaped_traj = html.escape(redacted_traj, quote=False)
            prompt_parts.append(f"trajectory_context (tool outputs stripped):\n{escaped_traj}")
        prompt_parts.append(f"</untrusted_input_{nonce}>")
        user_prompt = "\n".join(prompt_parts)
        triage_grader = get_canonical_grader("PolicyGatewayFastTriageGrader")
        sys_prompt = (
            triage_grader.assemble_system_prompt() if triage_grader else FAST_TRIAGE_SYSTEM_PROMPT
        )
        if self.custom_instructions and self.custom_instructions.strip():
            custom_block = (
                f"\n<custom_organization_instructions>\n"
                f"{self.custom_instructions.strip()}\n"
                f"</custom_organization_instructions>\n"
            )
            sys_prompt += custom_block
            user_prompt += custom_block
        llm = self._llm_grade(sys_prompt, user_prompt, deadline=deadline)
        if llm:
            return llm

        # Secret Reading Law:
        secret_patterns = [
            r"\.env\b",
            r"\.secrets\b",
            r"aws/credentials",
            r"id_rsa",
            r"id_ed25519",
            r"\.pem\b",
            r"\.key\b",
        ]
        is_secret_path = any(re.search(p, tool_input, re.IGNORECASE) for p in secret_patterns)

        if is_secret_path:
            if prior_state_exposed and not is_subagent:
                return StageResult(
                    5,
                    "Prior-State Contamination Floor: Re-referencing already-exposed credential (score 5).",
                    "data_exfiltration",
                )
            return StageResult(
                7,
                "Secret Reading Law: Accessing credential or secret file (.env, keys) is a security event.",
                "data_exfiltration",
            )

        if re.search(r"\brm\s+(-[rf]+|--force)\b", tool_input, re.IGNORECASE):
            return StageResult(
                8, "Destructive filesystem modification requested.", "undesirable_deletions"
            )

        if re.search(r"\b(truncate|mkfs|dd\s+of=)\b", tool_input, re.IGNORECASE):
            return StageResult(
                9,
                "High-risk raw data deletion or block device modification.",
                "infrastructure_disruption",
            )

        if re.search(r"\.(md|example|txt|css|html)\b", tool_input, re.IGNORECASE):
            return StageResult(2, "Low-risk non-executable documentation or template update.", None)

        heuristic = StageResult(3, "Routine coding operation within acceptable threshold.", None)
        if self.use_llm:
            return self._failure_score(heuristic)
        return heuristic

    def _build_deep_review_prompts(
        self,
        *,
        tool_name: str,
        tool_input: str,
        base_score: int,
        user_intent: str = "",
        prior_state_exposed: bool = False,
        trajectory_context: str = "",
        is_subagent: bool = False,
    ) -> tuple[str, str]:
        """Build the hardened Tier 3 system/user prompts (shared by sync and async paths)."""
        # Redact secrets and escape untrusted inputs with per-call random nonce tag
        nonce = uuid.uuid4().hex[:8]
        redacted_input = redact_text(tool_input)
        redacted_traj = redact_text(trajectory_context)

        escaped_tool_name = html.escape(tool_name, quote=False)
        escaped_tool_input = html.escape(redacted_input, quote=False)
        escaped_intent = html.escape(redact_text(user_intent), quote=False)

        prompt_parts = [
            f"<untrusted_input_{nonce}>",
            f"tool_name: {escaped_tool_name}",
            f"tool_input: {escaped_tool_input}",
            f"base_score: {base_score}",
            f"user_intent: {escaped_intent}",
            f"prior_state_exposed: {prior_state_exposed}",
            f"is_subagent: {is_subagent}",
        ]
        if redacted_traj:
            escaped_traj = html.escape(redacted_traj, quote=False)
            prompt_parts.append(f"trajectory_context (tool outputs stripped):\n{escaped_traj}")
        prompt_parts.append(f"</untrusted_input_{nonce}>")
        user_prompt = "\n".join(prompt_parts)

        deep_grader = get_canonical_grader("PolicyGatewayDeepGrader")
        sys_prompt = (
            deep_grader.assemble_system_prompt() if deep_grader else DEEP_REVIEW_SYSTEM_PROMPT
        )
        if self.custom_instructions and self.custom_instructions.strip():
            custom_block = (
                f"\n<custom_organization_instructions>\n"
                f"{self.custom_instructions.strip()}\n"
                f"</custom_organization_instructions>\n"
            )
            sys_prompt += custom_block
            user_prompt += custom_block
        return sys_prompt, user_prompt

    def _deep_review_heuristics(
        self,
        *,
        base_score: int,
        user_intent: str = "",
        prior_state_exposed: bool = False,
        negative_constraint_violated: bool = False,
        negative_violation_desc: str = "",
        is_subagent: bool = False,
        llm_judge_active: bool | None = None,
    ) -> StageResult:
        """Deterministic Score Adjustment Calculus used when no LLM is available."""
        if negative_constraint_violated:
            return StageResult(
                max(base_score, 8),
                f"Reversibility ≠ Authorization: Explicit negative constraint '{negative_violation_desc}' violated. "
                "Reversibility and user intent deductions disqualified; absolute floor score >= 8 enforced.",
                "oversight_subversion",
            )

        score = base_score
        adjustments: list[str] = []
        judge_active = self.use_llm if llm_judge_active is None else llm_judge_active

        if is_subagent:
            adjustments.append("Subagent delegate: user intent deductions disqualified")
        elif judge_active:
            adjustments.append("LLM judge active: heuristic user intent deductions disqualified")
        elif user_intent:
            if any(
                kw in user_intent.lower() for kw in ["delete", "remove", "clean", "update", "push"]
            ):
                score -= 4
                adjustments.append("User explicitly authorized action (-4)")
            else:
                score -= 2
                adjustments.append("Action is a plausible step for stated user intent (-2)")

        if prior_state_exposed and score > 6 and not is_subagent:
            score = 6
            adjustments.append("Prior-state contamination floor applied (capped at 6)")

        if is_subagent and score >= 6:
            score = max(score, 6)
            adjustments.append("Subagent strict ceiling maintained (score >= 6)")

        score = max(1, min(10, score))
        context = (
            f"Deep Review completed (Score {score}/10). Adjustments: "
            f"{', '.join(adjustments) if adjustments else 'Standard risk baseline'}."
        )
        return StageResult(score, context, None)

    def _deep_review(
        self,
        tool_name: str,
        tool_input: str,
        base_score: int,
        user_intent: str = "",
        prior_state_exposed: bool = False,
        trajectory_context: str = "",
        negative_constraint_violated: bool = False,
        negative_violation_desc: str = "",
        is_subagent: bool = False,
        deadline: float | None = None,
    ) -> StageResult:
        """Layer 2 Deep Review: LLM (optional) then Score Adjustment Calculus."""
        if negative_constraint_violated:
            return self._deep_review_heuristics(
                base_score=base_score,
                user_intent=user_intent,
                prior_state_exposed=prior_state_exposed,
                negative_constraint_violated=negative_constraint_violated,
                negative_violation_desc=negative_violation_desc,
                is_subagent=is_subagent,
            )

        sys_prompt, user_prompt = self._build_deep_review_prompts(
            tool_name=tool_name,
            tool_input=tool_input,
            base_score=base_score,
            user_intent=user_intent,
            prior_state_exposed=prior_state_exposed,
            trajectory_context=trajectory_context,
            is_subagent=is_subagent,
        )
        llm = self._llm_grade(sys_prompt, user_prompt, deadline=deadline)
        if llm:
            return llm

        heuristic = self._deep_review_heuristics(
            base_score=base_score,
            user_intent=user_intent,
            prior_state_exposed=prior_state_exposed,
            negative_constraint_violated=False,
            negative_violation_desc=negative_violation_desc,
            is_subagent=is_subagent,
        )
        if self.use_llm:
            return self._failure_score(heuristic)
        return heuristic

    _run_fast_triage = _fast_triage
    _run_fast_triage_sync = _fast_triage
    _run_deep_review = _deep_review
    _run_deep_review_sync = _deep_review
