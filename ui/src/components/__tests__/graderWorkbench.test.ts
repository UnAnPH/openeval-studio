import {
  ACTIVE_GRADER_NAMES,
  GRADER_METADATA,
  CATEGORIES,
  QUICK_JUMP_SECTIONS,
  FAST_TRIAGE_QUICK_JUMP,
  PAIRWISE_DIFF_QUICK_JUMP,
  SYNTHESIZER_QUICK_JUMP,
  assembleFullPrompt,
  INITIAL_GRADERS,
  JUDGE_MODEL_ROSTER,
} from '../GraderWorkbenchView';

const assertOk = (condition: unknown, msg?: string): void => {
  if (!condition) throw new Error(msg || 'Assertion failed');
};

const assertStrictEqual = (a: unknown, b: unknown, msg?: string): void => {
  if (a !== b) throw new Error(msg || `Expected ${a} === ${b}`);
};

export function runWorkbenchTests() {
  console.log('--- Running GraderWorkbench 11-Active-Judge 3-Category Verification Suite ---');

  // 1. Verify all 11 real active platform judges across 3 categories
  const expected11Graders = [
    // Scheming & Deception Scanners (Dimensions 1–3)
    'SituationalAwarenessGrader',
    'GoalGuardingSchemingGrader',
    'UnfaithfulCoTGrader',
    // Core Safety & Alignment Judges (Dimensions 4–7)
    'PlanAdherenceGrader',
    'HallucinationGrader',
    'RewardTamperingGrader',
    'RewardSeekingGrader',
    // Policy Gateway & Incident Judges (4)
    'PolicyGatewayFastTriageGrader',
    'PolicyGatewayDeepGrader',
    'SemanticRunComparatorGrader',
    'IncidentRemediationGrader',
  ];

  assertStrictEqual(INITIAL_GRADERS.length, 11, 'Expected exactly 11 initial active graders');
  assertStrictEqual(ACTIVE_GRADER_NAMES.size, 11, 'Expected 11 active grader names');
  assertStrictEqual(Object.keys(GRADER_METADATA).length, 11, 'Expected 11 grader metadata entries');

  for (const graderName of expected11Graders) {
    assertOk(ACTIVE_GRADER_NAMES.has(graderName), `Expected ACTIVE_GRADER_NAMES to contain ${graderName}`);

    const meta = GRADER_METADATA[graderName];
    assertOk(meta, `Expected metadata for ${graderName}`);
    assertOk(meta.title.length > 0, `Expected non-empty title for ${graderName}`);
    assertOk(meta.dim.length > 0, `Expected non-empty dimension for ${graderName}`);
    assertOk(meta.category.length > 0, `Expected category for ${graderName}`);
    assertOk(meta.platformRun.length > 0, `Expected platformRun for ${graderName}`);
    assertOk(meta.badgeColor.length > 0, `Expected badgeColor for ${graderName}`);

    const gDef = INITIAL_GRADERS.find((g) => g.name === graderName);
    assertOk(gDef, `Expected initial grader definition for ${graderName}`);
    if (!gDef) continue;
    assertOk(gDef.c1_axis.length > 0, `Expected c1_axis for ${graderName}`);
    assertOk(gDef.c3_definition.length > 0, `Expected c3_definition for ${graderName}`);
    assertOk(gDef.c4_directive.length > 0, `Expected c4_directive for ${graderName}`);
    assertOk(gDef.c12_rubric_table.length > 0, `Expected c12_rubric_table for ${graderName}`);
    assertOk(gDef.c15_worked_example.length > 0, `Expected c15_worked_example for ${graderName}`);
  }
  console.log('✓ All 11 Real Active Judges verified with C1-C15 fields, metadata, and platform targets');

  // 2. Verify 3 categories count matches (3 + 4 + 4 = 11)
  const scheming = Object.values(GRADER_METADATA).filter((m) => m.category === 'scheming_scanners');
  const coreSafety = Object.values(GRADER_METADATA).filter((m) => m.category === 'core_safety_judges');
  const gateway = Object.values(GRADER_METADATA).filter((m) => m.category === 'gateway_incident_judges');

  assertStrictEqual(scheming.length, 3, 'Expected 3 scheming scanners (Dimensions 1–3)');
  assertStrictEqual(coreSafety.length, 4, 'Expected 4 core safety judges (Dimensions 4–7)');
  assertStrictEqual(gateway.length, 4, 'Expected 4 gateway & incident judges');
  assertStrictEqual(CATEGORIES.length, 4, 'Expected 4 category options (All + 3 categories)');

  console.log('✓ 3 categories correctly partition the 11 active evaluators (3 + 4 + 4 = 11)');

  // 3. Verify assembleFullPrompt: Canonical C1-C15, Lean Trajectory Preset, and Decoupled Specialized Tools
  for (const grader of INITIAL_GRADERS) {
    // 3A. In 'canonical' mode, all 11 evaluators assemble C1-C15 fields
    const canonical = assembleFullPrompt(grader, 'canonical');
    assertOk(canonical.length > 100, `Canonical prompt for ${grader.name} should be substantial`);
    assertOk(canonical.includes('<thinking>'), `Canonical prompt for ${grader.name} must include <thinking>`);
    assertOk(canonical.includes('<score>'), `Canonical prompt for ${grader.name} must include <score>`);
    assertOk(canonical.includes(grader.c1_axis), `Canonical prompt for ${grader.name} must include c1_axis`);
    assertOk(canonical.includes(grader.c3_definition), `Canonical prompt for ${grader.name} must include c3_definition`);
    assertOk(canonical.includes('### Step 2: Agent Attribution'), `Canonical prompt for ${grader.name} must include Step 2 Attribution`);
    assertOk(canonical.includes('### Step 4: Scoring Rubric'), `Canonical prompt for ${grader.name} must include Step 4 Rubric`);

    // 3B. In default active mode (lean for monitors, specialized for tools):
    const active = assembleFullPrompt(grader);
    if (!grader.template_type || grader.template_type === 'apollo_trajectory') {
      assertOk(active.includes('<thinking>'), `Lean prompt for ${grader.name} must include <thinking>`);
      assertOk(active.includes('<score>'), `Lean prompt for ${grader.name} must include <score>`);
      assertOk(active.includes('### Step 1: Agent vs. User Attribution'), `Lean prompt for ${grader.name} must include Step 1 Attribution`);
      assertOk(active.includes('### Step 3: 1–10 Scoring Rubric & Adjustments'), `Lean prompt for ${grader.name} must include Step 3 Rubric`);
      assertOk(!active.includes('### Step 1: Context & Environmental Risk Factors'), `Lean prompt for ${grader.name} must drop C5/C6 context questions`);
    } else if (grader.template_type === 'fast_triage') {
      assertOk(active.includes('<thinking>'), 'Fast triage must include <thinking>');
      assertOk(active.includes('<decision_score>'), 'Fast triage must emit <decision_score>');
      assertOk(active.includes('<threat_category>'), 'Fast triage must emit <threat_category>');
    } else if (grader.template_type === 'pairwise_diff') {
      assertOk(active.includes('<thinking>'), 'Pairwise diff must include <thinking>');
      assertOk(active.includes('<divergence_turn>'), 'Pairwise diff must emit <divergence_turn>');
      assertOk(active.includes('<drift_type>'), 'Pairwise diff must emit <drift_type>');
      assertOk(active.includes('<score>'), 'Pairwise diff must emit <score>');
    } else if (grader.template_type === 'guardrail_synthesizer') {
      assertOk(active.includes('<thinking>'), 'Guardrail synthesizer must include <thinking>');
      assertOk(active.includes('<p1_actions>'), 'Guardrail synthesizer must emit <p1_actions>');
      assertOk(active.includes('<p2_interceptor_rule>'), 'Guardrail synthesizer must emit <p2_interceptor_rule>');
      assertOk(active.includes('<p2_agents_md_patch>'), 'Guardrail synthesizer must emit <p2_agents_md_patch>');
      assertOk(!active.includes('<score>'), 'Guardrail synthesizer decoupled from 1-10 rubric <score>');

      // Verify that calling assembleFullPrompt(grader, 'lean') also retains the specialized template
      const leanModePrompt = assembleFullPrompt(grader, 'lean');
      assertStrictEqual(leanModePrompt, active, `assembleFullPrompt(grader, 'lean') should return specialized template for ${grader.name}`);
    }
  }
  console.log('✓ assembleFullPrompt verified: canonical baseline, lean trajectory preset, and decoupled specialized tools');

  // 3C. Verify full_prompt_override precedence across all 11 evaluators (trajectory + specialized)
  for (const grader of INITIAL_GRADERS) {
    const customOverride = `Custom full prompt override for ${grader.name}`;
    const overridden = { ...grader, full_prompt_override: customOverride };

    // In default/lean and canonical mode, override must be returned
    assertStrictEqual(assembleFullPrompt(overridden, 'lean'), customOverride);
    assertStrictEqual(assembleFullPrompt(overridden, 'canonical'), customOverride);

    // In compressed mode, override is bypassed to generate compressed preset
    const compressed = assembleFullPrompt(overridden, 'compressed');
    assertOk(compressed !== customOverride, `Compressed mode must bypass override for ${grader.name}`);
    assertOk(compressed.includes('<thinking>'), `Compressed mode must retain <thinking> for ${grader.name}`);
  }
  console.log('✓ full_prompt_override precedence verified across all 11 evaluators');

  // 4. Verify quick-jump section references for trajectory monitors and decoupled tools
  for (const sec of QUICK_JUMP_SECTIONS) {
    const sample = assembleFullPrompt(INITIAL_GRADERS[0], 'canonical');
    assertOk(
      sample.toLowerCase().includes(sec.search.toLowerCase()),
      `Sample prompt must contain jump search: ${sec.label}`
    );
  }

  // Fast triage quick jumps
  const fastTriageGrader = INITIAL_GRADERS.find((g) => g.template_type === 'fast_triage')!;
  const fastTriagePrompt = assembleFullPrompt(fastTriageGrader, 'lean');
  for (const sec of FAST_TRIAGE_QUICK_JUMP) {
    assertOk(
      fastTriagePrompt.toLowerCase().includes(sec.search.toLowerCase()),
      `Fast triage prompt must contain jump search: ${sec.label}`
    );
  }

  // Pairwise diff quick jumps
  const pairwiseGrader = INITIAL_GRADERS.find((g) => g.template_type === 'pairwise_diff')!;
  const pairwisePrompt = assembleFullPrompt(pairwiseGrader, 'lean');
  for (const sec of PAIRWISE_DIFF_QUICK_JUMP) {
    assertOk(
      pairwisePrompt.toLowerCase().includes(sec.search.toLowerCase()),
      `Pairwise diff prompt must contain jump search: ${sec.label}`
    );
  }

  // Synthesizer quick jumps
  const synthGrader = INITIAL_GRADERS.find((g) => g.template_type === 'guardrail_synthesizer')!;
  const synthPrompt = assembleFullPrompt(synthGrader, 'lean');
  for (const sec of SYNTHESIZER_QUICK_JUMP) {
    assertOk(
      synthPrompt.toLowerCase().includes(sec.search.toLowerCase()),
      `Synthesizer prompt must contain jump search: ${sec.label}`
    );
  }

  console.log('✓ Quick-jump section anchors verified across trajectory and specialized tools');

  // 5. Verify Curated Judge Model Roster (Strict Provenance & Multi-Model Ensemble)
  const expectedModels = [
    // Google Gemini
    'gemini-3.5-flash-lite',
    'gemini-3.5-flash',
    'gemini-3.1-flash-lite',
    'gemini-3.7-flash',
    'gemini-3.8-flash',
    // OpenCode Models
    'opencode/glm-5.3-flash-free',
    'opencode/deepseek-v4.1-flash-free',
    // Cross-Family Frontier
    'gpt-4o-mini',
    'claude-3-5-sonnet',
  ];

  assertStrictEqual(JUDGE_MODEL_ROSTER.length, expectedModels.length, `Expected exactly ${expectedModels.length} curated judge models`);
  const rosterIds = new Set(JUDGE_MODEL_ROSTER.map((m) => m.id));
  assertStrictEqual(rosterIds.size, expectedModels.length, 'Expected all model IDs in JUDGE_MODEL_ROSTER to be unique');

  for (const mId of expectedModels) {
    const entry = JUDGE_MODEL_ROSTER.find((m) => m.id === mId);
    assertOk(entry, `Expected model ${mId} to be in JUDGE_MODEL_ROSTER`);
    if (!entry) continue;
    assertOk(entry.name.length > 0, `Expected non-empty name for ${mId}`);
    assertOk(entry.description.length > 0, `Expected non-empty description for ${mId}`);
    assertOk(
      ['Google Gemini', 'OpenCode (Free)', 'Cross-Family Frontier'].includes(entry.provider),
      `Invalid provider ${entry.provider} for ${mId}`
    );
  }

  const defaultModel = JUDGE_MODEL_ROSTER.find((m) => m.recommended);
  assertOk(defaultModel, 'Expected a recommended default model');
  assertStrictEqual(defaultModel?.id, 'gemini-3.5-flash-lite', 'Expected gemini-3.5-flash-lite as recommended default');

  const geminiModels = JUDGE_MODEL_ROSTER.filter((m) => m.provider === 'Google Gemini');
  const opencodeModels = JUDGE_MODEL_ROSTER.filter((m) => m.provider === 'OpenCode (Free)');
  const crossFamilyModels = JUDGE_MODEL_ROSTER.filter((m) => m.provider === 'Cross-Family Frontier');

  assertStrictEqual(geminiModels.length, 5, 'Expected 5 Google Gemini models');
  assertStrictEqual(opencodeModels.length, 2, 'Expected 2 OpenCode models');
  assertStrictEqual(crossFamilyModels.length, 2, 'Expected 2 Cross-Family Frontier models');
  console.log('✓ Curated Judge Model Roster verified across Google Gemini (5), OpenCode (2), and Cross-Family (2)');

  console.log('All 11-judge tests PASSED successfully!');
  return true;
}

runWorkbenchTests();
