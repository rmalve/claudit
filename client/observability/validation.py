"""
Schema validation and data quality tracking for ingested events.

Validates that events have required fields populated, classifies missing
fields by ownership (hook, agent, environment), and records data quality
events to QDrant for the Trace Auditor and Director to consume.

Field ownership determines who gets the feedback:
- hook:        The observability hook code is responsible. Escalate to platform owner.
- agent:       The external agent controls this field. Issue directive to agent lead.
- environment: Deployment configuration issue. Escalate to platform owner.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class FieldOwner(str, Enum):
    """Who is responsible for populating a field."""
    HOOK = "hook"               # observability hook code (post_tool_use, session_end)
    AGENT = "agent"             # external agent behavior
    ENVIRONMENT = "environment" # deployment config (env vars, paths)


class ValidationSeverity(str, Enum):
    """How serious a missing field is."""
    CRITICAL = "critical"   # core identity/linkage field — event is nearly useless without it
    HIGH = "high"           # important for audit analysis
    MEDIUM = "medium"       # useful but auditors can work around it
    LOW = "low"             # nice to have


@dataclass
class FieldSpec:
    """Specification for a single field's validation requirements."""
    name: str
    owner: FieldOwner
    severity: ValidationSeverity
    description: str = ""


@dataclass
class ValidationError:
    """A single validation error for a missing or empty field."""
    field_name: str
    owner: FieldOwner
    severity: ValidationSeverity
    description: str
    actual_value: Any = None


@dataclass
class ValidationResult:
    """Result of validating an event."""
    event_type: str
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def total_issues(self) -> int:
        return len(self.errors) + len(self.warnings)

    def error_summary(self) -> str:
        parts = []
        for e in self.errors:
            parts.append(f"[{e.severity.value}] {e.field_name} ({e.owner.value}): {e.description}")
        for w in self.warnings:
            parts.append(f"[{w.severity.value}] {w.field_name} ({w.owner.value}): {w.description}")
        return "; ".join(parts)


# ── Field Specifications Per Event Type ──
# These define what fields are required, who owns them, and how severe a gap is.

TOOL_CALL_FIELDS = [
    FieldSpec("session_id", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Session identity — links all events in a session"),
    FieldSpec("tool_name", FieldOwner.HOOK, ValidationSeverity.CRITICAL,
              "Which tool was called — core identity of the event"),
    FieldSpec("status", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Success/failure — drives error rate metrics"),
    FieldSpec("agent_version", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Agent definition version — required for drift correlation"),
    FieldSpec("agent_version_path", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Absolute path to versioned agent file — enables definition diffing"),
    FieldSpec("project", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Project identifier — required for cross-project filtering"),
    FieldSpec("input_summary", FieldOwner.HOOK, ValidationSeverity.MEDIUM,
              "Truncated tool input — powers semantic search for auditors"),
    FieldSpec("output_summary", FieldOwner.HOOK, ValidationSeverity.MEDIUM,
              "Truncated tool output — lets auditors see results not just inputs"),
]

AGENT_SPAWN_FIELDS = [
    FieldSpec("session_id", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Session identity"),
    FieldSpec("child_agent", FieldOwner.HOOK, ValidationSeverity.CRITICAL,
              "Which agent was spawned — core identity"),
    FieldSpec("prompt", FieldOwner.AGENT, ValidationSeverity.HIGH,
              "Full prompt sent to sub-agent — critical for policy and hallucination auditing"),
    FieldSpec("description", FieldOwner.AGENT, ValidationSeverity.MEDIUM,
              "Short task description — used for quick triage"),
    FieldSpec("child_agent_version", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Child agent definition version"),
    FieldSpec("child_agent_version_path", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Absolute path to child agent versioned file"),
    FieldSpec("project", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Project identifier"),
]

CODE_CHANGE_FIELDS = [
    FieldSpec("session_id", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Session identity"),
    FieldSpec("file_path", FieldOwner.HOOK, ValidationSeverity.CRITICAL,
              "Which file was changed — core identity"),
    FieldSpec("operation", FieldOwner.HOOK, ValidationSeverity.CRITICAL,
              "Write or Edit — determines change type"),
    FieldSpec("diff_summary", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Human-readable change summary — powers auditor analysis"),
    FieldSpec("new_content", FieldOwner.HOOK, ValidationSeverity.MEDIUM,
              "Actual content written — needed for security and hallucination checks"),
    FieldSpec("agent_version", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Agent version that made the change"),
    FieldSpec("project", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Project identifier"),
]

HALLUCINATION_FIELDS = [
    FieldSpec("session_id", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Session identity"),
    FieldSpec("hallucination_type", FieldOwner.HOOK, ValidationSeverity.CRITICAL,
              "Classification of the hallucination"),
    FieldSpec("claim", FieldOwner.HOOK, ValidationSeverity.CRITICAL,
              "What the agent claimed — core evidence"),
    FieldSpec("evidence", FieldOwner.HOOK, ValidationSeverity.CRITICAL,
              "What reality shows — required for verification"),
    FieldSpec("severity", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Impact level"),
    FieldSpec("project", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Project identifier"),
]

SESSION_SUMMARY_FIELDS = [
    FieldSpec("session_id", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Session identity"),
    FieldSpec("project", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Project identifier"),
    FieldSpec("duration_seconds", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Session length — used for efficiency analysis"),
    FieldSpec("total_tool_calls", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Tool call count — baseline metric for drift detection"),
]

BUG_FIELDS = [
    FieldSpec("session_id", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Session where bug was discovered"),
    FieldSpec("description", FieldOwner.AGENT, ValidationSeverity.HIGH,
              "What the bug is — needed for semantic search and trend analysis"),
    FieldSpec("severity", FieldOwner.HOOK, ValidationSeverity.HIGH,
              "Bug impact level"),
    FieldSpec("file_paths", FieldOwner.HOOK, ValidationSeverity.MEDIUM,
              "Affected files"),
    FieldSpec("project", FieldOwner.ENVIRONMENT, ValidationSeverity.CRITICAL,
              "Project identifier"),
]

# Registry mapping event type names to their field specs
FIELD_SPECS: dict[str, list[FieldSpec]] = {
    "tool_call": TOOL_CALL_FIELDS,
    "agent_spawn": AGENT_SPAWN_FIELDS,
    "code_change": CODE_CHANGE_FIELDS,
    "hallucination": HALLUCINATION_FIELDS,
    "session_summary": SESSION_SUMMARY_FIELDS,
    "bug": BUG_FIELDS,
}


def _is_empty(value: Any) -> bool:
    """Check if a value is effectively empty/missing."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def validate_event(event: BaseModel, event_type: str) -> ValidationResult:
    """Validate an event against its field specifications.

    Args:
        event: The Pydantic event model to validate
        event_type: Key into FIELD_SPECS (e.g., "tool_call", "agent_spawn")

    Returns:
        ValidationResult with errors (critical/high) and warnings (medium/low)
    """
    result = ValidationResult(event_type=event_type)
    specs = FIELD_SPECS.get(event_type, [])

    for spec in specs:
        value = getattr(event, spec.name, None)

        if _is_empty(value):
            error = ValidationError(
                field_name=spec.name,
                owner=spec.owner,
                severity=spec.severity,
                description=spec.description,
                actual_value=value,
            )

            if spec.severity in (ValidationSeverity.CRITICAL, ValidationSeverity.HIGH):
                result.errors.append(error)
            else:
                result.warnings.append(error)

    return result


class DataQualityEvent(BaseModel):
    """Recorded when validation finds missing or incomplete fields.

    Stored in QDrant's data_quality collection for the Trace Auditor
    and Director to analyze patterns.
    """
    event_id: str = ""
    timestamp: datetime = datetime.now(timezone.utc)
    source_event_type: str              # tool_call, agent_spawn, etc.
    session_id: str = ""
    agent: str = ""
    agent_version: str | None = None
    project: str = ""
    # Validation results
    missing_fields: list[str] = []
    missing_field_owners: dict[str, str] = {}  # {field_name: owner}
    missing_field_severities: dict[str, str] = {}  # {field_name: severity}
    error_count: int = 0
    warning_count: int = 0
    summary: str = ""

    def qdrant_payload(self) -> dict:
        return {
            "event_id": self.event_id,
            "source_event_type": self.source_event_type,
            "session_id": self.session_id,
            "agent": self.agent,
            "agent_version": self.agent_version,
            "project": self.project,
            "missing_fields": self.missing_fields,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "timestamp": self.timestamp.isoformat(),
        }

    def semantic_text(self) -> str:
        parts = [f"Data quality issue in {self.source_event_type}"]
        if self.agent:
            parts.append(f"agent={self.agent}")
        parts.append(f"missing: {', '.join(self.missing_fields)}")
        if self.summary:
            parts.append(self.summary)
        return " | ".join(parts)

    @classmethod
    def from_validation_result(
        cls,
        result: ValidationResult,
        session_id: str = "",
        agent: str = "",
        agent_version: str | None = None,
        project: str = "",
    ) -> "DataQualityEvent":
        """Create a DataQualityEvent from a ValidationResult."""
        all_issues = result.errors + result.warnings
        missing_fields = [e.field_name for e in all_issues]
        missing_field_owners = {e.field_name: e.owner.value for e in all_issues}
        missing_field_severities = {e.field_name: e.severity.value for e in all_issues}

        # Group by owner for the summary
        by_owner: dict[str, list[str]] = {}
        for e in all_issues:
            by_owner.setdefault(e.owner.value, []).append(e.field_name)

        summary_parts = []
        for owner, fields in by_owner.items():
            summary_parts.append(f"{owner}: {', '.join(fields)}")

        return cls(
            event_id=str(uuid.uuid4()),
            source_event_type=result.event_type,
            session_id=session_id,
            agent=agent,
            agent_version=agent_version,
            project=project,
            missing_fields=missing_fields,
            missing_field_owners=missing_field_owners,
            missing_field_severities=missing_field_severities,
            error_count=len(result.errors),
            warning_count=len(result.warnings),
            summary="; ".join(summary_parts),
        )
