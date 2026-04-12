"""
IPC message schemas for the audit platform's Redis Streams communication layer.

Defines the message envelope and all payload types that flow between
the Audit Director and individual auditor processes.

Streams:
    audit:findings     — auditors publish findings for the Director
    audit:tasks        — Director assigns work to auditors
    audit:status       — auditors report health and task completion
    audit:directives   — Director issues directives to external agents
    audit:escalations  — Director escalates to the user
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Stream Names ──

STREAM_FINDINGS = "audit:findings"
STREAM_TASKS = "audit:tasks"
STREAM_STATUS = "audit:status"
STREAM_DIRECTIVES = "audit:directives"
STREAM_ESCALATIONS = "audit:escalations"
STREAM_REPORTS = "audit:reports"

ALL_STREAMS = [
    STREAM_FINDINGS,
    STREAM_TASKS,
    STREAM_STATUS,
    STREAM_DIRECTIVES,
    STREAM_ESCALATIONS,
    STREAM_REPORTS,
]


def project_directive_stream(project: str) -> str:
    """Per-project stream for delivering directives to external agents."""
    return f"directives:{project}"


def project_compliance_stream(project: str) -> str:
    """Per-project stream for receiving compliance responses from external agents."""
    return f"compliance:{project}"


def project_promotion_stream(project: str) -> str:
    """Per-project stream for delivering promotion instructions to external agents."""
    return f"promotions:{project}"


def project_promotion_ack_stream(project: str) -> str:
    """Per-project stream for receiving promotion acknowledgments from external agents."""
    return f"promotion_ack:{project}"


def project_escalation_resolution_stream(project: str) -> str:
    """Per-project stream for delivering user escalation guidance to the Director."""
    return f"escalation_resolutions:{project}"


# ── Enums ──

class MessageType(str, Enum):
    FINDING = "finding"
    TASK = "task"
    STATUS = "status"
    DIRECTIVE = "directive"
    ESCALATION = "escalation"
    REPORT = "report"
    HEARTBEAT = "heartbeat"
    PROMOTION = "promotion"
    PROMOTION_ACK = "promotion_ack"
    ESCALATION_RESOLUTION = "escalation_resolution"


class TaskType(str, Enum):
    AUDIT_SESSION = "audit_session"     # review a session
    DEEP_DIVE = "deep_dive"             # thorough investigation
    CROSS_CHECK = "cross_check"         # verify another auditor's finding
    CANARY = "canary"                   # test with known-bad input (meta-audit)
    BASELINE = "baseline"               # establish normal behavior profile
    VERIFY_COMPLIANCE = "verify_compliance"  # verify behavioral change after directive acknowledgment


class TaskPriority(str, Enum):
    CRITICAL = "critical"   # drop everything
    HIGH = "high"           # next up
    NORMAL = "normal"       # queued
    LOW = "low"             # when idle


class AuditorState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ERROR = "error"


class StatusType(str, Enum):
    HEARTBEAT = "heartbeat"
    TASK_ACCEPTED = "task_accepted"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    OVERLOADED = "overloaded"


# ── Message Envelope ──

class MessageEnvelope(BaseModel):
    """Standard wrapper for every message on every Redis stream.

    Every message in the audit IPC system is wrapped in this envelope.
    The payload field contains the type-specific content, discriminated
    by message_type.
    """
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    stream: str                              # which Redis stream this was published to
    timestamp: datetime = Field(default_factory=_utcnow)
    source: str                              # who sent this: "director", "auditor:safety", etc.
    target: str                              # who should consume: "director", "auditor:drift", "user"
    correlation_id: str | None = None        # chains related messages (finding → directive → compliance)
    message_type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_stream_dict(self) -> dict[str, str]:
        """Serialize to flat dict for Redis XADD (all values must be strings)."""
        import json
        return {
            "message_id": self.message_id,
            "stream": self.stream,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "target": self.target,
            "correlation_id": self.correlation_id or "",
            "message_type": self.message_type.value,
            "payload": json.dumps(self.payload),
        }

    @classmethod
    def from_stream_dict(cls, data: dict[str, str]) -> "MessageEnvelope":
        """Deserialize from Redis XREAD result."""
        import json
        return cls(
            message_id=data["message_id"],
            stream=data["stream"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=data["source"],
            target=data["target"],
            correlation_id=data.get("correlation_id") or None,
            message_type=MessageType(data["message_type"]),
            payload=json.loads(data.get("payload", "{}")),
        )


# ── Payload Models ──
# These are the structured payloads that go inside MessageEnvelope.payload.
# They are serialized to dict before being placed in the envelope.


class FindingPayload(BaseModel):
    """Auditor → Director: a finding produced during audit work."""
    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    auditor_type: str                        # trace, safety, policy, hallucination, drift, cost
    finding_type: str                        # violation, anomaly, trend, info, verification
    severity: str = "medium"                 # critical, high, medium, low, info
    confidence: float = 0.5                  # 0.0-1.0
    target_agent: str                        # which external agent this is about
    target_session: str | None = None        # session_id
    target_event_ids: list[str] = Field(default_factory=list)  # QDrant point IDs
    claim: str = ""                          # what's wrong (plain language)
    evidence: str = ""                       # factual basis (specifics)
    recommendation: str = ""                 # suggested action
    related_findings: list[str] = Field(default_factory=list)
    qdrant_refs: dict[str, Any] = Field(default_factory=dict)  # {collection, point_ids}
    # Verification fields — populated by Policy Auditor when reporting back on a
    # directive's compliance verification. Optional so non-verification findings
    # keep their current shape. See eager-giggling-rivest.md (Gap 1).
    target_directive_id: str | None = None
    verification_result: Literal["compliant", "non_compliant"] | None = None
    verification_evidence: str = ""
    sessions_examined: int = 0


class TaskPayload(BaseModel):
    """Director → Auditor: a task assignment."""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_type: TaskType
    priority: TaskPriority = TaskPriority.NORMAL
    target_auditor: str                      # which auditor should pick this up
    parameters: dict[str, Any] = Field(default_factory=dict)  # task-specific params
    deadline: datetime | None = None         # when Director expects a response
    depends_on: list[str] = Field(default_factory=list)  # other task_ids


class StatusPayload(BaseModel):
    """Auditor → Director: health and task completion reporting."""
    status_type: StatusType
    task_id: str | None = None               # null for heartbeats
    auditor: str                             # which auditor is reporting
    state: AuditorState = AuditorState.HEALTHY
    findings_produced: int = 0               # count of findings for this task
    processing_time_ms: float | None = None
    error: str | None = None                 # error details if task_failed


class DirectivePayload(BaseModel):
    """Director → External: a directive issued to an external agent lead.

    Also synced to .audit/directives.json in the external project directory.
    """
    directive_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    directive_type: str                      # RECOMMENDATION or DIRECTIVE
    target_agent: str                        # which external agent must comply
    triggered_by_finding: str | None = None  # finding_id that triggered this
    content: str = ""                        # the directive text
    required_action: str = ""                # how to demonstrate compliance
    compliance_due: str | None = None        # ISO 8601 deadline
    issued_by: str = "director"              # director, director:auto, director:approved
    confidence: float = 0.5
    supporting_metrics: dict[str, Any] = Field(default_factory=dict)
    supersedes: str | None = None            # directive_id this replaces


class EscalationPayload(BaseModel):
    """Director → User: an escalation requiring human decision.

    Includes quantifiable metrics, pros/cons, impact assessment, and
    recommended action per the user's requirements.
    """
    escalation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    escalation_type: str                     # non_compliance, critical_finding, trend_alert, etc.
    severity: str = "high"
    subject_agent: str | None = None         # which agent this is about
    directive_id: str | None = None          # if about non-compliance
    finding_ids: list[str] = Field(default_factory=list)
    summary: str = ""                        # clinical/formal
    metrics: dict[str, Any] = Field(default_factory=dict)
    impact_assessment: str = ""              # direct/conversational
    pros_of_action: list[str] = Field(default_factory=list)
    cons_of_action: list[str] = Field(default_factory=list)
    recommended_action: str = ""             # assertive
    mitigations: list[str] = Field(default_factory=list)


class CompliancePayload(BaseModel):
    """External Agent → Director: acknowledgment/compliance response to a directive.

    Published by external project hooks to compliance:{project} stream.
    """
    compliance_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    directive_id: str                        # the directive being responded to
    agent: str                               # which agent is responding
    agent_version: str | None = None
    session_id: str | None = None            # session where compliance occurred
    action_taken: str = ""                   # what the agent did to comply
    conflict_reason: str | None = None       # if escalating, why compliance conflicts


class PromotionPayload(BaseModel):
    """Director → External: instruction to add/remove standing directive verbiage.

    The Director provides exact text to add and optionally exact text to remove
    (for supersession). The external hook is a faithful executor — it writes
    exactly what is specified and acks back with the result.
    """
    promotion_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    directive_id: str                        # the directive being promoted
    decision_type: str                       # PROMOTE or SUPERSEDE_AND_PROMOTE
    add_verbiage: str                        # exact text to add to standing file
    remove_verbiage: str | None = None       # exact text to remove (if superseding)
    target_file: str = "observability/standing_directives.md"
    target_agents: list[str] = Field(default_factory=list)
    audit_cycle_id: str | None = None


class PromotionAckPayload(BaseModel):
    """External → Director: confirmation of standing directive file edit.

    The hook reports exactly what was written so the Director can verify
    the verbiage matches intent.
    """
    promotion_id: str
    verbiage_added: str                      # exact text that was added
    verbiage_removed: str | None = None      # exact text that was removed
    file_state_hash: str                     # SHA-256 of standing file after edit
    warning: str | None = None               # e.g. "remove_verbiage not found in file"


class EscalationResolutionPayload(BaseModel):
    """User (dashboard) → Director: guidance for resolving an escalation.

    Published when the user clicks Dismiss in the dashboard. Contains
    the full conversation thread context and final guidance.
    """
    escalation_id: str
    final_guidance: str                      # user's dismissal message
    message_history: list[dict[str, Any]] = Field(default_factory=list)


class ReportPayload(BaseModel):
    """Director → User: a session audit report.

    Generated after every audited session per the operational cadence requirement.
    """
    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    project: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)
    # Session metadata
    session_duration_seconds: float = 0.0
    agents_involved: list[str] = Field(default_factory=list)
    # Findings summary
    total_findings: int = 0
    findings_by_severity: dict[str, int] = Field(default_factory=dict)
    findings_by_auditor: dict[str, int] = Field(default_factory=dict)
    critical_findings: list[dict[str, Any]] = Field(default_factory=list)
    # Directives issued
    directives_issued: int = 0
    directives_pending: int = 0
    # Trend indicators
    trends_detected: list[str] = Field(default_factory=list)
    # Director's assessment
    overall_risk_level: str = "low"          # low, medium, high, critical
    director_notes: str = ""                 # free-form Director observations


# ── Helper: build envelope ──

def build_message(
    stream: str,
    source: str,
    target: str,
    message_type: MessageType,
    payload: BaseModel,
    correlation_id: str | None = None,
) -> MessageEnvelope:
    """Convenience function to build a properly formed message envelope."""
    return MessageEnvelope(
        stream=stream,
        source=source,
        target=target,
        correlation_id=correlation_id,
        message_type=message_type,
        payload=payload.model_dump(mode="json"),
    )
