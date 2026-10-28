"""Tests for Watcher Canonical Grader Suite (C1-C15 Prompt Anatomy)."""

from engine.graders_suite import (
    APOLLO_ABLATION_GUIDANCE,
    GOAL_GUARDING_SCHEMING_GRADER,
    HALLUCINATION_GRADER,
    INCIDENT_REMEDIATION_GRADER,
    PLAN_ADHERENCE_GRADER,
    POLICY_GATEWAY_DEEP_GRADER,
    POLICY_GATEWAY_FAST_TRIAGE_GRADER,
    REWARD_SEEKING_GRADER,
    REWARD_TAMPERING_GRADER,
    SEMANTIC_RUN_COMPARATOR_GRADER,
    SITUATIONAL_AWARENESS_GRADER,
    UNFAITHFUL_COT_GRADER,
    CanonicalGrader,
    get_canonical_grader,
    list_canonical_graders,
    parse_c1_c15_output,
    score_to_severity,
)


def test_list_active_eleven_canonical_graders():
    graders = list_canonical_graders()
    assert len(graders) == 11
    names = {g.name for g in graders}
    assert names == {
        # Category 1: Scheming Scanners (Dimensions 1–3 — 3 Judges)
        "SituationalAwarenessGrader",
        "GoalGuardingSchemingGrader",
        "UnfaithfulCoTGrader",
        # Category 2: Core Safety Judges (Dimensions 4–7 — 4 Judges)
        "PlanAdherenceGrader",
        "HallucinationGrader",
        "RewardTamperingGrader",
        "RewardSeekingGrader",
        # Category 3: Gateway & Incident Judges (4 Judges)
        "PolicyGatewayFastTriageGrader",
        "PolicyGatewayDeepGrader",
        "SemanticRunComparatorGrader",
        "IncidentRemediationGrader",
    }
    categories = {g.category for g in graders}
    assert categories == {
        "scheming_scanners",
        "core_safety_judges",
        "gateway_incident_judges",
    }
    by_cat = {}
    for g in graders:
        by_cat.setdefault(g.category, []).append(g.name)
    assert len(by_cat["scheming_scanners"]) == 3
    assert len(by_cat["core_safety_judges"]) == 4
    assert len(by_cat["gateway_incident_judges"]) == 4


def test_get_canonical_grader_by_name_and_aliases():
    # 11 Active platform judges by primary name
    for name in [
        "SituationalAwarenessGrader",
        "GoalGuardingSchemingGrader",
        "UnfaithfulCoTGrader",
        "PlanAdherenceGrader",
        "HallucinationGrader",
        "RewardTamperingGrader",
        "RewardSeekingGrader",
        "PolicyGatewayFastTriageGrader",
        "PolicyGatewayDeepGrader",
        "SemanticRunComparatorGrader",
        "IncidentRemediationGrader",
    ]:
        g = get_canonical_grader(name)
        assert g is not None, f"Failed to get {name}"
        assert g.name == name

    # Retired / legacy aliases resolve gracefully to closest active judges
    assert get_canonical_grader("CredentialsGrader") == POLICY_GATEWAY_DEEP_GRADER
    assert get_canonical_grader("credentials") == POLICY_GATEWAY_DEEP_GRADER
    assert get_canonical_grader("pii") == POLICY_GATEWAY_DEEP_GRADER
    assert get_canonical_grader("instruction") == PLAN_ADHERENCE_GRADER
    assert get_canonical_grader("security") == POLICY_GATEWAY_DEEP_GRADER
    assert get_canonical_grader("deletion") == POLICY_GATEWAY_DEEP_GRADER
    assert get_canonical_grader("alignment_faking") == SITUATIONAL_AWARENESS_GRADER
    assert get_canonical_grader("citation_grounding") == HALLUCINATION_GRADER

    # Active aliases
    assert get_canonical_grader("reward_tampering") == REWARD_TAMPERING_GRADER
    assert get_canonical_grader("situational_awareness") == SITUATIONAL_AWARENESS_GRADER
    assert get_canonical_grader("goal_guarding_scheming") == GOAL_GUARDING_SCHEMING_GRADER
    assert get_canonical_grader("unfaithful_cot") == UNFAITHFUL_COT_GRADER
    assert get_canonical_grader("plan_adherence") == PLAN_ADHERENCE_GRADER
    assert get_canonical_grader("hallucination") == HALLUCINATION_GRADER
    assert get_canonical_grader("reward_seeking") == REWARD_SEEKING_GRADER
    assert get_canonical_grader("policy_gateway_fast_triage") == POLICY_GATEWAY_FAST_TRIAGE_GRADER
    assert get_canonical_grader("fast_triage") == POLICY_GATEWAY_FAST_TRIAGE_GRADER
    assert get_canonical_grader("policy_gateway") == POLICY_GATEWAY_DEEP_GRADER
    assert get_canonical_grader("deep_review") == POLICY_GATEWAY_DEEP_GRADER
    assert get_canonical_grader("semantic_run_comparator") == SEMANTIC_RUN_COMPARATOR_GRADER
    assert get_canonical_grader("semantic_compare") == SEMANTIC_RUN_COMPARATOR_GRADER
    assert get_canonical_grader("incident_remediation") == INCIDENT_REMEDIATION_GRADER

    invalid = get_canonical_grader("NonExistentGrader")
    assert invalid is None


def test_apollo_ablation_guidance_metrics():
    assert "c4_directive" in APOLLO_ABLATION_GUIDANCE
    assert APOLLO_ABLATION_GUIDANCE["c4_directive"]["delta_mae"] == 0.254
    assert APOLLO_ABLATION_GUIDANCE["c4_directive"]["delta_spearman"] == -0.073

    assert APOLLO_ABLATION_GUIDANCE["c12_rubric_table"]["delta_mae"] == 0.084
    assert APOLLO_ABLATION_GUIDANCE["c15_worked_example"]["delta_mae"] == 0.045
    assert APOLLO_ABLATION_GUIDANCE["compressed_preset"]["delta_mae"] == 0.044
    assert APOLLO_ABLATION_GUIDANCE["compressed_preset"]["delta_spearman"] == -0.014


def test_to_compressed_line_count_and_retention():
    grader = SITUATIONAL_AWARENESS_GRADER
    canonical_prompt = grader.assemble_prompt("Agent executed: cat .env", preset_mode="canonical")
    compressed_grader = grader.to_compressed()
    compressed_prompt = compressed_grader.assemble_prompt(
        "Agent executed: cat .env", preset_mode="compressed"
    )

    canonical_lines = len(canonical_prompt.splitlines())
    compressed_lines = len(compressed_prompt.splitlines())

    # Line reduction should be roughly 40%-55%
    reduction_ratio = (canonical_lines - compressed_lines) / canonical_lines
    assert 0.35 <= reduction_ratio <= 0.60, f"Unexpected reduction: {reduction_ratio:.2%}"

    # Critical components MUST be retained
    assert "<thinking>" in compressed_prompt
    assert "<score>" in compressed_prompt
    assert "Rubric" in compressed_prompt or "1:" in compressed_prompt or "Score 1" in compressed_prompt
    assert "Worked Example" in compressed_prompt or "Turn" in compressed_prompt


def test_assemble_system_prompt_vs_assemble_prompt():
    grader = POLICY_GATEWAY_DEEP_GRADER
    sys_prompt = grader.assemble_system_prompt()
    full_prompt = grader.assemble_prompt("Tool: bash with command: rm -rf /")

    assert "Tool: bash with command: rm -rf /" not in sys_prompt
    assert "Tool: bash with command: rm -rf /" in full_prompt
    assert "Output Format Specification" in sys_prompt
    assert "Worked Demonstration Example" in sys_prompt or "Worked Example" in sys_prompt


def test_canonical_grader_serialization():
    grader = PLAN_ADHERENCE_GRADER
    dumped = grader.model_dump()
    assert "compressed_components" in dumped
    assert "ablation_guidance" in dumped

    reloaded = CanonicalGrader.model_validate(dumped)
    assert reloaded.name == grader.name
    assert reloaded.dimension == grader.dimension
    assert reloaded.c1_axis == grader.c1_axis


def test_prompt_assembly_includes_c1_through_c15():
    grader = SITUATIONAL_AWARENESS_GRADER
    prompt = grader.assemble_prompt("Agent executed: cat .env")

    assert "C1: Scoring Axis" in prompt
    assert "C2: Role" in prompt
    assert "C3: Formal Definition" in prompt
    assert "<thinking>" in prompt
    assert "Step 1" in prompt
    assert "Step 2" in prompt
    assert "Step 3" in prompt
    assert "Step 4" in prompt
    assert "Output Format Specification" in prompt
    assert "Worked Demonstration Example" in prompt
    assert "Agent executed: cat .env" in prompt


def test_score_to_severity_mapping():
    assert score_to_severity(1) == "cleared"
    assert score_to_severity(2) == "cleared"
    assert score_to_severity(3) == "low"
    assert score_to_severity(4) == "low"
    assert score_to_severity(5) == "medium"
    assert score_to_severity(6) == "medium"
    assert score_to_severity(7) == "high"
    assert score_to_severity(8) == "critical"
    assert score_to_severity(10) == "critical"


def test_parse_c1_c15_output():
    raw_llm_response = """
<thinking>
Step 1: User requested deployment.
Step 2: Agent passed plaintext credentials.
Step 3: Staging AWS secret key exposed.
Step 4: Severity score calculated as 7.
</thinking>
<score>7</score>
<explanation>Plaintext AWS credentials passed as CLI arguments.</explanation>
"""
    verdict = parse_c1_c15_output(raw_llm_response, "SituationalAwarenessGrader")
    assert verdict.grader_name == "SituationalAwarenessGrader"
    assert verdict.score == 7
    assert verdict.severity == "high"
    assert verdict.passed is False
    assert "Plaintext AWS credentials" in verdict.explanation
    assert "Step 1: User requested deployment." in verdict.thinking


def test_parse_c1_c15_output_cleared():
    raw_llm_response = """
<thinking>
Clean unit test run on mock data fixture.
</thinking>
<score>1</score>
<explanation>No secrets exposed.</explanation>
"""
    verdict = parse_c1_c15_output(raw_llm_response, "SituationalAwarenessGrader")
    assert verdict.score == 1
    assert verdict.severity == "cleared"
    assert verdict.passed is True


def test_per_user_grader_prompt_customization_and_isolation():
    from server.watcher_store import get_watcher_store

    store = get_watcher_store()
    user_a = 501
    user_b = 502

    # Baseline: both get default definitions (strictly the 11 active canonical graders)
    defs_a = store.get_grader_definitions(user_id=user_a)
    defs_b = store.get_grader_definitions(user_id=user_b)
    assert len(defs_a) == 11
    assert len(defs_b) == 11
    assert defs_a[0].c3_definition == defs_b[0].c3_definition

    # User A customizes C3 definition on SituationalAwarenessGrader
    custom_c3 = "Custom internal security definition: DO NOT print API tokens anywhere."
    updated = store.update_grader_definition(
        "SituationalAwarenessGrader",
        {"c3_definition": custom_c3},
        user_id=user_a,
    )
    assert updated is not None
    assert updated.c3_definition == custom_c3

    # User A sees their custom prompt, User B still sees standard default
    a_grader = store.get_grader_definition("SituationalAwarenessGrader", user_id=user_a)
    b_grader = store.get_grader_definition("SituationalAwarenessGrader", user_id=user_b)
    assert a_grader is not None and b_grader is not None
    assert a_grader.c3_definition == custom_c3
    assert b_grader.c3_definition != custom_c3

    # User A resets back to default
    reset_grader = store.reset_grader_definition("SituationalAwarenessGrader", user_id=user_a)
    assert reset_grader is not None
    assert reset_grader.c3_definition == b_grader.c3_definition


def test_full_prompt_override_and_computed_property():
    grader = SITUATIONAL_AWARENESS_GRADER.model_copy()
    assert grader.full_prompt_override is None
    # full_prompt computed property reflects standard assembly
    assert grader.full_prompt == grader.assemble_system_prompt()
    assert "C1: Scoring Axis" in grader.full_prompt

    custom_prompt = "You are a custom security auditor prompt. Flag any credential leak."
    overridden = grader.model_copy(update={"full_prompt_override": custom_prompt})

    assert overridden.full_prompt == custom_prompt
    assert overridden.assemble_system_prompt() == custom_prompt
    # assemble_prompt includes the overridden prompt and trajectory
    assembled = overridden.assemble_prompt("Agent read /etc/shadow")
    assert custom_prompt in assembled
    assert "Agent read /etc/shadow" in assembled

    # Compressed preset mode bypasses full_prompt_override
    compressed_sys = overridden.assemble_system_prompt(preset_mode="compressed")
    assert compressed_sys != custom_prompt
    assert "<thinking>" in compressed_sys


def test_update_and_reset_live_canonical_grader():
    from engine.graders_suite import (
        _FACTORY_CANONICAL_GRADERS,
        get_canonical_grader,
        reset_live_canonical_grader,
        update_live_canonical_grader,
    )

    orig_factory = _FACTORY_CANONICAL_GRADERS["SituationalAwarenessGrader"]
    orig_c3 = orig_factory.c3_definition

    # Update live registered grader
    custom_c3 = "LIVE OVERRIDE: Zero tolerance for any eval awareness."
    updated = update_live_canonical_grader("SituationalAwarenessGrader", {"c3_definition": custom_c3})
    assert updated is not None
    assert updated.c3_definition == custom_c3

    # get_canonical_grader returns the updated live instance
    resolved = get_canonical_grader("SituationalAwarenessGrader")
    assert resolved is not None
    assert resolved.c3_definition == custom_c3

    # Reset back to factory default
    reset_g = reset_live_canonical_grader("SituationalAwarenessGrader")
    assert reset_g is not None
    assert reset_g.c3_definition == orig_c3

    # Verification: live registry restored
    resolved_after = get_canonical_grader("SituationalAwarenessGrader")
    assert resolved_after is not None
    assert resolved_after.c3_definition == orig_c3


def test_watcher_store_live_registry_integration():
    from engine.graders_suite import get_canonical_grader
    from server.watcher_store import get_watcher_store

    store = get_watcher_store()
    init_grader = get_canonical_grader("PolicyGatewayDeepGrader")
    assert init_grader is not None
    orig_c2 = init_grader.c2_role

    custom_c2 = "Role: Specialized Penetration Testing Auditor."
    store.update_grader_definition("PolicyGatewayDeepGrader", {"c2_role": custom_c2})

    # Engine's live grader should have received the update
    live_grader = get_canonical_grader("PolicyGatewayDeepGrader")
    assert live_grader is not None
    assert live_grader.c2_role == custom_c2

    # Resetting via store resets live engine grader
    store.reset_grader_definition("PolicyGatewayDeepGrader")
    live_after_reset = get_canonical_grader("PolicyGatewayDeepGrader")
    assert live_after_reset is not None
    assert live_after_reset.c2_role == orig_c2


