import { sessionIsBlocked, sessionIsLive, sessionIsClosed } from '../SessionsView';
import { getFreshDemoSessions, getFreshDemoEvalRuns } from '../../data/demoFixtures';
import { WatcherSession } from '../../types';

const assertOk = (cond: unknown, msg: string) => {
  if (!cond) throw new Error(`[FAIL] ${msg}`);
};

const assertEqual = (a: unknown, b: unknown, msg: string) => {
  if (a !== b) throw new Error(`[FAIL] ${msg} - expected ${a} === ${b}`);
};

export function runSessionStatusTests() {
  console.log('--- Running Session Status & Mutual Exclusivity Test Suite ---');

  const now = Date.now();
  const freshTime = new Date(now - 2 * 60 * 1000).toISOString(); // 2 min ago
  const staleTime = new Date(now - 60 * 60 * 1000).toISOString(); // 60 min ago (Oct 11 scenario)

  const cleanFreshWorking: WatcherSession = {
    session_id: 'test-fresh-working',
    org_id: 'default_org',
    project_name: 'Fresh Active Project',
    agent_type: 'antigravity',
    model: 'gemini-3.7-flash',
    provider: 'google',
    status: 'working',
    total_tokens: 1000,
    total_duration_sec: 120,
    estimated_cost_usd: 0.01,
    created_at: freshTime,
    updated_at: freshTime,
    trajectory: {
      session_id: 'test-fresh-working',
      messages: [{ role: 'user', content: 'hello', timestamp: now / 1000 }],
      tool_calls: [],
      tool_results: [],
      reviews: [{ id: 'rev-1', session_id: 'test-fresh-working', timestamp: freshTime, tool_name: 'view_file', tool_input: '', decision: 'allow', score: 1, stage: 'rule', explanation: '', latency_ms: 10 }],
    },
  };

  const blockedWorking: WatcherSession = {
    session_id: 'antigravity-9f42be37',
    title: 'Telemetry And Storage Architecture Refactoring',
    org_id: 'default_org',
    project_name: 'Telemetry Refactor',
    agent_type: 'antigravity',
    model: 'gemini-3.7-flash',
    provider: 'google',
    status: 'working', // Marked 'working' by agent file, but has blocked review!
    total_tokens: 5000,
    total_duration_sec: 300,
    estimated_cost_usd: 0.05,
    created_at: staleTime,
    updated_at: staleTime,
    trajectory: {
      session_id: 'antigravity-9f42be37',
      messages: [{ role: 'user', content: 'test', timestamp: 1791747764 }],
      tool_calls: [],
      tool_results: [],
      reviews: [
        { id: 'rev-1', session_id: 'antigravity-9f42be37', timestamp: staleTime, tool_name: 'execute_bash', tool_input: 'rm -rf /', decision: 'block', score: 9, stage: 'rule', explanation: 'Destructive deletion', latency_ms: 5 },
      ],
    },
  };

  const staleWorking: WatcherSession = {
    session_id: 'antigravity-d9d8f062',
    title: 'This is a side question from the user. You MUST answer directly...',
    org_id: 'default_org',
    project_name: 'Side question',
    agent_type: 'antigravity',
    model: 'gemini-3.7-flash',
    provider: 'google',
    status: 'working', // Left as 'working' on disk from a week ago
    total_tokens: 2000,
    total_duration_sec: 60,
    estimated_cost_usd: 0.02,
    created_at: staleTime,
    updated_at: staleTime,
    trajectory: {
      session_id: 'antigravity-d9d8f062',
      messages: [{ role: 'user', content: 'question', timestamp: (now - 60 * 60 * 1000) / 1000 }],
      tool_calls: [],
      tool_results: [],
      reviews: [],
    },
  };

  const cleanCompleted: WatcherSession = {
    session_id: 'test-completed-clean',
    org_id: 'default_org',
    project_name: 'Completed Task',
    agent_type: 'claude_code',
    model: 'claude-3-7-sonnet',
    provider: 'anthropic',
    status: 'completed',
    total_tokens: 4000,
    total_duration_sec: 180,
    estimated_cost_usd: 0.03,
    created_at: freshTime,
    updated_at: freshTime,
    trajectory: {
      session_id: 'test-completed-clean',
      messages: [],
      tool_calls: [],
      tool_results: [],
      reviews: [],
    },
  };

  // Test 1: sessionIsBlocked detection
  assertOk(sessionIsBlocked(blockedWorking), 'blockedWorking must be recognized as blocked');
  assertOk(!sessionIsBlocked(cleanFreshWorking), 'cleanFreshWorking must NOT be blocked');
  assertOk(!sessionIsBlocked(staleWorking), 'staleWorking must NOT be blocked');
  assertOk(!sessionIsBlocked(cleanCompleted), 'cleanCompleted must NOT be blocked');

  // Test 2: Mutual Exclusivity - Never working and blocked
  const blockedWorkingIsLive = sessionIsLive(blockedWorking, false);
  assertEqual(blockedWorkingIsLive, false, 'Blocked session must NEVER be live/working');
  assertEqual(sessionIsBlocked(blockedWorking), true, 'Blocked session must be blocked');
  assertEqual(sessionIsClosed(blockedWorking, false), false, 'Blocked session is NOT closed');

  // Test 3: Stale session handling in Live mode
  assertEqual(sessionIsLive(staleWorking, false), false, 'Stale session > 15m old must NOT be live');
  assertEqual(sessionIsClosed(staleWorking, false), true, 'Stale session must be classified as closed');

  // Test 4: Fresh active session in Live mode
  assertEqual(sessionIsLive(cleanFreshWorking, false), true, 'Fresh active session must be live in non-demo');
  assertEqual(sessionIsClosed(cleanFreshWorking, false), false, 'Live session is NOT closed');

  // Test 5: In Demo mode, LIVE count MUST be 0
  assertEqual(sessionIsLive(cleanFreshWorking, true), false, 'Even a fresh working session must NOT be live in demo mode');
  assertEqual(sessionIsLive(blockedWorking, true), false, 'Blocked session must NOT be live in demo mode');
  assertEqual(sessionIsLive(staleWorking, true), false, 'Stale session must NOT be live in demo mode');
  assertEqual(sessionIsLive(cleanCompleted, true), false, 'Completed session must NOT be live in demo mode');

  // Test 6: Strict Partitioning - Total count invariant (sum === total)
  const testCohort: WatcherSession[] = [cleanFreshWorking, blockedWorking, staleWorking, cleanCompleted];
  for (const isDemo of [false, true]) {
    const liveCount = testCohort.filter((s) => sessionIsLive(s, isDemo)).length;
    const blockedCount = testCohort.filter((s) => sessionIsBlocked(s)).length;
    const closedCount = testCohort.filter((s) => sessionIsClosed(s, isDemo)).length;

    assertEqual(
      liveCount + blockedCount + closedCount,
      testCohort.length,
      `liveCount (${liveCount}) + blockedCount (${blockedCount}) + closedCount (${closedCount}) must exactly equal total (${testCohort.length}) [isDemo=${isDemo}]`
    );

    if (isDemo) {
      assertEqual(liveCount, 0, 'In Demo mode, LIVE count must be strictly 0');
    }
  }

  // Test 7: Bundled DEMO_SESSIONS audit
  const freshDemo = getFreshDemoSessions();
  assertOk(freshDemo.length > 0, 'DEMO_SESSIONS must contain fixtures');
  const liveDemoCount = freshDemo.filter((s) => sessionIsLive(s, true)).length;
  assertEqual(liveDemoCount, 0, 'All bundled demo sessions must have LIVE count = 0');

  const liveWorkingStatusCount = freshDemo.filter(
    (s) => s.status === 'working' || s.status === 'active' || s.status === 'running'
  ).length;
  assertEqual(liveWorkingStatusCount, 0, 'No demo sessions should have status working/active/running');

  // Test 8: OpenCode Evaluation Runs in DEMO_RUNS / DEMO_EVAL_RUNS
  const demoRuns = getFreshDemoEvalRuns();
  assertOk(demoRuns.length >= 19, `Expected at least 19 demo evaluation runs, got ${demoRuns.length}`);

  const openCodeRunIds = [
    'eval-delivery-routing-20261019-214555-ee3a2d',
    'eval-oversight-subversion-20261019-214952-0745e9',
    'eval-cancel-async-tasks-20261019-215046-1a3693',
    'eval-alignment-faking-20261019-215159-1eb0e4',
    'eval-oversight-subversion-20261019-221453-7eff19',
    'eval-oversight-subversion-20261019-221433-047ae8',
    'eval-delivery-routing-20261019-222700-71fa16',
    'eval-delivery-routing-20261019-222152-05ce56',
    'eval-cancel-async-tasks-20261019-221655-4e7d07',
    'eval-cancel-async-tasks-20261019-221551-4b6490',
    'eval-alignment-faking-20261019-221755-3848f1',
    'eval-alignment-faking-20261019-221728-30205d',
  ];

  for (const expectedId of openCodeRunIds) {
    const found = demoRuns.find((r) => r.run_id === expectedId);
    assertOk(found, `OpenCode run ${expectedId} must be present in demo eval runs`);
    assertOk(found.model.startsWith('opencode/'), `Model for ${expectedId} must start with opencode/, got ${found.model}`);
    assertEqual(found.audit_verdicts?.length, 7, `OpenCode run ${expectedId} must have all 7 safety verdicts scored`);
  }

  console.log('--- All Session Status & Mutual Exclusivity Tests Passed Cleanly ---');
}

runSessionStatusTests();
