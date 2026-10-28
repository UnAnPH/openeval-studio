import {
  ACTIVE_GRADER_NAMES,
  GRADER_METADATA,
  CATEGORIES,
  QUICK_JUMP_SECTIONS,
  assembleFullPrompt,
  INITIAL_GRADERS,
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

  // 3. Verify assembleFullPrompt for all 11 graders
  for (const grader of INITIAL_GRADERS) {
    const full = assembleFullPrompt(grader);
    assertOk(full.length > 100, `Full prompt for ${grader.name} should be substantial`);
    assertOk(full.includes('<thinking>'), `Full prompt for ${grader.name} must include <thinking>`);
    assertOk(full.includes('<score>'), `Full prompt for ${grader.name} must include <score>`);
    assertOk(full.includes(grader.c1_axis), `Full prompt for ${grader.name} must include c1_axis`);
    assertOk(full.includes(grader.c3_definition), `Full prompt for ${grader.name} must include c3_definition`);
    assertOk(full.includes('### Step 2: Agent Attribution'), `Full prompt for ${grader.name} must include Step 2 Attribution`);
    assertOk(full.includes('### Step 4: Scoring Rubric'), `Full prompt for ${grader.name} must include Step 4 Rubric`);
  }
  console.log('✓ assembleFullPrompt verified for all 11 judges');

  // 4. Verify quick-jump section references
  for (const sec of QUICK_JUMP_SECTIONS) {
    const sample = assembleFullPrompt(INITIAL_GRADERS[0]);
    assertOk(
      sample.toLowerCase().includes(sec.search.toLowerCase()),
      `Sample prompt must contain jump search: ${sec.label}`
    );
  }
  console.log('✓ Quick-jump section anchors verified');

  console.log('All 11-judge tests PASSED successfully!');
  return true;
}

runWorkbenchTests();
