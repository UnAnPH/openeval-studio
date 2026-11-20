/**
 * Canonical decision/review predicates for the UI.
 *
 * Single source of truth for "is this verdict blocked?". Keep in lockstep with
 * the backend decision vocabulary (`block | deny | reject | force_ask`) and the
 * risk-score threshold used by the runtime gate.
 */
import type { WatcherSession } from '../types';

export type ReviewVerdict = {
  decision?: string | null;
  score?: number | null;
  risk_score?: number | null;
  human_override?: string | null;
  resolution_status?: string | null;
};

/** Full blocked/lockout vocabulary, including interactive `force_ask` lockouts. */
export const BLOCKED_DECISIONS: ReadonlySet<string> = new Set([
  'block',
  'deny',
  'reject',
  'force_ask',
]);

/**
 * Decisions that classify a *session/review* as blocked. `force_ask` is a
 * client-side lockout signal handled separately, so it is intentionally not
 * part of the session blocked predicate (preserves existing UI behavior).
 */
export const STRICT_BLOCKED_DECISIONS: ReadonlySet<string> = new Set([
  'block',
  'deny',
  'reject',
]);

/** Risk score at or above which a review is treated as blocked. */
export const BLOCKED_RISK_SCORE = 8;

export function isBlockedDecisionName(decision?: string | null): boolean {
  return STRICT_BLOCKED_DECISIONS.has(decision ?? '');
}

export function isBlockedDecision(
  decision?: string | null,
  riskScore?: number | null
): boolean {
  return isBlockedDecisionName(decision) || (riskScore || 0) >= BLOCKED_RISK_SCORE;
}

/** Decision/score-only predicate; ignores operator overrides and resolution status. */
export function isBlockedVerdict(review: ReviewVerdict): boolean {
  return isBlockedDecision(review.decision, review.score ?? review.risk_score);
}

export function isBlockedReview(review: ReviewVerdict): boolean {
  if (review.human_override === 'allow') return false;
  if (review.resolution_status === 'human_approved') return false;
  return isBlockedVerdict(review);
}

export function sessionIsBlocked(session: WatcherSession): boolean {
  const blockedCount = (session as { blocked_count?: number }).blocked_count;
  return (
    session.status === 'blocked' ||
    (session.trajectory?.reviews || []).some(isBlockedReview) ||
    Boolean(blockedCount && blockedCount > 0)
  );
}
