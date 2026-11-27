"""Canonical C1–C15 grader package for OpenEval Studio.

The former single-file ``engine/graders_suite.py`` (~4,000 lines) is split here by
category so each module stays reviewable:

- :mod:`engine.graders.models` — :class:`CanonicalGrader`, prompt assembly, and the
  Apollo ablation guidance table (C1–C15 prompt anatomy).
- :mod:`engine.graders.safety_graders` — credentials, PII, security, deletion, and
  alignment-faking graders.
- :mod:`engine.graders.quality_graders` — instruction/plan fidelity, grounding,
  reward-tampering, and reward-seeking graders.
- :mod:`engine.graders.policy_graders` — scheming scanners plus the policy-gateway
  and incident-remediation tools.
- :mod:`engine.graders.registry` — the live ``CANONICAL_GRADERS`` registry, alias
  resolution, reset/update helpers, and single-pass batch evaluation.

``engine.graders_suite`` remains a thin backward-compatible facade over this package;
no caller import path changed.
"""

from __future__ import annotations

from engine.graders.models import APOLLO_ABLATION_GUIDANCE, CanonicalGrader
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
from engine.graders.registry import (
    _ALL_CANONICAL_GRADERS,
    _DEFAULT_CANONICAL_GRADERS,
    _FACTORY_CANONICAL_GRADERS,
    CANONICAL_GRADERS,
    _batch_grader_rubrics,
    _concise_grader_definition,
    evaluate_all_graders_single_pass,
    get_canonical_grader,
    list_canonical_graders,
    reset_canonical_graders,
    reset_live_canonical_grader,
    update_live_canonical_grader,
)
from engine.graders.safety_graders import (
    ALIGNMENT_FAKING_GRADER,
    CREDENTIALS_GRADER,
    DELETION_GRADER,
    PII_GRADER,
    SECURITY_GRADER,
)

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
    "_ALL_CANONICAL_GRADERS",
    "_DEFAULT_CANONICAL_GRADERS",
    "_FACTORY_CANONICAL_GRADERS",
    "_batch_grader_rubrics",
    "_concise_grader_definition",
    "CanonicalGrader",
    "evaluate_all_graders_single_pass",
    "get_canonical_grader",
    "list_canonical_graders",
    "reset_canonical_graders",
    "reset_live_canonical_grader",
    "update_live_canonical_grader",
]


def __getattr__(name: str) -> object:
    """Proxy any unlisted attribute to the implementation package."""
    import engine.graders as _impl

    try:
        return getattr(_impl, name)
    except AttributeError:  # pragma: no cover - defensive
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
