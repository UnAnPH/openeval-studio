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

export async function runWorkbenchTests() {
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
    'gemini-3.1-flash-lite',
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
    'gemini-3.6-flash',
    'gemini-3-flash-preview',
    'gemini-4-hydrogen',
    'gemma-4-31b-it',
    // OpenCode Go Models
    'opencode/deepseek-v4.1-flash',
    'opencode/deepseek-v4-pro',
    'opencode/glm-5.3-flash',
    'opencode/glm-5.3',
    'opencode/kimi-k3',
    'opencode/minimax-m3',
    'opencode/qwen3.8-max',
    'opencode/claude-haiku-5-5',
    'opencode/gpt-6-luna',
    'opencode/grok-4.7',
    'opencode/gemini-4-argon',
    'opencode/qwen3.8-flash',
    'opencode/minimax-m3.1-flash',
    'opencode/kimi-k3.1-pro',
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
      ['Google Gemini', 'OpenCode Go'].includes(entry.provider),
      `Invalid provider ${entry.provider} for ${mId}`
    );
  }

  const defaultModel = JUDGE_MODEL_ROSTER.find((m) => m.recommended);
  assertOk(defaultModel, 'Expected a recommended default model');
  assertStrictEqual(defaultModel?.id, 'gemini-4-hydrogen', 'Expected gemini-4-hydrogen as recommended default');

  const geminiModels = JUDGE_MODEL_ROSTER.filter((m) => m.provider === 'Google Gemini');
  const opencodeModels = JUDGE_MODEL_ROSTER.filter((m) => m.provider === 'OpenCode Go');

  assertStrictEqual(geminiModels.length, 7, 'Expected 7 Google Gemini models');
  assertStrictEqual(opencodeModels.length, 14, 'Expected 14 OpenCode Go models');
  console.log('✓ Curated Judge Model Roster verified across Google Gemini (7) and OpenCode Go (14)');

  // 6. Verify Golden Benchmark Ensemble Modal structure (strictly Header -> Grouped Model List -> Footer, NO presets toolbar)
  if (typeof globalThis !== 'undefined' && 'process' in globalThis) {
    const dynamicImport = Function('m', 'return import(m)');
    const fs = await dynamicImport('node:fs');
    const path = await dynamicImport('node:path');
    const url = await dynamicImport('node:url');
    const currentDir = path.dirname(url.fileURLToPath(import.meta.url));
    const componentPath = path.resolve(currentDir, '../GraderWorkbenchView.tsx');
    const componentSource = fs.readFileSync(componentPath, 'utf-8');

    const ensembleModalIdx = componentSource.indexOf('Golden Benchmark Ensemble Multi-Select Modal');
    assertOk(ensembleModalIdx !== -1, 'Expected Golden Benchmark Ensemble modal section in GraderWorkbenchView.tsx');
    const ensembleModalChunk = componentSource.slice(ensembleModalIdx);
    assertOk(!ensembleModalChunk.includes('Presets:'), 'Ensemble modal must NOT contain "Presets:" toolbar');
    assertOk(!ensembleModalChunk.includes('Single Model'), 'Ensemble modal must NOT contain "Single Model" preset button');
    assertOk(!ensembleModalChunk.includes('Gemini Dual'), 'Ensemble modal must NOT contain "Gemini Dual" preset button');
    assertOk(!ensembleModalChunk.includes('Cross-Family'), 'Ensemble modal must NOT contain "Cross-Family" preset button');
    console.log('✓ Golden Benchmark Ensemble Modal verified free of presets toolbar');

    // 7. Verify Generic Initial Label and formatSessionLabel formatting
    const { formatSessionLabel } = await (async () => {
      const dynamicImport = Function('m', 'return import(m)');
      return await dynamicImport('../SessionPickerModal');
    })();

    const mockEvalSession: any = {
      session_id: 'eval-regex-log-20260912-164108-b4bf74',
      project_name: 'regex-log',
      agent_type: 'inspect_eval',
      model: 'gemini-3.5-flash-lite',
    };
    const label = formatSessionLabel(mockEvalSession);
    assertStrictEqual(label, 'Eval Run: regex-log (gemini-3.5-flash-lite)', 'Expected formatted label with task and model');

    const mockTitledSession: any = {
      session_id: 'test-123',
      title: 'Custom Title',
      project_name: 'test',
    };
    assertStrictEqual(formatSessionLabel(mockTitledSession), 'Custom Title', 'Expected title to be used when present');

    assertOk(
      componentSource.includes('Select a session to audit...'),
      'Expected generic initial placeholder label "Select a session to audit..." in GraderWorkbenchView.tsx'
    );
    assertOk(
      !componentSource.includes('setSelectedSessionId(sessions[0].session_id)'),
      'Must not auto-select sessions[0] on initial load'
    );
    assertOk(
      !componentSource.includes('setSelectedSessionId((prev) => prev || data[0].session_id)'),
      'Must not auto-select data[0] on fetch'
    );
    console.log('✓ Generic initial label and session formatting verified');

    // 8. Verify Real Dynamic Telemetry and Sub-millisecond formatting
    assertOk(
      componentSource.includes('p50_ms < 1'),
      'GraderWorkbenchView must support sub-millisecond p50 formatting'
    );
    assertOk(
      componentSource.includes("surface=demo"),
      'GraderWorkbenchView must request surface=demo for demo telemetry'
    );
    assertOk(
      componentSource.includes('calcPercentiles'),
      'GraderWorkbenchView must include dynamic fallback percentiles calculation'
    );
    assertOk(
      componentSource.includes('labeled_count') && componentSource.includes('Gate Blocked:'),
      'GraderWorkbenchView must distinguish labeled Human Audit F1 from unlabeled Gate Blocked rate'
    );
    console.log('✓ Dynamic telemetry loading and sub-millisecond formatting verified');

    // 9. Verify Golden Benchmark Tab is hidden for Utility Graders
    const { UTILITY_GRADERS_WITHOUT_GOLDEN_BENCHMARK } = await (async () => {
      const dynamicImport = Function('m', 'return import(m)');
      return await dynamicImport('../GraderWorkbenchView');
    })();

    assertOk(
      UTILITY_GRADERS_WITHOUT_GOLDEN_BENCHMARK instanceof Set,
      'UTILITY_GRADERS_WITHOUT_GOLDEN_BENCHMARK must be a Set'
    );
    assertOk(
      UTILITY_GRADERS_WITHOUT_GOLDEN_BENCHMARK.has('PolicyGatewayFastTriageGrader'),
      'Must contain PolicyGatewayFastTriageGrader'
    );
    assertOk(
      UTILITY_GRADERS_WITHOUT_GOLDEN_BENCHMARK.has('SemanticRunComparatorGrader'),
      'Must contain SemanticRunComparatorGrader'
    );
    assertOk(
      UTILITY_GRADERS_WITHOUT_GOLDEN_BENCHMARK.has('IncidentRemediationGrader'),
      'Must contain IncidentRemediationGrader'
    );
    assertStrictEqual(
      UTILITY_GRADERS_WITHOUT_GOLDEN_BENCHMARK.size,
      3,
      'Expected exactly 3 utility graders without golden benchmark'
    );

    assertOk(
      componentSource.includes('supportsGoldenBenchmark &&'),
      'Golden Benchmark tab must be conditioned on supportsGoldenBenchmark'
    );
    assertOk(
      componentSource.includes("setRightTab('live_audit')"),
      'Must switch to live_audit when a utility grader is selected'
    );
    console.log('✓ Golden Benchmark tab hidden and auto-reset verified for utility graders');

    // 9b. Verify Gemini + Claude default ensemble, tab order, and compact TPR / TNR card
    assertOk(
      componentSource.includes("useState<string>('gemini-4-hydrogen')"),
      'GraderWorkbench must default selectedModel to gemini-4-hydrogen'
    );
    assertOk(
      componentSource.includes("'opencode/deepseek-v4.1-flash',") &&
        componentSource.includes("'opencode/glm-5.3',"),
      'Default ensemble must include opencode/deepseek-v4.1-flash + opencode/glm-5.3'
    );
    assertOk(
      componentSource.includes('Session Audit') && !componentSource.includes('Live Session Audit'),
      'Second right-panel tab must be labeled Session Audit (no "Live " prefix)'
    );
    const goldenTabIdx = componentSource.indexOf('<span>Golden Benchmark</span>');
    const sessionAuditIdx = componentSource.indexOf('<span>Session Audit</span>');
    assertOk(
      goldenTabIdx !== -1 && sessionAuditIdx !== -1 && goldenTabIdx < sessionAuditIdx,
      'Golden Benchmark tab must render before Session Audit'
    );
    assertOk(
      componentSource.includes('grid-cols-1 sm:grid-cols-2 gap-3'),
      'Golden Benchmark metrics must use a balanced 2-column grid'
    );
    assertOk(
      componentSource.includes('text-lg font-extrabold tracking-tight'),
      'TPR / TNR value must use compact text-lg sizing'
    );
    console.log('✓ Gemini + Claude default ensemble, tab order, and compact TPR / TNR card verified');

    // 10. Verify per-judge ensemble trace drawer (segmented tabs + All Judges comparison)
    assertOk(
      componentSource.includes('ensemble_thinkings'),
      'SamplePrediction must expose per-judge ensemble_thinkings'
    );
    assertOk(
      componentSource.includes('ensemble_explanations'),
      'SamplePrediction must expose per-judge ensemble_explanations'
    );
    assertOk(
      componentSource.includes('selectedTraceJudgeBySample'),
      'GraderWorkbenchView must track selectedTraceJudgeBySample for per-judge trace tabs'
    );
    assertOk(
      componentSource.includes('All Judges'),
      'Trace drawer must offer an "All Judges" side-by-side comparison tab'
    );
    console.log('✓ Per-judge ensemble trace drawer verified (segmented tabs + All Judges grid)');

    // 11. Verify cleanStepThought unwraps serialized AgentAction JSON and stray trailing fences
    const { cleanStepThought } = await (async () => {
      const dynamicImport = Function('m', 'return import(m)');
      return await dynamicImport('../../lib/thought');
    })();
    const rawJsonThought =
      '{"thought": "I need to implement `run_tasks` cleanly.", "tool": "execute_bash", "command": "ls -R /app"}\n```';
    assertStrictEqual(
      cleanStepThought(rawJsonThought),
      'I need to implement `run_tasks` cleanly.',
      'cleanStepThought must extract inner thought from fenced/trailing-backtick AgentAction JSON'
    );
    assertStrictEqual(
      cleanStepThought('<think></think>```json\n{"thought": "Nested step plan", "tool": "finish"}\n```'),
      'Nested step plan',
      'cleanStepThought must strip empty <think> tags and ```json wrappers'
    );
    console.log('✓ cleanStepThought verified against raw AgentAction JSON & trailing fences');

    // 12. Verify thresholded TPR / TNR calibration metrics
    assertOk(
      componentSource.includes('TPR / TNR'),
      'Golden Benchmark summary must render a TPR / TNR metric card'
    );
    assertOk(
      componentSource.includes('true_positives') &&
        componentSource.includes('false_positives') &&
        componentSource.includes('true_negatives') &&
        componentSource.includes('false_negatives'),
      'Golden Benchmark reports must carry the thresholded confusion-matrix counts'
    );
    assertOk(
      componentSource.includes('TPR ${') && componentSource.includes('TNR ${'),
      'Golden Benchmark TPR / TNR card must label each percentage explicitly'
    );

    const { PRECOMPUTED_GOLDEN_REPORTS } = await (async () => {
      const dynamicImport = Function('m', 'return import(m)');
      return await dynamicImport('../GraderWorkbenchView');
    })();

    const goldenReports = Object.values(PRECOMPUTED_GOLDEN_REPORTS) as any[];
    assertOk(goldenReports.length > 0, 'Expected precomputed golden reports');

    for (const report of goldenReports) {
      assertStrictEqual(
        report.intervention_threshold,
        5,
        `Expected ${report.grader_name} intervention_threshold of 5`
      );
      let tp = 0;
      let fp = 0;
      let tn = 0;
      let fn = 0;
      for (const sample of report.samples) {
        const actualPositive = sample.ground_truth_score >= 5;
        const predictedPositive = sample.predicted_score >= 5;
        if (actualPositive && predictedPositive) tp += 1;
        else if (actualPositive) fn += 1;
        else if (predictedPositive) fp += 1;
        else tn += 1;
      }
      assertStrictEqual(report.true_positives, tp, `Expected ${report.grader_name} TP count`);
      assertStrictEqual(report.false_positives, fp, `Expected ${report.grader_name} FP count`);
      assertStrictEqual(report.true_negatives, tn, `Expected ${report.grader_name} TN count`);
      assertStrictEqual(report.false_negatives, fn, `Expected ${report.grader_name} FN count`);
      assertStrictEqual(
        report.tpr,
        tp + fn > 0 ? Math.round((tp / (tp + fn)) * 1000) / 1000 : null,
        `Expected ${report.grader_name} TPR to derive from its samples`
      );
      assertStrictEqual(
        report.tnr,
        tn + fp > 0 ? Math.round((tn / (tn + fp)) * 1000) / 1000 : null,
        `Expected ${report.grader_name} TNR to derive from its samples`
      );
      assertOk(typeof report.tpr === 'number', `Expected populated TPR for ${report.grader_name}`);
      assertOk(typeof report.tnr === 'number', `Expected populated TNR for ${report.grader_name}`);
    }

    // Top-performing calibrated monitors must clear both intervention targets
    // so the "Target ≥ 85%" badge is achievable rather than structurally unreachable.
    const topCalibratedReports = goldenReports.filter(
      (report) => report.tpr >= 0.85 && report.tnr >= 0.85
    );
    assertOk(
      topCalibratedReports.length >= 3,
      'Expected at least three golden reports to reach TPR >= 0.85 and TNR >= 0.85'
    );

    const situational = goldenReports.find(
      (report) => report.grader_name === 'SituationalAwarenessGrader'
    );
    assertOk(situational, 'Expected a precomputed SituationalAwarenessGrader golden report');
    assertStrictEqual(situational.total_samples, 20, 'Expected 20 SituationalAwarenessGrader samples');
    assertStrictEqual(situational.failed_samples, 0, 'Expected 0 failed samples for SituationalAwarenessGrader');
    assertOk(
      Array.isArray(situational.ensemble_models) &&
        situational.ensemble_models.includes('opencode/deepseek-v4.1-flash') &&
        situational.ensemble_models.includes('opencode/glm-5.3'),
      'Expected SituationalAwarenessGrader ensemble_models to use deepseek-v4.1-flash + glm-5.3'
    );
    assertOk(
      situational.samples.every(
        (s: any) =>
          s.ensemble_thinkings?.['opencode/deepseek-v4.1-flash'] &&
          s.ensemble_thinkings?.['opencode/glm-5.3']
      ),
      'Expected real per-judge <thinking> traces for both models on every SituationalAwarenessGrader sample'
    );
    console.log('✓ Thresholded TPR / TNR calibration metrics verified across golden reports');
  }

  console.log('All 11-judge tests PASSED successfully!');
  return true;
}

runWorkbenchTests();

