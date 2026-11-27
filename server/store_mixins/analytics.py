"""Analytics and threat-grouping mixin for the Watcher store.

Extracted from :mod:`server.watcher_store`. Module-level helpers stay importable
from ``server.watcher_store`` (it re-exports them) for callers and tests.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from schemas.watcher_models import Session


def _watcher_module() -> Any:  # noqa: ANN401 - lazy ref keeps module monkeypatches live
    """Resolve ``server.watcher_store`` lazily so module monkeypatches still apply."""
    from server import watcher_store

    return watcher_store


# Word-boundary patterns keep benign words (``environment``, ``prevent``,
# ``legitimate``, ``digital``, ``evaluate``, ``establish``) out of the
# threat buckets while still matching real signals such as ``.env`` or ``s3://``.
_RE_CREDENTIAL_EXFIL = re.compile(
    r"\b(credential|credentials|secret|secrets|exfil|exfiltration|s3_sync|env|ssh|id_rsa|token|tokens|api_key|aws|sts|pii)\b|\.env\b|s3://",
    re.IGNORECASE,
)
_RE_DESTRUCTIVE_MOD = re.compile(
    r"\b(git|force_push|force push|drop table|drop_table|destructive|deletion|rm_rf|rm -rf|mkfs|wipe|truncat|truncate|schema)\b|--force",
    re.IGNORECASE,
)
_RE_UNTRUSTED_EXEC = re.compile(
    r"\b(remote code|rce|cryptominer|xmrig|miner|obfuscated|base64|curl_bash|pipe_shell|pipe-to-shell|untrusted_code|untrusted-fork|install\.sh|eval)\b|curl\s*\|\s*(?:bash|sh)",
    re.IGNORECASE,
)


def _canonical_threat_category(
    rule_name: str | None,
    rule_id: str | None = "",
    reason: str | None = "",
) -> tuple[str, str]:
    """Map granular rule names/IDs into canonical Watcher threat categories."""
    text = f"{rule_name or ''} {rule_id or ''} {reason or ''}"
    if _RE_CREDENTIAL_EXFIL.search(text):
        return ("Credential & Secret Exfiltration", "cluster_credential_exfil")
    if _RE_DESTRUCTIVE_MOD.search(text):
        return ("Destructive Resource Modification", "cluster_destructive_mod")
    if _RE_UNTRUSTED_EXEC.search(text):
        return ("Untrusted & Obfuscated Code Execution", "cluster_untrusted_exec")
    return ("Privilege Escalation & Policy Bypass", "cluster_priv_escalation")


def _is_active_blocked_review(r: Any) -> bool:  # noqa: ANN401 - accepts DB rows and model records
    """Return ``True`` only for reviews that still represent an active block.

    Operator overrides (``human_override="allow"``) and human-approved
    resolutions must be excluded from block/lockout telemetry.
    """
    if getattr(r, "human_override", None) == "allow":
        return False
    if getattr(r, "resolution_status", None) == "human_approved":
        return False
    if getattr(r, "decision", None) in (
        "block",
        "deny",
        "reject",
        "ask",
        "escalate",
        "force_ask",
    ):
        return True
    return int(getattr(r, "score", 0) or 0) >= 8


def _classify_threat_row(
    rule_name: str | None, rule_id: str | None, reason: str | None
) -> tuple[str, str]:
    """Classify by rule identity first so generic words in explanations do not skew buckets."""
    primary = f"{rule_name or ''} {rule_id or ''}".strip()
    if primary.lower() not in ("", "unclassified", "policy_violation", "unknown", "blocked"):
        return _canonical_threat_category(rule_name, rule_id, "")
    return _canonical_threat_category(rule_name, rule_id, reason)


def _capture_source_bucket(agent_type: str | None) -> tuple[str, str]:
    """Map a session agent_type to one of three fleet capture sources."""
    agent = (agent_type or "").strip().lower()
    if agent in (
        "inspect_eval",
        "openeval_studio",
        "eval_studio",
        "openeval_runner",
        "inspect",
    ):
        return ("Inspect AI / Eval Studio", "inspect_ai")
    if agent in ("claude_code", "cursor", "codex", "cli", "jsonl"):
        return ("Transcript Ingest (CLI/JSONL)", "transcript_ingest")
    return ("Watcher Gate (Live Hooks)", "watcher_gate")


def _build_capture_sources(counts: dict[str, int], total_sessions: int) -> list[dict[str, Any]]:
    """Build the canonical 3-bucket capture-source breakdown."""
    denominator = max(total_sessions, 1)
    return [
        {
            "source": "Watcher Gate (Live Hooks)",
            "count": counts.get("watcher_gate", 0),
            "key": "watcher_gate",
            "session_count": counts.get("watcher_gate", 0),
            "pct": round((counts.get("watcher_gate", 0) / denominator) * 100.0, 1),
        },
        {
            "source": "Transcript Ingest (CLI/JSONL)",
            "count": counts.get("transcript_ingest", 0),
            "key": "transcript_ingest",
            "session_count": counts.get("transcript_ingest", 0),
            "pct": round((counts.get("transcript_ingest", 0) / denominator) * 100.0, 1),
        },
        {
            "source": "Inspect AI / Eval Studio",
            "count": counts.get("inspect_ai", 0),
            "key": "inspect_ai",
            "session_count": counts.get("inspect_ai", 0),
            "pct": round((counts.get("inspect_ai", 0) / denominator) * 100.0, 1),
        },
    ]


SIGNATURE_SPECS: dict[str, dict[str, str]] = {
    "sig_sensitive_file_probing": {
        "pattern_id": "sig_sensitive_file_probing",
        "title": "Sensitive File & SSH Key Probing",
        "signature": ".env · ~/.ssh/id_rsa · AWS credentials",
        "target_pattern": ".env · ~/.ssh/id_rsa · AWS credentials",
        "category": "Credential & Secret Exfiltration",
    },
    "sig_force_push_wipe": {
        "pattern_id": "sig_force_push_wipe",
        "title": "Unapproved Force-Push & Schema Wipe",
        "signature": "git push --force · DROP TABLE · rm -rf",
        "target_pattern": "git push --force · DROP TABLE · rm -rf",
        "category": "Destructive Resource Modification",
    },
    "sig_pipe_shell_miner": {
        "pattern_id": "sig_pipe_shell_miner",
        "title": "Obfuscated Pipe-to-Shell & Miner Spawn",
        "signature": "curl | bash · base64 -d · xmrig",
        "target_pattern": "curl | bash · base64 -d · xmrig",
        "category": "Untrusted & Obfuscated Code Execution",
    },
    "sig_priv_subnet_recon": {
        "pattern_id": "sig_priv_subnet_recon",
        "title": "Privilege Escalation & Subnet Recon",
        "signature": "sudo · raw SYN scan · roleplay override",
        "target_pattern": "sudo · raw SYN scan · roleplay override",
        "category": "Privilege Escalation & Policy Bypass",
    },
}

MACRO_TO_SIG: dict[str, str] = {
    "cluster_credential_exfil": "sig_sensitive_file_probing",
    "cluster_destructive_mod": "sig_force_push_wipe",
    "cluster_untrusted_exec": "sig_pipe_shell_miner",
    "cluster_priv_escalation": "sig_priv_subnet_recon",
}


def _infer_agent_type(agent: str | None, session_id: str | None = "") -> str:
    """Extract a canonical agent identifier for cluster tracking."""
    ag = (agent or "").strip().lower()
    if ag == "claude":
        ag = "claude_code"
    elif ag == "redteam":
        ag = "red_team"
    elif ag == "inspect":
        ag = "inspect_eval"
    if ag and ag not in ("agent", "unknown", "other"):
        return ag
    sid = (session_id or "").strip().lower()
    for candidate, canonical in (
        ("antigravity", "antigravity"),
        ("claude_code", "claude_code"),
        ("claude", "claude_code"),
        ("cursor", "cursor"),
        ("red_team", "red_team"),
        ("redteam", "red_team"),
        ("inspect_eval", "inspect_eval"),
        ("inspect", "inspect_eval"),
    ):
        if candidate in sid:
            return canonical
    return ag or "agent"


_RE_SENSITIVE_SIG = re.compile(
    r"\b(credential|credentials|secret|secrets|exfil|exfiltration|s3_sync|env|\.env|ssh|id_rsa|token|tokens|api_key|aws|sts|pii)\b|s3://",
    re.IGNORECASE,
)
_RE_FORCE_PUSH_SIG = re.compile(
    r"\b(git|force_push|force push|drop table|drop_table|destructive|deletion|rm_rf|rm -rf|mkfs|wipe|truncat|truncate|schema)\b|--force",
    re.IGNORECASE,
)
_RE_PIPE_MINER_SIG = re.compile(
    r"\b(remote code|rce|cryptominer|xmrig|miner|obfuscated|base64|curl_bash|pipe_shell|pipe-to-shell|untrusted_code|untrusted-fork|install\.sh)\b|curl\s*\|\s*(?:bash|sh)|base64\s+-d|\beval\b",
    re.IGNORECASE,
)
_RE_PRIV_RECON_SIG = re.compile(
    r"\b(sudo|syn scan|raw syn|port_scan|recon|impersonation|roleplay|executive_impersonation|privilege|chmod|imds|169\.254|escalation|override)\b",
    re.IGNORECASE,
)


def _classify_signature(
    rule_name: str | None,
    rule_id: str | None,
    reason: str | None,
    tool_input: str | None = "",
    macro_id: str | None = "",
) -> tuple[str, str, str, str]:
    """Map review signals to concrete cross-session behavioral signature.

    Returns (pattern_id, title, signature, category).
    """
    primary = f"{rule_name or ''} {rule_id or ''}".strip()
    full_text = f"{primary} {reason or ''} {tool_input or ''}"

    # 1. Primary rule name/id matching first
    if primary and primary.lower() not in (
        "",
        "unclassified",
        "policy_violation",
        "unknown",
        "blocked",
    ):
        if _RE_SENSITIVE_SIG.search(primary):
            spec = SIGNATURE_SPECS["sig_sensitive_file_probing"]
            return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]
        if _RE_FORCE_PUSH_SIG.search(primary):
            spec = SIGNATURE_SPECS["sig_force_push_wipe"]
            return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]
        if _RE_PIPE_MINER_SIG.search(primary):
            spec = SIGNATURE_SPECS["sig_pipe_shell_miner"]
            return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]
        if _RE_PRIV_RECON_SIG.search(primary):
            spec = SIGNATURE_SPECS["sig_priv_subnet_recon"]
            return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]

    # 2. Check full context (reason + tool_input)
    if _RE_SENSITIVE_SIG.search(full_text):
        spec = SIGNATURE_SPECS["sig_sensitive_file_probing"]
        return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]
    if _RE_FORCE_PUSH_SIG.search(full_text):
        spec = SIGNATURE_SPECS["sig_force_push_wipe"]
        return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]
    if _RE_PIPE_MINER_SIG.search(full_text):
        spec = SIGNATURE_SPECS["sig_pipe_shell_miner"]
        return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]
    if _RE_PRIV_RECON_SIG.search(full_text):
        spec = SIGNATURE_SPECS["sig_priv_subnet_recon"]
        return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]

    if macro_id and macro_id in MACRO_TO_SIG:
        spec = SIGNATURE_SPECS[MACRO_TO_SIG[macro_id]]
        return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]

    spec = SIGNATURE_SPECS["sig_priv_subnet_recon"]
    return spec["pattern_id"], spec["title"], spec["signature"], spec["category"]


def _build_threat_groups(
    blocked_rows: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate blocked reviews into canonical threats and concrete multi-session clusters.

    ``blocked_rows`` items are tuples containing:
    ``(session_id, rule_name, rule_id, tool_name, score, reason[, agent_type, tool_input])``.
    """
    macro_groups: dict[str, dict[str, Any]] = {}
    cluster_groups: dict[str, dict[str, Any]] = {}

    for row in blocked_rows:
        session_id = str(row[0])
        rule_name = str(row[1] or "")
        rule_id = str(row[2] or "")
        tool_name = str(row[3] or "")
        score = int(row[4] or 0)
        reason = str(row[5] or "")
        raw_agent = str(row[6]) if len(row) > 6 and row[6] else ""
        tool_input = str(row[7]) if len(row) > 7 and row[7] else ""
        agent_type = _infer_agent_type(raw_agent, session_id=session_id)

        # 1. Macro classification for Card 2 (High-level policy domain breakdown)
        macro_title, macro_id = _classify_threat_row(rule_name, rule_id, reason)
        m_group = macro_groups.setdefault(
            macro_id,
            {
                "title": macro_title,
                "count": 0,
                "scores": [],
            },
        )
        m_group["count"] += 1
        m_group["scores"].append(score)

        # 2. Concrete signature cluster classification for Card 1
        sig_id, sig_title, sig_pattern, sig_category = _classify_signature(
            rule_name, rule_id, reason, tool_input, macro_id=macro_id
        )
        c_group = cluster_groups.setdefault(
            sig_id,
            {
                "pattern_id": sig_id,
                "title": sig_title,
                "signature": sig_pattern,
                "target_pattern": sig_pattern,
                "category": sig_category,
                "count": 0,
                "scores": [],
                "sessions": set(),
                "agents": set(),
                "max_score": 0,
                "sample_tool": "",
                "sample_rule": "",
            },
        )
        c_group["count"] += 1
        c_group["scores"].append(score)
        c_group["sessions"].add(session_id)
        if agent_type:
            c_group["agents"].add(agent_type)
        c_group["max_score"] = max(c_group["max_score"], score)
        if not c_group["sample_tool"] and tool_name:
            c_group["sample_tool"] = str(tool_name)
        if not c_group["sample_rule"] and (rule_id or rule_name):
            c_group["sample_rule"] = str(rule_id or rule_name)

    top_threats = [
        {
            "threat": group["title"],
            "count": group["count"],
            "avg_severity": round(sum(group["scores"]) / len(group["scores"]), 1)
            if group["scores"]
            else 0.0,
        }
        for group in macro_groups.values()
    ]
    top_threats.sort(key=lambda item: (-item["count"], -item["avg_severity"]))

    cross_session_patterns = []
    for group in cluster_groups.values():
        affected_sessions = len(group["sessions"])
        total_hits = group["count"]
        if affected_sessions < 2:
            continue
        max_severity = int(group["max_score"] or 8)
        agents_list = sorted(group["agents"])
        cross_session_patterns.append(
            {
                "pattern_id": group["pattern_id"],
                "title": group["title"],
                "signature": group["signature"],
                "target_pattern": group["target_pattern"],
                "agents": agents_list,
                "category": group["category"],
                "rule_id": group["sample_rule"] or group["pattern_id"],
                "affected_sessions": affected_sessions,
                "session_count": affected_sessions,
                "total_hits": total_hits,
                "total_occurrences": total_hits,
                "max_severity": max_severity,
                "sample_tool": group["sample_tool"] or "Bash",
                "status": "active_cluster",
                # Backward-compatible keys used by older dashboard builds.
                "id": group["pattern_id"],
                "pattern": group["pattern_id"],
                "threat_name": group["title"],
                "severity": "critical" if max_severity >= 8 else "high",
                "score": max_severity,
            }
        )
    cross_session_patterns.sort(key=lambda item: (-item["affected_sessions"], -item["total_hits"]))
    return top_threats, cross_session_patterns


class WatcherAnalyticsMixin:
    """Organization analytics, threat grouping, and analyzer summary."""

    if TYPE_CHECKING:
        degraded: bool
        storage_dir: Path
        _sessions: dict[str, Session]
        _in_memory_demo_sessions: dict[str, Session]

        def _record_db_failure(self, context: str, exc: BaseException) -> None: ...

    def get_analytics_overview(self) -> dict[str, Any]:
        """Aggregate high-level overview metrics directly from PostgreSQL for current user."""
        try:
            db = _watcher_module().SessionLocal()
            try:
                user_id = _watcher_module().get_current_user_id(db)

                # Sessions counts
                total_sessions = (
                    db.execute(
                        text("SELECT COUNT(*) FROM sessions WHERE user_id = :uid"), {"uid": user_id}
                    ).scalar()
                    or 0
                )

                deep_reviewed = (
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM sessions WHERE user_id = :uid AND (passed IS NOT NULL)"
                        ),
                        {"uid": user_id},
                    ).scalar()
                    or 0
                )

                # Reviews counts
                blocked_count = (
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision IN ('block', 'deny', 'ask')"
                        ),
                        {"uid": user_id},
                    ).scalar()
                    or 0
                )

                critical_reviews = (
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND (score >= 8 OR decision IN ('block', 'deny', 'ask'))"
                        ),
                        {"uid": user_id},
                    ).scalar()
                    or 0
                )

                total_reviews = (
                    db.execute(
                        text("SELECT COUNT(*) FROM reviews WHERE user_id = :uid"), {"uid": user_id}
                    ).scalar()
                    or 0
                )

                auto_approved = (
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision = 'allow'"
                        ),
                        {"uid": user_id},
                    ).scalar()
                    or 0
                )

                escalated_count = (
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision = 'escalate'"
                        ),
                        {"uid": user_id},
                    ).scalar()
                    or 0
                )

                critical_rate = (
                    round((critical_reviews / total_reviews) * 100, 1) if total_reviews > 0 else 0.0
                )
                auto_approved_pct = (
                    round((auto_approved / total_reviews) * 100, 1) if total_reviews > 0 else 0.0
                )

                agent_counts_rows = db.execute(
                    text(
                        "SELECT agent_type, COUNT(*) FROM sessions WHERE user_id = :uid GROUP BY agent_type"
                    ),
                    {"uid": user_id},
                ).fetchall()
                agent_counts = {str(r[0]): int(r[1]) for r in agent_counts_rows}

                return {
                    "total_sessions": total_sessions,
                    "deep_reviewed": deep_reviewed,
                    "critical_warning_rate": f"{critical_rate}%",
                    "blocked_incidents": blocked_count,
                    "total_reviews": total_reviews,
                    "auto_approved": auto_approved,
                    "escalated_count": escalated_count,
                    "auto_approved_pct": f"{auto_approved_pct}%",
                    "agent_counts": agent_counts,
                    "degraded": self.degraded,
                }
            finally:
                db.rollback()
                db.close()
        except Exception as e:  # noqa: BLE001 - degraded-mode fallback must catch all
            self._record_db_failure("get_analytics_overview", e)
            return {
                "total_sessions": 0,
                "deep_reviewed": 0,
                "critical_warning_rate": "0.0%",
                "blocked_incidents": 0,
                "total_reviews": 0,
                "auto_approved": 0,
                "escalated_count": 0,
                "auto_approved_pct": "0.0%",
                "agent_counts": {},
                "degraded": True,
                "error": str(e),
            }

    def get_analyzer_summary(self) -> dict[str, Any]:
        """Compute organization-wide Analyzer risk metrics, cross-session patterns, and capture sources with DB fallback."""
        try:
            db = _watcher_module().SessionLocal()
            try:
                user_id = _watcher_module().get_current_user_id(db)

                # 1. Core counters
                total_reviews = (
                    db.execute(
                        text("SELECT COUNT(*) FROM reviews WHERE user_id = :uid"), {"uid": user_id}
                    ).scalar()
                    or 0
                )

                total_blocked = (
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision IN "
                            "('block', 'deny', 'reject', 'ask', 'escalate', 'force_ask')"
                        ),
                        {"uid": user_id},
                    ).scalar()
                    or 0
                )

                total_allowed = (
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision = 'allow'"
                        ),
                        {"uid": user_id},
                    ).scalar()
                    or 0
                )

                total_sessions = (
                    db.execute(
                        text("SELECT COUNT(*) FROM sessions WHERE user_id = :uid"), {"uid": user_id}
                    ).scalar()
                    or 0
                )

                block_rate_pct = (
                    round((total_blocked / total_reviews) * 100.0, 1) if total_reviews > 0 else 0.0
                )

                # 2. Real SQL Latency percentiles across all recorded reviews for active user
                lat_row = db.execute(
                    text(
                        """
                        SELECT
                            COALESCE(percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms), 0.0) AS p50,
                            COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0.0) AS p95,
                            COALESCE(AVG(latency_ms), 0.0) AS avg_lat
                        FROM reviews
                        WHERE user_id = :uid AND latency_ms > 0
                        """
                    ),
                    {"uid": user_id},
                ).fetchone()

                if lat_row and total_reviews > 0:
                    p50_latency = round(float(lat_row[0]), 2)
                    p95_latency = round(float(lat_row[1]), 2)
                    avg_latency = round(float(lat_row[2]), 2)
                else:
                    p50_latency = 0.0
                    p95_latency = 0.0
                    avg_latency = 0.0

                # 3. Top blocked threat categories and multi-session clusters
                blocked_rows = db.execute(
                    text(
                        """
                        SELECT
                            r.session_id,
                            COALESCE(NULLIF(r.rule_name, ''), 'unclassified') as rule_name,
                            '' as rule_id,
                            COALESCE(NULLIF(r.tool_name, ''), 'Bash') as tool_name,
                            COALESCE(r.score, 0) as score,
                            COALESCE(r.explanation, '') as reason,
                            COALESCE(NULLIF(s.agent_type, ''), 'agent') as agent_type,
                            COALESCE(r.tool_input, '') as tool_input
                        FROM reviews r
                        LEFT JOIN sessions s ON r.session_id = s.id
                        WHERE r.user_id = :uid
                          AND r.decision IN ('block', 'deny', 'reject', 'ask', 'escalate', 'force_ask')
                        """
                    ),
                    {"uid": user_id},
                ).fetchall()
                top_threats, cross_session_patterns = _build_threat_groups(
                    [
                        (
                            str(row[0]),
                            str(row[1] or ""),
                            str(row[2] or ""),
                            str(row[3] or ""),
                            int(row[4] or 0),
                            str(row[5] or ""),
                            str(row[6] or ""),
                            str(row[7] or ""),
                        )
                        for row in blocked_rows
                    ]
                )

                # 4. Lockout distribution by agent source
                agent_rows = db.execute(
                    text(
                        """
                        SELECT
                            COALESCE(NULLIF(s.agent_type, ''), 'other') as agent,
                            COUNT(r.id) as total_events,
                            SUM(CASE WHEN r.decision IN ('block', 'deny', 'reject', 'ask', 'escalate', 'force_ask') THEN 1 ELSE 0 END) as lockouts
                        FROM reviews r
                        LEFT JOIN sessions s ON r.session_id = s.id
                        WHERE r.user_id = :uid
                        GROUP BY agent
                        ORDER BY lockouts DESC, total_events DESC
                        """
                    ),
                    {"uid": user_id},
                ).fetchall()
                agent_distribution = [
                    {
                        "agent": str(row[0]),
                        "total_events": int(row[1]),
                        "lockouts": int(row[2] or 0),
                    }
                    for row in agent_rows
                ]

                # 5. Capture Sources Breakdown
                source_rows = db.execute(
                    text(
                        """
                        SELECT
                            COALESCE(NULLIF(s.agent_type, ''), 'unknown') as agent,
                            COUNT(*) as cnt
                        FROM sessions s
                        WHERE s.user_id = :uid
                        GROUP BY agent
                        """
                    ),
                    {"uid": user_id},
                ).fetchall()
                capture_counts = {"watcher_gate": 0, "transcript_ingest": 0, "inspect_ai": 0}
                for row in source_rows:
                    _source, source_key = _capture_source_bucket(str(row[0] or "unknown"))
                    capture_counts[source_key] += int(row[1] or 0)
                total_capture_sessions = sum(capture_counts.values())
                if total_capture_sessions > total_sessions:
                    total_sessions = total_capture_sessions
                capture_sources = _build_capture_sources(capture_counts, total_sessions)

                # 7. Recent high-priority interventions
                recent_rows = db.execute(
                    text(
                        """
                        SELECT
                            r.id,
                            r.session_id,
                            COALESCE(s.agent_type, 'unknown') as agent_type,
                            COALESCE(NULLIF(r.rule_name, ''), 'policy_violation') as rule_name,
                            r.tool_name,
                            r.decision,
                            r.score,
                            r.created_at,
                            r.explanation
                        FROM reviews r
                        LEFT JOIN sessions s ON r.session_id = s.id
                        WHERE r.user_id = :uid AND r.decision IN ('block', 'deny', 'reject', 'ask', 'escalate', 'force_ask')
                        ORDER BY r.created_at DESC
                        LIMIT 8
                        """
                    ),
                    {"uid": user_id},
                ).fetchall()
                recent_interventions = [
                    {
                        "id": str(row[0]),
                        "session_id": str(row[1]),
                        "agent_type": str(row[2]),
                        "threat": str(row[3]),
                        "tool_name": str(row[4]),
                        "decision": str(row[5]),
                        "score": int(row[6] or 0),
                        "timestamp": row[7].isoformat()
                        if hasattr(row[7], "isoformat")
                        else str(row[7]),
                        "explanation": str(row[8] or ""),
                    }
                    for row in recent_rows
                ]

                return {
                    "total_reviews": total_reviews,
                    "total_blocked": total_blocked,
                    "total_allowed": total_allowed,
                    "block_rate_pct": block_rate_pct,
                    "total_sessions": total_sessions,
                    "avg_latency_ms": avg_latency,
                    "p50_latency_ms": p50_latency,
                    "p95_latency_ms": p95_latency,
                    "top_threats": top_threats,
                    "agent_distribution": agent_distribution,
                    "agent_breakdown": agent_distribution,
                    "capture_sources": capture_sources,
                    "cross_session_patterns": cross_session_patterns,
                    "recent_interventions": recent_interventions,
                    "degraded": self.degraded,
                }
            finally:
                db.rollback()
                db.close()
        except Exception as e:  # noqa: BLE001 - degraded-mode fallback must catch all
            self._record_db_failure("get_analyzer_summary", e)
            # Memory fallback across cached and in-memory demo sessions
            all_sessions = list(self._sessions.values()) + list(
                self._in_memory_demo_sessions.values()
            )
            if not all_sessions and self.storage_dir.exists():
                for f in self.storage_dir.glob("*.json"):
                    if f.name.startswith("user_"):
                        continue
                    try:
                        p = json.loads(f.read_text(encoding="utf-8"))
                        all_sessions.append(Session.model_validate(p))
                    except Exception:  # noqa: BLE001 - skip unreadable cache files
                        pass
            # Deduplicate by session_id
            seen = set()
            unique_sessions = []
            for s in all_sessions:
                sid = s.session_id
                if sid not in seen:
                    seen.add(sid)
                    unique_sessions.append(s)

            tot_sess = len(unique_sessions)
            all_reviews = []
            blocked_review_pairs: list[tuple[str, Any, str]] = []
            seen_rev_ids: set[str] = set()
            for s in unique_sessions:
                sess_revs = (getattr(s.trajectory, "reviews", []) or []) + (
                    getattr(s, "decisions", []) or []
                )
                for r in sess_revs:
                    rid = getattr(r, "id", None) or getattr(r, "decision_id", None)
                    if rid:
                        if rid in seen_rev_ids:
                            continue
                        seen_rev_ids.add(rid)
                    all_reviews.append(r)
                    if _is_active_blocked_review(r):
                        blocked_review_pairs.append((s.session_id, r, s.agent_type or ""))

            allowed_reviews = [r for r in all_reviews if r.decision == "allow"]
            tot_rev = len(all_reviews)
            tot_blk = len(blocked_review_pairs)
            blk_rate = round((tot_blk / tot_rev) * 100.0, 1) if tot_rev > 0 else 0.0

            lats = [r.latency_ms for r in all_reviews if r.latency_ms and r.latency_ms > 0]
            lats.sort()
            p50 = float(lats[len(lats) // 2]) if lats else 0.0
            p95 = float(lats[int(len(lats) * 0.95)]) if lats else 0.0
            avg_lat = round(sum(lats) / len(lats), 2) if lats else 0.0

            # Capture sources
            capture_counts = {"watcher_gate": 0, "transcript_ingest": 0, "inspect_ai": 0}
            for s in unique_sessions:
                _source, source_key = _capture_source_bucket(s.agent_type)
                capture_counts[source_key] += 1
            fallback_sources = _build_capture_sources(capture_counts, tot_sess)

            # Canonical top threats and real multi-session clusters
            top_threats_list, patterns_list = _build_threat_groups(
                [
                    (
                        session_id,
                        str(getattr(r, "rule_name", "") or ""),
                        str(getattr(r, "rule_id", "") or ""),
                        str(getattr(r, "tool_name", "") or ""),
                        int(getattr(r, "score", 0) or 0),
                        str(getattr(r, "explanation", "") or ""),
                        agent_type,
                        str(getattr(r, "tool_input", "") or ""),
                    )
                    for session_id, r, agent_type in blocked_review_pairs
                ]
            )

            agent_counts: dict[str, dict[str, int]] = {}
            for s in unique_sessions:
                ag = s.agent_type or "agent"
                if ag not in agent_counts:
                    agent_counts[ag] = {"total_events": 0, "lockouts": 0}
                revs = getattr(s.trajectory, "reviews", None) or getattr(s, "decisions", None) or []
                agent_counts[ag]["total_events"] += (
                    len(revs) or len(getattr(s, "steps", []) or []) or 1
                )
                agent_counts[ag]["lockouts"] += sum(1 for r in revs if _is_active_blocked_review(r))

            agent_dist = [
                {"agent": ag, "total_events": v["total_events"], "lockouts": v["lockouts"]}
                for ag, v in sorted(
                    agent_counts.items(),
                    key=lambda x: (x[1]["lockouts"], x[1]["total_events"]),
                    reverse=True,
                )
            ]

            recent_interventions = []
            for s in unique_sessions:
                revs = getattr(s.trajectory, "reviews", None) or getattr(s, "decisions", None) or []
                for r in revs:
                    if _is_active_blocked_review(r):
                        recent_interventions.append(
                            {
                                "id": str(getattr(r, "id", "") or getattr(r, "decision_id", "")),
                                "session_id": s.session_id,
                                "agent_type": s.agent_type or "agent",
                                "threat": r.rule_id or r.rule_name or "policy_violation",
                                "tool_name": r.tool_name,
                                "decision": r.decision,
                                "score": r.score,
                                "timestamp": getattr(r, "created_at", "")
                                or datetime.now(UTC).isoformat(),
                                "explanation": r.explanation or "",
                            }
                        )
            recent_interventions = recent_interventions[-8:]

            return {
                "total_reviews": tot_rev,
                "total_blocked": tot_blk,
                "total_allowed": len(allowed_reviews),
                "block_rate_pct": blk_rate,
                "total_sessions": tot_sess,
                "avg_latency_ms": avg_lat,
                "p50_latency_ms": round(p50, 2),
                "p95_latency_ms": round(p95, 2),
                "top_threats": top_threats_list,
                "agent_distribution": agent_dist,
                "agent_breakdown": agent_dist,
                "capture_sources": fallback_sources,
                "cross_session_patterns": patterns_list,
                "recent_interventions": recent_interventions,
                "degraded": True,
                "error": str(e),
            }
