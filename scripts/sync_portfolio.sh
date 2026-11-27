#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORTFOLIO_REPO="UnAnPH/openeval-studio"

echo "==> Verifying fixtures against denylist..."
python3 scripts/sanitize_demo_sessions.py --check

echo "==> Cleaning up previous workflow runs on ${PORTFOLIO_REPO}..."
RUN_IDS=$(gh api "/repos/${PORTFOLIO_REPO}/actions/runs" --jq '.workflow_runs[].id' 2>/dev/null || true)
for run_id in $RUN_IDS; do
  echo "  Deleting previous workflow run ${run_id}..."
  gh api -X DELETE "/repos/${PORTFOLIO_REPO}/actions/runs/${run_id}" >/dev/null 2>&1 || true
done

echo "==> Cleaning up releases and tags on ${PORTFOLIO_REPO}..."
RELEASES=$(gh release list -R "${PORTFOLIO_REPO}" --json tagName -q '.[].tagName' 2>/dev/null || true)
for rel in $RELEASES; do
  echo "  Deleting release ${rel}..."
  gh release delete "${rel}" -R "${PORTFOLIO_REPO}" -y --cleanup-tag >/dev/null 2>&1 || true
done

TAGS=$(git ls-remote --tags portfolio 2>/dev/null | awk -F'/' '{print $3}' | grep -E '^prod-' || true)
for tag in $TAGS; do
  echo "  Deleting tag ${tag} from portfolio..."
  git push --delete portfolio "${tag}" >/dev/null 2>&1 || true
done

echo "==> Creating single parentless snapshot root commit from HEAD^{tree}..."
SNAP_SHA=$(git commit-tree HEAD^{tree} -m "OpenEval Studio: runtime safety gate and Inspect AI eval IDE")
echo "Created snapshot root commit: ${SNAP_SHA}"

echo "==> Force-pushing single snapshot commit to portfolio remote (refs/heads/main)..."
git push --force portfolio "${SNAP_SHA}:refs/heads/main"

echo "==> Successfully synchronized single root commit ${SNAP_SHA} to portfolio remote."
