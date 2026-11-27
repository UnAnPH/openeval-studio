/** Precompiled hermetic demo fixtures + rolling timeline (see demoTimeline.ts). */
import fixtureData from './demoFixturesData.json';
import { generateTimeline } from './demoTimeline';

const RAW_SESSIONS: any[] = fixtureData.sessions;
const RAW_EVALS: any[] = fixtureData.evals;
const isBlockedDemoSession = (s: any) =>
  s.status === 'blocked' ||
  (s.trajectory?.reviews || []).some(
    (r: any) => ['block', 'deny', 'reject'].includes(r.decision) || Number(r.score) >= 8
  );

export function getFreshDemoSessions(): any[] {
  const timeline = generateTimeline();
  let activeSeeds = 0;
  return RAW_SESSIONS.map((s, idx) => {
    // Keep two non-blocked fixtures live so /demo exercises the Sessions Live tab.
    const isActive = !isBlockedDemoSession(s) && activeSeeds < 2;
    if (isActive) activeSeeds += 1;
    const ts = isActive ? timeline[activeSeeds - 1] : timeline[idx % timeline.length];
    const iso = new Date(ts).toISOString();
    return {
      ...s,
      status: isActive ? 'active' : s.status,
      current_activity: isActive
        ? !s.current_activity || s.current_activity === 'completed'
          ? 'Monitoring active tool calls'
          : s.current_activity
        : s.current_activity,
      created_at: iso,
      updated_at: new Date(ts + 5 * 60 * 1000).toISOString(),
      timestamp: iso,
      trajectory: {
        ...s.trajectory,
        messages: (s.trajectory?.messages || []).map((m: any, mIdx: number) => ({
          ...m, timestamp: (ts + mIdx * 2000) / 1000,
        })),
        reviews: (s.trajectory?.reviews || []).map((r: any, rIdx: number) => ({
          ...r, timestamp: new Date(ts + (rIdx * 2 + 1) * 1000).toISOString(),
        })),
      },
    };
  });
}
export function getFreshDemoEvalRuns(): any[] {
  const timeline = generateTimeline();
  return RAW_EVALS.map((r, idx) => {
    const ts = timeline[idx % timeline.length];
    const iso = new Date(ts).toISOString();
    return {
      ...r,
      verifier_output: (r.verifier_output as string) || undefined,
      created_at: iso,
      updated_at: new Date(ts + 5 * 60 * 1000).toISOString(),
      timestamp: iso,
    };
  });
}
export function getFreshDemoInterceptions(): any[] {
  return getFreshDemoSessions().flatMap((s) => {
    const revs = s.trajectory?.reviews || [];
    return revs
      .filter((r: any) => ['block', 'deny', 'escalate'].includes(r.decision))
      .map((r: any) => ({
        agent_id: s.agent_type || 'agent',
        session_id: s.session_id,
        decision: r.decision === 'escalate' ? 'escalate' : 'deny',
        reason: r.explanation || `Policy rule violation: ${r.rule_name || 'Firewall Gate'}`,
        risk_score: r.score ? r.score / 10.0 : 0.95,
        action_preview: r.tool_input || r.tool_name,
        full_command: r.tool_input || r.tool_name,
        rule_violation_tag: r.rule_name || 'Policy Rule Violation',
        timestamp: r.timestamp || s.created_at,
        threat_category: r.threat_category,
        is_safe: false,
        mode_applied: 'enforce',
      }));
  });
}
export const DEMO_SESSIONS: any[] = getFreshDemoSessions();
export const DEMO_EVAL_RUNS: any[] = getFreshDemoEvalRuns();
export const DEMO_EVALUATION_RUNS: any[] = DEMO_EVAL_RUNS;
export const DEMO_INTERCEPTIONS: any[] = getFreshDemoInterceptions();
