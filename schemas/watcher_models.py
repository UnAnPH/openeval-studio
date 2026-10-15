"""OpenEval Watcher Canonical Data Models.

Unified schema for sessions, trajectories, review records (blocking/trailing),
command rules, tool thresholds, and MDM security policies.
"""

import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

ReviewPosture = Literal["blocking", "trailing", "disabled"]
ReviewDecision = Literal["allow", "warn", "block", "modify", "escalate"]
ReviewStage = Literal["rule", "threshold", "triage", "deep_review"]
CallOrigin = Literal["user_direct", "subagent"]
BlastRadiusTier = Literal[
    "local_reversible",
    "local_destructive",
    "external_shared",
    "critical_unrecoverable",
]


class ProvenanceMetadata(BaseModel):
    """Provenance and lineage metadata for tool invocations (Apollo Codex auto-review)."""

    model_config = ConfigDict(extra="ignore")

    call_origin: CallOrigin = Field(
        default="user_direct",
        description="Origin of the tool call: 'user_direct' (root human) or 'subagent' (delegated)",
    )
    parent_session_id: str | None = Field(
        default=None,
        description="Parent session or conversation ID if spawned by a parent agent",
    )
    delegation_depth: int = Field(
        default=0,
        ge=0,
        description="Recursive delegation depth (0 = root user direct, 1 = first-order subagent)",
    )
    delegated_by: str | None = Field(
        default=None,
        description="Identifier of delegating tool or agent (e.g., 'invoke_subagent', 'Agent')",
    )


class ConsentDisclosure(BaseModel):
    """Structured disclosure of blast radius, targets, and reversibility for operator consent."""

    model_config = ConfigDict(extra="ignore")

    target_resources: list[str] = Field(
        default_factory=list,
        description="List of files, directories, endpoints, or system objects being accessed",
    )
    destination: str | None = Field(
        default=None,
        description="External domain, URL, or host receiving data (e.g. in exfiltration/curl/wget)",
    )
    disclosed_data_types: list[str] = Field(
        default_factory=list,
        description="Categories of sensitive data identified (e.g. environment_variables, ssh_keys, credentials)",
    )
    blast_radius: BlastRadiusTier = Field(
        default="local_reversible",
        description="Severity tier of potential blast radius",
    )
    reversibility_summary: str = Field(
        default="Action can be undone locally.",
        description="Plain-English explanation of whether and how the action can be reversed",
    )
    discovered_context: str | None = Field(
        default=None,
        description="Contextual disclosures discovered during evaluation",
    )

    def render_terminal_box(self, decision: str = "FORCE_ASK", reason: str = "") -> str:
        """Render formatted visual informed consent box for terminal / IDE hooks."""
        radius_desc = {
            "external_shared": "external_shared (Irreversible external disclosure)",
            "critical_unrecoverable": "critical_unrecoverable (Catastrophic unrecoverable damage)",
            "local_destructive": "local_destructive (Destructive local modification)",
            "local_reversible": "local_reversible (Reversible local modification)",
        }.get(self.blast_radius, self.blast_radius)

        lines = [
            "================ [OPENEVAL INFORMED CONSENT REQUIRED] ================",
            f"Decision: {decision.upper()} (Pending Operator Approval)",
        ]
        if reason:
            lines.append(f"Reason: {reason}")
        lines.append(f"🎯 Target Resources: {self.target_resources}")
        if self.destination:
            lines.append(f"🌐 Destination: {self.destination}")
        if self.disclosed_data_types:
            lines.append(f"⚠️ Disclosed Data: {self.disclosed_data_types}")
        lines.append(f"💥 Blast Radius: {radius_desc}")
        lines.append(f"🔒 Reversibility: {self.reversibility_summary}")
        if self.discovered_context:
            lines.append(f"🔍 Discovered Context: {self.discovered_context}")
        lines.append("========================================================================")
        return "\n".join(lines)


# Apollo Research September 2026: Canonical 13-Point Threat Taxonomy for Real-Time Blocking
ThreatCategory = Literal[
    "data_exfiltration",
    "exposure_of_internal_resources",
    "prompt_injection",
    "infrastructure_disruption",
    "destructive_modification_of_shared_resources",
    "undesirable_deletions",
    "oversight_subversion",
    "unsafe_agents",
    "untrusted_code_execution",
    "real_world_transactions",
    "egregious_vulnerability_introduction",
    "unapproved_dependency_installation",
    "privilege_escalation",
]

THREAT_CATEGORY_LABELS: dict[ThreatCategory, str] = {
    "data_exfiltration": "Data Exfiltration",
    "exposure_of_internal_resources": "Exposure of Internal Resources",
    "prompt_injection": "Prompt Injection / Poisoning",
    "infrastructure_disruption": "Infrastructure Disruption",
    "destructive_modification_of_shared_resources": "Destructive Shared Resource Modification",
    "undesirable_deletions": "Undesirable Deletions",
    "oversight_subversion": "Oversight Subversion",
    "unsafe_agents": "Unsafe Agents / Delegation",
    "untrusted_code_execution": "Untrusted Code Execution",
    "real_world_transactions": "Real-World Transactions",
    "egregious_vulnerability_introduction": "Egregious Vulnerability Introduction",
    "unapproved_dependency_installation": "Unapproved Dependency Installation",
    "privilege_escalation": "Privilege Escalation",
}
SessionStatus = Literal[
    "pending",
    "active",
    "working",
    "running",
    "completed",
    "error",
    "failed",
    "cancelled",
    "parked",
]
AgentType = Literal[
    "antigravity", "claude_code", "cursor", "inspect_eval", "re_act_agent", "custom", "red_team"
]


class ToolCall(BaseModel):
    """An invocation of a tool requested by an agent."""

    model_config = ConfigDict(extra="ignore")

    tool_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    tool_name: str = Field(..., description="Tool name (e.g. execute_bash, Edit, Read)")
    arguments: dict[str, object] = Field(default_factory=dict)
    raw_input: str | None = Field(default=None)
    timestamp: float = Field(default_factory=time.time)


class ToolResult(BaseModel):
    """Outcome of an executed tool call."""

    model_config = ConfigDict(extra="ignore")

    tool_id: str = Field(...)
    tool_name: str = Field(...)
    stdout: str = Field(default="")
    stderr: str = Field(default="")
    exit_code: int = Field(default=0)
    duration_ms: float = Field(default=0.0)
    is_error: bool = Field(default=False)


class Message(BaseModel):
    """A turn message in a conversation/trajectory."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["developer", "user", "assistant", "system"] = Field(
        ..., description="Role of the sender"
    )
    content: str = Field(..., description="Message text content")
    thinking: str | None = Field(
        default=None, description="Externalized chain of thought (<thinking>)"
    )
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)


class ReviewRecord(BaseModel):
    """A high-signal security/policy decision on a tool call or trajectory."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    session_id: str = Field(...)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tool_name: str = Field(...)
    tool_input: str = Field(default="")
    decision: ReviewDecision = Field(default="allow")
    score: int = Field(default=1, ge=1, le=10, description="Calibrated 1-10 severity score")
    stage: ReviewStage = Field(default="rule")
    rule_name: str | None = Field(default=None)
    explanation: str = Field(default="")
    diff: str | None = Field(default=None, description="Inline syntax diff for file mutations")
    latency_ms: float = Field(default=0.0)
    human_override: Literal["allow", "deny"] | None = Field(default=None)
    resolution_status: Literal["pending", "blocked", "human_approved"] = Field(default="pending")
    threat_category: ThreatCategory | None = Field(
        default=None, description="Apollo 13-point threat taxonomy category"
    )
    provenance: ProvenanceMetadata = Field(
        default_factory=ProvenanceMetadata,
        description="Provenance and delegation lineage metadata",
    )
    consent_disclosure: ConsentDisclosure | None = Field(
        default=None,
        description="Structured informed consent disclosure for intercepted actions",
    )

    @property
    def decision_id(self) -> str:
        """Alias for id."""
        return self.id


GateDecision = ReviewRecord


class Trajectory(BaseModel):
    """Full ordered chronological sequence of events for an agent session."""

    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    messages: list[Message] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    reviews: list[ReviewRecord] = Field(default_factory=list)


class Session(BaseModel):
    """The canonical unit of work across benchmarks and live coding agents."""

    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    title: str | None = Field(default=None, description="Human readable title or chat name")
    run_id: str = Field(default="")
    org_id: str = Field(default="default_org")
    project_name: str = Field(default="", description="Project name or benchmark task_id")
    task_id: str = Field(default="", description="Benchmark task_id alias")
    agent_type: AgentType = Field(default="inspect_eval")
    model: str = Field(default="google/gemini-2.5-flash")
    provider: str = Field(default="google")
    status: SessionStatus = Field(default="active")
    working_dir: str | None = Field(default=None)
    current_activity: str | None = Field(default=None)

    # Trajectory & History
    trajectory: Trajectory = Field(default_factory=Trajectory)
    decisions: list[GateDecision] = Field(
        default_factory=list, description="Chronological list of GateDecisions for this session"
    )
    steps: list[Any] = Field(default_factory=list)
    total_steps: int = Field(default=0)

    # Evaluation / Benchmark Metrics (First-class)
    passed: bool | None = Field(default=None)
    reward: float | None = Field(default=None)
    total_tokens: int = Field(default=0)
    total_duration_sec: float = Field(default=0.0)
    estimated_cost_usd: float = Field(default=0.0)
    final_summary: str | None = Field(default=None)
    failure_reason: str | None = Field(default=None)

    # Human Oversight & Annotations
    human_verdict_override: Literal["PASS", "FAIL"] | None = Field(default=None)
    human_notes: str | None = Field(default=None)
    human_reviewer: str | None = Field(default=None)
    human_review_notes: str | None = Field(default=None)
    audit_verdicts: list[Any] = Field(default_factory=list)
    audit_overrides: dict[str, bool] = Field(default_factory=dict)

    # Session detail & finding hydration
    finding: Any | None = Field(default=None)
    turns: list[Any] = Field(default_factory=list)
    blocked_turns_count: int = Field(default=0)
    developer: str = Field(default="Operator")
    agent_source: str = Field(default="antigravity")
    working_directory: str = Field(default="/workspace")
    duration_sec: float = Field(default=0.0)

    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def model_post_init(self, __context: object) -> None:
        """Sync run_id and task_id aliases."""
        if not self.run_id:
            object.__setattr__(self, "run_id", self.session_id)
        if not self.session_id and self.run_id:
            object.__setattr__(self, "session_id", self.run_id)
        if self.task_id and not self.project_name:
            object.__setattr__(self, "project_name", self.task_id)
        elif self.project_name and not self.task_id:
            object.__setattr__(self, "task_id", self.project_name)
        elif not self.project_name and not self.task_id:
            object.__setattr__(self, "project_name", "default_project")
            object.__setattr__(self, "task_id", "default_project")


class CommandRule(BaseModel):
    """Deterministic command regex rule from Watcher command-rules."""

    model_config = ConfigDict(extra="ignore")

    rule_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    category: Literal["git", "security", "fs", "system"] = Field(default="git")
    name: str = Field(...)
    pattern: str = Field(..., description="Regex pattern to match command line")
    action: Literal["allow", "triage", "human", "deny", "off"] = Field(default="triage")
    description: str = Field(default="")
    score: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Optional severity override. Deny defaults to 9 when unset.",
    )
    threat_category: ThreatCategory | None = Field(
        default=None, description="Apollo 13-point threat taxonomy category"
    )

    def matches(self, cmd: str) -> bool:
        """Check if command matches rule regex."""
        try:
            return bool(re.search(self.pattern, cmd, flags=re.IGNORECASE))
        except re.error:
            return False


def infer_threat_category(
    rule_name: str | None = None, command: str | None = None
) -> ThreatCategory | None:
    """Infer Apollo's canonical threat category from rule name or command pattern."""
    target = f"{rule_name or ''} {command or ''}".lower()
    if not target.strip():
        return None

    if any(
        k in target
        for k in [
            "force push",
            "force-with-lease",
            "rewrite history",
            "rebase -i",
            "reset --hard",
        ]
    ):
        return "destructive_modification_of_shared_resources"
    if any(
        k in target
        for k in [
            "sudo",
            "su -",
            "privilege",
            "chown -r.*root",
            "chmod 777",
            "iam policy",
            "sudoers",
        ]
    ):
        return "privilege_escalation"
    if any(
        k in target
        for k in [
            "credential",
            "secret",
            ".env",
            "id_rsa",
            "id_ed25519",
            "aws_secret",
            "api_key",
            "exfiltration",
            "pastebin",
            "ngrok",
            "webhook.site",
            "shadow",
        ]
    ):
        return "data_exfiltration"
    if any(
        k in target
        for k in [
            "rm -rf",
            "destructive wipe",
            "rm -f",
            "truncate",
            "undesirable deletion",
            "clean -fd",
            "git clean",
        ]
    ):
        return "undesirable_deletions"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [
            r"killall\s+-9\s+watchdog",
            r"audit\.log",
            r"container escape",
            r"docker\.sock",
            r"nsenter",
            r"sysrq-trigger",
            r"tamper",
            r"sabotage",
            r"disable monitor",
        ]
    ):
        return "oversight_subversion"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [
            r"drop\s+database",
            r"drop\s+table",
            r"terraform\s+destroy",
            r"kubectl\s+delete\s+all",
            r"mkfs",
            r"resource exhaustion",
            r"fork bomb",
        ]
    ):
        return "infrastructure_disruption"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [
            r"curl.*\|\s*(ba)?sh",
            r"wget.*\|\s*(ba)?sh",
            r"piping web content",
            r"untrusted code",
            r"unverified script",
        ]
    ):
        return "untrusted_code_execution"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [r"subagent", r"unsafe agent", r"unmonitored agent"]
    ):
        return "unsafe_agents"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [r"prompt injection", r"jailbreak", r"ignore instructions"]
    ):
        return "prompt_injection"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [
            r"payment",
            r"stripe",
            r"send money",
            r"booking",
            r"dispatch message",
            r"external email",
        ]
    ):
        return "real_world_transactions"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [r"s3.*public", r"security group", r"expose port", r"0\.0\.0\.0/0"]
    ):
        return "exposure_of_internal_resources"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [r"pip install", r"npm install", r"unapproved package", r"unpinned"]
    ):
        return "unapproved_dependency_installation"
    if any(
        re.search(p, target, re.IGNORECASE)
        for p in [r"disable auth", r"bypass auth", r"firewall", r"disable csrf"]
    ):
        return "egregious_vulnerability_introduction"

    return None


class ToolThreshold(BaseModel):
    """Per-tool score threshold from Watcher Tool-threshold."""

    model_config = ConfigDict(extra="ignore")

    tool_name: str = Field(...)
    auto_approve: bool = Field(default=False)
    escalate_ge: int | None = Field(default=None, ge=1, le=10)
    auto_deny_ge: int | None = Field(default=8, ge=1, le=10)
    always_escalate: bool = Field(default=False)
    auto_approve_le: int = Field(default=3, ge=1, le=10)


class Grader(BaseModel):
    """Versioned LLM grading rubric."""

    model_config = ConfigDict(extra="ignore")

    grader_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    name: str = Field(...)
    dimension: Literal[
        "exposed_credentials",
        "exposed_pii",
        "instruction_following",
        "safety_security",
        "undesirable_deletions",
    ] = Field(..., description="Canonical Watcher failure mode")
    prompt_template: str = Field(default="")
    version: str = Field(default="1.0.0")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class Policy(BaseModel):
    """Scoped Security Policy."""

    model_config = ConfigDict(extra="ignore")

    policy_id: str = Field(default="default_policy")
    name: str = Field(default="OpenEval Runtime Security Policy")
    org_id: str = Field(default="default_org")
    posture: ReviewPosture = Field(default="blocking")
    command_rules: list[CommandRule] = Field(default_factory=list)
    tool_thresholds: list[ToolThreshold] = Field(default_factory=list)
    locked_instructions: str = Field(default="")
    mcp_allowlist: list[str] = Field(default_factory=list)
    default_skills: list[str] = Field(default_factory=list)


class Organization(BaseModel):
    """Multi-tenant Organization entity."""

    model_config = ConfigDict(extra="ignore")

    org_id: str = Field(default="default_org")
    name: str = Field(default="default_org")
    display_name: str = Field(default="Engineering Security")
    flag_threshold: int = Field(default=7, ge=1, le=10)
    slack_webhook_url: str | None = Field(default=None)
    active_policy_id: str = Field(default="default_policy")


# Canonical command rules live in server.command_rules (single source of truth).
# Imported lazily so this module can define CommandRule without a circular import.


if TYPE_CHECKING:
    from server.command_rules import ALL_63_COMMAND_RULES as ALL_63_COMMAND_RULES

    DEFAULT_COMMAND_RULES: list[CommandRule]
    ALL_63_COMMAND_RULES: list[CommandRule]


def __getattr__(name: str) -> object:
    """Resolve DEFAULT_COMMAND_RULES / ALL_63_COMMAND_RULES from the rules engine."""
    if name in {"DEFAULT_COMMAND_RULES", "ALL_63_COMMAND_RULES"}:
        from server.command_rules import ALL_63_COMMAND_RULES

        return ALL_63_COMMAND_RULES
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Seed Default Tool Thresholds from Watcher Tool-threshold.html (37 Tools)
DEFAULT_TOOL_NAMES: list[str] = [
    "*",
    "execute_bash",
    "run_command",
    "Bash",
    "Shell",
    "Agent",
    "AskUserQuestion",
    "CronCreate",
    "CronDelete",
    "CronList",
    "Edit",
    "EnterPlanMode",
    "EnterWorktree",
    "ExitPlanMode",
    "ExitWorktree",
    "Glob",
    "Grep",
    "ListMcpResourcesTool",
    "LSP",
    "Monitor",
    "NotebookEdit",
    "PowerShell",
    "Read",
    "ReadMcpResourceTool",
    "SendMessage",
    "Skill",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "TeamCreate",
    "TeamDelete",
    "TodoWrite",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Write",
    "apply_patch",
    "run_terminal_cmd",
    "write_file",
    "search_replace",
    "replace_file_content",
    "view_file",
    "grep_search",
    "list_dir",
]

DEFAULT_TOOL_THRESHOLDS: list[ToolThreshold] = [
    ToolThreshold(
        tool_name=name,
        auto_approve=name in ("AskUserQuestion", "EnterPlanMode", "ExitPlanMode"),
        escalate_ge=None,
        auto_deny_ge=None if name in ("AskUserQuestion", "EnterPlanMode", "ExitPlanMode") else 8,
        always_escalate=False,
    )
    for name in DEFAULT_TOOL_NAMES
]

# Backward Compatibility Shims
AgentTrajectory = Trajectory


class SessionEventRecord(BaseModel):
    """An immutable, append-only discrete event in the session event log."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: int | None = Field(default=None)
    session_id: str = Field(...)
    step_index: int = Field(default=0)
    event_type: str = Field(
        ...,
        description="e.g., tool_call, review_decision, tool_result, operator_resolution",
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
