"""Backward-compatible facade for the canonical C1–C15 grader suite.

Phase 4 split the former ~4,000-line implementation into :mod:`engine.graders`
(``models``, ``safety_graders``, ``quality_graders``, ``policy_graders``,
``registry``). This module intentionally keeps the historical import surface:

    from engine.graders_suite import CanonicalGrader, get_canonical_grader

Every name that used to live here is re-exported unchanged, and unlisted
attributes are proxied to :mod:`engine.graders` via ``__getattr__``. Mutable
module state (``CANONICAL_GRADERS``, ``_FACTORY_CANONICAL_GRADERS``,
``_DEFAULT_CANONICAL_GRADERS``) is the *same object* as in the registry module,
so live overrides and resets remain visible through both import paths, and
``unittest.mock.patch("engine.graders_suite.get_canonical_grader")`` still works
for callers that resolve the name at call time.
"""

from __future__ import annotations

from engine.graders import (
    _DEFAULT_CANONICAL_GRADERS,
    _FACTORY_CANONICAL_GRADERS,
    ALIGNMENT_FAKING_GRADER,
    APOLLO_ABLATION_GUIDANCE,
    CANONICAL_GRADERS,
    CITATION_GROUNDING_GRADER,
    CREDENTIALS_GRADER,
    DELETION_GRADER,
    GOAL_GUARDING_SCHEMING_GRADER,
    HALLUCINATION_GRADER,
    INCIDENT_REMEDIATION_GRADER,
    INSTRUCTION_GRADER,
    PII_GRADER,
    PLAN_ADHERENCE_GRADER,
    POLICY_GATEWAY_DEEP_GRADER,
    POLICY_GATEWAY_FAST_TRIAGE_GRADER,
    REWARD_SEEKING_GRADER,
    REWARD_TAMPERING_GRADER,
    SECURITY_GRADER,
    SEMANTIC_RUN_COMPARATOR_GRADER,
    SITUATIONAL_AWARENESS_GRADER,
    UNFAITHFUL_COT_GRADER,
    CanonicalGrader,
    evaluate_all_graders_single_pass,
    get_canonical_grader,
    list_canonical_graders,
    reset_canonical_graders,
    reset_live_canonical_grader,
    update_live_canonical_grader,
)
from engine.scorers import parse_c1_c15_output, score_to_severity

__all__ = [
    "ALIGNMENT_FAKING_GRADER",
    "APOLLO_ABLATION_GUIDANCE",
    "CANONICAL_GRADERS",
    "CITATION_GROUNDING_GRADER",
    "CREDENTIALS_GRADER",
    "DELETION_GRADER",
    "GOAL_GUARDING_SCHEMING_GRADER",
    "HALLUCINATION_GRADER",
    "INCIDENT_REMEDIATION_GRADER",
    "INSTRUCTION_GRADER",
    "PII_GRADER",
    "PLAN_ADHERENCE_GRADER",
    "POLICY_GATEWAY_DEEP_GRADER",
    "POLICY_GATEWAY_FAST_TRIAGE_GRADER",
    "REWARD_SEEKING_GRADER",
    "REWARD_TAMPERING_GRADER",
    "SECURITY_GRADER",
    "SEMANTIC_RUN_COMPARATOR_GRADER",
    "SITUATIONAL_AWARENESS_GRADER",
    "UNFAITHFUL_COT_GRADER",
    "_DEFAULT_CANONICAL_GRADERS",
    "_FACTORY_CANONICAL_GRADERS",
    "CanonicalGrader",
    "evaluate_all_graders_single_pass",
    "get_canonical_grader",
    "list_canonical_graders",
    "parse_c1_c15_output",
    "reset_canonical_graders",
    "reset_live_canonical_grader",
    "score_to_severity",
    "update_live_canonical_grader",
]


def __getattr__(name: str) -> object:
    """Proxy any unlisted attribute to the implementation package."""
    import engine.graders as _impl

    try:
        return getattr(_impl, name)
    except AttributeError:  # pragma: no cover - defensive
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
