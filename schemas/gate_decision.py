"""Canonical GateDecision Data Model for OpenEval Studio.

GateDecision is the shared typed atom across both systems:
1. Live monitoring in production (Policy Gateway / PreToolUse hooks)
2. Offline benchmark evaluation (Inspect AI EvalLogs)
"""

from schemas.watcher_models import ReviewRecord

GateDecision = ReviewRecord

__all__ = ["GateDecision"]
