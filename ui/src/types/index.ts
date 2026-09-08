export interface ModelSpec {
  id: string;
  name: string;
  provider: 'google' | 'openai' | 'anthropic' | 'custom' | 'ollama' | 'vllm';
  tier: 'flagship' | 'balanced' | 'fast' | 'preview' | 'specialized';
  description: string;
  context_window: number;
  max_output_tokens: number;
  input_cost_per_m: number;
  output_cost_per_m: number;
  capabilities: string[];
  is_default?: boolean;
}

export interface TaskSummary {
  task_id: string;
  category: string | null;
  difficulty: string | null;
  tags: string[];
  timeout_sec: number;
  max_steps: number;
  memory_mb: number;
  instruction_preview: string;
}

export interface AgentAction {
  thought: string;
  tool: 'execute_bash' | 'view_file' | 'write_file' | 'finish' | string;
  command?: string;
  path?: string;
  content?: string;
  summary?: string;
  firewall_blocked?: boolean;
  firewall_reason?: string;
}

export interface AgentStep {
  step_number: number;
  thought: string;
  action: AgentAction;
  observation: string;
  latency_ms: number;
  tokens_used: number;
  firewall_blocked?: boolean;
  firewall_reason?: string;
}

export interface JudgeVerdict {
  metric_name:
    | 'plan_adherence'
    | 'hallucination_detection'
    | 'reward_tampering'
    | 'situational_awareness'
    | 'goal_guarding_scheming'
    | 'unfaithful_cot'
    | 'citation_grounding'
    | string;
  score: number;
  passed: boolean;
  reasoning: string;
  flagged_steps: number[];
  discrepancy_details?: string;
  overridden?: boolean;
  override_reason?: string;
}

export interface RunRecord {
  run_id: string;
  task_id: string;
  model: string;
  provider: string;
  status: 'pending' | 'running' | 'completed' | 'error' | 'max_steps_exceeded' | 'cancelled';
  created_at: string;
  steps: AgentStep[];
  total_steps: number;
  total_tokens: number;
  total_duration_sec: number;
  estimated_cost_usd: number;
  final_summary: string | null;
  reward: number | null;
  passed: boolean | null;
  failure_reason: string | null;
  audit_verdicts?: JudgeVerdict[];
  audit_overrides?: Record<string, boolean>;
  human_review_notes?: string;
  human_reviewer?: string;
  revised_at?: string;
  chaos_mode?: boolean;
}

export type MainNavTab =
  | 'dashboard'
  | 'overview'
  | 'sessions'
  | 'control'
  | 'policy'
  /** @deprecated use control */
  | 'firewall'
  /** @deprecated use policy */
  | 'watcher_live'
  | 'transcripts'
  | 'graders'
  | 'incident_detail'
  | 'runs'
  | 'run_detail'
  | 'benchmarks'
  | 'test_cases'
  | 'studio'
  | 'compare'
  | 'inspect';

export interface WatcherConfig {
  mode: 'enforce' | 'observe' | 'paused';
  deny_threshold: number;
  flag_threshold: number;
  fail_open: boolean;
  fallback_decision: 'allow' | 'escalate';
  timeout_sec: number;
}

export interface WatcherVerdict {
  decision: 'allow' | 'deny' | 'escalate';
  stage: 'stage_1_readonly' | 'stage_2_deterministic' | 'stage_3_triage' | 'stage_4_deep_review' | 'fallback';
  reason: string;
  risk_score: number;
  latency_ms: number;
  rule_violation_tag: string | null;
  mode_applied: 'enforce' | 'observe' | 'paused';
  shadow_decision: 'allow' | 'deny' | 'escalate' | null;
  is_safe: boolean;
  action_preview: string;
  agent_id: string;
  timestamp: string;
}

export interface RecommendedAction {
  priority: 'P1' | 'P2' | 'P3';
  category: 'INVESTIGATE' | 'HARDEN_AGENTS_MD' | 'REMEDIATE_CODE';
  title: string;
  description: string;
  code_snippet?: string | null;
  citations: string[];
}

export interface FindingRecord {
  id: string;
  session_id: string;
  headline: string;
  developer: string;
  timestamp: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  dimension: string;
  agent_source: 'antigravity' | 'claude_code' | 'openeval_runner';
  summary: string;
  recommended_actions: RecommendedAction[];
  flagged_turns: number[];
  blocked_turn?: number | null;
  tags: string[];
}

export interface IncidentTurn {
  step_number: number;
  role: 'user' | 'agent';
  content?: string;
  thought?: string;
  tool?: string;
  arguments?: Record<string, unknown>;
  observation?: string;
  is_blocked?: boolean;
  rule_violation_tag?: string;
  risk_score?: number;
  reason?: string;
}

export interface IncidentSessionDetail {
  session_id: string;
  finding: FindingRecord;
  turns: IncidentTurn[];
  total_turns: number;
  blocked_turns_count: number;
  developer: string;
  agent_source: string;
  working_directory: string;
  duration_sec: number;
  total_tokens: number;
}

export interface WatcherReviewRecord {
  id: string;
  session_id: string;
  timestamp: string;
  tool_name: string;
  tool_input: string;
  decision: 'allow' | 'warn' | 'block' | 'modify' | 'escalate';
  score: number;
  stage: 'rule' | 'threshold' | 'triage' | 'deep_review';
  rule_name?: string | null;
  explanation: string;
  diff?: string | null;
  latency_ms: number;
}

export interface WatcherSession {
  session_id: string;
  title?: string | null;
  run_id?: string;
  org_id: string;
  project_name: string;
  task_id?: string;
  agent_type: 'antigravity' | 'claude_code' | 'cursor' | 'inspect_eval' | 're_act_agent' | 'custom';
  model: string;
  provider: string;
  status: 'pending' | 'active' | 'working' | 'completed' | 'error' | 'cancelled' | 'parked';
  working_dir?: string | null;
  current_activity?: string | null;
  trajectory: {
    session_id: string;
    messages: Array<{ role: string; content: string; thinking?: string; timestamp?: number }>;
    tool_calls: Array<{ tool_id: string; tool_name: string; arguments: Record<string, unknown>; raw_input?: string; timestamp?: number }>;
    tool_results: Array<{ tool_id: string; tool_name: string; stdout: string; stderr: string; exit_code: number; duration_ms: number; is_error: boolean }>;
    reviews: WatcherReviewRecord[];
  };
  passed?: boolean | null;
  reward?: number | null;
  total_tokens: number;
  total_duration_sec: number;
  estimated_cost_usd: number;
  failure_reason?: string | null;
  final_summary?: string | null;
  human_verdict_override?: 'PASS' | 'FAIL' | null;
  human_notes?: string | null;
  human_reviewer?: string | null;
  audit_overrides?: Record<string, boolean>;
  created_at: string;
  updated_at: string;
}

export interface WatcherCommandRule {
  id: string;
  category: 'git' | 'security' | 'fs' | string;
  name: string;
  pattern: string;
  action: 'allow' | 'triage' | 'human' | 'deny' | 'off';
  description: string;
  is_custom: boolean;
}

export interface WatcherToolThreshold {
  tool_name: string;
  auto_approve: boolean;
  escalate_ge?: number | null;
  auto_deny_ge?: number | null;
  always_escalate: boolean;
  auto_approve_le?: number;
}

export interface WatcherPolicy {
  policy_id: string;
  name: string;
  org_id: string;
  posture: 'blocking' | 'trailing' | 'disabled';
  command_rules: WatcherCommandRule[];
  tool_thresholds: WatcherToolThreshold[];
}


