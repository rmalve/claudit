"""
Telemetry data models — cross-project, framework-agnostic.

These schemas define what gets captured, stored, and queried.
They're used by hooks (capture), Qdrant (semantic storage),
and Prometheus (metric extraction).
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_val(v) -> str:
    """Extract string value from an enum member or pass through a plain string."""
    return v.value if hasattr(v, "value") and isinstance(v, Enum) else str(v)


class ToolCallStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    DENIED = "denied"  # user denied permission


class HallucinationType(str, Enum):
    PHANTOM_FILE = "phantom_file"          # references file that doesn't exist
    SCHEMA_MISMATCH = "schema_mismatch"    # references field/method that doesn't exist
    ARCHITECTURE_CONTRADICTION = "architecture_contradiction"  # contradicts CLAUDE.md
    FABRICATED_OUTPUT = "fabricated_output"  # claims test passed without running
    WRONG_SIGNATURE = "wrong_signature"     # incorrect function signature
    NONEXISTENT_ENDPOINT = "nonexistent_endpoint"  # references API route that doesn't exist


class AgentRole(str, Enum):
    """Maps to agent definitions in .claude/agents/"""
    ARCHITECT = "architect"
    LEAD_ENGINEER = "lead-engineer"
    API_ENGINEER = "api-engineer"
    HARDWARE_ENGINEER = "hardware-engineer"
    CV_ENGINEER = "cv-engineer"
    FRONTEND_ENGINEER = "frontend-engineer"
    WEBSOCKET_ENGINEER = "websocket-engineer"
    SECURITY = "security"
    DEVOPS = "devops"
    INTEGRATION_ENGINEER = "integration-engineer"
    QA_BACKEND = "qa-backend"
    QA_FRONTEND = "qa-frontend"
    QA_CV = "qa-cv"
    TECH_WRITER = "tech-writer"
    MAIN = "main"  # the main Claude conversation, not a sub-agent


# ── Core Events ──

class ToolCallEvent(BaseModel):
    """Captured on every tool invocation via PostToolUse hook."""
    session_id: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)
    tool_name: str                      # Bash, Read, Write, Edit, Grep, Glob, Agent, etc.
    file_path: str | None = None        # target file, if applicable
    command: str | None = None          # for Bash tool calls
    status: ToolCallStatus = ToolCallStatus.SUCCESS
    duration_ms: float | None = None
    agent: AgentRole | str = AgentRole.MAIN
    agent_version: str | None = None    # e.g. "v3" from versioning system
    agent_version_path: str | None = None  # absolute path to versioned agent file
    project: str = ""                   # project identifier for cross-project framework
    error_message: str | None = None
    input_summary: str | None = None    # truncated tool input for semantic search
    output_summary: str | None = None   # truncated tool output for semantic search

    def qdrant_payload(self) -> dict:
        return {
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "file_path": self.file_path,
            "command": self.command,
            "agent": _enum_val(self.agent),
            "agent_version": self.agent_version,
            "agent_version_path": self.agent_version_path,
            "project": self.project,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "error_message": self.error_message,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        """Text to embed for semantic search."""
        parts = [f"Tool: {self.tool_name}"]
        if self.file_path:
            parts.append(f"File: {self.file_path}")
        if self.command:
            parts.append(f"Command: {self.command[:200]}")
        if self.error_message:
            parts.append(f"Error: {self.error_message[:300]}")
        if self.input_summary:
            parts.append(f"Input: {self.input_summary[:200]}")
        return " | ".join(parts)


class HallucinationEvent(BaseModel):
    """Captured when the hallucination detector finds a discrepancy."""
    session_id: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)
    agent: AgentRole | str = AgentRole.MAIN
    agent_version: str | None = None
    agent_version_path: str | None = None
    project: str = ""
    hallucination_type: HallucinationType
    claim: str                          # what the agent claimed
    evidence: str                       # what we found (or didn't)
    severity: str = "warning"           # warning, error, critical
    file_path: str | None = None        # file the claim was about
    resolved: bool = False              # was the agent corrected?

    def qdrant_payload(self) -> dict:
        return {
            "session_id": self.session_id,
            "agent": _enum_val(self.agent),
            "agent_version": self.agent_version,
            "agent_version_path": self.agent_version_path,
            "project": self.project,
            "type": self.hallucination_type.value,
            "severity": self.severity,
            "resolved": self.resolved,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        return f"Hallucination [{self.hallucination_type.value}]: Claimed: {self.claim} | Evidence: {self.evidence}"


class AgentSpawnEvent(BaseModel):
    """Captured when a sub-agent is launched via the Agent tool."""
    session_id: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)
    parent_agent: AgentRole | str = AgentRole.MAIN
    parent_agent_version: str | None = None
    parent_agent_version_path: str | None = None
    child_agent: AgentRole | str = ""
    child_agent_version: str | None = None
    child_agent_version_path: str | None = None
    description: str = ""               # the short description passed to Agent tool
    prompt: str = ""                    # FULL prompt sent to the sub-agent
    project: str = ""

    def qdrant_payload(self) -> dict:
        parent = _enum_val(self.parent_agent)
        child = _enum_val(self.child_agent)
        return {
            "session_id": self.session_id,
            "parent_agent": parent,
            "parent_agent_version": self.parent_agent_version,
            "parent_agent_version_path": self.parent_agent_version_path,
            "child_agent": child,
            "child_agent_version": self.child_agent_version,
            "child_agent_version_path": self.child_agent_version_path,
            "description": self.description,
            "prompt": self.prompt[:2000] if self.prompt else "",
            "project": self.project,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        parent = _enum_val(self.parent_agent)
        child = _enum_val(self.child_agent)
        # Use full prompt for embedding if available, otherwise description
        content = self.prompt[:1000] if self.prompt else self.description
        return f"Agent spawn: {parent} -> {child}: {content}"


class EvalResult(BaseModel):
    """Result of an eval check (test pass, convention adherence, etc.)."""
    session_id: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)
    eval_name: str                      # "test_pass_rate", "convention_adherence", etc.
    agent: AgentRole | str = AgentRole.MAIN
    agent_version: str | None = None
    agent_version_path: str | None = None
    project: str = ""
    passed: bool
    score: float | None = None          # 0.0 - 1.0
    details: str | None = None          # human-readable explanation
    metadata: dict[str, Any] = Field(default_factory=dict)

    def qdrant_payload(self) -> dict:
        return {
            "session_id": self.session_id,
            "eval_name": self.eval_name,
            "agent": _enum_val(self.agent),
            "agent_version": self.agent_version,
            "agent_version_path": self.agent_version_path,
            "project": self.project,
            "passed": self.passed,
            "score": self.score,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"Eval [{self.eval_name}] {status}: {self.details or 'no details'}"


class SessionSummary(BaseModel):
    """Aggregated summary of a full Claude Code session."""
    session_id: str
    project: str = ""
    start_time: datetime
    end_time: datetime = Field(default_factory=_utcnow)
    duration_seconds: float = 0.0
    # Tool usage
    total_tool_calls: int = 0
    tool_call_breakdown: dict[str, int] = Field(default_factory=dict)  # {tool_name: count}
    tool_failures: int = 0
    # Agent usage
    agents_spawned: int = 0
    agent_breakdown: dict[str, int] = Field(default_factory=dict)  # {agent_name: count}
    # Quality
    hallucinations_detected: int = 0
    evals_passed: int = 0
    evals_failed: int = 0
    tests_run: int = 0
    tests_passed: int = 0
    # Files
    files_created: int = 0
    files_modified: int = 0
    files_read: int = 0
    # Context saturation signals
    claude_md_reads: int = 0
    jsonl_self_reads: int = 0
    context_saturation: bool = False
    # Count provenance
    count_source: str = "qdrant_scroll"  # "qdrant_scroll" (exact) or "qdrant_search" (approximate)
    # Git state at session end
    latest_commit_sha: str | None = None

    def qdrant_payload(self) -> dict:
        return {
            "session_id": self.session_id,
            "project": self.project,
            "duration_seconds": self.duration_seconds,
            "total_tool_calls": self.total_tool_calls,
            "tool_failures": self.tool_failures,
            "hallucinations_detected": self.hallucinations_detected,
            "claude_md_reads": self.claude_md_reads,
            "jsonl_self_reads": self.jsonl_self_reads,
            "context_saturation": self.context_saturation,
            "count_source": self.count_source,
            "latest_commit_sha": self.latest_commit_sha,
            "timestamp": self.end_time.isoformat(),
            "timestamp_epoch": self.end_time.timestamp(),
        }

    def semantic_text(self) -> str:
        return (
            f"Session {self.session_id}: {self.duration_seconds:.0f}s, "
            f"{self.total_tool_calls} tool calls ({self.tool_failures} failures), "
            f"{self.agents_spawned} agents, {self.hallucinations_detected} hallucinations, "
            f"tests {self.tests_passed}/{self.tests_run}"
        )


class ConversationTurnEvent(BaseModel):
    """A single conversation turn extracted from the session JSONL transcript.

    Contains the user's prompt, model's responses, tool call metadata, and
    thinking block count. Stored in the conversation_turns QDrant collection
    for hallucination detection and accurate session hierarchy.
    """
    session_id: str = ""
    prompt_id: str = ""
    turn_index: int = 0
    timestamp_start: datetime = Field(default_factory=_utcnow)
    timestamp_end: datetime = Field(default_factory=_utcnow)
    project: str = ""
    user_prompt: str = ""                                    # truncated to 2000 chars
    assistant_response: str = ""                             # concatenated text blocks, truncated to 2000 chars
    thinking_count: int = 0
    tool_call_count: int = 0
    tool_call_names: list[str] = Field(default_factory=list) # unique tool names used
    tool_failure_count: int = 0
    subagent_spawns: list[str] = Field(default_factory=list)
    entry_count: int = 0
    # Subagent context (populated for subagent turns)
    is_subagent: bool = False
    parent_session_id: str | None = None
    agent_type: str | None = None

    # Chronological event stream within this turn
    events: list[dict] = Field(default_factory=list)

    def qdrant_payload(self) -> dict:
        return {
            "session_id": self.session_id,
            "prompt_id": self.prompt_id,
            "turn_index": self.turn_index,
            "project": self.project,
            "user_prompt": self.user_prompt,
            "assistant_response": self.assistant_response,
            "thinking_count": self.thinking_count,
            "tool_call_count": self.tool_call_count,
            "tool_call_names": self.tool_call_names,
            "subagent_spawns": self.subagent_spawns,
            "entry_count": self.entry_count,
            "is_subagent": self.is_subagent,
            "parent_session_id": self.parent_session_id,
            "agent_type": self.agent_type,
            "events": self.events,
            "timestamp": self.timestamp_start.isoformat(),
            "timestamp_epoch": self.timestamp_start.timestamp(),
        }

    def semantic_text(self) -> str:
        user = self.user_prompt[:500] if self.user_prompt else ""
        assistant = self.assistant_response[:500] if self.assistant_response else ""
        tools = ", ".join(self.tool_call_names) if self.tool_call_names else "no tools"
        return f"User: {user} | Assistant: {assistant} | Tools: {tools}"


class ChangeOperation(str, Enum):
    WRITE = "write"       # new file created
    EDIT = "edit"         # existing file modified
    DELETE = "delete"     # file deleted


class CodeChangeEvent(BaseModel):
    """Captured on every Write/Edit tool call — the atomic unit of code change tracking.

    This is finer-grained than a git commit. A single session may produce
    dozens of code changes, some of which introduce bugs that are fixed
    before the commit ever happens.
    """
    change_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)
    agent: AgentRole | str = AgentRole.MAIN
    agent_version: str | None = None
    agent_version_path: str | None = None
    project: str = ""
    file_path: str
    operation: ChangeOperation
    # Diff content — what actually changed
    old_content: str | None = None     # for Edit: the old_string
    new_content: str | None = None     # for Edit: the new_string; for Write: full content (truncated)
    diff_summary: str | None = None    # human-readable summary of what changed
    # Git linkage (populated at commit time, if applicable)
    commit_sha: str | None = None

    def qdrant_payload(self) -> dict:
        return {
            "change_id": self.change_id,
            "session_id": self.session_id,
            "agent": _enum_val(self.agent),
            "agent_version": self.agent_version,
            "agent_version_path": self.agent_version_path,
            "project": self.project,
            "file_path": self.file_path,
            "operation": self.operation.value,
            "commit_sha": self.commit_sha,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        parts = [f"{self.operation.value} {self.file_path}"]
        if self.diff_summary:
            parts.append(self.diff_summary)
        elif self.new_content:
            parts.append(self.new_content[:500])
        return " | ".join(parts)


class BugStage(str, Enum):
    DEV = "dev"                # caught during development (test failure, lint, etc.)
    PRODUCTION = "production"  # escaped to production


class BugDiscoveredBy(str, Enum):
    TEST_FAILURE = "test_failure"
    USER_REPORT = "user_report"
    MONITORING = "monitoring"
    CODE_REVIEW = "code_review"
    HALLUCINATION_DETECTOR = "hallucination_detector"
    LINT = "lint"


class DirectiveType(str, Enum):
    RECOMMENDATION = "recommendation"   # consider and respond
    DIRECTIVE = "directive"             # must comply unless escalated


class DirectiveStatus(str, Enum):
    PENDING = "pending"                             # issued, awaiting acknowledgment
    ACKNOWLEDGED = "acknowledged"                   # agent confirmed receipt (NOT verified)
    VERIFICATION_PENDING = "verification_pending"   # acknowledged, awaiting behavioral verification
    VERIFIED_COMPLIANT = "verified_compliant"       # Policy Auditor confirmed behavior changed
    VERIFIED_NON_COMPLIANT = "verified_non_compliant"  # acknowledged but behavior did NOT change
    NON_COMPLIANT = "non_compliant"                 # agent has not acknowledged within deadline
    SUPERSEDED = "superseded"                       # replaced by a newer directive
    ESCALATED = "escalated"                         # conflict escalated to user


class FindingType(str, Enum):
    VIOLATION = "violation"             # broke a rule
    ANOMALY = "anomaly"                 # unusual but not necessarily wrong
    TREND = "trend"                     # pattern over time
    INFO = "info"                       # noteworthy but no action needed


class EscalationType(str, Enum):
    NON_COMPLIANCE = "non_compliance"       # directive ignored
    CRITICAL_FINDING = "critical_finding"   # break-glass
    TREND_ALERT = "trend_alert"             # pattern detected
    DESIGN_GAP = "design_gap"              # agent definition issue
    AUDITOR_CONCERN = "auditor_concern"     # meta-audit flag


class AuditorType(str, Enum):
    TRACE = "trace"
    SAFETY = "safety"
    POLICY = "policy"
    HALLUCINATION = "hallucination"
    DRIFT = "drift"
    COST = "cost"


class BugFixAttempt(BaseModel):
    """One attempt to fix a bug — tracks the iteration cycle."""
    attempt_number: int
    timestamp: datetime = Field(default_factory=_utcnow)
    fix_change_id: str | None = None   # the CodeChangeEvent that attempted the fix
    agent: AgentRole | str = AgentRole.MAIN
    agent_version: str | None = None
    agent_version_path: str | None = None
    test_result: str = ""              # "pass", "fail", or test output snippet
    succeeded: bool = False


class BugEvent(BaseModel):
    """A bug discovered during development or in production.

    Links back to the specific CodeChangeEvent that introduced it
    and tracks the fix chain (how many attempts to resolve).
    """
    bug_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""               # session where bug was DISCOVERED
    introduced_in_session_id: str | None = None  # session where buggy code was WRITTEN (may differ)
    timestamp: datetime = Field(default_factory=_utcnow)
    stage: BugStage = BugStage.DEV
    discovered_by: BugDiscoveredBy = BugDiscoveredBy.TEST_FAILURE
    severity: str = "medium"           # critical, high, medium, low
    agent: AgentRole | str = AgentRole.MAIN  # agent whose code introduced the bug
    agent_version: str | None = None
    agent_version_path: str | None = None
    project: str = ""
    # What went wrong
    file_paths: list[str] = Field(default_factory=list)
    description: str = ""              # what the bug is
    error_message: str | None = None   # test output, stack trace, etc.
    root_cause: str | None = None      # why it happened
    # Linkage to code changes
    introduced_by_change_id: str | None = None  # the CodeChangeEvent that caused it
    fix_change_id: str | None = None             # the CodeChangeEvent that fixed it
    # Fix lifecycle
    fix_chain: list[BugFixAttempt] = Field(default_factory=list)
    resolved: bool = False
    resolved_at: datetime | None = None

    def qdrant_payload(self) -> dict:
        return {
            "bug_id": self.bug_id,
            "session_id": self.session_id,
            "introduced_in_session_id": self.introduced_in_session_id,
            "stage": self.stage.value,
            "discovered_by": self.discovered_by.value,
            "severity": self.severity,
            "agent": _enum_val(self.agent),
            "agent_version": self.agent_version,
            "agent_version_path": self.agent_version_path,
            "project": self.project,
            "file_paths": self.file_paths,
            "introduced_by_change_id": self.introduced_by_change_id,
            "fix_change_id": self.fix_change_id,
            "resolved": self.resolved,
            "fix_attempts": len(self.fix_chain),
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        parts = [f"Bug [{self.stage.value}] [{self.severity}]"]
        if self.description:
            parts.append(self.description)
        if self.error_message:
            parts.append(f"Error: {self.error_message[:300]}")
        if self.root_cause:
            parts.append(f"Cause: {self.root_cause[:200]}")
        if self.file_paths:
            parts.append(f"Files: {', '.join(self.file_paths[:5])}")
        return " | ".join(parts)


# ── Audit Platform Events ──


class AuditFinding(BaseModel):
    """A finding produced by an auditor and sent to the Director via Redis Streams.

    This is the primary output of every auditor — the atomic unit of audit work.
    The Director consumes these, cross-references them, and decides on action.
    """
    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=_utcnow)
    auditor_type: AuditorType                # which auditor produced this
    finding_type: FindingType                # violation, anomaly, trend, info
    severity: str = "medium"                 # critical, high, medium, low, info
    confidence: float = 0.5                  # 0.0-1.0 — Director checks against 0.9 threshold
    target_agent: AgentRole | str = AgentRole.MAIN
    target_session: str | None = None        # session_id this finding is about
    target_event_ids: list[str] = Field(default_factory=list)  # QDrant point IDs as evidence
    claim: str = ""                          # plain-language statement of what's wrong
    evidence: str = ""                       # factual basis with specifics
    recommendation: str = ""                 # what the auditor thinks should happen
    related_findings: list[str] = Field(default_factory=list)  # other finding_ids
    qdrant_collection: str | None = None     # which QDrant collection the evidence lives in
    qdrant_point_ids: list[str] = Field(default_factory=list)  # direct pointers into QDrant
    project: str = ""
    # Verification fields — populated by Policy Auditor when reporting back on a
    # directive's compliance verification. Optional so non-verification findings
    # keep their current shape. See eager-giggling-rivest.md (Gap 1).
    target_directive_id: str | None = None
    verification_result: Literal["compliant", "non_compliant"] | None = None
    verification_evidence: str = ""
    sessions_examined: int = 0

    def qdrant_payload(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "auditor_type": self.auditor_type.value,
            "finding_type": self.finding_type.value,
            "severity": self.severity,
            "confidence": self.confidence,
            "target_agent": _enum_val(self.target_agent),
            "target_session": self.target_session,
            "project": self.project,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        parts = [
            f"Finding [{self.auditor_type.value}] [{self.finding_type.value}] [{self.severity}]",
            f"Confidence: {self.confidence:.2f}",
        ]
        if self.claim:
            parts.append(f"Claim: {self.claim}")
        if self.evidence:
            parts.append(f"Evidence: {self.evidence[:300]}")
        if self.recommendation:
            parts.append(f"Recommendation: {self.recommendation[:200]}")
        return " | ".join(parts)


class DirectiveEvent(BaseModel):
    """A directive issued by the Audit Director to an external agent lead.

    Directives are append-only — never retracted. A wrong directive is superseded
    by a new one referencing the old. The full history is always preserved.
    """
    directive_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=_utcnow)
    directive_type: DirectiveType            # RECOMMENDATION or DIRECTIVE
    target_agent: AgentRole | str            # which external agent this targets
    content: str = ""                        # the directive text (precise, imperative)
    required_action: str = ""                # how the agent should demonstrate compliance
    compliance_due: datetime | None = None   # deadline for acknowledgment
    triggered_by_finding: str | None = None  # finding_id that triggered this
    issued_by: str = "director"              # attribution: director, director:auto, director:approved
    confidence: float = 0.5                  # confidence behind the directive
    supporting_metrics: dict[str, Any] = Field(default_factory=dict)  # quantifiable justification
    supersedes: str | None = None            # directive_id this replaces (never retract, only supersede)
    status: DirectiveStatus = DirectiveStatus.PENDING
    verification_window_sessions: int = 5   # how many sessions after acknowledgment before verification
    verification_criteria: str = ""         # what behavioral change to look for (used by Policy Auditor)
    project: str = ""

    def qdrant_payload(self) -> dict:
        return {
            "directive_id": self.directive_id,
            "directive_type": self.directive_type.value,
            "target_agent": _enum_val(self.target_agent),
            "triggered_by_finding": self.triggered_by_finding,
            "issued_by": self.issued_by,
            "confidence": self.confidence,
            "supersedes": self.supersedes,
            "status": self.status.value,
            "project": self.project,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        target = _enum_val(self.target_agent)
        parts = [
            f"Directive [{self.directive_type.value}] to {target}",
            f"Content: {self.content[:300]}",
        ]
        if self.supersedes:
            parts.append(f"Supersedes: {self.supersedes}")
        return " | ".join(parts)

    def to_queue_payload(self) -> dict:
        """Format for publishing to the per-project directive queue."""
        return {
            "directive_id": self.directive_id,
            "directive_type": self.directive_type.value.upper(),
            "target_agent": _enum_val(self.target_agent),
            "content": self.content,
            "required_action": self.required_action,
            "issued_at": self.timestamp.isoformat(),
            "compliance_due": self.compliance_due.isoformat() if self.compliance_due else None,
            "supersedes": self.supersedes,
            "confidence": self.confidence,
            "supporting_metrics": self.supporting_metrics,
        }


class DirectiveComplianceEvent(BaseModel):
    """Records an external agent's response to a directive, or a verification result.

    Two types of events flow through this model:
    1. Agent acknowledgment: agent says "I comply" (action_taken populated)
    2. Verification result: Policy Auditor confirms behavior changed (verification_* populated)

    Acknowledgment alone does NOT mean compliance. The directive stays in
    VERIFICATION_PENDING until the Policy Auditor produces a verification result.
    """
    compliance_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=_utcnow)
    directive_id: str                        # the directive being responded to
    agent: AgentRole | str                   # the agent responding (or being verified)
    agent_version: str | None = None
    agent_version_path: str | None = None
    session_id: str | None = None            # session where compliance/verification occurred
    action_taken: str = ""                   # what the agent did to comply
    conflict_reason: str | None = None       # if escalating, why compliance conflicts
    # Verification fields (populated by Policy Auditor, not by the agent)
    is_verification: bool = False            # True if this is a verification result, not agent ack
    verification_passed: bool | None = None  # did behavior actually change?
    sessions_examined: int = 0               # how many sessions the Policy Auditor checked
    verification_evidence: str = ""          # specific behavioral evidence for/against compliance
    verification_finding_id: str | None = None  # finding_id from the Policy Auditor
    project: str = ""

    def qdrant_payload(self) -> dict:
        return {
            "compliance_id": self.compliance_id,
            "directive_id": self.directive_id,
            "agent": _enum_val(self.agent),
            "agent_version": self.agent_version,
            "agent_version_path": self.agent_version_path,
            "session_id": self.session_id,
            "project": self.project,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        agent = _enum_val(self.agent)
        parts = [f"Compliance by {agent} for directive {self.directive_id}"]
        if self.action_taken:
            parts.append(f"Action: {self.action_taken[:200]}")
        if self.conflict_reason:
            parts.append(f"Conflict: {self.conflict_reason[:200]}")
        return " | ".join(parts)


class EscalationEvent(BaseModel):
    """An escalation from the Director to the user.

    Includes quantifiable metrics, pros/cons, impact assessment, and
    recommended action — everything the user needs to make a decision.
    """
    escalation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=_utcnow)
    escalation_type: EscalationType
    severity: str = "high"
    subject_agent: AgentRole | str | None = None
    directive_id: str | None = None          # if about non-compliance
    finding_ids: list[str] = Field(default_factory=list)
    summary: str = ""                        # clinical/formal voice
    metrics: dict[str, Any] = Field(default_factory=dict)  # quantifiable backing
    impact_assessment: str = ""              # direct/conversational voice
    pros_of_action: list[str] = Field(default_factory=list)
    cons_of_action: list[str] = Field(default_factory=list)
    recommended_action: str = ""             # assertive voice
    mitigations: list[str] = Field(default_factory=list)
    project: str = ""

    def qdrant_payload(self) -> dict:
        return {
            "escalation_id": self.escalation_id,
            "escalation_type": self.escalation_type.value,
            "severity": self.severity,
            "subject_agent": _enum_val(self.subject_agent) if self.subject_agent else None,
            "directive_id": self.directive_id,
            "project": self.project,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_epoch": self.timestamp.timestamp(),
        }

    def semantic_text(self) -> str:
        parts = [f"Escalation [{self.escalation_type.value}] [{self.severity}]"]
        if self.summary:
            parts.append(self.summary[:300])
        if self.recommended_action:
            parts.append(f"Recommended: {self.recommended_action[:200]}")
        return " | ".join(parts)
