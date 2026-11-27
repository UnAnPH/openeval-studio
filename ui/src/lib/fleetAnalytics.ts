/**
 * Fleet analytics helpers for the Overview dashboard.
 *
 * Canonical threat grouping, behavioural signatures, capture-source buckets and
 * operator-override-aware block predicates. Word-boundary regexes keep benign
 * words (`environment`, `prevent`, `legitimate`, `digital`, `evaluate`,
 * `establish`) out of the threat buckets.
 */

export type CanonicalThreat = { title: string; id: string };

export type SignatureSpec = {
  id: string;
  title: string;
  signature: string;
  category: string;
};

export const SIGNATURE_SPECS: Record<string, SignatureSpec> = {
  cluster_credential_exfil: {
    id: 'sig_sensitive_file_probing',
    title: 'Sensitive File & SSH Key Probing',
    signature: '.env · ~/.ssh/id_rsa · AWS credentials',
    category: 'Credential & Secret Exfiltration',
  },
  cluster_destructive_mod: {
    id: 'sig_force_push_wipe',
    title: 'Unapproved Force-Push & Schema Wipe',
    signature: 'git push --force · DROP TABLE · rm -rf',
    category: 'Destructive Resource Modification',
  },
  cluster_untrusted_exec: {
    id: 'sig_pipe_shell_miner',
    title: 'Obfuscated Pipe-to-Shell & Miner Spawn',
    signature: 'curl | bash · base64 -d · xmrig',
    category: 'Untrusted & Obfuscated Code Execution',
  },
  cluster_priv_escalation: {
    id: 'sig_priv_subnet_recon',
    title: 'Privilege Escalation & Subnet Recon',
    signature: 'sudo · raw SYN scan · roleplay override',
    category: 'Privilege Escalation & Policy Bypass',
  },
  sig_sensitive_file_probing: {
    id: 'sig_sensitive_file_probing',
    title: 'Sensitive File & SSH Key Probing',
    signature: '.env · ~/.ssh/id_rsa · AWS credentials',
    category: 'Credential & Secret Exfiltration',
  },
  sig_force_push_wipe: {
    id: 'sig_force_push_wipe',
    title: 'Unapproved Force-Push & Schema Wipe',
    signature: 'git push --force · DROP TABLE · rm -rf',
    category: 'Destructive Resource Modification',
  },
  sig_pipe_shell_miner: {
    id: 'sig_pipe_shell_miner',
    title: 'Obfuscated Pipe-to-Shell & Miner Spawn',
    signature: 'curl | bash · base64 -d · xmrig',
    category: 'Untrusted & Obfuscated Code Execution',
  },
  sig_priv_subnet_recon: {
    id: 'sig_priv_subnet_recon',
    title: 'Privilege Escalation & Subnet Recon',
    signature: 'sudo · raw SYN scan · roleplay override',
    category: 'Privilege Escalation & Policy Bypass',
  },
};

export const CANONICAL_THREATS = [
  { title: 'Credential & Secret Exfiltration', id: 'cluster_credential_exfil' },
  { title: 'Destructive Resource Modification', id: 'cluster_destructive_mod' },
  { title: 'Untrusted & Obfuscated Code Execution', id: 'cluster_untrusted_exec' },
  { title: 'Privilege Escalation & Policy Bypass', id: 'cluster_priv_escalation' },
] as const;

export const GENERIC_RULE_NAMES = new Set([
  '',
  'unclassified',
  'policy_violation',
  'unknown',
  'blocked',
]);

export const RE_SENSITIVE_SIG =
  /\b(credential|credentials|secret|secrets|exfil|exfiltration|s3_sync|env|\.env|ssh|id_rsa|token|tokens|api_key|aws|sts|pii)\b|s3:\/\//i;
export const RE_FORCE_PUSH_SIG =
  /\b(git|force_push|force push|drop table|drop_table|destructive|deletion|rm_rf|rm -rf|mkfs|wipe|truncat|truncate|schema)\b|--force/i;
export const RE_PIPE_MINER_SIG =
  /\b(remote code|rce|cryptominer|xmrig|miner|obfuscated|base64|curl_bash|pipe_shell|pipe-to-shell|untrusted_code|untrusted-fork|install\.sh)\b|curl\s*\|\s*(?:bash|sh)|base64\s+-d|\beval\b/i;
export const RE_PRIV_RECON_SIG =
  /\b(sudo|syn scan|raw syn|port_scan|recon|impersonation|roleplay|executive_impersonation|privilege|chmod|imds|169\.254|escalation|override)\b/i;

export const canonicalThreatCategory = (text: string): CanonicalThreat => {
  const lowered = (text || '').toLowerCase();
  if (RE_SENSITIVE_SIG.test(lowered)) {
    return CANONICAL_THREATS[0];
  }
  if (RE_FORCE_PUSH_SIG.test(lowered)) {
    return CANONICAL_THREATS[1];
  }
  if (RE_PIPE_MINER_SIG.test(lowered)) {
    return CANONICAL_THREATS[2];
  }
  return CANONICAL_THREATS[3];
};

export const threatCategoryForReview = (
  ruleName: string,
  ruleId: string,
  reason: string
): CanonicalThreat => {
  const primary = `${ruleName} ${ruleId}`.trim();
  if (primary && !GENERIC_RULE_NAMES.has(primary.toLowerCase())) {
    return canonicalThreatCategory(primary);
  }
  return canonicalThreatCategory(`${primary} ${reason}`);
};

export const classifySignature = (
  ruleName: string,
  ruleId: string,
  reason: string,
  toolInput: string = '',
  macroId: string = ''
): SignatureSpec => {
  const primary = `${ruleName || ''} ${ruleId || ''}`.trim();
  const fullText = `${primary} ${reason || ''} ${toolInput || ''}`;

  // 1. Primary rule name/id matching first
  if (primary && !GENERIC_RULE_NAMES.has(primary.toLowerCase())) {
    if (RE_SENSITIVE_SIG.test(primary)) return SIGNATURE_SPECS.sig_sensitive_file_probing;
    if (RE_FORCE_PUSH_SIG.test(primary)) return SIGNATURE_SPECS.sig_force_push_wipe;
    if (RE_PIPE_MINER_SIG.test(primary)) return SIGNATURE_SPECS.sig_pipe_shell_miner;
    if (RE_PRIV_RECON_SIG.test(primary)) return SIGNATURE_SPECS.sig_priv_subnet_recon;
  }

  // 2. Check full context (reason + toolInput)
  if (RE_SENSITIVE_SIG.test(fullText)) return SIGNATURE_SPECS.sig_sensitive_file_probing;
  if (RE_FORCE_PUSH_SIG.test(fullText)) return SIGNATURE_SPECS.sig_force_push_wipe;
  if (RE_PIPE_MINER_SIG.test(fullText)) return SIGNATURE_SPECS.sig_pipe_shell_miner;
  if (RE_PRIV_RECON_SIG.test(fullText)) return SIGNATURE_SPECS.sig_priv_subnet_recon;

  // 3. Fallback to macro category mapping
  if (macroId && SIGNATURE_SPECS[macroId]) {
    return SIGNATURE_SPECS[macroId];
  }

  return SIGNATURE_SPECS.sig_priv_subnet_recon;
};

export const captureSourceForAgent = (agentType?: string): { source: string; key: string } => {
  const agent = (agentType || '').toLowerCase();
  if (
    ['inspect_eval', 'openeval_studio', 'eval_studio', 'openeval_runner', 'inspect'].includes(
      agent
    )
  ) {
    return { source: 'Inspect AI / Eval Studio', key: 'inspect_ai' };
  }
  if (['claude_code', 'cursor', 'codex', 'cli', 'jsonl'].includes(agent)) {
    return { source: 'Transcript Ingest (CLI/JSONL)', key: 'transcript_ingest' };
  }
  return { source: 'Watcher Gate (Live Hooks)', key: 'watcher_gate' };
};

const FLEET_BLOCKED_DECISIONS = ['block', 'deny', 'ask', 'escalate', 'force_ask', 'reject'];

/** Operator-override-aware block predicate used for fleet review aggregation. */
export const isActiveBlockedFleetReview = (review: {
  decision?: string | null;
  score?: number | null;
  risk_score?: number | null;
  is_blocked?: boolean | null;
  human_override?: string | null;
  resolution_status?: string | null;
}): boolean => {
  if (review.human_override === 'allow') return false;
  if (review.resolution_status === 'human_approved') return false;
  if (review.is_blocked === true) return true;
  const score = Number(review.score ?? review.risk_score ?? 0) || 0;
  if (score >= 8) return true;
  return FLEET_BLOCKED_DECISIONS.includes((review.decision || '').toLowerCase());
};
