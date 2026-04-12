"""
LLM Observability Framework

Reusable agent observability with OpenTelemetry metrics + Qdrant semantic storage.
Audit platform IPC via Redis Streams. Persistent audit store via SQLite.
"""

from observability.schemas import (
    ToolCallEvent, SessionSummary, HallucinationEvent, AgentSpawnEvent,
    EvalResult, CodeChangeEvent, BugEvent,
    # Audit platform schemas (used by IPC messages, not stored in QDrant)
    AuditFinding, DirectiveEvent, DirectiveComplianceEvent, EscalationEvent,
    DirectiveType, DirectiveStatus, FindingType, EscalationType, AuditorType,
)
from observability.client import ObservabilityClient
from observability.stream_client import StreamClient
from observability.audit_store import AuditStore
from observability.messages import (
    MessageEnvelope, MessageType,
    FindingPayload, TaskPayload, StatusPayload,
    DirectivePayload, EscalationPayload, ReportPayload,
    build_message,
    STREAM_FINDINGS, STREAM_TASKS, STREAM_STATUS,
    STREAM_DIRECTIVES, STREAM_ESCALATIONS, STREAM_REPORTS,
)

__all__ = [
    # Observability client
    "ObservabilityClient",
    # Core event schemas
    "ToolCallEvent",
    "SessionSummary",
    "HallucinationEvent",
    "AgentSpawnEvent",
    "EvalResult",
    "CodeChangeEvent",
    "BugEvent",
    # Audit platform schemas
    "AuditFinding",
    "DirectiveEvent",
    "DirectiveComplianceEvent",
    "EscalationEvent",
    "DirectiveType",
    "DirectiveStatus",
    "FindingType",
    "EscalationType",
    "AuditorType",
    # IPC
    "StreamClient",
    "AuditStore",
    "MessageEnvelope",
    "MessageType",
    "FindingPayload",
    "TaskPayload",
    "StatusPayload",
    "DirectivePayload",
    "EscalationPayload",
    "ReportPayload",
    "build_message",
    # Stream names
    "STREAM_FINDINGS",
    "STREAM_TASKS",
    "STREAM_STATUS",
    "STREAM_DIRECTIVES",
    "STREAM_ESCALATIONS",
    "STREAM_REPORTS",
]
