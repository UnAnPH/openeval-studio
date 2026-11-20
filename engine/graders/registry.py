"""Canonical grader registry, alias resolution, and single-pass batch evaluation.

The live ``CANONICAL_GRADERS`` dict and its immutable ``_DEFAULT_CANONICAL_GRADERS``
snapshot are defined here so mutation, reset, and per-user overrides all funnel
through a single module. Callers keep importing them from
``engine.graders_suite`` (the backward-compatible facade).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.graders.models import CanonicalGrader
from engine.graders.policy_graders import (
    GOAL_GUARDING_SCHEMING_GRADER,
    INCIDENT_REMEDIATION_GRADER,
    POLICY_GATEWAY_DEEP_GRADER,
    POLICY_GATEWAY_FAST_TRIAGE_GRADER,
    SEMANTIC_RUN_COMPARATOR_GRADER,
    SITUATIONAL_AWARENESS_GRADER,
    UNFAITHFUL_COT_GRADER,
)
from engine.graders.quality_graders import (
    CITATION_GROUNDING_GRADER,
    HALLUCINATION_GRADER,
    INSTRUCTION_GRADER,
    PLAN_ADHERENCE_GRADER,
    REWARD_SEEKING_GRADER,
    REWARD_TAMPERING_GRADER,
)
from engine.graders.safety_graders import (
    ALIGNMENT_FAKING_GRADER,
    CREDENTIALS_GRADER,
    DELETION_GRADER,
    PII_GRADER,
    SECURITY_GRADER,
)

if TYPE_CHECKING:
    from engine.decision_client import AsyncDecisionClient


# =============================================================================
# Canonical Graders Registry & Resolvers
# =============================================================================

# Registry of strictly the 11 active platform judges across 3 categories
CANONICAL_GRADERS: dict[str, CanonicalGrader] = {
    # Category 1: Scheming Scanners (Dimensions 1–3 — 3 Judges)
    "SituationalAwarenessGrader": SITUATIONAL_AWARENESS_GRADER,
    "GoalGuardingSchemingGrader": GOAL_GUARDING_SCHEMING_GRADER,
    "UnfaithfulCoTGrader": UNFAITHFUL_COT_GRADER,
    # Category 2: Core Safety Judges (Dimensions 4–7 — 4 Judges)
    "PlanAdherenceGrader": PLAN_ADHERENCE_GRADER,
    "HallucinationGrader": HALLUCINATION_GRADER,
    "RewardTamperingGrader": REWARD_TAMPERING_GRADER,
    "RewardSeekingGrader": REWARD_SEEKING_GRADER,
    # Category 3: Gateway & Incident Judges (4 Judges)
    "PolicyGatewayFastTriageGrader": POLICY_GATEWAY_FAST_TRIAGE_GRADER,
    "PolicyGatewayDeepGrader": POLICY_GATEWAY_DEEP_GRADER,
    "SemanticRunComparatorGrader": SEMANTIC_RUN_COMPARATOR_GRADER,
    "IncidentRemediationGrader": INCIDENT_REMEDIATION_GRADER,
}

_FACTORY_CANONICAL_GRADERS: dict[str, CanonicalGrader] = {
    k: v.model_copy(deep=True) for k, v in CANONICAL_GRADERS.items()
}

# Immutable process-lifetime snapshot. ``reset_canonical_graders`` restores from
# here, so repeated test runs always start from the exact shipped defaults.
_DEFAULT_CANONICAL_GRADERS: dict[str, CanonicalGrader] = {
    k: v.model_copy(deep=True) for k, v in CANONICAL_GRADERS.items()
}


def get_canonical_grader(name: str) -> CanonicalGrader | None:
    """Retrieve a canonical grader by name or alias."""
    if name in CANONICAL_GRADERS:
        return CANONICAL_GRADERS[name]
    norm = name.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        # Legacy Threat Monitors & Scanners -> Closest Active Judges
        # CredentialsGrader -> PolicyGatewayDeepGrader
        "credentialsgrader": "PolicyGatewayDeepGrader",
        "credentials_grader": "PolicyGatewayDeepGrader",
        "credentials_and_api_keys": "PolicyGatewayDeepGrader",
        "credentials": "PolicyGatewayDeepGrader",
        "data_exfiltration": "PolicyGatewayDeepGrader",
        # PIIGrader -> PolicyGatewayDeepGrader
        "piigrader": "PolicyGatewayDeepGrader",
        "pii_grader": "PolicyGatewayDeepGrader",
        "pii_and_sensitive_info": "PolicyGatewayDeepGrader",
        "pii_and_sensitive_information": "PolicyGatewayDeepGrader",
        "pii": "PolicyGatewayDeepGrader",
        "privacy_and_pii": "PolicyGatewayDeepGrader",
        "privacy": "PolicyGatewayDeepGrader",
        # InstructionGrader -> PlanAdherenceGrader
        "instructiongrader": "PlanAdherenceGrader",
        "instruction_grader": "PlanAdherenceGrader",
        "instruction_and_policy_adherence": "PlanAdherenceGrader",
        "instruction": "PlanAdherenceGrader",
        "instruction_following": "PlanAdherenceGrader",
        # SecurityGrader -> PolicyGatewayDeepGrader
        "securitygrader": "PolicyGatewayDeepGrader",
        "security_grader": "PolicyGatewayDeepGrader",
        "code_security": "PolicyGatewayDeepGrader",
        "security": "PolicyGatewayDeepGrader",
        # DeletionGrader -> PolicyGatewayDeepGrader
        "deletiongrader": "PolicyGatewayDeepGrader",
        "deletion_grader": "PolicyGatewayDeepGrader",
        "infrastructure_safety": "PolicyGatewayDeepGrader",
        "deletion": "PolicyGatewayDeepGrader",
        "destructive_deletions": "PolicyGatewayDeepGrader",
        # AlignmentFakingGrader -> SituationalAwarenessGrader
        "alignmentfakinggrader": "SituationalAwarenessGrader",
        "alignment_faking_grader": "SituationalAwarenessGrader",
        "alignment_faking": "SituationalAwarenessGrader",
        "alignmentfaking": "SituationalAwarenessGrader",
        "strategic_compliance": "SituationalAwarenessGrader",
        # CitationGroundingGrader -> HallucinationGrader
        "citationgroundinggrader": "HallucinationGrader",
        "citation_grounding_grader": "HallucinationGrader",
        "citation_grounding": "HallucinationGrader",
        "citationgrounding": "HallucinationGrader",
        "grounding_and_citation": "HallucinationGrader",
        "citation_grounding_scorer": "HallucinationGrader",
        # Active 11 Platform Judges
        # 1. SituationalAwarenessGrader
        "situational_awareness": "SituationalAwarenessGrader",
        "situationalawareness": "SituationalAwarenessGrader",
        "situational_awareness_grader": "SituationalAwarenessGrader",
        "situationalawarenessgrader": "SituationalAwarenessGrader",
        "sandbagging": "SituationalAwarenessGrader",
        "eval_awareness": "SituationalAwarenessGrader",
        # 2. GoalGuardingSchemingGrader
        "goal_guarding_scheming": "GoalGuardingSchemingGrader",
        "goal_guarding": "GoalGuardingSchemingGrader",
        "goalguardingscheming": "GoalGuardingSchemingGrader",
        "goal_guarding_scheming_grader": "GoalGuardingSchemingGrader",
        "scheming": "GoalGuardingSchemingGrader",
        "goalguarding": "GoalGuardingSchemingGrader",
        # 3. UnfaithfulCoTGrader
        "unfaithful_cot": "UnfaithfulCoTGrader",
        "unfaithfulcot": "UnfaithfulCoTGrader",
        "unfaithful_cot_grader": "UnfaithfulCoTGrader",
        "cot_faithfulness": "UnfaithfulCoTGrader",
        "unfaithful_chain_of_thought": "UnfaithfulCoTGrader",
        # 4. PlanAdherenceGrader
        "plan_adherence": "PlanAdherenceGrader",
        "planadherence": "PlanAdherenceGrader",
        "plan_adherence_grader": "PlanAdherenceGrader",
        "planadherencegrader": "PlanAdherenceGrader",
        "plan": "PlanAdherenceGrader",
        # 5. HallucinationGrader
        "hallucination": "HallucinationGrader",
        "hallucinations": "HallucinationGrader",
        "hallucinationgrader": "HallucinationGrader",
        "hallucination_grader": "HallucinationGrader",
        "hallucination_detection": "HallucinationGrader",
        "grounding": "HallucinationGrader",
        "hallucination_and_grounding": "HallucinationGrader",
        # 6. RewardTamperingGrader
        "reward_tampering": "RewardTamperingGrader",
        "rewardtampering": "RewardTamperingGrader",
        "reward_tampering_grader": "RewardTamperingGrader",
        "rewardtamperinggrader": "RewardTamperingGrader",
        "tampering": "RewardTamperingGrader",
        "test_tampering": "RewardTamperingGrader",
        "sabotage": "RewardTamperingGrader",
        # 7. RewardSeekingGrader
        "reward_seeking": "RewardSeekingGrader",
        "rewardseeking": "RewardSeekingGrader",
        "reward_seeking_grader": "RewardSeekingGrader",
        "specification_gaming": "RewardSeekingGrader",
        # 8. PolicyGatewayFastTriageGrader
        "policy_gateway_fast_triage": "PolicyGatewayFastTriageGrader",
        "fast_triage": "PolicyGatewayFastTriageGrader",
        "policy_gateway_fast_triage_grader": "PolicyGatewayFastTriageGrader",
        "fasttriage": "PolicyGatewayFastTriageGrader",
        "triage_grader": "PolicyGatewayFastTriageGrader",
        # 9. PolicyGatewayDeepGrader
        "policy_gateway": "PolicyGatewayDeepGrader",
        "policygateway": "PolicyGatewayDeepGrader",
        "policy_gateway_deep": "PolicyGatewayDeepGrader",
        "policy_gateway_deep_grader": "PolicyGatewayDeepGrader",
        "policygatewaydeepgrader": "PolicyGatewayDeepGrader",
        "policy_gateway_grader": "PolicyGatewayDeepGrader",
        "gateway": "PolicyGatewayDeepGrader",
        "deep_review": "PolicyGatewayDeepGrader",
        # 10. SemanticRunComparatorGrader
        "semantic_run_comparator": "SemanticRunComparatorGrader",
        "semantic_compare": "SemanticRunComparatorGrader",
        "semantic_run_comparator_grader": "SemanticRunComparatorGrader",
        "run_comparator": "SemanticRunComparatorGrader",
        "semantic_trajectory_compare": "SemanticRunComparatorGrader",
        # 11. IncidentRemediationGrader
        "incident_remediation": "IncidentRemediationGrader",
        "incident_remediation_grader": "IncidentRemediationGrader",
        "remediation_grader": "IncidentRemediationGrader",
        "incident_remediator": "IncidentRemediationGrader",
    }
    resolved = aliases.get(norm)
    if resolved and resolved in CANONICAL_GRADERS:
        return CANONICAL_GRADERS[resolved]
    for k, v in CANONICAL_GRADERS.items():
        if k.lower() == norm or k.lower().replace("grader", "") == norm:
            return v
    return None


def list_canonical_graders(from_factory: bool = False) -> list[CanonicalGrader]:
    """List strictly the 11 active platform judges across 3 categories."""
    source = _FACTORY_CANONICAL_GRADERS if from_factory else CANONICAL_GRADERS
    return [v.model_copy() for v in source.values()]


def update_live_canonical_grader(name: str, updates: dict[str, str]) -> CanonicalGrader | None:
    """Dynamically update a registered canonical grader in CANONICAL_GRADERS."""
    grader = get_canonical_grader(name)
    if not grader:
        return None
    valid_fields = set(CanonicalGrader.model_fields.keys())
    filtered = {k: v for k, v in updates.items() if k in valid_fields}
    updated = grader.model_copy(update=filtered)
    CANONICAL_GRADERS[grader.name] = updated
    return updated


def reset_live_canonical_grader(name: str) -> CanonicalGrader | None:
    """Reset a registered canonical grader in CANONICAL_GRADERS back to factory defaults."""
    grader = get_canonical_grader(name)
    if not grader:
        return None
    factory = _DEFAULT_CANONICAL_GRADERS.get(grader.name)
    if not factory:
        return None
    restored = factory.model_copy(deep=True)
    CANONICAL_GRADERS[grader.name] = restored
    return restored


def reset_canonical_graders() -> list[str]:
    """Restore every live canonical grader to the immutable default snapshot.

    Returns the names restored. Runtime-injected keys that are not part of the
    shipped registry are dropped. Used by ``reset_all_runtime_state`` so tests
    never leak prompt overrides into later tests.
    """
    for name in [k for k in CANONICAL_GRADERS if k not in _DEFAULT_CANONICAL_GRADERS]:
        del CANONICAL_GRADERS[name]
    for name, default in _DEFAULT_CANONICAL_GRADERS.items():
        CANONICAL_GRADERS[name] = default.model_copy(deep=True)
    return list(_DEFAULT_CANONICAL_GRADERS)


# =============================================================================
# Single-Pass Multi-Grader Evaluation (gemini-decision-1 Calibrated Prefill)
# =============================================================================

# All 18 canonical C1-C15 graders, including the legacy threat monitors that are
# defined but not part of the active 11-judge platform registry (Credentials,
# PII, Instruction, Security, Deletion, AlignmentFaking, CitationGrounding).
_ALL_CANONICAL_GRADERS: tuple[CanonicalGrader, ...] = (
    CREDENTIALS_GRADER,
    PII_GRADER,
    INSTRUCTION_GRADER,
    SECURITY_GRADER,
    DELETION_GRADER,
    REWARD_TAMPERING_GRADER,
    SITUATIONAL_AWARENESS_GRADER,
    PLAN_ADHERENCE_GRADER,
    HALLUCINATION_GRADER,
    POLICY_GATEWAY_DEEP_GRADER,
    GOAL_GUARDING_SCHEMING_GRADER,
    UNFAITHFUL_COT_GRADER,
    ALIGNMENT_FAKING_GRADER,
    REWARD_SEEKING_GRADER,
    CITATION_GROUNDING_GRADER,
    POLICY_GATEWAY_FAST_TRIAGE_GRADER,
    SEMANTIC_RUN_COMPARATOR_GRADER,
    INCIDENT_REMEDIATION_GRADER,
)


def _concise_grader_definition(grader: CanonicalGrader) -> str:
    """Extract the first substantive sentence of a grader's formal C3 definition."""
    text = grader.c3_definition or ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:400]
    return ""


def _batch_grader_rubrics() -> dict[str, str]:
    """Build concise per-grader rubrics for a single Decisions API batch request."""
    rubrics: dict[str, str] = {}
    for grader in _ALL_CANONICAL_GRADERS:
        parts: list[str] = []
        if grader.description:
            parts.append(grader.description.strip())
        if grader.dimension:
            parts.append(f"Dimension: {grader.dimension.strip()}")
        definition = _concise_grader_definition(grader)
        if definition:
            parts.append(f"Violation: {definition}")
        rubrics[grader.name] = " | ".join(parts)
    return rubrics


async def evaluate_all_graders_single_pass(
    trajectory_text: str,
    decision_client: AsyncDecisionClient | None = None,
) -> dict[str, dict]:
    """Evaluate all 18 canonical C1-C15 graders in a single Decisions API request.

    Uses ``AsyncDecisionClient.evaluate_batch_graders`` to score every canonical
    grader in ONE ``v1beta/decisions`` call (0 output tokens) instead of 18
    separate generative LLM calls. Returns ``{grader_name: result}`` where each
    result carries ``violation_probability``, ``expected_severity`` (1-5),
    ``severity_confidence``, and the raw ``probabilities`` distribution.
    """
    from engine.decision_client import AsyncDecisionClient

    if not trajectory_text or not trajectory_text.strip():
        return {}

    client = decision_client or AsyncDecisionClient()
    owns_client = decision_client is None
    try:
        return await client.evaluate_batch_graders(trajectory_text, _batch_grader_rubrics())
    finally:
        if owns_client:
            await client.close()
