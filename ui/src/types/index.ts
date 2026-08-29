export interface ModelSpec {
  id: string;
  name: string;
  provider: 'google' | 'openai' | 'anthropic' | 'custom';
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
  metric_name: 'plan_adherence' | 'hallucination_detection' | 'reward_tampering' | 'citation_grounding' | string;
  score: number;
  passed: boolean;
  reasoning: string;
  flagged_steps: number[];
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

export type MainNavTab = 'overview' | 'graph' | 'test_cases' | 'compare' | 'studio' | 'inspect';


