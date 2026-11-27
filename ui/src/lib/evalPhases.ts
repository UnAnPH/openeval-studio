import { EvalPhase, RunRecord } from '../types';

export interface EvalStepperItem {
  key: 'sandbox' | 'react' | 'verifier' | 'audits';
  label: string;
  active: boolean;
  done: boolean;
  failed: boolean;
}

export interface EvalStepperState {
  hasError: boolean;
  items: EvalStepperItem[];
}

export function computeEvalStepperPhases(params: {
  phase: EvalPhase;
  status: string;
  showLive: boolean;
  isCompleted: boolean;
  stepsCount: number;
  run?: Pick<
    RunRecord,
    'status' | 'reward' | 'passed' | 'failure_reason' | 'verifier_output' | 'audit_verdicts'
  > | null;
}): EvalStepperState {
  const { phase, status, showLive, isCompleted, stepsCount, run } = params;
  const rawStatus = String(status || '');
  const rawRunStatus = String(run?.status || '');
  const terminalAbortStatuses = ['error', 'cancelled', 'timeout'];

  const verifierHasScore = run?.reward !== null && run?.reward !== undefined;
  const verifierHasEvidence = verifierHasScore || Boolean(run?.verifier_output);
  const auditsHasEvidence = Boolean(run?.audit_verdicts && run.audit_verdicts.length > 0);
  const hasAuditFailure = Boolean(run?.audit_verdicts?.some((v) => v.passed === false));

  const isAbortedRun =
    phase === 'error' ||
    terminalAbortStatuses.includes(rawStatus) ||
    terminalAbortStatuses.includes(rawRunStatus) ||
    (rawStatus === 'failed' && !verifierHasEvidence && !auditsHasEvidence) ||
    (rawRunStatus === 'failed' && !verifierHasEvidence && !auditsHasEvidence);

  const reactLikelyStarted =
    stepsCount > 0 ||
    verifierHasEvidence ||
    auditsHasEvidence ||
    Boolean(run?.failure_reason && /LLM API Error|agent|step/i.test(run.failure_reason));

  const sandboxActive = !isAbortedRun && showLive && phase === 'sandbox' && stepsCount === 0;
  const sandboxFailed = isAbortedRun && !reactLikelyStarted;
  const sandboxDone =
    !sandboxFailed &&
    (['react', 'verifier', 'audits', 'completed'].includes(phase) ||
      reactLikelyStarted ||
      (!showLive && isCompleted));

  const reactActive =
    !isAbortedRun && showLive && (phase === 'react' || (phase === 'idle' && stepsCount > 0));
  const reactFailed = isAbortedRun && reactLikelyStarted && !verifierHasEvidence && !auditsHasEvidence;
  const reactDone =
    !reactFailed &&
    (['verifier', 'audits', 'completed'].includes(phase) ||
      verifierHasEvidence ||
      auditsHasEvidence ||
      (!isAbortedRun && !showLive && stepsCount > 0));

  const verifierActive = !isAbortedRun && showLive && phase === 'verifier';
  const verifierDone =
    verifierHasEvidence || (!isAbortedRun && ['audits', 'completed'].includes(phase));
  const verifierFailed = verifierDone && run?.passed === false;

  const auditsActive = !isAbortedRun && showLive && phase === 'audits';
  const auditsDone = auditsHasEvidence || (!isAbortedRun && !showLive && isCompleted);
  const auditsFailed = auditsDone && hasAuditFailure;

  return {
    hasError: isAbortedRun,
    items: [
      { key: 'sandbox', label: '1. Sandbox Init', active: sandboxActive, done: sandboxDone, failed: sandboxFailed },
      { key: 'react', label: '2. Autonomous ReAct', active: reactActive, done: reactDone, failed: reactFailed },
      { key: 'verifier', label: '3. Pytest Verifier', active: verifierActive, done: verifierDone, failed: verifierFailed },
      { key: 'audits', label: '4. LLM Judge Audits', active: auditsActive, done: auditsDone, failed: auditsFailed },
    ],
  };
}
